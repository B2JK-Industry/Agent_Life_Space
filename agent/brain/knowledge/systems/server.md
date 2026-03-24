# Server b2jk-agentlifespace
_Kategória: systems | Tags: server, hardware, ubuntu | Aktualizované: 2026-03-24_

## Hardware
- Počítač: Acer Aspire V3-572G
- CPU: Intel i7-5500U (4 jadrá, až 3GHz)
- RAM: 8GB DDR3
- GPU: NVIDIA GeForce 840M (nepoužívam)
- Disk: ~dostatočný (9.3% využitie)

## Software
- OS: Ubuntu 24.04.4 LTS
- Python: 3.x vo venv
- Systemd user service: agent-life-space

## Prístupy
- SSH pre Daniela
- Žiadne sudo pre Johna
- Venv izolovaný od systému

## Služba
- Start: `systemctl --user start agent-life-space`
- Stop: `systemctl --user stop agent-life-space`
- Restart: `systemctl --user restart agent-life-space`
- Logy: `journalctl --user -u agent-life-space`
