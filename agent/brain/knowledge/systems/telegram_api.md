# Telegram Bot API
_Kategória: systems | Tags: telegram, api, bot | Aktualizované: 2026-03-24_

## Bot
- Username: @b2jk_john_bot
- Token: v vault
- Metóda: long polling

## Dôležité API endpointy
- `getUpdates` — polling pre nové správy
- `sendMessage` — odoslanie správy
- `editMessageText` — úprava existujúcej správy

## Markdown v2
- Špeciálne znaky treba escapovať: `_*[]()~>#+-=|{}.!`
- Fallback na plain text keď parsovanie zlyhá
- Maximálna dĺžka správy: 4096 znakov
