# Systémový monitoring
_Kategória: skills | Tags: system, health, monitoring | Aktualizované: 2026-03-24_

## Príkazy
- `free -h` — RAM využitie
- `df -h /` — disk využitie
- `ps aux | head` — aktívne procesy
- `uptime` — uptime servera
- `journalctl --user -u agent-life-space` — logy služby

## Aktuálny stav (2026-03-24)
- CPU: ~0%
- RAM: 8.2% (645 MB / 7855 MB)
- Disk: 9.3%
- Uptime: 13h
- Všetky moduly: healthy

## Watchdog
- `agent/core/watchdog.py` — heartbeat monitoring
- Sleduje zdravie modulov
- Vie reštartovať zlyhané moduly
