# Agent Life Space — Používateľská dokumentácia

Self-hosted autonómny agent bežiaci na vlastnom hardvéri (Acer Aspire V, i7, Ubuntu 24.04).

---

## Rýchly štart

```bash
# 1. Aktivuj prostredie
source .venv/bin/activate

# 2. Zobraz stav agenta
python -m agent --status

# 3. Zobraz zdravie systému (CPU, RAM, moduly)
python -m agent --health

# 4. Spusti agenta (Ctrl+C pre ukončenie)
python -m agent

# 5. Spusti testy
python -m pytest tests/ -q
```

---

## Architektúra

```
┌──────────────────────────────────────────┐
│           Agent Orchestrátor             │
│                                          │
│  ┌──────────┐  ┌─────────────────────┐  │
│  │ Watchdog  │  │ Brain (Decision     │  │
│  │ heartbeat │  │   Engine)           │  │
│  │ restart   │  │ algo vs LLM routing │  │
│  └─────┬────┘  └──────────┬──────────┘  │
│        │                  │              │
│  ┌─────┴──────────────────┴───────────┐  │
│  │         Message Router             │  │
│  │  priority queue · dead letters     │  │
│  │  retry · TTL · metrics             │  │
│  └──┬───┬───┬───┬───┬───┬───┬───┬────┘  │
│     │   │   │   │   │   │   │   │        │
│   Brain Mem Task Work Proj Soc Fin Log   │
│                                          │
│  ┌──────────┐  ┌─────────┐  ┌────────┐  │
│  │LLM Router│  │Job Runner│  │ Vault  │  │
│  │templates │  │timeout   │  │encrypt │  │
│  │schema    │  │retry     │  │audit   │  │
│  └──────────┘  └─────────┘  └────────┘  │
└──────────────────────────────────────────┘
```

---

## Moduly

### 1. Core — Message Protocol (`agent/core/messages.py`)

Všetka komunikácia medzi modulmi je cez štruktúrované JSON správy.

**Čo to robí:**
- Každá správa má unikátne ID, odosielateľa, príjemcu, typ, prioritu a TTL
- Správy sú immutabilné po vytvorení (Pydantic frozen model)
- Payload sa validuje — musí byť JSON-serializovateľný
- Správy s expirovaným TTL sa automaticky zahodia (žiadne zombie správy)

**Typy správ:**
- `request/response/error/ack` — základná komunikácia
- `task.create/update/complete/fail` — správa úloh
- `memory.store/query/result` — pamäťové operácie
- `llm.request/response/error` — LLM volania
- `job.*` — job lifecycle
- `health.*` — watchdog
- `finance.proposal/approval/rejection` — financie (vždy s approval)

**Priority (nižšie číslo = vyššia priorita):**
- `CRITICAL (0)` — systémové zdravie, watchdog
- `HIGH (1)` — aktívne odpovede
- `NORMAL (2)` — štandardné operácie
- `LOW (3)` — background tasky
- `IDLE (4)` — údržba

**Príklad:**
```python
from agent.core.messages import Message, MessageType, ModuleID, Priority

msg = Message(
    source=ModuleID.BRAIN,
    target=ModuleID.MEMORY,
    msg_type=MessageType.MEMORY_QUERY,
    priority=Priority.NORMAL,
    payload={"query": "posledných 5 dokončených taskov"},
    ttl_seconds=60,
)
```

---

### 2. Message Router (`agent/core/router.py`)

Centrálny nervový systém — smeruje správy medzi modulmi.

**Čo to robí:**
- Asynchrónna priority queue — dôležité správy sa spracujú prvé
- Dead Letter Queue — neodoručiteľné správy sa neukladajú do /dev/null, ale do DLQ
- Automatický retry s exponential backoff pri zlyhaniach
- Timeout na každé doručenie — handler musí odpovedať v rámci TTL
- Metriky: enqueued, delivered, expired, errors

**Kľúčové garancie:**
- Žiadna správa sa tíško nestratí — buď doručená alebo v DLQ
- Expirované správy sa nikdy nedoručia
- Zlyhanie jedného handlera nerozbije router

---

### 3. Brain — Decision Engine (`agent/brain/decision_engine.py`)

Rozhoduje ČO sa robí a AKO — algoritmicky, bez LLM kde to nie je nutné.

**Pravidlo:** LLM len tam kde je pridaná hodnota. Všade inde algoritmus.

**Algoritmické (deterministické, žiadny LLM):**
- Task prioritizácia (scoring formula)
- Task routing (kam správu poslať)
- Správa rozvrhu
- Error handling (retry/dead letter rozhodnutia)
- Memory management

