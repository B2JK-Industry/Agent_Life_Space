"""
Agent Life Space — Knowledge Base

Dlhodobá referenčná pamäť. John nemusí všetko vedieť naspamäť —
ale musí vedieť KDE hľadať.

Knowledge base je adresár s .md súbormi — čitateľné človekom aj agentom.
John si sem zapisuje naučené veci, recepty, fakty, postupy.

Štruktúra:
    agent/brain/knowledge/
    ├── skills/          — čo viem robiť (automaticky zo skills.json)
    ├── systems/         — ako fungujú veci (GitHub API, Docker, server)
    ├── people/          — kto je kto (Daniel, kontakty)
    ├── projects/        — aktívne projekty
    └── learned/         — čo som sa naučil (z experimentov, chýb)

John vie:
    - Prehľadať knowledge base podľa kategórie alebo kľúčového slova
    - Pridať novú znalosť
    - Aktualizovať existujúcu
    - Vrátiť relevantné znalosti pre aktuálny kontext
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class KnowledgeBase:
    """
    File-based knowledge base. Human-readable .md files.
    """

    def __init__(self, base_dir: str = "agent/brain/knowledge") -> None:
        self._base = Path(base_dir)
        self._categories = ["skills", "systems", "people", "projects", "learned"]
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for cat in self._categories:
            (self._base / cat).mkdir(parents=True, exist_ok=True)

    def store(
        self,
        category: str,
        name: str,
        content: str,
        tags: list[str] | None = None,
    ) -> Path:
        """
        Uloží znalosť do .md súboru.
        Ak existuje, prepíše (knowledge sa updatuje, nie duplikuje).
        """
        if category not in self._categories:
            msg = f"Unknown category: {category}. Valid: {self._categories}"
            raise ValueError(msg)

        # Sanitize filename
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.lower())
        filepath = self._base / category / f"{safe_name}.md"

        tags_str = ", ".join(tags) if tags else ""
        header = (
            f"# {name}\n"
            f"_Kategória: {category} | "
            f"Tags: {tags_str} | "
            f"Aktualizované: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n\n"
        )

        filepath.write_text(header + content, encoding="utf-8")
        logger.info("knowledge_stored", category=category, name=name)
        return filepath

    def get(self, category: str, name: str) -> str | None:
        """Načítaj konkrétnu znalosť."""
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.lower())
        filepath = self._base / category / f"{safe_name}.md"
        if filepath.exists():
            return filepath.read_text(encoding="utf-8")
        return None

    def search(self, keyword: str, category: str | None = None) -> list[dict[str, str]]:
        """
        Hľadaj v knowledge base. Vracia zoznam nájdených záznamov.
        Prehľadáva obsah aj názvy súborov.
        """
        keyword_lower = keyword.lower()
        results = []

        dirs = [self._base / category] if category else [self._base / c for c in self._categories]

        for d in dirs:
            if not d.exists():
                continue
            for filepath in d.glob("*.md"):
                content = filepath.read_text(encoding="utf-8")
                if keyword_lower in content.lower() or keyword_lower in filepath.stem.lower():
                    # Return first 300 chars as preview
                    results.append({
                        "category": d.name,
                        "name": filepath.stem,
                        "preview": content[:300],
                        "path": str(filepath),
                    })

        return results

    def list_category(self, category: str) -> list[str]:
        """Zoznam znalostí v kategórii."""
        d = self._base / category
        if not d.exists():
            return []
        return [f.stem for f in sorted(d.glob("*.md"))]

    def list_all(self) -> dict[str, list[str]]:
        """Prehľad celej knowledge base."""
        return {cat: self.list_category(cat) for cat in self._categories}

    def delete(self, category: str, name: str) -> bool:
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.lower())
        filepath = self._base / category / f"{safe_name}.md"
        if filepath.exists():
            filepath.unlink()
            return True
        return False

    def summary(self) -> dict[str, Any]:
        """Pre JSON kontext — koľko znalostí v každej kategórii."""
        counts = {}
        total = 0
        for cat in self._categories:
            items = self.list_category(cat)
            counts[cat] = len(items)
            total += len(items)
        return {"total": total, "by_category": counts}
