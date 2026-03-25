# Agent Life Space — Verifikačný Dokument

Tento dokument je určený pre verifikujúceho agenta. Obsahuje kompletný zoznam zmien, dôvody, a inštrukcie na overenie.

---

## Kontext

Na základe architektonického review bolo identifikovaných 6 kritických problémov. Tento dokument popisuje implementované opravy a ako ich overiť.

---

## Zmena 1: API Client (Fallback)

### Problém
Claude Code CLI (subprocess) je krehký základ pre produkčného agenta — žiadne SLA, Anthropic môže zmeniť TOS.

### Riešenie
Vytvorený `agent/core/llm_client.py` — pripravený Anthropic API klient ako fallback. CLI ostáva (Max subscription), ale API klient je ready ak bude treba.

### Zmenené súbory
- `agent/core/llm_client.py` — **NOVÝ** — AnthropicClient, cost estimation, usage tracking

### Čo overiť
```bash
# 1. Test existencie a importovateľnosti
python -c "from agent.core.llm_client import AnthropicClient, _estimate_cost, LLMCallResult; print('OK')"

# 2. Testy
python -m pytest tests/test_llm_client.py -v

# 3. Cost estimation overenie
python -c "
from agent.core.llm_client import _estimate_cost
# Opus: 15 input + 75 output per 1M tokens
assert _estimate_cost('claude-opus-4-6', 1_000_000, 1_000_000) == 90.0
# Sonnet: 3 + 15
assert _estimate_cost('claude-sonnet-4-6', 1_000_000, 1_000_000) == 18.0
# Haiku: 0.8 + 4
assert _estimate_cost('claude-haiku-4-5-20251001', 1_000_000, 1_000_000) == 4.8
# Zero tokens
assert _estimate_cost('claude-sonnet-4-6', 0, 0) == 0.0
print('Cost estimation: OK')
"
```

### Počet testov: 15
### Kritické kontroly:
- [ ] `_estimate_cost()` vracia správne hodnoty pre všetky 3 modely
- [ ] `AnthropicClient.chat()` vracia `LLMCallResult` s `success=True` pri úspechu
- [ ] `AnthropicClient.chat()` vracia `success=False` pri timeout/error
- [ ] Kumulatívna spotreba sa správne akumuluje cez viacero volaní
- [ ] Neznámy model defaultuje na Sonnet pricing

---

## Zmena 2: BLUEPRINT.md — Opravené Claimy

### Problém
"0 tokenov" pri vrstvách 3-5 je zavádzajúce. MiniLM žerie ~470MB RAM. Cache hit rate je reálne 5-10%.

### Riešenie
Všetky "0 tokenov" nahradené za "0 API callov" alebo "lokálny compute". Pridané sekcie: Resource Odhad, Learning Loop diagram, Bezpečnostný Model.

### Zmenené súbory
- `BLUEPRINT.md` — opravené claimy, nové sekcie

### Čo overiť
```bash
# 1. Žiadne "0 tokenov" v cascade diagrame
grep -c "0 tokenov" BLUEPRINT.md
# Výsledok by mal byť 0

# 2. Nové sekcie existujú
grep -c "Resource Odhad" BLUEPRINT.md        # ≥ 1
grep -c "Learning Loop" BLUEPRINT.md          # ≥ 1
grep -c "Bezpečnostný Model" BLUEPRINT.md     # ≥ 1

# 3. Cache hit rate je realisticky uvedený
grep "5-10%" BLUEPRINT.md  # musí existovať

# 4. Docker je povinný, nie voliteľný
grep "Docker (povinné" BLUEPRINT.md  # musí existovať

# 5. Vault v inštalácii
grep "setup_vault" BLUEPRINT.md  # musí existovať
```

### Kritické kontroly:
- [ ] Žiadne "0 tokenov" v cascade diagrame (nahradené "0 API callov" alebo "lokálny compute")
- [ ] MiniLM RAM cost uvedený (~470MB)
- [ ] Cache hit rate realistický (5-10%)
- [ ] Docker označený ako povinný
- [ ] Vault setup v inštalačnom návode
- [ ] Learning loop má konkrétny diagram (nie prázdna škatuľka)
- [ ] Cost estimate sekcia s reálnymi číslami

---

## Zmena 3: Docker Sandbox = Povinný

