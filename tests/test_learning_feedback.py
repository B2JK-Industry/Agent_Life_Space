"""
Tests pre agent/brain/learning.py — feedback loop (nové metódy).

Pokrýva:
    - process_outcome() — detekcia skills, aktualizácia, knowledge zápis
    - get_advice_for_task() — rady pred úlohou
    - _detect_skills_in_text() — pattern matching
    - _extract_error() — error extraction z textu
    - _build_recommendation() — odporúčanie
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from agent.brain.learning import LearningSystem


@pytest.fixture
def learning_system(tmp_path: Path) -> LearningSystem:
    """Create a learning system with temp files."""
    skills_path = tmp_path / "skills.json"
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir()
    # Create required subdirs for knowledge base
    for subdir in ["skills", "systems", "people", "projects", "learned"]:
        (knowledge_dir / subdir).mkdir()

    ls = LearningSystem(
        skills_path=str(skills_path),
        knowledge_dir=str(knowledge_dir),
    )
    return ls


# --- _detect_skills_in_text ---


class TestDetectSkillsInText:
    def test_detect_curl(self, learning_system: LearningSystem):
        text = "Použil som curl -s https://example.com a dostal som odpoveď."
        skills = learning_system._detect_skills_in_text(text)
        assert "curl" in skills

    def test_detect_git(self, learning_system: LearningSystem):
        text = "Spustil som git commit -m 'fix' a git push origin main."
        skills = learning_system._detect_skills_in_text(text)
        assert "git_commit" in skills

    def test_detect_python(self, learning_system: LearningSystem):
        text = "Spustil som python3 -c 'print(42)' a výsledok je 42."
        skills = learning_system._detect_skills_in_text(text)
        assert "python_run" in skills

    def test_detect_pytest(self, learning_system: LearningSystem):
        text = "Spustil som pytest a 15 testov prešlo."
        skills = learning_system._detect_skills_in_text(text)
        assert "pytest" in skills

    def test_detect_docker(self, learning_system: LearningSystem):
        text = "Docker run na alpine kontajneri prebehol úspešne."
        skills = learning_system._detect_skills_in_text(text)
        assert "docker_run" in skills

    def test_detect_multiple_skills(self, learning_system: LearningSystem):
        text = "Prečítal som súbor, spustil pytest a commitol zmeny cez git commit."
        skills = learning_system._detect_skills_in_text(text)
        assert len(skills) >= 2
        assert "pytest" in skills
        assert "git_commit" in skills

    def test_detect_nothing(self, learning_system: LearningSystem):
        text = "Ahoj Daniel, ako sa máš?"
        skills = learning_system._detect_skills_in_text(text)
        assert skills == []

    def test_case_insensitive(self, learning_system: LearningSystem):
        text = "DOCKER RUN na PYTHON3 image"
        skills = learning_system._detect_skills_in_text(text)
        assert "docker_run" in skills


# --- _extract_error ---


class TestExtractError:
    def test_extract_error_from_text(self, learning_system: LearningSystem):
        text = "Pokúšal som sa ale Error: FileNotFoundError: /tmp/missing.txt neexistuje"
        error = learning_system._extract_error(text)
        assert "FileNotFoundError" in error

    def test_extract_chyba(self, learning_system: LearningSystem):
        text = "Chyba: timeout pri pripojení na server"
        error = learning_system._extract_error(text)
        assert "timeout" in error

    def test_extract_exception(self, learning_system: LearningSystem):
        text = "Traceback: ValueError: invalid literal for int()"
        error = learning_system._extract_error(text)
        assert "ValueError" in error or "invalid" in error

    def test_no_error_found(self, learning_system: LearningSystem):
        text = "Všetko prebehlo v poriadku, žiadne problémy."
        error = learning_system._extract_error(text)
        assert error == ""

    def test_extract_failed(self, learning_system: LearningSystem):
        text = "Command failed: permission denied na /etc/shadow"
        error = learning_system._extract_error(text)
        assert "permission" in error.lower() or error != ""


# --- _build_recommendation ---


class TestBuildRecommendation:
    def test_confident_skills(self, learning_system: LearningSystem):
        confident = [{"name": "curl", "confidence": 0.9}]
        result = learning_system._build_recommendation(confident, [], [])
        assert "curl" in result

    def test_risky_skills(self, learning_system: LearningSystem):
        risky = [{"name": "docker_run", "status": "failed"}]
        result = learning_system._build_recommendation([], risky, [])
        assert "docker_run" in result
        assert "nestabilné" in result or "Pozor" in result

    def test_past_errors(self, learning_system: LearningSystem):
        errors = [{"preview": "timeout pri docker"}]
        result = learning_system._build_recommendation([], [], errors)
        assert "chyby" in result.lower()

    def test_no_experience(self, learning_system: LearningSystem):
        result = learning_system._build_recommendation([], [], [])
        assert "Nemám" in result or "skúsenosti" in result


# --- process_outcome ---


class TestProcessOutcome:
    def test_success_updates_skills(self, learning_system: LearningSystem):
        reply = "Spustil som pytest a 10 testov prešlo. Všetko OK."
        result = learning_system.process_outcome(
            task_description="otestuj kód",
            reply=reply,
            success=True,
        )
        assert "pytest:success" in result["updates"]
        assert "pytest" in result["detected_skills"]

    def test_failure_records_error(self, learning_system: LearningSystem):
        reply = "Spustil som pytest ale Error: ModuleNotFoundError: No module named 'foo'"
        result = learning_system.process_outcome(
            task_description="otestuj foo modul",
            reply=reply,
            success=False,
        )
        assert "pytest:failure" in result["updates"]

    def test_failure_saves_knowledge(self, learning_system: LearningSystem):
        reply = "Docker run zlyhal. Error: permission denied, nemáte oprávnenie na docker socket"
        result = learning_system.process_outcome(
            task_description="spusti docker kontajner",
            reply=reply,
            success=False,
        )
        assert result["knowledge_saved"] is True

    def test_no_skills_detected(self, learning_system: LearningSystem):
        reply = "Rozmýšľam nad tvojou otázkou o filozofii."
        result = learning_system.process_outcome(
            task_description="čo je zmysel života",
            reply=reply,
            success=True,
        )
        assert result["detected_skills"] == []
        assert result["updates"] == []

    def test_multiple_skills_in_one_reply(self, learning_system: LearningSystem):
        reply = "Prečítal som súbor cez file_read, spustil pytest a commitol cez git commit."
        result = learning_system.process_outcome(
            task_description="otestuj a commitni",
            reply=reply,
            success=True,
        )
        assert len(result["detected_skills"]) >= 2


# --- get_advice_for_task ---


class TestGetAdviceForTask:
    def test_returns_structure(self, learning_system: LearningSystem):
        advice = learning_system.get_advice_for_task("spusti pytest")
        assert "confident_skills" in advice
        assert "risky_skills" in advice
        assert "past_errors" in advice
        assert "recommendation" in advice

    def test_recommendation_not_empty(self, learning_system: LearningSystem):
        advice = learning_system.get_advice_for_task("niečo neznáme")
        assert len(advice["recommendation"]) > 0

    def test_known_skill_shows_confidence(self, learning_system: LearningSystem):
        # Record enough successes for high confidence (need 8+ for >0.7)
        # confidence = ratio * volume_factor = 1.0 * min(1.0, n/10)
        # n=8 → 0.8 > 0.7 threshold
        for _ in range(8):
            learning_system.skills.record_success("pytest")

        advice = learning_system.get_advice_for_task("pytest")
        confident_names = [s["name"] for s in advice["confident_skills"]]
        assert "pytest" in confident_names

    def test_failed_skill_shows_risky(self, learning_system: LearningSystem):
        # Make docker fail
        learning_system.skills.record_failure("docker_run", "permission denied")

        advice = learning_system.get_advice_for_task("docker_run")
        risky_names = [s["name"] for s in advice["risky_skills"]]
        assert "docker_run" in risky_names
