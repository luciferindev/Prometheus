"""Cost estimates from token usage.

Prices are Anthropic's first-party list prices in USD per million tokens as of 2026-11-03.
They are an estimate for the budget guard, not a bill; the Claude Console is authoritative.
A model that is not in this table has no estimate at all rather than a guessed one.
"""
from __future__ import annotations

from dataclasses import dataclass

# model id -> (input, output, cache read or None)
PRICES: dict[str, tuple[float, float, float | None]] = {
    "claude-fable-5-2": (8.00, 40.00, 0.20),
    "claude-fable-5-1": (10.00, 50.00, None),
    "claude-fable-5": (10.00, 50.00, None),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, None),
    "claude-opus-4-8": (5.00, 25.00, None),
    "claude-opus-4-7": (5.00, 25.00, None),
    "claude-opus-4-6": (5.00, 25.00, None),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-sonnet-5": (2.00, 10.00, None),
    "claude-sonnet-4-6": (3.00, 15.00, None),
    "claude-haiku-5-5": (0.10, 0.50, None),
    "claude-haiku-4-5": (1.00, 5.00, None),
}
CACHE_READ_FACTOR = 0.1      # used when no explicit cache-read price is listed
CACHE_WRITE_FACTOR = 1.25    # 5-minute cache writes


@dataclass
class Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    last_latency_s: float = 0.0

    def add(self, usage, latency_s: float) -> None:
        self.requests += 1
        self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        self.cache_read_tokens += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        self.cache_write_tokens += int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        self.last_latency_s = latency_s

    def cost(self, model: str) -> float | None:
        price = PRICES.get(model)
        if price is None:
            return None
        inp, out, read = price
        read = read if read is not None else inp * CACHE_READ_FACTOR
        return (self.input_tokens * inp + self.output_tokens * out + self.cache_read_tokens * read
                + self.cache_write_tokens * inp * CACHE_WRITE_FACTOR) / 1_000_000
