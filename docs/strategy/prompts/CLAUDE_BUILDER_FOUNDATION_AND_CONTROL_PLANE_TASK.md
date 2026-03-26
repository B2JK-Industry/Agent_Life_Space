# Claude Code Task: Builder Foundation And Control-Plane Convergence

Pracuješ v repozitári:

`/Users/danielbabjak/Desktop/ANP/Agent_Life_Space`

Najprv si načítaj:
- `docs/strategy/MASTER_SOURCE_OF_TRUTH.md`
- `docs/strategy/THEMES_EPICS_STORIES.md`
- `docs/strategy/BACKLOG_PROGRESS.md`
- `docs/strategy/backlog_seed.yaml`

## Aktuálny stav pred týmto kolom

Reviewer v1 je `complete_for_phase`:
- execution_mode vždy READ_ONLY_HOST (pravdivý)
- _get_analysis_path() je single source of truth pre analyzéry
- delivery_ready=False by default (vyžaduje explicit approval)
- redaction pipeline na všetkých textových poliach (description, impact, recommendation, evidence)
- requester, source, execution_mode, execution_trace stripnuté z client-safe exportu
- ReviewArtifact.from_dict() hydratuje metadata graf
- operator má mock-driven TS skeleton s CI typecheck (bez live backendu)
- ADR-001 definuje execution sidecar contract (Go)

Čo ešte neexistuje:
- žiadny shared control-plane job model (ReviewJob, JobRunner, Task, AgentLoop koexistujú)
- žiadny builder bounded context
- žiadna acceptance criteria model
- žiadny builder verification loop

## Cieľ tohto kola

Neotváraj nový chaos. Urob ďalší vysokoleverage krok po Reviewer v1:

`Builder Foundation + Control-Plane Convergence`

Tento slice má projekt posunúť:
- z reviewer-first systému
- na reviewer + builder foundation
- bez toho, aby sa builder nalepil na starý chat/runtime flow

## Čo teraz nerobiť

Nerob:
- Operator product slice
- External capability gateway
- full GitHub App
- multi-tenant architektúru
- mikroservisy
- veľký rewrite Reviewer bounded contextu

Ak bude scope príliš veľký:
- preferuj správne foundations
- neotváraj naraz všetky build use cases

## Priorita

Uprednostni:
1. correctness
2. workspace and execution discipline
3. explicitný job/control-plane model
4. auditability
5. testability
6. extensibility

## Backlog items, ktoré má toto kolo posunúť

Primárne:
- `T1-E1-S1`
- `T1-E1-S2`
- `T1-E1-S3`
- `T1-E1-S5`
- `T1-E3-S1`
- `T1-E3-S3`
- `T3-E1-S1`
- `T3-E1-S2`
- `T3-E1-S3`
- `T3-E1-S4`
- `T3-E2-S1`
- `T3-E2-S3`
- `T3-E2-S4`
- `T3-E3-S1`
- `T3-E3-S2`
- `T8-E1-S1`
- `T8-E1-S4`

Sekundárne, ak zostane čas bez chaosu:
- `T4-E1-S1`
- `T4-E2-S1`

## Architektonický cieľ

Po tomto kole musí byť v kóde jasné:
- čo je shared control-plane primitive
- čo je reviewer-specific job
- čo je builder-specific job
- ako sa builder job viaže na workspace, artifacts, verification, acceptance criteria

Nechcem, aby Builder vznikol ako:
- ďalší branch v `AgentBrain`
- ďalšia chat command vetva
- ďalšia ad-hoc sada dictov

Chcem:
- nový bounded context
- explicitné modely
- explicitný lifecycle
- explicitné artifacts
- explicitný verification loop

## Implementuj tieto časti

### 1. Shared control-plane job foundation

Zaveď nový shared foundation layer, napríklad:
- `agent/control/`
- alebo iný rozumný bounded context

Potrebujem minimálne:
- shared `JobKind` / `JobType`
- shared `JobStatus`
- shared `JobTiming` alebo ekvivalent
- shared `ArtifactRef`
- shared `ExecutionRef` alebo `ExecutionTraceRef`
- shared `UsageSummary` alebo foundation pre cost/usage

Dôležité:
- nerob full migration celého systému naraz
- reviewer môže zatiaľ používať adapter alebo bridge
- ale nový builder slice už má stáť na shared control-plane primitives

Acceptance:
- existuje nový shared job foundation modul
- builder ho používa priamo
- reviewer je s ním kompatibilný alebo pripravený na kompatibilitu

### 2. Builder bounded context

Vytvor nový bounded context, ideálne:
- `agent/build/`

Potrebujem minimálne tieto modely:
- `BuildJob`
- `BuildIntake`
- `BuildArtifact`
- `BuildVerificationResult`
- `AcceptanceCriteria`
- `AcceptanceVerdict`