### Problém
Sandbox bol "voliteľný". Agent čo spúšťa kód bez sandboxu = bezpečnostná diera.

### Riešenie
- `DockerSandbox._ensure_docker()` — overí Docker pred prvým run
- `SandboxUnavailableError` — jasná chybová hláška
- Docker check pri štarte v `__main__.py`

### Zmenené súbory
- `agent/core/sandbox.py` — `_ensure_docker()`, `SandboxUnavailableError`, `_docker_verified` cache
- `agent/__main__.py` — Docker check pri štarte

### Čo overiť
```bash
# 1. Testy
python -m pytest tests/test_sandbox.py -v

# 2. SandboxUnavailableError existuje
python -c "from agent.core.sandbox import SandboxUnavailableError; print('OK')"

# 3. _ensure_docker volá check_docker
python -c "
import asyncio
from agent.core.sandbox import DockerSandbox, SandboxUnavailableError
async def test():
    s = DockerSandbox()
    # Ak Docker nie je dostupný, musí vyhodiť error
    try:
        await s._ensure_docker()
        print('Docker available')
    except SandboxUnavailableError:
        print('Docker unavailable — error correctly raised')
asyncio.run(test())
"

# 4. Docker check v __main__.py
grep "check_docker\|docker_available\|docker_not_available" agent/__main__.py
# Musí nájsť referencie
```

### Počet testov: 18
### Kritické kontroly:
- [ ] `SandboxUnavailableError` sa vyhodí ak Docker nie je dostupný
- [ ] `_ensure_docker()` cachuje výsledok (voláme len raz)
- [ ] `_docker_run()` volá `_ensure_docker()` pred každým run
- [ ] `run_python()`, `run_code()`, `run_command()` všetky prechádzajú cez `_docker_run()`
- [ ] Timeout je max 300s (`_MAX_TIMEOUT`)
- [ ] Neznámy jazyk vracia error (nie crash)
- [ ] `__main__.py` kontroluje Docker pri štarte

---

## Zmena 4: Vault Integrácia do Setupu

### Problém
Vault kód existoval ale setup ho nepoužíval. Env premenné boli na jednom mieste = single point of failure.

### Riešenie
`setup_vault.py` teraz ukladá aj GITHUB_TOKEN a TELEGRAM_BOT_TOKEN do vaultu. BLUEPRINT inštalácia zahŕňa vault setup.

### Zmenené súbory
- `scripts/setup_vault.py` — nová funkcia `_store_if_env()`, automatické ukladanie tokenov
- `BLUEPRINT.md` — vault setup v inštalácii

### Čo overiť
```bash
# 1. _store_if_env funkcia existuje
grep "_store_if_env" scripts/setup_vault.py
# Musí nájsť definíciu aj volania

# 2. GITHUB_TOKEN a TELEGRAM_BOT_TOKEN sa ukladajú
grep "GITHUB_TOKEN\|TELEGRAM_BOT_TOKEN" scripts/setup_vault.py
# Musí nájsť oba v _store_if_env volaniach

# 3. BLUEPRINT má vault v inštalácii
grep "setup_vault" BLUEPRINT.md

# 4. AGENT_VAULT_KEY je prvý v systemd
grep -A 5 "Minimum env vars" BLUEPRINT.md
```

### Kritické kontroly:
- [ ] `_store_if_env()` ukladá token do vaultu ak existuje v env
- [ ] `_store_if_env()` preskočí ak token už je vo vaulte
- [ ] Vault setup je krok 4 v BLUEPRINT inštalácii
- [ ] Systemd service má AGENT_VAULT_KEY

---

## Zmena 5: Learning Feedback Loop

### Problém
Learning modul claimoval "učí sa" ale nemal implementovaný feedback loop. Prázdna škatuľka.

### Riešenie
Pridané 3 nové metódy do `agent/brain/learning.py`:
- `process_outcome()` — po každom LLM call detekuje skills, aktualizuje, ukladá errors
- `get_advice_for_task()` — pred úlohou radí ktoré skills sú confident/risky
- `_detect_skills_in_text()`, `_extract_error()`, `_build_recommendation()` — helpery

Integrované do `agent/social/telegram_handler.py` — volá sa po každom Claude response.

