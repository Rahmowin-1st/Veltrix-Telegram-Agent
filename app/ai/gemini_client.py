from __future__ import annotations

import base64
import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import Settings

log = logging.getLogger(__name__)


class GeminiError(RuntimeError):
    pass


class GeminiClient:
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15))

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

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.7, min=0.7, max=6), reraise=True)
    async def _post(self, model: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.BASE_URL}/{model}:generateContent"
        response = await self.client.post(url, headers=self._headers(), json=payload)
        if response.status_code >= 400:
            body = response.text[:1000]
            raise GeminiError(f"Gemini HTTP {response.status_code}: {body}")
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
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": instruction},
                    {"inlineData": {"mimeType": mime_type, "data": base64.b64encode(data).decode("ascii")}},
                ],
            }],
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
        chunks = [p.get("text", "") for p in cls.candidate_parts(data) if p.get("text")]
        return "\n".join(chunks).strip()

    @classmethod
    def extract_function_calls(cls, data: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for part in cls.candidate_parts(data):
            call = part.get("functionCall")
            if call:
                out.append({"name": call.get("name"), "args": call.get("args") or {}})
        return out

    @staticmethod
    def model_content_from_response(data: dict[str, Any]) -> dict[str, Any]:
        try:
            return data["candidates"][0]["content"]
        except (KeyError, IndexError, TypeError):
            return {"role": "model", "parts": [{"text": ""}]}

    async def close(self) -> None:
        await self.client.aclose()
