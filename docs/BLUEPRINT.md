# Agent Life Space — Blueprint

Návod ako si vytvoriť vlastného autonómneho agenta. Daj toto svojmu botovi/agentovi a on pochopí architektúru.

---

## Čo to je

Self-hosted AI agent ktorý:
- Komunikuje cez Telegram
- Má vlastnú pamäť, skills, knowledge base
- Učí sa z toho čo robí (feedback loop: episodic → skills → knowledge)
- Minimalizuje API volania cez 7-vrstvový cascade (lokálny compute namiesto LLM)
- Programuje, scrapuje web, spúšťa kód v povinnom Docker sandboxe
- Má šifrovaný vault pre citlivé dáta (Fernet AES-128 + PBKDF2)

---

## Požiadavky

**Hardware (minimum):**
- 4-core CPU, 8GB RAM, 100GB disk
- GPU (voliteľné) — pre OCR, image processing

**Software:**
- Ubuntu 22.04+ (alebo iný Linux)
- Python 3.12+
- Docker (povinné — sandbox pre spúšťanie kódu)
- Git

**Účty:**
- Telegram Bot (cez @BotFather)
- Claude Max subscription (pre Claude Code CLI) alebo Anthropic API kľúč
- GitHub účet (voliteľné)

---

## Inštalácia

```bash
# 1. Klonuj repo
git clone https://github.com/B2JK-Industry/Agent_Life_Space.git
cd Agent_Life_Space

# 2. Vytvor Python prostredie
python3 -m venv .venv
source .venv/bin/activate

# 3. Nainštaluj závislosti
pip install -e .
pip install sentence-transformers  # pre semantic router

# 4. Nastav vault (šifrované úložisko pre kľúče)
python scripts/setup_vault.py
# Výstup: AGENT_VAULT_KEY=... (ulož si ho bezpečne)

# 5. Nastav env premenné (ideálne cez systemd, nie export)
export TELEGRAM_BOT_TOKEN="tvoj-telegram-token"
export TELEGRAM_USER_ID="tvoj-telegram-id"
export CLAUDE_CODE_OAUTH_TOKEN="tvoj-claude-token"
export AGENT_VAULT_KEY="z kroku 4"
export GITHUB_TOKEN="tvoj-github-token"  # voliteľné

# 6. Over Docker (povinné pre sandbox)
docker --version || echo "CHYBA: Docker je povinný pre sandbox!"

# 7. Spusti
python -m agent
```

### Alternatíva: Docker Compose (one-liner)

```bash
git clone https://github.com/B2JK-Industry/Agent_Life_Space.git
cd Agent_Life_Space
cp .env.example .env  # uprav tokeny
docker compose up -d
```

---

## Architektúra

```
┌─────────────────────────────────────────────────────┐
│                    TELEGRAM                          │
│              (vstup od používateľa)                  │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│                 7-LAYER CASCADE                      │
│                                                      │
│  1. /commands      → priame odpovede (0 API callov)  │
│  2. Dispatcher     → regex patterny (0 API callov)   │
│  3. Semantic Router→ MiniLM lokálne (CPU/RAM)        │
│  4. Semantic Cache → podobná otázka? (lokálne)       │
│  5. Self-RAG       → knowledge base (lokálne embed.) │
│  6. Haiku/Sonnet   → jednoduché/konverzácia (API)   │
│  7. Opus           → programovanie (API)             │
│                                                      │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│                    MODULES                           │
│                                                      │
│  BRAIN          MEMORY         CORE                  │
│  ├ decision     ├ store        ├ router              │
│  ├ dispatcher   ├ consolidate  ├ job_runner          │
│  ├ semantic_rtr ├ cache        ├ watchdog            │
│  ├ skills       ├ rag          ├ models              │
│  ├ knowledge    │              ├ web                 │
│  ├ learning     │              ├ sandbox             │
│  └ programmer   │              └ cron                │
│                 │                                    │
│  SOCIAL         FINANCE        VAULT                 │
│  ├ telegram_bot ├ tracker      └ secrets (encrypted) │
│  └ handler      │                                    │
│                 TASKS          LOGS                   │
│                 └ manager      └ logger               │
│                                                      │
└─────────────────────────────────────────────────────┘
```

---

## Ako funguje cascade

Cieľ: **minimalizovať API volania.** Vrstvy 1-5 bežia lokálne (CPU/RAM, nie zadarmo, ale bez API callov). LLM sa volá len keď interné moduly nestačia.

### Vrstva 1: Slash commands (0 API callov)
```python
# telegram_handler.py
if text.startswith("/"):
    handlers = {"/status": ..., "/health": ..., "/tasks": ...}
    return await handlers[command](args)
```
Deterministické, priamo z modulov.