### Zmenené súbory
- `agent/brain/learning.py` — nové metódy (153 riadkov)
- `agent/social/telegram_handler.py` — integrácia feedback loopu

### Čo overiť
```bash
# 1. Testy
python -m pytest tests/test_learning_feedback.py -v

# 2. process_outcome existuje a funguje
python -c "
import tempfile, os
from pathlib import Path
tmp = tempfile.mkdtemp()
skills = os.path.join(tmp, 'skills.json')
kb = os.path.join(tmp, 'knowledge')
os.makedirs(os.path.join(kb, 'learned'))
os.makedirs(os.path.join(kb, 'skills'))
os.makedirs(os.path.join(kb, 'systems'))
os.makedirs(os.path.join(kb, 'people'))
os.makedirs(os.path.join(kb, 'projects'))

from agent.brain.learning import LearningSystem
ls = LearningSystem(skills_path=skills, knowledge_dir=kb)

# Test feedback loop
result = ls.process_outcome(
    task_description='spusti pytest',
    reply='Spustil som pytest a 10 testov prešlo. Hotovo.',
    success=True
)
assert 'pytest:success' in result['updates'], f'Expected pytest:success, got {result}'
print('process_outcome: OK')

# Test advice
advice = ls.get_advice_for_task('pytest')
assert 'recommendation' in advice
print('get_advice_for_task: OK')
"

# 3. Integrácia v telegram_handler
grep "process_outcome\|LearningSystem" agent/social/telegram_handler.py
# Musí nájsť volanie process_outcome
```

### Počet testov: 22
### Kritické kontroly:
- [ ] `_detect_skills_in_text()` správne detekuje curl, git, pytest, docker, python z textu
- [ ] `_extract_error()` extrahuje error message z "Error:", "Chyba:", "Traceback:"
- [ ] `process_outcome()` aktualizuje skills.json pri úspechu/zlyhaní
- [ ] `process_outcome()` ukladá errors do knowledge base
- [ ] `get_advice_for_task()` vracia confident/risky skills správne
- [ ] `_build_recommendation()` nikdy nevracia prázdny string
- [ ] Integrácia v telegram_handler.py volá `process_outcome()` po každom LLM response
- [ ] Fallback: ak learning zlyhá, necrashne to celý handler (try/except)

---

## Zmena 6: Error Recovery, Monitoring, Cost Tracking

### Problém
Chýbalo: čo keď agent crashne uprostred tasku? Ako vieš že funguje správne?

### Riešenie
- Circuit breaker v `agent_loop.py` — 3 consecutive errors → 30s pauza
- Error counting a error rate v `get_status()`
- Crash log pri fatal error v `__main__.py`

### Zmenené súbory
- `agent/core/agent_loop.py` — circuit breaker, error_count, consecutive_errors, error_rate
- `agent/__main__.py` — crash log do `agent/logs/last_crash.txt`
- `agent/core/models.py` — cost odhad v docstringu

### Čo overiť
```bash
# 1. Testy
python -m pytest tests/test_agent_loop.py -v

# 2. Circuit breaker je v kóde
grep "consecutive_errors >= 3" agent/core/agent_loop.py
grep "asyncio.sleep(30)" agent/core/agent_loop.py
# Oba musia existovať

# 3. Error rate v get_status
python -c "
from agent.core.agent_loop import AgentLoop
loop = AgentLoop()
loop._processed_count = 7
loop._error_count = 3
status = loop.get_status()
assert status['error_rate'] == 0.3, f'Expected 0.3, got {status[\"error_rate\"]}'
assert 'total_errors' in status
assert 'consecutive_errors' in status
print('get_status: OK')
"

# 4. Crash log
grep "last_crash" agent/__main__.py
grep "traceback" agent/__main__.py
# Oba musia existovať

# 5. Cost v models.py
grep "Cost odhad" agent/core/models.py
```

### Počet testov: 17
### Kritické kontroly:
- [ ] Circuit breaker triggeruje sa po 3 consecutive errors
- [ ] Circuit breaker pauzuje na 30s
- [ ] Consecutive errors sa resetujú po úspechu
- [ ] `get_status()` vracia `total_errors`, `consecutive_errors`, `error_rate`
- [ ] Error rate sa počíta správne (errors / total)
- [ ] Division by zero handling (0/1 = 0.0)
- [ ] Crash log sa uloží do `agent/logs/last_crash.txt` pri fatal error
- [ ] Crash log obsahuje timestamp, error message, traceback

