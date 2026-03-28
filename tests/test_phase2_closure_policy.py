from agent.build.capabilities import get_capability
from agent.build.models import BuildJobType, BuildOperation, BuildOperationType
from agent.control.policy import (
    classify_provider_delivery_outcome,
    evaluate_build_capability_guardrails,
    evaluate_release_readiness,
)


def test_build_capability_guardrails_validate_source_and_target_scope():
    capability = get_capability(BuildJobType.IMPLEMENTATION)

    decision = evaluate_build_capability_guardrails(
        capability=capability,
        operations=[
            BuildOperation(
                operation_type=BuildOperationType.COPY_FILE,
                source_path="templates/snippet.txt",
                path="docs/copied.txt",
            )
        ],
        target_files=["docs/copied.txt"],
    )

    assert decision["allowed"] is False
    assert "source_path templates/snippet.txt is outside declared target_files" in decision["errors"][0]


def test_classify_provider_delivery_outcome_marks_pending_receipts():
    outcome = classify_provider_delivery_outcome(receipt_status="queued", ok=True)

    assert outcome["outcome"] == "pending"
    assert outcome["terminal"] is False
    assert outcome["success"] is True
    assert outcome["attention_required"] is True


def test_release_readiness_fails_closed_on_quality_regression_and_warns_on_gateway_config():
    readiness = evaluate_release_readiness(
        quality_summary={
            "release_label": "v1.15.0",
            "total_cases": 3,
            "exact_match_rate": 1.0,
            "verdict_accuracy": 1.0,
            "count_accuracy": 1.0,
            "title_accuracy": 1.0,
            "false_positive_cases": 0,
            "false_negative_cases": 0,
            "trend": {
                "has_baseline": True,
                "regression_detected": True,
                "summary": "Golden review quality regressed versus the previous baseline.",
            },
        },
        gateway_catalog={
            "summary": {
                "total_routes": 2,
                "configured_routes": 0,
            }
        },
    )

    assert readiness["ready"] is False
    assert readiness["blocking_reasons"] == [
        "Golden review quality regressed versus the previous baseline."
    ]
    assert readiness["warnings"] == [
        "Gateway routes exist, but none are configured in the current environment."
    ]
