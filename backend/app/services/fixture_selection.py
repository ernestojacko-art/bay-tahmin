"""Timezone-safe selection rules for fixtures offered as future matches.

This module deliberately fails closed: a fixture with a naive kickoff time
cannot be advertised as an upcoming match.  Providers must normalize their
timestamps before they reach this layer.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from app.providers.models import Fixture, FixtureStatus

ISTANBUL = ZoneInfo("Europe/Istanbul")
EVENING_START = time(17, 0)
EVENING_END = time(23, 59, 59)


def istanbul_today() -> date:
    """The product date; never depend on the server's local timezone."""
    return datetime.now(ISTANBUL).date()


def kickoff_utc(fixture: Fixture) -> datetime | None:
    """Return an aware UTC kickoff, or ``None`` for malformed provider data."""
    if fixture.kickoff.tzinfo is None or fixture.kickoff.utcoffset() is None:
        return None
    return fixture.kickoff.astimezone(timezone.utc)


def is_upcoming_scheduled(fixture: Fixture, now: datetime | None = None) -> bool:
    """A future list may contain only scheduled fixtures strictly after now."""
    kickoff = kickoff_utc(fixture)
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return (
        fixture.status in {FixtureStatus.NOT_STARTED, FixtureStatus.SCHEDULED, FixtureStatus.TIMED}
        and kickoff is not None
        and kickoff > reference
    )


def select_upcoming(
    fixtures: list[Fixture],
    *,
    target_date: date | None = None,
    evening: bool = False,
    now: datetime | None = None,
) -> list[Fixture]:
    """Filter fixtures by status, absolute time, Istanbul date and evening window."""
    selected: list[Fixture] = []
    for fixture in fixtures:
        if not is_upcoming_scheduled(fixture, now):
            continue
        local_kickoff = fixture.kickoff.astimezone(ISTANBUL)
        if target_date is not None and local_kickoff.date() != target_date:
            continue
        if evening and not (EVENING_START <= local_kickoff.timetz().replace(tzinfo=None) <= EVENING_END):
            continue
        selected.append(fixture)
    return sorted(selected, key=lambda fixture: fixture.kickoff)