---

## Celkový Test Suite

```bash
# Spusti VŠETKY testy
python -m pytest tests/ -v --tb=short

# Očakávaný výsledok: 472+ passed, 4 skipped, 0 failed

# Nové test súbory:
# tests/test_llm_client.py        — 15 testov (API client)
# tests/test_sandbox.py           — 18 testov (Docker sandbox)
# tests/test_agent_loop.py        — 17 testov (circuit breaker)
# tests/test_learning_feedback.py — 22 testov (learning feedback loop)
# Spolu: 72 nových testov
```

---

## Verifikačný Checklist

### Bezpečnosť
- [ ] Žiadne private keys v logoch alebo výstupoch
- [ ] Docker sandbox je povinný (nie voliteľný)
- [ ] Vault ukladá citlivé tokeny šifrovane
- [ ] `--dangerously-skip-permissions` je len pre programovacie úlohy cez CLI
- [ ] Finance modul vyžaduje human approval pre transakcie

### Funkčnosť
- [ ] 472+ testov prechádza
- [ ] Learning feedback loop detekuje skills z textu
- [ ] Circuit breaker zastaví cascading failures
- [ ] Cost estimation je správna pre Haiku/Sonnet/Opus
- [ ] Crash log sa zapisuje pri fatal error

### Dokumentácia
- [ ] BLUEPRINT.md neobsahuje "0 tokenov" v cascade
- [ ] Learning loop je popísaný s konkrétnym diagramom
- [ ] Resource odhad má reálne čísla
- [ ] Cache hit rate je 5-10% (nie nerealisticky vysoký)

### Architektúra
- [ ] CLI ostáva ako runtime (Max subscription)
- [ ] API client je ready ako fallback
- [ ] Vault je integrovaný do setup procesu
- [ ] Error recovery funguje na viacerých úrovniach (circuit breaker, watchdog, crash log)

---

## Nasadzovacie Scenáre (E2E na serveri)

Tieto scenáre sú pre agenta (John) po nasadení na server. Spúšťaj postupne, každý scenár overuje jednu zmenu v produkcii.

### Scenár 0: Pre-deploy príprava

```bash
# Na serveri:
cd ~/agent-life-space
git pull origin main

# Aktivuj venv
source .venv/bin/activate

# Nainštaluj závislosti (ak sa zmenili)
pip install -e .

# Spusti unit testy PRED nasadením
python -m pytest tests/ -q --tb=short
# MUSÍ: 472+ passed, 0 failed
# Ak zlyhá čokoľvek → NEPLOY, oprav najprv

# Vault setup (ak ešte nebol)
python scripts/setup_vault.py
```

---

### Scenár 1: Docker Sandbox Overenie

**Čo testuje:** Docker je povinný a sandbox funguje.

```bash
# Krok 1: Over Docker
docker --version
# MUSÍ: Docker version X.X.X

# Krok 2: Over sandbox z Python
python -c "
import asyncio
from agent.core.sandbox import DockerSandbox

async def test():
    s = DockerSandbox()

    # Test Docker dostupnosť
    status = await s.check_docker()
    assert status['available'], 'Docker nie je dostupný!'
    print('Docker: OK')

    # Test Python sandbox
    result = await s.run_python('print(2+2)')
    assert result.success, f'Sandbox fail: {result.stderr}'
    assert '4' in result.stdout
    print(f'Python sandbox: OK (stdout: {result.stdout.strip()})')

    # Test timeout
    result = await s.run_python('import time; time.sleep(999)', timeout=3)
    assert result.timed_out, 'Timeout sa nespustil!'
    print('Timeout: OK')

    # Test read-only filesystem
    result = await s.run_python(\"import os; os.makedirs('/usr/test')\")
    assert not result.success, 'Read-only filesystem nefunguje!'
    print('Read-only FS: OK')

    print('\\nVšetky sandbox testy: PASSED')

asyncio.run(test())
"
```

**Očakávaný výsledok:**
```
Docker: OK
Python sandbox: OK (stdout: 4)
Timeout: OK
Read-only FS: OK
Všetky sandbox testy: PASSED
```

