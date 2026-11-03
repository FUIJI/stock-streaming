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
    was unusable, so a smaller batch may succeed where this one failed. rate_limited: a
    429, our key is over a per-minute/per-day limit (waiting fixes it, another model
    usually doesn't)."""

    def __init__(self, message: str, retryable: bool, bad_output: bool = False,
                 rate_limited: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.bad_output = bad_output
        self.rate_limited = rate_limited


def _error_message(resp: httpx.Response) -> str:
    """Google's one-line error message instead of a multi-line JSON dump."""
    try:
        message = resp.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        message = resp.text
    return " ".join(str(message).split())[:300]


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
        self.last_usage: dict = {}  # usageMetadata of the last answer (token counts)
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
            raise LLMError(f"Gemini {resp.status_code}: {_error_message(resp)}",
                           retryable=resp.status_code == 429 or resp.status_code >= 500,
                           rate_limited=resp.status_code == 429)
        try:
            self.last_usage = resp.json().get("usageMetadata") or {}
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

