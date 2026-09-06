"""
Sample / fixture data provider -- FOR TESTS AND LOCAL DEVELOPMENT ONLY.

This is NOT a real football data integration. It returns a small, fixed
set of hand-written sample matches so that the Intelligence Engine's
math (Team Strength, Poisson/Dixon-Coles, Scenario, Surprise, Sanity,
Confidence) can be exercised and unit-tested without a live API key.

It must never be selected via `ACTIVE_PROVIDER` in a production
environment, and the data it returns must never be presented to end
users as real statistics. `app/main.py` refuses to start with this
provider active unless `ENVIRONMENT=development` or `ENVIRONMENT=test`.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.providers.base import BaseFootballDataProvider
from app.providers.models import (
    Fixture,
    FixtureCongestion,
    FixtureStatus,
    H2HRecord,
    MatchResult,
    OddsMarket,
    OddsSelection,
    RecentMatch,
    StandingsEntry,
    TeamRawDataset,
    TeamRef,
)

_TEAM_A = TeamRef(team_id="T100", name="Sample United", league_id="L1")
_TEAM_B = TeamRef(team_id="T200", name="Sample City", league_id="L1")

_FIXTURE_ID = "M1"


def _make_recent_matches(
    team: TeamRef,
    opponent_pool: list[TeamRef],
    n: int,
    home_bias: float,
    scoring_bias: float,
    seed: int,
) -> list[RecentMatch]:
    """Deterministically build a plausible-looking recent-form sample."""
    matches: list[RecentMatch] = []
    rng_state = seed
    for i in range(n):
        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        is_home = (rng_state % 100) / 100 < home_bias
        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        gf = int(round(max(0, scoring_bias + ((rng_state % 300) / 100 - 1.5))))
        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        ga = int(round(max(0, 1.1 + ((rng_state % 300) / 100 - 1.5))))
        opponent = opponent_pool[i % len(opponent_pool)]
        if gf > ga:
            result = MatchResult.HOME_WIN if is_home else MatchResult.AWAY_WIN
        elif gf < ga:
            result = MatchResult.AWAY_WIN if is_home else MatchResult.HOME_WIN
        else:
            result = MatchResult.DRAW
        matches.append(
            RecentMatch(
                match_id=f"sample-{team.team_id}-{i}",
                date=date.today() - timedelta(days=7 * (n - i)),
                is_home=is_home,
                opponent=opponent,
                goals_for=gf,
                goals_against=ga,
                xg_for=round(gf * 0.9 + 0.3, 2),
                xg_against=round(ga * 0.9 + 0.3, 2),
                shots_for=10 + gf,
                shots_on_target_for=4 + gf,
                shots_against=8 + ga,
                shots_on_target_against=3 + ga,
                result=result,
                opponent_strength_hint=0.5,
            )
        )
    return matches


class SampleDataProvider(BaseFootballDataProvider):
    name = "sample-dev-only"

    def __init__(self) -> None:
        self._teams = {"T100": _TEAM_A, "T200": _TEAM_B}
        self._other_teams = [
            TeamRef(team_id=f"T{300+i}", name=f"Opponent FC {i}", league_id="L1") for i in range(6)
        ]

    async def get_fixtures(self, on_date: date, league_id: Optional[str] = None) -> list[Fixture]:
        return [
            Fixture(
                match_id=_FIXTURE_ID,
                league_id="L1",
                league_name="Sample League",
                kickoff=datetime.combine(on_date, datetime.min.time(), tzinfo=timezone.utc)
                + timedelta(hours=18),
                home_team=_TEAM_A,
                away_team=_TEAM_B,
                status=FixtureStatus.SCHEDULED,
                round="Sample Round 1",
            )
        ]

    async def get_fixture(self, match_id: str) -> Optional[Fixture]:
        if match_id != _FIXTURE_ID:
            return None
        fixtures = await self.get_fixtures(date.today())
        return fixtures[0]

    async def get_team_dataset(self, team_id: str, league_id: Optional[str] = None) -> TeamRawDataset:
        team = self._teams.get(team_id)
        if team is None:
            team = TeamRef(team_id=team_id, name=f"Unknown Team {team_id}", league_id=league_id)
        home_bias = 0.55 if team_id == "T100" else 0.45
        scoring_bias = 1.7 if team_id == "T100" else 1.3
        all_matches = _make_recent_matches(
            team, self._other_teams, 20, home_bias, scoring_bias, seed=hash(team_id) % 1000
        )
        return TeamRawDataset(
            team=team,
            recent_matches=all_matches,
            recent_matches_home=[m for m in all_matches if m.is_home],
            recent_matches_away=[m for m in all_matches if not m.is_home],
            standing=StandingsEntry(
                team=team,
                position=3 if team_id == "T100" else 9,
                played=20,
                won=12 if team_id == "T100" else 7,
                drawn=4,
                lost=4 if team_id == "T100" else 9,
                goals_for=sum(m.goals_for for m in all_matches),
                goals_against=sum(m.goals_against for m in all_matches),
                points=(12 if team_id == "T100" else 7) * 3 + 4,
            ),
            fixture_congestion=FixtureCongestion(
                matches_last_14_days=3, days_since_last_match=4, next_match_days_away=6
            ),
        )

    async def get_standings(self, league_id: str) -> list[StandingsEntry]:
        home_ds = await self.get_team_dataset("T100")
        away_ds = await self.get_team_dataset("T200")
        return [home_ds.standing, away_ds.standing]  # type: ignore[list-item]

    async def get_h2h(self, team_a_id: str, team_b_id: str, limit: int = 10) -> Optional[H2HRecord]:
        matches = _make_recent_matches(_TEAM_A, [_TEAM_B], min(limit, 5), 0.5, 1.4, seed=7)
        return H2HRecord(matches=matches)

    async def get_odds(self, match_id: str) -> list[OddsMarket]:
        # Deliberately included so Market Cross-Check has something to
        # exercise in tests. Real deployments get this from a real odds
        # provider (or nothing, which is equally valid).
        return [
            OddsMarket(
                market_name="1X2",
                selections=[
                    OddsSelection(label="1", price=1.85),
                    OddsSelection(label="X", price=3.60),
                    OddsSelection(label="2", price=4.20),
                ],
                bookmaker="sample-bookmaker",
            )
        ]
