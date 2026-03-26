"""
Confusion analysis for routing categories.

Maps actual vs expected classifications to find systematic errors.
"""

from __future__ import annotations

from agent.core.models import classify_task


class TestConfusionMatrix:
    """Systematic analysis of routing confusion patterns."""

    # Ground truth with potential confusion pairs
    CONFUSION_CASES = [
        # (input, expected, common_confusion)
        ("analyzuj kód", "analysis", "programming"),
        ("porovnaj Sonnet a Opus", "analysis", "programming"),
        ("prečo nefunguje server?", "analysis", "programming"),
        ("naprogramuj mi parser", "programming", "analysis"),
        ("commitni zmeny", "programming", "analysis"),
        ("aký je dnes deň?", "factual", "chat"),
        ("čo robíš?", "chat", "factual"),
        ("ok", "simple", "chat"),
    ]

    def test_no_systematic_confusion(self):
        """Check that confusion pairs don't systematically misroute."""
        misrouted = []
        for text, expected, confusion_target in self.CONFUSION_CASES:
            actual = classify_task(text)
            if actual == confusion_target and actual != expected:
                misrouted.append(f"'{text}': expected={expected}, got={actual} (confused with {confusion_target})")

        # Allow up to 20% confusion rate
        max_allowed = max(1, len(self.CONFUSION_CASES) // 5)
        assert len(misrouted) <= max_allowed, (
            f"Too many confusion errors ({len(misrouted)}/{len(self.CONFUSION_CASES)}):\n"
            + "\n".join(misrouted)
        )


class TestFallbackHierarchy:
    """Fallback routing is deterministic and predictable."""

    def test_unknown_input_falls_to_chat(self):
        """Unrecognized input defaults to chat (BALANCED tier)."""
        assert classify_task("hmm zaujímavé") == "chat"

    def test_empty_falls_to_chat(self):
        assert classify_task("") == "chat"

    def test_single_word_not_keyword(self):
        assert classify_task("zmrzlina") == "chat"

    def test_fallback_never_programming(self):
        """Random input should never route to expensive POWERFUL tier."""
        random_inputs = [
            "dnes je pekne",
            "mám hlad",
            "zaujímavé počasie",
            "koľko stojí chlieb",
        ]
        for text in random_inputs:
            result = classify_task(text)
            assert result != "programming", f"'{text}' should not route to programming"
