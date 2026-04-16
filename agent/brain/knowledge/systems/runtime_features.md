# Runtime Features — Čo beží na pozadí
_Kategória: systems | Tags: cron, runtime, background | Aktualizované: 2026-03-25_

## Cron joby (7 aktívnych)
Všetky bežia — over cez `/runtime`.

1. **Health check** (1h) — CPU, RAM, alerty → Telegram ak problém
2. **Memory maintenance** (6h) — decay, cleanup starých spomienok
3. **Morning report** (8:00 UTC) — ranný stav pre ownera
4. **Task review** (4h) — kontrola fronty úloh
5. **Server maintenance** (3h) — cache cleanup, stale procesy
6. **Memory consolidation** (2h) — episodic → semantic/procedural
7. **Dead man switch** (12h) — kontrola stale proposals

## Dead Man Switch
Finance proposals nemôžu čakať navždy:
- 3 dni → warning (pripomienka cez Telegram)
- 7 dní → escalation (urgentná notifikácia)
- 14 dní → auto-cancel

## Obolos Marketplace (aktívne)
John má plnú marketplace integráciu (worker + client role):
- **Cron scan** (6h) — auto-evaluate listings, auto-bid, Telegram alert
- **Konverzačný search** — "nájdi mi prácu" → live listings (WORK_SEARCH intent)
- **Bid workflow** — /marketplace bid → /yes → submit (approval-gated)
- **Job polling** — detekcia nových accepted jobov → Telegram alert
- **Client mode** — /marketplace create-listing (FINANCE approval)

DÔLEŽITÉ: Marketplace commands (/marketplace, /yes, /no) fungujú aj v sandbox mode.
Sú to deterministic Telegram handlery, NEpoužívajú LLM tool use.
Ak sa ťa niekto opýta "vieš nájsť prácu" — odpoveď je ÁNO, použi /marketplace listings
alebo sa opýtaj prirodzene ("nájdi mi prácu").

## Tool Pre-routing
Pred CLI callom sa automaticky fetchnú externé dáta:
- Počasie → wttr.in (zadarmo, slovenské mestá)
- Krypto ceny → CoinGecko
- Dátum/čas → vždy injektovaný

## Agent-to-Agent API
- Port 8420, HTTP endpoint
- POST /api/message — správa od iného agenta
- GET /api/status, /api/health — verejné
- Auth: Bearer token (AGENT_API_KEY)
- Cloudflare tunnel pre verejný prístup

## Response Quality Detector
Po odpovedi z Haiku sa vyhodnotí kvalita:
- Signály: refusal, generic, error, echo, too_short
- Ak score < 0.5 → auto-eskalácia na Sonnet

## Persistent Conversation
Správy sa ukladajú do SQLite (conversations.db):
- Core memory (fakty), rolling summary, recent messages
- Prežije reštart — agent si pamätá o čom sa bavil