**LLM:**
- Generovanie obsahu (články, texty)

**Hybrid (algoritmus + LLM):**
- Evaluácia príležitostí (algoritmus filtruje, LLM analyzuje)
- Finance (algoritmus validuje limity, LLM hodnotí príležitosť)

**Task scoring formula:**
```
priority = importance × 0.4 + urgency × 0.3 + (1 - effort) × 0.2 + deps × 0.1
combined = priority × 0.6 + urgency × 0.4
```
Deadline < 1h → urgency sa automaticky zvýši na 1.0

**Príklad:**
```python
from agent.brain.decision_engine import DecisionEngine

engine = DecisionEngine()

# Rozhodne či úloha potrebuje LLM alebo algoritmus
decision = engine.should_use_llm("Sort these items by priority")
# → action="use_algorithm", confidence=0.8

decision = engine.should_use_llm("Write a blog post about AI")
# → action="use_llm", confidence=0.7
```

---

### 4. Memory Store (`agent/memory/store.py`)

Viacvrstvová pamäť — agent ju aktívne používa pri rozhodovaní.

**4 typy pamäte:**

| Typ | Čo ukladá | Decay |
|---|---|---|
| **Working** | Aktuálny kontext, dočasné dáta | Rýchly (5× base) |
| **Episodic** | Čo sa stalo, skúsenosti | Normálny |
| **Semantic** | Fakty, znalosti | Normálny |
| **Procedural** | Naučené postupy | Normálny |

**Relevance scoring (deterministický):**
```
score = tag_overlap × importance × confidence × decay × recency × frequency
```
- `tag_overlap` — Jaccard similarity medzi query tags a memory tags
- `recency` — novšie pamäte skórujú vyššie (polčas 24h)
- `frequency` — často pristupované pamäte sú dôležitejšie
- `decay` — časom klesá, access reinforcement ju zvyšuje

**Persistence:** SQLite databáza, prežije reštart.

**Príklad:**
```python
from agent.memory.store import MemoryStore, MemoryEntry, MemoryType

store = MemoryStore(db_path="agent/memory/memories.db")
await store.initialize()

# Ulož pamäť
await store.store(MemoryEntry(
    content="API rate limit je 100 req/min",
    memory_type=MemoryType.SEMANTIC,
    tags=["api", "limits", "rate-limit"],
    importance=0.8,
))

# Vyhľadaj
results = await store.query(tags=["api"], limit=5)
results = await store.query(keyword="rate limit")

# Decay — spusti pravidelne (napr. denne)
deleted = await store.apply_decay(decay_rate=0.01)
```

---

### 5. Task Manager (`agent/tasks/manager.py`)

Deterministická správa úloh s dependency tracking.

**Životný cyklus tasku:**
```
CREATED → QUEUED → RUNNING → COMPLETED
                           → FAILED
                           → CANCELLED
         BLOCKED → (dependencies splnené) → QUEUED
```

**Vlastnosti:**
- **Dependencies** — task čaká na dokončenie iných taskov
- **Priority scoring** — algoritmický, rovnaký vstup = rovnaký výstup
- **Persistent** — SQLite, prežije reštart
- **Tags** — filtrovanie podľa kategórií

**Príklad:**
```python
from agent.tasks.manager import TaskManager

mgr = TaskManager(db_path="agent/tasks/tasks.db")
await mgr.initialize()

# Vytvor task
task = await mgr.create_task(
    name="Research competitors",
    importance=0.8,
    urgency=0.6,
    tags=["research"],
)

# Vytvor závislý task
task2 = await mgr.create_task(
    name="Write report",
    dependencies=[task.id],  # Čaká na "Research competitors"
)
# task2.status == BLOCKED

# Dokonči prvý → druhý sa odblokuje
await mgr.start_task(task.id)
await mgr.complete_task(task.id, result={"findings": "..."})
# task2.status sa zmení na QUEUED

# Získaj najdôležitejší task
next_task = mgr.get_next_task()
```

---

### 6. Job Runner (`agent/core/job_runner.py`)

Spoľahlivé vykonávanie jobov — žiadne zaseknutie, žiadne tiché zlyhania.

**Garancie:**
- **Hard timeout** na každý job (default 60s)
- **Exponential backoff** retry: `delay = base × 2^attempt` (capped)
- **Max retries** (default 3) → potom dead letter queue
- **Concurrent limit** (default 4) — nepretečie CPU/RAM
- **JSON validácia** — job musí vrátiť dict

