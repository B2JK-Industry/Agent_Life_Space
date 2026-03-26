# Agent Life Space — hlavný projekt
_Kategória: projects | Tags: main, agent, john | Aktualizované: 2026-03-26_

## Čo to je
Autonómny AI agent (John) bežiaci na vlastnom serveri. Nie chatbot — bytosť s pamäťou, úlohami, rozpočtom a zdravím.

## Stav
- Verzia: 3.0.0
- Fáza: aktívny vývoj, production-ready core
- Všetky moduly: healthy
- Testy: 696+ (unit + integration + e2e + security audit)

## Čo funguje
- **LLM:** Provider-agnostic (Claude CLI, Anthropic API, OpenAI, Ollama)
- **Tool use:** 10 nástrojov cez function calling (store_memory, run_code, run_tests...)
- **ToolUseLoop:** Multi-turn konverzácia kde LLM volá agentove funkcie
- **Pamäť:** 4 typy, konsolidácia, RAG retrieval, sémantický cache, per-chat kontext
- **Persistent konverzácia:** SQLite, prežije reštarty, per-chat session ID
- **Sandbox:** Docker-first, SandboxExecutor s iterate (run→error→fix→re-run)
- **Self-testing:** Agent píše kód + testy, spúšťa pytest v sandboxe
- **Komunikácia:** Telegram + Agent API + Channel abstrakcia pre ďalšie kanály
- **Anti-konfabulácia:** Runtime facts injection, ConfabulationTracker
- **Bezpečnosť:** RequestContext, safe mode, PID lockfile, 50 security audit testov
- **Finance:** Human-in-the-loop approval, dead man switch
- **Learning:** Feedback loop, model eskalácia, prompt augmentation
- **Cron:** 7 background jobov (health, memory, morning report, task review...)
- **Git/GitHub:** B2JK-Industry, CI/CD, releases

## Čo chýba / plánované
- Email + X.com účty (Daniel musí vytvoriť)
- Earning modul (agent hľadá prácu, navrhne, Daniel schváli)
- Discord, Slack kanály (Channel ABC pripravené)
- Vlastná iniciatíva (GoalManager, proaktívne konanie)

## Cieľ
John sa má stať plne autonómnym agentom, ktorý vie riešiť úlohy, zarábať, učiť sa, a komunikovať cez viaceré kanály.
