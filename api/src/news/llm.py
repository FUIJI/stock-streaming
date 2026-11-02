"""LLM providers behind one small interface (LLM_PROVIDER / LLM_MODEL)."""

import os
from typing import Protocol

import httpx

GEMINI_URL = "https://generativelanguage.googleapis.com"


class LLMError(RuntimeError):
    """retryable: worth trying again later. bad_output: the call worked but the answer
    was unusable, so a smaller batch may succeed where this one failed."""

    def __init__(self, message: str, retryable: bool, bad_output: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.bad_output = bad_output


class LLMProvider(Protocol):
    name: str
    model: str

    async def generate_json(self, system: str, user: str, schema: dict) -> str:
        """Return the model's raw JSON text for `user`, constrained to `schema`."""
        ...

    async def aclose(self) -> None: ...


class GeminiProvider:
    """Gemini REST generateContent with structured output. No tools, no search grounding."""

    name = "gemini"

    def __init__(self, api_key: str, model: str, temperature: float | None = None,
                 thinking_level: str | None = None, base_url: str = GEMINI_URL,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.model = model
        self._temperature = temperature
        self._thinking_level = thinking_level
        self._http = httpx.AsyncClient(base_url=base_url, timeout=60, transport=transport,
                                       headers={"x-goog-api-key": api_key})

    async def generate_json(self, system: str, user: str, schema: dict) -> str:
        config: dict = {"responseMimeType": "application/json", "responseSchema": schema}
        if self._temperature is not None:
            config["temperature"] = self._temperature
        if self._thinking_level:
            config["thinkingConfig"] = {"thinkingLevel": self._thinking_level}
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }
        try:
            resp = await self._http.post(f"/v1beta/models/{self.model}:generateContent", json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"Gemini request failed: {exc}", retryable=True) from exc
        if resp.status_code >= 400:
            raise LLMError(f"Gemini {resp.status_code}: {resp.text[:300]}",
                           retryable=resp.status_code == 429 or resp.status_code >= 500)
        try:
            parts = resp.json()["candidates"][0]["content"]["parts"]
            # Thinking models may return thought parts first; the answer is the rest.
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"Gemini returned no content: {resp.text[:300]}",
                           retryable=True) from exc

    async def aclose(self) -> None:
        await self._http.aclose()


def provider_from_env() -> LLMProvider | None:
    """None when no provider is configured: articles are then shown untranslated."""
    name = os.environ.get("LLM_PROVIDER", "gemini").lower()
    if name in ("", "off", "none"):
        return None
    if name == "gemini":
        key = os.environ.get("GEMINI_API_KEY", "")
        if not key:
            return None
        temperature = os.environ.get("LLM_TEMPERATURE", "")
        return GeminiProvider(
            key,
            os.environ.get("LLM_MODEL", "gemini-3.8-flash"),
            temperature=float(temperature) if temperature else None,
            thinking_level=os.environ.get("LLM_THINKING_LEVEL", "low") or None,
            base_url=os.environ.get("GEMINI_BASE_URL", GEMINI_URL),
        )
    raise RuntimeError(f"Unknown LLM_PROVIDER: {name!r} (expected gemini|off)")
