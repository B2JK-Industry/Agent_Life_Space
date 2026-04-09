# Self-Update from Telegram

The agent has an explicit, owner-only capability to pull the latest
code from its public GitHub remote and (optionally) restart itself
under a process supervisor — all from a single Telegram message.

## TL;DR

```
operator (Telegram): nasad novú verziu u seba
agent:               Fast-forwarded `main` from abc1234 to def5678
                     (5 commits).

                     Process supervisor detected: `systemd`. I will
                     now drain in-flight work and exit gracefully so
                     the supervisor starts a fresh process with the
                     new code. You should see the new version reply
                     to your next message.
                     [agent exits 0; systemd brings up a fresh
                      process with the new code]
```

The full end-to-end loop is:

1. Owner sends an imperative on Telegram
   (`nasad novú verziu u seba`, `update yourself`, `aktualizuj sa`,
   `deploy latest`, etc.)
2. Brain layer recognises the imperative deterministically (no LLM,
   no Claude permission prompt).
3. `agent.core.self_update.run_self_update()` runs the deterministic
   git fast-forward workflow:
   - owner-only check
   - git working tree check (`.git` exists)
   - upstream tracking ref check
   - dirty worktree → fail-closed
   - `git fetch` → count commits behind
   - if behind, `git pull --ff-only`
   - never destructive (`reset --hard` / rebase / stash are forbidden)
4. If `AGENT_SELF_RESTART_AFTER_UPDATE=1` AND a process supervisor
   is detected, the brain schedules a graceful self-restart:
   - waits `AGENT_SELF_RESTART_GRACE_S` seconds (default 3) so the
     Telegram bot can deliver the reply
   - calls `agent.stop()` to drain cron loops, queue, DB
   - flushes stdout/stderr
   - calls `os._exit(0)`
5. The supervisor (systemd / supervisord / docker `restart=always`)
   sees the clean exit and starts a fresh process with the new code.

## Operator opt-in

Self-restart is **off by default**. The agent will pull but won't
exit unless you explicitly turn it on.

| Env var | Default | Purpose |
|---|---|---|
| `AGENT_SELF_RESTART_AFTER_UPDATE` | unset | Set to `1` / `true` / `yes` / `on` / `systemd` to enable post-update self-restart. |
| `AGENT_PROCESS_SUPERVISOR` | auto-detected | Override the supervisor name. Recognised values: `systemd`, `supervisord`, `docker`. |
| `AGENT_SELF_RESTART_GRACE_S` | `3` | Seconds to wait between sending the reply and starting the graceful shutdown. Range 0–30. |

The brain refuses to self-restart if **no supervisor is detected**.
Detection order:

1. `AGENT_PROCESS_SUPERVISOR` env (operator override)
2. `INVOCATION_ID` env (set by systemd for every unit invocation)
3. `SUPERVISOR_ENABLED` env (set by supervisord)
4. `container` env or `/.dockerenv` file (docker / kubernetes)

If you turn on `AGENT_SELF_RESTART_AFTER_UPDATE=1` but the agent is
running directly from a shell (no supervisor), the update still
happens but the agent surfaces a clear misconfiguration message:

> AGENT_SELF_RESTART_AFTER_UPDATE is set but no process supervisor
> was detected (no INVOCATION_ID, no AGENT_PROCESS_SUPERVISOR, no
> /.dockerenv). I will not self-kill in an unsupervised environment
> — restart manually, then set AGENT_PROCESS_SUPERVISOR=systemd
> (or run me under systemd / supervisord / docker).

This is intentional: a self-killing agent in an unsupervised
environment just disappears.

## Recommended systemd unit

Drop this into `/etc/systemd/system/agent-life-space.service`,
edit the paths and the user, then `systemctl enable --now
agent-life-space`:

