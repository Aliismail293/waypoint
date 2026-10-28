"""Token prices and per-call cost.

Prices are USD per 1M tokens on Vertex AI. Google's official page didn't list the 3.5 models when this was
written, so these come from third-party price trackers (see docs/OBSERVABILITY.md); check them against the
Cloud Billing console. Thinking tokens are billed as output; cached input is billed at the cached rate.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cached_input: float


PRICES: dict[str, Price] = {
    "gemini-3.5-flash": Price(input=1.50, output=9.00, cached_input=0.15),
    "gemini-3.5-flash-lite": Price(input=0.30, output=2.50, cached_input=0.03),
    "gemini-embedding-001": Price(input=0.15, output=0.0, cached_input=0.0),
}
DEFAULT_MODEL = "gemini-3.5-flash"


def price_for(model: str) -> Price:
    for name in sorted(PRICES, key=len, reverse=True):  # longest prefix first: flash-lite before flash
        if model.startswith(name):
            return PRICES[name]
    return PRICES[DEFAULT_MODEL]  # unknown (e.g. the mock): price as Flash so offline numbers are plausible


def cost_usd(
    model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0, thinking_tokens: int = 0
) -> float:
    price = price_for(model)
    uncached = max(input_tokens - cached_tokens, 0)
    return (
        uncached * price.input + cached_tokens * price.cached_input + (output_tokens + thinking_tokens) * price.output
    ) / 1_000_000
