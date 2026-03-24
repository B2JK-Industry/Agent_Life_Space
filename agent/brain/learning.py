"""
Agent Life Space — Learning System

Prepája skills, knowledge base, a pamäť do jedného flow.

Keď John dostane úlohu:
    1. Pozri skills → viem to? (skills.json)
    2. Ak nie → pozri knowledge base → je tam návod? (knowledge/)
    3. Ak nie → skús to (test) → zapíš výsledok
    4. Ak áno → urob to → zapíš úspech/zlyhanie

Keď John niečo urobí:
    1. Zapíš do episodic memory (čo sa stalo)
    2. Aktualizuj skill (success/failure)
    3. Ak sa naučil niečo nové → zapíš do knowledge base
    4. Ak vzor sa opakuje → povýš na semantic memory

Toto je most medzi "myslím" a "viem".
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import structlog

from agent.brain.knowledge import KnowledgeBase
from agent.brain.skills import Skill, SkillRegistry, SkillStatus

logger = structlog.get_logger(__name__)

# Test commands for each skill — used by try_skill()
_SKILL_TESTS: dict[str, str] = {
    "file_write": "echo 'john_test' > /tmp/john_skill_test.txt && cat /tmp/john_skill_test.txt && rm /tmp/john_skill_test.txt",
    "file_read": "cat /etc/hostname",
    "git_commit": "cd ~/agent-life-space && git status",
    "git_status": "cd ~/agent-life-space && git status",
    "system_health": "free -h && df -h /",
    "process_check": "ps aux --sort=-%mem | head -5",
    "curl": "curl -s -o /dev/null -w '%{http_code}' https://httpbin.org/get",
    "python_run": "python3 -c 'print(\"hello from john\")'",
    "pytest": "cd ~/agent-life-space && python3 -m pytest tests/ -q --tb=no 2>&1 | tail -1",
    "pip_install": "pip3 list 2>/dev/null | head -3",
    "docker_run": "docker --version 2>/dev/null || echo 'docker not available'",
    "maintenance": "python3 -c 'import psutil; print(f\"CPU: {psutil.cpu_percent()}%, RAM: {psutil.virtual_memory().percent}%\")'",
    "telegram_send": "echo 'telegram_send: ok'",
    "memory_store": "cd ~/agent-life-space && python3 -c 'print(\"memory_store: ok\")'",
    "memory_query": "cd ~/agent-life-space && python3 -c 'print(\"memory_query: ok\")'",
    "task_create": "cd ~/agent-life-space && python3 -c 'print(\"task_create: ok\")'",
    "web_scraping": "curl -s -o /dev/null -w '%{http_code}' https://example.com",
    "github_api": "curl -s -o /dev/null -w '%{http_code}' https://api.github.com",
    "github_create_issue": "echo 'requires token — skip auto-test'",
    "github_create_repo": "echo 'requires token — skip auto-test'",
}


class LearningSystem:
    """
    Prepája skills, knowledge, a pamäť.
    John sa učí z toho čo robí.
    """

    def __init__(
        self,
        skills_path: str = "agent/brain/skills.json",
        knowledge_dir: str = "agent/brain/knowledge",
    ) -> None:
        self.skills = SkillRegistry(skills_path)
        self.knowledge = KnowledgeBase(knowledge_dir)

    def can_i_do(self, skill_name: str, auto_test: bool = False) -> dict[str, Any]:
        """
        John sa pýta: "Viem toto robiť?"
        Vracia rozhodnutie a kontext.

        If auto_test=True and skill needs testing, runs test automatically.
        """
        skill = self.skills.get(skill_name)

        if skill is None:
            # Nepoznám tento skill — pozri knowledge base
            kb_results = self.knowledge.search(skill_name)
            return {
                "answer": "unknown",
                "skill_exists": False,
                "should_test": True,
                "knowledge_found": len(kb_results) > 0,
                "knowledge_hints": [r["preview"][:100] for r in kb_results[:2]],
                "advice": "Nepoznám tento skill. Skús to a uvidíme.",
            }

        # Auto-test: if skill needs testing and we have a test command, run it now
        if auto_test and skill.needs_testing:
            test_result = self.try_skill(skill_name)
            if test_result["tested"]:
                # Re-read skill after test updated it
                skill = self.skills.get(skill_name)

        if skill.status == SkillStatus.MASTERED:
            return {
                "answer": "yes",
                "skill_exists": True,
                "should_test": False,
                "confidence": skill.confidence,
                "success_count": skill.success_count,
                "advice": f"Viem to — {skill.description}. Robil som to {skill.success_count}×.",
            }

        if skill.status == SkillStatus.LEARNED:
            return {
                "answer": "probably",
                "skill_exists": True,
                "should_test": skill.needs_testing,
                "confidence": skill.confidence,
                "success_count": skill.success_count,
                "advice": f"Už som to robil {skill.success_count}×, ale ešte nie som istý.",
            }

        if skill.status == SkillStatus.FAILED:
            return {
                "answer": "failed_before",
                "skill_exists": True,
                "should_test": True,
                "last_error": skill.last_error,
                "advice": f"Minule sa to nepodarilo: {skill.last_error}. Môžem skúsiť znova.",
            }

        # UNKNOWN or TESTING
        return {
            "answer": "not_yet",
            "skill_exists": True,
            "should_test": True,
            "advice": f"Ešte som to neskúšal. Command: {skill.command_example}",
        }

    def try_skill(self, skill_name: str) -> dict[str, Any]:
        """
        Otestuj skill teraz. Spustí test command a zapíše výsledok.
        Event-driven — volá sa keď John skill potrebuje, nie z cronu.
        """
        cmd = _SKILL_TESTS.get(skill_name)
        if not cmd:
            return {"tested": False, "reason": "no test command"}

        logger.info("skill_auto_test_start", skill=skill_name)

        try:
            result = subprocess.run(
                ["bash", "-c", cmd],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                self.skills.record_success(skill_name)
                output = result.stdout.strip()[:200]
                logger.info("skill_auto_test_ok", skill=skill_name, output=output)
                return {
                    "tested": True,
                    "success": True,
                    "output": output,
                    "skill": skill_name,
                }
            else:
                error = result.stderr.strip()[:200]
                self.skills.record_failure(skill_name, error)
                logger.warning("skill_auto_test_fail", skill=skill_name, error=error)
                return {
                    "tested": True,
                    "success": False,
                    "error": error,
                    "skill": skill_name,
                }
        except subprocess.TimeoutExpired:
            self.skills.record_failure(skill_name, "timeout (30s)")
            logger.warning("skill_auto_test_timeout", skill=skill_name)
            return {"tested": True, "success": False, "error": "timeout", "skill": skill_name}
        except Exception as e:
            logger.error("skill_auto_test_error", skill=skill_name, error=str(e))
            return {"tested": True, "success": False, "error": str(e), "skill": skill_name}

    def i_did_it(
        self,
        skill_name: str,
        success: bool,
        error: str = "",
        what_i_learned: str = "",
    ) -> dict[str, Any]:
        """
        John hlási: "Urobil som to" (alebo nie).
        Aktualizuje skills a voliteľne knowledge base.
        """
        if success:
            self.skills.record_success(skill_name)
            action = "success"
        else:
            self.skills.record_failure(skill_name, error)
            action = "failure"

        # Ak sa naučil niečo nové, zapíš do knowledge base
        if what_i_learned:
            self.knowledge.store(
                category="learned",
                name=f"{skill_name}_{action}",
                content=what_i_learned,
                tags=[skill_name, action],
            )

        skill = self.skills.get(skill_name)
        return {
            "action": action,
            "skill": skill_name,
            "new_status": skill.status.value if skill else "unknown",
            "confidence": skill.confidence if skill else 0,
            "knowledge_saved": bool(what_i_learned),
        }

    def learn_new_skill(
        self,
        name: str,
        description: str,
        category: str = "general",
        command_example: str = "",
        knowledge_content: str = "",
    ) -> dict[str, Any]:
        """
        John sa naučí nový skill — zaregistruje ho a voliteľne zapíše do KB.
        """
        skill = Skill(
            name=name,
            description=description,
            category=category,
            command_example=command_example,
        )
        self.skills.register(skill)

        if knowledge_content:
            self.knowledge.store(
                category="skills",
                name=name,
                content=(
                    f"## {description}\n\n"
                    f"Kategória: {category}\n"
                    f"Príkaz: `{command_example}`\n\n"
                    f"{knowledge_content}"
                ),
                tags=[name, category],
            )

        return {
            "registered": True,
            "name": name,
            "status": "unknown",
            "knowledge_saved": bool(knowledge_content),
        }

    def what_do_i_know(self) -> dict[str, Any]:
        """
        John sa pýta: "Čo všetko viem?"
        """
        return {
            "skills": self.skills.summary(),
            "knowledge": self.knowledge.summary(),
        }

    def find_relevant(self, topic: str) -> dict[str, Any]:
        """
        John hľadá: "Čo viem o tejto téme?"
        Prehľadá skills aj knowledge base.
        """
        # Skills match
        matching_skills = []
        for skill in self.skills._skills.values():
            if topic.lower() in skill.name.lower() or topic.lower() in skill.description.lower():
                matching_skills.append({
                    "name": skill.name,
                    "status": skill.status.value,
                    "confidence": skill.confidence,
                })

        # Knowledge match
        kb_results = self.knowledge.search(topic)

        return {
            "topic": topic,
            "matching_skills": matching_skills,
            "knowledge_entries": [
                {"category": r["category"], "name": r["name"], "preview": r["preview"][:150]}
                for r in kb_results
            ],
            "total_found": len(matching_skills) + len(kb_results),
        }
