"""Bay Tahmin maç sohbeti: yalnızca Intelligence Engine verilerini Türkçe anlatır."""
from __future__ import annotations

import re

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

_RISK_TR = {"low": "düşük", "medium": "orta", "high": "yüksek"}
_QUALITY_TR = {"low": "düşük", "medium": "orta", "high": "yüksek"}


def _looks_like_match_question(message: str) -> bool:
    return bool(_MATCH_INTENT_PATTERNS.search(message))


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
        f"• {prediction.home_team.team_name} ilk yarıyı önde bitirir: %{ht.home_win*100:.1f}\n"
        f"• İlk yarı beraberliği: %{ht.draw*100:.1f}\n"
        f"• {prediction.away_team.team_name} ilk yarıyı önde bitirir: %{ht.away_win*100:.1f}"
    )


def _narrate_surprises(prediction: MatchPrediction) -> str:
    if not prediction.surprises:
        return "Bu maç için modelin yeterli güçte bulduğu belirgin bir İY/MS sürpriz senaryosu yok."
    lines = ["Öne çıkan sürpriz İY/MS senaryoları:"]
    for s in prediction.surprises:
        lines.append(
            f"• {s.combination} — uyum skoru: {s.composite_score:.2f}, risk: {_risk_tr(s.risk)}"
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
        f"• Karşılıklı gol olur olasılığı: %{prediction.btts_yes_probability*100:.1f}",
    ]

    if prediction.scenarios:
        lines.append("")
        lines.append("Modelin öne çıkardığı maç senaryosu:")
        lines.append(f"• En olası senaryo {favorite} sonucunu destekliyor.")

    lines.append("")
    lines.append(_narrate_confidence(prediction))
    lines.append(
        "Bu değerlendirme kesin sonuç garantisi değildir; mevcut gerçek veriler ve istatistiksel modelin ürettiği olasılıklara dayanır."
    )
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

    async def handle_message(self, session_id: str, message: str, match_id: str | None) -> ChatResponse:
        context = self._context_store.get_or_create(session_id)
        context.add_turn("user", message, self._settings.chat_context_max_turns)

        effective_match_id = match_id or context.active_match_id
        wants_match_analysis = bool(match_id) or (
            context.active_match_id is not None and _looks_like_match_question(message)
        )

        if wants_match_analysis and effective_match_id:
            return await self._handle_match_question(session_id, context, message, effective_match_id)

        return await self._handle_general_question(session_id, context, message)

    async def _handle_match_question(
        self, session_id: str, context: MatchContext, message: str, match_id: str
    ) -> ChatResponse:
        try:
            if context.active_match_id != match_id or context.latest_analysis is None:
                prediction = await self._analysis_service.analyze_match(match_id)
                context.bind_match(
                    match_id, prediction.home_team.team_name, prediction.away_team.team_name
                )
                context.set_analysis(prediction)
            else:
                prediction = context.latest_analysis

            reply = _build_structured_match_reply(message, prediction)
            context.previous_intent = "match_analysis"
            context.add_turn("assistant", reply, self._settings.chat_context_max_turns)
            return ChatResponse(
                session_id=session_id,
                reply=reply,
                intent="match_analysis",
                match_id=match_id,
                used_prediction_engine=True,
                grounded_in_analysis=True,
            )
        except MatchNotFoundError:
            reply = "Bu maçı şu anda gerçek veri sağlayıcısında bulamadım."
            context.add_turn("assistant", reply, self._settings.chat_context_max_turns)
            return ChatResponse(
                session_id=session_id, reply=reply, intent="fallback", match_id=match_id,
                used_prediction_engine=False, grounded_in_analysis=False,
            )
        except BayTahminError:
            reply = "Bu maç için şu anda tam analiz üretilemedi. Lütfen daha sonra tekrar deneyin."
            context.add_turn("assistant", reply, self._settings.chat_context_max_turns)
            return ChatResponse(
                session_id=session_id, reply=reply, intent="fallback", match_id=match_id,
                used_prediction_engine=False, grounded_in_analysis=False,
            )

    async def _handle_general_question(
        self, session_id: str, context: MatchContext, message: str
    ) -> ChatResponse:
        answer = await self._football_expert.answer(message)
        context.previous_intent = "general_football"
        context.add_turn("assistant", answer.text, self._settings.chat_context_max_turns)
        return ChatResponse(
            session_id=session_id,
            reply=answer.text,
            intent="general_football",
            match_id=context.active_match_id,
            used_prediction_engine=False,
            grounded_in_analysis=answer.grounded_in_knowledge_base,
        )
