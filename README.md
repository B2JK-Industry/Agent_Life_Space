# Agent Life Space

Self-hosted autonomous AI agent that lives on your server. Communicates via Telegram, learns from outcomes, runs code in Docker sandbox.

## What it does

- **7-layer cascade** — 5 layers of local processing before calling LLM
- **Learns** — tracks skill outcomes, escalates model on failure, augments prompts from past errors
- **Docker sandbox** — code runs in isolated containers (256MB, no network, read-only FS)
- **Encrypted vault** — API keys, wallet keys (ETH/BTC) encrypted with Fernet AES-128
- **Memory** — 4 types (episodic, semantic, procedural, working), consolidation, decay
- **Agent-to-Agent API** — HTTP endpoint for communication with other agents
- **470+ tests**

## Setup Your Own Agent (Step by Step)

### 1. Requirements

- Linux server (Ubuntu 22.04+), 4-core CPU, 8GB RAM
- Python 3.12+
- Docker (required for sandbox)
- Git
- Telegram account
- Claude Max subscription or Anthropic API key

### 2. Clone and install

```bash
git clone https://github.com/B2JK-Industry/Agent_Life_Space.git
cd Agent_Life_Space
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install sentence-transformers  # for semantic router (~470MB)
```

### 3. Create Telegram Bot

1. Open Telegram, find **@BotFather**
2. Send `/newbot`, follow instructions
3. Copy the bot token (looks like `123456:ABC-DEF...`)
4. Send a message to your new bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your user ID

### 4. Setup encrypted vault

```bash
python scripts/setup_vault.py
```

This generates a master key and creates ETH/BTC wallets. **Save the master key** — you need it to decrypt secrets.

### 5. Configure environment

Create systemd service:

```bash
mkdir -p ~/.config/systemd/user/
cat > ~/.config/systemd/user/agent-life-space.service << 'EOF'
[Unit]
Description=Agent Life Space
After=network.target

[Service]
Type=simple
WorkingDirectory=%h/Agent_Life_Space
ExecStart=%h/Agent_Life_Space/.venv/bin/python -m agent
Restart=always
RestartSec=10
Environment=PATH=%h/.local/bin:/usr/bin:/bin
Environment=AGENT_VAULT_KEY=<your-vault-key-from-step-4>
Environment=TELEGRAM_BOT_TOKEN=<your-bot-token>
Environment=TELEGRAM_USER_ID=<your-user-id>
Environment=CLAUDE_CODE_OAUTH_TOKEN=<your-claude-token>

[Install]
WantedBy=default.target
EOF
```

Replace `<placeholders>` with your actual values.

### 6. BotFather settings

Send to @BotFather:
- `/mybots` → select your bot → **Bot Settings** → **Group Privacy** → **Turn OFF**
  (allows bot to see messages in groups)

### 7. Start

```bash
systemctl --user daemon-reload
systemctl --user enable agent-life-space
systemctl --user start agent-life-space
```

### 8. Verify

```bash
systemctl --user status agent-life-space  # should be "active"
```

Then send `/status` to your bot on Telegram. You should see:
```
Agent Status
Running: True
Spomienky: 1
Úlohy: 0
Watchdog moduly: 5 (5 healthy)
```

### 9. Docker setup (required)

```bash
sudo apt install docker.io
sudo usermod -aG docker $USER
# Log out and back in, then verify:
docker run --rm hello-world
```

### 10. Optional: Agent-to-Agent API

For communication with other agents:

```bash
# Add API key to service
# Generate: python -c "import secrets; print(f'agent_api_{secrets.token_urlsafe(24)}')"
# Add to systemd: Environment=AGENT_API_KEY=<generated-key>

# Open firewall
sudo ufw allow 8420/tcp

# Or use cloudflare tunnel (no port forwarding needed):
sudo apt install cloudflared  # or download from cloudflare
cloudflared tunnel --url http://localhost:8420
```

Other agents can then send messages:
```bash
curl -X POST https://<your-tunnel>.trycloudflare.com/api/message \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <api-key>" \
  -d '{"message": "hello", "sender": "other-agent"}'
```

## Customization

### Identity
Edit `JOHN.md` — change name, owner, capabilities. This is injected into every LLM prompt.

### Rules
Edit `CLAUDE.md` — agent rules, language, permissions.

### Skills
Edit `agent/brain/skills.json` — 20 default skills. Add your own.

### Knowledge Base
Add `.md` files to `agent/brain/knowledge/`:
- `people/` — who is the owner
- `systems/` — server, APIs
- `projects/` — what you're working on
- `skills/` — how-tos
- `learned/` — auto-generated from outcomes

### Telegram Commands

| Command | What it does |
|---------|-------------|
| `/status` | Agent status |
| `/health` | CPU, RAM, disk, module states |
| `/tasks` | Task list |
| `/memory [keyword]` | Search memory |
| `/budget` | Finance status |
| `/newtask [name]` | Create task |
| `/projects [name]` | List/create projects |
| `/web [url]` | Fetch and read a webpage |
| `/sandbox [code]` | Run Python in Docker sandbox |
| `/wallet` | ETH/BTC addresses |
| `/usage` | Token usage and costs |
| `/consolidate` | Run memory consolidation |
| `/review [file]` | Code review |
| `/help` | All commands |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                 7-LAYER CASCADE                      │
│                                                      │
│  1. /commands      → direct response (0 API calls)  │
│  2. Dispatcher     → regex patterns (0 API calls)   │
│  3. Semantic Router→ MiniLM local (~470MB RAM)       │
│  4. Semantic Cache → similar question? (local)       │
│  5. Self-RAG       → knowledge base (local embed.)   │
│  6. Haiku/Sonnet   → simple/conversation             │
│  7. Opus           → programming tasks               │
└─────────────────────────────────────────────────────┘
```

## Docs

- [docs/BLUEPRINT.md](docs/BLUEPRINT.md) — full architecture details
- [docs/VERIFICATION.md](docs/VERIFICATION.md) — 8 E2E deployment scenarios

## License

MIT
