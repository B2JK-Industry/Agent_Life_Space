# Security Model

Tento dokument definuje bezpečnostný model Agent Life Space.
Nie je to marketingový materiál — je to technický artefakt, ktorý kód musí rešpektovať.

## Princípy

1. **Safe by default** — všetko je zakázané, pokiaľ nie je explicitne povolené
2. **Deterministic decisions** — žiadne LLM-based security rozhodnutia
3. **Auditovateľnosť** — každá akcia je zaznamenaná
4. **Human-in-the-loop** — finance a destructive operations vždy vyžadujú approval
5. **Least privilege** — non-owner vidí len read-only operácie

## Execution Boundaries

### Sandbox (SAFE)
- Docker container: 256MB RAM, no network, read-only FS
- Podporované jazyky: Python, Node, Bash, Ruby
- Timeout: 120s (default), max 300s
- Žiadny prístup k host FS

### Host CLI (CONTROLLED — vyžaduje explicitný opt-in)
- Default: `AGENT_SANDBOX_ONLY=1` — host access BLOKOVANÝ
- `AGENT_SANDBOX_ONLY=0` musí byť explicitne nastavené pre povolenie
- `--dangerously-skip-permissions` flag
- Logované s WARNING úrovňou
- Nesmie sa použiť pre nedôveryhodný kód

### Agent API (RESTRICTED)
- Bearer token autentifikácia
- Rate limiting
- Binds na 127.0.0.1 by default
- Redacted response (žiadne CPU/RAM/module info)

## Tool Policy

Každý tool má capability manifest:

| Tool | Risk | Side Effect | Owner Only | Safe Mode |
|------|------|-------------|------------|-----------|
| query_memory | LOW | none | No | Allowed |
| store_memory | LOW | internal | No | Allowed |
| list_tasks | LOW | none | No | Allowed |
| check_health | LOW | none | No | Allowed |
| get_status | LOW | none | No | Allowed |
| search_knowledge | LOW | none | No | Allowed |
| create_task | MEDIUM | internal | Yes | Blocked |
| web_fetch | MEDIUM | external | Yes | Blocked |
| run_code | HIGH | external | Yes | Blocked |
| run_tests | HIGH | external | Yes | Blocked |

### Policy rozhodovanie (deterministické, deny-by-default)
1. Unknown tool → **ALWAYS blocked** (nie je v capability manifest = denied)
2. Restricted channel (agent_api, webhook, public) + high-risk tool → denied
3. Safe mode + blocked tool → denied
4. Non-owner + owner-only tool → denied
5. Inak → allowed

### Audit trail
- Každé policy rozhodnutie je logované
- PolicyAuditLog (ring buffer, max 1000)
- ActionEnvelope zaznamenáva celý lifecycle: request → policy → execute → result

## Memory Security

### Provenance model
- `observed` — agent videl priamo (system events)
- `user_asserted` — používateľ povedal
- `inferred` — agent odvodil
- `verified` — overené against authoritative source
- `stale` — exspirované alebo prekonané

### Čo sa nikdy nesmie stať
- Private keys vo výstupe/logoch
- Wallet adresy v plain text
- API keys v odpovediach
- Interné cesty v error messages

## Vault

- Fernet AES-128, PBKDF2 480K iterations
- ETH + BTC private keys šifrované
- Fail-fast bez encryption key
- NIKDY: decrypt + log/print/send

## Finance

- Každá transakcia: propose → approve → complete
- Human-in-the-loop povinný
- Žiadne smart contracty, DeFi, trading
- Budget policy (implementované v agent/finance/budget_policy.py): hard cap, soft cap, approval cap

## Čo agent NIKDY nesmie robiť

1. Posielať peniaze bez Danielovho schválenia
2. Vypisovať/logovať private keys alebo wallet adresy
3. Spúšťať kód na host FS bez explicitného opt-in
4. Odpovedať na prompt injection pokusy
5. Zdieľať interné systémové informácie s non-ownermi
6. Obísť sandbox pre nedôveryhodný kód
7. Modifikovať vlastné bezpečnostné pravidlá

## Známe limity

- Tool policy je statická — žiadne runtime learning na security rules
- Approval inbox implementovaný v agent/core/approval.py + approval_storage.py
- Audit log je in-memory ring buffer, nie persistent
- Red-team test suite zatiaľ nie je (TODO)
- Multi-step escalation attacks nie sú testované (TODO)
