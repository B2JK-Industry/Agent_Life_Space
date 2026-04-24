# Pattern: NOTIFIER

**Kedy použiť:** Iniciatíva vyžaduje doručenie informácie majiteľovi alebo tretej strane v reakcii na udalosť (nový záznam, chyba, milník, threshold breach).

## Kanály

- **Telegram** (default) — cez `agent.social.telegram_handler.TelegramBot.send_message(chat_id, text, parse_mode='Markdown')`
- **Email** — cez `agent.social.email` (SMTP, ak je nakonfigurovaný)
- **Webhook** — POST JSON na zadaný URL (auth cez HMAC)
- **API agent-to-agent** — `POST /api/notify` na peer-agent (Bearer token)

## Princípy

1. **Idempotencia** — každá notifikácia má `notification_id` (hash payload + recipient + bucket-time). Duplikát sa zahodí.
2. **Throttling** — rovnaký typ správy môže prísť max N×/hod (default 6). Override len v emergency=true.
3. **Šablóny** — text sa renderuje cez `string.Template` z payloadu, nikdy direct interpolation s user-controlled stringmi.
4. **Severity** — INFO / WARN / ALERT — určuje formátovanie (emoji prefix) a kanál override (ALERT vždy aj na email).
5. **Audit** — každá poslaná notifikácia sa zapíše do `notifications.db` (recipient, channel, severity, payload_hash, sent_at, delivery_status).

## Šablóny v knowledge

- `notification:new_listing` — "🏠 *Nová ponuka*: {{title}} — {{price}} Kč, {{location}}\n{{url}}"
- `notification:scraper_failure` — "⚠️ Scraper *{{initiative_name}}* zlyhal: {{error}}\nPauznutý do manuálneho restartu."
- `notification:milestone` — "✅ *{{initiative_name}}* milestone: {{milestone_title}}"
- `notification:approval_required` — "🔐 *Akcia vyžaduje schválenie*: {{action}}\nDôvod: {{rationale}}\nOdpovedz `/yes {{approval_id}}` alebo `/no {{approval_id}}`."

## Acceptance criteria

- [ ] Doručenie cez aspoň jeden funkčný kanál
- [ ] Zápis do `notifications.db`
- [ ] Žiadny duplikát do 1 minúty pre identický payload
- [ ] Šablóna nepúšťa raw user input (XSS-like injection v Markdown)
- [ ] Throttling testovaný (7. správa za hodinu zahodená)
