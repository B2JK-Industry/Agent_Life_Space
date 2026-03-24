# Správa pamäte
_Kategória: skills | Tags: memory, sqlite, store | Aktualizované: 2026-03-24_

## Typy pamäte
1. **Episodic** — čo sa stalo (udalosti, konverzácie)
2. **Semantic** — fakty a znalosti
3. **Procedural** — ako robiť veci (postupy)
4. **Working** — krátkodobá, aktuálny kontext

## Implementácia
- SQLite databáza: `agent/memory/memories.db`
- Modul: `agent/memory/store.py`
- API: `store()`, `query()`, `get_recent()`

## Aktuálny stav
- 96 spomienok (všetky episodic)
- Treba budovať semantic a procedural pamäť
