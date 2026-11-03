"""LLM providers behind one small interface. Configuration from the environment
(models, fallback, quota) lives in managed.py."""

import time
from typing import Protocol

import httpx

GEMINI_URL = "https://generativelanguage.googleapis.com"
# The model answered but the text was withheld (e.g. RECITATION: too close to the source
# article). Another model would hit the same filter, so this is bad output, not an outage.
FILTERED_FINISH_REASONS = {"RECITATION", "SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"}


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

    async def generate_json(self, system: str, user: str, schema: dict,
                            max_tokens: int | None = None) -> str:
        """Return the model's raw JSON text for `user`, constrained to `schema`.
        Stops after `max_tokens` output tokens (an over-long answer is bad output)."""
        ...

    async def aclose(self) -> None: ...


class GeminiProvider:
    """Gemini REST generateContent with structured output. No tools, no search grounding."""

    name = "gemini"

    def __init__(self, api_key: str, model: str, temperature: float | None = None,
                 thinking_level: str | None = None, base_url: str = GEMINI_URL,
                 transport: httpx.AsyncBaseTransport | None = None, timeout: float = 300):
        self.model = model
        self._temperature = temperature
        # Only Gemini models take a thinking level; Gemma rejects it with a 400.
        self._thinking_level = thinking_level if model.startswith("gemini") else None
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport,
                                       headers={"x-goog-api-key": api_key})

    async def generate_json(self, system: str, user: str, schema: dict,
                            max_tokens: int | None = None) -> str:
        config: dict = {"responseMimeType": "application/json", "responseSchema": schema}
        if max_tokens:
            config["maxOutputTokens"] = max_tokens
        if self._temperature is not None:
            config["temperature"] = self._temperature
        if self._thinking_level:
            config["thinkingConfig"] = {"thinkingLevel": self._thinking_level}
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }
        started = time.monotonic()
        try:
            resp = await self._http.post(f"/v1beta/models/{self.model}:generateContent", json=body)
        except httpx.HTTPError as exc:
            # str() of timeouts is empty: the exception type is the useful part.
            raise LLMError(
                f"Gemini request failed after {time.monotonic() - started:.0f}s: "
                f"{type(exc).__name__} {exc}".rstrip(), retryable=True) from exc
        if resp.status_code >= 400:
            raise LLMError(f"Gemini {resp.status_code}: {resp.text[:300]}",
                           retryable=resp.status_code == 429 or resp.status_code >= 500)
        try:
            candidate = resp.json()["candidates"][0]
            reason = candidate.get("finishReason")
            if reason in FILTERED_FINISH_REASONS:
                raise LLMError(f"Gemini withheld the answer ({reason})", retryable=True,
                               bad_output=True)
            if reason == "MAX_TOKENS":
                # Cut off mid-answer (often a model repeating itself): a smaller batch may fit.
                raise LLMError("Gemini hit the output token limit", retryable=True,
                               bad_output=True)
            parts = candidate["content"]["parts"]
            # Thinking models may return thought parts first; the answer is the rest.
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"Gemini returned no content: {resp.text[:300]}",
                           retryable=True) from exc

    async def aclose(self) -> None:
        await self._http.aclose()

