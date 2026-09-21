"""
Optional LLM client wrapper.

The Football Expert Chat Agent can optionally use an external LLM to make
its natural-language phrasing richer. This is entirely optional and the
system MUST keep working (spec section 16: "LLM yavaşlarsa veya
başarısız olursa sistem tamamen çöküp 'ulaşılamadı' dememeli") when:
  * no LLM is configured (`llm_provider = "none"`), or
  * the LLM call fails or times out.

In both cases `generate()` returns `None`, and callers fall back to the
structured, template-based response built directly from Intelligence
Engine output.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, settings: Settings):
        self._settings = settings

    @property
    def is_configured(self) -> bool:
        return self._settings.llm_provider != "none" and bool(self._settings.llm_api_key)

    async def generate(self, system_prompt: str, user_message: str, timeout_seconds: float = 8.0) -> Optional[str]:
        """
        Attempt to generate a natural-language response via the configured
        LLM provider. Returns None (never raises) on any failure so the
        caller can fall back to a structured response.
        """
        if not self.is_configured:
            return None

        try:
            if self._settings.llm_provider == "anthropic":
                return await asyncio.wait_for(
                    self._call_anthropic(system_prompt, user_message), timeout=timeout_seconds
                )
            if self._settings.llm_provider in ("gemini", "google"):
                return await asyncio.wait_for(
                    self._call_gemini(system_prompt, user_message), timeout=timeout_seconds
                )
            logger.warning("Unsupported llm_provider '%s'; falling back.", self._settings.llm_provider)
            return None
        except Exception:  # noqa: BLE001 - any failure must degrade gracefully
            logger.exception("LLM call failed; falling back to structured response.")
            return None

    async def _call_anthropic(self, system_prompt: str, user_message: str) -> Optional[str]:
        headers = {
            "x-api-key": self._settings.llm_api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self._settings.llm_model,
            "max_tokens": 600,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages", headers=headers, json=payload
            )
            resp.raise_for_status()
            data = resp.json()
            parts = [block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"]
            text = "\n".join(p for p in parts if p).strip()
            return text or None

    async def _call_gemini(self, system_prompt: str, user_message: str) -> Optional[str]:
        # Google Generative Language API (Gemini). settings.llm_model should
        # be a bare model id, e.g. "gemini-2.5-flash" or "gemini-2.0-flash".
        model = self._settings.llm_model or "gemini-2.5-flash"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_message}]}],
            "generationConfig": {"maxOutputTokens": 600},
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                params={"key": self._settings.llm_api_key or ""},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                return None
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "\n".join(p.get("text", "") for p in parts if p.get("text")).strip()
            return text or None
