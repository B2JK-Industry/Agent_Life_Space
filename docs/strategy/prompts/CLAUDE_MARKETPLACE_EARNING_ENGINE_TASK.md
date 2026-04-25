# Claude Code Task: Marketplace Earning Engine Foundation

Pracuješ v repo `Agent_Life_Space` na aktuálnom worktree.

Najprv si načítaj:
- `docs/strategy/MASTER_SOURCE_OF_TRUTH.md`
- `docs/strategy/MARKETPLACE_EARNING_ENGINE_BACKLOG.md`
- `docs/CONTROLLED_ENVIRONMENTS.md`

## Cieľ

Začni budovať generický marketplace earning engine pre Johna cez Telegram nad ALS.

Veľmi dôležité:
- nejde o `obolos-only hack`
- ide o multi-platform-ready foundation
- `obolos.tech` je prvý connector

Nechcem broad refactor celej architektúry.
Chcem prvý praktický slice, ktorý dá Johnovi reálny smer k získavaniu zákaziek.

## Tento task je len Phase 1

Implementuj len:
1. marketplace domain foundation
2. connector abstraction
3. prvý read-only `ObolosConnector`
4. Telegram surfaces pre browse/detail/qualify
5. basic project linkage pre zaujímavé opportunities

Zatiaľ NEimplementuj:
- auto bid submit
- negotiation automation
- auto delivery
- auto payout
- unrestricted wallet movement
- multi-platform compare UX

## Grounded current state

ALS už má:
- gateway model
- `obolos.tech` provider catalog
- marketplace catalog/API call routes
- wallet balance / wallet top-up foundation
- approval queue
- Telegram operator surface
- projects, jobs, workflows, cost ledger

ALS ešte nemá:
- generic marketplace job domain
- marketplace connector abstraction
- John-facing `/market ...` surface
- job qualification engine
- bid draft flow

## Implementačný scope

### 1. Marketplace domain foundation

Pridaj malý bounded context, napríklad:

```text
agent/marketplace/
  models.py
  connectors.py
  registry.py
  service.py
```

Minimum models:
- `MarketplaceJob`
- `MarketplaceJobDetail`
- `QualificationResult`
- `BidDraft`

Minimum connector interface:
- `list_jobs(...)`
- `get_job(...)`
- `qualify_job(...)`

Ak chceš pridať draft bid model už teraz, môžeš, ale submit nech ostane mimo scope.

### 2. Obolos read-only connector

Vytvor `ObolosConnector` nad existujúcou gateway foundation.

Nech vie:
- získať job/opportunity listing cez existujúce marketplace API capability
- získať detail jednej položky
- namapovať to na normalized marketplace model

Ak dokumentované Obolos route ešte nedajú presný "jobs board" endpoint:
- urob conservative adapter boundary
- implementuj to cez aktuálne marketplace/catalog shape
- jasne pomenuj residual risk

Nepíš fake marketing.

### 3. Marketplace service

Pridaj service layer, ktorý:
- používa registry connectorov
- vracia normalized jobs
- robí jednoduchú qualification analýzu

Qualification v tejto fáze môže byť deterministic/lightweight:
- vie ALS build/review/research?
- je to obvious mismatch?
- potrebuje approval?
- aké capability chýbajú?

Netreba veľký scoring engine, ale odpoveď musí byť grounded.

### 4. Telegram operator surface

Pridaj deterministic commands:
- `/market connectors`
- `/market jobs --platform obolos`
- `/market job obolos <id>`
- `/market qualify obolos <id>`

Ak je syntax treba zjednodušiť, OK, ale nech je konzistentná.

Požadované správanie:
- browse jobs
- zobraziť detail
- dať grounded qualification

### 5. Optional project linkage

Ak má job dobrú kvalifikáciu, umožni:
- založiť project record z opportunity
alebo
- aspoň pripraviť jasný next step do `/projects`

Stačí practical MVP.

## Design constraints

- minimal diffs
- preserve ALS architecture
- deterministic-first
- connector abstraction first
- no obolos-only hardcoded core logic
- no broad brain refactor
- no payout automation
- no auto-bid submit in this phase

## Qualification behavior

Qualification reply má rozlišovať:
- executable now
- possible with supervision
- missing capability
- unsafe / should not bid

A má uviesť:
- why
- next recommended action

## Testy, ktoré chcem

Minimálne:

1. marketplace models
- serialization / basic invariants

2. connector registry
- vie zaregistrovať a resolve-nuť `obolos`

3. Obolos connector
- list jobs mapping z fixture/provider mocku
- job detail mapping z fixture/provider mocku

4. marketplace service
- returns normalized jobs
- qualification is deterministic and grounded

5. Telegram commands
- `/market connectors`
- `/market jobs --platform obolos`
- `/market job obolos <id>`
- `/market qualify obolos <id>`

6. Regression
- existing gateway tests stay green
- existing Telegram operator commands sa nerozbijú
- existing project/workflow/build/review surfaces sa nerozbijú

Ak nie je vhodné volať live provider v testoch:
- použi fixtures alebo gateway mocks

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
3. čo Phase 1 teraz vie
4. ako funguje `ObolosConnector`
5. aké `/market` commandy pribudli
6. čo qualification vie a čo ešte nie
7. aké testy si spustil a výsledky
8. residual risks
9. odporúčaný Phase 2 next step

## Dôležitý focus

MVP úspech tejto fázy nie je "agent si už zarába sám".

MVP úspech tejto fázy je:
- John vie nájsť externé opportunities
- vie ich grounded posúdiť
- vie ich premeniť na ďalší akčný krok
- architektúra je pripravená na bid/delivery/payout bez obolos-only chaosu
