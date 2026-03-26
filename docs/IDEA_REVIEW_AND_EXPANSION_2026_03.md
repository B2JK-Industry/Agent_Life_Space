# Agent Life Space — Idea Review and Expansion

Dátum review: 2026-03-26

## Executive Verdict

Projekt má veľmi dobrý základ. Nie je to len "AI shell" s pekným README, ale pomerne disciplinovaný self-hosted agent stack so silným dôrazom na deterministické vrstvy, bezpečnosť a testovateľnosť.

Najsilnejší aspekt nie je samotný "autonomous agent" claim, ale to, že systém sa snaží byť:
- self-hosted,
- bezpečnostne zdržanlivý,
- lacný na beh,
- a auditovateľný.

To je správny smer.

## Čo je na nápade silné

### 1. Rozumná architektúra namiesto hype

7-layer cascade dáva zmysel. Myšlienka "LLM až keď lokálne vrstvy nestačia" je lepšia než defaultný pattern "všetko pošli modelu".

### 2. Bezpečnostný mindset je v DNA projektu

Sandbox, vault, auth, safe mode, owner gating a security test suite ukazujú, že projekt berie dôsledky agentic systému vážne.

### 3. Test-first kultúra výrazne zvyšuje dôveryhodnosť

Repo nie je len koncept. Má robustný test suite, ktorý pokrýva unit, integration, e2e aj security vrstvu.

### 4. Dobrý produktový framing

"Agent, ktorý žije na tvojom serveri" je zrozumiteľná a zapamätateľná idea. Je to oveľa silnejšie než generický "AI assistant".

## Kde je koncept stále slabší

### 1. Chýbala explicitná governance vrstva pre tool use

Pôvodne bol skok medzi "LLM navrhne tool" a "tool sa vykoná" príliš priamy. To je problém pri systéme, ktorý má robiť reálne akcie.

V tomto passe som doplnil deterministickú `ToolPolicy` vrstvu:
- blokuje citlivé tooly v safe mode,
- blokuje owner-only tooly pre ne-owner kontext,
- vracia risk metadata pre audit.

### 2. Produkt je zatiaľ skôr "single-operator sovereign agent" než všeobecná platforma

To nie je chyba, ale dôležité pomenovanie. Dnes je to veľmi silné pre jedného vlastníka a jeho infra/workflow. Menej pripravené je to na multi-user, team governance alebo delegated approvals.

### 3. Pamäť a učenie sú zaujímavé, ale stále skôr heuristické než epistemicky silné

Systém vie ukladať, sumarizovať a spätne používať znalosti. To je dobré. Ale stále chýba silnejšie odlíšenie:
- verified facts,
- user claims,
- stale knowledge,
- and action-critical assumptions.

### 4. Telegram je dobrý interface, ale nie je to ideálne centrum operačného systému

Pre daily use je to výborné. Pre dlhšie workflows, approvals, action history a governance bude skôr potrebný dashboard alebo explicitný review inbox.

## Čo som rozšíril v tomto passe

### 1. Tool governance layer

Pridaný nový modul:
- `agent/core/tool_policy.py`

Prínos:
- explicitná policy medzi LLM tool requestom a vykonaním,
- citlivé tooly (`run_code`, `run_tests`, `web_fetch`, `create_task`) už nie sú len implicitne "dúfajme safe",
- safe mode má teraz aj tool-level význam, nielen command-level význam.

### 2. Oprava API tool-use flow v AgentBrain

Fixnutý bug v `agent/core/brain.py`, kde API tool-use vetva používala na konci neexistujúcu premennú `response`.

Prínos:
- API backend s tool use už nepadá pri usage footeri,
- usage tracking funguje aj cez `ToolLoopResult`,
- token/cost reporting je konzistentnejší.

### 3. Tool loop teraz prenáša request context

`ToolUseLoop` teraz vie forwardovať execution context do `ToolExecutor`, takže policy rozhoduje podľa reálneho kontextu requestu.

## Review existujúcej implementácie

## Overené fakty

- Celý suite: `708 passed, 4 skipped` na 2026-03-26
- Security subset: `116 passed`
- Test runtime lokálne: približne `21s`

## Silné stránky implementácie

### 1. Repo má lepší pomer "claimy vs realita" než väčšina agent projektov

Je tu reálny kód, reálne testy, reálne oddelené moduly.

### 2. Modulárnosť je praktická, nie iba teoretická

`core`, `brain`, `memory`, `social`, `finance`, `tasks`, `work`, `projects` dávajú zmysel aj v kóde, nielen v diagrame.

### 3. Security audit testy sú veľké plus

Automatizovaný security regression layer je presne to, čo agent projekty zvyčajne nemajú.

## Nálezy

### Fixnuté v tomto passe

1. `AgentBrain` API tool-use flow mal runtime bug s neexistujúcou premennou `response`.
2. Chýbala deterministická policy vrstva pre tool execution.

### Stále otvorené / odporúčam riešiť ďalej

1. Pydantic warning okolo poľa `model_used`
2. Jeden `RuntimeWarning` v test suite (`AsyncMock` neawaitnutý v test scénari)
3. README ešte miestami skĺzava do claimov, ktoré treba držať viac pri zemi než v marketingu

## Najlepšie ďalšie rozšírenia

### 1. Approval inbox / governance UI

Nie len Telegram approval, ale explicitné miesto pre:
- pending actions,
- risky tool calls,
- finance proposals,
- external write actions.

### 2. Fact provenance layer

Každá dôležitá znalosť by mala mať status:
- observed,
- user-asserted,
- inferred,
- verified,
- expired.

### 3. Event-sourced action log

Agent potrebuje silnejší audit trail:
- kto požiadal,
- čo agent navrhol,
- čo policy dovolila,
- čo sa vykonalo,
- aký bol výsledok.

### 4. Capability manifest pre plug-in model

Ak má byť z toho platforma, capability registry musí byť formálny:
- vstupy,
- side effects,
- trust level,
- required approvals,
- rollback story.

### 5. Eval harness na reálne úlohy, nie len correctness tests

Doplniť task benchmarky typu:
- "nájdi a oprav bug",
- "sprav repo review",
- "navrhni release plan",
- "sprav bezpečnostný pre-check".

## Záver

Ako nápad je to silné.
Ako implementácia je to nadpriemerne poctivé.
Ako produkt je to už teraz dobrý "personal sovereign operator agent".

Najväčší ďalší skok nebude viac "autonomy", ale lepšia:
- governance,
- provenance,
- reviewability,
- and human-control surface.
