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
| Dashboard / UI | Not started | Len Telegram + CLI |

## Denná operácia

### Štart
```bash
source .venv/bin/activate
python -m agent
```

### Kontrola stavu
- `/status` — základný stav
- `/health` — CPU, RAM, disk, moduly
- `/usage` — token costs
- `/memory [keyword]` — hľadaj v pamäti

### Monitorovanie
Agent loguje všetko cez `structlog`. Kľúčové log eventy:
- `tool_executed` — tool bol vykonaný
- `policy_blocked` — tool bol blokovaný
- `agent_state_change` — stav sa zmenil
- `learning_feedback` — agent sa niečo naučil
- `approval_proposed` — akcia čaká na schválenie

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

## Troubleshooting

| Problém | Riešenie |
|---------|----------|
| Agent neodpovedá | Skontroluj `/health`, reštartuj |
| Vysoké náklady | Skontroluj `/usage`, pozri routing |
| Zlá odpoveď | Agent sa učí — daj feedback |
| Security incident | Lockdown → logy → investigate |
| Pamäť plná | Spusti memory decay + consolidation |
| Workspace stuck | `cleanup_expired()` alebo manuálny cleanup |

## Čo nerobiť

1. Nedávať agentovi prístup k produkčným databázam
2. Nepovoliť host FS access bez dôvodu (AGENT_SANDBOX_ONLY=0)
3. Nekonfigurovať viac ako 3 aktívne workspaces
4. Nedôverovať inferred faktom bez overenia
5. Nenechať agent schvaľovať vlastné finančné návrhy
