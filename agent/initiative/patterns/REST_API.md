# Pattern: REST_API

**Kedy použiť:** Goal je "spravme API endpoint / mikroservis / REST appku".

## Polyglot

- **Python + FastAPI** — default, rýchle, integrácia s ALS
- **Rust + axum / actix** — high perf, malý binar, prod servisy
- **Go + chi / gin** — sieťové sidecary
- **TypeScript + Hono** — edge / serverless

## Fázy plánu

1. **ANALYZE** — endpoint(s), authentifikácia, validácia, persist layer.
2. **DESIGN** — OpenAPI spec (priamo do knowledge ako .yaml).
3. **CODE** — handler + schemas + migrations + tests.
4. **TEST** — integration test (httpx + ASGI app).
5. **VERIFY** — všetky endpointy v OpenAPI majú aspoň 1 test, stavové kódy, chybové paths.
6. **DEPLOY** (vyžaduje approval) — Docker build, push do registry, env update.
7. **MONITOR** — health check endpoint + integrácia do `agent.core.watchdog`.

## Acceptance criteria

- [ ] OpenAPI spec validuje
- [ ] Všetky endpointy authentifikované (alebo explicit `public_endpoints` whitelist)
- [ ] Validation errors → 422 s field-level detailom
- [ ] Pytest coverage >= 80% pre handler kód
- [ ] Žiadne secrets v kóde
