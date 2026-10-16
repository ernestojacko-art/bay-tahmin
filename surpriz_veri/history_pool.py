"""
SÜRPRİZ VERİ - Büyük Tarihsel Maç Havuzu (arka plan yükleyici)

Eski tasarım her analizde yalnızca 8 ligden 50'şer maç çekiyordu (~320 maç).
Oran bazlı birebir eşleştirme için bu havuz çok küçüktü: açılış/kapanış
havuzları çoğu zaman 0-2 maç döndürüyordu.

Bu modül tüm aktif liglerden, sayfa sayfa (önce her ligin 1. sayfası, sonra 2.
sayfası ...) GERÇEK bitmiş maçları ve açılış/kapanış oranlarını arka planda
toplar:

* Hız sınırı paylaşımlı olarak ``data_provider`` tarafından uygulanır (429'da
  yavaşlar), yükleyici bunun içinde çalışır; analiz istekleri beklemez.
* Havuz bellekte tutulur ve (varsa) diske pickle olarak yedeklenir; servis
  yeniden başlarsa kaldığı yerden değil ama diskteki havuzla hızlıca başlar.
* ``wait_for_minimum`` ilk analizde havuz henüz boşken kısa süre bekler.
* Hiçbir maç uydurulmaz: yalnızca API'den gelen, oranı ve sonucu olan gerçek
  maçlar eklenir.
"""

from __future__ import annotations

import logging
import os
import pickle
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from .data_mapper import map_fixture_to_match_record
from .data_provider import FootballAPIError, current_rpm
from .models import HistoricalMatch
from .result_mapper import map_fixture_result

logger = logging.getLogger("surpriz_veri.history_pool")

MAX_MATCHES = int(os.getenv("SURPRISE_HISTORY_MAX_MATCHES", "30000"))
MAX_PAGES_PER_LEAGUE = int(os.getenv("SURPRISE_HISTORY_PAGES_PER_LEAGUE", "8"))
PER_PAGE = 50
REFRESH_AFTER_SECONDS = int(os.getenv("SURPRISE_HISTORY_REFRESH_HOURS", "12")) * 3600
PERSIST_PATH = os.getenv("SURPRISE_HISTORY_PATH", "/tmp/surprise_history.pkl")
PERSIST_MAX_AGE_SECONDS = 48 * 3600
PERSIST_EVERY_PAGES = 25


def fixtures_to_matches(fixtures: List[Dict[str, Any]], source: str) -> List[HistoricalMatch]:
    """Ham fixture listesini gerçek açılış/kapanış oranı + sonucu olan kayıtlara çevirir.

    Gömülü oranı olmayan, 1X2'si çözümlenemeyen veya sonucu bilinmeyen maçlar
    atlanır (hiçbir havuzda eşleşemezler).
    """
    out: List[HistoricalMatch] = []
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            continue
        odds = fixture.get("odds") if isinstance(fixture.get("odds"), dict) else None
        if not odds:
            continue
        try:
            record = map_fixture_to_match_record(fixture, odds_data=odds)
            if record.opening_odds.home is None and record.closing_odds.home is None:
                continue
            record = replace(record, outcome=map_fixture_result(fixture))
            out.append(HistoricalMatch(record=record, source=source))
        except (ValueError, TypeError):
            continue
    return out