Builder v tomto kole nemusí robiť všetko.
Stačí prvý poctivý foundation slice.

Build job musí niesť minimálne:
- id
- type
- requester / owner
- repo/work target
- workspace_id
- acceptance_criteria
- status
- created_at / started_at / completed_at
- artifacts
- verification results
- execution trace

Acceptance:
- builder bounded context existuje
- modely nie sú len placeholdery
- sú testované

### 3. Workspace-first builder execution

Mutable engineering work musí byť workspace-first.

Sprav:
- build jobs nech pracujú cez `WorkspaceManager`
- build flow nech nepíše priamo do host repo bez explicitného workspace modelu
- execution mode buildera musí byť explicitný a auditovateľný

Ak je potrebné začať menším use case:
- supportni najprv lokálny workspace-bound implementation task
- nie full repo automation

Acceptance:
- builder execution je workspace-bound
- trace to zaznamenáva
- artifacts vedia ukázať výsledný diff alebo patch surface

### 4. Build artifacts

Pridaj builder artifacts as first-class outputs:
- patch artifact
- diff artifact
- verification artifact
- acceptance report artifact

Ak treba, reuse-ni shared artifact primitives z reviewer foundations.

Acceptance:
- builder job vytvára a ukladá artifacts
- artifacts sú linkované na job
- recovery/reload story je aspoň základne rozumná

### 5. Verification loop

Builder musí mať prvý verification loop.

Potrebujem aspoň:
- test command hook
- lint hook
- type-check hook ak dáva zmysel v tomto repo
- explicitný verification verdict

Nech builder job vie:
- spustiť verification kroky
- zlyhať čitateľne
- uložiť verification outputs ako artifacts

Acceptance:
- builder verification je first-class súčasť jobu
- výsledok je auditovateľný
- job nezostane len "spravil patch a hotovo"

### 6. Acceptance criteria foundation

Acceptance criteria už nesmú byť len strategická veta.

Zaveď:
- explicitný `AcceptanceCriteria` model
- builder job ho vie niesť
- completion path ho vie vyhodnotiť aspoň v prvom, foundation-grade režime

Nemusíš spraviť full semantic engine.
Stačí poctivá foundation:
- definícia
- storage
- verdict
- report artifact

Acceptance:
- acceptance criteria sú first-class model
- builder flow ich nesie a vyhodnocuje

### 7. Minimal build service

Vytvor channel-independent build service, podobne ako reviewer service.

Preferované moduly:
- `agent/build/service.py`
- `agent/build/models.py`
- `agent/build/storage.py`
- `agent/build/verification.py`

Build service musí vedieť aspoň:
- vytvoriť build job
- validovať intake
- pripraviť workspace
- spustiť minimálny build flow
- uložiť artifacts
- spustiť verification
- vyprodukovať acceptance verdict

### 8. Runtime neutrality

Tento slice nemá byť Telegram-first.

To znamená:
- builder flow musí fungovať bez Telegramu
- ak pridáš runtime adapter, nech je len tenký
- business logika musí bývať v `agent/build/`, nie v `agent/social/`

### 9. Strategy truth update

Po implementácii pravdivo aktualizuj:
- `docs/strategy/BACKLOG_PROGRESS.md`
- `docs/strategy/THEMES_EPICS_STORIES.md`
- `docs/strategy/backlog_seed.yaml`

Neoznačuj veci za hotové, ak sú len foundation.
Používaj poctivo:
- `started`
- `in_progress`
- `mostly_complete`
- `complete_for_phase`

## Testy

Doplň testy minimálne na:
- shared job foundation modely
- build job creation
- workspace binding
- build artifact generation
- verification artifacts
- acceptance criteria model and verdict
- build service end-to-end as first slice
- recovery/reload tam, kde to v tomto kole podporíš

Na konci spusti:
- `python3 -m pytest tests -q`
- ak je dostupné:
  - `python3 -m ruff check .`
  - `python3 -m mypy agent`

Ak `ruff` alebo `mypy` v env chýba, povedz to explicitne.

## Acceptance criteria tohto tasku

Na konci musí platiť:
1. existuje shared control-plane job foundation
2. existuje builder bounded context
3. builder jobs sú workspace-first
4. builder jobs vytvárajú artifacts
5. builder jobs majú verification loop
6. acceptance criteria sú first-class model
7. build service je channel-independent
8. stratégia je aktualizovaná podľa reality
9. testy prejdú

## Formát záverečnej odpovede

Na konci vypíš:
1. čo si zmenil
2. ktoré backlog items si posunul
3. čo zostáva otvorené
4. aké testy si spustil a s akým výsledkom
5. ako sa po tomto kole mení progress T1, T3, T4 a T8

Najprv implementuj. Nepíš marketingový summary.
