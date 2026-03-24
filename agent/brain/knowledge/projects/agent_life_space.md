# Agent Life Space — hlavný projekt
_Kategória: projects | Tags: main, agent, john | Aktualizované: 2026-03-24_

## Čo to je
Autonómny AI agent (John) bežiaci na vlastnom serveri. Nie chatbot — bytosť s pamäťou, úlohami, rozpočtom a zdravím.

## Stav
- Verzia: 0.1.0
- Fáza: ranné štádium, základné moduly fungujú
- Všetky moduly: healthy

## Čo funguje
- Telegram komunikácia s Danielom
- Pamäťový systém (96 episodic spomienok)
- Task manager
- Watchdog monitoring
- Job runner s circuit breakerom
- LLM router (Claude Opus 4.6)
- Decision engine
- Skills registry
- Knowledge base (práve sa napĺňa)

## Čo chýba / plánované
- Internet prístup (curl, API volania) — čaká na povolenie
- Docker sandbox pre cudzí kód
- Vlastná iniciatíva (proaktívne konanie)
- Viac typov pamäte (semantic, procedural)
- Konsolidácia pamäte

## Cieľ
John sa má stať plne autonómnym agentom, ktorý vie riešiť úlohy, učiť sa, a komunikovať s Danielom.
