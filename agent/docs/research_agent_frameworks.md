# Research: Agent Frameworks — Poučenia pre Agent Life Space

Stav: aktualizované 2026-03-24 po code review a opravách.

---

## Porovnanie s existujúcimi agentmi

### Auto-GPT
**Problém:** Infinite loops, nekontrolované výdavky, halucinácie, žiadna pamäť medzi sessions.
**U nás:**
- ✅ Základ: TTL na správach, max retries, dead letter queue
- ✅ Základ: Budget limity v FinanceTracker (checked, nie enforced — human rozhoduje)
- ✅ Základ: Persistentná pamäť (SQLite)
- ⚠️ Čiastočné: Brain klasifikuje algo vs LLM, ale cez keyword heuristiku — nie robustný classifier

### BabyAGI
**Problém:** Task explosion, žiadna validácia, flat pamäť.
**U nás:**
- ✅ Základ: Dependency tracking, task scoring je algoritmický
- ✅ Základ: 4-vrstvová pamäť s decay mechanizmom
- ⚠️ Čiastočné: Scoring formula je deterministická, ale kvalita závisí od vstupných metrík

### CrewAI / AutoGen
**Problém:** Agent confusion, nekonečné konverzácie, vysoká spotreba tokenov.
**U nás:**
- ✅ Základ: Message protocol s TTL umožňuje budúcu multi-agent komunikáciu
- ✅ Základ: Priority systém
- ❌ Zatiaľ: Jeden agent, nie multi-agent

### LangGraph
**Problém:** Komplexnosť, vendor lock-in, memory leaks.
**U nás:**
- ✅ Základ: State machine pre tasky
- ✅ Základ: Human-in-the-loop pre finance
- ✅ Základ: Žiadny vendor lock-in

### Open Interpreter
**Problém:** Command injection, žiadny sandbox, rm -rf incidenty.
**U nás:**
- ✅ Základ: Watchdog monitoruje heartbeaty modulov
- ✅ Základ: Job runner s timeoutom
- ❌ Chýba: Sandbox pre systémové príkazy

---

## Čo sme implementovali — reálny stav

### Funguje spoľahlivo (otestované):
- JSON message protocol s immutabilnými správami, TTL, priority
- Message router s priority queue, dead letters, exponential backoff retry
- Job runner s hard timeout, circuit breaker, bounded history
- 4-vrstvová pamäť s SQLite persistence, decay, relevance scoring
- Task manager s dependency tracking, priority scoring
- Watchdog s heartbeat monitoring, restart cooldown, alert deduplication
- Encrypted vault s PBKDF2 key derivation, audit trail
- Finance tracker s approval flow (checked, nie enforced budget)
- Structured logger s secret redaction

### Funguje, ale s obmedzeniami:
- **Brain/Decision Engine** — keyword heuristika, nie robustný classifier. Cache funguje, ale confidence hodnoty sú heuristické odhady, nie kalibrované pravdepodobnosti
- **LLM Router** — template + schema validácia znižuje halucinácie formátu, ale NEodstraňuje halucinácie obsahu. Chýba fact-check layer
- **Watchdog** — monitoruje heartbeaty, NIE OS-level procesy. "Unresponsive" znamená "no heartbeat", nie "process exited"
- **Finance** — budget je checked, nie enforced. Agent môže navrhnúť aj nad-budget výdavok (správne — human rozhoduje)

### Zatiaľ neimplementované:
- Sandbox pre systémové príkazy
- Rate limiting pre LLM volania
- Jitter na exponential backoff
- Fact-checking layer (porovnanie LLM output s pamäťou)
- Multi-model routing (lacný model na jednoduché)
- Learning loop (agent sa učí z výsledkov)
- Social modul, Projects modul
- Deploy na server

---

## Bezpečnostné zraniteľnosti — stav

| Hrozba | Stav | Detail |
|---|---|---|
| Prompt injection | ⚠️ Čiastočne riešené | Template prompty, ale nie prompt firewall |
| Excessive agency | ✅ Základ | Finance vyžaduje approval, watchdog limits |
| Wallet draining | ✅ Riešené | Žiadny wallet prístup, budget tracking |
| Data exfiltration | ⚠️ Čiastočne | Vault šifruje, logger redactuje, ale chýba network sandbox |
| Infinite loops | ✅ Základ | TTL, max retries, circuit breaker |
| Zombie processes | ⚠️ Čiastočne | Heartbeat monitoring, nie OS-level kill |
| Supply chain | ⚠️ Čiastočne | Pinned verzie, ale treba audit |

---

## Anti-halucinačné techniky — reálny stav

| Technika | Stav |
|---|---|
| Structured output (JSON mode) | ✅ Implementované |
| JSON Schema validácia | ✅ Implementované |
| Retry s chybovou správou | ✅ Implementované (max 2 retries) |
| Template prompty | ✅ Implementované |
| Temperature 0.0 default | ✅ Implementované |
| Fact-checking cez pamäť | ❌ Neimplementované |
| Content hallucination detection | ❌ Neimplementované |
| Fallback na algoritmus | ✅ Základ (keyword heuristika) |

---

## Ďalšie kroky (prioritizované)

### Kritické (pred deploy):
1. Sandbox pre systémové príkazy
2. Rate limiting pre LLM volania
3. Network isolation audit

### Dôležité (po deploy):
1. Fact-check layer pre LLM odpovede
2. Robustnejší task classifier (nahradiť keyword matching)
3. Jitter na retry backoff
4. Social modul
5. Projects modul

### Neskôr:
1. Multi-model routing
2. Learning loop
3. Rust moduly (PyO3)
4. Web dashboard
5. Earning pipeline
