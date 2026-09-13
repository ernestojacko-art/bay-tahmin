from __future__ import annotations

from datetime import datetime, timezone

from app.providers.models import Fixture, FixtureStatus, TeamRef
from app.services.fixture_selection import select_upcoming


def _fixture(match_id: str, kickoff: datetime, status: FixtureStatus = FixtureStatus.SCHEDULED) -> Fixture:
    return Fixture(
        match_id=match_id,
        league_id="L1",
        kickoff=kickoff,
        home_team=TeamRef(team_id="H", name="Home"),
        away_team=TeamRef(team_id="A", name="Away"),
        status=status,
    )


def test_future_filter_excludes_finished_live_cancelled_and_past_fixtures():
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    fixtures = [
        _fixture("upcoming", datetime(2026, 9, 13, 18, tzinfo=timezone.utc)),
        _fixture("past", datetime(2026, 9, 13, 11, tzinfo=timezone.utc)),
        _fixture("finished", datetime(2026, 9, 13, 18, tzinfo=timezone.utc), FixtureStatus.FINISHED),
        _fixture("live", datetime(2026, 9, 13, 18, tzinfo=timezone.utc), FixtureStatus.LIVE),
        _fixture("cancelled", datetime(2026, 9, 13, 18, tzinfo=timezone.utc), FixtureStatus.CANCELLED),
    ]

    assert [item.match_id for item in select_upcoming(fixtures, now=now)] == ["upcoming"]


def test_evening_window_uses_europe_istanbul_not_the_server_timezone():
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    fixtures = [
        # 17:00 and 23:59 in Istanbul are included.
        _fixture("start", datetime(2026, 9, 13, 14, tzinfo=timezone.utc)),
        _fixture("end", datetime(2026, 9, 13, 20, 59, tzinfo=timezone.utc)),
        _fixture("late", datetime(2026, 9, 13, 21, tzinfo=timezone.utc)),
    ]

    selected = select_upcoming(fixtures, target_date=now.date(), evening=True, now=now)
    assert [item.match_id for item in selected] == ["start", "end"]


def test_future_filter_rejects_naive_kickoff_instead_of_guessing_its_timezone():
    now = datetime(2026, 9, 13, 12, tzinfo=timezone.utc)
    naive = _fixture("ambiguous", datetime(2026, 9, 13, 18))

    assert select_upcoming([naive], now=now) == []
