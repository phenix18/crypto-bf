"""Talking to a language model, cheaply and optionally.

The council in `ai_agents.py` reads better with a model behind it, but it has
to work without one: free endpoints rate-limit, change model names, and go
away. So every call here can return ``None`` and that is a normal outcome, not
an error — the agent then falls back to its deterministic reasoning.

Two rules hold throughout:

**A missing model never becomes a trade.** `complete` returning ``None`` is
indistinguishable, to the caller, from a model that abstained. Nothing here
can approve anything.

**The key never leaves this module.** It goes in a header and nowhere else —
not into a log line, not into an exception message, not into the audit trail.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional, Protocol, Sequence

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Free-tier models, tried in order. These names move: OpenRouter retires and
# renames free models regularly, so OPENROUTER_MODELS overrides the list rather
# than anyone having to patch this file.
DEFAULT_FREE_MODELS = (
    "deepseek/deepseek-chat-v3-0324:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-2-9b-it:free",
    "qwen/qwen-2.5-7b-instruct:free",
)


@dataclass(frozen=True)
class LlmReply:
    """What a model said, and which one said it."""

    text: str
    model: str


class LlmClient(Protocol):
    name: str
    available: bool

    def complete(self, *, system: str, user: str, max_tokens: int = 700) -> Optional[LlmReply]:
        ...


@dataclass
class NullClient:
    """No model configured. Every agent runs on its own reasoning."""

    name: str = "none"
    available: bool = False

    def complete(self, *, system: str, user: str, max_tokens: int = 700) -> Optional[LlmReply]:
        return None


@dataclass
class ScriptedClient:
    """A fixed list of replies, for tests and for rehearsing a council run."""

    replies: Sequence[object] = field(default_factory=tuple)
    name: str = "scripted"
    available: bool = True
    calls: list[dict] = field(default_factory=list)

    def complete(self, *, system: str, user: str, max_tokens: int = 700) -> Optional[LlmReply]:
        index = len(self.calls)
        self.calls.append({"system": system, "user": user})
        if index >= len(self.replies):
            return None
        reply = self.replies[index]
        if isinstance(reply, Exception):
            raise reply
        if reply is None:
            return None
        if isinstance(reply, LlmReply):
            return reply
        return LlmReply(text=str(reply), model="scripted")


@dataclass
class OpenRouterClient:
    """OpenRouter's chat completions, over the free tier.

    Free models fail in ways paid ones do not: 429 when the shared quota is
    spent, 404 when a model is retired overnight, 402 when the free allowance
    runs out. All three mean "try the next model", not "stop" — and when the
    list is exhausted, the answer is ``None`` and the council carries on
    without a model.
    """

    api_key: str
    models: tuple[str, ...] = DEFAULT_FREE_MODELS
    timeout: float = 25.0
    name: str = "openrouter"
    # Why the last error is kept rather than logged: it is useful for the audit
    # trail and for /api/health, and it is written here where we know it holds
    # no credential.
    last_error: Optional[str] = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, *, system: str, user: str, max_tokens: int = 700) -> Optional[LlmReply]:
        if not self.api_key:
            self.last_error = "no API key"
            return None

        for model in self.models:
            body = json.dumps(
                {
                    "model": model,
                    "max_tokens": max_tokens,
                    # Deterministic as the endpoint allows: the same briefing
                    # should not argue with itself between two runs.
                    "temperature": 0.1,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                OPENROUTER_URL,
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    # OpenRouter asks free-tier callers to identify themselves.
                    "HTTP-Referer": "https://github.com/phenix18/crypto-bf",
                    "X-Title": "ai-trader",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:200]
                self.last_error = f"{model}: HTTP {exc.code} {detail}"
                continue
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                self.last_error = f"{model}: {exc}"
                continue

            text = _first_message(payload)
            if text:
                self.last_error = None
                return LlmReply(text=text, model=model)
            self.last_error = f"{model}: empty completion"

        return None


def _first_message(payload) -> Optional[str]:
    """Pull the assistant text out of a chat-completions response."""
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    # Some providers return content as a list of parts.
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict)]
        joined = "".join(parts).strip()
        return joined or None
    return None


def build_llm_client() -> LlmClient:
    """The client the environment describes, or none at all.

    No key means no model, which means the council runs deterministically.
    That is the supported configuration, not a degraded one.
    """
    key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not key:
        return NullClient()
    raw = (os.getenv("OPENROUTER_MODELS") or "").strip()
    models = tuple(m.strip() for m in raw.split(",") if m.strip()) or DEFAULT_FREE_MODELS
    return OpenRouterClient(api_key=key, models=models)
