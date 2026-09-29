"""
SÜRPRİZ VERİ - Historical Similar Match Finder

Güncel maçın:
1) ilk açılış 1X2 oranlarını geçmiş maçların ilk açılışlarıyla,
2) güncel 1X2 oranlarını geçmiş maçların kapanışlarıyla
ayrı ayrı karşılaştırır.

Açılış-kapanış hareketi artık ayrı bir benzerlik havuzu değildir.
Çünkü oranlar maç boyunca değişebilir; hareketi ayrıca eşleştirmek
ana tarihsel karşılaştırmayı gereksiz yere karmaşıklaştırır.

Ana amaç:
    "Bugünkü açılış oranına benzeyen geçmiş maçlarda ne oldu?"
    "Bugünkü güncel orana benzeyen geçmiş kapanışlarda ne oldu?"
    "İki tarihsel tablonun kesişiminde ne oldu?"
"""

from collections import Counter
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

from .config import CONFIG
from .models import HistoricalMatch, MatchRecord, Odds


def _close(a: Optional[float], b: Optional[float], tolerance: float) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tolerance


def _odds_component_tolerance(
    current_value: Optional[float],
    base_tolerance: float,
) -> float:
    """Oranın büyüklüğüne göre ölçeklenen tolerans."""
    if current_value is None:
        return base_tolerance
    return max(base_tolerance, abs(current_value) * 0.05)


def _odds_match(current: Odds, historical: Odds, tolerance: float) -> bool:
    return all(
        _close(c, h, _odds_component_tolerance(c, tolerance))
        for c, h in zip(current.values(), historical.values())
    )


def _odds_distance(current: Odds, historical: Odds, tolerance: float) -> float:
    values = []
    for c, h in zip(current.values(), historical.values()):
        if c is None or h is None:
            return 999.0
        component_tolerance = _odds_component_tolerance(c, tolerance)
        values.append(abs(c - h) / max(component_tolerance, 0.0001))
    return sum(values) / len(values) if values else 999.0


def _match_row(
    h: HistoricalMatch,
    match_type: str,
    distance: float,
) -> Dict[str, Any]:
    r = h.record
    outcome = r.outcome
    total_goals = outcome.other_markets.get("total_goals")

    # Eski kayıtların bazıları total_goals taşımayabilir.
    if total_goals is None and outcome.full_time_score:
        try:
            home, away = [
                int(x.strip())
                for x in outcome.full_time_score.replace("–", "-").split("-", 1)
            ]
            total_goals = home + away
        except (ValueError, TypeError):
            pass

    return {
        "match_id": r.match_id,
        "date": r.kickoff_time,
        "league": r.competition,
        "match": f"{r.home_team} - {r.away_team}",
        "home_team": r.home_team,
        "away_team": r.away_team,
        "opening_odds": asdict(r.opening_odds),
        "closing_odds": asdict(r.closing_odds),
        "opening_odds_text": _odds_text(r.opening_odds),
        "closing_odds_text": _odds_text(r.closing_odds),
        "movement_text": None,
        "first_half_score": outcome.first_half_score,
        "full_time_score": outcome.full_time_score,
        "ht_ft": outcome.ht_ft,
        "total_goals": total_goals,
        "match_type": match_type,
        "similarity_distance": round(distance, 4),
    }


def _odds_text(o: Odds) -> Optional[str]:
    if any(v is None for v in o.values()):
        return None
    return f"{o.home:.2f} / {o.draw:.2f} / {o.away:.2f}"


