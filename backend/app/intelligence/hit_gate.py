"""Ölçülmüş tutturma oranı kapısı.

Bir seçim türü ("1x2", "over", "under", "btts_yes", "btts_no", "dc") ancak, modelin o seçime
verdiği olasılık bandında GERÇEKTE (model_log'daki bitmiş maçlarda) en az MIN_HIT oranında
tutmuşsa ve yeterli örnek (MIN_SAMPLES) varsa kartlara girebilir. Tablo model_log'dan
periyodik olarak doldurulur (bkz. app.services.model_log.refresh_hit_table).
"""
from __future__ import annotations

import os

MIN_HIT = float(os.getenv("PICK_MIN_HIT_RATE", "0.60"))
MIN_SAMPLES = int(os.getenv("PICK_MIN_SAMPLES", "20"))

# (tür, bant) -> (örnek sayısı, tutturma oranı)
_table: dict[tuple[str, int], tuple[int, float]] = {}
_loaded = False


def band(prob: float) -> int:
    return min(9, int(prob * 10))


def set_table(table: dict[tuple[str, int], tuple[int, float]]) -> None:
    global _table, _loaded
    _table, _loaded = table, True


def table_snapshot() -> dict[str, dict]:
    return {f"{k[0]}:{k[1]*10}-{k[1]*10+10}%": {"n": v[0], "hit": round(v[1], 3)} for k, v in sorted(_table.items())}


def ok(kind: str, prob: float) -> bool:
    """Ölçüm yoksa ya da yetersizse güvenli tarafta kalır: False."""
    if not _loaded:
        return False
    n, hit = _table.get((kind, band(prob)), (0, 0.0))
    return n >= MIN_SAMPLES and hit >= MIN_HIT
