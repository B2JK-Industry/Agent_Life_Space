# Product Identity

## Decision: Personal Sovereign Operator Agent

Agent Life Space je **personal sovereign operator** — nie general agent platform.

### Čo to znamená
- Jeden vlastník (Daniel), jeden agent (John)
- Všetky rozhodnutia s human-in-the-loop
- Self-hosted, local-first, security-first
- Agent slúži vlastníkovi, nie verejnosti

### Čo NIE sme
- Nie sme general-purpose agent framework
- Nie sme multi-tenant platform
- Nie sme "AI chatbot as a service"
- Nie sme open-ended autonomous agent bez controls

### Core identity pillars
1. **Sovereign** — agent beží na vlastníkovom hardware, dáta neopúšťajú server
2. **Operator** — agent robí prácu pre vlastníka, nie pre seba
3. **Trustworthy** — agent je poctivý o tom čo vie a čo nevie
4. **Auditable** — každá akcia je sledovateľná a vysvetliteľná
5. **Safe** — agent nikdy nerobí nič nebezpečné bez schválenia

### Features to keep
- Epistemic memory (provenance, conflict detection)
- Tool governance (capability manifest, policy engine)
- Approval queue (human-in-the-loop)
- Operator controls (lockdown, status model)
- Security invariant tests

### Features to NOT build
- Multi-user auth / RBAC
- Public API endpoints
- Autonomous financial operations
- Self-modifying security rules
- "App store" for agent capabilities

### Success metric
> Agent je užitočný pre Daniela a Daniel vždy rozumie čo agent robí a prečo.
