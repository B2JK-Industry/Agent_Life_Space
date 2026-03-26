"""
Agent Life Space — Programmer Brain

John nie je len "spusti príkaz". Je programátor.

Workflow:
    1. ANALYZE — pochop problém, prečítaj existujúci kód, nájdi súvislosti
    2. PLAN — navrhni riešenie, identifikuj riziká, rozbi na kroky
    3. IMPLEMENT — píš kód po malých krokoch, testuj priebežne
    4. REVIEW — skontroluj vlastný kód, hľadaj chyby, anti-patterny
    5. TEST — spusti testy, over edge cases
    6. LEARN — zapíš čo si sa naučil, aktualizuj skills

Čo toto NIE JE:
    - Nie je to náhrada za Claude (LLM stále myslí)
    - Je to štruktúra pre lepšie programátorské rozhodovanie
    - Pomáha Johnovi písať lepší kód a učiť sa z chýb
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class CodeAnalysis:
    """Výsledok analýzy kódu/problému."""
    files_involved: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    complexity: str = "low"  # low, medium, high
    similar_past_work: list[str] = field(default_factory=list)
    suggested_approach: str = ""


@dataclass
class CodeReview:
    """Výsledok code review."""
    issues: list[dict[str, str]] = field(default_factory=list)
    passed: bool = True
    suggestions: list[str] = field(default_factory=list)
    test_coverage: str = "unknown"


class Programmer:
    """
    John's programming brain. Structured approach to coding.
    """

    def __init__(self, project_root: str = "") -> None:
        from agent.core.paths import get_project_root
        self._root = Path(
            project_root
            or get_project_root()
        )

    # --- ANALYZE ---

    def analyze_task(self, description: str) -> CodeAnalysis:
        """
        Analyzuj programátorskú úlohu PRED kódením.
        Pozri čo existuje, čo sa zmení, aké sú riziká.
        """
        analysis = CodeAnalysis()

        # Identifikuj relevantné súbory podľa kľúčových slov
        keywords = [w.lower() for w in description.split() if len(w) > 3]
        analysis.files_involved = self._find_relevant_files(keywords)

        # Odhad komplexity
        if any(w in description.lower() for w in ["refactor", "rewrite", "migration", "new module"]):
            analysis.complexity = "high"
        elif any(w in description.lower() for w in ["add", "extend", "update", "fix"]):
            analysis.complexity = "medium"
        else:
            analysis.complexity = "low"

        # Identifikuj riziká
        if "database" in description.lower() or "sqlite" in description.lower():
            analysis.risks.append("Database schema change — needs migration plan")
        if "api" in description.lower() or "endpoint" in description.lower():
            analysis.risks.append("API change — check backward compatibility")
        if len(analysis.files_involved) > 5:
            analysis.risks.append(f"Wide impact — {len(analysis.files_involved)} files affected")

        # Navrhni prístup
        if analysis.complexity == "high":
            analysis.suggested_approach = (
                "Veľká zmena. Navrhni plán, rozbi na kroky, "
                "sprav každý krok samostatne s testami."
            )
        elif analysis.complexity == "medium":
            analysis.suggested_approach = (
                "Stredná zmena. Prečítaj existujúci kód, "
                "napíš test najprv, potom implementuj."
            )
        else:
            analysis.suggested_approach = (
                "Malá zmena. Implementuj priamo, over testami."
            )

        logger.info(
            "code_analysis",
            complexity=analysis.complexity,
            files=len(analysis.files_involved),
            risks=len(analysis.risks),
        )
        return analysis

    def _find_relevant_files(self, keywords: list[str]) -> list[str]:
        """Nájdi Python súbory relevantné pre dané kľúčové slová."""
        results = []
        agent_dir = self._root / "agent"
        if not agent_dir.exists():
            return results

        for py_file in agent_dir.rglob("*.py"):
            if py_file.name.startswith("__"):
                continue
            try:
                content = py_file.read_text(encoding="utf-8").lower()
                name = py_file.stem.lower()
                if any(kw in content or kw in name for kw in keywords):
                    results.append(str(py_file.relative_to(self._root)))
            except Exception:
                pass

        return results[:20]

    # --- REVIEW ---

    def review_file(self, filepath: str) -> CodeReview:
        """
        Skontroluj Python súbor — hľadaj bežné problémy.
        Nie je to náhrada za LLM review, ale zachytí zjavné chyby.
        """
        review = CodeReview()
        path = self._root / filepath if not Path(filepath).is_absolute() else Path(filepath)

        if not path.exists():
            review.issues.append({"type": "error", "message": f"File not found: {filepath}"})
            review.passed = False
            return review

        try:
            content = path.read_text(encoding="utf-8")
            lines = content.split("\n")
        except Exception as e:
            review.issues.append({"type": "error", "message": f"Cannot read: {e}"})
            review.passed = False
            return review

        # Check: bare except
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped == "except:" or stripped == "except Exception:":
                if "pass" in (lines[i] if i < len(lines) else ""):
                    review.issues.append({
                        "type": "warning",
                        "message": f"Line {i}: bare except with pass — errors silently swallowed",
                    })

        # Check: TODO/FIXME/HACK
        for i, line in enumerate(lines, 1):
            for marker in ["TODO", "FIXME", "HACK", "XXX"]:
                if marker in line:
                    review.issues.append({
                        "type": "info",
                        "message": f"Line {i}: {marker} found — {line.strip()[:80]}",
                    })

        # Check: very long functions (> 50 lines)
        func_start = None
        func_name = ""
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("def ") or stripped.startswith("async def "):
                if func_start and (i - func_start) > 50:
                    review.issues.append({
                        "type": "warning",
                        "message": f"Function '{func_name}' is {i - func_start} lines — consider splitting",
                    })
                func_start = i
                func_name = stripped.split("(")[0].replace("def ", "").replace("async ", "")

        # Check: file too large
        if len(lines) > 500:
            review.issues.append({
                "type": "warning",
                "message": f"File has {len(lines)} lines — consider splitting into modules",
            })

        # Check: no docstring at module level
        if lines and not any(line.strip().startswith('"""') for line in lines[:5]):
            review.suggestions.append("Add module-level docstring")

        # Check: unused imports (basic — check if import name appears only once)
        import_names = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("import ") and " as " not in stripped:
                name = stripped.split()[-1]
                import_names.append(name)
            elif stripped.startswith("from ") and "import" in stripped:
                parts = stripped.split("import")[-1].strip()
                for name in parts.split(","):
                    clean = name.strip().split(" as ")[0].strip()
                    if clean and clean != "*":
                        import_names.append(clean)

        for name in import_names:
            count = content.count(name)
            if count == 1:  # Only in the import line itself
                review.suggestions.append(f"Possibly unused import: {name}")

        review.passed = not any(
            issue["type"] == "error" for issue in review.issues
        )

        logger.info(
            "code_review",
            file=filepath,
            issues=len(review.issues),
            passed=review.passed,
        )
        return review

    # --- TEST ---

    def run_tests(self, path: str = "tests/", verbose: bool = False) -> dict[str, Any]:
        """Spusti pytest a vráť štrukturovaný výsledok."""
        cmd = ["python3", "-m", "pytest", str(self._root / path), "-q", "--tb=short"]
        if verbose:
            cmd.append("-v")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
                cwd=str(self._root),
            )

            output = result.stdout
            # Parse pytest output
            passed = 0
            failed = 0
            errors = 0

            for line in output.split("\n"):
                if "passed" in line:
                    parts = line.split()
                    for i, p in enumerate(parts):
                        if p == "passed" and i > 0:
                            try:
                                passed = int(parts[i - 1])
                            except ValueError:
                                pass
                        if p == "failed" and i > 0:
                            try:
                                failed = int(parts[i - 1])
                            except ValueError:
                                pass
                        if p in ("error", "errors") and i > 0:
                            try:
                                errors = int(parts[i - 1])
                            except ValueError:
                                pass

            return {
                "success": result.returncode == 0,
                "passed": passed,
                "failed": failed,
                "errors": errors,
                "output": output[-2000:] if not verbose else output[-5000:],
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "timeout", "passed": 0, "failed": 0}
        except Exception as e:
            return {"success": False, "error": str(e), "passed": 0, "failed": 0}

    def run_lint(self, filepath: str) -> dict[str, Any]:
        """Spusti základný Python syntax check."""
        path = self._root / filepath
        try:
            result = subprocess.run(
                ["python3", "-m", "py_compile", str(path)],
                capture_output=True, text=True, timeout=10,
            )
            return {
                "valid": result.returncode == 0,
                "errors": result.stderr.strip() if result.returncode != 0 else "",
            }
        except Exception as e:
            return {"valid": False, "errors": str(e)}

    # --- LEARN FROM ERRORS ---

    def analyze_error(self, error_text: str) -> dict[str, Any]:
        """
        Analyzuj chybovú hlášku — identifikuj typ, príčinu, návrh riešenia.
        Nie LLM — deterministické pattern matching.
        """
        error_lower = error_text.lower()
        result: dict[str, Any] = {
            "error_type": "unknown",
            "likely_cause": "",
            "suggested_fix": "",
            "severity": "medium",
        }

        # Import errors
        if "importerror" in error_lower or "modulenotfounderror" in error_lower:
            result["error_type"] = "import_error"
            result["likely_cause"] = "Missing module or wrong import path"
            result["suggested_fix"] = "Check if module is installed (pip install) or import path is correct"
            result["severity"] = "high"

        # Name errors
        elif "nameerror" in error_lower:
            result["error_type"] = "name_error"
            result["likely_cause"] = "Variable or function not defined — typo or missing import"
            result["suggested_fix"] = "Check spelling, add missing import, or define the variable"
            result["severity"] = "high"

        # Type errors
        elif "typeerror" in error_lower:
            result["error_type"] = "type_error"
            result["likely_cause"] = "Wrong type passed to function or wrong number of arguments"
            result["suggested_fix"] = "Check function signature and argument types"
            result["severity"] = "high"

        # Attribute errors
        elif "attributeerror" in error_lower:
            result["error_type"] = "attribute_error"
            result["likely_cause"] = "Object doesn't have this method/property"
            result["suggested_fix"] = "Check object type and available methods (dir(obj))"
            result["severity"] = "high"

        # Timeout
        elif "timeout" in error_lower or "timed out" in error_lower:
            result["error_type"] = "timeout"
            result["likely_cause"] = "Operation took too long — network, slow query, or infinite loop"
            result["suggested_fix"] = "Add timeout parameter, check for infinite loops, optimize query"
            result["severity"] = "medium"

        # Permission
        elif "permission" in error_lower or "denied" in error_lower:
            result["error_type"] = "permission_error"
            result["likely_cause"] = "Insufficient permissions for file/directory/command"
            result["suggested_fix"] = "Check file permissions, use correct user, or use sg docker for Docker"
            result["severity"] = "medium"

        # Connection errors
        elif "connection" in error_lower or "refused" in error_lower:
            result["error_type"] = "connection_error"
            result["likely_cause"] = "Service not running or wrong host/port"
            result["suggested_fix"] = "Check if service is running, verify host and port"
            result["severity"] = "medium"

        # Syntax errors
        elif "syntaxerror" in error_lower:
            result["error_type"] = "syntax_error"
            result["likely_cause"] = "Invalid Python syntax"
            result["suggested_fix"] = "Check line number in traceback, fix syntax (missing colon, bracket, etc.)"
            result["severity"] = "high"

        # Key errors
        elif "keyerror" in error_lower:
            result["error_type"] = "key_error"
            result["likely_cause"] = "Dictionary key doesn't exist"
            result["suggested_fix"] = "Use .get(key, default) instead of dict[key]"
            result["severity"] = "medium"

        # File not found
        elif "filenotfound" in error_lower or "no such file" in error_lower:
            result["error_type"] = "file_not_found"
            result["likely_cause"] = "File or directory doesn't exist"
            result["suggested_fix"] = "Check path, use Path.exists() before accessing"
            result["severity"] = "medium"

        return result

    # --- STRUCTURED PROGRAMMING WORKFLOW ---

    def programming_workflow(self, task: str) -> dict[str, Any]:
        """
        Vráti štruktúrovaný plán pre programátorskú úlohu.
        Používa sa v JSON kontexte pre Claude.
        """
        analysis = self.analyze_task(task)

        steps = []

        # Step 1: Always analyze first
        steps.append({
            "step": 1,
            "action": "analyze",
            "description": "Prečítaj relevantné súbory a pochop existujúci kód",
            "files": analysis.files_involved[:5],
        })

        # Step 2: Check tests
        steps.append({
            "step": 2,
            "action": "check_tests",
            "description": "Pozri existujúce testy — čo je pokryté, čo chýba",
        })

        # Step 3: Plan (for medium+ complexity)
        if analysis.complexity in ("medium", "high"):
            steps.append({
                "step": 3,
                "action": "plan",
                "description": "Navrhni riešenie — aká štruktúra, aké rozhranie, aké edge cases",
                "risks": analysis.risks,
            })

        # Step 4: Write test first (TDD)
        steps.append({
            "step": len(steps) + 1,
            "action": "write_test",
            "description": "Napíš test PRED implementáciou — definuj očakávané správanie",
        })

        # Step 5: Implement
        steps.append({
            "step": len(steps) + 1,
            "action": "implement",
            "description": "Implementuj riešenie po malých krokoch",
        })

        # Step 6: Review
        steps.append({
            "step": len(steps) + 1,
            "action": "review",
            "description": "Skontroluj vlastný kód — hľadaj chyby, anti-patterny, chýbajúce edge cases",
        })

        # Step 7: Run all tests
        steps.append({
            "step": len(steps) + 1,
            "action": "test",
            "description": "Spusti VŠETKY testy (nie len nové) — over že nič nie je rozbité",
        })

        # Step 8: Commit
        steps.append({
            "step": len(steps) + 1,
            "action": "commit",
            "description": "Commitni s jasným popisom čo a prečo",
        })

        return {
            "task": task,
            "complexity": analysis.complexity,
            "files_involved": analysis.files_involved[:10],
            "risks": analysis.risks,
            "steps": steps,
            "approach": analysis.suggested_approach,
        }
