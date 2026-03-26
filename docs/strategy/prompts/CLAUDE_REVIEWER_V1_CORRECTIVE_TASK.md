# Claude Code Task: Reviewer V1 Corrective Pass

Pracuješ v repozitári:

`/Users/danielbabjak/Desktop/ANP/Agent_Life_Space`

Aktuálny kontext:
- Reviewer vertical slice v1 bol implementovaný v PR `#44`
- Auditovaný commit: `8537f26`
- Slice je reálny a dobrý základ, ale ešte nie je produktovo uzavretý

Toto nie je nový feature-sprawl task.
Je to corrective pass, ktorý má reviewer slice dotiahnuť bližšie k hotovému
`Reviewer v1`.

## Čo je už dobré

Reviewer bounded context už existuje:
- `agent/review/models.py`
- `agent/review/analyzers.py`
- `agent/review/service.py`
- `agent/review/storage.py`
- `agent/review/verifier.py`

Full suite na auditovanom PR stave prešla:
- `1123 passed, 4 skipped`

## Čo ešte nie je dotiahnuté

### 1. Artifact recovery je stále príliš plytký

Problém:
- `ReviewJob.to_dict()` neukladá celý intake payload
- storage vracia skôr job/artifact metadata než plne recoverable payloads
- delivery foundation je tým pádom slabšia, než sa môže zdať

Chcem:
- persistovať celý `ReviewIntake`
- persistovať plný artifact payload pre Markdown aj JSON exporty
- vedieť znovu načítať review job tak, aby bol použiteľný pre recovery,
  delivery a audit, nielen pre listovanie

Files:
- `agent/review/models.py`
- `agent/review/storage.py`
- `agent/review/service.py`
- testy v `tests/test_review_domain.py` alebo nové separátne review storage tests

Acceptance:
- job reload obsahuje intake
- artifact reload vie vrátiť reálny obsah, nie len metadata
- recovery testy pokrývajú report + findings + trace + intake

### 2. Reviewer flow ešte nie je naozaj workspace-bound

Problém:
- `ReviewJob` má `workspace_id`, ale reviewer flow workspace reálne nepoužíva
- v review v1 sa analyzuje repo priamo podľa path
- workspace discipline preto zatiaľ nie je uzavretá

Chcem:
- buď reviewer flow reálne previazať s `WorkspaceManager`
- alebo zaviesť poctivý dvojrežimový model:
  - read-only review path bez mutable workspace
  - mutable review/build path s workspace

Preferencia:
- read-only review môže zostať read-only, ale musí to byť explicitný,
  auditovaný a architektonicky čistý režim
- nenechávaj `workspace_id` ako prázdne placebo pole

Files:
- `agent/review/service.py`
- `agent/review/models.py`
- `agent/work/workspace.py`
- prípadne shared execution helper modul

Acceptance:
- je jasné, kedy review používa workspace a kedy nie
- model a trace to explicitne nesú
- testy pokrývajú toto rozhodovanie

### 3. Telegram/API review entrypoints stále nejdú cez nový ReviewService

Problém:
- reviewer bounded context existuje
- ale `/review` path v Telegram handleri stále ide cez legacy `Programmer.review_file()`
- to udržiava starý produktový drift

Chcem:
- review entrypointy smerovať do nového `ReviewService`, alebo
- ak nie je možné kompletne migrovať všetko v jednom kole, tak:
  - aspoň zaviesť nový explicitný reviewer command/path
  - starý path označiť ako legacy
  - a zabrániť ďalšiemu driftu

Dôležité:
- nechcem, aby social/channel vrstva bola centrom reviewer business logiky

Files:
- `agent/social/telegram_handler.py`
- prípadne `agent/social/agent_api.py`
- `agent/core/agent.py`
- `agent/review/service.py`

Acceptance:
- existuje aspoň jeden reálny adapter path do `ReviewService`
- reviewer bounded context sa používa z runtime entrypointu
- test pokrýva adapter → service path

### 4. Reviewer execution path ešte nie je pod jednotným policy/execution framingom

Problém:
- diff/repo analýza ide priamo cez host operations
- z dlhodobého hľadiska to nesedí s control plane / execution plane smerom

Nechcem teraz veľký framework rewrite.
Chcem pevný foundation krok:
- explicitne pomenovať review execution mode
- auditovať ho v trace
- pripraviť čistý hook pre budúci unified execution policy path

Acceptance:
- reviewer execution mode je explicitný v modeli alebo trace
- nie je to skrytá host-level obchádzka bez pomenovania

### 5. Progress a scope claimy majú zostať pravdivé

Po zmenách:
- neupravuj docs marketingovo
- ak reviewer stále nie je full delivery-ready, povedz to priamo
- žiadne “complete” claimy, ak runtime realita ešte nie je uzavretá

## Backlog items, ktoré týmto kolom cieliš

- `T1-E1-S5`: Reconcile coexistence rules between `ReviewJob`, `JobRunner`,
  `Task`, and `AgentLoop`
- `T1-E2-S5`: Persist full intake, report payloads, and artifact payloads for
  recovery-safe reload
- `T1-E3-S5`: Bind reviewer jobs to workspace discipline or define explicit
  read-only review execution mode
- `T2-E4-S5`: Route Telegram/API review requests through `ReviewService`
- `T5-E1-S5`: Bring repository and diff analysis under shared execution-policy
  intent
- `T8-E1-S5`: Reduce duplicated reviewer flows and legacy channel coupling

## Testy, ktoré chcem

Minimálne doplň:
- recovery test pre plný reload jobu vrátane intake
- recovery test pre artifact payload
- runtime adapter test: Telegram/API path → ReviewService
- execution mode test: review trace nesie read-only/workspace režim
- regression test, že reviewer artifacts sú použiteľné pre delivery/reload

## Acceptance criteria

Na konci musí platiť:
- reviewer slice je stále green
- full pytest suite prejde
- reviewer job reload je výrazne bohatší a recovery-safe
- aspoň jeden runtime adapter používa nový `ReviewService`
- workspace/execution režim je explicitný a poctivý
- docs nepreháňajú hotovosť slice-u

## Formát záverečnej odpovede

Na konci vypíš:
1. čo si zmenil
2. ktoré findings si uzavrel
3. čo zostáva otvorené
4. aké testy si spustil a s akým výsledkom
5. či je reviewer v1 po tomto kole už `usable`, `mostly complete`, alebo stále
   len `in progress`
