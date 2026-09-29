"""
SÜRPRİZ VERİ - Historical Similar Match Finder

Güncel maçın 1X2 açılış, kapanış ve oran hareketini ayrı ayrı
geçmiş maçlarla karşılaştırır.

Ana amaç:
    "Bu oran profiline benzeyen geçmiş maçlarda gerçekte ne oldu?"

Bu modül tahmin skoru üretmez. Gerçek tarihsel kayıtları ve
gerçekleşmiş sonuç dağılımını döndürür.
"""

from collections import Counter
from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import CONFIG
from .models import HistoricalMatch, MatchRecord, Odds, OddsMovement


def _close(a: Optional[float], b: Optional[float], tolerance: float) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tolerance


def _odds_match(current: Odds, historical: Odds, tolerance: float) -> bool:
    return all(
        _close(c, h, tolerance)
        for c, h in zip(current.values(), historical.values())
    )


def _movement_values(movement: OddsMovement) -> List[Optional[float]]:
    return [movement.home, movement.draw, movement.away]


def _movement_match(
    current: OddsMovement,
    historical: OddsMovement,
    tolerance: float,
) -> bool:
    return all(
        _close(c, h, tolerance)
        for c, h in zip(_movement_values(current), _movement_values(historical))
    )


def _distance(
    current: Iterable[Optional[float]],
    historical: Iterable[Optional[float]],
    tolerance: float,
) -> float:
    values = []
    for c, h in zip(current, historical):
        if c is None or h is None:
            return 999.0
        values.append(abs(c - h) / max(tolerance, 0.0001))
    return sum(values) / len(values) if values else 999.0


def _match_row(h: HistoricalMatch, match_type: str, distance: float) -> Dict[str, Any]:
    r = h.record
    outcome = r.outcome
    total_goals = outcome.other_markets.get("total_goals")

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
        "movement_text": _movement_text(r.odds_movement),
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


def _movement_text(m: OddsMovement) -> Optional[str]:
    values = [m.home, m.draw, m.away]
    if any(v is None for v in values):
        return None
    return f"{m.home:+.2f} / {m.draw:+.2f} / {m.away:+.2f}"


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
        return [
            (
                h,
                _distance(current.opening_odds.values(), h.record.opening_odds.values(), tolerance),
            )
            for h in historical
            if _odds_match(current.opening_odds, h.record.opening_odds, tolerance)
        ]

    if mode == "closing":
        tolerance = CONFIG.closing_odds_tolerance
        return [
            (
                h,
                _distance(current.closing_odds.values(), h.record.closing_odds.values(), tolerance),
            )
            for h in historical
            if _odds_match(current.closing_odds, h.record.closing_odds, tolerance)
        ]

    if mode == "movement":
        # Hareket tek başına benzer maç kanıtı değildir.
        # Örneğin 1.17/5.50/15.00 -> 1.18/5.25/15.00
        # hareket olarak 0.00/-0.25/+0.03'e yakın görünebilir;
        # fakat 8.00/5.00/1.33 profiliyle ilgisizdir.
        #
        # Bu nedenle hareket havuzuna yalnızca:
        #   1) hareketi tolerans içinde benzer VE
        #   2) açılış VEYA kapanış 1X2 oran profili de tolerans içinde benzer
        # olan gerçek tarihsel maçlar alınır.
        move_tolerance = CONFIG.movement_tolerance
        opening_tolerance = CONFIG.opening_odds_tolerance
        closing_tolerance = CONFIG.closing_odds_tolerance
        rows = []
        for h in historical:
            movement_ok = _movement_match(
                current.odds_movement,
                h.record.odds_movement,
                move_tolerance,
            )
            if not movement_ok:
                continue

            opening_ok = _odds_match(
                current.opening_odds,
                h.record.opening_odds,
                opening_tolerance,
            )
            closing_ok = _odds_match(
                current.closing_odds,
                h.record.closing_odds,
                closing_tolerance,
            )
            if not (opening_ok or closing_ok):
                continue

            movement_distance = _distance(
                _movement_values(current.odds_movement),
                _movement_values(h.record.odds_movement),
                move_tolerance,
            )

            # Hareket eşleşmesini, ilgili açılış/kapanış profilinin
            # yakınlığıyla birlikte sıralıyoruz.
            profile_distances = []
            if opening_ok:
                profile_distances.append(
                    _distance(
                        current.opening_odds.values(),
                        h.record.opening_odds.values(),
                        opening_tolerance,
                    )
                )
            if closing_ok:
                profile_distances.append(
                    _distance(
                        current.closing_odds.values(),
                        h.record.closing_odds.values(),
                        closing_tolerance,
                    )
                )

            profile_distance = min(profile_distances)
            combined_distance = (movement_distance + profile_distance) / 2
            rows.append((h, combined_distance))

        return rows

    raise ValueError(f"Unknown similarity mode: {mode}")


def find_similar_matches(
    current: MatchRecord,
    historical: List[HistoricalMatch],
    display_limit: int = 20,
) -> Dict[str, Any]:
    """
    Açılış/kapanış/hareket için ayrı eşleşme grupları ve
    üç kriterin ortak kesişimini döndürür.
    """
    display_limit = max(1, min(display_limit, CONFIG.max_result_limit))

    groups: Dict[str, List[Tuple[HistoricalMatch, float]]] = {}
    ids_by_mode: Dict[str, set] = {}

    for mode in ("opening", "closing", "movement"):
        pairs = sorted(_find(current, historical, mode), key=lambda x: x[1])
        groups[mode] = pairs
        ids_by_mode[mode] = {h.record.match_id for h, _ in pairs}

    common_ids = (
        ids_by_mode["opening"]
        & ids_by_mode["closing"]
        & ids_by_mode["movement"]
    )

    by_id = {h.record.match_id: h for h in historical}
    common_pairs: List[Tuple[HistoricalMatch, float]] = []

    for match_id in common_ids:
        h = by_id[match_id]
        d_open = _distance(
            current.opening_odds.values(),
            h.record.opening_odds.values(),
            CONFIG.opening_odds_tolerance,
        )
        d_close = _distance(
            current.closing_odds.values(),
            h.record.closing_odds.values(),
            CONFIG.closing_odds_tolerance,
        )
        d_move = _distance(
            _movement_values(current.odds_movement),
            _movement_values(h.record.odds_movement),
            CONFIG.movement_tolerance,
        )
        common_pairs.append((h, (d_open + d_close + d_move) / 3))

    common_pairs.sort(key=lambda x: x[1])

    def section(mode: str) -> Dict[str, Any]:
        pairs = groups[mode]
        rows = [
            _match_row(h, mode, distance)
            for h, distance in pairs
        ]
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

    common_rows = [
        _match_row(h, "common", distance)
        for h, distance in common_pairs
    ]

    return {
        "opening": section("opening"),
        "closing": section("closing"),
        "movement": section("movement"),
        "common": {
            "match_count": len(common_rows),
            "sample_size_note": (
                "Küçük örneklem"
                if 0 < len(common_rows) < CONFIG.min_historical_samples
                else None
            ),
            "outcome_distribution": _distribution(common_rows),
            "matches": common_rows[:display_limit],
        },
    }
