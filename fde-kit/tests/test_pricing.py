import pytest

from fde_kit.pricing import DEFAULT_PRICES, Price, cost_usd, price_for


def test_longest_prefix_wins():
    assert price_for("gemini-3.5-flash-lite-001") == DEFAULT_PRICES["gemini-3.5-flash-lite"]
    assert price_for("gemini-3.5-flash-002") == DEFAULT_PRICES["gemini-3.5-flash"]
    assert price_for("some-other-model") is None


def test_cost_rules():
    # 1M uncached input + 1M output on Flash = 1.50 + 9.00
    assert cost_usd("gemini-3.5-flash", 1_000_000, 1_000_000) == pytest.approx(10.5)
    # cached input is part of input and billed at the cached rate
    assert cost_usd("gemini-3.5-flash", 1_000_000, 0, cached_tokens=1_000_000) == pytest.approx(0.15)
    assert cost_usd("gemini-3.5-flash", 1_000_000, 0, cached_tokens=400_000) == pytest.approx(0.9 + 0.06)
    # thinking tokens bill as output
    assert cost_usd("gemini-3.5-flash", 0, 0, thinking_tokens=1_000_000) == pytest.approx(9.0)
    assert cost_usd("gemini-3.5-flash-lite", 1_000_000, 1_000_000) == pytest.approx(2.80)


def test_unknown_model_is_free_unless_a_default_is_given():
    assert cost_usd("mystery", 1_000_000, 1_000_000) == 0.0
    assert cost_usd("mystery", 1_000_000, 0, default=Price(2.0, 0.0, 0.0)) == pytest.approx(2.0)


def test_custom_price_table():
    table = {"claude-x": Price(input=3.0, output=15.0, cached_input=0.3)}
    assert cost_usd("claude-x-2026", 1_000_000, 1_000_000, prices=table) == pytest.approx(18.0)
    assert cost_usd("gemini-3.5-flash", 1_000_000, 0, prices=table) == 0.0  # the default table isn't merged in
