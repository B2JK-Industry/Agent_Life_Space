# Operator Handbook

Praktický sprievodca pre vlastníka Agent Life Space.

## Čo agent vie robiť

| Schopnosť | Stav | Poznámka |
|-----------|------|----------|
| Odpovedať na otázky (SK/EN) | Stable | 9-layer cascade |
| Spúšťať kód v sandboxe | Stable | Docker, 256MB, no network |
| Pamätať si fakty | Stable | Provenance model, FTS5 |
| Spravovať úlohy | Stable | Create/list/complete |
| Sťahovať web stránky | Stable | Rate limited |
| Komunikovať s inými agentmi | Stable | API + replay protection |
| Navrhovať výdavky | Stable | Human-in-the-loop |
| Učiť sa z výsledkov | Partial | Skill tracking, model escalation |
| Dashboard / UI | Partial | API-key protected operator dashboard exists for jobs, settlements, retention, and audit |

## Denná operácia

### Štart
```bash
source .venv/bin/activate
python -m agent --setup-doctor
python -m agent
```

Odporúčané pre self-host:
- nastav `AGENT_PROJECT_ROOT`
- nastav dedikovaný `AGENT_DATA_DIR` mimo source tree, napr. `.agent_runtime`
- nastav `AGENT_API_KEY`, ak chceš dashboard a operator API
- skontroluj warnings zo `--setup-doctor` ešte pred prvým systemd deployom

### Kontrola stavu
- `/status` — základný stav
- `/health` — CPU, RAM, disk, moduly
- `/usage` — token costs
- `/memory [keyword]` — hľadaj v pamäti
- `python -m agent --setup-doctor` — self-host config audit
- `python -m agent --report` — operator inbox / settlement attention
- `python -m agent --llm-runtime-status` — aktuálny runtime LLM attach/backend/provider stav
- dashboard: `http://127.0.0.1:8420/dashboard` (autentifikácia cez `Authorization: Bearer $AGENT_API_KEY` — od v1.35.0 už `?key=` query string nie je podporovaný)

### Telegram + Claude CLI backend obmedzenie
Programovacie úlohy poslané z Telegramu **nemôžu** použiť Claude CLI backend v default sandbox móde. Claude Code CLI vyžaduje interaktívne kliknutie "Allow" na permission prompt, čo z Telegramu nie je dosiahnuteľné — request by visel v typing indicator-i kým ho timeout neukončí.

Brain má fail-closed guard ktorý odmietne túto kombináciu hneď po klasifikácii tasku a vráti operator-friendly hlášku. Conversational tasky (otázky, status, memory) na CLI backend-e fungujú normálne — guard sa týka len `programming` tasku.

**Možnosti odblokovania (pre programovacie tasky cez Telegram):**

| Voľba | Ako | Bezpečnosť |
|---|---|---|
| Prepnúť na API backend | `/runtime` v Telegrame alebo `POST /api/operator/llm` | ✅ ToolUseLoop nevyžaduje interaktívny prompt |
| Host opt-in | `AGENT_SANDBOX_ONLY=0` v `.env` na servery + reštart | ⚠️ CLI dostane host file access cez `--dangerously-skip-permissions` — rob len ak vieš čo robíš |

V API móde sa vyžaduje nakonfigurovaný `ANTHROPIC_API_KEY` (alebo `OPENAI_API_KEY`/`OPENAI_BASE_URL`).

### Runtime LLM ovládanie
Ak chceš dočasne odpojiť LLM, alebo prepnúť medzi `cli` a `api` bez editovania `.env`, použi runtime override. Stav sa ukladá do `AGENT_DATA_DIR/control/llm_runtime.json`.

CLI:
```bash
python -m agent --llm-runtime-disable --llm-runtime-note "maintenance"
python -m agent --llm-runtime-enable --llm-runtime-backend cli --llm-runtime-note "back to Claude CLI"
python -m agent --llm-runtime-enable --llm-runtime-backend api --llm-runtime-provider anthropic
python -m agent --llm-runtime-enable --llm-runtime-backend api --llm-runtime-provider openai
python -m agent --llm-runtime-follow-env --llm-runtime-enable
```

API:
- `GET /api/operator/llm`
- `POST /api/operator/llm`

Dashboard:
- panel `LLM Runtime` na `/dashboard`

### Monitorovanie
Agent loguje všetko cez `structlog`. Kľúčové log eventy:
- `tool_executed` — tool bol vykonaný
- `policy_blocked` — tool bol blokovaný
- `agent_state_change` — stav sa zmenil
- `learning_feedback` — agent sa niečo naučil
- `approval_proposed` — akcia čaká na schválenie
- `telegram_cli_programming_denied` — fail-closed guard zachytil neuskutočniteľnú kombináciu
- `vault_migrated_to_v2_single_file_format` — automatická migrácia legacy vault na v2

### Tiered logging
Od v1.35.0 agent píše do **dvoch súbežných sinkov** s rozdielnymi retention oknami:

