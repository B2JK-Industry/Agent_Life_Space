# Initiative Engine — Deployment & First Use

## 1. Deploy na server (b2jk)

```bash
ssh b2jk
cd ~/Agent_Life_Space
git pull origin feat/marketplace-provider-lifecycle
.venv/bin/pip install -e ".[dev]"   # no new deps, ale pre istotu
systemctl --user restart agent-life-space
journalctl --user -u agent-life-space -f --since "1 minute ago" | grep -i initiative
```

Očakávané log linky pri štarte:
```
initiative_engine_initialized
cron_started jobs=14
initiative_driver_bot_attached     # po prvom tiku (~60s)
```

## 2. First initiative cez Telegram

Pošli ownerovi cez Telegrame (alebo cez API):
```
/initiative urob mi denný scraper na sreality.cz, byty 2+kk Praha pod 8M Kč, pošli mi notifikáciu keď príde nová zaujímavá ponuka
```

Očakávaná odpoveď:
```
🚀 Iniciatíva spustená

`<id>` — Denný scraper sreality.cz pre 2+kk Praha pod 8M Kč
Pattern: `scraper`
Krokov: 6-8
Long-running: True

Driver beží na pozadí (~30s tick). Status: `/initiative <id>`
```

## 3. Sledovanie progresu

```
/initiatives           # zoznam všetkých aktívnych
/initiative <id>       # detail jednej (steps + status)
```

V Telegrame budú prichádzať notifikácie pri:
- Dokončení každého kroku (ak `metadata.notify_step=true`)
- Zlyhaní kroku po 3 pokusoch (PAUSED + alert)
- Finalizácii (✅ COMPLETED / 🌙 MONITORING / ⚠️ chyby)
- Pri scraper hits — keď monitor cron task nájde nové dáta

## 4. Control commands

```
/initiative pause <id>        # zastav driver pre túto iniciatívu
/initiative resume <id>       # obnov
/initiative cancel <id>       # natrvalo ukonči (ABANDONED)
```

## 5. Inspect na serveri

```bash
# Plán + meta
cat ~/Agent_Life_Space/.agent_runtime/initiatives_data/<id>/plan.json | jq
cat ~/Agent_Life_Space/.agent_runtime/initiatives_data/<id>/meta.json | jq

# Step results
ls -la ~/Agent_Life_Space/.agent_runtime/initiatives_data/<id>/steps/
cat ~/Agent_Life_Space/.agent_runtime/initiatives_data/<id>/steps/00_analyze.json | jq

# Dream learnings (post-completion)
ls ~/Agent_Life_Space/agent/brain/knowledge/initiatives/

# DB tasks
.venv/bin/python -c "
import asyncio, aiosqlite, orjson
async def main():
    db = await aiosqlite.connect('.agent_runtime/tasks/tasks.db')
    async with db.execute('SELECT data FROM tasks ORDER BY rowid DESC LIMIT 5') as cur:
        async for row in cur:
            d = orjson.loads(row[0])
            print(f\"{d['id']} {d['status']} {d['name'][:60]}\")
    await db.close()
asyncio.run(main())
"
```

## 6. Troubleshooting

| Symptom | Príčina | Riešenie |
|---|---|---|
| `initiative_engine_initialized` chýba | Import chyba | `journalctl --user -u agent-life-space \| grep initiative_engine_init_failed` — skontroluj traceback |
| Driver tick neexekvuje | `initiative.tick()` vyhadzuje výnimku | `journalctl ... \| grep initiative_tick_error` |
| Plánovač zlyhá 2× | LLM provider nedostupný / parsing chyba | Skontroluj LLM_BACKEND env var; pozri `initiative_planner_*_error` logy |
| `awaiting_approval` zaseknuté | Krok vyžaduje schválenie | `/yes <approval_id>` cez approval queue, alebo `/initiative resume <id>` |
| Driver odpojený od bota | Cron spustený pred Telegrambot init | Reštart agenta — bot sa attachne v 1. ticku driveru |

## 7. Advanced: ručné spustenie initiative cez API

```bash
curl -X POST http://127.0.0.1:8420/api/message \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"message": "/initiative urob mi scraper na sreality.cz..."}'
```