---

### Scenár 2: Vault Overenie

**Čo testuje:** Vault je funkčný, šifruje/dešifruje, neukladá plain text.

```bash
python -c "
import os
from agent.vault.secrets import SecretsManager

vault_dir = os.path.expanduser('~/agent-life-space/agent/vault')
master_key = os.environ.get('AGENT_VAULT_KEY', '')
assert master_key, 'AGENT_VAULT_KEY nie je nastavený!'

vault = SecretsManager(vault_dir=vault_dir, master_key=master_key)

# 1. Over existujúce kľúče
secrets = vault.list_secrets()
print(f'Kľúče vo vaulte: {secrets}')
assert 'ETH_ADDRESS' in secrets, 'ETH_ADDRESS chýba!'
assert 'BTC_ADDRESS' in secrets, 'BTC_ADDRESS chýba!'

# 2. Prečítaj adresy (verejné — bezpečné)
eth = vault.get_secret('ETH_ADDRESS')
btc = vault.get_secret('BTC_ADDRESS')
print(f'ETH: {eth}')
print(f'BTC: {btc}')

# 3. Over že private keys existujú (nikdy nevypisuj!)
assert 'ETH_PRIVATE_KEY' in secrets, 'ETH_PRIVATE_KEY chýba!'
assert 'BTC_PRIVATE_KEY' in secrets, 'BTC_PRIVATE_KEY chýba!'
print('Private keys: existujú (nevypisujem)')

# 4. Test zápis/čítanie/mazanie
vault.set_secret('TEST_KEY', 'test_value_12345')
assert vault.get_secret('TEST_KEY') == 'test_value_12345'
vault.delete_secret('TEST_KEY')
assert 'TEST_KEY' not in vault.list_secrets()
print('CRUD: OK')

# 5. Over audit log
log = vault.get_audit_log()
print(f'Audit log entries: {len(log)}')

print('\\nVšetky vault testy: PASSED')
"
```

**Očakávaný výsledok:**
```
Kľúče vo vaulte: ['ETH_ADDRESS', 'ETH_PRIVATE_KEY', 'BTC_ADDRESS', 'BTC_PRIVATE_KEY', ...]
ETH: 0x...
BTC: 1... alebo bc1...
Private keys: existujú (nevypisujem)
CRUD: OK
Audit log entries: N
Všetky vault testy: PASSED
```

---

### Scenár 3: Learning Feedback Loop

**Čo testuje:** Agent sa učí z výsledkov — skills sa aktualizujú, errors sa ukladajú.

```bash
python -c "
import tempfile, os, json
from pathlib import Path

# Použijeme reálne skills.json
from agent.brain.learning import LearningSystem

tmp = tempfile.mkdtemp()
skills_path = os.path.join(tmp, 'skills.json')
kb_dir = os.path.join(tmp, 'knowledge')
for d in ['skills', 'systems', 'people', 'projects', 'learned']:
    os.makedirs(os.path.join(kb_dir, d))

ls = LearningSystem(skills_path=skills_path, knowledge_dir=kb_dir)

# === Test 1: Success feedback ===
result = ls.process_outcome(
    task_description='spusti testy',
    reply='Spustil som pytest a všetkých 50 testov prešlo. Hotovo.',
    success=True
)
print(f'Success feedback: {result[\"updates\"]}')
assert 'pytest:success' in result['updates']

# === Test 2: Failure feedback s knowledge save ===
result = ls.process_outcome(
    task_description='spusti docker',
    reply='Docker run zlyhal. Error: permission denied, Got permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock',
    success=False
)
print(f'Failure feedback: {result[\"updates\"]}')
assert result['knowledge_saved'] is True

# === Test 3: Advice pred úlohou ===
# Najprv nechaj pytest dosiahnuť vysokú confidence
for _ in range(10):
    ls.skills.record_success('pytest')

advice = ls.get_advice_for_task('pytest')
print(f'Advice: {advice[\"recommendation\"]}')
assert len(advice['confident_skills']) > 0

# === Test 4: Skills sú persistované ===
with open(skills_path) as f:
    saved = json.load(f)
assert saved['pytest']['success_count'] >= 10
print(f'Persisted skills: pytest has {saved[\"pytest\"][\"success_count\"]} successes')

# === Test 5: Knowledge bola uložená ===
learned_dir = os.path.join(kb_dir, 'learned')
files = os.listdir(learned_dir)
assert len(files) > 0, 'Knowledge base prázdna!'
print(f'Knowledge entries saved: {len(files)}')

print('\\nVšetky learning testy: PASSED')
"
```

