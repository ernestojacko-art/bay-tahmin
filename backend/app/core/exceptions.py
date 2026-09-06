"""
Custom exception hierarchy.

These exceptions let every layer fail loudly and specifically instead of
silently falling back to fabricated data. The Golden Rule of this project
is: if we don't have reliable data, we say so -- we never invent it.
"""


class BayTahminError(Exception):
    """Base class for all application-specific errors."""


class ProviderError(BayTahminError):
    """Raised when an upstream data provider fails or is misconfigured."""


class ProviderNotConfiguredError(ProviderError):
    """Raised when no live data provider is wired up for a requested source."""


class InsufficientDataError(BayTahminError):
    """
    Raised (or converted to a low-confidence result) when there is not
    enough reliable data to produce a meaningful analysis.

    Per specification section 5 & 20: the system must never fabricate
    statistics to fill this gap.
    """


class DataQualityError(BayTahminError):
    """Raised when incoming data fails normalization / sanity validation."""


class ContradictionUnresolvedError(BayTahminError):
    """
    Raised when the Sanity & Contradiction Engine detects a contradiction
    that is severe enough that no confident output should be published
    (e.g. definitely-wrong team/match identifiers).
    """


class MatchNotFoundError(BayTahminError):
    """Raised when a requested match/fixture cannot be located."""


class ChatContextError(BayTahminError):
    """Raised on invalid or expired conversational context."""
