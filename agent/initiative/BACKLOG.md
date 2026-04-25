# ALS Improvement Backlog — 2026-04-25

Ambícia: ALS = lepší ako Claude Code v autonomous + persistent + production patterns.
Realizácia: postupne, každá zmena testovaná z každého uhla.

## P0 — User-visible spam / bugs (HOTOVÉ → DOTIAHNUŤ)

- [x] Marketplace report — len ak `len(fresh) > 0` + dedup 24h per fresh-set hash
- [x] Auto-work submit fail — dedup 24h per `job_id` (retries ticho)
- [ ] Subcontract found — dedup 24h per `(job_id, api_name)`
- [ ] Job accepted — dedup 24h per `job_id`
- [ ] Auto-work fatal — dedup 24h per `job_id`
- [ ] Tests for `NotificationDedup`
- [ ] Tests for cron dedup wire-up (no spam in 10×scan simulation)
- [ ] Deploy + live verify (sledovať Telegram 24h že žiadny spam)

## P1 — Real-estate iteration loop with ALS

- [ ] Audit ALS-written real-estate code (manuálne: scoring, dedup, edge cases)
- [ ] Diskusia s ALS — konkrétne otázky: HEAD check? prvý high-score? denný report obsah?
- [ ] Initiative: "review your own real-estate impl, list known gaps + fix them"
- [ ] Live test: prinútiť scraper spustiť mimo cron, over reálne hits
- [ ] Iterovať dokým: 0 dead URLs, scoring vyzerá zmysluplne, daily report konkrétny

## P1 — Server cleanup initiative

- [ ] Initiative: "audit server filesystem, find dead files, duplicates, leftover initiatives_data, propose cleanup plan"
- [ ] Approval gate: ja schvália plán pred delete
- [ ] Execute cleanup
- [ ] Verify (du -sh /home/b2jk/Agent_Life_Space pred + po)

## P1 — Missing patterns from Claude Code (ccunpacked.dev)

- [ ] **Coordinator Mode** — InitiativeEngine spawn parallel sub-agents v isolated workspaces (existujúci `agent/work/`)
  - Schema: PlannedStep.metadata.coordinator=True → spawn workspace
  - Worker prompt s vlastným plan + collect results
  - 1 lead + N workers pattern
- [ ] **Generator-based driver** — `tick_stream()` ako AsyncGenerator[StepEvent]
  - Yield po každom kroku
  - Telegram streaming updates real-time
  - Pausing mid-tick pri externom signáli
- [ ] **Sleep tool / WakeUp** — agent si môže schedulovať vlastné prebudenia
- [ ] **TaskManager refresh** — externe pridané tasks viditeľné bez restart
  - Periodic poll DB pre nové tasks (každých 60s)
  - Alebo file-watch / signal mechanism
- [ ] **Pause initiative → cancel cron tasks** — engine.pause() musí kanselovať child cron tasks
- [ ] **Auto-Dream upgrade** — namiesto 1 dump per init, structured memdir/ hierarchy:
  ```
  agent/brain/memdir/
    initiatives/<id>/
      lessons.md
      patterns_used.json
      cost_summary.json
    skills/<skill_name>/<date>.md
    daily_summaries/<date>.md
  ```

## P2 — Quality + tooling

- [ ] Add ruff + mypy do `.venv` setup pre full project (nie len realestate)
- [ ] CI gate: `ruff check . && mypy agent/`
- [ ] Test coverage report
- [ ] **VERIFY** krok môže reálne spustiť `pytest` cez subprocess (mimo Claude CLI sandbox)
  - Use `agent/work/` Docker workspace
  - Alebo direct subprocess.run(["pytest", "tests/realestate/"])
- [ ] Per-pattern test fixture library (nie inline v testoch)

## P3 — Better than Claude Code

- [ ] **Persistent memory consolidation** — auto-Dream rozšírené, full memdir/ hierarchy
- [ ] **Autonomous self-improvement** — agent píše PR pre vlastný kód
  - Initiative: "review your last 7 days of action — find anti-patterns — propose code fix as PR"
  - Cron: weekly self-improvement initiative
- [ ] **Multi-tenant initiatives** — šablóny pre rýchle spawn (`/initiative from-template scraper sreality.cz 3+kk Brno 6M`)
- [ ] **A2A pattern library** — agent-to-agent task delegation cez UDS sockets
- [ ] **Learning rate adaptation** — model selection based on past success rate (skill confidence × tier)
- [ ] **Cost prediction** — pred každou initiative spočítaj odhad USD a ukáž majiteľovi

## P4 — Polish (later)

- [ ] Dashboard UI pre initiatives (web)
- [ ] Notification preferences (per-user channel routing)
- [ ] Multi-language patterns (Rust scraper template)

## Verification protocol per fix

Pre každý fix:
1. Unit tests (lokálne, `pytest tests/<module>/ -q`)
2. Lint clean (`ruff check`)
3. Type check (`mypy agent/`)
4. Integration test (mocked dependencies)
5. Live test (kde je možné — scraper hit, telegram send)
6. Deploy + 24h sledovanie (logy, žiadny spam)
7. Iterácia ak edge case nájdený
