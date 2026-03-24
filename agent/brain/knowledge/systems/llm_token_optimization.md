# LLM Token Optimization Research

Dátum: 2026-03-24
Účel: Architektúra agenta s minimálnym využitím LLM tokenov

---

## 1. ROUTING BEZ LLM (Semantic Router)

### Čo to je
Namiesto volania LLM na klasifikáciu intentu sa user query embeduje do vektora a porovná s predpripravenými vektormi intentov cez cosine similarity.

### Ako to znižuje LLM usage
- Routing je klasifikačný problém, nie generačný — nepotrebuje LLM
- 50x rýchlejšie, 100x lacnejšie ako LLM routing
- Sub-penny per 10k queries vs ~$0.65 per 10k LLM queries

### Kaskádový vzor (ODPORÚČANÝ)
1. **Keyword/regex filter** — jasné prípady (napr. "koľko je hodín", "/status")
2. **Semantic router** — embedding similarity pre známe intenty (92-96% presnosť)
3. **LLM fallback** — len pre nejednoznačné alebo neznáme prípady

### Implementácia pre Johna
- Model: `paraphrase-multilingual-MiniLM-L12-v2` (384 dim, 50+ jazykov vrátane slovenčiny)
- ~470MB RAM, rýchly inference na CPU
- In-memory vector store (nie DB — máme <20 intentov)
- Knižnica: `sentence-transformers` alebo `semantic-router`

### Zložitosť: NÍZKA
- Definovať intenty + 5-10 príkladov pre každý
- Pár hodín implementácie
- Funguje na 8GB RAM bez problémov

---

## 2. PROMPT CACHING

### Čo to je
Anthropic prompt caching: ak system prompt zostáva rovnaký medzi volaniami, cached input tokeny majú 90% zľavu.

### Ako to znižuje LLM usage
- 90% zľava na cached input tokeny
- System prompt + tool definície sa cachujú automaticky
- Reálna úspora 20-30% z celkového účtu

### Implementácia pre Johna
- Štruktúrovať system prompt ako stabilnú prefix + dynamický suffix
- Tool definície vždy na začiatku (cache-friendly)
- Anthropic API to robí automaticky pri konzistentnom prefixe

### Zložitosť: VEĽMI NÍZKA
- Len reorganizovať prompt štruktúru
- Žiadny extra kód

---

## 3. SEMANTIC CACHE

### Čo to je
Cache LLM odpovedí indexovaný podľa sémantickej podobnosti query (nie exact match). Nový query sa embeduje a porovná s cache — ak similarity > threshold (0.85-0.95), vráti cached odpoveď.

### Ako to znižuje LLM usage
- Elimínuje duplicitné alebo podobné LLM volania
- Zvlášť efektívne pre opakujúce sa otázky

### Implementácia pre Johna
- Embedding model: ten istý `MiniLM-L12-v2` ako pre routing
- Storage: SQLite + numpy vektory (alebo FAISS ak treba rýchlejšie)
- Threshold: 0.90 pre začiatok, tunovať podľa false positive rate
- TTL: cache expirácia podľa typu odpovede
- Knižnica: vlastná implementácia (GPTCache je overkill)

### Zložitosť: STREDNÁ
- Embedding pipeline už máme z routingu
- Treba: cache invalidácia, TTL, monitoring hit rate

---

## 4. CONTEXT WINDOW MANAGEMENT

### Čo to je
Namiesto posielania celej konverzácie/histórie do LLM, posielať len relevantné časti.

### Techniky
- **Sliding window** — len posledných N správ
- **Progressive summarization** — staršie správy sumarizovať (ale to je ďalšie LLM volanie!)
- **Selective tool output** — z tool output extrahovať len relevantné časti
- **RAG namiesto stuffing** — retrieval relevantných chunkov namiesto celých dokumentov

### Implementácia pre Johna
- Max 5 posledných správ v kontexte
- Tool output: truncate na max 500 tokenov, extrahovať kľúčové info programaticky
- Pamäť (memories.db): retrieval cez embedding search, nie dump celej DB

### Zložitosť: STREDNÁ
- Treba implementovať context builder s budget awareness

---

## 5. MODEL ROUTING (Lacný vs Drahý model)

### Čo to je
Jednoduché úlohy → lacný model, komplexné → drahý model.

### Cenový rozdiel (Anthropic 2026)
- Claude Haiku: $0.25/1M input, $1.25/1M output
- Claude Sonnet: $3/1M input, $15/1M output
- Claude Opus: $15/1M input, $75/1M output
- Rozdiel: 12-60x medzi Haiku a Opus

