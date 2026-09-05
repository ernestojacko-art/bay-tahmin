"""Runtime request classifier guard for the production intelligence facade."""
from __future__ import annotations


def install(facade):
    original = getattr(facade, "_model_only_iyms_surprises", None)
    if original is None or getattr(original, "_request_guard", False):
        return

    async def guarded(message, rows):
        text = str(message or "")
        if not facade._is_iyms_request(text) or not facade._is_surprise_request(text):
            return None
        return await original(message, rows)

    guarded._request_guard = True
    facade._model_only_iyms_surprises = guarded