class HistoryPool:
    def __init__(self, client_factory: Callable[[], Any], source: str = "5DollarFootballAPI"):
        self._client_factory = client_factory
        self._source = source
        self._lock = threading.Lock()
        self._matches: Dict[str, HistoricalMatch] = {}
        self._thread: Optional[threading.Thread] = None
        self._loaded_event = threading.Event()
        self.status: Dict[str, Any] = {
            "state": "idle",            # idle | loading | complete | error
            "matches": 0,
            "leagues_total": 0,
            "leagues_done": 0,
            "pages_fetched": 0,
            "started_at": None,
            "finished_at": None,
            "last_error": None,
        }
        self._finished_ts = 0.0
        self._load_from_disk()

    # ------------------------------------------------------------------ API
    def matches(self) -> List[HistoricalMatch]:
        with self._lock:
            return list(self._matches.values())

    def size(self) -> int:
        with self._lock:
            return len(self._matches)

    def ensure_started(self, days: int) -> None:
        """Yükleyiciyi (yoksa/bayatsa) başlatır; zaten çalışıyorsa dokunmaz."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            fresh = self._finished_ts and (time.time() - self._finished_ts) < REFRESH_AFTER_SECONDS
            if fresh and self.status["state"] == "complete":
                return
            self.status.update(state="loading", started_at=datetime.now(timezone.utc).isoformat(), last_error=None)
            self._thread = threading.Thread(target=self._run, args=(days,), name="surprise-history", daemon=True)
            self._thread.start()

    def wait_for_minimum(self, minimum: int, timeout: float) -> None:
        """Havuz henüz küçükse (ilk analiz), yükleyiciye kısa süre tanır."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.size() >= minimum or self.status["state"] in ("complete", "error"):
                return
            time.sleep(0.5)

    # ------------------------------------------------------------ internals
    def _add(self, matches: List[HistoricalMatch]) -> int:
        added = 0
        with self._lock:
            for m in matches:
                mid = str(m.record.match_id)
                if mid not in self._matches:
                    self._matches[mid] = m
                    added += 1
            self.status["matches"] = len(self._matches)
        if added:
            self._loaded_event.set()
        return added

    def _leagues(self, client, start_ts: int) -> List[int]:
        ids: List[int] = []
        seen = set()
        page = 1
        while page <= 20:
            try:
                payload = client.leagues(active_since=start_ts, page=page, per_page=100)
            except FootballAPIError as exc:
                logger.warning("lig listesi alınamadı (sayfa %s): %s", page, exc)
                break
            data = payload.get("data", [])
            if isinstance(data, dict):
                data = data.get("leagues") or data.get("items") or data.get("results") or []
            if not isinstance(data, list) or not data:
                break
            for league in data:
                if not isinstance(league, dict) or league.get("id") is None:
                    continue
                try:
                    lid = int(league["id"])
                except (TypeError, ValueError):
                    continue
                if lid not in seen:
                    seen.add(lid)
                    ids.append(lid)
            pagination = payload.get("pagination", {})
            if not isinstance(pagination, dict) or not pagination.get("has_more"):
                break
            page += 1
        return ids

    def _run(self, days: int) -> None:
        try:
            client = self._client_factory()
            now = datetime.now(timezone.utc)
            start_ts = int((now - timedelta(days=min(max(days, 1), 730))).timestamp())
            end_ts = int(now.timestamp())

            leagues = self._leagues(client, start_ts)
            self.status["leagues_total"] = len(leagues)
            logger.info("tarihsel havuz: %d lig bulundu, yükleme başlıyor (rpm=%.1f)", len(leagues), current_rpm())

            active = list(leagues)
            pages = 0
            for page in range(1, MAX_PAGES_PER_LEAGUE + 1):
                still_active: List[int] = []
                for league_id in active:
                    if self.size() >= MAX_MATCHES:
                        break
                    try:
                        payload = client.league_fixtures(
                            league_id=league_id, start_time=start_ts, end_time=end_ts,
                            status="finished", include="odds,events,stats",
                            page=page, per_page=PER_PAGE, order="desc",
                        )
                    except FootballAPIError as exc:
                        logger.warning("lig %s sayfa %s alınamadı: %s", league_id, page, exc)
                        self.status["last_error"] = str(exc)[:200]
                        continue
                    pages += 1
                    self.status["pages_fetched"] = pages
                    rows = client.flatten_fixture_list(payload)
                    self._add(fixtures_to_matches(rows, self._source))
                    pagination = payload.get("pagination", {}) if isinstance(payload, dict) else {}
                    if rows and isinstance(pagination, dict) and pagination.get("has_more"):
                        still_active.append(league_id)
                    else:
                        self.status["leagues_done"] += 1
                    if pages % PERSIST_EVERY_PAGES == 0:
                        self._save_to_disk()
                        logger.info("tarihsel havuz: %d maç (%d sayfa, rpm=%.1f)", self.size(), pages, current_rpm())
                active = still_active
                if not active or self.size() >= MAX_MATCHES:
                    break

            self._finished_ts = time.time()
            self.status.update(state="complete", finished_at=datetime.now(timezone.utc).isoformat())
            self._save_to_disk()
            logger.info("tarihsel havuz tamamlandı: %d maç, %d sayfa", self.size(), pages)
        except Exception as exc:  # arka plan iş parçacığı asla sessizce ölmesin
            logger.exception("tarihsel havuz yüklenemedi: %s", exc)
            self.status.update(state="error", last_error=str(exc)[:200])
        finally:
            self._loaded_event.set()

    # ----------------------------------------------------------- persistence
    def _save_to_disk(self) -> None:
        try:
            tmp = f"{PERSIST_PATH}.tmp"
            with open(tmp, "wb") as fh:
                pickle.dump({"saved_at": time.time(), "matches": self.matches()}, fh)
            os.replace(tmp, PERSIST_PATH)
        except Exception as exc:
            logger.warning("tarihsel havuz diske yazılamadı: %s", exc)

    def _load_from_disk(self) -> None:
        try:
            if not os.path.exists(PERSIST_PATH):
                return
            with open(PERSIST_PATH, "rb") as fh:  # kendi yazdığımız dosya
                blob = pickle.load(fh)
            if time.time() - float(blob.get("saved_at", 0)) > PERSIST_MAX_AGE_SECONDS:
                return
            self._add(list(blob.get("matches", [])))
            logger.info("tarihsel havuz diskten yüklendi: %d maç", self.size())
        except Exception as exc:
            logger.warning("tarihsel havuz diskten okunamadı: %s", exc)
