# Telegram Markdown V2 escapovanie
_Kategória: learned | Tags: telegram, markdown, bugfix | Aktualizované: 2026-03-24_

## Problém
Telegram MarkdownV2 vyžaduje escapovanie špeciálnych znakov. Keď sa neescapujú, API vráti parse error.

## Riešenie
- Escapovať tieto znaky: `_*[]()~>#+-=|{}.!`
- Implementovaný fallback: keď Markdown zlyhá, pošle sa plain text
- Commit: "Fix: Telegram Markdown fallback, prevent parse errors"

## Poučenie
Vždy mať fallback na plain text pri Telegram správach.
