# Credential Hygiene Incident — Post-Mortem

**Date:** 2026-04-07
**Severity:** HIGH (no public exposure, but multiple credentials persisted in
local-only logs and one key file pair lived in the repo working directory).
**Status:** Cleanup completed, rotation pending operator action.

## Summary

A self-audit of the working tree and local Claude conversation logs found:

1. Two `.pem` files (an RSA private key and an X.509 certificate, both
   for a local lab IP) sitting in the repository root. They were
   never committed to git — `.gitignore` always covered `*.pem` — but they
   were inside the project directory, where any backup, copy, or accidental
   `--no-respect-gitignore` clone would carry them along.
2. An SSH password, a Telegram bot token, an Anthropic API key, and the
   `AGENT_VAULT_KEY` had all been pasted into Claude Code conversations at
   various points. Claude Code stores conversation history as JSONL files
   under `~/.claude/projects/<project>/`. Those files are local-only, but
   they are world-readable for the local user and survive across sessions.
3. A handful of repository files referenced personal data:
   - One source comment in `agent/memory/consolidation.py` showed the
     operator's actual server hostname as an example.
   - Several test fixtures in `tests/test_review_domain.py`,
     `tests/test_consolidation.py`, `tests/test_brain_core.py`, and
     `tests/test_control_plane_jobs.py` used the operator's hostname token
     as a literal in redaction-layer test inputs.
   - Three documents under `docs/strategy/` had absolute paths under
     `/Users/<operator>/...` baked into prose.

None of these were ever pushed to a public remote. The `.pem` files were
never tracked by git at any point in history.

## Timeline

| Time         | Event |
|--------------|-------|
| 2026-03-23   | First credential paste into a Claude conversation (SSH password, while bootstrapping `ssh-copy-id`). |
| 2026-03-23 → | Subsequent sessions accreted further leaks: vault key, bot token, API keys. |
| 2026-04-07   | Routine deep audit discovered the conversation log leaks and the in-tree `.pem` files. |
| 2026-04-07   | Operator approved cleanup. Hardening, redaction, and hostname scrub landed in this commit. |

## What we found, by fingerprint

Values are intentionally **not** included. Each row shows only the secret
type, where it appeared, and a SHA-256 fingerprint prefix.

| Type             | Unique values | Files affected | Notes |
|------------------|--------------:|---------------:|-------|
| SSH password     | 1            | 2              | Local lab server account. |
| Telegram bot token | 1          | 4              | Full bot takeover risk if leaked further. |
| Anthropic API key | 2           | 2              | Billing and data access. |
| Operator API key | 1            | 4              | `AGENT_API_KEY` for `/api/operator/*`. |
| Vault master key | 2            | ~30            | `AGENT_VAULT_KEY` propagated through subagent JSONL snapshots. |

The Vault key fan-out is the worst of these: it appeared in ~30 files
because subagent invocations capture environment state and tool-result
snapshots. Anything that leaks the vault key effectively decrypts the
entire local secrets store.

## Remediation (this commit)

Hardening that landed in the repository:

- **`.gitignore`** — added explicit blocks for `*.crt`, `*.p12`, `*.pfx`,
  `*.jks`, common SSH key file names, and a generic `local/`, `secrets/`,
  `private/` set so per-user setup notes can never be tracked.
- **`docs/SETUP_LOCAL.md`** — new operator setup guide so a fresh clone
  has a step-by-step checklist for vault key, bot token, API key, and
  SSH access. Nothing in the guide hardcodes any operator's data.
- **`docs/SECURITY_INCIDENT_2026-04-07.md`** — this document.
- **`docs/strategy/prompts/*.md`** and **`docs/strategy/BACKLOG_PROGRESS.md`**
  — replaced absolute `/Users/<operator>/Desktop/...` paths with
  `<PROJECT_ROOT>` placeholders.
- **`agent/memory/consolidation.py`** — generalized the example comment so
  it no longer names the operator's actual hostname.
- **`tests/test_review_domain.py`**, **`tests/test_consolidation.py`**,
  **`tests/test_brain_core.py`**, **`tests/test_control_plane_jobs.py`**
  — replaced literal hostname tokens with neutral fixtures (e.g.
  `acme-host-*`). The redaction layer is still tested with the same
  surface area; only the example hostname strings changed.

Local-only ops actions performed by the operator after approval:

- The two `.pem` files were moved out of the repository working tree to
  `~/.ssh/agent_life_space/`.
- Local Claude conversation JSONL files containing the SSH password,
  vault key, Telegram token, and Anthropic API key were backed up to
  `~/.claude-secret-quarantine/<timestamp>/` and then redacted in place.
  Each leaked value was replaced with a typed placeholder of the form
  `<TYPE>_REDACTED_<HASH8>`, so the JSONL files remain valid JSON and
  conversation structure is preserved.

## Rotation status (operator-side, not in this commit)

These steps cannot be automated from inside the agent and require manual
operator action. Each is documented in `docs/SETUP_LOCAL.md` so a new
clone gets the same checklist:

- [ ] Rotate the SSH password on the local lab server (`passwd`).
- [ ] Once `ssh-copy-id` is in place, set
  `PasswordAuthentication no` in `/etc/ssh/sshd_config` and reload sshd.
- [ ] Revoke and reissue the Telegram bot token via `@BotFather`
  (`/revoke` then `/newtoken`).
- [ ] Revoke and reissue the Anthropic API key in the Anthropic console.
- [ ] Generate a new `AGENT_VAULT_KEY`, re-encrypt the local vault, and
  store the new value in `.env` (which is gitignored).
- [ ] Generate a new `AGENT_API_KEY` and update `.env`.

## What did **not** happen

- No commits to git history contained these values. Verified with
  `git log --all --diff-filter=A -- '*.pem' '*.key' '.env'` and a content
  grep across `git log --all -p`.
- No public remote ever held the keys.
- No third party is known to have accessed any of the leaked values.

## Lessons

1. **Never paste a credential into a chat assistant**, even a local one,
   if the assistant persists conversation history. If you must show the
   value to the assistant, store it in an env var or a file outside the
   project tree and refer to its name only.
2. **Key material does not belong in the project root**, even if
   `.gitignore` covers it. Move it to `~/.ssh/<project>/` or a vault.
3. **Tests that exercise a redaction layer should use neutral fixtures**,
   not the operator's real hostname or paths. The redaction layer is
   tested either way; using real values just guarantees they grep up.
4. **Subagent JSONL snapshots fan secrets out**. A single env-var leak
   in one conversation can propagate to dozens of subagent files. The
   cleanup script must walk the whole project log directory, not just
   the top-level session files.
