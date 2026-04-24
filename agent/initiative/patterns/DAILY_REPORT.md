# Pattern: DAILY_REPORT

**Kedy použiť:** Goal "každý deň o X mi pošli súhrn Y" — kombinácia SCHEDULE + AGGREGATE + NOTIFY.

## Fázy plánu

1. **ANALYZE** — definuj zdroje dát, agregačné okno (24h, 7d), formát výstupu.
2. **DESIGN** — query / fetch logika pre každý zdroj, formát Markdown sumáru.
3. **CODE** — `report.py` modul s `async def build_report(window: timedelta) -> str`.
4. **TEST** — fixture data → očakávaný report.
5. **SCHEDULE** — cron (default `0 8 * * *` = 8:00 ráno).
6. **NOTIFY** — denne v plánovanom čase pošle report.
7. **MONITOR** — initiative zostáva v MONITORING režime.

## Acceptance criteria

- [ ] Report nikdy neprekročí 4000 znakov (Telegram limit)
- [ ] Pri prázdnych dátach pošle "žiadne nové udalosti za posledných X" (nie crash)
- [ ] Schopný retry pri chybe (max 3×, potom alert)

## Príklady

- "Každý deň o 8 mi pošli koľko nových bytov scraper našiel"
- "V piatok mi pošli týždenný súhrn aktivity ALS"
- "Každú nedeľu prehľad GitHub PRs ktoré čakajú na review"
