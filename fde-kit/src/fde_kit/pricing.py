"""Token prices and per-call cost in USD.

Prices are USD per 1M tokens. The defaults are for Gemini 3.5 on Vertex AI and come from third-party price
trackers (Google's pricing page didn't list these models when Waypoint was written), so check them against
your Cloud Billing console and pass your own table when they differ.

Billing rules encoded here: cached input is billed at the cached rate (and is a subset of input, not extra),
and thinking tokens are billed as output.
"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    """USD per 1M tokens."""

    input: float
    output: float
    cached_input: float


# Third-party figures, not official Google prices (see module docstring).
DEFAULT_PRICES: Mapping[str, Price] = {
    "gemini-3.5-flash": Price(input=1.50, output=9.00, cached_input=0.15),
    "gemini-3.5-flash-lite": Price(input=0.30, output=2.50, cached_input=0.03),
}


def price_for(model: str, prices: Mapping[str, Price] = DEFAULT_PRICES) -> Price | None:
    """The price for `model`, matched by longest prefix, or None if nothing matches.

    Prefix matching lets versioned names ("gemini-3.5-flash-001") use the family's price. Longest first, so
    "gemini-3.5-flash-lite-001" gets flash-lite's price, not flash's.
    """
    for name in sorted(prices, key=len, reverse=True):
        if model.startswith(name):
            return prices[name]
    return None


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    thinking_tokens: int = 0,
    *,
    prices: Mapping[str, Price] = DEFAULT_PRICES,
    default: Price | None = None,
) -> float:
    """Cost of one model call. Unknown models cost `default` (0.0 if no default is given).

    Returning 0.0 for unknown models, rather than raising, means a missing price entry never breaks a live
    request; it just shows up as an unpriced call in your metrics.
    """
    price = price_for(model, prices) or default
    if price is None:
        return 0.0
    uncached = max(input_tokens - cached_tokens, 0)  # cached tokens are already counted in input_tokens
    return (
        uncached * price.input + cached_tokens * price.cached_input + (output_tokens + thinking_tokens) * price.output
    ) / 1_000_000
