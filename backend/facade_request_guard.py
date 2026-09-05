"""Runtime request classifier guard for the production intelligence facade."""
from __future__ import annotations


def install(facade):
    original = getattr(facade, "answer", None)
    if original is None or getattr(original, "_request_guard", False):
        return

    async def guarded(main, message, history=None):
        text = str(message or "")
        is_iyms = bool(facade._is_iyms_request(text))
        is_surprise = bool(facade._is_surprise_request(text))
        if not (is_iyms and is_surprise):
            # The facade's generic path must handle ordinary requests; its
            # HT/FT surprise scorer must never hijack "strongest picks" queries.
            return await facade._impl.answer(main, message, history or [])
        return await original(main, message, history or [])

    guarded._request_guard = True
    facade.answer = guarded
