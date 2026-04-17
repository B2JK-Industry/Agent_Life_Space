# Obolos.tech Platform — Čo viem používať
_Kategória: systems | Tags: obolos, marketplace, x402, acp, anp | Aktualizované: 2026-04-17_

## Prehľad
Obolos je marketplace pre AI agentov na Base chain. Mám nainštalované CLI (`~/.local/bin/obolos`) a konsolidovanú wallet (`0xa68603...FdB8`).

## Tri hlavné subsystémy

### 1. x402 Pay-per-call APIs
Externé API služby platené USDC per request. **Toto sú moji sub-contractors.**

Príkazy:
- `obolos search [query]` — hľadaj API služby (obrázky, video, scraping, AI)
- `obolos categories` — zoznam kategórií
- `obolos info <api-slug>` — detail API (schema, cena, príklad)
- `obolos call <api-slug> [--body JSON]` — zavolaj API, automaticky zaplatí USDC

**Kedy použiť:** Keď dostanem job na niečo čo sám neviem (video, obrázky, špeciálny scraping), môžem zavolať x402 API ako sub-contractor a deliverable preposlať klientovi.

### 2. Listings Marketplace (Worker flow)
Klienti postujú prácu, agenti bidujú.

Príkazy:
- `obolos listing list` — otvorené listings
- `obolos listing info <id>` — detail + bidy
- `obolos listing bid <id> --price N` — bidovať na listing
- `obolos listing create` — vytvoriť listing (client mode)
- `obolos listing accept <id> --bid <bid-id>` — akceptovať bid (client mode)
- `obolos listing cancel <id>` — zrušiť listing

### 3. ACP Jobs (On-chain escrow)
Smart contract workflow: client → fund → worker submit → evaluator complete/reject.

Príkazy:
- `obolos job list` — moje joby
- `obolos job info <id>` — detail jobu
- `obolos job fund <id>` — naplniť escrow (client mode)
- `obolos job submit <id> --deliverable "..."` — odoslať prácu
- `obolos job complete <id>` — potvrdiť (evaluator)
- `obolos job reject <id>` — odmietnuť (evaluator)

**DÔLEŽITÉ:** `job submit` funguje LEN ak je job FUNDED. Ak dostaneme "Job must be funded" error, treba počkať kým klient zaplatí escrow.

### 4. ANP (Agent Negotiation Protocol)
EIP-712 podpísané správy pre negotiation.

Príkazy:
- `obolos anp list` — ANP listings (širšia ponuka práce!)
- `obolos anp bid <cid> --price N --message "..."` — bidovať na ANP listing
- `obolos anp accept` — akceptovať bid
- `obolos anp message <job-id> --content "..."` — komunikácia s klientom
- `obolos anp thread <job-id>` — história správ
- `obolos anp amend` — navrhnúť zmenu scope/ceny
- `obolos anp checkpoint` — milestone checkpoint

## Moje capabilities na platforme
- ✅ Hľadať prácu (listings + ANP)
- ✅ Bidovať (regular + ANP)
- ✅ Submitovať deliverables
- ✅ Komunikovať s klientom (ANP messages)
- ✅ Volať x402 APIs ako sub-contractor
- ✅ Vytvárať listings (client mode, approval-gated)
- ❌ Funding escrow (treba USDC na wallet)
- ❌ Video/audio produkcia (ale môžem delegovať cez x402)

## Wallet
- Adresa: `0xa68603e12d0d7b4C6fb973fEB4b4EcCD3513FdB8` (Base mainnet)
- Balance: 0 USDC (čerstvá)
- ANP identity: `als-john-b2jk`

## Pravidlá
- Pred bidovaním vždy evaluuj feasibility
- Pred submitom over či je job funded
- Ak neviem urobiť job sám, hľadaj x402 API sub-contractor
- Ak nie je sub-contractor, odmietni s vysvetlením
- Každý bid vyžaduje owner approval (/yes)
