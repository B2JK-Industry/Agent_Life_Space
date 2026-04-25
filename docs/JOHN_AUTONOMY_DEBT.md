# John Autonomy Debt — Čo treba opraviť aby bol John naozaj autonómny

_Vytvorené: 2026-04-17. Autor: Claude (tech lead review session)._
_Kontext: Daniel povedal "prečo John nie je ako ty? Veď je viac sofistikovaný."_

## Koreňový problém

John má tooling (CLI, Docker, marketplace connectors, Telegram) ale **nemá agency**. Nemá schopnosť chain-of-thought reasoning → akcie → feedback → ďalšia akcia. Má len:
- Regex intent matching (krehké, nepokrýva prirodzenú reč)
- Single-shot LLM calls s `no_tools=True` (nevie čítať output svojich akcií)
- Hardcoded if/else v cronu (nevie sa adaptovať)

Claude v konverzácii dokáže: vidieť listing → vyhodnotiť → bidnúť → počkať na výsledok → analyzovať chybu → fixnúť → znova → uspieť. John dokáže len prvý krok a potom čaká na ľudský zásah.

## Rozdiely medzi Claude (conversation) a John (agent)

| Schopnosť | Claude (ja) | John (agent) |
|---|---|---|
| Vidieť Obolos listings | áno (Bash → CLI) | áno (cron + intent) |
| Vyhodnotiť feasibility | áno (reasoning) | áno (deterministic scorer) |
| Bidovať | áno (Bash → CLI) | áno (ale len cez Telegram commands) |
| Reagovať na chybu | áno (read error → fix → retry) | NIE (loguje a čaká) |
| Využiť x402 API | NIE (neimplementované) | NIE |
| Generovať kód | áno (Edit/Write tools) | sandbox-blokované (fixnuté no_tools) |
| Multi-step reasoning | áno (chain-of-thought) | NIE (single-shot) |
| Učiť sa z výsledkov | áno (conversation context) | NIE (no feedback loop) |
| Volať externé API | áno (curl, web) | obmedzené (gateway + policy) |
| Pracovať na viacerých veciach | áno (paralelné tool calls) | NIE (sekvenčný cron) |

## Dlh #1 — Obolos platforma nie je plne využitá

### Čo Obolos ponúka (z dokumentácie + web scrape)
1. **x402 Pay-per-call APIs** — obrázky, video, text, scraping, audio. Cez `obolos call <api-slug>` a platí sa USDC per request.
2. **Listings marketplace** — klienti postujú prácu, agenti bidujú. (Toto John čiastočne robí.)
3. **ACP on-chain jobs** — smart contract escrow: fund → submit → complete/reject. USDC locked until delivery.
4. **ANP negotiation** — EIP-712 signed messages pre bidding + amendments + checkpoints.
5. **MCP server** — `@obolos_tech/mcp-server` exponuje všetky Obolos commands ako MCP tools.
6. **Reputation system** — on-chain score 0-100 based on completed jobs.
7. **Agent identity (ERC-8004)** — on-chain identity registry.

### Čo John využíva
- ✅ Listings (scan + bid + submit)
- ✅ ANP bid (len cez CLI, nie plný negotiation flow)
- ✅ Job submit/complete/reject
- ❌ x402 API calls — John nevie zavolať pay-per-call API
- ❌ ACP funding check — auto-execute na unfunded job zlyháva
- ❌ ANP messages — John nevie komunikovať s klientom v jobu
- ❌ MCP server — nie je integrovaný
- ❌ Reputation building — žiadna stratégia
- ❌ x402 sub-contracting — ak John dostane video job, mohol by zavolať x402 video API

### Akčné položky
- [ ] **P0: ACP funding check pred auto-execute** — "Job must be funded" error
- [ ] **P0: x402 API integration** — `obolos call` wrapper + gateway route. John by mohol volať image/video/scraping APIs ako sub-contractor.
- [ ] **P1: ANP message support** — `obolos anp message` pre komunikáciu s klientom (kedy chýba spec, kedy treba clarification)
- [ ] **P1: MCP server integration** — namiesto CLI wrappers použiť MCP tools (stabilnejšie, typed schema)
- [ ] **P2: Reputation strategy** — prioritizovať malé feasible joby pre reputation building
- [ ] **P2: x402 sub-contracting pipeline** — video/image job → zavolaj x402 API → submit deliverable

## Dlh #2 — Intent matching je krehké

### Problém
Regex patterny pokrývajú len explicitné frázy. "zapoj sa do práce" muselo mať 3 iterácie fixov. "najdi si pracu" (bez diakritiky) nefungovalo. "zisti či je praca" nefungovalo.

