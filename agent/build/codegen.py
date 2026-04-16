"""
Agent Life Space — Build Code Generation

Generates BuildOperation[] from a natural language description using LLM.
Bridges the gap between "user describes what to build" and deterministic
build execution via WRITE_FILE operations.

Workflow:
    1. User provides description via /build --description "..."
    2. This module sends description to Opus for structured code generation
    3. Opus returns JSON array of BuildOperation dicts
    4. Operations are validated and returned for build execution

Safety:
    - Only WRITE_FILE operations generated (no destructive ops on existing code)
    - All paths validated as relative with no ".." traversal
    - Max operation count enforced from capability constraints
    - LLM output parsed as strict JSON, not eval'd
"""

from __future__ import annotations

import json

import structlog

from agent.build.models import BuildOperation, BuildOperationType

logger = structlog.get_logger(__name__)

# Max operations to request from LLM (safety cap)
_MAX_GENERATED_OPERATIONS = 20

_CODEGEN_SYSTEM_PROMPT = """\
You are an expert code generation AI. Your job is to generate implementation \
files for a software project based on the user's description.

You MUST respond with ONLY a valid JSON array of file operations. No markdown, \
no explanations, no code fences — just the raw JSON array.

Each operation is an object with these fields:
- "operation_type": always "write_file"
- "path": relative file path (e.g. "app/main.py", "tests/test_api.py")
- "description": short description of what this file does
- "content": the complete file content as a string

Rules:
- Generate complete, working, production-quality code
- Include all imports, proper error handling, type hints
- Generate test files with good coverage (aim for 80%+)
- Include requirements.txt or pyproject.toml if dependencies are needed
- Use relative paths only — no absolute paths, no ".."
- Maximum {max_ops} files
- Do NOT include any text outside the JSON array
- Ensure all files are self-consistent and would pass pytest + linting

Example response format:
[
  {{"operation_type": "write_file", "path": "app/main.py", "description": "Main application", "content": "import ...\\n..."}},
  {{"operation_type": "write_file", "path": "tests/test_app.py", "description": "Tests", "content": "import pytest\\n..."}}
]
"""


async def generate_build_operations(
    description: str,
    *,
    max_operations: int = _MAX_GENERATED_OPERATIONS,
    model: str = "",
    timeout: int = 300,
) -> list[BuildOperation]:
    """Generate BuildOperations from a natural language description via LLM.

    Returns a list of validated WRITE_FILE operations ready for build execution.
    Raises RuntimeError if LLM fails or output is unparseable.
    """
    from agent.core.llm_provider import GenerateRequest, get_provider

    provider = get_provider()

    system_prompt = _CODEGEN_SYSTEM_PROMPT.format(max_ops=max_operations)

    user_prompt = (
        f"Generate implementation files for the following project:\n\n"
        f"{description}\n\n"
        f"Respond with ONLY a JSON array of write_file operations. "
        f"Maximum {max_operations} files."
    )

    if not model:
        from agent.core.models import get_model

        model = get_model("programming").model_id

    full_prompt = f"{system_prompt}\n\n{user_prompt}"
    logger.info("codegen_request",
                model=model,
                prompt_length=len(full_prompt),
                timeout=timeout)

    response = await provider.generate(GenerateRequest(
        messages=[
            {"role": "user", "content": full_prompt},
        ],
        model=model,
        timeout=timeout,
        max_turns=1,  # Single-turn text generation, no tool use needed
    ))

    if not response.success:
        logger.error("codegen_llm_error",
                     error=response.error[:300],
                     latency_ms=response.latency_ms,
                     raw_keys=list(response.raw.keys()) if response.raw else [])
        raise RuntimeError(f"LLM codegen failed: {response.error[:200]}")

    raw_text = (response.text or "").strip()
    if not raw_text:
        raise RuntimeError("LLM codegen returned empty response")

    logger.info(
        "codegen_response",
        model=model,
        cost_usd=round(response.cost_usd, 4),
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        response_length=len(raw_text),
    )
    # Verbose dump (short tier — only useful while actively debugging
    # a parse failure). Truncated to 2KB to keep the line size sane.
    logger.debug(
        "codegen_raw_response_preview",
        model=model,
        preview=raw_text[:2048],
    )

    try:
        operations = _parse_operations(raw_text, max_operations)
    except RuntimeError as e:
        # Long-tier event: codegen produced output but we couldn't parse
        # it. The build orchestrator's fallback guard will reject the
        # job — we want this in long retention so post-mortems can find
        # the offending response.
        logger.error(
            "codegen_parse_failed",
            model=model,
            error=str(e)[:300],
            response_length=len(raw_text),
            response_starts_with=raw_text[:80],
        )
        raise

    logger.info("codegen_operations_generated", count=len(operations))
    return operations


def _parse_operations(
    raw_text: str,
    max_operations: int,
) -> list[BuildOperation]:
    """Parse LLM output into validated BuildOperation list.

    Handles common LLM quirks: markdown fences, trailing commas, etc.
    """
    # Strip markdown code fences if present
    text = raw_text.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        first_newline = text.index("\n") if "\n" in text else len(text)
        text = text[first_newline + 1:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    # Strip preamble/postamble text around the JSON array.
    # Opus sometimes prefixes explanations like "The fix is..." before the JSON.
    # Find the first '[' and matching last ']' to extract just the array.
    if not text.startswith("[") and "[" in text:
        text = text[text.index("["):]
    if not text.endswith("]") and "]" in text:
        text = text[:text.rindex("]") + 1]

    # Try parsing as JSON array
    try:
        data = json.loads(text, strict=False)
    except json.JSONDecodeError:
        # Try fixing trailing commas
        cleaned = text.rstrip().rstrip(",")
        if not cleaned.endswith("]"):
            cleaned += "]"
        try:
            data = json.loads(cleaned, strict=False)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"LLM codegen output is not valid JSON: {e}\n"
                f"First 500 chars: {text[:500]}"
            ) from e

    if not isinstance(data, list):
        raise RuntimeError(
            f"LLM codegen output is not a JSON array, got {type(data).__name__}"
        )

    operations: list[BuildOperation] = []
    for i, item in enumerate(data[:max_operations]):
        if not isinstance(item, dict):
            logger.warning("codegen_skip_non_dict", index=i)
            continue

        op = BuildOperation(
            operation_type=BuildOperationType.WRITE_FILE,
            path=str(item.get("path", "")),
            description=str(item.get("description", "")),
            content=str(item.get("content", "")),
        )

        # Validate
        errors = op.validate()
        if errors:
            logger.warning("codegen_operation_invalid", index=i, errors=errors)
            continue

        if not op.content:
            logger.warning("codegen_empty_content", index=i, path=op.path)
            continue

        operations.append(op)

    if not operations:
        raise RuntimeError("LLM codegen produced no valid operations")

    return operations
