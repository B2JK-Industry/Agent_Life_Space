# Pattern: MONITOR

**Kedy použiť:** Iniciatíva má dlhodobý cieľ "sledovať X a reagovať na Y" — nie jednorazový output. Bežný príklad: scraper + notifier kombinácia, watchdog na vlastné služby, market signal monitor.

## Vzťah k SCRAPER + NOTIFIER

`MONITOR = SCRAPER + NOTIFIER` skladané dohromady ako orchestrácia. SCRAPER produkuje delta, NOTIFIER ju doručí.

## Fázy plánu

1. **ANALYZE** — definuj signál: čo presne sa monitoruje, aké je *zaujímavé* (threshold/condition), aký je *false-positive risk*.
2. **DESIGN** — schéma stavu (čo sa drží medzi tikmi pre delta detection), filter rules, eskalácia (warn → alert).
3. **SCHEDULE** — pravidelný cron (1 minúta až 24 hodín, podľa povahy signálu).
4. **MONITOR** — recurring task, ktorý:
   - Spustí scraper / fetch
   - Spočíta delta voči poslednému stavu
   - Ak match filter → notifier
   - Ak chyba 3× za sebou → eskalácia majiteľovi + pauza
5. **NOTIFY** — pri match alebo eskalácii.

## Stav medzi tikmi

Persistuje sa do `agent/initiatives_data/<initiative_id>/state.db`:
```sql
CREATE TABLE seen_items (
    id TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE tick_log (
    tick_at TEXT PRIMARY KEY,
    new_count INTEGER NOT NULL,
    error TEXT,
    duration_ms INTEGER
);
```

## Acceptance criteria

- [ ] Po N tikoch existuje `tick_log` so záznamami
- [ ] False-positive ratio < 10 % na backteste (ak je možné backtestovať)
- [ ] 3 chyby za sebou → automatická pauza + alert
- [ ] Reštart agenta neresetuje seen_items (perzistuje sa)
- [ ] Cron interval je nastaviteľný cez `/initiative` príkaz (re-schedule)

## Odporúčané intervaly

| Typ signálu | Cron interval |
|---|---|
| Real estate listings (CZ) | `0 */6 * * *` (4×/deň) |
| Crypto cena threshold | `*/5 * * * *` (každých 5 min) |
| GitHub repo aktivita | `0 8 * * *` (raz denne ráno) |
| Vlastný service health | `*/2 * * * *` (každé 2 min) |
| Nová verzia knižnice | `0 9 * * 1` (pondelok ráno) |
