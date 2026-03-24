# Správa pamäte
_Kategória: skills | Tags: memory, sqlite, store | Aktualizované: 2026-03-24_

## Typy pamäte
1. **Episodic** — čo sa stalo (udalosti, konverzácie)
2. **Semantic** — fakty a znalosti
3. **Procedural** — ako robiť veci (postupy)
4. **Working** — krátkodobá, aktuálny kontext

## Implementácia
- SQLite databáza: `agent/memory/memories.db`
- `agent/memory/store.py` — hlavné úložisko, API: `store()`, `query()`, `get_recent()`
- `agent/memory/consolidation.py` — konsolidácia a zhrnutie spomienok
- `agent/memory/rag.py` — RAG retrieval pre kontextové vyhľadávanie
- `agent/memory/semantic_cache.py` — sémantický cache pre opakované otázky

## Aktuálny stav
- 466 spomienok (väčšinou episodic)
- Konsolidácia, RAG a sémantický cache implementované
- Treba budovať semantic a procedural pamäť
