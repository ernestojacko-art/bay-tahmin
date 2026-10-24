"""
Analysis Service.

Thin coordination layer used by the API and Chat layers: fetches (or
reuses cached) data, runs the Prediction Engine, and caches the result.
This is the only place that should know about both "provider I/O" and
"intelligence computation" at once -- API routers and the Chat Agent
should not talk to providers or the Prediction Engine directly.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.cache.cache import match_analysis_cache, raw_dataset_cache
from app.core.config import Settings, TeamStrengthWeights, get_settings, get_team_strength_weights
from app.intelligence import historical_evidence_client
from app.intelligence.data_intelligence import DataIntelligenceLayer
from app.intelligence.prediction_engine import PredictionEngine
from app.providers.base import BaseFootballDataProvider
from app.providers.models import MatchRawDataset
from app.schemas.prediction import MatchPrediction

_log = logging.getLogger("analysis_store")
STORE_MAX_AGE_SECONDS = 6 * 3600  # bu süreden sonra kayıt "bayat" sayılır ve arka planda yenilenir
STORE_HARD_MAX_AGE_SECONDS = 36 * 3600  # bayat da olsa bu süreye kadar ANINDA gösterilir (kullanıcı beklemez)
_refreshing: set[str] = set()


async def _store_load(key: str, model=MatchPrediction):
    obj, _age = await _store_load_ex(key, model)
    return obj


async def _store_load_ex(key: str, model=MatchPrediction):
    """(nesne, yaş_saniye) döner; 36 saatten eskiyse (None, None)."""
    try:
        import accuracy
        rows = await accuracy._db("GET", "analysis_cache", params={"key": f"eq.{key}", "select": "payload,updated_at"})
        if not rows:
            return None, None
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(rows[0]["updated_at"].replace("Z", "+00:00"))).total_seconds()
        if age > STORE_HARD_MAX_AGE_SECONDS:
            return None, None
        return model.model_validate(rows[0]["payload"]), age
    except Exception as exc:  # noqa: BLE001
        _log.debug("kalıcı analiz okunamadı: %s", exc)
        return None, None


async def _store_save(key: str, prediction) -> None:
    try:
        import accuracy
        await accuracy._db("POST", "analysis_cache", params={"on_conflict": "key"}, prefer=True, json_body={
            "key": key, "payload": prediction.model_dump(mode="json"),
            "updated_at": datetime.now(timezone.utc).isoformat()})
    except Exception as exc:  # noqa: BLE001
        _log.debug("kalıcı analiz yazılamadı: %s", exc)


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

    def _schedule_refresh(self, match_id: str, include_evidence: bool) -> None:
        """Bayat kaydı kullanıcıyı bekletmeden, arka plan önceliğinde yeniler (maç başına tek seferde)."""
        key = f"{match_id}:{include_evidence}"
        if key in _refreshing:
            return
        _refreshing.add(key)

        async def _run() -> None:
            try:
                from app.core import api_gate
                api_gate.background.set(True)
                await self.analyze_match(match_id, force_refresh=True, include_evidence=include_evidence)
            except Exception as exc:  # noqa: BLE001
                _log.debug("arka plan yenileme başarısız (%s): %s", match_id, exc)
            finally:
                _refreshing.discard(key)

        asyncio.create_task(_run())

    async def get_dataset(self, match_id: str, use_cache: bool = True) -> MatchRawDataset:
        cache_key = f"dataset:{self._provider.name}:{match_id}"
        if use_cache:
            cached = raw_dataset_cache.get(cache_key)
            if cached is not None:
                return cached
        store_key = f"{self._provider.name}:{match_id}:dataset"
        if use_cache:
            stored, age = await _store_load_ex(store_key, MatchRawDataset)
            if stored is not None:
                raw_dataset_cache.set(cache_key, stored, self._settings.cache_default_ttl_seconds)
                if age is not None and age > STORE_MAX_AGE_SECONDS:
                    self._schedule_refresh(match_id, False)
                return stored
        dataset = await self._data_layer.build_match_dataset(match_id)
        raw_dataset_cache.set(cache_key, dataset, self._settings.cache_default_ttl_seconds)
        asyncio.create_task(_store_save(store_key, dataset))
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

        if not force_refresh:
            base = f"{self._provider.name}:{match_id}"
            stored, age = await _store_load_ex(f"{base}:full2")
            if stored is None:
                noev, age = await _store_load_ex(f"{base}:noev2")
                if noev is not None:
                    if include_evidence and historical_evidence_client.is_configured():
                        try:
                            noev.historical_evidence = await historical_evidence_client.fetch_historical_evidence(match_id)
                        except Exception:
                            pass
                        asyncio.create_task(_store_save(f"{base}:full2", noev))
                    stored = noev
            if stored is not None:
                if age is not None and age > STORE_MAX_AGE_SECONDS:
                    self._schedule_refresh(match_id, include_evidence)
                match_analysis_cache.set(cache_key, stored, self._settings.match_analysis_cache_ttl_seconds)
                return stored

        dataset = await self.get_dataset(match_id, use_cache=not force_refresh)
        prediction = self._prediction_engine.analyze(dataset)
        from app.services import model_log
        asyncio.create_task(model_log.log_prediction(
            prediction, dataset,
            source="live" if dataset.fixture.kickoff > datetime.now(timezone.utc) else "backtest"))

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
        asyncio.create_task(_store_save(
            f"{self._provider.name}:{match_id}:{'full2' if include_evidence else 'noev2'}", prediction))
        return prediction
