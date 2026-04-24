# Claude Code Task: Real Estate Listing Analyzer MVP

Pracuješ v samostatnom projekte pre `Real Estate Listing Analyzer MVP`.

Ak ešte neexistuje nový repozitár alebo chýbajú GitHub/Telegram tokeny, tak:
- nevymýšľaj deploy
- nevypytuj sa opakovane na tokeny
- urob lokálny MVP foundation slice

Najprv si načítaj:
- `docs/strategy/MASTER_SOURCE_OF_TRUTH.md`
- `docs/strategy/REAL_ESTATE_LISTING_ANALYZER_MVP.md`

## Cieľ

Postav lokálny MVP základ pre analytický nástroj nad realitnými listingami so
zameraním na `sreality.cz` ako prvý adapter.

Nejde o:
- production deploy
- live Telegram integráciu
- hotové alerting workflowy

Ide o:
- poctivý foundation slice
- znovupoužiteľné modely
- per-analysis konfiguráciu
- deterministic testy

## Produktové pravidlá

Každá analýza musí mať vlastné filtre.

Nesmie existovať jeden globálny budget alebo jedna globálna lokalita.

Musí byť možné paralelne držať napríklad:
- `Brno 3+kk do 5.5M`
- `Praha 9 2+1 2.8M–4.2M`

bez toho, aby sa navzájom prepísali.

## MVP Scope

Implementuj:

1. Normalized domain model
- listing model
- analysis profile model
- analysis run model

2. Source adapter abstraction
- base interface
- prvý adapter pre `sreality.cz`

3. Local persistence
- SQLite
- per-analysis storage
- results/runs storage

4. Analysis service
- search/fetch
- normalize
- filter
- price-per-m2
- simple ranking
- formatted result output

5. CLI-friendly entrypoint alebo service-level API
- nie nutne finálny Telegram bot
- ale tak, aby sa Telegram neskôr dal napojiť bez refactoru

6. Fixture-based tests
- bez závislosti na live sieti

## Dôležité constraints

- nezačni deployom
- nezačni GitHub automation
- nezačni Telegram token setupom
- nezačni Playwright-only riešením, ak stačí HTTP + parser
- nerob broad framework
- sprav malý, čistý, testovateľný základ

## Odporúčaný layout

```text
app/
  sources/
  domain/
  storage/
  services/
  interfaces/
tests/
  fixtures/
```

Ak zvolíš inú štruktúru, nech je rovnako jasná.

## Minimálne fields pre Listing

- source
- external_id
- title
- url
- price_czk
- area_m2
- location_text
- disposition
- property_type
- offer_type
- state
- summary
- raw_payload

Derived:
- price_per_m2
- fingerprint

## Minimálne fields pre AnalysisProfile

- analysis_id
- name
- source
- property_type
- offer_type
- location_query
- price_min_czk
- price_max_czk
- area_min_m2
- area_max_m2
- dispositions
- state_filters
- feature_filters
- enabled

## Parsing Guidance

Pre `sreality.cz`:
- začni conservative deterministic parsing
- najprv HTTP + HTML parsing
- Playwright pridaj iba ak je nutný na funkčný MVP parser

Ak live page parsing nie je spoľahlivý bez browseru:
- vytvor adapter boundary
- nechaj parser nad fixture HTML/JSON
- jasne popíš residual risk

## Testy, ktoré chcem

Minimálne:
- parser extracts normalized listing fields from fixture
- filtering works per analysis profile
- two analyses coexist in SQLite without overwriting each other
- ranking/score is deterministic
- analysis service returns formatted ranked results

Ak pridáš CLI:
- basic CLI smoke test

## Validácia

Spusti:
- `python3 -m pytest -q --tb=short`

Ak má projekt lint/type tooling:
- spusti aj to

## Výstup

Na konci chcem:
1. stručné zhrnutie
2. changed files
3. čo MVP vie
4. čo ešte zámerne nerieši
5. aké testy si spustil
6. residual risks

Ak chýbajú tokeny alebo deploy access:
- neber to ako blocker pre foundation slice
- dokonči všetko, čo sa dá lokálne
