"""Utility functions for agent core."""

from datetime import datetime
import locale


def get_slovak_time() -> str:
    """Return current time formatted in Slovak locale (e.g. '24. marca 2026, 14:35:02')."""
    now = datetime.now()
    # Slovak month names (genitive case, as used in dates)
    slovak_months = {
        1: "januára", 2: "februára", 3: "marca", 4: "apríla",
        5: "mája", 6: "júna", 7: "júla", 8: "augusta",
        9: "septembra", 10: "októbra", 11: "novembra", 12: "decembra",
    }
    month = slovak_months[now.month]
    return f"{now.day}. {month} {now.year}, {now.strftime('%H:%M:%S')}"