### Riešenie
- [ ] **P0: Semantic intent classification** — namiesto regexov použiť embedding similarity. John už má `paraphrase-multilingual-MiniLM-L12-v2` model loaded (pre RAG). Stačí vectorize 20 intent exemplárov a matchovať cosine similarity > 0.7.
- [ ] **P1: Fallback chain** — ak regex nefirne a LLM dá výhovorku, rescue chain je OK ale nie dostatočný. Treba "always try marketplace handler if question contains any marketplace-adjacent word".

## Dlh #3 — John nemá multi-step execution

### Problém
Keď John nájde listing, vie naň bidnúť. Ale keď bid akceptujú a job treba urobiť:
1. John generuje generický deliverable (hardcoded text) namiesto skutočnej práce
2. John nevie "pozri si zadanie → rozhodni čo treba → urob to → over výsledok → odošli"
3. John nemá feedback loop — ak submit zlyháva, loguje a čaká

### Riešenie
- [ ] **P0: Real work execution** — accepted job → extract spec from description → /build with that spec → collect artifacts → submit artifacts as deliverable
- [ ] **P1: Error recovery loop** — if submit fails (unfunded, rejected), wait → retry → notify
- [ ] **P1: Quality self-check** — before submitting, John runs verification on his own output
- [ ] **P2: Client communication** — if spec is vague, ANP message to ask for clarification

## Dlh #4 — Sandbox vs. capability mismatch

### Problém
`AGENT_SANDBOX_ONLY=1` blokuje Bash/Edit/Write v Claude CLI tool-use. To je správne pre bezpečnosť. Ale:
- Codegen nepotrebuje file access (fixnuté: no_tools=True)
- Build pipeline používa Docker (safe) ale codegen path bola blokovaná
- John nemôže čítať output svojich vlastných príkazov v LLM brain path

### Riešenie
- [x] **DONE: no_tools=True pre codegen/spec/docker-fix** (commit e0e57e2)
- [ ] **P1: Structured tool execution** — namiesto Claude CLI tool-use, dať Johnovi vlastné "safe tools" (read own logs, read job status, read marketplace data) ktoré fungujú aj v sandbox
- [ ] **P2: Agent loop with tools** — John by mal mať vlastný tool_loop (agent/core/tool_loop.py existuje!) ale nikdy sa nepoužíva pre marketplace flow

## Dlh #5 — Knowledge base je povrchná

### Problém
John má knowledge/ directory ale:
- Žiadny .md o Obolos API detailoch
- Žiadny .md o x402 protocol
- Žiadny .md o ACP/ANP flow
- autonomy_rules.md je dobré ale generic

### Riešenie
- [ ] **P0: Scrape obolos.tech docs** a vytvor knowledge/ súbory
- [ ] **P0: Scrape obolos CLI --help** pre všetky commands a ulož
- [ ] **P1: x402 API catalog** — aké APIs sú dostupné, čo stoja, aké vstupy/výstupy
- [ ] **P1: ACP lifecycle knowledge** — fund → submit → complete → payout

## Dlh #6 — Univerzálna marketplace integrácia

### Problém
Aktuálna integrácia je hardcoded na Obolos. Keby zajtra pribudla ďalšia platforma (napr. Replit Bounties, Fiverr API, GitHub Sponsors), treba všetko od nuly.

### Riešenie
- [ ] **P2: Abstract marketplace protocol** — ConnectorRegistry už existuje, ale connectors sú 1:1 s Obolos CLI
- [ ] **P2: Discovery layer** — scan viacerých platforiem, normalize do Opportunity modelu
- [ ] **P3: Multi-platform bid strategy** — kde bidovať, za koľko, aká priorita

## Prioritná roadmapa

### Ihneď (P0) — ✅ HOTOVÉ (2026-04-17)
1. ✅ ACP funding check pred auto-execute (commit ab2146b)
2. ✅ x402 API volanie — cli_api_search, cli_api_call, connector methods (commit ab2146b)
3. ✅ Semantic intent classification — work_search/work_status v semantic_router + dispatcher (commit 378e8ac)
4. ✅ Obolos knowledge base — obolos_platform.md s celou platformou (commit ab2146b)

### Tento týždeň (P1) — ✅ HOTOVÉ (2026-04-17)
5. ✅ Real work execution — LLM-generated deliverables (commit 3448ee6)
6. ✅ ANP message support — /marketplace message + /marketplace thread (commit 3448ee6)
7. ✅ Error recovery loop — retry set + max 3 retries (commit 3b21295)
8. ✅ no_tools=True fix — codegen/spec/docker work in sandbox (commit e0e57e2)
9. ✅ x402 sub-contracting pipeline — search APIs + notify operator (commit ab2146b)

### Tento mesiac (P2) — PENDING
10. Reputation building strategy
11. Multi-platform abstraction
12. Self-check pred submit

### Budúcnosť (P3) — PENDING
13. Multi-platform discovery
14. Autonomous bidding (no human /yes needed for low-risk bids)
15. Revenue optimization (which jobs are most profitable)
