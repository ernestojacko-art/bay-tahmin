"""Bay Tahmin maç sohbeti: Intelligence Engine verilerini Türkçe ve açık biçimde anlatır."""
from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.chat.context_memory import ContextMemoryStore, MatchContext
from app.chat.football_expert import FootballExpertAgent
from app.chat.llm_client import LLMClient
from app.core.config import Settings
from app.core.exceptions import BayTahminError, MatchNotFoundError
from app.schemas.chat import ChatResponse
from app.schemas.prediction import MatchPrediction
from app.services.analysis_service import AnalysisService

_MATCH_INTENT_PATTERNS = re.compile(
    r"ilk yarı|iy/ms|ht/ft|sürpriz|güven|1x2|olasılık|skor|kaç kaç|"
    r"favori|banko|alt.?üst|over|under|btts|karşılıklı gol",
    re.IGNORECASE,
)
_DAILY_SURPRISE_PATTERNS = re.compile(
    r"(?:bugün|yarın|günün|yarının).{0,100}(?:sürpriz|iy/ms|ht/ft).{0,100}(?:\d+|bir|iki|üç|dört|beş|altı|yedi|sekiz|dokuz|on).{0,40}(?:maç|karşılaş|öner)|"
    r"(?:sürpriz|iy/ms|ht/ft).{0,100}(?:\d+|bir|iki|üç|dört|beş|altı|yedi|sekiz|dokuz|on).{0,40}(?:maç|karşılaş|öner)",
    re.IGNORECASE,
)

_RISK_TR = {"low": "düşük", "medium": "orta", "high": "yüksek"}
_QUALITY_TR = {"low": "düşük", "medium": "orta", "high": "yüksek"}
_NUMBER_TR = {
    "bir": 1, "iki": 2, "üç": 3, "dört": 4, "beş": 5,
    "altı": 6, "yedi": 7, "sekiz": 8, "dokuz": 9, "on": 10,
}


def _looks_like_match_question(message: str) -> bool:
    return bool(_MATCH_INTENT_PATTERNS.search(message))


def _requested_match_count(message: str) -> int:
    lowered = message.lower()
    numeric = re.search(r"\b([1-9]|10)\s*(?:maç|karşılaşma|karşılaşma(?:sı)?)", lowered)
    if numeric:
        return int(numeric.group(1))
    for word, value in _NUMBER_TR.items():
        if re.search(rf"\b{re.escape(word)}\s*(?:maç|karşılaşma|karşılaşma(?:sı)?)", lowered):
            return value
    return 5


def _looks_like_daily_surprise_request(message: str) -> bool:
    return bool(_DAILY_SURPRISE_PATTERNS.search(message))


def _risk_tr(value) -> str:
    raw = getattr(value, "value", str(value)).lower()
    return _RISK_TR.get(raw, raw)


def _quality_tr(value) -> str:
    raw = getattr(value, "value", str(value)).lower()
    return _QUALITY_TR.get(raw, raw)


def _favorite_label(prediction: MatchPrediction) -> str:
    ox = prediction.one_x_two
    values = {
        prediction.home_team.team_name: ox.home_win,
        "Beraberlik": ox.draw,
        prediction.away_team.team_name: ox.away_win,
    }
    return max(values, key=values.get)


def _narrate_half_time(prediction: MatchPrediction) -> str:
    ht = prediction.half_time_one_x_two
    return (
        "İlk yarı değerlendirmem:\n"
        f"• {prediction.home_team.team_name}: %{ht.home_win*100:.1f}\n"
        f"• Beraberlik: %{ht.draw*100:.1f}\n"
        f"• {prediction.away_team.team_name}: %{ht.away_win*100:.1f}"
    )


def _narrate_surprises(prediction: MatchPrediction) -> str:
    if not prediction.surprises:
        return "Bu maç için modelin yeterli güçte bulduğu belirgin bir İY/MS sürpriz senaryosu yok."
    lines = ["Bu maç için öne çıkan sürpriz İY/MS senaryoları:"]
    for s in prediction.surprises[:5]:
        lines.append(
            f"• {s.combination} — sürpriz skoru {s.composite_score:.2f}, risk {_risk_tr(s.risk)}"
        )
    return "\n".join(lines)


