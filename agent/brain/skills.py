"""
Agent Life Space — Skills Registry

John si pamätá čo vie robiť. Keď niečo skúsi prvýkrát, otestuje to.
Keď to urobí mnohokrát, je si istý a netestuje.

Lifecycle:
    UNKNOWN → TESTING → LEARNED → MASTERED
                ↓
              FAILED (can retry later)

Storage: skills.json — čitateľný, editovateľný, persistentný.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import orjson
import structlog

logger = structlog.get_logger(__name__)


class SkillStatus(str, Enum):
    UNKNOWN = "unknown"      # Nikdy neskúšal
    TESTING = "testing"      # Práve testuje
    LEARNED = "learned"      # Vie to, ale ešte nie je istý
    MASTERED = "mastered"    # Vie to, robí to pravidelne
    FAILED = "failed"        # Skúsil, nefunguje


# Po koľkých úspešných použitiach sa skill stane mastered
MASTERY_THRESHOLD = 5


class Skill:
    """Jedna schopnosť Johna."""

    def __init__(
        self,
        name: str,
        description: str = "",
        category: str = "general",
        command_example: str = "",
        status: SkillStatus = SkillStatus.UNKNOWN,
        success_count: int = 0,
        fail_count: int = 0,
        last_used: str = "",
        first_learned: str = "",
        last_error: str = "",
        notes: str = "",
    ) -> None:
        self.name = name
        self.description = description
        self.category = category
        self.command_example = command_example
        self.status = status
        self.success_count = success_count
        self.fail_count = fail_count
        self.last_used = last_used
        self.first_learned = first_learned
        self.last_error = last_error
        self.notes = notes

    @property
    def confidence(self) -> float:
        """0.0–1.0 based on success ratio and count."""
        total = self.success_count + self.fail_count
        if total == 0:
            return 0.0
        ratio = self.success_count / total
        # More uses = more confidence (caps at 1.0)
        volume_factor = min(1.0, total / 10)
        return round(ratio * volume_factor, 2)

    @property
    def needs_testing(self) -> bool:
        """Should John test this before using it?"""
        if self.status in (SkillStatus.UNKNOWN, SkillStatus.FAILED):
            return True
        if self.status == SkillStatus.TESTING:
            return True
        if self.status == SkillStatus.LEARNED and self.success_count < 3:
            return True
        return False  # MASTERED or LEARNED with enough successes

    def record_success(self) -> None:
        """John used this skill successfully."""
        now = datetime.now(UTC).isoformat()
        self.success_count += 1
        self.last_used = now
        if not self.first_learned:
            self.first_learned = now

        if self.status == SkillStatus.UNKNOWN:
            self.status = SkillStatus.LEARNED
        elif self.status == SkillStatus.TESTING:
            self.status = SkillStatus.LEARNED
        elif self.status == SkillStatus.FAILED:
            self.status = SkillStatus.LEARNED

        if self.success_count >= MASTERY_THRESHOLD:
            self.status = SkillStatus.MASTERED

    def record_failure(self, error: str = "") -> None:
        """John tried but failed."""
        self.fail_count += 1
        self.last_used = datetime.now(UTC).isoformat()
        self.last_error = error
        if self.success_count == 0:
            self.status = SkillStatus.FAILED

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "command_example": self.command_example,
            "status": self.status.value,
            "confidence": self.confidence,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "last_used": self.last_used,
            "first_learned": self.first_learned,
            "last_error": self.last_error,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Skill:
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            category=data.get("category", "general"),
            command_example=data.get("command_example", ""),
            status=SkillStatus(data.get("status", "unknown")),
            success_count=data.get("success_count", 0),
            fail_count=data.get("fail_count", 0),
            last_used=data.get("last_used", ""),
            first_learned=data.get("first_learned", ""),
            last_error=data.get("last_error", ""),
            notes=data.get("notes", ""),
        )


class SkillRegistry:
    """
    Persistent registry of John's skills.
    Reads/writes skills.json — human-readable, agent-writable.
    """

    def __init__(self, path: str = "agent/brain/skills.json") -> None:
        self._path = Path(path)
        self._skills: dict[str, Skill] = {}
        self._load()

    def _load(self) -> None:
        """Load from disk."""
        if self._path.exists():
            try:
                data = orjson.loads(self._path.read_bytes())
                for name, skill_data in data.items():
                    self._skills[name] = Skill.from_dict(skill_data)
                logger.info("skills_loaded", count=len(self._skills))
            except Exception as e:
                logger.error("skills_load_error", error=str(e))
        else:
            self._register_defaults()
            self._save()

    def _save(self) -> None:
        """Persist to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {name: skill.to_dict() for name, skill in self._skills.items()}
        self._path.write_bytes(
            orjson.dumps(data, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
        )

    def _register_defaults(self) -> None:
        """Pre-register known capabilities."""
        defaults = [
            Skill("curl", "HTTP requesty cez curl", "internet", "curl -s https://example.com"),
            Skill("git_commit", "Git commit a push", "git", "git add . && git commit -m 'msg' && git push"),
            Skill("git_status", "Git status check", "git", "git status"),
            Skill("python_run", "Spustenie Python skriptu", "code", "python3 script.py"),
            Skill("pytest", "Spustenie testov", "code", "python -m pytest tests/ -q"),
            Skill("docker_run", "Spustenie Docker kontajnera", "docker", "docker run --rm image"),
            Skill("file_read", "Čítanie súborov", "filesystem", "cat file.py"),
            Skill("file_write", "Zápis do súborov", "filesystem", "echo 'text' > file"),
            Skill("system_health", "Kontrola CPU/RAM/disk", "system", "free -h && df -h /"),
            Skill("process_check", "Kontrola procesov", "system", "ps aux | head"),
            Skill("github_api", "GitHub API volania", "internet", "curl -H 'Authorization: token $GITHUB_TOKEN' https://api.github.com/..."),
            Skill("github_create_issue", "Vytvoriť GitHub issue", "github", "curl -X POST -H 'Authorization: token ...' .../issues"),
            Skill("github_create_repo", "Vytvoriť GitHub repo", "github", "curl -X POST .../user/repos"),
            Skill("telegram_send", "Posielanie Telegram správ", "communication", "via TelegramBot.send_message"),
            Skill("maintenance", "Server maintenance check", "system", "ServerMaintenance().run_full_maintenance()"),
            Skill("memory_store", "Uloženie do pamäte", "agent", "memory.store(MemoryEntry(...))"),
            Skill("memory_query", "Hľadanie v pamäti", "agent", "memory.query(keyword=...)"),
            Skill("task_create", "Vytvorenie úlohy", "agent", "tasks.create_task(name=...)"),
            Skill("pip_install", "Inštalácia Python balíkov", "code", "pip install package"),
            Skill("web_scraping", "Čítanie webových stránok", "internet", "curl -s URL | python3 parse"),
        ]
        for skill in defaults:
            self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def register(self, skill: Skill) -> None:
        """Register a new skill."""
        self._skills[skill.name] = skill
        self._save()
        logger.info("skill_registered", name=skill.name)

    def record_success(self, name: str) -> None:
        """Record successful use of a skill."""
        skill = self._skills.get(name)
        if skill:
            skill.record_success()
            self._save()
            logger.info(
                "skill_success",
                name=name,
                count=skill.success_count,
                status=skill.status.value,
            )

    def record_failure(self, name: str, error: str = "") -> None:
        """Record failed use of a skill."""
        skill = self._skills.get(name)
        if skill:
            skill.record_failure(error)
            self._save()
            logger.warning("skill_failure", name=name, error=error[:100])

    def should_test(self, name: str) -> bool:
        """Should John test this skill before using it?"""
        skill = self._skills.get(name)
        if not skill:
            return True  # Unknown skill — definitely test
        return skill.needs_testing

    def get_by_category(self, category: str) -> list[Skill]:
        return [s for s in self._skills.values() if s.category == category]

    def get_mastered(self) -> list[Skill]:
        return [s for s in self._skills.values() if s.status == SkillStatus.MASTERED]

    def get_known(self) -> list[Skill]:
        """Skills John knows (learned or mastered)."""
        return [
            s for s in self._skills.values()
            if s.status in (SkillStatus.LEARNED, SkillStatus.MASTERED)
        ]

    def get_unknown(self) -> list[Skill]:
        return [s for s in self._skills.values() if s.status == SkillStatus.UNKNOWN]

    def summary(self) -> dict[str, Any]:
        """Quick summary for context."""
        by_status: dict[str, int] = {}
        for s in self._skills.values():
            by_status[s.status.value] = by_status.get(s.status.value, 0) + 1

        return {
            "total": len(self._skills),
            "by_status": by_status,
            "mastered": [s.name for s in self.get_mastered()],
            "known": [s.name for s in self.get_known()],
            "unknown": [s.name for s in self.get_unknown()],
        }

    def to_context_string(self) -> str:
        """For including in John's thinking context."""
        lines = []
        for status_label, skills in [
            ("Mastered", self.get_mastered()),
            ("Known", [s for s in self._skills.values() if s.status == SkillStatus.LEARNED]),
            ("Unknown", self.get_unknown()),
            ("Failed", [s for s in self._skills.values() if s.status == SkillStatus.FAILED]),
        ]:
            if skills:
                names = ", ".join(s.name for s in skills)
                lines.append(f"{status_label}: {names}")
        return "\n".join(lines)