---

### Scenár 4: Circuit Breaker

**Čo testuje:** Agent loop sa zastaví po 3 consecutive errors.

```bash
python -c "
import asyncio
from agent.core.agent_loop import AgentLoop

# Verifikuj konštanty v kóde
import inspect
source = inspect.getsource(AgentLoop._worker)
assert 'self._consecutive_errors >= 3' in source, 'Circuit breaker threshold nie je 3!'
assert 'asyncio.sleep(30)' in source, 'Circuit breaker pause nie je 30s!'
print('Circuit breaker konštanty: OK (threshold=3, pause=30s)')

# Verifikuj get_status formát
loop = AgentLoop()
status = loop.get_status()
required_fields = ['queue_size', 'processing', 'total_processed', 'total_errors',
                   'consecutive_errors', 'running', 'error_rate']
for field in required_fields:
    assert field in status, f'Chýba field: {field}'
print(f'get_status fields: OK ({len(required_fields)} fields)')

# Verifikuj error_rate výpočet
loop._processed_count = 8
loop._error_count = 2
status = loop.get_status()
assert status['error_rate'] == 0.2, f'Error rate wrong: {status[\"error_rate\"]}'
print(f'Error rate calculation: OK (2/10 = 0.2)')

# Verifikuj zero division protection
loop2 = AgentLoop()
assert loop2.get_status()['error_rate'] == 0.0
print('Zero division protection: OK')

print('\\nVšetky circuit breaker testy: PASSED')
"
```

---

### Scenár 5: Telegram E2E (manuálny)

**Čo testuje:** Celý flow cez Telegram po nasadení.

Spusti agenta: `systemctl --user restart agent-life-space`

Potom cez Telegram pošli tieto správy a over odpovede:

| # | Pošli | Očakávaná reakcia | Overuje |
|---|-------|-------------------|---------|
| 1 | `/status` | Agent status (running, spomienky, úlohy) | Cascade vrstva 1 — slash command |
| 2 | `/health` | CPU%, RAM%, moduly | Watchdog funguje |
| 3 | `/usage` | Požiadavky: 0, Náklady: $0 | Usage tracking |
| 4 | `Ahoj John` | Krátka odpoveď (1-2 vety) + 💰 Haiku | Cascade: Haiku pre jednoduché |
| 5 | `/usage` | Požiadavky: 1, Náklady > $0 | Usage sa aktualizoval |
| 6 | `Aký je stav servera?` | Interná odpoveď BEZ 💰 | Cascade: dispatcher/semantic router |
| 7 | `Koľko mám úloh?` | Odpoveď z interných modulov | Cascade: dispatcher regex |
| 8 | `/sandbox print(2+2)` | Sandbox výstup: 4 | Docker sandbox povinný |
| 9 | `/wallet` | ETH + BTC adresy | Vault integrácia |
| 10 | `Napíš test pre funkciu max()` | Dlhšia odpoveď + 💰 Opus | Cascade: Opus pre programovanie |
| 11 | `/usage` | Požiadavky: 3+, cost rastie | Kumulatívne tracking |
| 12 | `/consolidate` | Konsolidácia report | Memory consolidation |

**Po každej odpovedi s 💰 over:**
- Model sa správne vybral (haiku/sonnet/opus)
- Token count je nenulový
- Cost je nenulový

---

### Scenár 6: Crash Recovery (manuálny)

**Čo testuje:** Systém sa zotaví z pádu.

```bash
# 1. Over že systemd reštartuje service
systemctl --user status agent-life-space | grep "Restart="
# MUSÍ: Restart=always

# 2. Simuluj crash (pošli SIGKILL)
kill -9 $(systemctl --user show agent-life-space --property=MainPID --value)

# 3. Počkaj 15s a over že sa reštartoval
sleep 15
systemctl --user is-active agent-life-space
# MUSÍ: active

# 4. Over crash log
ls -la ~/agent-life-space/agent/logs/last_crash.txt
# Ak existuje, crash bol zaznamenaný
```

