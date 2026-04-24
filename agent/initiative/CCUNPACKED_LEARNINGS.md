# Claude Code Unpacked → ALS Initiative Engine

Patterny z [ccunpacked.dev](https://ccunpacked.dev) — Anthropic Claude Code internals, mapované na ALS Initiative Engine. Toto je live doc, updateuje sa s každou novou verziou patternov.

## 1. Agent Loop (11 krokov, `query.ts` 1729 riadkov, async generator)

```
1.  Input        — src/components/TextInput.tsx       (stdin / Ink TextInput)
2.  Message      — createUserMessage()                 (wrap do Anthropic formátu)
3.  History      — push to in-memory conv array        (context manager trims later)
4.  System       — assemble system prompt              (CLAUDE.md + tool defs + memory)
5.  API          — stream cez Anthropic SDK            (SSE)
6.  Tokens       — parse + render live                 (Ink + Yoga flexbox)
7.  Tools?       — findToolByName + canUseTool         (parallel execution)
8.  Loop         — append tool results, call API again (THE AGENTIC LOOP)
9.  Render       — final markdown                      (Ink)
10. Hooks        — auto-compact + extract memories     (DREAM MODE)
11. Await        — REPL idle                            (Ctrl+C graceful)
```

**Kľúčový insight:** "The real work is in the orchestration — model call is 1 stage out of 7." Generator pattern → explicit state transitions, control yielded pri 7 bodoch bez straty stavu.

**Mapping na InitiativeEngine:**
- 1–2 → Telegram intent → `start_initiative(goal_nl, chat_id)`
- 3–4 → `Project.create()` + `plan.json` perzistencia
- 5–8 → `tick()` driver volá `executor.execute()` (provider robí tool-loop interne)
- 9 → step result do `steps/<idx>.json`
- 10 → **Auto-compact** + **Dream mode** (NOVÉ — viď nižšie)
- 11 → AgentCron sleep medzi tikmi

## 2. Hidden Features → ALS Roadmap

| Claude Code | Popis | Mapping na ALS | Priorita |
|---|---|---|---|
| **Kairos** | Persistent mode, memory consolidation, SleepTool | InitiativeEngine + AgentCron driver | **P0 — implementovaný** |
| **Auto-Dream** | Post-session: review konverzáciu, pull worth keeping → memdir/ | `_dream_completed_initiative()` hook | **P0** |
| **UltraPlan** | Long planning na Opus, 30 min windows, polling | `is_long_running` → Opus + max_turns=2 | **P1** |
| **Coordinator Mode** | Lead agent → workers v isolated git worktrees | `step.metadata.coordinator=True` → sub-agent v `agent/work/` workspace | **P1** |
| **Daemon Mode** | `--bg` cez tmux | systemd-managed (už máme) | **DONE** |
| **UDS Inbox** | Inter-session messaging cez Unix sockets | `agent.social.api` HTTP A2A endpoint (už máme) | **DONE** |
| **Bridge** | Phone/browser remote control | `/dashboard` (už máme) | **DONE** |
| **Buddy** | Virtual pet | (skip) | — |

## 3. Tool System (50+ tools, kategorizované)

Mapping na ALS tools (cez `agent.core.tools`):

| Claude Code | ALS ekvivalent |
|---|---|
| FileRead/Edit/Write, Glob, Grep | LLM provider má file access (Claude CLI) — `FILE_TOUCHING_KINDS` flag |
| Bash, REPL | Claude CLI Bash tool |
| WebFetch, WebSearch | Claude CLI tools |
| **TaskCreate/Get/List/Update** | `agent.tasks.TaskManager` (1:1) |
| **Agent, SendMessage** | `agent.social.api` agent-to-agent endpoint |
| **EnterWorktree** | `agent.work.workspace_manager` |
| **CronCreate/Delete/List** | `agent.tasks.TaskManager` (TaskType.CRON) |
| AskUserQuestion | Telegram message round-trip |
| TodoWrite | InitiativePlan.steps |
| Skill | `agent.brain.skills` |
| **PushNotification, Monitor** | `_notify_loop` v executor |

## 4. Aplikované zlepšenia v `engine.py`

### A. Auto-Compact (Hook #10)
Keď `prior_outputs > 5` krokov → LLM compaction do 1 summary blocku. Bráni context bloatu pri dlhých initiatives.

### B. Dream Mode (Auto-Dream)
Pri `_finalize` → spusti dream pass:
- Extrahuj learnings z `steps/*.json`
- Zapíš do `agent/brain/knowledge/initiatives/<id>_lessons.md`
- Update `skills.json` ak vznikol nový skill
- Telegram notifikácia s sumárom

### C. Generator-based tick (yield points)
Driver `tick_stream()` je `AsyncGenerator[StepEvent]` — yields po každom kroku. Umožňuje:
- Real-time progress streaming na Telegram
- Pausing mid-tick pri externom signáli
- Observability metrics per-yield

### D. UltraPlan tier (vybraný model podľa scope)
`InitiativePlanner` switch:
- `len(goal_nl) < 200` → Sonnet
- `len(goal_nl) >= 200` alebo "ultraplan" v texte → Opus + extended thinking

### E. Coordinator Mode (P1)
`step.metadata.coordinator=True` → executor namiesto LLM volá sub-agent cez `WorkspaceManager` v izolovanom workspace. Sub-agent má vlastný plan + prompts.

## 5. Otvorené P2 položky

- **Stream tool execution events** do Telegram (tokens-as-they-arrive, ako Claude Code render)
- **Permission gates** pre senzitívne tools (canUseTool ekvivalent — už čiastočne v `tool_policy.py`)
- **Memory directory** organizovaná podľa Claude Code conventions (`memdir/` štruktúra)
- **`/ultraplan` slash command** v Telegrame pre force-Opus mode

## 6. Zdroje

- [ccunpacked.dev](https://ccunpacked.dev) — primárny zdroj
- [code.claude.com/docs/agent-sdk/agent-loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)
- [github.com/VILA-Lab/Dive-into-Claude-Code](https://github.com/VILA-Lab/Dive-into-Claude-Code) — systematická analýza
- DeepWiki linky pre konkrétne features (kairos, ultraplan, coordinator, autodream — viď tabuľku 2)