def _narrate_confidence(prediction: MatchPrediction) -> str:
    c = prediction.confidence
    return (
        f"En güçlü tahminime güven düzeyim %{c.confidence*100:.1f}. "
        f"Risk seviyesi {_risk_tr(c.risk)}, veri kalitesi {_quality_tr(c.data_quality)}."
    )


def _narrate_summary(prediction: MatchPrediction) -> str:
    ox = prediction.one_x_two
    eg = prediction.expected_goals
    favorite = _favorite_label(prediction)

    lines = [
        f"Bu maç için ana görüşüm: {favorite}.",
        "",
        "Tahmin özeti:",
        f"• Maç sonucu: {prediction.home_team.team_name} %{ox.home_win*100:.1f} | Beraberlik %{ox.draw*100:.1f} | {prediction.away_team.team_name} %{ox.away_win*100:.1f}",
        f"• En güçlü sonuç eğilimi: {favorite}",
        f"• Beklenen gol projeksiyonu: {eg.home_xg:.2f} - {eg.away_xg:.2f} (toplam {eg.total_xg:.2f})",
        f"• Karşılıklı gol olur: %{prediction.btts_yes_probability*100:.1f}",
    ]
    if prediction.scenarios:
        lines += ["", "Modelin öne çıkardığı senaryo:", f"• {favorite} sonucunun gerçekleşmesi daha güçlü görünüyor."]
    lines += ["", _narrate_confidence(prediction)]
    return "\n".join(lines)


def _build_structured_match_reply(message: str, prediction: MatchPrediction) -> str:
    lowered = message.lower()
    if "ilk yarı" in lowered or "ht" in lowered:
        return _narrate_half_time(prediction)
    if "sürpriz" in lowered:
        return _narrate_surprises(prediction)
    if "güven" in lowered or "banko" in lowered:
        return _narrate_confidence(prediction)
    return _narrate_summary(prediction)


