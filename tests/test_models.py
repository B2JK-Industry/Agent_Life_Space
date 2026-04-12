"""
Test scenarios for Model Router.

1. classify_task returns correct task type for different inputs
2. get_model returns correct ModelConfig
3. Programming keywords detected
4. Simple greetings go to Haiku
5. Complexity scoring routes to correct tier
6. Default is Sonnet for unknown
"""

from __future__ import annotations

from agent.core.models import (
    HAIKU,
    OPUS,
    SONNET,
    classify_task,
    get_model,
    list_models,
)


class TestClassifyTask:
    """Task classification routes to correct model."""

    def test_programming_detected(self):
        assert classify_task("naprogramuj nový modul") == "programming"
        assert classify_task("implementuj endpoint") == "programming"
        assert classify_task("oprav bug v routeri") == "programming"

    def test_simple_greeting(self):
        assert classify_task("ahoj") == "simple"
        assert classify_task("čau") == "simple"
        assert classify_task("hello") == "simple"

    def test_simple_response(self):
        assert classify_task("áno") == "simple"
        assert classify_task("ok") == "simple"
        assert classify_task("ďakujem") == "simple"

    def test_short_question_is_factual(self):
        assert classify_task("koľko je hodín?") == "factual"

    def test_general_chat_default(self):
        assert classify_task("povedz mi niečo o webových technológiách a ako fungujú v praxi") == "chat"

    def test_empty_is_chat(self):
        assert classify_task("") == "chat"

    # --- Nové: complexity scoring ---

    def test_url_escalates_to_complex(self):
        """URL v texte → agent musí niečo spracovať → minimálne analysis."""
        result = classify_task("prečítaj si https://github.com/B2JK-Industry/Agent_Life_Space")
        assert result in ("analysis", "programming")

    def test_action_verb_escalates(self):
        """Action verb → agent má niečo urobiť."""
        result = classify_task("zaregistruj sa na moltbook")
        assert result in ("analysis", "programming")

    def test_capability_question(self):
        """'vieš...?' otázka → nie je factual, agent musí premýšľať."""
        result = classify_task("vieš sa registrovať na moltbook?")
        assert result in ("analysis", "programming")

    def test_complex_multi_signal(self):
        """URL + analytical verb → analysis (not programming).
        Reading and analyzing a URL is not a code-generation task."""
        result = classify_task("prečítaj si https://github.com/repo a analyzuj čo tam je")
        assert result == "analysis"

    def test_simple_question_stays_factual(self):
        """Krátka otázka bez complexity → factual."""
        assert classify_task("čo je to Python?") == "factual"


class TestGetModel:
    """Model selection returns correct config."""

    def test_programming_gets_opus(self):
        model = get_model("programming")
        assert model == OPUS
        assert model.max_turns == 15

    def test_chat_gets_sonnet(self):
        model = get_model("chat")
        assert model == SONNET

    def test_simple_gets_haiku(self):
        model = get_model("simple")
        assert model == HAIKU
        assert model.max_turns == 1

    def test_unknown_defaults_to_sonnet(self):
        model = get_model("nonexistent_type")
        assert model == SONNET

    def test_work_queue_gets_sonnet(self):
        model = get_model("work_queue")
        assert model == SONNET


class TestListModels:
    """list_models returns complete mapping."""

    def test_all_tasks_listed(self):
        models = list_models()
        assert "chat" in models
        assert "programming" in models
        assert "simple" in models

    def test_model_ids_valid(self):
        models = list_models()
        for _task, model_id in models.items():
            assert "claude" in model_id
