"""The one place the builder talks to the Claude API.

Every call goes through :func:`complete`, which enforces the four spend layers
of §8 that live in the app: the kill switch (``BUILDER_MODEL_CALLS=off``),
the monthly cap, the per-job call and dollar caps, and the ledger row that
prices each response's ``usage`` at the rate card below. The Console
workspace limit is the layer outside this code.

The model is ``claude-opus-5-5`` for all three call kinds (parse, content,
fit); ``BUILDER_MODEL`` overrides it. Thinking is adaptive, depth is set per
call with ``effort``, and the system prompt carries a cache breakpoint so the
knowledge notes and schema are paid for once per job rather than per call.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field

from builder import config
from builder.pipeline.store import Store

log = logging.getLogger("builder.model")

# USD per million tokens: input, cache write (1.25x), cache read, output.
RATES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 5.00, 0.20, 20.00),
    "claude-opus-5": (5.00, 6.25, 0.50, 25.00),
    "claude-sonnet-5-5": (2.00, 2.50, 0.20, 10.00),
    "claude-haiku-5-5": (0.10, 0.125, 0.01, 0.50),
}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ModelError(RuntimeError):
    """A model step could not complete. The message is safe to show the volunteer."""


class ModelOff(ModelError):
    pass


class SpendCap(ModelError):
    pass


def price(model: str, usage: dict) -> float:
    base = RATES.get(model) or RATES.get(model.rsplit("-", 1)[0]) or RATES["claude-opus-5-5"]
    i, w, r, o = base
    return (usage.get("input_tokens", 0) * i + usage.get("cache_write_tokens", 0) * w
            + usage.get("cache_read_tokens", 0) * r + usage.get("output_tokens", 0) * o) / 1_000_000


def usage_dict(usage) -> dict:
    return {
        "input_tokens": getattr(usage, "input_tokens", 0) or 0,
        "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0,
    }


@dataclass
class Job:
    """One unit of model work with its own caps: a parse, a fit loop, a chat message."""
    kind: str
    label: str
    store: Store
    max_calls: int = 0
    max_usd: float = 0.0
    id: str = ""
    calls: int = 0
    cost: float = 0.0
    log_lines: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"{self.kind}:{self.label}:{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(2)}"
        caps = config.JOB_CAPS.get(self.kind, (4, 1.0))
        self.max_calls = self.max_calls or caps[0]
        self.max_usd = self.max_usd or caps[1]

    def check(self) -> None:
        if not config.MODEL_CALLS:
            raise ModelOff("Model calls are switched off on this server (BUILDER_MODEL_CALLS=off). The deterministic steps still work.")
        month = self.store.month_spend()
        if month >= config.MONTHLY_CAP_USD:
            raise SpendCap(f"This month's model spend (${month:.2f}) has reached the ${config.MONTHLY_CAP_USD:.0f} cap. Contact Colby.")
        if self.calls >= self.max_calls:
            raise SpendCap(f"{self.kind} stopped after {self.calls} calls (the per-job cap).")
        if self.cost >= self.max_usd:
            raise SpendCap(f"{self.kind} stopped at ${self.cost:.2f} (the per-job cap is ${self.max_usd:.2f}).")

    def record(self, model: str, usage: dict) -> float:
        cost = price(model, usage)
        self.calls += 1
        self.cost += cost
        self.store.add_ledger(self.id, self.kind, model, usage, cost)
        return cost


_client = None


def client():
    global _client
    if _client is None:
        import anthropic

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ModelError("ANTHROPIC_API_KEY is not set in ~/.config/bulletin-builder/env.")
        _client = anthropic.Anthropic(max_retries=2)
    return _client


def complete(job: Job, *, system: str, messages: list[dict], tools: list[dict] | None = None,
             schema: dict | None = None, effort: str = "medium", max_tokens: int = 16000, model: str | None = None):
    """One Messages API call under the job's caps. Returns the SDK Message."""
    import anthropic

    job.check()
    model = model or config.MODEL
    output_config: dict = {"effort": effort}
    if schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    req: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": messages,
        "thinking": {"type": "adaptive"},
        "output_config": output_config,
    }
    if tools:
        req["tools"] = tools
    t0 = time.time()
    try:
        # Streaming so a long answer never trips the SDK's non-streaming timeout guard.
        try:
            with client().beta.messages.stream(betas=[FALLBACK_BETA], fallbacks="default", **req) as stream:
                resp = stream.get_final_message()
        except anthropic.BadRequestError as e:
            if "fallback" not in str(e).lower():
                raise
            log.warning("fallbacks not accepted (%s); retrying without", e)
            with client().messages.stream(**req) as stream:
                resp = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise ModelError("The Anthropic API key was rejected. Check ~/.config/bulletin-builder/env.") from None
    except anthropic.RateLimitError:
        raise ModelError("The Anthropic API is rate-limiting this workspace. Try again in a minute.") from None
    except anthropic.APIStatusError as e:
        raise ModelError(f"Anthropic API error {e.status_code}: {e.message}") from None
    except anthropic.APIConnectionError as e:
        raise ModelError(f"Could not reach the Anthropic API: {e}") from None
    usage = usage_dict(resp.usage)
    cost = job.record(resp.model or model, usage)
    job.log_lines.append(f"{job.kind} call {job.calls}: {usage['input_tokens']} in, {usage['cache_read_tokens']} cached, "
                         f"{usage['output_tokens']} out, ${cost:.3f}, {time.time() - t0:.1f}s, stop={resp.stop_reason}")
    log.info(job.log_lines[-1])
    if resp.stop_reason == "refusal":
        detail = getattr(resp, "stop_details", None)
        raise ModelError(f"The model declined this request ({getattr(detail, 'category', None) or 'no category'}).")
    if resp.stop_reason == "max_tokens":
        raise ModelError("The model's answer was cut off at the output limit; the input may be too long.")
    return resp


def text_of(resp) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


def json_of(resp) -> dict:
    text = text_of(resp).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ModelError(f"The model returned something that is not JSON: {e}") from None


def tool_uses(resp) -> list:
    return [b for b in resp.content if getattr(b, "type", None) == "tool_use"]


def spend_status(store: Store) -> dict:
    month = store.month_spend()
    cap = config.MONTHLY_CAP_USD
    return {
        "month": month, "cap": cap, "fraction": (month / cap) if cap else 0.0,
        "warn": cap and month >= config.WARN_FRACTION * cap, "blocked": cap and month >= cap,
        "model_calls": config.MODEL_CALLS,
    }