class ChatOrchestrator:
    def __init__(
        self,
        settings: Settings,
        analysis_service: AnalysisService,
        football_expert: FootballExpertAgent,
        llm_client: LLMClient,
        context_store: ContextMemoryStore,
    ):
        self._settings = settings
        self._analysis_service = analysis_service
        self._football_expert = football_expert
        self._llm_client = llm_client
        self._context_store = context_store

    async def _daily_surprises(self, message: str) -> str:
        tz = ZoneInfo("Europe/Istanbul")
        target = datetime.now(tz).date()
        if "yarın" in message.lower() or "yarının" in message.lower():
            from datetime import timedelta
            target = target + timedelta(days=1)

        requested_count = _requested_match_count(message)
        provider = self._analysis_service._provider
        fixtures = await provider.get_fixtures(target)
        ranked = []
        for fixture in fixtures:
            try:
                prediction = await self._analysis_service.analyze_match(fixture.match_id)
            except BayTahminError:
                continue
            if not prediction.surprises:
                continue
            best = max(prediction.surprises, key=lambda item: item.composite_score)
            ranked.append((best.composite_score, fixture, prediction, best))

        ranked.sort(key=lambda item: item[0], reverse=True)
        ranked = ranked[:requested_count]
        if not ranked:
            return f"{target.isoformat()} için gerçek veriyle yeterli sayıda analiz edilebilir sürpriz İY/MS maçı bulamadım."

        lines = [f"{target.isoformat()} için sürpriz potansiyeli en yüksek {len(ranked)} İY/MS maçı:"]
        for index, (_, fixture, prediction, best) in enumerate(ranked, 1):
            lines.append(
                f"{index}. {fixture.home_team.name} - {fixture.away_team.name}\n"
                f"   İY/MS: {best.combination} | Sürpriz skoru: {best.composite_score:.2f} | "
                f"Risk: {_risk_tr(best.risk)} | Güven: %{best.confidence*100:.1f}"
            )
        lines.append("")
        lines.append("Bu liste yalnızca 5DollarFootballAPI üzerinden bulunan gerçek maçların Cloud Intelligence Engine tarafından analiz edilmesiyle oluşturuldu.")
        return "\n".join(lines)

    async def handle_message(self, session_id: str, message: str, match_id: str | None) -> ChatResponse:
        context = self._context_store.get_or_create(session_id)
        context.add_turn("user", message, self._settings.chat_context_max_turns)

        if _looks_like_daily_surprise_request(message):
            try:
                reply = await self._daily_surprises(message)
                context.previous_intent = "daily_surprises"
                context.add_turn("assistant", reply, self._settings.chat_context_max_turns)
                return ChatResponse(
                    session_id=session_id, reply=reply, intent="match_analysis",
                    match_id=None, used_prediction_engine=True, grounded_in_analysis=True,
                )
            except Exception:
                reply = "Bugünün gerçek maçları için şu anda sürpriz analizi üretilemedi. Veri sağlayıcısındaki geçici sınır nedeniyle genel sohbet yanıtına düşürmedim."
                context.add_turn("assistant", reply, self._settings.chat_context_max_turns)
                return ChatResponse(
                    session_id=session_id, reply=reply, intent="fallback", match_id=None,
                    used_prediction_engine=False, grounded_in_analysis=False,
                )

        if context.previous_intent == "daily_surprises" and re.search(r"hangi maç|hangileri|hangi karşılaş", message, re.I):
            reply = await self._daily_surprises("bugün sürpriz İY/MS maçları")
            context.add_turn("assistant", reply, self._settings.chat_context_max_turns)
            return ChatResponse(
                session_id=session_id, reply=reply, intent="match_analysis",
                match_id=None, used_prediction_engine=True, grounded_in_analysis=True,
            )

        effective_match_id = match_id or context.active_match_id
        wants_match_analysis = bool(match_id) or (
            context.active_match_id is not None and _looks_like_match_question(message)
        )
        if wants_match_analysis and effective_match_id:
            return await self._handle_match_question(session_id, context, message, effective_match_id)

        return await self._handle_general_question(session_id, context, message)

    async def _handle_match_question(self, session_id: str, context: MatchContext, message: str, match_id: str) -> ChatResponse:
        try:
            if context.active_match_id != match_id or context.latest_analysis is None:
                prediction = await self._analysis_service.analyze_match(match_id)
                context.bind_match(match_id, prediction.home_team.team_name, prediction.away_team.team_name)
                context.set_analysis(prediction)
            else:
                prediction = context.latest_analysis

            reply = _build_structured_match_reply(message, prediction)
            context.previous_intent = "match_analysis"
            context.add_turn("assistant", reply, self._settings.chat_context_max_turns)
            return ChatResponse(
                session_id=session_id, reply=reply, intent="match_analysis", match_id=match_id,
                used_prediction_engine=True, grounded_in_analysis=True,
            )
        except MatchNotFoundError:
            reply = "Bu maçı şu anda gerçek veri sağlayıcısında bulamadım."
        except BayTahminError:
            reply = "Bu maç için şu anda tam analiz üretilemedi. Lütfen daha sonra tekrar deneyin."
        context.add_turn("assistant", reply, self._settings.chat_context_max_turns)
        return ChatResponse(
            session_id=session_id, reply=reply, intent="fallback", match_id=match_id,
            used_prediction_engine=False, grounded_in_analysis=False,
        )

    async def _handle_general_question(self, session_id: str, context: MatchContext, message: str) -> ChatResponse:
        answer = await self._football_expert.answer(message)
        context.previous_intent = "general_football"
        context.add_turn("assistant", answer.text, self._settings.chat_context_max_turns)
        return ChatResponse(
            session_id=session_id, reply=answer.text, intent="general_football",
            match_id=context.active_match_id, used_prediction_engine=False,
            grounded_in_analysis=answer.grounded_in_knowledge_base,
        )
