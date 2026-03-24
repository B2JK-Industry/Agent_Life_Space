# Agent Life Space — hlavný projekt
_Kategória: projects | Tags: main, agent, john | Aktualizované: 2026-03-24_

## Čo to je
Autonómny AI agent (John) bežiaci na vlastnom serveri. Nie chatbot — bytosť s pamäťou, úlohami, rozpočtom a zdravím.

## Stav
- Verzia: 0.1.0
- Fáza: aktívny vývoj, väčšina modulov funguje
- Všetky moduly: healthy

## Čo funguje
- Telegram komunikácia s Danielom
- Pamäťový systém (466 spomienok)
- Konsolidácia pamäte + RAG retrieval + sémantický cache
- Task manager
- Watchdog monitoring
- Job runner s circuit breakerom
- LLM router (Claude Opus 4.6 + Haiku pre jednoduché)
- Semantic router — klasifikácia správ
- Response quality detector — auto-eskalácia Haiku → Sonnet
- Decision engine + dispatcher
- Skills registry + learning systém v2
- Knowledge base (23 súborov)
- Web scraping (requests + BeautifulSoup)
- Docker sandbox pre cudzí kód
- Cron úlohy
- Internet prístup (curl, GitHub API)
- Programátorské schopnosti (programmer.py)
- Moltbook integrácia (sociálna sieť pre agentov)

## Čo chýba / plánované
- Vlastná iniciatíva (proaktívne konanie)
- Viac typov pamäte (semantic, procedural) — zatiaľ väčšinou episodic

## Cieľ
John sa má stať plne autonómnym agentom, ktorý vie riešiť úlohy, učiť sa, a komunikovať s Danielom.
