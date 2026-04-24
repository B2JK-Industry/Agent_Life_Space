# Pattern: SCRAPER

**Kedy použiť:** Goal vyžaduje pravidelné stiahnutie dát z webu/API a ich filtrovanie/normalizáciu (sreality.cz, bezrealitky.cz, scrapovanie eshopov, monitoring cien, jobs feed).

**Polyglot voľba:**
- **Python (default)** — rýchle prototypovanie, BeautifulSoup + httpx + asyncio
- **Rust + reqwest + scraper crate** — ak treba >100 req/s, dlhodobá efektivita
- **Go + colly** — ak treba multi-domain crawling s queue

## Fázy plánu

1. **ANALYZE** — preskúmaj cieľovú stránku/API: štruktúra HTML, dostupnosť oficiálneho API, robots.txt, rate limits, autentifikácia, paginácia, anti-bot opatrenia. Output: structured spec.

2. **DESIGN** — navrhni schému dát (Pydantic model), perzistenciu (SQLite v `agent/initiatives_data/<id>/store.db`), de-duplikáciu (hash IDs), filter/sort kritériá, cron interval.

3. **CODE** — napíš modul `agent/initiatives_data/<id>/scraper.py`:
   - `async def fetch_page(client, url) -> list[Item]`
   - `async def normalize(raw) -> Item`
   - `async def upsert(db, item) -> bool` (vracia True ak nový)
   - `async def run_once(db, filters) -> list[Item]` (returns new items)
   - Logging cez `structlog`, retry s exponenciálnym backoffom
   - Respect `robots.txt` (cache /robots.txt 24h)
   - User-Agent: `AgentLifeSpace/1.x (+https://github.com/B2JK-Industry/Agent_Life_Space)`

4. **TEST** — `tests/initiatives/<id>/test_scraper.py`:
   - Fixture HTML súbor (uložený lokálne) → parser test
   - Mock httpx client → end-to-end run_once test
   - De-dup test (rovnaký item dvakrát → druhý raz vráti []).

5. **VERIFY** — auto-review: prešlo všetko `pytest -q`? Pokrýva test happy + dedup + parse-error path?

6. **SCHEDULE** — zaregistruj recurring task v `TaskManager` s `cron_expression` (default `0 */6 * * *` = 4× denne; user-spec môže override).

7. **NOTIFY** — pri prvom úspešnom behu pošli majiteľovi sumár (počet nájdených, sample 3 položky).

8. **MONITOR** — iniciatíva zostáva v MONITORING režime; každý cron tick pri novom záchyte → notify (delta only).

## Acceptance criteria

- [ ] Modul stiahne minimálne 1 položku z testovacej fixture
- [ ] De-dup funguje (žiadne duplikáty v storei)
- [ ] Cron task je vytvorený a aktívny v TaskManager
- [ ] Pri novom záchyte príde notifikácia na owner_chat_id
- [ ] Žiadne private keys / API keys v kóde (len `os.environ` + vault)
- [ ] Žiadne porušenie `robots.txt`
- [ ] Rate limit >= 1s medzi requestami na ten istý host

## Edge cases na pokrytie

- HTTP 429 / 503 → exponenciálny backoff, max 3 retries
- HTML štruktúra zmenená → parser vyhodí typed error, scraper sa pauzne, upozorní majiteľa
- Sieť nedostupná → log + retry pri ďalšom cron tiku, žiadny crash
- Veľký response (>5MB) → odmietni (možná zmena stránky / chyba)

## Poznámka pre real estate špecificky

- **sreality.cz** má JSON API: `https://www.sreality.cz/api/cs/v2/estates?...` — preferuj toto pred HTML scrapingom
- **bezrealitky.cz** má GraphQL endpoint
- **reality.idnes.cz** vyžaduje HTML scraping (no public API)
- Filter parametre Praha 2+kk pod 8M Kč: `category_main_cb=1&category_sub_cb=3&locality_region_id=10&czk_price_summary_order2=8000000`
