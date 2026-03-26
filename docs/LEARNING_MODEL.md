# Learning Model

Definícia čo "learning" znamená v Agent Life Space.
Nie je to marketingový claim — je to technická špecifikácia.

## Čo learning JE

Learning je zmena správania agenta na základe minulých výsledkov.
Zmena musí byť:
- **auditovateľná** — viem čo sa zmenilo a prečo
- **reverzibilná** — viem vrátiť predchádzajúce správanie
- **explicitná** — nie tiché samoupravovanie

## 4 typy learning

### 1. Skill learning
- **Čo:** Agent si pamätá success/failure pre konkrétne schopnosti
- **Kde:** `agent/brain/skills.json`
- **Zmena správania:** Skill confidence ovplyvňuje routing a prompt
- **Audit:** `skills.json` je verzionovaný v git

### 2. Prompt learning (augmentation)
- **Čo:** Minulé chyby sa pridávajú do promptu pri podobných úlohách
- **Kde:** `agent/brain/learning.py:augment_prompt()`
- **Zmena správania:** LLM dostane kontext o minulých chybách
- **Audit:** Logované ako `learning_prompt_augmented`

### 3. Routing learning (model escalation)
- **Čo:** Ak model zlyhal na skill, nabudúce sa eskaluje na silnejší
- **Kde:** `agent/brain/learning.py:adapt_model()`
- **Zmena správania:** Iný model pre rovnaký typ úlohy
- **Audit:** Logované ako `learning_model_escalation`
- **Limit:** In-memory, resets na reštarte (by design)

### 4. Factual learning
- **Čo:** Nové fakty z tool results, API responses, user statements
- **Kde:** `agent/memory/store.py` s provenance model
- **Zmena správania:** Rozhodnutia informované pamäťou
- **Audit:** Memory provenance (observed/user_asserted/inferred/verified/stale)

## Čo learning NIE JE

- Nie je to fine-tuning modelu
- Nie je to automatické prepisovanie pravidiel
- Nie je to nesledované samoupravovanie
- Nie je to "AI sa učí" v marketingovom zmysle

## Bezpečnostné pravidlá

1. **Learning nesmie meniť security rules** — tool policy je statická
2. **Learning nesmie meniť approval thresholds** — finance approval je hardcoded
3. **Každá zmena musí byť logovaná** — žiadne tiché úpravy
4. **Rollback:** skill reset cez `skills.json` edit, memory cez `mark_stale()`
5. **Owner control:** Daniel môže kedykoľvek resetnúť skills alebo pamäť

## Metriky

- `skill_confidence` — success_count / (success_count + failure_count)
- `model_escalation_count` — koľkokrát sa eskalovalo
- `prompt_augmentation_count` — koľkokrát sa pridali past errors
- `memory_verified_ratio` — podiel verified facts v memory

## Známe limity

- Model failure tracking je in-memory (resets na reštarte)
- Success/failure detection z reply textu je heuristická
- Žiadny eval set na meranie learning kvality (TODO)
- Žiadne confidence intervals, len point estimates