**Príklad:**
```python
from agent.core.job_runner import JobRunner, JobConfig

runner = JobRunner(max_concurrent=4)

# Registruj typ jobu
async def my_job(url: str) -> dict:
    # ... urob niečo ...
    return {"status": "ok", "data": "..."}

runner.register_job_type("scrape", my_job)

# Naplánuj s custom konfiguráciou
job_id = await runner.schedule(
    "scrape",
    kwargs={"url": "https://example.com"},
    config=JobConfig(
        timeout_seconds=30,
        max_retries=2,
        retry_base_delay=1.0,
    ),
)
```

---

### 7. LLM Router (`agent/core/llm_router.py`)

Komunikácia s Claude API — šablóny, nie raw prompty.

**Anti-halucinačné opatrenia:**
1. Všetky prompty sú template-based (preddefinované šablóny)
2. Každá odpoveď sa validuje cez JSON Schema
3. Nevalidná odpoveď → retry s chybovou správou (max 2×)
4. Temperature 0.0 default (deterministický output)
5. Markdown-wrapped JSON sa automaticky extrahuje

**Zabudované šablóny:**
- `task_breakdown` — rozdelenie úlohy na kroky
- `summarize_for_memory` — sumarizácia pre pamäť
- `evaluate_opportunity` — hodnotenie príležitostí
- `generate_content` — generovanie obsahu

**Príklad vlastnej šablóny:**
```python
from agent.core.llm_router import LLMRouter, PromptTemplate

router = LLMRouter()

router.templates.register(PromptTemplate(
    template_id="analyze_market",
    system_prompt="You are a market analyst. Respond ONLY with valid JSON.",
    user_template="Analyze market for: {product}\nBudget: {budget}",
    response_schema={
        "type": "object",
        "properties": {
            "viable": {"type": "boolean"},
            "competitors": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["viable", "competitors"],
    },
))
```

---

### 8. Watchdog (`agent/core/watchdog.py`)

Monitoruje všetky moduly a systémové zdravie.

**Stavy modulov:**
```
HEALTHY → DEGRADED → UNHEALTHY → DEAD
   ↑          |
   └── heartbeat (recovery)
```

- `HEALTHY` — heartbeat v rámci timeout
- `DEGRADED` — heartbeat meškajúci (1-2× timeout)
- `UNHEALTHY` — heartbeat meškajúci (2-3× timeout)
- `DEAD` — žiadny heartbeat > 3× timeout → auto-restart

**Systémové metriky:** CPU %, RAM %, disk %, per-modul stav

**Príklad:**
```python
from agent.core.watchdog import Watchdog

wd = Watchdog(check_interval=10.0, cpu_threshold=90.0)
wd.register_module("brain", heartbeat_timeout=30.0)

# Modul posiela heartbeat pravidelne
wd.heartbeat("brain")

# Kontrola zdravia
health = wd.get_system_health()
# → cpu_percent, memory_percent, modules: {brain: "healthy"}, alerts: []
```

---

### 9. Vault — Secrets Manager (`agent/vault/secrets.py`)

Šifrované úložisko pre API kľúče a citlivé dáta.

**Bezpečnosť:**
- AES-128-CBC + HMAC-SHA256 (Fernet) šifrovanie
- Kľúč derivovaný cez PBKDF2 (480,000 iterácií)
- Audit trail každého prístupu
- In-memory cache (clearovateľný)
- Nesprávny master key → decrypt fail, nie corrupted data

**Setup:**
```bash
# Nastav master key (env variable)
export AGENT_VAULT_KEY="tvoj-silny-master-key"
```

**Príklad:**
```python
from agent.vault.secrets import SecretsManager

vault = SecretsManager(vault_dir="agent/vault", master_key="...")

# Ulož secret
vault.set_secret("ANTHROPIC_API_KEY", "sk-ant-...")

# Načítaj
key = vault.get_secret("ANTHROPIC_API_KEY")

# Zoznam (len názvy, NIE hodnoty)
names = vault.list_secrets()  # ["ANTHROPIC_API_KEY"]

# Audit
log = vault.get_audit_log()
```

---

### 10. Finance Tracker (`agent/finance/tracker.py`)

Sledovanie rozpočtu a finančných návrhov. **Všetko vyžaduje ľudské schválenie.**

**Workflow:**
```
Agent navrhne výdavok → PROPOSED
    ↓
Daniel schváli        → APPROVED → COMPLETED
Daniel zamietne       → REJECTED
```

**Budget limity (nastaviteľné):**
- Denný: $50 default
- Mesačný: $500 default

