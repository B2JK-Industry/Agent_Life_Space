"""
Test scenarios for LLM Router.

Tests that DON'T require API calls (no money spent, no network needed):
1. Template rendering with variables
2. JSON response validation against schema
3. Missing template variables caught
4. Invalid JSON responses detected
5. Schema validation catches wrong types
6. Markdown-wrapped JSON extracted correctly
7. Template registry works
"""

from __future__ import annotations

import pytest

from agent.core.llm_router import (
    LLMRouter,
    PromptTemplate,
    TemplateRegistry,
    validate_json_response,
)
from agent.core.messages import LLMRequest


class TestPromptTemplate:
    """Templates prevent free-form prompts and prompt injection."""

    def test_render_with_variables(self) -> None:
        template = PromptTemplate(
            template_id="test",
            system_prompt="You are a helper.",
            user_template="Do this: {task} with context: {context}",
        )
        system, user = template.render({"task": "research", "context": "AI agents"})
        assert system == "You are a helper."
        assert "research" in user
        assert "AI agents" in user

    def test_missing_variable_raises(self) -> None:
        """Missing template variables must fail loudly, not silently skip."""
        template = PromptTemplate(
            template_id="test",
            system_prompt="System",
            user_template="Do: {task} with {context}",
        )
        with pytest.raises(ValueError, match="missing variable"):
            template.render({"task": "research"})  # Missing 'context'

    def test_extra_variables_ignored(self) -> None:
        """Extra variables are fine — just ignored."""
        template = PromptTemplate(
            template_id="test",
            system_prompt="System",
            user_template="Do: {task}",
        )
        system, user = template.render({"task": "test", "extra": "ignored"})
        assert "test" in user


class TestTemplateRegistry:
    """Registry manages all available templates."""

    def test_default_templates_loaded(self) -> None:
        registry = TemplateRegistry()
        templates = registry.list_templates()
        assert "task_breakdown" in templates
        assert "summarize_for_memory" in templates
        assert "evaluate_opportunity" in templates
        assert "generate_content" in templates

    def test_get_unknown_template_raises(self) -> None:
        registry = TemplateRegistry()
        with pytest.raises(KeyError, match="Unknown template"):
            registry.get("nonexistent_template")

    def test_register_custom_template(self) -> None:
        registry = TemplateRegistry()
        custom = PromptTemplate(
            template_id="custom_analysis",
            system_prompt="Analyze this.",
            user_template="Analyze: {input}",
        )
        registry.register(custom)
        assert "custom_analysis" in registry.list_templates()
        retrieved = registry.get("custom_analysis")
        assert retrieved.template_id == "custom_analysis"


class TestJSONValidation:
    """JSON validation is the primary anti-hallucination measure."""

    def test_valid_json_passes(self) -> None:
        raw = '{"name": "test", "value": 42}'
        parsed, is_valid, errors = validate_json_response(raw, None)
        assert is_valid
        assert parsed == {"name": "test", "value": 42}
        assert errors == []

    def test_invalid_json_fails(self) -> None:
        raw = "This is not JSON at all"
        parsed, is_valid, errors = validate_json_response(raw, None)
        assert not is_valid
        assert parsed is None
        assert len(errors) > 0

    def test_json_array_rejected(self) -> None:
        """We only accept objects, not arrays."""
        raw = '[1, 2, 3]'
        parsed, is_valid, errors = validate_json_response(raw, None)
        assert not is_valid
        assert "object" in errors[0].lower()

    def test_schema_validation_passes(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name", "age"],
        }
        raw = '{"name": "Agent", "age": 1}'
        parsed, is_valid, errors = validate_json_response(raw, schema)
        assert is_valid
        assert parsed["name"] == "Agent"

    def test_schema_validation_catches_wrong_type(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "count": {"type": "integer"},
            },
            "required": ["count"],
        }
        raw = '{"count": "not a number"}'
        parsed, is_valid, errors = validate_json_response(raw, schema)
        assert not is_valid
        assert len(errors) > 0

    def test_schema_validation_catches_missing_required(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "value": {"type": "integer"},
            },
            "required": ["name", "value"],
        }
        raw = '{"name": "test"}'  # Missing 'value'
        parsed, is_valid, errors = validate_json_response(raw, schema)
        assert not is_valid

    def test_markdown_wrapped_json_extracted(self) -> None:
        """LLMs sometimes wrap JSON in markdown code blocks. We handle that."""
        raw = '```json\n{"result": "success"}\n```'
        parsed, is_valid, errors = validate_json_response(raw, None)
        assert is_valid
        assert parsed["result"] == "success"

    def test_markdown_wrapped_without_language_tag(self) -> None:
        raw = '```\n{"result": "success"}\n```'
        parsed, is_valid, errors = validate_json_response(raw, None)
        assert is_valid

    def test_complex_schema_validation(self) -> None:
        """Test with the task_breakdown schema from default templates."""
        schema = {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step_number": {"type": "integer"},
                            "description": {"type": "string"},
                            "requires_llm": {"type": "boolean"},
                        },
                        "required": ["step_number", "description", "requires_llm"],
                    },
                },
                "total_steps": {"type": "integer"},
            },
            "required": ["steps", "total_steps"],
        }

        valid_response = (
            '{"steps": ['
            '{"step_number": 1, "description": "Research", "requires_llm": true},'
            '{"step_number": 2, "description": "Implement", "requires_llm": false}'
            '], "total_steps": 2}'
        )
        parsed, is_valid, errors = validate_json_response(valid_response, schema)
        assert is_valid
        assert parsed["total_steps"] == 2
        assert len(parsed["steps"]) == 2

    def test_nested_json_with_special_chars(self) -> None:
        """JSON with escaped characters must parse correctly."""
        raw = '{"text": "Hello \\"world\\"", "path": "C:\\\\Users\\\\test"}'
        parsed, is_valid, errors = validate_json_response(raw, None)
        assert is_valid
        assert parsed["text"] == 'Hello "world"'


class TestLLMRequest:
    """LLM requests must be properly structured."""

    def test_default_values_are_safe(self) -> None:
        """Default LLM request values prioritize safety."""
        req = LLMRequest(template_id="task_breakdown")
        assert req.temperature == 0.0  # Deterministic by default
        assert req.require_json is True  # Always JSON
        assert req.retry_on_invalid == 2  # Retry invalid
        assert req.timeout_seconds == 30  # Reasonable timeout
        assert req.max_tokens == 1024  # Bounded output


class TestLLMRouterInit:
    """LLM Router initialization and template access."""

    def test_router_has_templates(self) -> None:
        router = LLMRouter()
        templates = router.templates.list_templates()
        assert len(templates) >= 4

    def test_stats_start_at_zero(self) -> None:
        router = LLMRouter()
        stats = router.get_stats()
        assert stats["total_calls"] == 0
        assert stats["total_errors"] == 0
        assert stats["total_tokens"] == 0