### Vrstva 2: InternalDispatcher (0 API callov)
```python
# brain/dispatcher.py
class InternalDispatcher:
    def try_handle(self, text):
        if re.search(r"\b(stav|status)\b", text): return self._handle_status()
        if re.search(r"\b(zdravie|health)\b", text): return self._handle_health()
        ...
        return None  # → ďalšia vrstva
```
Regex patterny pre jednoznačné dotazy.

### Vrstva 3: Semantic Router (lokálny compute, ~470MB RAM)
```python
# brain/semantic_router.py
# MiniLM-L12-v2 (470MB, slovenčina + angličtina)
intent, confidence = classify_intent("ako sa cítiš?")
# → ("status", 0.78) → dispatch interne
```
Embedding model detekuje intent aj keď je otázka formulovaná inak.

### Vrstva 4: Semantic Cache (lokálny compute)
```python
# memory/semantic_cache.py
cached = cache.lookup("koľko mám úloh?")  # cosine > 0.90 → return cached
```
Ak agent už odpovedal na podobnú otázku, vráti cache.

### Vrstva 5: Self-RAG (lokálne embeddingy, alebo kontext pre LLM)
```python
# memory/rag.py
result = rag.retrieve_for_llm("čo viem o Danielovi?")
if result["action"] == "direct":  # score > 0.85
    return result["context"]  # z knowledge base
elif result["action"] == "augment":  # score 0.60-0.85
    prompt += result["context"]  # pridaj kontext k LLM
```
Hľadá v knowledge base cez embeddingy.

### Vrstva 6-7: LLM (Haiku/Sonnet/Opus)
```python
# core/models.py
task_type = classify_task(text)  # "simple" → Haiku, "chat" → Sonnet, "programming" → Opus
model = get_model(task_type)
# → subprocess.run(["claude", "--model", model.model_id, ...])
```
Najdrahšia vrstva — len keď ostatné nestačia.

---

## Ako vytvoriť vlastného agenta

### Krok 1: Identita
Vytvor `CLAUDE.md` s pravidlami pre agenta a `JOHN.md` (alebo tvoj názov) s identitou.

### Krok 2: Telegram Bot
1. Vytvor bota cez @BotFather
2. Nastav `TELEGRAM_BOT_TOKEN` a `TELEGRAM_USER_ID`
3. Agent automaticky začne polling

### Krok 3: Skills
Uprav `agent/brain/skills.py` — pridaj skills relevantné pre tvojho agenta. Predvolených 20:

| Skill | Kategória | Popis |
|-------|-----------|-------|
| `curl` | internet | HTTP requesty |
| `web_scraping` | internet | Čítanie webových stránok |
| `github_api` | internet | GitHub API volania |
| `github_create_issue` | github | Vytvoriť GitHub issue |
| `github_create_repo` | github | Vytvoriť GitHub repo |
| `git_commit` | git | Git commit a push |
| `git_status` | git | Git status check |
| `python_run` | code | Spustenie Python skriptu |
| `pytest` | code | Spustenie testov |
| `pip_install` | code | Inštalácia balíkov |
| `docker_run` | docker | Docker kontajner |
| `file_read` | filesystem | Čítanie súborov |
| `file_write` | filesystem | Zápis do súborov |
| `system_health` | system | CPU/RAM/disk kontrola |
| `process_check` | system | Kontrola procesov |
| `maintenance` | system | Server maintenance |
| `memory_store` | agent | Uloženie do pamäte |
| `memory_query` | agent | Hľadanie v pamäti |
| `task_create` | agent | Vytvorenie úlohy |
| `telegram_send` | communication | Telegram správy |

### Krok 4: Knowledge Base
Pridaj .md súbory do `agent/brain/knowledge/`:
- `people/` — kto je tvoj majiteľ
- `systems/` — aký server, aké služby
- `projects/` — na čom pracuješ

### Krok 5: Model Router
Uprav `agent/core/models.py` — nastav aké modely chceš používať a na čo.

### Krok 6: Customizácia
- `agent/brain/dispatcher.py` — pridaj vlastné regex patterny
- `agent/brain/semantic_router.py` — pridaj vlastné intenty
- `agent/core/cron.py` — pridaj periodické joby

---

## Kľúčové princípy

1. **LLM je posledná možnosť** — najprv hľadaj internú odpoveď (lokálny compute, nie API)
2. **Determinizmus kde sa dá** — scoring, routing, scheduling algoritmicky
3. **Pamäť je základ** — 4 typy, konsolidácia, nie len log udalostí
4. **Skills sa učia** — UNKNOWN → LEARNED → MASTERED, auto-testing, feedback loop
5. **Sandbox je povinný** — kód sa spúšťa v Docker kontajneri, nikdy priamo na host
6. **Vault pre všetky kľúče** — env vars len na master key, zvyšok šifrovaný vo vaulte
7. **Error recovery** — watchdog, heartbeaty, auto-restart, circuit breaker
8. **Stručnosť** — agent odpovedá krátko, tokeny stoja peniaze (aj lokálny compute)

---

