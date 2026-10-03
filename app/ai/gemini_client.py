from __future__ import annotations

import base64
import logging
import time
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings

log = logging.getLogger(__name__)


class GeminiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class GeminiTransientError(GeminiError):
    pass


class GeminiClient:
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15))
        self.last_http_status: int | None = None
        self._blocked_until = 0.0
        self._blocked_status: int | None = None

    @property
    def ready(self) -> bool:
        return self.settings.has_gemini

    def _headers(self) -> dict[str, str]:
        if not self.settings.gemini_api_key:
            raise GeminiError("GEMINI_API_KEY is not configured")
        return {
            "x-goog-api-key": self.settings.gemini_api_key,
            "content-type": "application/json",
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.7, min=0.7, max=6),
        retry=retry_if_exception_type((httpx.TransportError, GeminiTransientError)),
        reraise=True,
    )
    async def _post(self, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        if time.monotonic() < self._blocked_until:
            raise GeminiError("AI configuration temporarily blocked", self._blocked_status)
        url = f"{self.BASE_URL}/{model}:generateContent"
        response = await self.client.post(url, headers=self._headers(), json=payload)
        self.last_http_status = response.status_code
        if response.status_code >= 400:
            if response.status_code in {401, 402, 403}:
                # A billing/key failure is project-wide. Never spend retries or
                # silently switch to a paid service/model to bypass it.
                self._blocked_until = time.monotonic() + 60
                self._blocked_status = response.status_code
            # Provider error bodies may contain request details. Do not echo or log them.
            error = (
                GeminiTransientError
                if response.status_code == 429 or response.status_code >= 500
                else GeminiError
            )
            raise error(
                f"AI provider HTTP {response.status_code}", status_code=response.status_code
            )
        self._blocked_status = None
        self._blocked_until = 0.0
        return response.json()

    async def agent_turn(
        self,
        *,
        system_prompt: str,
        contents: list[dict[str, Any]],
        function_declarations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 0.25,
                "maxOutputTokens": 5000,
            },
        }
        if function_declarations:
            payload["tools"] = [{"functionDeclarations": function_declarations}]
        return await self._post(self.settings.gemini_model, payload)

    async def research(self, query: str) -> str:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": query}]}],
            "tools": [{"googleSearch": {}}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 3500},
        }
        data = await self._post(self.settings.gemini_search_model, payload)
        return self.extract_text(data) or "No web-search answer returned."

    async def understand_media(self, *, data: bytes, mime_type: str, instruction: str) -> str:
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": instruction},
                        {
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": base64.b64encode(data).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
            "generationConfig": {"temperature": 0.15, "maxOutputTokens": 3000},
        }
        result = await self._post(self.settings.gemini_model, payload)
        return self.extract_text(result) or "I could not understand that media."

    @staticmethod
    def candidate_parts(data: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            return data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError):
            return []

    @classmethod
    def extract_text(cls, data: dict[str, Any]) -> str:
        chunks = [
            p.get("text", "")
            for p in cls.candidate_parts(data)
            if p.get("text") and not p.get("thought")
        ]
        return "\n".join(chunks).strip()

    @classmethod
    def extract_function_calls(cls, data: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for part in cls.candidate_parts(data):
            call = part.get("functionCall")
            if call:
                out.append(
                    {"name": call.get("name"), "args": call.get("args") or {}, "id": call.get("id")}
                )
        return out

    @staticmethod
    def model_content_from_response(data: dict[str, Any]) -> dict[str, Any]:
        try:
            return data["candidates"][0]["content"]
        except (KeyError, IndexError, TypeError):
            return {"role": "model", "parts": [{"text": ""}]}

    async def close(self) -> None:
        await self.client.aclose()
