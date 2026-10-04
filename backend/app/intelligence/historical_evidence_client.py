"""
Sürpriz Veri (tarihsel kanıt motoru) için isteğe bağlı, best-effort köprü.

Sürpriz Veri, ana Bay Tahmin backend'inden tamamen ayrı, kendi başına
deploy edilen bir servistir (bkz. surpriz_veri/README.md). Bu modül o
servisi HTTP üzerinden çağırır ve sonucu ana tahmin akışına ekler.

Tasarım ilkeleri:
- SURPRISE_VERI_URL ortam değişkeni tanımlı değilse bu modül hiçbir ağ
  isteği yapmaz ve sessizce None döner. Servis henüz deploy edilmemişse
  ana uygulama bundan hiçbir şekilde etkilenmez.
- Kısa bir zaman aşımı kullanılır; Sürpriz Veri servisi yavaş/erişilemez
  olsa bile ana maç analizi bunun için beklemez veya hata vermez.
- Her hata (zaman aşımı, bağlantı hatası, 4xx/5xx, bozuk JSON) sessizce
  yutulur ve None döner -- bu servise bağımlılık YOKTUR, sadece ek bilgi
  kaynağıdır.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 4.0


def _base_url() -> Optional[str]:
    url = os.getenv("SURPRISE_VERI_URL")
    if not url:
        return None
    return url.rstrip("/")


def is_configured() -> bool:
    return _base_url() is not None


async def fetch_raw_analysis(fixture_id: str) -> Optional[dict[str, Any]]:
    """Sürpriz Veri servisinin /analyze/{fixture_id} uç noktasını çağırır.

    Servis yapılandırılmamışsa veya herhangi bir hata oluşursa None döner.
    Bu fonksiyon hiçbir zaman exception fırlatmaz.
    """

    base = _base_url()
    if base is None:
        return None

    timeout_seconds = _DEFAULT_TIMEOUT_SECONDS
    try:
        timeout_seconds = float(
            os.getenv("SURPRISE_VERI_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT_SECONDS))
        )
    except (TypeError, ValueError):
        pass

    url = f"{base}/analyze/{fixture_id}"

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url)
        if response.status_code != 200:
            log.info(
                "Sürpriz Veri analizi alınamadı (HTTP %s): %s",
                response.status_code,
                url,
            )
            return None
        return response.json()
    except Exception as exc:  # noqa: BLE001 -- bu servise kasıtlı olarak bağımlı değiliz
        log.info("Sürpriz Veri servisine erişilemedi: %s", exc)
        return None


def to_historical_evidence(raw: Optional[dict[str, Any]]) -> "HistoricalEvidence":
    """Sürpriz Veri API cevabını MatchPrediction.historical_evidence şemasına çevirir."""

    # Döngüsel import'tan kaçınmak için burada import edilir.
    from app.schemas.prediction import HistoricalEvidence, HistoricalEvidenceCandidate

    if not raw:
        return HistoricalEvidence(available=False, note="Sürpriz Veri servisi yapılandırılmamış veya erişilemedi.")

    candidates_raw = raw.get("surprise_candidates") or []
    pool_sizes = raw.get("pool_sizes") or {}
    historical_sample_size = raw.get("historical_matches") or 0

    candidates = []
    for item in candidates_raw:
        if not isinstance(item, dict):
            continue
        try:
            candidates.append(
                HistoricalEvidenceCandidate(
                    outcome=str(item.get("outcome", "")),
                    score=float(item.get("score", 0.0)),
                    supporting_pools=list(item.get("supporting_pools") or []),
                    sample_sizes=dict(item.get("sample_sizes") or {}),
                    explanation=str(item.get("explanation", "")),
                )
            )
        except (TypeError, ValueError):
            continue

    note = raw.get("surprise_candidates_note")

    return HistoricalEvidence(
        available=True,
        historical_sample_size=int(historical_sample_size) if isinstance(historical_sample_size, (int, float)) else 0,
        pool_sizes={str(k): int(v) for k, v in pool_sizes.items() if isinstance(v, (int, float))},
        candidates=candidates,
        note=str(note) if note else None,
    )


async def fetch_historical_evidence(fixture_id: str) -> "HistoricalEvidence":
    """Sürpriz Veri'den tam, şemaya uygun HistoricalEvidence nesnesi getirir.

    Her koşulda (servis kapalı, hata, boş sonuç) geçerli bir HistoricalEvidence
    nesnesi döner -- çağıran taraf None kontrolü yapmak zorunda kalmaz.
    """

    raw = await fetch_raw_analysis(fixture_id)
    return to_historical_evidence(raw)