```ini
[Unit]
Description=Agent Life Space
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=youruser
Group=youruser
WorkingDirectory=/home/youruser/Agent_Life_Space
EnvironmentFile=/home/youruser/Agent_Life_Space/.env
Environment=AGENT_DATA_DIR=/var/lib/agent-life-space
Environment=AGENT_LOG_DIR=/var/log/agent-life-space
Environment=AGENT_SELF_RESTART_AFTER_UPDATE=1

ExecStart=/home/youruser/Agent_Life_Space/.venv/bin/python -m agent

# Restart policy: bring us back on any exit, including the clean
# self-restart after a successful update.
Restart=always
RestartSec=2
StartLimitBurst=5
StartLimitIntervalSec=60

# Watchdog (optional but recommended).
WatchdogSec=120

# Isolation hardening — adjust to taste.
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/agent-life-space /var/log/agent-life-space
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Key points:

- `Restart=always` is required so the supervisor brings the agent
  back after the clean self-exit.
- `RestartSec=2` keeps the gap short.
- `StartLimitBurst=5` + `StartLimitIntervalSec=60` prevents an
  infinite restart loop if a bad update brings the agent down on
  every boot. Adjust to taste.
- `WatchdogSec` is optional — the agent's heartbeat loop will keep
  the watchdog satisfied.
- `INVOCATION_ID` is set by systemd automatically; the agent uses
  it to detect that it's running under a supervisor.

## Recommended supervisord config

```ini
[program:agent-life-space]
command=/home/youruser/Agent_Life_Space/.venv/bin/python -m agent
directory=/home/youruser/Agent_Life_Space
user=youruser
autostart=true
autorestart=true
startsecs=5
stopwaitsecs=30
environment=
    AGENT_DATA_DIR="/var/lib/agent-life-space",
    AGENT_LOG_DIR="/var/log/agent-life-space",
    AGENT_SELF_RESTART_AFTER_UPDATE="1",
    AGENT_PROCESS_SUPERVISOR="supervisord"
```

Note: supervisord doesn't set `INVOCATION_ID`, so set
`AGENT_PROCESS_SUPERVISOR=supervisord` explicitly.

## Recommended docker compose

```yaml
services:
  agent:
    image: ghcr.io/b2jk-industry/agent-life-space:latest
    restart: always
    environment:
      AGENT_DATA_DIR: /var/lib/agent-life-space
      AGENT_LOG_DIR: /var/log/agent-life-space
      AGENT_SELF_RESTART_AFTER_UPDATE: "1"
      AGENT_PROCESS_SUPERVISOR: docker
    volumes:
      - agent-data:/var/lib/agent-life-space
      - agent-logs:/var/log/agent-life-space
volumes:
  agent-data:
  agent-logs:
```

`restart: always` plus `AGENT_PROCESS_SUPERVISOR=docker` does the
trick.

## Failure modes & fallbacks

| Situation | Reply |
|---|---|
| Non-owner triggers update | "Self-update is owner-only and cannot be triggered from a group chat." |
| Not a git working tree | "This deployment is not a git working tree, so self-update is not available." |
| No upstream tracking ref | "Branch `main` has no upstream tracking ref, so I cannot fast-forward." |
| Dirty worktree | "Self-update refused: the working tree has uncommitted changes." |
| Already up to date | "Already up to date on `main` (abc1234). Nothing to pull." |
| Branch diverged from upstream | "Cannot fast-forward: branch `main` is N commits ahead..." |
| Network / DNS / auth failure | Short friendly sentence, no raw `git` stderr leakage. |
| Self-restart enabled but no supervisor | Pulls successfully, refuses to self-kill, surfaces the misconfiguration. |
| Self-restart enabled and supervisor detected | Pulls, drains, exits 0, supervisor restarts with new code. |

## Troubleshooting

**The agent says "AGENT_SELF_RESTART_AFTER_UPDATE is set but no
process supervisor was detected".**

Either you're running the agent from a shell (without systemd /
supervisord / docker) or the detection didn't fire. Solutions:

1. Run the agent under systemd using the recommended unit above.
2. Or set `AGENT_PROCESS_SUPERVISOR=systemd` explicitly in your
   environment file.
3. Or unset `AGENT_SELF_RESTART_AFTER_UPDATE` and restart manually
   after each update.

**The agent restarts but still serves the old code.**

Check that the systemd unit's `WorkingDirectory` matches the git
checkout the agent pulled into. If you have multiple checkouts the
unit might be pointing at the wrong one.

**The agent restarts in a loop after a bad update.**

`StartLimitBurst=5` + `StartLimitIntervalSec=60` will throw it into
`failed` state after 5 restarts in a minute. From there:

1. SSH in (or recover the host).
2. `git -C /path/to/Agent_Life_Space reset --hard <known-good-sha>`
3. `systemctl reset-failed agent-life-space`
4. `systemctl start agent-life-space`

## Why the agent never restarts itself in an unsupervised environment

Self-killing without a supervisor means *the agent disappears*.
There's no way to recover except SSH-ing in and starting it again,
which is exactly the situation we want to avoid (the operator may
not have shell access at the moment).

The contract is: **the supervisor brings me back, or I don't kill
myself.** This is checked at runtime, not at config-load time, so
operators can fix the misconfiguration without redeploying.
