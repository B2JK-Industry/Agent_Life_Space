"""Tests for agent.core.utils."""

import re

from agent.core.utils import get_slovak_time


def test_get_slovak_time_format():
    result = get_slovak_time()
    # Should match pattern like "24. marca 2026, 14:35:02"
    pattern = r"^\d{1,2}\. \w+ \d{4}, \d{2}:\d{2}:\d{2}$"
    assert re.match(pattern, result), f"Unexpected format: {result}"


def test_get_slovak_time_contains_slovak_month():
    result = get_slovak_time()
    slovak_months = [
        "januára", "februára", "marca", "apríla", "mája", "júna",
        "júla", "augusta", "septembra", "októbra", "novembra", "decembra",
    ]
    assert any(m in result for m in slovak_months), f"No Slovak month found in: {result}"