| Tier | Default retention | Súbor | Čo obsahuje |
|---|---|---|---|
| **long** | 720 hodín (~30 dní) | `<AGENT_LOG_DIR>/long/agent-long.log` | lifecycle, build, finance, audit, security, vault, ERROR/CRITICAL/AUDIT events |
| **short** | 6 hodín | `<AGENT_LOG_DIR>/short/agent-short.log` | verbose pipeline diagnostics — brain pipeline stages, semantic cache hits, telegram polling, typing indicators |

Tier router je deterministický: ERROR/CRITICAL/AUDIT vždy long, ostatné podľa event-name prefixov v `agent/logs/retention.py::_LONG_TIER_EVENTS` a `_SHORT_TIER_EVENTS`. Cron loop volá `LogRetentionManager.prune_all()` každú hodinu a maže súbory staršie než tier window.

**Konfigurácia (env vars):**
```bash
AGENT_LOG_DIR=/path/to/logs              # default: <AGENT_DATA_DIR>/logs
AGENT_LOG_LONG_RETENTION_HOURS=720       # default: 720h = 30 dní
AGENT_LOG_SHORT_RETENTION_HOURS=6        # default: 6h
AGENT_LOG_TIERED=1                       # default: 1 (zapnuté)
```

> ⚠️ **Deprecated:** `AGENT_LOG_LONG_RETENTION_DAYS` ešte funguje, ale od v1.35.0 emit-uje deprecation warning a interne sa promote-uje na hodiny. Prejdi na `AGENT_LOG_LONG_RETENTION_HOURS` (napr. `30 dní × 24 = 720`).

## Bezpečnostné operácie

### Lockdown (incident response)
Ak niečo vyzerá zle:
1. OperatorControls.lockdown() — vypne všetky externé tooly
2. Skontroluj logy
3. OperatorControls.unlock() keď je jasné

### Čo agent NIKDY nerobí bez schválenia
- Posielanie peňazí
- Prístup k host filesystému (default blocked)
- Smart contracty, DeFi, trading

### Kto vidí čo
| Kontext | Vidí |
|---------|------|
| Owner (private chat) | Všetko — wallet, health, budget |
| Non-owner (group) | Len verejné odpovede |
| Agent API | Len safe responses |

## Pamäť

### Provenance stavy
| Stav | Význam | Príklad |
|------|--------|---------|
| observed | Agent videl priamo | "Test prešiel" |
| user_asserted | Používateľ povedal | "Server má 16GB RAM" |
| inferred | Agent odvodil | "owner preferuje krátke odpovede" |
| verified | Overené | "Python 3.12 nainštalovaný" (po 5+ prístupoch) |
| stale | Zastarané | "Server IP: 10.0.1.100" (neprístupné 30 dní) |

### Ako skontrolovať čo agent vie
```python
from agent.memory.inspection import MemoryInspector
inspector = MemoryInspector(store)
inspector.get_overview()           # Celkový prehľad
inspector.get_verified_facts()     # Overené fakty
inspector.get_stale_report()       # Zastarané informácie
inspector.get_conflict_report(["server"])  # Konfliktné fakty
```

## Finance

### Budget limity (default)
| Limit | Suma | Typ |
|-------|------|-----|
| Denný soft cap | $30 | Warning |
| Denný hard cap | $50 | Blokuje |
| Mesačný soft cap | $300 | Warning |
| Mesačný hard cap | $500 | Blokuje |
| Jedná transakcia | $20 | Extra approval |

### Approval flow
1. Agent navrhne výdavok → `propose_expense()`
2. Approval request sa vytvorí v queue
3. Owner schváli/zamietne cez Telegram alebo API
4. Schválená transakcia sa dokončí

### Settlement flow
1. Gateway vráti `402 payment required`
2. ALS vytvorí persisted settlement request
3. Operator ho vidí v `/settlement`, dashboarde, a v operator reporte
4. Owner môže `approve`, `deny`, alebo `execute`
5. Úspešný top-up môže automaticky retry-nuť pôvodný API call

## Troubleshooting

| Problém | Riešenie |
|---------|----------|
| Agent neodpovedá | Skontroluj `/health`, reštartuj |
| Vysoké náklady | Skontroluj `/usage`, pozri routing |
| Zlá odpoveď | Agent sa učí — daj feedback |
| Security incident | Lockdown → logy → investigate |
| Pamäť plná | Spusti memory decay + consolidation |
| Workspace stuck | `cleanup_expired()` alebo manuálny cleanup |
| Dashboard sa nepripojí | Skontroluj `AGENT_API_KEY`, otvor `/dashboard?key=...`, pozri `python -m agent --setup-doctor` |
| Runtime píše dáta do repo | Nastav `AGENT_DATA_DIR` na dedikovaný adresár mimo `./agent` |

## Čo nerobiť

1. Nedávať agentovi prístup k produkčným databázam
2. Nepovoliť host FS access bez dôvodu (AGENT_SANDBOX_ONLY=0)
3. Nekonfigurovať viac ako 3 aktívne workspaces
4. Nedôverovať inferred faktom bez overenia
5. Nenechať agent schvaľovať vlastné finančné návrhy