## Ako sa agent učí (Learning Loop)

Nie je to prázdna škatuľka. Konkrétny flow:

```
┌─────────────────────────────────────────────┐
│                LEARNING LOOP                 │
│                                              │
│  1. ÚLOHA PRÍDE                              │
│     │                                        │
│  2. CHECK: can_i_do(skill)?                  │
│     ├─ MASTERED → urob to s istotou          │
│     ├─ LEARNED  → urob to, sleduj výsledok   │
│     ├─ UNKNOWN  → auto-test, potom skús      │
│     └─ FAILED   → skús znova s novým prístup.│
│     │                                        │
│  3. VYKONANIE                                │
│     │                                        │
│  4. VÝSLEDOK → i_did_it(skill, success/fail) │
│     ├─ Aktualizuj skills.json                │
│     │  (confidence, success_count, status)    │
│     ├─ Zapíš do episodic memory              │
│     └─ Ak nový poznatok → knowledge base     │
│     │                                        │
│  5. KONSOLIDÁCIA (každých 2-6h)              │
│     ├─ Opakujúce sa patterny → semantic mem  │
│     ├─ Procedurálne znalosti → procedural m. │
│     └─ Deduplikácia starých spomienok        │
│                                              │
│  Čo sa ukladá: skill outcomes, error msgs,   │
│  naučené workaroundy, nové poznatky          │
│                                              │
│  Ako sa aplikuje: can_i_do() kontroluje      │
│  skills pred každou úlohou, RAG hľadá        │
│  v knowledge base relevantné poznatky        │
└─────────────────────────────────────────────┘
```

**Moduly:**
- `agent/brain/learning.py` — orchestrácia (can_i_do, i_did_it, try_skill)
- `agent/brain/skills.py` — skills.json lifecycle (UNKNOWN→TESTING→LEARNED→MASTERED)
- `agent/brain/knowledge.py` — knowledge base (16 .md súborov v 5 kategóriách)
- `agent/memory/consolidation.py` — episodic → semantic/procedural transformácia

---

## Resource Odhad

**Lokálny compute (vrstvy 1-5):**
- MiniLM model: ~470MB RAM (jednorazovo pri štarte)
- Embedding výpočet: <100ms per query (i7-5500U)
- Semantic cache: <1MB RAM (max 200 entries, 1h TTL)
- RAG index: ~10MB RAM (16 knowledge docs)

**API volania (vrstvy 6-7, len keď treba):**
- Haiku: ~$0.001 per odpoveď
- Sonnet: ~$0.01 per odpoveď
- Opus: ~$0.05-0.20 per programovacia úloha
- S Max subscription: $0/volanie (zahrnuté v predplatnom)

**Reálna cache hit rate:** 5-10% (väčšina otázok je unikátna). Cache je bonus, nie základ optimalizácie. Hlavná úspora sú vrstvy 1-3 (dispatcher, regex, semantic router).

---

## Bezpečnostný Model

```
┌──────────────────────────────────┐
│         SECURITY LAYERS          │
│                                  │
│  ENV VARS                        │
│  └─ Len AGENT_VAULT_KEY          │
│     (master šifrovací kľúč)      │
│                                  │
│  VAULT (Fernet AES-128 + PBKDF2)│
│  └─ Všetky ostatné kľúče:       │
│     API tokens, wallet keys,    │
│     passwords                    │
│                                  │
│  SANDBOX (Docker, povinný)       │
│  └─ Kód beží v kontajneri:      │
│     256MB RAM, 1 CPU, no network │
│     read-only FS, 60s timeout   │
│                                  │
│  FINANCE (human-in-the-loop)     │
│  └─ Každá transakcia:           │
│     propose → approve → complete │
│                                  │
│  WATCHDOG                        │
│  └─ Heartbeaty, auto-restart,   │
│     CPU/RAM alerty, circuit break│
└──────────────────────────────────┘
```

---

## Systemd Service

```ini
[Unit]
Description=Agent Life Space
After=network.target

[Service]
Type=simple
WorkingDirectory=%h/agent-life-space
ExecStart=%h/agent-life-space/.venv/bin/python -m agent
Restart=always
RestartSec=10
Environment=PATH=%h/.local/bin:/usr/bin:/bin
# Minimum env vars — zvyšok je vo vaulte
Environment=AGENT_VAULT_KEY=xxx
Environment=TELEGRAM_BOT_TOKEN=xxx
Environment=TELEGRAM_USER_ID=xxx
Environment=CLAUDE_CODE_OAUTH_TOKEN=xxx

[Install]
WantedBy=default.target
```

```bash
# Inštalácia
mkdir -p ~/.config/systemd/user/
cp agent-life-space.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable agent-life-space
systemctl --user start agent-life-space

# Kontrola
systemctl --user status agent-life-space
journalctl --user -u agent-life-space -f
```

---

## Licencia

MIT — použi, uprav, zdieľaj.
