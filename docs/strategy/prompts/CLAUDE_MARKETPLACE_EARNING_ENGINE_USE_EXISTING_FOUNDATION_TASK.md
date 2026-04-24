# Claude Code Task: Marketplace Earning Engine Using Existing ALS Foundation

Pracuješ v repo `Agent_Life_Space` na aktuálnom worktree.

Najprv si načítaj:
- `docs/strategy/MASTER_SOURCE_OF_TRUTH.md`
- `docs/strategy/MARKETPLACE_EARNING_ENGINE_BACKLOG.md`
- `docs/CONTROLLED_ENVIRONMENTS.md`
- `agent/control/policy.py`
- `agent/control/gateway.py`
- `agent/control/settlement.py`
- `agent/core/agent.py`
- `agent/social/telegram_handler.py`

## Dôležitý rámec

Veľa foundation už v ALS existuje.

Nechcem greenfield návrh.
Nechcem, aby si znovu staval veci, ktoré už máme.
Chcem, aby si maximálne využil existujúci ALS runtime, control-plane, gateway,
approval, project, workflow, delivery, settlement a Telegram surfaces.

`obolos.tech` je prvý marketplace connector, ale architektúra musí zostať
rozšíriteľná aj pre ďalšie platformy.

## Čo už ALS má a čo MUSÍŠ reuse-nuť

Toto nepíšem ako hypotézu. Toto už v projekte existuje a má sa to využiť:

### Gateway / provider foundation
- explicitný provider model pre `obolos.tech`
- capability routes pre:
  - `marketplace_catalog_v1`
  - `marketplace_api_call_v1`
  - `seller_publish_v1`
  - `wallet_balance_v1`
  - `wallet_topup_v1`
  - handoff/delivery routes

### Safety / money / control-plane
- approval queue
- x402-aware settlement workflow
- persisted control-plane jobs, traces, deliveries, cost ledger
- runtime budget / finance posture

### Product execution
- build jobs
- review jobs
- delivery bundles / gateway send
- projects + project/job linkage
- recurring workflows

### Operator surfaces
- Telegram operator commands
- Agent API
- dashboard/reporting surfaces

## Preto:

NEIMPLEMENTUJ znovu:
- nový generic approval system
- nový wallet layer
- nový delivery system
- nový job execution engine
- nový project system
- nový workflow system
- nový gateway abstraction

Namiesto toho doplň len to, čo ešte chýba na marketplace earning flow.

## Cieľ tejto fázy

Postav prvý použiteľný marketplace-worker slice pre Johna cez Telegram:

1. browse opportunities
2. inspect one opportunity
3. grounded qualification
4. create bid draft
5. prepojiť opportunity na project

Zatiaľ bez auto-submit a bez auto-payout.

## Scope tejto fázy

Implementuj len tieto missing slices:

### 1. Marketplace domain layer

Pridaj malý bounded context napríklad:

```text
agent/marketplace/
  models.py
  registry.py
  service.py
  obolos.py
```

Použi minimum nových modelov:
- `MarketplaceJob`
- `MarketplaceJobDetail`
- `QualificationResult`
- `BidDraft`

Nenavrhuj veľký framework.
Len to, čo treba na browse/detail/qualify/bid-draft.

### 2. Obolos connector over existing gateway

Neobchádzaj gateway.

`ObolosConnector` musí reuse-nuť existujúce capability routes:
- `marketplace_catalog_v1`
- `marketplace_api_call_v1`

Ak treba detail podľa slug/resource, používaj existujúce gateway API-call path.

Ak z dokumentovaných route ešte nevyplýva presný “job board” endpoint:
- použij current marketplace/catalog semantics
- normalizuj výsledok na `MarketplaceJob`
- jasne pomenuj residual risk

### 3. Qualification over current ALS capabilities

Qualification nemá byť halucinácia.

