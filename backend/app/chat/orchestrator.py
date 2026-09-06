"""
BAY TAHMİN Chat Orchestrator (spec section 3, 12, 13, 22).

    KULLANICI / FRONTEND
            |
            v
    BAY TAHMİN CHAT ORCHESTRATOR
            |
    +-------+--------------------+
    |                            |
    v                            v
GENERAL FOOTBALL EXPERT   MATCH / PREDICTION REQUEST
                                 |
                                 v
                     FOOTBALL INTELLIGENCE ENGINE
                                 |
                                 v
                       ANALYSIS + PROJECTIONS
                                 |
                                 v
                     NATURAL LANGUAGE RESPONSE

GOLDEN RULE enforced here: for match/prediction questions, this module
NEVER invents numbers. It only narrates fields already computed by the
Prediction Engine (or, when an LLM is configured, asks the LLM to
*phrase* those already-computed numbers -- never to invent new ones).
"""
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


def _looks_like_match_question(message: str) -> bool:
    return bool(_MATCH_INTENT_PATTERNS.search(message))


def _narrate_half_time(prediction: MatchPrediction) -> str:
    ht = prediction.half_time_one_x_two
    return (
        f"İlk yarı olasılıkları -- Ev sahibi önde: %{ht.home_win*100:.1f}, "
        f"Beraberlik: %{ht.draw*100:.1f}, Deplasman önde: %{ht.away_win*100:.1f}."
    )


def _narrate_surprises(prediction: MatchPrediction) -> str:
    if not prediction.surprises:
        return "Bu maç için öne çıkan bir İY/MS sürpriz senaryosu bulunmuyor."
    lines = ["Öne çıkan sürpriz İY/MS senaryoları:"]
    for s in prediction.surprises:
        lines.append(
            f"- {s.combination}: {s.description} (uyum skoru: {s.composite_score:.2f}, "
            f"risk: {s.risk.value})"
        )
    return "\n".join(lines)


def _narrate_confidence(prediction: MatchPrediction) -> str:
    c = prediction.confidence
    warning_note = ""
    if c.warnings:
        warning_note = " Not: " + c.warnings[0]
    return (
        f"En yüksek olasılıklı sonuca güvenim: %{c.confidence*100:.1f} "
        f"(risk seviyesi: {c.risk.value}, veri kalitesi: {c.data_quality.value})." + warning_note
    )


def _narrate_summary(prediction: MatchPrediction) -> str:
    ox = prediction.one_x_two
    eg = prediction.expected_goals
    fav_scenario = next((s for s in prediction.scenarios if s.scenario_type == "favorite"), None)
    lines = [
        f"1X2 olasılıkları -- Ev: %{ox.home_win*100:.1f}, Beraberlik: %{ox.draw*100:.1f}, "
        f"Deplasman: %{ox.away_win*100:.1f}.",
        f"Beklenen gol: {eg.home_xg:.2f} - {eg.away_xg:.2f} (toplam {eg.total_xg:.2f}).",
        f"KG Var olasılığı: %{prediction.btts_yes_probability*100:.1f}.",
    ]
    if fav_scenario:
        lines.append(fav_scenario.description)
    lines.append(_narrate_confidence(prediction))
    if prediction.disclaimers:
        lines.append(prediction.disclaimers[0])
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

            # Optionally ask the LLM to *rephrase* (never regenerate numbers).
            if self._llm_client.is_configured:
                system_prompt = (
                    "Rewrite the following football match analysis in natural, professional, "
                    "conversational language, in the same language the underlying text is "
                    "written in. Do not change, invent, or add any numbers, probabilities, or "
                    "team names beyond what is given."
                )
                llm_text = await self._llm_client.generate(system_prompt, reply)
                if llm_text:
                    reply = llm_text

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
        except MatchNotFoundError as exc:
            reply = f"Bu maçı bulamadım: {exc}"
            context.add_turn("assistant", reply, self._settings.chat_context_max_turns)
            return ChatResponse(
                session_id=session_id,
                reply=reply,
                intent="fallback",
                match_id=match_id,
                used_prediction_engine=False,
                grounded_in_analysis=False,
            )
        except BayTahminError as exc:
            # Structured fallback per spec section 16 -- never a bare "unreachable" error.
            reply = (
                "Bu maç için şu anda tam analiz üretemedim "
                f"({exc}). Genel bir futbol sohbeti yapabilir ya da başka bir maç deneyebiliriz."
            )
            context.add_turn("assistant", reply, self._settings.chat_context_max_turns)
            return ChatResponse(
                session_id=session_id,
                reply=reply,
                intent="fallback",
                match_id=match_id,
                used_prediction_engine=False,
                grounded_in_analysis=False,
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
