# Server Environment
_Kategória: systems | Tags: server, hardware, runtime | Aktualizované: 2026-03-29_

## Deployment-Specific Facts
- Hostname, hardware a disk posture závisia od aktuálneho servera
- Nepredpokladaj fixný model počítača, OS image ani domáci adresár
- Reálne fakty majú prichádzať z runtime reportu, nie z upstream knowledge template

## Software
- Python beží vo venv alebo inom izolovanom runtime podľa nasadenia
- System service môže byť `systemd`, Docker, supervisor alebo iný process manager
- Repo path a data dir sú deployment-configured

## Prístupy
- SSH a shell prístup patria ownerovi alebo autorizovanému operátorovi
- Escalated oprávnenia sa majú povoľovať explicitne, nie predpokladať
- Venv a data dir majú zostať izolované od zvyšku systému

## Služba
- Start: `systemctl --user start agent-life-space`
- Stop: `systemctl --user stop agent-life-space`
- Restart: `systemctl --user restart agent-life-space`
- Logy: `journalctl --user -u agent-life-space`