**Príklad:**
```python
from agent.finance.tracker import FinanceTracker

ft = FinanceTracker(daily_budget_usd=50.0, monthly_budget_usd=500.0)
await ft.initialize()

# Agent navrhne výdavok
tx = await ft.propose_expense(
    amount_usd=12.99,
    description="Kúpiť doménu example.com",
    category="infrastructure",
    rationale="Doména pre projekt",
)
# tx.status == PROPOSED

# Daniel schváli
await ft.approve(tx.id)
await ft.complete(tx.id)

# Záznam príjmu
await ft.record_income(50.0, "Freelance platba", source="client_a")

# Stav rozpočtu
stats = ft.get_stats()
# → total_income, total_expenses, net, pending_proposals, budget
```

---

### 11. Logger (`agent/logs/logger.py`)

Štruktúrované JSON logy so secret redaction.

**Bezpečnosť:** Akýkoľvek key obsahujúci `api_key`, `password`, `token`, `secret`, `credential`, `auth`, `bearer` sa automaticky nahradí `***REDACTED***`.

**Príklad:**
```python
from agent.logs.logger import AgentLogger

log = AgentLogger(log_dir="agent/logs")

log.info("task_completed", source="brain", task_id="abc123")
log.error("job_failed", source="runner", error="timeout")
log.audit("secret_accessed", source="vault", target="ANTHROPIC_API_KEY")

# Vyhľadávanie
results = log.search("task_completed", limit=10)
recent = log.read_recent(count=20)
```

---

## Štruktúra súborov

```
Agent_Life_Space/
├── pyproject.toml           # Python projekt konfigurácia
├── DOCS.md                  # Táto dokumentácia
├── .gitignore               # Git exclusions
├── agent/
│   ├── __init__.py          # v0.1.0
│   ├── __main__.py          # CLI entry point
│   ├── core/
│   │   ├── messages.py      # JSON message protocol (310 riadkov)
│   │   ├── router.py        # Message bus (200 riadkov)
│   │   ├── llm_router.py    # LLM s template + schema (320 riadkov)
│   │   ├── job_runner.py    # Spoľahlivý job executor (250 riadkov)
│   │   ├── watchdog.py      # Process monitoring (230 riadkov)
│   │   └── agent.py         # Orchestrátor (300 riadkov)
│   ├── brain/
│   │   └── decision_engine.py  # Algo vs LLM (290 riadkov)
│   ├── memory/
│   │   └── store.py         # 4-vrstvová pamäť (310 riadkov)
│   ├── tasks/
│   │   └── manager.py       # Task lifecycle (280 riadkov)
│   ├── finance/
│   │   └── tracker.py       # Budget + approval (260 riadkov)
│   ├── logs/
│   │   └── logger.py        # Structured logging (170 riadkov)
│   ├── vault/
│   │   └── secrets.py       # Encrypted secrets (190 riadkov)
│   ├── docs/
│   │   └── research_agent_frameworks.md
│   ├── social/              # (scaffold — na doplnenie)
│   ├── work/                # (scaffold — na doplnenie)
│   └── projects/            # (scaffold — na doplnenie)
└── tests/
    ├── test_messages.py     # 23 testov
    ├── test_router.py       # 12 testov
    ├── test_llm_router.py   # 19 testov
    ├── test_job_runner.py   # 12 testov
    ├── test_brain.py        # 25 testov
    ├── test_memory.py       # 19 testov
    ├── test_tasks.py        # 16 testov
    ├── test_watchdog.py     # 14 testov
    ├── test_vault.py        # 16 testov
    ├── test_finance.py      # 16 testov
    ├── test_logger.py       # 13 testov
    └── test_integration.py  # 14 testov
```

---

## Bezpečnostné princípy

1. **Financie** — Agent navrhuje, Daniel schvaľuje. Vždy. Bez výnimky.
2. **Secrets** — Šifrované na disku, redaktované v logoch, audit trail.
3. **Žiadne wallet prístupy** — Agent nemá priamy prístup k peniazom.
4. **Žiadne smart contracty** — Agent neinteraguje s blockchainom.
5. **Anti-stochastika** — Kde nie je nutný LLM, beží deterministický algoritmus.
6. **Timeout na všetkom** — Žiadny proces beží nekonečne.
7. **Dead letter queue** — Žiadne tiché zlyhanie, všetko sa zaloguje.

---

## Ďalšie kroky

- [ ] Deploy na server (ssh b2jk)
- [ ] Social modul (API komunikácia, web scraping)
- [ ] Projects modul (riadenie earning projektov)
- [ ] Sandbox pre systémové príkazy
- [ ] Circuit breaker pattern
- [ ] Multi-model routing (lacný model na jednoduché, Opus na ťažké)
- [ ] Web dashboard pre monitoring
- [ ] Prvý earning use case
