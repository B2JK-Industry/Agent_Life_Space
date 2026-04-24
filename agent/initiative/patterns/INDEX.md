# Pattern Library — Index

Patterny, ktoré InitiativePlanner berie do úvahy pri rozkladaní NL goalu na štruktúrovaný plán. Každý pattern má vlastný `.md` súbor s fázami, acceptance criteria a edge cases.

| Pattern ID | Súbor | Stručne |
|---|---|---|
| `scraper` | SCRAPER.md | Stiahnuť dáta z webu/API + persistovať + de-dupovať |
| `notifier` | NOTIFIER.md | Doručiť správu majiteľovi (Telegram/email/webhook) |
| `monitor` | MONITOR.md | SCRAPER + NOTIFIER orchestrácia (long-running) |
| `code_review` | CODE_REVIEW.md | Analyzovať repo/PR a vyprodukovať report |
| `rest_api` | REST_API.md | Postaviť REST endpoint / mikroservis |
| `daily_report` | DAILY_REPORT.md | SCHEDULE + AGGREGATE + NOTIFY (denný/týždenný report) |

## Ako pridať nový pattern

1. Vytvor `<NAME>.md` v `agent/initiative/patterns/`
2. Pridaj riadok do tabuľky vyššie (alphabeticky)
3. Štruktúra súboru:
   - **Kedy použiť** (1-2 vety)
   - **Polyglot** (jazyk voľby)
   - **Fázy plánu** (číslovaný zoznam s `kind` mapping)
   - **Acceptance criteria** (checklist)
   - **Edge cases** (čo nesmie spadnúť)

## Princípy patternov

- **Konkrétne, nie abstraktné** — spomínaj konkrétne knižnice, file paths, premenné
- **Explicitné acceptance criteria** — verifier sa o ne opiera
- **Polyglot voľba** — naznač kedy *neísť* defaultným jazykom
- **Edge cases** — čo môže pokaziť production beh
