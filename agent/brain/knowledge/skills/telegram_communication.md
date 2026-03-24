# Telegram komunikácia
_Kategória: skills | Tags: telegram, bot, messaging | Aktualizované: 2026-03-24_

## Bot
- Meno: @b2jk_john_bot
- Komunikujem s Danielom (ID: 6698890771)
- Token je v agent/vault/

## Ako funguje
- `agent/social/telegram_bot.py` — bot interface, polling
- `agent/social/telegram_handler.py` — spracovanie správ
- Správy idú cez message bus → handler → LLM → odpoveď

## Formátovanie
- Telegram Markdown v2 — treba escapovať špeciálne znaky
- Fallback na plain text keď Markdown zlyhá
- Krátke odpovede, slovenčina
