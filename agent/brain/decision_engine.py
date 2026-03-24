"""
Agent Life Space — Decision Controller (Brain)

NOT a reasoning engine — a decision controller.
Routes decisions, enforces policies, scores priorities.

Design:
    - Classification is a lookup table (deterministic)
    - Scoring is a weighted formula (deterministic)
    - LLM routing is keyword heuristic (cheap, fast, imperfect)
    - Finance pre-filtering is algorithmic (safety layer)
    - Cache stores repeated decisions to avoid recomputation
    - Unknown categories FAIL FAST (no silent fallback)

What this is NOT:
    - Not a planner (that's a future LLM-backed module)
    - Not a reasoner (confidence values are heuristic estimates, not calibrated)
    - Not a complete classifier (keyword matching is a first filter, not final word)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class DecisionMethod(str, Enum):
    """How the decision was made."""

    ALGORITHM = "algorithm"
    LLM = "llm"
    HYBRID = "hybrid"
    CACHED = "cached"


class DecisionCategory(str, Enum):
    """What kind of decision is being made."""

    TASK_PRIORITY = "task_priority"
    TASK_ROUTING = "task_routing"
    CONTENT_GENERATION = "content_generation"
    OPPORTUNITY_EVALUATION = "opportunity_evaluation"
    MEMORY_MANAGEMENT = "memory_management"
    SCHEDULE_MANAGEMENT = "schedule_management"
    ERROR_HANDLING = "error_handling"
    FINANCE = "finance"


# Explicit mapping — every category MUST be listed
_CATEGORY_METHOD: dict[DecisionCategory, DecisionMethod] = {
    DecisionCategory.TASK_PRIORITY: DecisionMethod.ALGORITHM,
    DecisionCategory.TASK_ROUTING: DecisionMethod.ALGORITHM,
    DecisionCategory.SCHEDULE_MANAGEMENT: DecisionMethod.ALGORITHM,
    DecisionCategory.ERROR_HANDLING: DecisionMethod.ALGORITHM,
    DecisionCategory.MEMORY_MANAGEMENT: DecisionMethod.ALGORITHM,
    DecisionCategory.CONTENT_GENERATION: DecisionMethod.LLM,
    DecisionCategory.OPPORTUNITY_EVALUATION: DecisionMethod.HYBRID,
    DecisionCategory.FINANCE: DecisionMethod.HYBRID,
}

# Convenience sets derived from the single source of truth
ALGORITHMIC_CATEGORIES = {
    k for k, v in _CATEGORY_METHOD.items() if v == DecisionMethod.ALGORITHM
}
LLM_CATEGORIES = {
    k for k, v in _CATEGORY_METHOD.items() if v == DecisionMethod.LLM
}
HYBRID_CATEGORIES = {
    k for k, v in _CATEGORY_METHOD.items() if v == DecisionMethod.HYBRID
}


@dataclass
class Decision:
    """Result of a decision process."""

    category: DecisionCategory
    method: DecisionMethod
    action: str
    confidence: float  # 0.0 - 1.0 (heuristic estimate, NOT calibrated probability)
    reasoning: str
    data: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False


@dataclass
class TaskScore:
    """Deterministic task scoring result."""

    task_id: str
    priority_score: float
    urgency_score: float
    combined_score: float
    factors: dict[str, float] = field(default_factory=dict)


class DecisionEngine:
    """
    Decision controller — routes, scores, filters, caches.
    The classifier itself is ALWAYS algorithmic.
    """

    def __init__(self, cache_max_size: int = 500) -> None:
        self._decision_count = 0
        self._cache: dict[str, Decision] = {}
        self._cache_max_size = cache_max_size
        self._cache_hits = 0

    # --- Classification ---

    def classify(self, category: DecisionCategory) -> DecisionMethod:
        """
        Determine how to handle a decision. ALWAYS deterministic.
        Fails fast on unknown categories — no silent fallback.
        """
        method = _CATEGORY_METHOD.get(category)
        if method is None:
            msg = (
                f"Unknown DecisionCategory: '{category.value}'. "
                f"Every category must be explicitly mapped in _CATEGORY_METHOD. "
                f"Known: {[c.value for c in _CATEGORY_METHOD]}"
            )
            raise ValueError(msg)
        return method

    # --- Cache ---

    def _cache_key(self, prefix: str, *args: Any) -> str:
        """Deterministic cache key from inputs."""
        raw = f"{prefix}:{'|'.join(str(a) for a in args)}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    def _cache_get(self, key: str) -> Decision | None:
        decision = self._cache.get(key)
        if decision is not None:
            self._cache_hits += 1
        return decision

    def _cache_set(self, key: str, decision: Decision) -> None:
        if len(self._cache) >= self._cache_max_size:
            # Evict oldest (first inserted)
            oldest = next(iter(self._cache))
            del self._cache[oldest]
        self._cache[key] = decision

    # --- Task Scoring ---

    def score_task(
        self,
        task_id: str,
        importance: float = 0.5,
        urgency: float = 0.5,
        effort: float = 0.5,
        dependencies_met: bool = True,
        has_deadline: bool = False,
        deadline_hours: float | None = None,
    ) -> TaskScore:
        """
        Score a task for prioritization. PURE ALGORITHM — no LLM.

        Scoring:
            priority_score = importance * 0.4 + (1 - effort) * 0.2 + dep_bonus * 0.1
            urgency_score  = urgency (boosted by deadline proximity)
            combined       = priority_score * 0.6 + urgency_score * 0.4

        Note: urgency is separate from priority_score. It enters via combined.
        This is intentional — a low-importance but urgent task still rises.
        """
        if not task_id:
            msg = "task_id cannot be empty"
            raise ValueError(msg)

        # Clamp inputs to valid range
        importance = max(0.0, min(1.0, importance))
        urgency = max(0.0, min(1.0, urgency))
        effort = max(0.0, min(1.0, effort))

        # Deadline urgency boost
        if has_deadline and deadline_hours is not None:
            if deadline_hours < 0:
                urgency = 1.0  # Past deadline = max urgency
            elif deadline_hours <= 1:
                urgency = 1.0
            elif deadline_hours <= 24:
                urgency = max(urgency, 0.8)
            elif deadline_hours <= 72:
                urgency = max(urgency, 0.6)

        dep_bonus = 1.0 if dependencies_met else 0.0

        priority_score = importance * 0.4 + (1 - effort) * 0.2 + dep_bonus * 0.1
        urgency_score = urgency
        combined = priority_score * 0.6 + urgency_score * 0.4

        factors = {
            "importance": importance,
            "urgency": urgency,
            "effort": effort,
            "dependencies_met": float(dependencies_met),
            "has_deadline": float(has_deadline),
        }

        return TaskScore(
            task_id=task_id,
            priority_score=round(priority_score, 4),
            urgency_score=round(urgency_score, 4),
            combined_score=round(combined, 4),
            factors=factors,
        )

    def prioritize_tasks(self, tasks: list[dict[str, Any]]) -> list[TaskScore]:
        """
        Sort tasks by priority. DETERMINISTIC — same input = same output.

        Each task dict must have: task_id
        Optional: importance, urgency, effort, dependencies_met, has_deadline, deadline_hours
        """
        scores = []
        for i, task in enumerate(tasks):
            tid = task.get("task_id")
            if not tid:
                msg = f"Task at index {i} missing 'task_id'"
                raise ValueError(msg)

            score = self.score_task(
                task_id=tid,
                importance=task.get("importance", 0.5),
                urgency=task.get("urgency", 0.5),
                effort=task.get("effort", 0.5),
                dependencies_met=task.get("dependencies_met", True),
                has_deadline=task.get("has_deadline", False),
                deadline_hours=task.get("deadline_hours"),
            )
            scores.append(score)

        scores.sort(key=lambda s: s.combined_score, reverse=True)
        return scores

    # --- Error Handling ---

    def decide_error_action(
        self,
        error_type: str,
        retry_count: int,
        max_retries: int,
    ) -> Decision:
        """
        Decide what to do with an error. ALWAYS algorithmic.
        """
        if not error_type:
            msg = "error_type cannot be empty"
            raise ValueError(msg)
        if retry_count < 0:
            msg = "retry_count cannot be negative"
            raise ValueError(msg)
        if max_retries < 0:
            msg = "max_retries cannot be negative"
            raise ValueError(msg)

        self._decision_count += 1

        if retry_count < max_retries:
            return Decision(
                category=DecisionCategory.ERROR_HANDLING,
                method=DecisionMethod.ALGORITHM,
                action="retry",
                confidence=1.0,
                reasoning=f"Retry {retry_count + 1}/{max_retries}. "
                f"Exponential backoff applies.",
            )
        elif error_type in ("timeout", "rate_limit", "service_unavailable"):
            return Decision(
                category=DecisionCategory.ERROR_HANDLING,
                method=DecisionMethod.ALGORITHM,
                action="dead_letter",
                confidence=1.0,
                reasoning=f"Max retries ({max_retries}) exhausted for "
                f"transient error: {error_type}. Dead lettered.",
            )
        else:
            return Decision(
                category=DecisionCategory.ERROR_HANDLING,
                method=DecisionMethod.ALGORITHM,
                action="dead_letter_with_alert",
                confidence=1.0,
                reasoning=f"Non-transient error: {error_type}. "
                f"Dead lettered with alert for manual review.",
            )

    # --- LLM Routing ---

    def should_use_llm(self, task_description: str) -> Decision:
        """
        Decide if a task needs LLM or can be handled algorithmically.

        Uses keyword heuristic (deterministic, cheap, fast).
        This is a FIRST FILTER, not a final classifier.
        Confidence reflects the heuristic nature — it's an estimate, not truth.

        Known limitations:
            - "generate a list of priorities" may trigger LLM path unnecessarily
            - "analyze numbers" triggers LLM but could be algorithmic
            - Ambiguous tasks get low confidence, signaling uncertainty
        """
        if not task_description or not task_description.strip():
            msg = "task_description cannot be empty"
            raise ValueError(msg)

        self._decision_count += 1

        # Check cache first
        cache_key = self._cache_key("llm_routing", task_description.lower().strip())
        cached = self._cache_get(cache_key)
        if cached is not None:
            return Decision(
                category=cached.category,
                method=DecisionMethod.CACHED,
                action=cached.action,
                confidence=cached.confidence,
                reasoning=f"Cached decision. Original: {cached.reasoning}",
                data=cached.data,
            )

        desc_lower = task_description.lower()

        # Algorithmic tasks (keywords)
        algo_keywords = [
            "sort",
            "filter",
            "count",
            "calculate",
            "schedule",
            "priority",
            "route",
            "delete",
            "move",
            "copy",
            "rename",
            "list",
            "status",
            "health",
            "metrics",
        ]

        # LLM tasks (keywords)
        llm_keywords = [
            "write",
            "generate",
            "create content",
            "analyze text",
            "summarize",
            "translate",
            "explain",
            "evaluate",
            "brainstorm",
            "suggest",
            "research",
            "draft",
        ]

        algo_matches = [kw for kw in algo_keywords if kw in desc_lower]
        llm_matches = [kw for kw in llm_keywords if kw in desc_lower]
        algo_score = len(algo_matches)
        llm_score = len(llm_matches)

        if algo_score > llm_score:
            decision = Decision(
                category=DecisionCategory.TASK_ROUTING,
                method=DecisionMethod.ALGORITHM,
                action="use_algorithm",
                confidence=min(1.0, 0.5 + algo_score * 0.1),
                reasoning=f"Keyword heuristic: {algo_score} algo matches "
                f"({', '.join(algo_matches)}) vs {llm_score} LLM matches. "
                f"Note: this is a heuristic, not definitive.",
                data={
                    "algo_score": algo_score,
                    "llm_score": llm_score,
                    "algo_matches": algo_matches,
                    "llm_matches": llm_matches,
                },
            )
        elif llm_score > 0:
            decision = Decision(
                category=DecisionCategory.TASK_ROUTING,
                method=DecisionMethod.ALGORITHM,
                action="use_llm",
                confidence=min(1.0, 0.5 + llm_score * 0.1),
                reasoning=f"Keyword heuristic: {llm_score} LLM matches "
                f"({', '.join(llm_matches)}) vs {algo_score} algo matches. "
                f"Note: this is a heuristic, not definitive.",
                data={
                    "algo_score": algo_score,
                    "llm_score": llm_score,
                    "algo_matches": algo_matches,
                    "llm_matches": llm_matches,
                },
            )
        else:
            decision = Decision(
                category=DecisionCategory.TASK_ROUTING,
                method=DecisionMethod.ALGORITHM,
                action="use_algorithm",
                confidence=0.3,  # Low — we genuinely don't know
                reasoning="No keyword matches. Defaulting to algorithm (safe, cheap). "
                "Low confidence signals this may need review.",
                data={"algo_score": 0, "llm_score": 0},
            )

        self._cache_set(cache_key, decision)
        return decision

    # --- Finance Pre-Check ---

    def evaluate_finance_proposal(
        self,
        amount_usd: float,
        risk_level: str,
    ) -> Decision:
        """
        Algorithmic pre-check for financial proposals.
        This is the FIRST stage of a hybrid flow:
            1. This method: algorithmic safety filter (amount, risk)
            2. Future: LLM evaluates opportunity quality (not implemented yet)
            3. Always: human approves

        method=HYBRID because the overall finance flow is hybrid,
        even though this specific stage is algorithmic.
        """
        if amount_usd < 0:
            msg = f"amount_usd cannot be negative: {amount_usd}"
            raise ValueError(msg)

        valid_risks = {"low", "medium", "high", "critical"}
        if risk_level not in valid_risks:
            msg = f"Invalid risk_level: '{risk_level}'. Must be one of {valid_risks}"
            raise ValueError(msg)

        self._decision_count += 1

        if amount_usd > 100:
            return Decision(
                category=DecisionCategory.FINANCE,
                method=DecisionMethod.HYBRID,
                action="require_detailed_review",
                confidence=1.0,
                reasoning=f"Algorithmic pre-check: amount ${amount_usd} exceeds "
                f"$100 threshold. Needs detailed review before human approval.",
                requires_approval=True,
            )

        if risk_level in ("high", "critical"):
            return Decision(
                category=DecisionCategory.FINANCE,
                method=DecisionMethod.HYBRID,
                action="require_detailed_review",
                confidence=1.0,
                reasoning=f"Algorithmic pre-check: risk level '{risk_level}' "
                f"triggers detailed review before human approval.",
                requires_approval=True,
            )

        return Decision(
            category=DecisionCategory.FINANCE,
            method=DecisionMethod.HYBRID,
            action="propose_to_human",
            confidence=0.8,
            reasoning=f"Algorithmic pre-check passed: amount ${amount_usd}, "
            f"risk '{risk_level}'. Ready for human approval.",
            requires_approval=True,
        )

    # --- Stats ---

    def get_stats(self) -> dict[str, int]:
        return {
            "total_decisions": self._decision_count,
            "cache_size": len(self._cache),
            "cache_hits": self._cache_hits,
        }
