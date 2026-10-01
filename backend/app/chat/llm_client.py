"""
Thin async LLM client (spec section 12: "LLM'İN ROLÜ").

The LLM is used ONLY to phrase/explain answers in natural Turkish -- it
never invents fixtures, dates, odds, or probabilities. If no provider is
configured, or the call fails for any reason, callers fall back to a
structured, template-based response instead.
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
        return bool(self._settings.llm_provider != "none" and self._settings.llm_api_key)

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

    async def _list_gemini_models(self, client: httpx.AsyncClient) -> list[str]:
        """Bu anahtarın gerçekten erişebildiği, generateContent destekleyen modelleri döndürür."""
        resp = await client.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": self._settings.llm_api_key or ""},
            params={"pageSize": 200},
        )
        resp.raise_for_status()
        names: list[str] = []
        for m in resp.json().get("models", []):
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                names.append(str(m.get("name", "")).removeprefix("models/"))
        return names

    @staticmethod
    def _pick_best_flash(names: list[str]) -> Optional[str]:
        """Metin sohbeti için uygun, hızlı ("flash") ve en güncel görünen modeli seç."""
        import re as _re

        def version_key(n: str) -> tuple:
            nums = [int(x) for x in _re.findall(r"\d+", n)]
            return tuple(nums)

        candidates = [
            n for n in names
            if "flash" in n and not any(bad in n for bad in ("image", "tts", "live", "audio", "thinking-exp", "embedding", "lite"))
        ]
        if not candidates:
            candidates = [n for n in names if "flash" in n] or names
        candidates.sort(key=version_key, reverse=True)
        return candidates[0] if candidates else None

    async def _call_gemini(self, system_prompt: str, user_message: str) -> Optional[str]:
        # Google Generative Language API (Gemini). Yeni "AQ." önekli anahtarlar
        # `?key=` yerine `x-goog-api-key` header'ı ile daha güvenilir çalışır.
        # Model adı 404 dönerse (bu anahtarda o model yoksa), erişilebilir
        # modelleri Google'dan sorup uygun bir "flash" modeli otomatik seçer.
        headers = {"x-goog-api-key": self._settings.llm_api_key or "", "content-type": "application/json"}
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_message}]}],
            "generationConfig": {"maxOutputTokens": 600},
        }

        async def _post(client: httpx.AsyncClient, model: str) -> httpx.Response:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            return await client.post(url, headers=headers, json=payload)

        async with httpx.AsyncClient(timeout=10.0) as client:
            model = getattr(self, "_resolved_gemini_model", None) or self._settings.llm_model or "gemini-2.5-flash"
            resp = await _post(client, model)

            if resp.status_code == 404:
                available = await self._list_gemini_models(client)
                logger.warning("Gemini model '%s' bulunamadı. Erişilebilir modeller: %s", model, available)
                picked = self._pick_best_flash(available)
                if not picked:
                    return None
                logger.warning("Gemini için otomatik seçilen model: %s", picked)
                self._resolved_gemini_model = picked
                resp = await _post(client, picked)

            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                return None
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "\n".join(p.get("text", "") for p in parts if p.get("text")).strip()
            return text or None
