"""
Provider registry / factory.

Selects the active `BaseFootballDataProvider` implementation based on
`Settings.active_provider`. Adding a new real provider means writing a new
adapter class implementing `BaseFootballDataProvider` and registering it
here -- no other layer of the application needs to change.
"""
from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.core.exceptions import ProviderNotConfiguredError
from app.providers.base import BaseFootballDataProvider
from app.providers.null_provider import NullProvider
from app.providers.rest_football_provider import RestFootballDataProvider
from app.providers.sample_provider import SampleDataProvider
from app.providers.five_dollar_provider import FiveDollarFootballProvider

_SIMPLE_REGISTRY: dict[str, type[BaseFootballDataProvider]] = {
    "none": NullProvider,
    "sample-dev-only": SampleDataProvider,
}

_SETTINGS_AWARE_REGISTRY = {
    "rest-generic": RestFootballDataProvider,
}

_REAL_SIMPLE_REGISTRY = {
    "five_dollar_football_api": FiveDollarFootballProvider,
    "5dollarfootballapi": FiveDollarFootballProvider,
}


@lru_cache
def get_active_provider() -> BaseFootballDataProvider:
    settings = get_settings()
    provider_key = settings.active_provider.lower()

    if provider_key == "sample-dev-only" and settings.environment not in ("development", "test"):
        raise ProviderNotConfiguredError(
            "The sample-dev-only provider cannot be used outside development/test "
            "environments. Configure a real ACTIVE_PROVIDER/DATA_PROVIDER for production."
        )

    if provider_key in _REAL_SIMPLE_REGISTRY:
        return _REAL_SIMPLE_REGISTRY[provider_key]()

    if provider_key in _SETTINGS_AWARE_REGISTRY:
        provider_cls = _SETTINGS_AWARE_REGISTRY[provider_key]
        return provider_cls(settings)  # type: ignore[call-arg]

    provider_cls = _SIMPLE_REGISTRY.get(provider_key)
    if provider_cls is None:
        known = list(_SIMPLE_REGISTRY) + list(_SETTINGS_AWARE_REGISTRY) + list(_REAL_SIMPLE_REGISTRY)
        raise ProviderNotConfiguredError(
            f"Unknown ACTIVE_PROVIDER/DATA_PROVIDER '{settings.active_provider}'. "
            f"Known providers: {known}"
        )
    return provider_cls()


def reset_provider_cache() -> None:
    """Useful in tests when settings/env vars change between cases."""
    get_active_provider.cache_clear()
