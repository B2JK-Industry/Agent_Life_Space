# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| v1.0.x  | Yes       |

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

50 security audit tests run on every commit, covering:
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
- **Encrypted vault** — Fernet AES-128, PBKDF2 480K iterations
- **API authentication** — Bearer token, rate limiting
- **Safe mode** — non-owners restricted to read-only commands
- **PID lockfile** — prevents duplicate agent instances
- **Finance** — human-in-the-loop approval for all expenses
