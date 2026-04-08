# Security Policy

## Supported Versions

| Version | Supported          |
|---------|---------------------|
| v1.21.x | Yes                |
| v1.17.x-v1.20.x | Upgrade recommended |
| v1.0.x  | End-of-life (EOL)  |

## Reporting a Vulnerability

**Do NOT open a public issue for security vulnerabilities.**

Instead:

1. Open a [private security advisory](https://github.com/B2JK-Industry/Agent_Life_Space/security/advisories/new)
2. Or contact the maintainer directly via GitHub

### What to include

- Description of the vulnerability
- Steps to reproduce
- Impact assessment
- Suggested fix (if you have one)

### Response timeline

- **Acknowledgment**: within 48 hours
- **Assessment**: within 7 days
- **Fix**: depends on severity (critical: ASAP, high: 7 days, medium: 30 days)

## Security Measures

This project implements multiple security layers. See [Security wiki](https://github.com/B2JK-Industry/Agent_Life_Space/wiki/Security) for details.

### Automated Testing

129 security audit tests run on every commit, covering:
- Hardcoded secrets scan
- SQL injection detection
- eval/exec ban
- Vault encryption enforcement
- Sandbox isolation verification
- API authentication checks
- Log redaction verification
- Subprocess safety
- Prompt injection protection
- Owner enforcement

### Key Security Features

- **Input sanitization** — prompt injection guard (EN + SK patterns)
- **Docker sandbox** — read-only, no-network, resource-limited containers
- **Tool governance** — capability manifest with risk/side-effect classification, policy engine with audit trail
- **Host access blocked by default** — AGENT_SANDBOX_ONLY=1 is the default, explicit opt-in required
- **Encrypted vault** — Fernet AES-128 + HMAC-SHA256, PBKDF2 480K iterations, single-file v2
  format with embedded random salt and crash-safe atomic writes (`os.replace` + `fsync`),
  wrong-key writes fail-fast via `VaultDecryptionError` (no silent destruction)
- **API authentication** — Bearer token only (no `?key=` query string fallback), rate limiting
- **SQL injection guards** — whitelist + identifier regex + escape on dynamic DDL paths
- **Safe mode** — non-owners restricted to read-only commands
- **PID lockfile** — prevents duplicate agent instances
- **Finance** — human-in-the-loop approval for all expenses, per-tx asyncio.Lock against
  concurrent approve races
- **Telegram fail-closed guards** — programming tasks via CLI backend in sandbox-only mode
  return deterministic operator message instead of hanging on unreachable permission prompt
- **Tiered structured logging** — long-tier retention for audit/security/finance events
  (default 30 days), short-tier for verbose diagnostics (default 6 hours)
