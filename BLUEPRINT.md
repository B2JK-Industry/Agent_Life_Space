# Agent Life Space — Blueprint

Návod ako si vytvoriť vlastného autonómneho agenta. Daj toto svojmu botovi/agentovi a on pochopí architektúru.

---

## Čo to je

Self-hosted AI agent ktorý:
- Komunikuje cez Telegram
- Má vlastnú pamäť, skills, knowledge base
- Učí sa z toho čo robí
- Minimalizuje LLM tokeny cez 7-vrstvový cascade
- Programuje, scrapuje web, spúšťa Docker kontajnery
- Má šifrovaný vault pre citlivé dáta

---

## Požiadavky

**Hardware (minimum):**
- 4-core CPU, 8GB RAM, 100GB disk
- GPU (voliteľné) — pre OCR, image processing

**Software:**
- Ubuntu 22.04+ (alebo iný Linux)
- Python 3.12+
- Docker (voliteľné, pre sandbox)
- Git

**Účty:**
- Telegram Bot (cez @BotFather)
- Claude Max subscription (pre Claude Code CLI)
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

# 4. Nastav env premenné
export TELEGRAM_BOT_TOKEN="tvoj-telegram-token"
export TELEGRAM_USER_ID="tvoj-telegram-id"
export CLAUDE_CODE_OAUTH_TOKEN="tvoj-claude-token"
export GITHUB_TOKEN="tvoj-github-token"  # voliteľné

# 5. Spusti
python -m agent
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
│  1. /commands      → priame odpovede (0 tokenov)    │
│  2. Dispatcher     → regex patterny (0 tokenov)     │
│  3. Semantic Router→ MiniLM embeddingy (0 tokenov)  │
│  4. Semantic Cache → podobná otázka? (0 tokenov)    │
│  5. Self-RAG       → knowledge base (0 tokenov)     │
│  6. Haiku/Sonnet   → jednoduché/konverzácia         │
│  7. Opus           → programovanie                   │
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

Cieľ: **odpovedať čo najlacnejšie.** LLM sa volá len keď interné moduly nestačia.

### Vrstva 1: Slash commands (0 tokenov)
```python
# telegram_handler.py
if text.startswith("/"):
    handlers = {"/status": ..., "/health": ..., "/tasks": ...}
    return await handlers[command](args)
```
Deterministické, priamo z modulov.

### Vrstva 2: InternalDispatcher (0 tokenov)
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

### Vrstva 3: Semantic Router (0 tokenov)
```python
# brain/semantic_router.py
# MiniLM-L12-v2 (470MB, slovenčina + angličtina)
intent, confidence = classify_intent("ako sa cítiš?")
# → ("status", 0.78) → dispatch interne
```
Embedding model detekuje intent aj keď je otázka formulovaná inak.

### Vrstva 4: Semantic Cache (0 tokenov)
```python
# memory/semantic_cache.py
cached = cache.lookup("koľko mám úloh?")  # cosine > 0.90 → return cached
```
Ak John už odpovedal na podobnú otázku, vráti cache.

### Vrstva 5: Self-RAG (0 tokenov alebo kontext pre LLM)
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
Uprav `agent/brain/skills.py` — pridaj skills relevantné pre tvojho agenta. Predvolených je 20.

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

1. **LLM je posledná možnosť** — najprv hľadaj internú odpoveď
2. **Determinizmus kde sa dá** — scoring, routing, scheduling algoritmicky
3. **Pamäť je základ** — 4 typy, konsolidácia, nie len log udalostí
4. **Skills sa učia** — UNKNOWN → LEARNED → MASTERED, auto-testing
5. **Bezpečnosť** — vault pre kľúče, approval pre financie, timeout na všetkom
6. **Stručnosť** — agent odpovedá krátko, tokeny stoja peniaze

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
Environment=TELEGRAM_BOT_TOKEN=xxx
Environment=TELEGRAM_USER_ID=xxx
Environment=CLAUDE_CODE_OAUTH_TOKEN=xxx
Environment=GITHUB_TOKEN=xxx
Environment=AGENT_VAULT_KEY=xxx

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
