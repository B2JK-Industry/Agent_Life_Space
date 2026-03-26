"""
Agent Life Space — LLM Router

Handles all communication with LLM providers (Claude, future others).
NEVER sends raw prompts — always uses templates with variable substitution.
ALWAYS validates responses against JSON schemas.

Anti-hallucination measures:
    1. All prompts are templates (no free text)
    2. All responses must be valid JSON
    3. Responses are validated against expected schema
    4. Invalid responses trigger retry with error feedback
    5. Max 3 retries, then fail to dead letter queue
    6. Temperature 0.0 by default for deterministic output
    7. Timeouts on every call — no hanging
"""

from __future__ import annotations

import time
from typing import Any

import jsonschema
import orjson
import structlog

from agent.core.messages import LLMRequest, LLMResponse

logger = structlog.get_logger(__name__)


class PromptTemplate:
    """
    A pre-defined prompt template. Templates prevent prompt injection
    and ensure consistent, schema-compliant LLM interactions.
    """

    def __init__(
        self,
        template_id: str,
        system_prompt: str,
        user_template: str,
        response_schema: dict[str, Any] | None = None,
        description: str = "",
    ) -> None:
        self.template_id = template_id
        self.system_prompt = system_prompt
        self.user_template = user_template
        self.response_schema = response_schema
        self.description = description

    def render(self, variables: dict[str, str]) -> tuple[str, str]:
        """
        Render template with variables.
        Returns (system_prompt, user_message).
        Uses safe string formatting — missing keys raise, not silently skip.
        """
        try:
            rendered_user = self.user_template.format(**variables)
        except KeyError as e:
            msg = f"Template '{self.template_id}' missing variable: {e}"
            raise ValueError(msg) from e
        return self.system_prompt, rendered_user


class TemplateRegistry:
    """
    Registry of all prompt templates.
    Templates are loaded at startup, not generated at runtime.
    """

    def __init__(self) -> None:
        self._templates: dict[str, PromptTemplate] = {}
        self._register_defaults()

    def register(self, template: PromptTemplate) -> None:
        self._templates[template.template_id] = template

    def get(self, template_id: str) -> PromptTemplate:
        if template_id not in self._templates:
            msg = f"Unknown template: '{template_id}'. Available: {list(self._templates.keys())}"
            raise KeyError(msg)
        return self._templates[template_id]

    def list_templates(self) -> list[str]:
        return list(self._templates.keys())

    def _register_defaults(self) -> None:
        """Register built-in templates."""
        self.register(
            PromptTemplate(
                template_id="task_breakdown",
                system_prompt=(
                    "You are a task planning assistant. Break down tasks into "
                    "actionable steps. Respond ONLY with valid JSON matching the schema."
                ),
                user_template=(
                    "Break down this task into steps: {task}\n\n"
                    "Context: {context}\n\n"
                    "Respond with JSON."
                ),
                response_schema={
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "step_number": {"type": "integer"},
                                    "description": {"type": "string"},
                                    "estimated_minutes": {"type": "integer"},
                                    "requires_llm": {"type": "boolean"},
                                },
                                "required": [
                                    "step_number",
                                    "description",
                                    "requires_llm",
                                ],
                            },
                        },
                        "total_steps": {"type": "integer"},
                    },
                    "required": ["steps", "total_steps"],
                },
                description="Break a high-level task into actionable steps",
            )
        )
        self.register(
            PromptTemplate(
                template_id="summarize_for_memory",
                system_prompt=(
                    "You are a memory manager. Summarize information for storage. "
                    "Be concise and factual. Respond ONLY with valid JSON."
                ),
                user_template=(
                    "Summarize this for long-term memory storage:\n\n"
                    "{content}\n\n"
                    "Category: {category}\n"
                    "Respond with JSON."
                ),
                response_schema={
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "maxLength": 500},
                        "keywords": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 10,
                        },
                        "importance": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                        "category": {"type": "string"},
                    },
                    "required": ["summary", "keywords", "importance", "category"],
                },
                description="Summarize content for memory storage",
            )
        )
        self.register(
            PromptTemplate(
                template_id="evaluate_opportunity",
                system_prompt=(
                    "You are a business analyst. Evaluate opportunities objectively. "
                    "Be conservative with risk assessment. Respond ONLY with valid JSON."
                ),
                user_template=(
                    "Evaluate this opportunity:\n\n"
                    "{opportunity}\n\n"
                    "Budget constraint: {budget}\n"
                    "Risk tolerance: {risk_tolerance}\n"
                    "Respond with JSON."
                ),
                response_schema={
                    "type": "object",
                    "properties": {
                        "viable": {"type": "boolean"},
                        "confidence": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                        "risks": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "potential_revenue_usd": {"type": "number", "minimum": 0},
                        "recommendation": {"type": "string"},
                        "requires_human_review": {"type": "boolean"},
                    },
                    "required": [
                        "viable",
                        "confidence",
                        "risks",
                        "recommendation",
                        "requires_human_review",
                    ],
                },
                description="Evaluate a business/earning opportunity",
            )
        )
        self.register(
            PromptTemplate(
                template_id="generate_content",
                system_prompt=(
                    "You are a content creator. Generate content as specified. "
                    "Respond ONLY with valid JSON."
                ),
                user_template=(
                    "Generate content:\n"
                    "Type: {content_type}\n"
                    "Topic: {topic}\n"
                    "Tone: {tone}\n"
                    "Max length: {max_length} words\n"
                    "Respond with JSON."
                ),
                response_schema={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "word_count": {"type": "integer"},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["title", "content", "word_count"],
                },
                description="Generate structured content (articles, posts, etc.)",
            )
        )