### Routing pravidlá pre Johna
- **Haiku**: klasifikácia, extrakcia, formátovanie, jednoduché Q&A, sumarizácia
- **Sonnet**: plánovanie, code generation, komplexné rozhodovanie
- **Opus**: len pre kritické rozhodnutia (ak vôbec)

### Implementácia
- Decision v `llm_router.py` — podľa task type vybrať model
- Fallback: ak Haiku odpoveď má nízku confidence → retry so Sonnet

### Zložitosť: NÍZKA
- Len routing logika v existujúcom module

---

## 6. PROMPT COMPRESSION (LLMLingua)

### Čo to je
Microsoft LLMLingua komprimuje prompty odstránením redundantných tokenov. Dosahuje 20x kompresiu s minimálnou stratou kvality.

### Ako funguje
- Malý model (BERT-level) klasifikuje tokeny na "zachovať" vs "zahodiť"
- LLMLingua-2: 3-6x rýchlejší, trénovaný cez data distillation z GPT-4
- Integrovaný do LangChain a LlamaIndex

### Pre Johna
- Užitočné hlavne pre RAG context a dlhé tool outputy
- Model: `llmlingua-2-bert-base-multilingual-cased-meetingbank-coarse`
- ~440MB RAM

### Zložitosť: STREDNÁ
- Inštalácia a integrácia do pipeline
- Treba testovať kvalitu na slovenských textoch

---

## 7. LOKÁLNY MALÝ MODEL (Ollama)

### Čo to je
Lokálne bežiaci SLM pre jednoduché úlohy namiesto API volaní.

### Modely pre 8GB RAM
- **Phi-4-mini (3.8B)**: 3.5GB RAM pri Q4_K_M, MMLU 68.5
- **Llama 3.2 3B**: dobrý na routing, klasifikáciu, jednoduché úlohy
- **Mistral 7B**: tesne sa zmestí, pomalší

### Pre Johna
- Ollama na jednoduché úlohy: sumarizácia logov, formátovanie, extrakcia
- Ušetrí API volania pre rutinné operácie
- POZOR: 8GB RAM = embedding model + lokálny LLM + Python = tesné

### Zložitosť: STREDNÁ-VYSOKÁ
- Ollama inštalácia je jednoduchá
- RAM management je kritický na 8GB systéme
- Treba benchmark či sa to oplatí vs Haiku API

---

## 8. RULE-BASED DECISION ENGINE

### Čo to je
Algoritmické rozhodovanie pre predvídateľné situácie bez LLM.

### Vzory
- **Decision tree**: if/elif/else pre známe scenáre
- **Regex matching**: parsovanie štruktúrovaných príkazov
- **Template responses**: predpripravené odpovede pre FAQ
- **Workflow automation**: cron jobs, scheduled tasks bez LLM

### Pre Johna (decision_engine.py)
- `/status`, `/tasks`, `/budget` → priamo, bez LLM
- Systémové alerty (disk full, high CPU) → template správy
- Scheduled reports → generovať programaticky
- Git operácie → priamo cez subprocess

### Zložitosť: NÍZKA
- Už máme `decision_engine.py` na toto
- Rozšíriť o viac rule-based ciest

---

## 9. SELF-RAG (Adaptive Retrieval)

### Čo to je
Namiesto always-retrieve alebo never-retrieve, model sa rozhodne či retrieval potrebuje.

### Ako to funguje
- "Reflection tokens" rozhodnú: potrebujem externe info?
- Ak áno → retrieve → validate relevance → generate
- Ak nie → generate priamo

### Pre Johna
- Pred LLM call: je odpoveď v knowledge base? → semantic search
- Ak match > 0.92 → vráť priamo bez LLM
- Ak match 0.75-0.92 → retrieval + LLM
- Ak match < 0.75 → LLM bez retrieval

### Zložitosť: STREDNÁ
- Reuse embedding model z routing
- Integrácia s memory store

---

## 10. BATCH PROCESSING

### Čo to je
Anthropic/OpenAI batch API: 50% zľava za ne-urgentné úlohy (24h turnaround).

### Pre Johna
- Nočné reporty
- Bulk analýza logov
- Spracovanie knowledge base updates
- Review accumulated data

### Zložitosť: NÍZKA
- Anthropic Batch API je straightforward

---

## ODPORÚČANÁ PRIORITA IMPLEMENTÁCIE

