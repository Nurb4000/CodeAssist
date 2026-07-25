"""Cost Tracker - Real-time token budget enforcement for the agent loop."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger(__name__)


@dataclass
class BudgetConfig:
    """Configuration for cost tracking."""
    enabled: bool = False
    max_tokens_per_session: int = 0  # 0 = unlimited
    max_tokens_per_message: int = 0
    max_cost_per_session: float = 0.0  # 0.0 = unlimited
    warning_threshold_pct: float = 80.0  # Warn when this % of budget is used
    hard_limit: bool = True  # Stop when budget exceeded vs just warn


@dataclass
class UsageRecord:
    """A single usage record."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    timestamp: str = ""
    model: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        self.total_tokens = self.prompt_tokens + self.completion_tokens


# Approximate costs per 1M tokens (USD) — update as pricing changes
MODEL_COSTS = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    "claude-3-opus": {"input": 15.00, "output": 75.00},
    "claude-3-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate the cost of a completion in USD."""
    costs = MODEL_COSTS.get(model)
    if not costs:
        # Default to gpt-4o pricing for unknown models
        costs = MODEL_COSTS["gpt-4o"]
    input_cost = (prompt_tokens / 1_000_000) * costs["input"]
    output_cost = (completion_tokens / 1_000_000) * costs["output"]
    return input_cost + output_cost


class CostTracker:
    """Tracks token usage and enforces budget limits."""

    def __init__(self, config: BudgetConfig | None = None):
        self.config = config or BudgetConfig()
        self.records: list[UsageRecord] = []
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._total_cost = 0.0

    def record_usage(self, model: str, prompt_tokens: int, completion_tokens: int) -> UsageRecord:
        """Record token usage for a completion."""
        cost = estimate_cost(model, prompt_tokens, completion_tokens)
        record = UsageRecord(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost=cost,
            model=model,
        )
        self.records.append(record)
        self._total_prompt_tokens += prompt_tokens
        self._total_completion_tokens += completion_tokens
        self._total_cost += cost
        return record

    def check_budget(self) -> tuple[bool, str]:
        """Check if budget is exceeded. Returns (ok, message)."""
        if not self.config.enabled:
            return True, ""

        total_tokens = self._total_prompt_tokens + self._total_completion_tokens

        # Check token limits
        if self.config.max_tokens_per_session > 0:
            pct = (total_tokens / self.config.max_tokens_per_session) * 100
            if pct >= 100:
                return False, f"Token budget exceeded: {total_tokens:,} / {self.config.max_tokens_per_session:,} tokens"
            if pct >= self.config.warning_threshold_pct:
                log.warning("Token usage at %.0f%% of budget", pct)

        # Check cost limits
        if self.config.max_cost_per_session > 0:
            if self._total_cost >= self.config.max_cost_per_session:
                return False, f"Cost budget exceeded: ${self._total_cost:.4f} / ${self.config.max_cost_per_session:.2f}"

        return True, ""

    def check_message_limit(self, prompt_tokens: int, completion_tokens: int) -> tuple[bool, str]:
        """Check if a single message would exceed limits."""
        if not self.config.enabled:
            return True, ""

        if self.config.max_tokens_per_message > 0:
            total = prompt_tokens + completion_tokens
            if total > self.config.max_tokens_per_message:
                return False, f"Message too large: {total:,} > {self.config.max_tokens_per_message:,} tokens"

        return True, ""

    def get_summary(self) -> dict:
        """Get a summary of current usage."""
        return {
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
            "total_tokens": self._total_prompt_tokens + self._total_completion_tokens,
            "total_cost_usd": round(self._total_cost, 6),
            "num_completions": len(self.records),
            "budget_enabled": self.config.enabled,
            "budget_remaining_tokens": max(0, self.config.max_tokens_per_session - self._total_prompt_tokens - self._total_completion_tokens) if self.config.max_tokens_per_session > 0 else None,
            "budget_remaining_cost": max(0, self.config.max_cost_per_session - self._total_cost) if self.config.max_cost_per_session > 0 else None,
        }

    def reset(self):
        """Reset all usage tracking."""
        self.records.clear()
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._total_cost = 0.0
