"""Tests for cost estimation and budget enforcement.

Regression coverage for the two ways estimate_cost used to report fiction:
charging gpt-4o list price for self-hosted inference, and charging gpt-4o list
price for any hosted model missing from MODEL_COSTS.
"""

import logging

import pytest

import codeassist.cost_tracker as ct
from codeassist.cost_tracker import (
    MODEL_COSTS,
    BudgetConfig,
    CostTracker,
    estimate_cost,
)


class TestEstimateCostKnownModel:
    """Priced models still use their own rates."""

    def test_known_model_uses_its_own_rates(self):
        cost = estimate_cost("gpt-4o-mini", 1_000_000, 1_000_000)
        assert cost == pytest.approx(0.15 + 0.60)

    def test_local_flag_forces_zero(self):
        """Self-hosted inference has no marginal per-token cost."""
        assert estimate_cost("gpt-4o", 10_000_000, 10_000_000, local=True) == 0.0

    def test_local_overrides_known_pricing(self):
        """A self-hosted model that happens to share a name with a priced one
        must not be billed. Local wins unconditionally."""
        assert estimate_cost("gpt-4o", 1_000_000, 0, local=True) == 0.0


class TestEstimateCostUnknownModel:
    """Unpriced models report nothing rather than a fabricated figure."""

    def test_unknown_hosted_model_is_zero(self):
        assert estimate_cost("qwen3-max", 1_000_000, 1_000_000) == 0.0

    def test_unknown_local_model_name_is_zero(self):
        """The configured local model (e.g. "ornith") has no price entry."""
        assert estimate_cost("ornith", 1_000_000, 1_000_000) == 0.0

    def test_versioned_id_is_not_silently_charged_list_price(self):
        """A pinned id like gpt-4o-2024-08-06 misses the exact-match table.
        It previously fell back to gpt-4o rates; now it reports $0.00."""
        assert estimate_cost("gpt-4o-2024-08-06", 1_000_000, 1_000_000) == 0.0

    def test_warns_once_per_model(self, caplog):
        """A long session must not repeat the pricing warning per completion."""
        ct._UNPRICED_WARNED.clear()
        with caplog.at_level(logging.WARNING, logger="codeassist.cost_tracker"):
            for _ in range(5):
                estimate_cost("qwen3-max", 100, 100)
        assert sum("qwen3-max" in r.getMessage() for r in caplog.records) == 1
        ct._UNPRICED_WARNED.clear()

    def test_local_model_does_not_warn(self, caplog):
        """Correct behaviour, not a gap in pricing — stay quiet."""
        ct._UNPRICED_WARNED.clear()
        with caplog.at_level(logging.WARNING, logger="codeassist.cost_tracker"):
            estimate_cost("ornith", 100, 100, local=True)
        assert caplog.records == []
        ct._UNPRICED_WARNED.clear()

    def test_empty_model_is_zero(self):
        assert estimate_cost("", 1000, 1000) == 0.0


class TestRecordUsage:
    """record_usage propagates the local flag into the stored record."""

    def test_local_record_has_zero_cost(self):
        tracker = CostTracker()
        record = tracker.record_usage("ornith", 5000, 2000, local=True)
        assert record.estimated_cost == 0.0
        assert record.total_tokens == 7000
        assert tracker.get_summary()["total_cost_usd"] == 0.0

    def test_hosted_record_accumulates(self):
        tracker = CostTracker()
        tracker.record_usage("gpt-4o", 1_000_000, 0)
        assert tracker.get_summary()["total_cost_usd"] == pytest.approx(2.50)

    def test_totals_stay_zero_for_all_local_session(self):
        """Regression: a long local session previously accrued phantom spend,
        which would have tripped a configured max_cost_per_session ceiling."""
        tracker = CostTracker()
        for _ in range(50):
            tracker.record_usage("ornith", 100_000, 10_000, local=True)
        assert tracker.get_summary()["total_cost_usd"] == 0.0
        assert tracker.get_summary()["total_tokens"] == 5_500_000

    def test_local_defaults_to_false_for_backcompat(self):
        """Existing callers that don't pass the flag keep the priced behaviour."""
        tracker = CostTracker()
        assert tracker.record_usage("gpt-4o", 1_000_000, 0).estimated_cost > 0


class TestCostBudget:
    """Budget ceilings behave for both local and hosted sessions."""

    def test_local_session_never_trips_cost_ceiling(self):
        tracker = CostTracker(BudgetConfig(enabled=True, max_cost_per_session=1.0))
        for _ in range(20):
            tracker.record_usage("ornith", 1_000_000, 1_000_000, local=True)
            ok, _msg = tracker.check_budget()
            assert ok is True

    def test_hosted_session_trips_cost_ceiling(self):
        tracker = CostTracker(BudgetConfig(enabled=True, max_cost_per_session=1.0))
        tracker.record_usage("gpt-4o", 1_000_000, 0)
        ok, msg = tracker.check_budget()
        assert ok is False
        assert "Cost budget exceeded" in msg

    def test_token_ceiling_still_enforced(self):
        tracker = CostTracker(BudgetConfig(enabled=True, max_tokens_per_session=1000))
        tracker.record_usage("ornith", 900, 200, local=True)
        ok, msg = tracker.check_budget()
        assert ok is False
        assert "Token budget exceeded" in msg

    def test_unpriced_hosted_model_does_not_trip_cost_ceiling(self):
        """Unknown pricing yields $0, so cost enforcement can't fire on a
        hosted model we simply don't have a price for."""
        tracker = CostTracker(BudgetConfig(enabled=True, max_cost_per_session=0.01))
        tracker.record_usage("qwen3-max", 10_000_000, 10_000_000)
        assert tracker.check_budget()[0] is True


def test_model_costs_table_is_non_empty():
    """Guard against a refactor emptying the table and silently zeroing all cost."""
    assert MODEL_COSTS
    for model, prices in MODEL_COSTS.items():
        assert prices["input"] > 0, model
        assert prices["output"] > 0, model