def _distribution(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counter = Counter(row["ht_ft"] for row in rows if row.get("ht_ft"))
    total = sum(counter.values())
    return [
        {
            "outcome": outcome,
            "count": count,
            "percentage": round((count / total) * 100, 1) if total else 0.0,
        }
        for outcome, count in counter.most_common()
    ]


def _find(
    current: MatchRecord,
    historical: List[HistoricalMatch],
    mode: str,
) -> List[Tuple[HistoricalMatch, float]]:
    if mode == "opening":
        tolerance = CONFIG.opening_odds_tolerance
        return sorted(
            [
                (
                    h,
                    _odds_distance(
                        current.opening_odds,
                        h.record.opening_odds,
                        tolerance,
                    ),
                )
                for h in historical
                if _odds_match(
                    current.opening_odds,
                    h.record.opening_odds,
                    tolerance,
                )
            ],
            key=lambda x: x[1],
        )

    if mode == "closing":
        tolerance = CONFIG.closing_odds_tolerance
        return sorted(
            [
                (
                    h,
                    _odds_distance(
                        current.closing_odds,
                        h.record.closing_odds,
                        tolerance,
                    ),
                )
                for h in historical
                if _odds_match(
                    current.closing_odds,
                    h.record.closing_odds,
                    tolerance,
                )
            ],
            key=lambda x: x[1],
        )

    raise ValueError(f"Unknown similarity mode: {mode}")


def _section(
    pairs: List[Tuple[HistoricalMatch, float]],
    mode: str,
    display_limit: int,
) -> Dict[str, Any]:
    rows = [_match_row(h, mode, distance) for h, distance in pairs]
    return {
        "match_count": len(rows),
        "sample_size_note": (
            "Küçük örneklem"
            if 0 < len(rows) < CONFIG.min_historical_samples
            else None
        ),
        "outcome_distribution": _distribution(rows),
        "matches": rows[:display_limit],
    }


def find_similar_matches(
    current: MatchRecord,
    historical: List[HistoricalMatch],
    display_limit: int = 20,
) -> Dict[str, Any]:
    """
    İki bağımsız tarihsel tablo ve bunların gerçek maç bazlı kesişimini döndürür.

    opening:
        Güncel maçın ilk açılış 1X2 oranı ↔ geçmiş maçların ilk açılış 1X2 oranı

    closing:
        Güncel maçın mevcut/güncel 1X2 oranı ↔ geçmiş maçların kapanış 1X2 oranı

    comparison:
        Hem açılış hem kapanış tablosunda bulunan aynı tarihsel maçlar.
        Bu bölüm, iki ayrı tarihsel gözlemin kesişimini gösterir.
    """
    display_limit = max(1, min(display_limit, CONFIG.max_result_limit))

    opening_pairs = _find(current, historical, "opening")
    closing_pairs = _find(current, historical, "closing")

    opening_ids = {h.record.match_id for h, _ in opening_pairs}
    closing_ids = {h.record.match_id for h, _ in closing_pairs}
    common_ids = opening_ids & closing_ids

    by_id = {h.record.match_id: h for h in historical}
    opening_distance = {h.record.match_id: d for h, d in opening_pairs}
    closing_distance = {h.record.match_id: d for h, d in closing_pairs}

    common_pairs: List[Tuple[HistoricalMatch, float]] = []
    for match_id in common_ids:
        h = by_id[match_id]
        common_pairs.append(
            (
                h,
                (
                    opening_distance.get(match_id, 999.0)
                    + closing_distance.get(match_id, 999.0)
                )
                / 2,
            )
        )
    common_pairs.sort(key=lambda x: x[1])

    opening = _section(opening_pairs, "opening", display_limit)
    closing = _section(closing_pairs, "closing", display_limit)
    comparison_rows = [
        _match_row(h, "comparison", distance)
        for h, distance in common_pairs
    ]

    opening_outcomes = {
        item["outcome"] for item in opening["outcome_distribution"]
    }
    closing_outcomes = {
        item["outcome"] for item in closing["outcome_distribution"]
    }
    common_outcomes = opening_outcomes & closing_outcomes

    comparison = {
        "match_count": len(comparison_rows),
        "sample_size_note": (
            "Küçük örneklem"
            if 0 < len(comparison_rows) < CONFIG.min_historical_samples
            else None
        ),
        "outcome_distribution": _distribution(comparison_rows),
        "matches": comparison_rows[:display_limit],
        "common_outcomes": sorted(common_outcomes),
        "opening_match_count": len(opening_pairs),
        "closing_match_count": len(closing_pairs),
        "description": (
            "Aynı geçmiş maçın hem açılış oranı hem de kapanış oranı "
            "bugünkü iki karşılaştırmaya yakınsa ortak eşleşme kabul edilir."
        ),
    }

    return {
        "opening": opening,
        "closing": closing,
        "comparison": comparison,
        # API/ön yüz eski sürümle konuşursa hata vermesin diye hareket
        # alanı boş tutuluyor; artık karar mekanizmasında kullanılmıyor.
        "movement": {
            "match_count": 0,
            "sample_size_note": None,
            "outcome_distribution": [],
            "matches": [],
            "deprecated": True,
        },
        "common": comparison,
    }
