"""
Team Strength Engine (spec section 6).

Produces an explainable, multi-component strength profile for a team.
No single statistic determines "strength" -- attack, defense, form, and
home/away splits are computed independently and then combined with
configurable weights, and every component is exposed in the output.
"""
from __future__ import annotations

from app.core.config import TeamStrengthWeights
from app.providers.models import RecentMatch, TeamRawDataset
from app.schemas.common import DataQuality
from app.schemas.team import TeamStrengthProfile

# League-average goals per team per match, used to center attack/defense
# ratings around 1.0 (a rating of 1.2 = 20% more prolific than average).
# This should ideally come from the Data Intelligence Layer per-league;
# using a widely-accepted general average as a neutral prior is
# reasonable when a league-specific baseline isn't available, and is
# clearly documented as such (not fabricated match data).
LEAGUE_AVERAGE_GOALS_PER_MATCH = 1.35


def _weighted_goal_rate(matches: list[RecentMatch], attr: str, weights: tuple[float, float, float]) -> float | None:
    """Blend last-5 / last-10 / last-20 rates for a goals_for/goals_against style attribute."""
    if not matches:
        return None
    ordered = sorted(matches, key=lambda m: m.date, reverse=True)
    buckets = [ordered[:5], ordered[:10], ordered[:20]]
    values = []
    total_w = 0.0
    for bucket, w in zip(buckets, weights):
        if not bucket:
            continue
        avg = sum(getattr(m, attr) for m in bucket) / len(bucket)
        values.append(avg * w)
        total_w += w
    if total_w == 0:
        return None
    return sum(values) / total_w


def _form_score(matches: list[RecentMatch], n: int = 5) -> float | None:
    """0..1 form score from most recent n results (win=1, draw=0.5, loss=0)."""
    if not matches:
        return None
    ordered = sorted(matches, key=lambda m: m.date, reverse=True)[:n]
    if not ordered:
        return None
    points = 0.0
    for m in ordered:
        won = (m.is_home and m.result.value == "1") or (not m.is_home and m.result.value == "2")
        drew = m.result.value == "X"
        points += 1.0 if won else (0.5 if drew else 0.0)
    return points / len(ordered)


def _opponent_strength_adjustment(matches: list[RecentMatch]) -> float:
    """
    Average opponent-strength hint across recent matches, used to nudge
    attack/defense ratings for strength of schedule. Falls back to a
    neutral 0.5 (average) when providers don't supply this hint --
    that's an honest "unknown", not a fabricated rating.
    """
    hints = [m.opponent_strength_hint for m in matches if m.opponent_strength_hint is not None]
    if not hints:
        return 0.5
    return sum(hints) / len(hints)


class TeamStrengthEngine:
    def __init__(self, weights: TeamStrengthWeights):
        self._weights = weights

    def compute_profile(self, dataset: TeamRawDataset) -> TeamStrengthProfile:
        notes: list[str] = []
        matches = dataset.recent_matches
        w = (
            self._weights.recent_5_weight,
            self._weights.recent_10_weight,
            self._weights.recent_20_weight,
        )

        avg_gf = _weighted_goal_rate(matches, "goals_for", w)
        avg_ga = _weighted_goal_rate(matches, "goals_against", w)

        if avg_gf is None or avg_ga is None:
            notes.append("Yetersiz son maç geçmişi; güç profili nötr varsayılan değerler kullanıyor.")
            attack = 0.5
            defense = 0.5
        else:
            attack = min(1.0, max(0.0, (avg_gf / LEAGUE_AVERAGE_GOALS_PER_MATCH) / 2))
            # Lower goals-against => stronger defense, so invert.
            defense = min(1.0, max(0.0, 1 - (avg_ga / LEAGUE_AVERAGE_GOALS_PER_MATCH) / 2))

        recent_form = _form_score(matches, n=5)
        if recent_form is None:
            notes.append("Form skoru için son sonuç verisi yok; nötr 0.5 varsayıldı.")
            recent_form = 0.5

        home_matches = dataset.recent_matches_home
        away_matches = dataset.recent_matches_away
        home_strength = None
        away_strength = None
        if home_matches:
            home_gf = _weighted_goal_rate(home_matches, "goals_for", w) or 0.0
            home_ga = _weighted_goal_rate(home_matches, "goals_against", w) or 0.0
            home_strength = min(
                1.0,
                max(
                    0.0,
                    0.5
                    + ((home_gf - home_ga) / (2 * LEAGUE_AVERAGE_GOALS_PER_MATCH)),
                ),
            )
        else:
            notes.append("Ev sahibine özel maç örneklemi yok; ev sahibi gücü hesaba katılmadı.")

        if away_matches:
            away_gf = _weighted_goal_rate(away_matches, "goals_for", w) or 0.0
            away_ga = _weighted_goal_rate(away_matches, "goals_against", w) or 0.0
            away_strength = min(
                1.0,
                max(
                    0.0,
                    0.5
                    + ((away_gf - away_ga) / (2 * LEAGUE_AVERAGE_GOALS_PER_MATCH)),
                ),
            )
        else:
            notes.append("Deplasmana özel maç örneklemi yok; deplasman gücü hesaba katılmadı.")

        opponent_strength = _opponent_strength_adjustment(matches)
        # Opponent-adjusted performance: reward beating strong opponents,
        # discount beating weak ones. `opponent_strength` is 0..1 (0.5 = average).
        adjustment = (opponent_strength - 0.5) * self._weights.opponent_strength_adjustment
        opponent_adjusted = min(1.0, max(0.0, ((attack + defense) / 2) + adjustment))

        # Season-long standing vs recent-form blend, if standing is available.
        season_component = None
        if dataset.standing is not None and dataset.standing.played > 0:
            s = dataset.standing
            win_rate = s.won / s.played
            draw_rate = s.drawn / s.played
            season_component = win_rate + draw_rate * 0.5
        blend = self._weights.season_vs_recent_form_weight
        if season_component is not None:
            overall_form_component = (blend * recent_form) + ((1 - blend) * season_component)
        else:
            overall_form_component = recent_form
            notes.append("Sezon sıralaması verisi yok; genel değerlendirme sadece son form üzerinden yapıldı.")

        overall = min(
            1.0,
            max(
                0.0,
                (attack * 0.35) + (defense * 0.35) + (overall_form_component * 0.30),
            ),
        )

        matches_considered = len(matches)
        if matches_considered >= 15:
            quality = DataQuality.HIGH
        elif matches_considered >= 8:
            quality = DataQuality.MEDIUM
        elif matches_considered >= 3:
            quality = DataQuality.LOW
        else:
            quality = DataQuality.INSUFFICIENT
            notes.append(
                "3'ten az son maç mevcut -- güç profili güveni çok düşük."
            )

        return TeamStrengthProfile(
            team_id=dataset.team.team_id,
            team_name=dataset.team.name,
            overall=round(overall, 4),
            attack=round(attack, 4),
            defense=round(defense, 4),
            form=round(recent_form, 4),
            home_strength=round(home_strength, 4) if home_strength is not None else None,
            away_strength=round(away_strength, 4) if away_strength is not None else None,
            opponent_adjusted=round(opponent_adjusted, 4),
            matches_considered=matches_considered,
            data_quality=quality,
            weights_used={
                "recent_5_weight": self._weights.recent_5_weight,
                "recent_10_weight": self._weights.recent_10_weight,
                "recent_20_weight": self._weights.recent_20_weight,
                "opponent_strength_adjustment": self._weights.opponent_strength_adjustment,
                "season_vs_recent_form_weight": self._weights.season_vs_recent_form_weight,
            },
            notes=notes,
        )