---

### Scenár 7: Watchdog Health Monitoring

**Čo testuje:** Watchdog monitoruje moduly a generuje alerty.

```bash
python -c "
import asyncio
from agent.core.agent import AgentOrchestrator

async def test():
    agent = AgentOrchestrator()
    await agent.initialize()

    # Over registrované moduly
    states = agent.watchdog.get_module_states()
    print(f'Registered modules: {list(states.keys())}')
    assert len(states) >= 5, 'Menej ako 5 modulov!'

    # Over health snapshot
    health = agent.watchdog.snapshot_health()
    print(f'CPU: {health.cpu_percent:.1f}%')
    print(f'RAM: {health.memory_percent:.1f}%')
    print(f'Disk: {health.disk_percent:.1f}%')
    print(f'Moduly: {health.modules}')

    # Over stats
    stats = agent.watchdog.get_stats()
    print(f'Watchdog stats: {stats}')

    await agent.stop()
    print('\\nWatchdog test: PASSED')

asyncio.run(test())
"
```

---

### Scenár 8: Full Integration — Agent Lifecycle

**Čo testuje:** Kompletný životný cyklus agenta.

```bash
python -c "
import asyncio
from agent.core.agent import AgentOrchestrator
from agent.memory.store import MemoryEntry, MemoryType
from agent.tasks.manager import TaskStatus

async def test():
    agent = AgentOrchestrator()
    await agent.initialize()

    # 1. Memory store + query
    mem_id = await agent.memory.store(MemoryEntry(
        content='Integration test memory entry',
        memory_type=MemoryType.EPISODIC,
        tags=['test', 'integration'],
        source='verification',
        importance=0.5,
    ))
    results = await agent.memory.query(keyword='integration', limit=1)
    assert len(results) >= 1
    print('Memory: OK')

    # 2. Task create + status
    task = await agent.tasks.create_task(
        name='Verification test task',
        importance=0.5, urgency=0.5
    )
    assert task.status == TaskStatus.QUEUED
    print('Tasks: OK')

    # 3. Brain decision
    decision = agent.brain.should_use_llm('aký je čas?')
    print(f'Brain decision: {decision.method.value} (confidence: {decision.confidence})')

    # 4. Finance
    stats = agent.finance.get_stats()
    print(f'Finance: net=${stats[\"net\"]:.2f}')

    # 5. Watchdog
    health = agent.watchdog.snapshot_health()
    print(f'Health: CPU={health.cpu_percent:.1f}%, RAM={health.memory_percent:.1f}%')

    # 6. Overall status
    status = agent.get_status()
    assert status['running'] is False  # not started, just initialized
    print(f'Status fields: {list(status.keys())}')

    await agent.stop()
    print('\\nFull lifecycle test: PASSED')

asyncio.run(test())
"
```

---

## Poradie Nasadenia

1. **Spusti Scenár 0** — unit testy musia prejsť (472+ passed)
2. **Spusti Scenár 8** — lifecycle test (bez Telegram)
3. **Spusti Scenár 7** — watchdog health
4. **Spusti Scenár 1** — Docker sandbox
5. **Spusti Scenár 2** — vault
6. **Spusti Scenár 3** — learning loop
7. **Spusti Scenár 4** — circuit breaker
8. **Nasaď service** — `systemctl --user restart agent-life-space`
9. **Spusti Scenár 5** — Telegram E2E (manuálne)
10. **Spusti Scenár 6** — crash recovery

Ak akýkoľvek scenár zlyhá → STOP. Oprav problém, spusti znova od začiatku.

---

## Rollback Plán

Ak deploy zlyhá po scenári 5+ (service beží ale niečo nefunguje):

```bash
# 1. Zisti posledný fungujúci commit
git log --oneline -10

# 2. Rollback na posledný fungujúci commit
git checkout <last-working-commit-hash>

# 3. Reštartuj service
systemctl --user restart agent-life-space

# 4. Over že funguje
sleep 10
systemctl --user is-active agent-life-space

# 5. Pošli /status cez Telegram — over odpoveď
```

**Dôležité:** Rollback cez `git checkout` je bezpečný — nestraťíš žiadne dáta (pamäť, úlohy, vault sú v SQLite/files mimo git).
