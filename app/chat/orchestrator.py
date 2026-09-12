"""Bay Tahmin maç sohbeti: Intelligence Engine verilerini Türkçe ve açık biçimde anlatır."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.chat.context_memory import ContextMemoryStore, MatchContext
from app.chat.football_expert import FootballExpertAgent
from app.chat.llm_client import LLMClient
from app.core.config import Settings
from app.core.exceptions import BayTahminError, MatchNotFoundError
from app.providers.models import Fixture, FixtureStatus
from app.schemas.chat import ChatResponse
from app.schemas.prediction import MatchPrediction
from app.services.analysis_service import AnalysisService

ISTANBUL = ZoneInfo("Europe/Istanbul")

_MATCH_INTENT_PATTERNS = re.compile(
    r"ilk yarı|iy/ms|ht/ft|sürpriz|güven|1x2|olasılık|skor|kaç kaç|"
    r"favori|banko|alt.?üst|over|under|btts|karşılıklı gol|çifte şans|"
    r"handikap|el handikap|maç sonucu|kazanır|beraberlik",
    re.IGNORECASE,
)
_DAILY_SURPRISE_PATTERNS = re.compile(
    r"(bugün|yarın|günün|yarının).{0,80}(sürpriz|iy/ms|ht/ft).{0,80}(5|beş|maç|öner)|"
    r"(sürpriz|iy/ms|ht/ft).{0,80}(5|beş).{0,80}(maç|öner)",
    re.IGNORECASE,
)
# Sadece fikstür listesi istenen, sürpriz/İY-MS analizi GEREKTİRMEYEN genel sorular
# (spec §5: TODAY_MATCHES / EVENING_MATCHES). Bunlar da mutlaka gerçek veriyle
# cevaplanmalı; aksi halde LLM fikstür uydurma riskine girer (spec §27).
_FIXTURE_LIST_PATTERNS = re.compile(
    r"(bugün|bu akşam|bu gece|yarın|yarın akşam|hafta ?sonu).{0,40}"
    r"(maç|karşılaşma|fikstür|program).{0,20}(var mı|neler|hangi|listele)?|"
    r"(hangi|kaç).{0,20}maç.{0,20}(var|oynan)",
    re.IGNORECASE,
)


def _looks_like_match_question(message: str) -> bool:
    return bool(_MATCH_INTENT_PATTERNS.search(message))


def _looks_like_daily_surprise_request(message: str) -> bool:
    return bool(_DAILY_SURPRISE_PATTERNS.search(message))


def _looks_like_fixture_list_request(message: str) -> bool:
    return bool(_FIXTURE_LIST_PATTERNS.search(message))


def _target_date_from_message(message: str) -> date:
    """§10: bugün/yarın gibi doğal tarih ifadelerini Europe/Istanbul tarihine çevirir."""
    lowered = message.lower()
    target = datetime.now(ISTANBUL).date()
    if "yarın" in lowered:
        target = target + timedelta(days=1)
    return target


def _is_evening_request(message: str) -> bool:
    """§9: 'bu akşam' / 'bu gece' ifadesi 17:00-23:59 Europe/Istanbul penceresi demektir."""
    lowered = message.lower()
    return "akşam" in lowered or "gece" in lowered


def _future_scheduled_fixtures(fixtures: list[Fixture]) -> list[Fixture]:
    """
    §8/§12 (EN KRİTİK SORUN): bitmiş, canlı ya da ertelenmiş maçları VE kickoff
    zamanı şu andan önce olan hiçbir maçı asla "gelecek maç" olarak döndürme.
    """
    now_utc = datetime.now(timezone.utc)
    return [
        f for f in fixtures
        if f.status == FixtureStatus.SCHEDULED and f.kickoff > now_utc
    ]


def _within_evening_window(fixtures: list[Fixture], target: date) -> list[Fixture]:
    """§9: hedef günün Europe/Istanbul 17:00-23:59 penceresindeki maçlar."""
    window_start = datetime.combine(target, datetime.min.time(), tzinfo=ISTANBUL).replace(hour=17)
    window_end = datetime.combine(target, datetime.min.time(), tzinfo=ISTANBUL).replace(hour=23, minute=59, second=59)
    return [f for f in fixtures if window_start <= f.kickoff.astimezone(ISTANBUL) <= window_end]


# Bilinen üst düzey Avrupa ligleri/kupaları -- 5DollarFootballAPI'nin verdiği
# league_name alanına göre eşleştirilir. Bu liste kapsamlı değildir; API'nin
# döndürdüğü tam isimlerle eşleşmeyen bir lig burada yanlışlıkla dışarıda
# kalabilir -- bu bilinen bir sınırlamadır (ayrı bir "ülke/bölge" alanı yok).
_TOP_EUROPEAN_LEAGUE_HINTS = (
    "premier league", "la liga", "laliga", "serie a", "bundesliga", "ligue 1",
    "champions league", "europa league", "conference league", "eredivisie",
    "primeira liga", "süper lig", "super lig", "premiership", "jupiler",
)

_EUROPEAN_LEAGUE_REQUEST_PATTERN = re.compile(r"avrupa|üst lig|büyük lig|top lig", re.IGNORECASE)

_SURPRISE_CONTINUATION_PATTERN = re.compile(
    r"hangi maç|hangileri|hangi karşılaş|başka|farklı|diğer|değiştir|filtrele|"
    r"daha|tekrar|yine|\d+\s*(tane\s*)?maç|kaç tane|eksik",
    re.IGNORECASE,
)


def _is_top_european_league(fixture: Fixture) -> bool:
    name = (fixture.league_name or "").lower()
    return any(hint in name for hint in _TOP_EUROPEAN_LEAGUE_HINTS)


def _wants_european_leagues(message: str) -> bool:
    return bool(_EUROPEAN_LEAGUE_REQUEST_PATTERN.search(message))


_RISK_TR = {"low": "düşük", "medium": "orta", "high": "yüksek"}
_QUALITY_TR = {"low": "düşük", "medium": "orta", "high": "yüksek"}


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


def _narrate_double_chance(prediction: MatchPrediction) -> str:
    dc = prediction.double_chance
    dnb = prediction.draw_no_bet
    return (
        "Çifte şans ve Beraberlik İadeli (DNB) pazarları:\n"
        f"• {prediction.home_team.team_name} veya Beraberlik (1X): %{dc.home_or_draw*100:.1f}\n"
        f"• Beraberlik veya {prediction.away_team.team_name} (X2): %{dc.draw_or_away*100:.1f}\n"
        f"• {prediction.home_team.team_name} veya {prediction.away_team.team_name} (12): %{dc.home_or_away*100:.1f}\n"
        f"• DNB — {prediction.home_team.team_name}: %{dnb.home*100:.1f} | {prediction.away_team.team_name}: %{dnb.away*100:.1f}"
    )


def _narrate_asian_handicap(prediction: MatchPrediction) -> str:
    if not prediction.asian_handicap:
        return "Bu maç için Asya handikapı hesaplayacak yeterli veri yok."
    lines = [f"Asya handikapı (çizgi {prediction.home_team.team_name} tarafına uygulanır):"]
    for ah in prediction.asian_handicap:
        push_note = f" | Push: %{ah.push*100:.1f}" if ah.push > 0 else ""
        lines.append(
            f"• {ah.line:+.2f}: {prediction.home_team.team_name} %{ah.home_cover*100:.1f} | "
            f"{prediction.away_team.team_name} %{ah.away_cover*100:.1f}{push_note}"
        )
    return "\n".join(lines)


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
    if "çifte şans" in lowered or "dnb" in lowered or "beraberlik iadeli" in lowered:
        return _narrate_double_chance(prediction)
    if "handikap" in lowered:
        return _narrate_asian_handicap(prediction)
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

    async def _daily_surprises(self, message: str, exclude_match_ids: frozenset[str] = frozenset()) -> tuple[str, list[str]]:
        target = _target_date_from_message(message)
        evening = _is_evening_request(message)
        european_only = _wants_european_leagues(message)

        provider = self._analysis_service._provider
        fixtures = await provider.get_fixtures(target)
        fixtures = _future_scheduled_fixtures(fixtures)
        if evening:
            fixtures = _within_evening_window(fixtures, target)
        if european_only:
            fixtures = [f for f in fixtures if _is_top_european_league(f)]
        already_shown = [f for f in fixtures if f.match_id in exclude_match_ids]
        if exclude_match_ids:
            fixtures = [f for f in fixtures if f.match_id not in exclude_match_ids]

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
        ranked = ranked[:5]
        if not ranked:
            window_note = " (17:00-23:59 aralığında)" if evening else ""
            league_note = ", sadece üst düzey Avrupa liglerinde" if european_only else ""
            already_note = (
                f" Daha önce gösterdiğim {len(already_shown)} maç dışında,"
                if exclude_match_ids and already_shown else ""
            )
            return (
                f"{target.isoformat()}{window_note}{league_note} için,{already_note} piyasası açık ve yeterli "
                "veri kalitesine sahip başka bir gerçek sürpriz İY/MS adayı bulamadım. Elimdeki tüm kaliteli "
                "adayları zaten paylaştım."
                if exclude_match_ids else
                f"{target.isoformat()}{window_note}{league_note} için, piyasası açık ve yeterli veri kalitesine sahip "
                "gerçek bir sürpriz İY/MS adayı bulamadım. Bu, o saatlerdeki maçların çoğunda bahis "
                "piyasasının henüz açılmamış olmasından ya da veri örnekleminin yetersiz kalmasından "
                "kaynaklanabilir."
            ), []

        league_label = " üst düzey Avrupa liglerinden" if european_only else ""
        header_prefix = "Daha önce gösterdiklerim dışında, " if exclude_match_ids else ""
        lines = [f"{header_prefix}{target.isoformat()} için{league_label} sürpriz potansiyeli en yüksek İY/MS maçlar:"]
        if len(ranked) < 5:
            lines[0] = (
                f"{header_prefix}{target.isoformat()} için{league_label}, piyasası açık ve yeterli veri kalitesine "
                f"sahip yalnızca {len(ranked)} güçlü sürpriz adayı bulundu:"
            )
        for index, (_, fixture, prediction, best) in enumerate(ranked, 1):
            kickoff_local = fixture.kickoff.astimezone(ISTANBUL).strftime("%H:%M")
            lines.append(
                f"{index}. {fixture.home_team.name} - {fixture.away_team.name} ({kickoff_local})\n"
                f"   İY/MS: {best.combination} | Sürpriz skoru: {best.composite_score:.2f} | "
                f"Risk: {_risk_tr(best.risk)} | Güven: %{best.confidence*100:.1f}"
            )
        lines.append("")
        lines.append(
            "Bu liste yalnızca 5DollarFootballAPI üzerinden bulunan, henüz başlamamış ve piyasası "
            "açık gerçek maçların Cloud Intelligence Engine tarafından analiz edilip gerçek piyasa "
            "olasılıklarıyla karşılaştırılmasıyla oluşturuldu."
        )
        shown_ids = [fixture.match_id for (_, fixture, _, _) in ranked]
        return "\n".join(lines), shown_ids

    async def _list_matches(self, message: str) -> str:
        """§5 TODAY_MATCHES/EVENING_MATCHES: sürpriz analizi gerektirmeyen düz fikstür listesi.
        LLM'in fikstür uydurmasını (§27) önlemek için bu da gerçek veriden üretilir."""
        target = _target_date_from_message(message)
        evening = _is_evening_request(message)

        provider = self._analysis_service._provider
        fixtures = await provider.get_fixtures(target)
        fixtures = _future_scheduled_fixtures(fixtures)
        if evening:
            fixtures = _within_evening_window(fixtures, target)
        fixtures.sort(key=lambda f: f.kickoff)

        window_note = " (bu akşam, 17:00-23:59)" if evening else ""
        if not fixtures:
            return f"{target.isoformat()}{window_note} için henüz başlamamış planlı bir maç bulamadım."

        lines = [f"{target.isoformat()}{window_note} için henüz başlamamış maçlar:"]
        for fixture in fixtures[:20]:
            kickoff_local = fixture.kickoff.astimezone(ISTANBUL).strftime("%H:%M")
            league = f" ({fixture.league_name})" if fixture.league_name else ""
            lines.append(f"• {kickoff_local} — {fixture.home_team.name} - {fixture.away_team.name}{league}")
        if len(fixtures) > 20:
            lines.append(f"... ve {len(fixtures) - 20} maç daha.")
        return "\n".join(lines)

    async def _resolve_match_by_teams(self, message: str) -> str | None:
        """
        Mesajda geçen takım isimlerinden gerçek bir fikstürü bulmaya çalışır
        (bugün + yarın penceresinde). Sadece isim eşleşmesi yapar; hiçbir
        tahmin/olasılık üretmez -- o iş Prediction Engine'e aittir.
        """
        lowered = message.lower()
        provider = self._analysis_service._provider
        candidates: list[Fixture] = []
        today = datetime.now(ISTANBUL).date()
        for offset in (0, 1):
            try:
                fixtures = await provider.get_fixtures(today + timedelta(days=offset))
            except Exception:
                continue
            for fixture in _future_scheduled_fixtures(fixtures):
                home = fixture.home_team.name.lower()
                away = fixture.away_team.name.lower()

                def _hit(team_name: str) -> bool:
                    if team_name in lowered:
                        return True
                    return any(tok in lowered for tok in team_name.split() if len(tok) > 3)

                if _hit(home) and _hit(away):
                    candidates.append(fixture)
        if len(candidates) == 1:
            return candidates[0].match_id
        return None

    async def handle_message(self, session_id: str, message: str, match_id: str | None) -> ChatResponse:
        context = self._context_store.get_or_create(session_id)
        context.add_turn("user", message, self._settings.chat_context_max_turns)

        if _looks_like_daily_surprise_request(message):
            try:
                reply, shown_ids = await self._daily_surprises(message)
                context.previous_intent = "daily_surprises"
                context.last_surprise_query = message
                context.last_surprise_match_ids = shown_ids
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

        # Sürpriz/fikstür listesi üzerine gelen doğal devam mesajları ("başka
        # üst liglerden ver", "3 maç daha ver", "farklı maçlar var mı" gibi)
        # sürpriz/maç anahtar kelimesi içermeyebilir; bu yüzden yukarıdaki
        # desenle yakalanamaz. Önceki niyet hâlâ sürpriz/fikstür listesiyse
        # VE mesaj belirli bir maçtan bahsetmiyorsa, arayüzden gelen (ve o an
        # ekranda açık olan alakasız bir maça ait olabilecek) match_id'yi
        # görmezden gelip önceki sorguyu yeni mesajla birleştirip, daha önce
        # gösterilen maçları hariç tutarak tekrar çalıştırıyoruz.
        if (
            context.previous_intent in ("daily_surprises", "fixture_list")
            and not _looks_like_match_question(message)
            and not _looks_like_daily_surprise_request(message)
        ):
            resolved_team_match = await self._resolve_match_by_teams(message)
            if resolved_team_match is None and (
                _SURPRISE_CONTINUATION_PATTERN.search(message) or _wants_european_leagues(message)
            ):
                combined_query = f"{context.last_surprise_query or ''} {message}".strip()
                try:
                    reply, shown_ids = await self._daily_surprises(
                        combined_query, exclude_match_ids=frozenset(context.last_surprise_match_ids)
                    )
                    context.previous_intent = "daily_surprises"
                    context.last_surprise_query = combined_query
                    context.last_surprise_match_ids = context.last_surprise_match_ids + shown_ids
                    context.add_turn("assistant", reply, self._settings.chat_context_max_turns)
                    return ChatResponse(
                        session_id=session_id, reply=reply, intent="match_analysis",
                        match_id=None, used_prediction_engine=True, grounded_in_analysis=True,
                    )
                except Exception:
                    pass  # düşer, aşağıdaki genel akışa devam eder

        # §5 TODAY_MATCHES/EVENING_MATCHES: sürpriz istenmeyen düz fikstür soruları da
        # gerçek veriyle cevaplanmalı -- LLM'e bırakılırsa fikstür uydurma riski doğar.
        # Sadece aktif bir maç bağlamı YOKSA devreye girer (var olan maç sohbetini ezmez).
        effective_match_id = match_id or context.active_match_id
        if (
            effective_match_id is None
            and _looks_like_fixture_list_request(message)
            and not _looks_like_match_question(message)
        ):
            try:
                reply = await self._list_matches(message)
                context.previous_intent = "fixture_list"
                context.add_turn("assistant", reply, self._settings.chat_context_max_turns)
                return ChatResponse(
                    session_id=session_id, reply=reply, intent="fixture_list",
                    match_id=None, used_prediction_engine=False, grounded_in_analysis=True,
                )
            except Exception:
                reply = "Fikstür verisine şu anda ulaşılamıyor. Veri kaynağında geçici bir sorun olabilir."
                context.add_turn("assistant", reply, self._settings.chat_context_max_turns)
                return ChatResponse(
                    session_id=session_id, reply=reply, intent="fallback", match_id=None,
                    used_prediction_engine=False, grounded_in_analysis=False,
                )

        wants_match_analysis = bool(match_id) or (
            context.active_match_id is not None and _looks_like_match_question(message)
        )
        if wants_match_analysis and effective_match_id:
            return await self._handle_match_question(session_id, context, message, effective_match_id)

        # Mesajda takım isimleri geçiyorsa (örn. "Fenerbahçe Beşiktaş maçında ne olur?"),
        # genel sohbet fallback'ine düşmeden önce gerçek fikstürü bulup Cloud Engine'e bağla.
        if effective_match_id is None:
            resolved_id = await self._resolve_match_by_teams(message)
            if resolved_id:
                return await self._handle_match_question(session_id, context, message, resolved_id)

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
