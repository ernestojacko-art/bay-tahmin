"""
Analysis Service.

Thin coordination layer used by the API and Chat layers: fetches (or
reuses cached) data, runs the Prediction Engine, and caches the result.
This is the only place that should know about both "provider I/O" and
"intelligence computation" at once -- API routers and the Chat Agent
should not talk to providers or the Prediction Engine directly.
"""
from __future__ import annotations

from app.cache.cache import match_analysis_cache, raw_dataset_cache
from app.core.config import Settings, TeamStrengthWeights, get_settings, get_team_strength_weights
from app.intelligence import historical_evidence_client
from app.intelligence.data_intelligence import DataIntelligenceLayer
from app.intelligence.prediction_engine import PredictionEngine
from app.providers.base import BaseFootballDataProvider
from app.providers.models import MatchRawDataset
from app.schemas.prediction import MatchPrediction


class AnalysisService:
    def __init__(
        self,
        provider: BaseFootballDataProvider,
        settings: Settings | None = None,
        weights: TeamStrengthWeights | None = None,
    ):
        self._provider = provider
        self._settings = settings or get_settings()
        self._data_layer = DataIntelligenceLayer(
            provider, min_matches_for_reliable_form=self._settings.min_matches_for_reliable_form
        )
        self._prediction_engine = PredictionEngine(
            self._settings, weights or get_team_strength_weights()
        )

    async def get_dataset(self, match_id: str, use_cache: bool = True) -> MatchRawDataset:
        cache_key = f"dataset:{self._provider.name}:{match_id}"
        if use_cache:
            cached = raw_dataset_cache.get(cache_key)
            if cached is not None:
                return cached
        dataset = await self._data_layer.build_match_dataset(match_id)
        raw_dataset_cache.set(cache_key, dataset, self._settings.cache_default_ttl_seconds)
        return dataset

    async def analyze_match(
        self, match_id: str, force_refresh: bool = False, include_evidence: bool = True
    ) -> MatchPrediction:
        # include_evidence=False: Daily Picks yalnızca istatistiksel tahmini kullanır;
        # Sürpriz Veri servisine maç başına yapılan (yavaş) çağrı atlanır.
        cache_key = f"prediction:{self._provider.name}:{match_id}"
        if not include_evidence:
            cache_key += ":noev"
            cached_full = match_analysis_cache.get(f"prediction:{self._provider.name}:{match_id}")
            if cached_full is not None and not force_refresh:
                return cached_full
        if not force_refresh:
            cached = match_analysis_cache.get(cache_key)
            if cached is not None:
                return cached

        dataset = await self.get_dataset(match_id, use_cache=not force_refresh)
        prediction = self._prediction_engine.analyze(dataset)

        # Sürpriz Veri (ayrı, bağımsız tarihsel kanıt motoru) yapılandırılmışsa
        # sonucunu ekler. Yapılandırılmamışsa veya erişilemezse bu adım
        # tamamen sessiz kalır ve ana analiz akışını etkilemez.
        if include_evidence and historical_evidence_client.is_configured():
            try:
                prediction.historical_evidence = await historical_evidence_client.fetch_historical_evidence(
                    match_id
                )
            except Exception:
                pass

        match_analysis_cache.set(
            cache_key, prediction, self._settings.match_analysis_cache_ttl_seconds
        )
        return prediction