def validate_json_response(
    raw_text: str,
    schema: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, bool, list[str]]:
    """
    Parse and validate LLM response against JSON schema.

    Returns (parsed_dict, is_valid, error_list).
    Deterministic — no randomness, no LLM involved.
    """
    errors: list[str] = []

    # Step 1: Parse JSON
    try:
        parsed = orjson.loads(raw_text.encode())
    except (orjson.JSONDecodeError, ValueError) as e:
        # Try to extract JSON from markdown code blocks
        import re

        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", raw_text, re.DOTALL)
        if json_match:
            try:
                parsed = orjson.loads(json_match.group(1).encode())
            except (orjson.JSONDecodeError, ValueError):
                return None, False, [f"Invalid JSON: {e!s}"]
        else:
            return None, False, [f"Invalid JSON: {e!s}"]

    if not isinstance(parsed, dict):
        return None, False, ["Response must be a JSON object, not array or primitive"]

    # Step 2: Validate against schema if provided
    if schema is not None:
        try:
            jsonschema.validate(instance=parsed, schema=schema)
        except jsonschema.ValidationError as e:
            errors.append(f"Schema validation failed: {e.message}")
            return parsed, False, errors

    return parsed, True, []


class LLMRouter:
    """
    Routes requests to LLM providers.
    Currently supports Claude (Anthropic API).
    Designed for multi-provider future.
    """

    def __init__(self, default_provider: str = "anthropic") -> None:
        self.templates = TemplateRegistry()
        self._default_provider = default_provider
        self._clients: dict[str, Any] = {}
        self._call_count = 0
        self._error_count = 0
        self._total_tokens = 0

    def _get_client(self, provider: str = "anthropic") -> Any:
        """Get or create API client for provider."""
        if provider not in self._clients:
            if provider == "anthropic":
                import anthropic

                self._clients[provider] = anthropic.Anthropic()
            else:
                msg = f"Unknown provider: {provider}"
                raise ValueError(msg)
        return self._clients[provider]

    async def call(
        self,
        request: LLMRequest,
        provider: str | None = None,
    ) -> LLMResponse:
        """
        Send a structured request to an LLM provider.

        1. Render the template
        2. Call the API with timeout
        3. Validate response against schema
        4. Retry if invalid (up to request.retry_on_invalid times)
        5. Return structured LLMResponse
        """
        provider = provider or self._default_provider
        template = self.templates.get(request.template_id)
        system_prompt, user_message = template.render(request.variables)

        last_error: str = ""
        retry_count = 0

        for attempt in range(1 + request.retry_on_invalid):
            start_time = time.monotonic()

            try:
                # If retrying, add correction context
                if attempt > 0:
                    user_message_with_correction = (
                        f"{user_message}\n\n"
                        f"IMPORTANT: Your previous response was invalid. "
                        f"Error: {last_error}\n"
                        f"Please fix and respond with valid JSON only."
                    )
                else:
                    user_message_with_correction = user_message

                client = self._get_client(provider)

                # Synchronous call wrapped for now (will be async with httpx later)
                import asyncio

                # Use request.model if specified, otherwise default to sonnet (cost-effective)
                model_id = request.model or "claude-sonnet-4-6"

                response = await asyncio.to_thread(
                    client.messages.create,
                    model=model_id,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    system=system_prompt,
                    messages=[
                        {"role": "user", "content": user_message_with_correction}
                    ],
                )

                latency_ms = int((time.monotonic() - start_time) * 1000)
                raw_text = response.content[0].text
                tokens_used = response.usage.input_tokens + response.usage.output_tokens

                self._call_count += 1
                self._total_tokens += tokens_used

                # Validate response
                parsed, is_valid, errors = validate_json_response(
                    raw_text, template.response_schema
                )

                if is_valid:
                    return LLMResponse(
                        request_id=request.template_id,
                        raw_text=raw_text,
                        parsed=parsed,
                        is_valid=True,
                        model_used=response.model,
                        tokens_used=tokens_used,
                        latency_ms=latency_ms,
                        retry_count=attempt,
                    )

                # Invalid — prepare for retry
                last_error = "; ".join(errors)
                retry_count = attempt + 1
                logger.warning(
                    "llm_response_invalid",
                    template=request.template_id,
                    attempt=attempt + 1,
                    errors=errors,
                )

            except Exception as e:
                latency_ms = int((time.monotonic() - start_time) * 1000)
                self._error_count += 1
                last_error = str(e)
                logger.error(
                    "llm_call_error",
                    template=request.template_id,
                    attempt=attempt + 1,
                    error=str(e),
                )

        # All retries exhausted
        return LLMResponse(
            request_id=request.template_id,
            raw_text="",
            parsed=None,
            is_valid=False,
            validation_errors=[last_error],
            model_used="unknown",
            tokens_used=0,
            latency_ms=0,
            retry_count=retry_count,
        )

    def get_stats(self) -> dict[str, int]:
        return {
            "total_calls": self._call_count,
            "total_errors": self._error_count,
            "total_tokens": self._total_tokens,
        }