Má reuse-núť to, čo ALS už má:
- build capability
- review capability
- delivery capability
- approvals
- project tracking
- cost/budget awareness

Výstup kvalifikácie má rozlišovať:
- executable now
- possible with supervision
- missing capability
- unsafe / should not bid

Minimum fields:
- verdict
- reasons
- missing capabilities
- recommended next step
- approval needed yes/no

### 4. Bid draft

Neimplementuj submit.

Sprav len deterministic bid draft:
- stručné predstavenie capability
- navrhovaný scope
- assumptions
- risk notes
- why ALS/John can do it

Ak chceš použiť lacné LLM polish, môžeš, ale default nech je deterministic-first.

### 5. Telegram operator surface

Pridaj commandy:
- `/market connectors`
- `/market jobs --platform obolos`
- `/market job obolos <id>`
- `/market qualify obolos <id>`
- `/market bid-draft obolos <id>`
- voliteľne `/market project obolos <id>` na create/link project recordu

Nech sú commandy konzistentné s existujúcim operator štýlom.

### 6. Project linkage

Opportunity, ktorá dáva zmysel, sa má dať premeniť na project.

Reuse existujúci `ProjectManager`.

Stačí practical MVP:
- create project from opportunity
- názov
- stručný scope
- platform metadata
- linked remote job id
- next step

## Čo v tejto fáze neriešiť

Nerob:
- bid submit
- negotiation automation
- auto-accept
- auto-delivery
- payout / withdrawal / forwarding
- whitelist wallet policy implementation
- multi-platform compare UX

To príde až v ďalšej fáze.

## Definition of Done pre túto fázu

Táto fáza je hotová, keď John vie cez Telegram:
- vypísať dostupné Obolos opportunities
- zobraziť detail jednej opportunity
- povedať, či ALS vie job zvládnuť
- vygenerovať bid draft
- založiť k tomu project record

A všetko je grounded v existujúcich ALS surfaces.

## Testy, ktoré chcem

Minimálne:

1. Marketplace models
- basic invariants / serialization

2. Connector registry
- resolve `obolos`

3. Obolos connector
- list opportunities mapping cez gateway mock
- detail mapping cez gateway mock

4. Qualification
- executable-now example
- supervision-needed example
- missing-capability example
- provider sa zbytočne nevolá pre qualification, ak už detail máme

5. Bid draft
- deterministic structured output

6. Telegram commands
- `/market connectors`
- `/market jobs --platform obolos`
- `/market job obolos <id>`
- `/market qualify obolos <id>`
- `/market bid-draft obolos <id>`

7. Project linkage
- opportunity -> project record
- metadata persists

8. Regression
- existing gateway tests stay green
- existing `/projects`, `/workflow`, `/jobs`, `/deliver`, `/settlement` sa nerozbijú

## Validácia

Spusti minimálne:
- `python3 -m pytest tests/test_gateway.py tests/test_telegram_operator.py tests/test_brain_core.py -q --tb=short`

A doplň nové relevantné testy pre marketplace slice.

Potom podľa potreby:
- `python3 -m pytest tests/ -q --tb=short`

Ak sú dostupné:
- `.venv/bin/python -m ruff check agent tests`
- `.venv/bin/python -m mypy agent --ignore-missing-imports --no-error-summary`

## Výstup

Na konci chcem:
1. stručné zhrnutie
2. changed files
3. ktoré existujúce ALS capabilities si reuse-nul
4. čo teraz John vie robiť cez `/market`
5. ako funguje qualification
6. ako funguje bid draft
7. ako sa opportunity mení na project
8. aké testy si spustil a výsledky
9. residual risks
10. odporúčaný ďalší krok

## Dôležitý mindset

Nesprávny prístup:
- "ideme stavať marketplace systém od nuly"

Správny prístup:
- "ALS už má gateway, approvals, projects, jobs, settlement, delivery, Telegram surfaces"
- "dopĺňame len marketplace domain a John-facing operator flow"