### Fáza 1 — Quick Wins (1-2 dni)
1. **Rule-based decision engine** — rozšíriť existujúci modul
2. **Prompt caching** — reorganizovať system prompt
3. **Model routing** (Haiku vs Sonnet) — v llm_router.py
4. **Context window management** — sliding window + truncation

Očakávaná úspora: **50-60%** tokenov

### Fáza 2 — Semantic Layer (3-5 dní)
5. **Semantic router** — MiniLM embedding + intent matching
6. **Semantic cache** — cache LLM odpovedí
7. **Self-RAG** — adaptive retrieval z knowledge base

Očakávaná úspora: **ďalších 20-30%** tokenov

### Fáza 3 — Advanced (1-2 týždne)
8. **Prompt compression** — LLMLingua pre dlhé kontexty
9. **Lokálny model** — Ollama pre rutinné úlohy (ak RAM dovolí)
10. **Batch processing** — nočné úlohy

Očakávaná úspora: **ďalších 10-15%** tokenov

### Celková očakávaná úspora: 70-80% LLM tokenov

---

## ARCHITEKTÚRA PRE JOHNA — DECISION FLOW

```
User Message
    │
    ├─ [1] Regex/Keyword match? ──YES──→ Direct response (0 tokens)
    │
    ├─ [2] Slash command? ──YES──→ Execute tool directly (0 tokens)
    │
    ├─ [3] Semantic router ──KNOWN INTENT──→
    │       │                                  ├─ Simple? → Haiku
    │       │                                  └─ Complex? → Sonnet
    │       │
    │       └─ UNKNOWN INTENT ──→ [4] Semantic cache hit?
    │                                   │
    │                                   ├─YES──→ Return cached (0 tokens)
    │                                   │
    │                                   └─NO──→ [5] RAG retrieval needed?
    │                                              │
    │                                              ├─YES──→ Retrieve + Sonnet
    │                                              └─NO──→ Sonnet direct
    │
    └─ Response → Cache for future queries
```

---

## ZDROJE

### Agent Architecture & Cost
- [AI Agent Cost Optimization Guide 2026](https://moltbook-ai.com/posts/ai-agent-cost-optimization-2026)
- [Building Effective Agents — Anthropic](https://www.anthropic.com/research/building-effective-agents)
- [Utility-Guided Agent Orchestration](https://arxiv.org/abs/2603.19896)

### Semantic Routing
- [Intent Router with Semantic Similarity](https://blog.getzep.com/building-an-intent-router-with-langchain-and-zep/)
- [Intent Recognition & Auto-Routing](https://gist.github.com/mkbctrl/a35764e99fe0c8e8c00b2358f55cd7fa)
- [vLLM Semantic Router](https://blog.vllm.ai/2025/09/11/semantic-router.html)
- [Fast Intent Classification via Statistical Analysis](https://openreview.net/forum?id=UMuVvvIEvA)

### Multilingual NLP
- [paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)
- [Slavic BERT NER](https://github.com/deeppavlov/Slavic-BERT-NER)
- [Slovak NLP Resources](https://github.com/slovak-nlp/resources)
- [Slavic NLP Workshop 2025](https://aclanthology.org/volumes/2025.bsnlp-1/)

### RAG Optimization
- [RAG Best Practices 2025](https://gradientflow.substack.com/p/best-practices-in-retrieval-augmented)
- [Enhancing RAG: Best Practices Study](https://arxiv.org/abs/2501.07391)

### Prompt Compression
- [LLMLingua — Microsoft](https://www.llmlingua.com/)
- [LLMLingua-2 Paper](https://arxiv.org/abs/2403.12968)

### Semantic Caching
- [GPT Semantic Cache Paper](https://arxiv.org/html/2411.05276v3)
- [Prompt Caching vs Semantic Caching — Redis](https://redis.io/blog/prompt-caching-vs-semantic-caching/)
- [GPTCache — GitHub](https://github.com/zilliztech/GPTCache)

### Local Models
- [Best AI Models for 8GB RAM 2026](https://localaimaster.com/blog/best-local-ai-models-8gb-ram)
- [Self-Hosted LLM Guide 2026](https://blog.premai.io/self-hosted-llm-guide-setup-tools-cost-comparison-2026/)

### Frameworks
- [LangGraph vs CrewAI vs AutoGPT 2026](https://agixtech.com/langgraph-vs-crewai-vs-autogpt/)
- [AI Agent Routing Best Practices](https://arize.com/blog/best-practices-for-building-an-ai-agent-router/)
- [Anthropic Claude Pricing 2026](https://platform.claude.com/docs/en/about-claude/pricing)
