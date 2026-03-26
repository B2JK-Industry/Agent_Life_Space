"""
Tests pre agent/core/response_quality.py — Post-routing quality assessment.

Pokrýva:
    - assess_quality() signály: refusal, generic, error, echo, short, redirect
    - Escalation rozhodovanie: Haiku → Sonnet, Sonnet stop
    - Score boundaries (0.0–1.0)
"""

from __future__ import annotations

from agent.core.response_quality import QualityAssessment, assess_quality


class TestHighQualityResponses:
    """Dobré odpovede majú vysoké score a žiadnu eskaláciu."""

    def test_good_answer(self):
        q = assess_quality(
            "čo je Python?",
            "Python je vysokoúrovňový programovací jazyk vytvorený Guidom van Rossumom.",
            "claude-haiku-4-5-20251001",
        )
        assert q.score >= 0.8
        assert q.should_escalate is False

    def test_long_detailed_answer(self):
        q = assess_quality(
            "vysvetli mi Docker",
            "Docker je kontajnerizačná platforma. Umožňuje baliť aplikácie do "
            "izolovaných kontajnerov s vlastným filesystémom, sieťou a procesmi. "
            "Kontajnery sú ľahké, rýchle a prenosné medzi rôznymi prostrediami.",
            "claude-haiku-4-5-20251001",
        )
        assert q.score >= 0.8
        assert q.should_escalate is False


class TestLowQualityResponses:
    """Zlé odpovede majú nízke score."""

    def test_refusal(self):
        q = assess_quality(
            "vieš sa registrovať na moltbook?",
            "Neviem čo je moltbook. Nepoznám túto službu.",
            "claude-haiku-4-5-20251001",
        )
        assert q.score < 0.5
        assert "refusal" in str(q.signals)

    def test_generic_response(self):
        q = assess_quality(
            "analyzuj tento kód a povedz mi čo je zle",
            "Nedostal som výsledok. Skús to inak alebo zopakuj otázku.",
            "claude-haiku-4-5-20251001",
        )
        assert q.score < 0.7
        assert "generic_response" in q.signals

    def test_too_short_for_complex_question(self):
        q = assess_quality(
            "vysvetli mi ako funguje 7-vrstvový cascade v agent life space",
            "OK.",
            "claude-haiku-4-5-20251001",
        )
        assert q.score < 0.7
        assert "too_short" in q.signals

    def test_error_in_response(self):
        q = assess_quality(
            "spusti testy",
            "Chyba: nepodarilo sa pripojiť k databáze. Zlyhalo pripojenie.",
            "claude-haiku-4-5-20251001",
        )
        assert q.score <= 0.8
        assert any("error" in s for s in q.signals)

    def test_echo_plus_refusal(self):
        """Odpoveď ktorá opakuje otázku a pridá 'neviem'."""
        q = assess_quality(
            "čo je moltbook a ako sa tam registrovať",
            "Čo je moltbook? Neviem čo je moltbook. Nepoznám túto službu.",
            "claude-haiku-4-5-20251001",
        )
        assert q.score < 0.5
        assert any("refusal" in s or "echo" in s for s in q.signals)


class TestEscalationDecision:
    """Eskalácia z Haiku na Sonnet, ale nie ďalej."""

    def test_haiku_escalates_on_low_quality(self):
        q = assess_quality(
            "vieš sa registrovať na moltbook?",
            "Neviem čo je moltbook. Nemám informácie.",
            "claude-haiku-4-5-20251001",
        )
        assert q.should_escalate is True
        assert "Sonnet" in q.reason

    def test_sonnet_does_not_escalate(self):
        """Sonnet je max — neeskalujeme na Opus cez API."""
        q = assess_quality(
            "vieš sa registrovať na moltbook?",
            "Neviem čo je moltbook. Nemám informácie.",
            "claude-sonnet-4-6",
        )
        assert q.should_escalate is False

    def test_good_haiku_stays(self):
        """Dobrá Haiku odpoveď → žiadna eskalácia."""
        q = assess_quality(
            "koľko je 2+2?",
            "2+2 = 4",
            "claude-haiku-4-5-20251001",
        )
        assert q.should_escalate is False

    def test_programming_model_no_escalate(self):
        """Opus neeskaluje nikam."""
        q = assess_quality(
            "napíš test",
            "Neviem.",
            "claude-opus-4-6",
        )
        assert q.should_escalate is False


class TestScoreBoundaries:
    def test_score_never_negative(self):
        # Stack multiple negative signals
        q = assess_quality(
            "vysvetli mi veľmi dlhú a komplexnú tému o kvantovej fyzike",
            "Neviem. Nepoznám. Nemám informácie. Nedokážem. Chyba: timeout.",
            "claude-haiku-4-5-20251001",
        )
        assert q.score >= 0.0

    def test_score_never_above_one(self):
        q = assess_quality(
            "ahoj",
            "Ahoj! Ako sa máš?",
            "claude-haiku-4-5-20251001",
        )
        assert q.score <= 1.0

    def test_empty_answer(self):
        q = assess_quality("niečo", "", "claude-haiku-4-5-20251001")
        assert q.score < 0.7


class TestQualityAssessmentDataclass:
    def test_fields(self):
        q = QualityAssessment(score=0.5, should_escalate=True, reason="test", signals=["a"])
        assert q.score == 0.5
        assert q.should_escalate is True
        assert q.reason == "test"
        assert q.signals == ["a"]
