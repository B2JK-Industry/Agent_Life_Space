"""
Agent Life Space — Main Entry Point

Usage:
    python -m agent              # Start the agent
    python -m agent --status     # Show agent status
    python -m agent --health     # Show system health
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from agent.core.agent import AgentOrchestrator

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(colors=True),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger(__name__)


PIDFILE = os.environ.get("AGENT_PIDFILE_PATH", "/tmp/agent-life-space.pid")


def _resolve_default_data_dir(root: Path | None = None) -> str:
    """Prefer explicit runtime dirs, but preserve legacy in-repo data if present."""
    configured = os.environ.get("AGENT_DATA_DIR", "").strip()
    if configured:
        return configured

    base = root or Path.cwd()
    legacy_root = base / "agent"
    legacy_markers = (
        legacy_root / "approval" / "approvals.db",
        legacy_root / "build" / "builds.db",
        legacy_root / "control" / "control.db",
        legacy_root / "review" / "reviews.db",
        legacy_root / "identity" / "owner_profile.json",
    )
    if any(marker.exists() for marker in legacy_markers):
        return "agent"
    return ".agent_runtime"


DEFAULT_DATA_DIR = _resolve_default_data_dir()


def _apply_runtime_env_defaults(data_dir: str) -> None:
    """Keep CLI/runtime helpers aligned with the selected data directory."""
    os.environ.update({"AGENT_DATA_DIR": data_dir})


def _kill_orphan_agent_processes() -> int:
    """Kill any stray `python -m agent` processes left from prior failed runs.

    Bug pozorovaný 2× v praxi: po failed restart-e zostal v `ps` orphan PID
    bez pidfile (parent=1). Nový proces stále spadol kvôli Telegram getUpdates
    conflict (dvaja pollers). Tento helper preventívne kill-ne všetky orphan
    `python -m agent` procesy okrem aktuálneho PIDu.

    Bezpečné: kill iba procesy bežiace pod rovnakým UID + matchujúce CMD.
    Returns count of killed processes.
    """
    import signal as _signal
    import subprocess as _subprocess

    my_pid = os.getpid()
    killed = 0
    try:
        # Find all python processes running `agent` module (or `python -m agent`)
        result = _subprocess.run(
            ["pgrep", "-fu", str(os.getuid()), "python -m agent"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().splitlines():
            try:
                pid = int(line.strip())
                if pid == my_pid:
                    continue
                # Re-verify it's truly an agent process (not pgrep itself)
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    cmdline = f.read().decode(errors="replace")
                if "python" not in cmdline or "agent" not in cmdline:
                    continue
                logger.warning(
                    "killing_orphan_agent_process", pid=pid, cmdline=cmdline[:100]
                )
                os.kill(pid, _signal.SIGTERM)
                killed += 1
            except (ProcessLookupError, FileNotFoundError, ValueError, PermissionError):
                continue
    except (FileNotFoundError, _subprocess.TimeoutExpired):
        # pgrep nedostupný (non-Linux) alebo timeout — skip silently
        pass

    if killed:
        # Daj orphans 2s na clean shutdown
        import time as _time
        _time.sleep(2)
    return killed


def _check_pidfile() -> None:
    """Prevent duplicate agent instances. Fail-fast if already running.

    Pred check-om PIDfilu zabíja orphan agent procesy bez pidfile. Toto rieši
    častý failure mode kde systemd reštart agenta zlyhá kvôli Telegram getUpdates
    conflict s previous orphan instance.
    """
    # POZN: _kill_orphan_agent_processes() bolo dočasne vypnuté — spôsobilo
    # restart bomb (každý nový daemon zabil predchádzajúci → systemd restart loop).
    # Lepšie: spustenie cez systemd s `KillMode=process` a manuálny kill <pid>
    # pri orphan situácii. Refactor neskôr.
    # _kill_orphan_agent_processes()

    if os.path.exists(PIDFILE):
        try:
            with open(PIDFILE) as _pf:
                old_pid = int(_pf.read().strip())
            # Check if process is actually alive
            os.kill(old_pid, 0)
            # Process exists — refuse to start
            logger.error("agent_already_running", pid=old_pid, pidfile=PIDFILE)
            print(f"Agent už beží (PID {old_pid}). Použi 'kill {old_pid}' alebo zmaž {PIDFILE}.")
            sys.exit(1)
        except (ProcessLookupError, ValueError):
            # PID doesn't exist or file is corrupt — stale pidfile, remove it
            os.remove(PIDFILE)
        except PermissionError:
            # Process exists but owned by different user
            logger.error("agent_already_running_different_user", pidfile=PIDFILE)
            sys.exit(1)

    # Write our PID
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))


def _remove_pidfile() -> None:
    """Remove PID file on shutdown."""
    try:
        if os.path.exists(PIDFILE):
            os.remove(PIDFILE)
    except OSError:
        pass


async def run_agent(data_dir: str = "agent") -> None:
    """Main agent loop with graceful shutdown."""
    _apply_runtime_env_defaults(data_dir)
    _check_pidfile()

    # Configure tiered structured logging BEFORE anything else.
    # Long-term sink keeps INFO+/AUDIT/build/finance/security events for
    # ~30 days; short-term sink keeps verbose DEBUG/pipeline/poll events
    # for ~6 hours. The cron loop runs LogRetentionManager hourly to
    # delete files older than the configured window.
    if os.environ.get("AGENT_LOG_TIERED", "1") == "1":
        from agent.logs.logger import setup_tiered_logging
        log_dir = os.environ.get(
            "AGENT_LOG_DIR",
            os.path.join(data_dir, "logs"),
        )
        # Pin the resolved path into the environment so the cron-side
        # LogRetentionManager prunes the *same* directory the logging
        # setup is writing to. Without this the cron fallback used
        # get_project_root()/agent/logs while __main__ wrote into
        # <data_dir>/logs and retention silently swept nothing.
        os.environ.update({"AGENT_LOG_DIR": log_dir})
        # Resolve long retention in HOURS — same env var the cron-side
        # LogRetentionManager reads. Default is 720h (30 days).
        # Backwards compat: if AGENT_LOG_LONG_RETENTION_HOURS is unset
        # but AGENT_LOG_LONG_RETENTION_DAYS is, honour DAYS once and
        # warn the operator that the variable is deprecated.
        env_hours = os.environ.get("AGENT_LOG_LONG_RETENTION_HOURS", "").strip()
        env_days_legacy = os.environ.get("AGENT_LOG_LONG_RETENTION_DAYS", "").strip()
        if env_hours:
            try:
                long_retention_hours = int(env_hours)
            except ValueError:
                logger.warning(
                    "log_retention_hours_invalid",
                    raw=env_hours,
                    fallback=720,
                )
                long_retention_hours = 720
        elif env_days_legacy:
            try:
                long_retention_hours = int(env_days_legacy) * 24
            except ValueError:
                long_retention_hours = 720
            logger.warning(
                "log_retention_days_env_deprecated",
                hint=(
                    "AGENT_LOG_LONG_RETENTION_DAYS is deprecated; use "
                    "AGENT_LOG_LONG_RETENTION_HOURS instead. The cron "
                    "prune sweep only reads the HOURS variable, so "
                    "setting only DAYS leaves the two halves out of sync."
                ),
                derived_hours=long_retention_hours,
            )
            # Pin the equivalent HOURS value so the cron sweep agrees.
            os.environ.update({
                "AGENT_LOG_LONG_RETENTION_HOURS": str(long_retention_hours),
            })
        else:
            long_retention_hours = 720
        try:
            paths = setup_tiered_logging(
                log_dir,
                long_retention_hours=long_retention_hours,
                short_retention_hours=int(
                    os.environ.get("AGENT_LOG_SHORT_RETENTION_HOURS", "6"),
                ),
            )
            logger.info("logging_tiered_enabled", **paths)
        except Exception as e:
            # Logging setup must never crash the agent. Fall back to
            # the previous behaviour (terminal redirect or stderr).
            logger.warning("logging_tiered_setup_failed", error=str(e))

    # Redirect logs to file BEFORE anything else if terminal mode is active
    enable_terminal = (
        os.environ.get("AGENT_TERMINAL", "0") == "1"
        or "--terminal" in sys.argv
    )
    if enable_terminal:
        from agent.social.terminal_repl import redirect_logs_to_file
        redirect_logs_to_file()

    agent = AgentOrchestrator(data_dir=data_dir)

    # Handle shutdown signals
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def signal_handler() -> None:
        logger.info("shutdown_signal_received")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await agent.initialize()

        # Docker check — sandbox je povinný pre programovacie úlohy
        from agent.core.sandbox import DockerSandbox
        sandbox = DockerSandbox()
        docker_status = await sandbox.check_docker()
        docker_available = docker_status.get("available", False)
        if docker_available:
            logger.info("docker_available", status="ok")
        else:
            logger.warning(
                "docker_not_available",
                error=docker_status.get("error", "unknown"),
                hint="Docker je povinný pre sandbox. Programovacie úlohy budú odmietnuté.",
            )
        # Store docker status as agent attribute (not env var mutation)
        agent._docker_available = docker_available
        # SECURITY: Sandbox-only mode is DEFAULT. Host file access blocked unless
        # explicitly overridden with AGENT_SANDBOX_ONLY=0.
        if os.environ.get("AGENT_SANDBOX_ONLY") is None:
            os.environ.setdefault("AGENT_SANDBOX_ONLY", "1")
            logger.info("sandbox_only_mode", status="enabled (default)")
        elif os.environ.get("AGENT_SANDBOX_ONLY") == "0":
            logger.warning("sandbox_only_disabled", hint="Host file access enabled. CLI has full FS access.")

        # Startup configuration summary
        logger.info(
            "deployment_config",
            project_root=agent._data_dir.parent if hasattr(agent, "_data_dir") else "unknown",
            api_port=os.environ.get("AGENT_API_PORT", "8420"),
            api_host=os.environ.get("AGENT_API_HOST", "127.0.0.1"),
            vault_ready=agent._secrets_manager.is_ready if hasattr(agent, "_secrets_manager") and agent._secrets_manager else False,
            docker=docker_available,
            sandbox=os.environ.get("AGENT_SANDBOX_ONLY", "1"),
            pidfile=PIDFILE,
        )

        # Start agent in background
        agent_task = asyncio.create_task(agent.start())

        # Strong references to fire-and-forget background tasks. asyncio
        # only keeps weak references internally, so anything we don't store
        # here can be garbage-collected mid-execution.
        background_tasks: set[asyncio.Task[Any]] = set()

        # Start Telegram bot if token is available
        telegram_task = None
        tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        tg_user_id = os.environ.get("TELEGRAM_USER_ID", "")
        if tg_token:
            from agent.social.telegram_bot import TelegramBot
            from agent.social.telegram_handler import TelegramHandler

            # Support comma-separated user IDs: "123,456,789"
            allowed_ids = [int(x.strip()) for x in tg_user_id.split(",") if x.strip()] if tg_user_id else []
            owner_name = os.environ.get("AGENT_OWNER_NAME", "owner")
            bot = TelegramBot(
                token=tg_token,
                allowed_user_ids=allowed_ids,
                owner_name=owner_name,
                state_dir=os.path.join(data_dir, "telegram"),
            )
            # Start agent work loop
            from agent.core.agent_loop import AgentLoop
            owner_id = int(tg_user_id.split(",")[0]) if tg_user_id else 0
            work_loop = AgentLoop(telegram_bot=bot)
            agent.agent_loop = work_loop
            work_loop_task = asyncio.create_task(work_loop.start())

            # Initialize AgentBrain (channel-agnostic) + ToolExecutor
            from agent.core.brain import AgentBrain
            from agent.core.sandbox_executor import SandboxExecutor
            from agent.core.tool_executor import ToolExecutor

            sandbox_executor = SandboxExecutor()
            tool_executor = ToolExecutor(
                agent=agent,
                sandbox=sandbox_executor,
                operator_controls=agent.operator_controls,
            )
            brain = AgentBrain(agent=agent, work_loop=work_loop, owner_chat_id=owner_id)
            brain._tool_executor = tool_executor  # Available for tool use loop

            handler = TelegramHandler(
                agent, bot=bot, work_loop=work_loop, owner_chat_id=owner_id,
                brain=brain,
            )
            bot.on_message(handler.handle)
            telegram_task = asyncio.create_task(bot.start())
            logger.info("telegram_bot_enabled")

            # Preload semantic models in background.
            # IMPORTANT: keep a strong reference to the task. asyncio only
            # holds a weak reference internally, so an unstored task can be
            # garbage-collected mid-flight ("Task was destroyed but it is
            # pending!"). Tracking it also lets shutdown cancel it cleanly.
            async def _preload_models() -> None:
                try:
                    from agent.brain.semantic_router import _get_intent_embeddings, _load_model
                    _load_model()
                    _get_intent_embeddings()
                    logger.info("semantic_router_preloaded")

                    from agent.memory.rag import RAGIndex
                    rag = RAGIndex()
                    count = rag.build_index()
                    logger.info("rag_index_preloaded", documents=count)
                except Exception as e:
                    logger.warning("preload_failed", error=str(e))
            preload_task = asyncio.create_task(_preload_models())
            background_tasks.add(preload_task)
            preload_task.add_done_callback(background_tasks.discard)

            # Start Agent-to-Agent API (s autentifikáciou)
            from agent.social.agent_api import AgentAPI
            agent_api_keys = []
            agent_api_key = os.environ.get("AGENT_API_KEY", "")
            if agent_api_key:
                agent_api_keys.append(agent_api_key)
            # SECURITY: bind na 127.0.0.1 default — cloudflare tunnel sa pripája lokálne
            # Ak chceš exponovať priamo (nie cez tunnel), použi AGENT_API_BIND=0.0.0.0
            api_bind = os.environ.get("AGENT_API_BIND", "127.0.0.1")
            agent_api = AgentAPI(
                handler_callback=handler.handle,
                agent=agent,
                api_keys=agent_api_keys if agent_api_keys else None,
                bind_host=api_bind,
            )
            agent_api_task = asyncio.create_task(agent_api.start())
            logger.info("agent_api_enabled", port=8420, bind=api_bind,
                       auth="key" if agent_api_keys else "NONE — SET AGENT_API_KEY!")

            # Start cron (agent's initiative)
            from agent.core.cron import AgentCron
            cron = AgentCron(agent, telegram_bot=bot, owner_chat_id=owner_id)
            cron_task = asyncio.create_task(cron.start())
            logger.info("cron_enabled", owner_chat_id=owner_id)
        else:
            work_loop = None
            agent.agent_loop = None
            work_loop_task = None
            handler = None
            cron = None
            cron_task = None
            agent_api = None
            agent_api_task = None
            logger.info("telegram_bot_disabled", reason="no TELEGRAM_BOT_TOKEN")

        # Start terminal REPL if requested
        terminal_repl = None
        terminal_task = None
        enable_terminal = (
            os.environ.get("AGENT_TERMINAL", "0") == "1"
            or "--terminal" in sys.argv
        )
        if enable_terminal and handler is not None:
            from agent.social.terminal_repl import TerminalREPL

            owner_id_for_terminal = int(tg_user_id.split(",")[0]) if tg_user_id else 0
            terminal_repl = TerminalREPL(
                handler_callback=handler.handle,
                owner_user_id=owner_id_for_terminal,
            )
            terminal_task = asyncio.create_task(terminal_repl.start())
            logger.info("terminal_repl_enabled")

        # Wait for shutdown signal
        await shutdown_event.wait()

        # Graceful shutdown — cancel any tracked fire-and-forget tasks
        # (e.g. _preload_models) and await their completion to avoid
        # "Task was destroyed but it is pending!" warnings.
        if background_tasks:
            for t in list(background_tasks):
                if not t.done():
                    t.cancel()
            await asyncio.gather(*background_tasks, return_exceptions=True)
            background_tasks.clear()

        if work_loop:
            await work_loop.stop()
            if work_loop_task:
                work_loop_task.cancel()

        if cron:
            await cron.stop()
            if cron_task:
                cron_task.cancel()

        if terminal_repl:
            await terminal_repl.stop()
            if terminal_task:
                terminal_task.cancel()

        if agent_api:
            await agent_api.stop()
            if agent_api_task:
                agent_api_task.cancel()

        if telegram_task:
            await bot.stop()
            telegram_task.cancel()
            try:
                await telegram_task
            except asyncio.CancelledError:
                pass

        await agent.stop()
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass

        _remove_pidfile()

    except Exception as e:
        logger.exception("agent_fatal_error", error=str(e))
        # Store crash info for post-mortem
        try:
            from pathlib import Path
            crash_log = Path(data_dir) / "logs" / "last_crash.txt"
            crash_log.parent.mkdir(parents=True, exist_ok=True)
            import traceback
            crash_log.write_text(
                f"Time: {datetime.now(UTC).isoformat()}\n"
                f"Error: {e}\n\n"
                f"{traceback.format_exc()}"
            )
            logger.info("crash_log_saved", path=str(crash_log))
        except Exception:
            pass
        await agent.stop()
        _remove_pidfile()
        sys.exit(1)


async def show_status(data_dir: str = "agent") -> None:
    """Show agent status without starting the full agent."""
    import orjson

    _apply_runtime_env_defaults(data_dir)
    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    status = agent.get_status()
    print(orjson.dumps(status, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def show_health(data_dir: str = "agent") -> None:
    """Show system health."""
    import orjson

    _apply_runtime_env_defaults(data_dir)
    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    health = agent.watchdog.get_system_health()
    result = {
        "cpu_percent": health.cpu_percent,
        "memory_percent": health.memory_percent,
        "memory_used_mb": health.memory_used_mb,
        "memory_available_mb": health.memory_available_mb,
        "disk_percent": health.disk_percent,
        "modules": health.modules,
        "alerts": health.alerts,
    }
    print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def show_operator_report(data_dir: str = "agent") -> None:
    """Show a compact operator-facing report/inbox snapshot."""
    import orjson

    _apply_runtime_env_defaults(data_dir)
    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    report = agent.get_operator_report()
    print(orjson.dumps(report, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def show_runtime_model(data_dir: str = "agent") -> None:
    """Show explicit runtime coexistence rules."""
    import orjson

    _apply_runtime_env_defaults(data_dir)
    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    print(orjson.dumps(agent.get_runtime_model(), option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def show_llm_runtime_command(data_dir: str = "agent") -> None:
    """Show persistent runtime LLM controls and effective selection."""
    import orjson

    _apply_runtime_env_defaults(data_dir)
    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    print(orjson.dumps(agent.get_llm_runtime_state(), option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def update_llm_runtime_command(
    *,
    data_dir: str = "agent",
    enabled: bool | None = None,
    backend: str | None = None,
    provider: str | None = None,
    follow_env: bool = False,
    note: str = "",
    updated_by: str = "cli",
) -> None:
    """Persist runtime LLM controls and print the updated state."""
    import orjson

    _apply_runtime_env_defaults(data_dir)
    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    state = agent.update_llm_runtime_state(
        enabled=enabled,
        backend=backend,
        provider=provider,
        follow_env=follow_env,
        note=note,
        updated_by=updated_by,
    )
    print(orjson.dumps(state, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def show_gateway_catalog(
    *,
    data_dir: str = "agent",
    provider_id: str = "",
    capability_id: str = "",
    kind: str = "",
    export_mode: str = "",
) -> None:
    """Show configured external gateway providers, routes, and readiness."""
    import orjson

    _apply_runtime_env_defaults(data_dir)
    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    print(
        orjson.dumps(
            agent.get_gateway_catalog(
                provider_id=provider_id,
                capability_id=capability_id,
                kind=kind or None,
                export_mode=export_mode,
            ),
            option=orjson.OPT_INDENT_2,
        ).decode()
    )
    await agent.stop()


def _parse_key_value_pairs(items: list[str]) -> dict[str, str]:
    """Parse repeated key=value CLI items into a dict."""
    parsed: dict[str, str] = {}
    for item in items:
        key, separator, value = str(item).partition("=")
        if not separator or not key.strip():
            raise ValueError(f"Expected key=value item, got: {item}")
        parsed[key.strip()] = value
    return parsed


async def call_provider_api_command(
    *,
    data_dir: str = "agent",
    provider_id: str,
    capability_id: str,
    resource: str = "",
    method: str = "",
    query_items: list[str] | None = None,
    json_payload: dict[str, object] | None = None,
    route_id: str = "",
    auth_token: str = "",
    gateway_policy_id: str = "",
    requester: str = "cli",
    job_id: str = "",
    title: str = "",
) -> None:
    """Call one external provider API capability through the gateway."""
    import orjson

    _apply_runtime_env_defaults(data_dir)
    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    result = await agent.call_external_api(
        provider_id=provider_id,
        capability_id=capability_id,
        resource=resource,
        method=method,
        query_params=_parse_key_value_pairs(list(query_items or [])),
        json_payload=dict(json_payload or {}),
        route_id=route_id,
        auth_token=auth_token,
        gateway_policy_id=gateway_policy_id,
        job_id=job_id,
        requester=requester,
        title=title,
    )
    print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def list_artifacts_command(
    *,
    data_dir: str = "agent",
    kind: str = "",
    job_id: str = "",
    artifact_kind: str = "",
    limit: int = 20,
) -> None:
    """List shared build/review artifacts."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    artifacts = agent.list_product_artifacts(
        kind=kind or None,
        job_id=job_id,
        artifact_kind=artifact_kind,
        limit=limit,
    )
    print(orjson.dumps(artifacts, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def list_plans_command(
    *,
    data_dir: str = "agent",
    status: str = "",
    limit: int = 20,
) -> None:
    """List persisted operator plan records."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    plans = agent.list_operator_plans(status=status, limit=limit)
    print(orjson.dumps(plans, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def show_plan_command(*, data_dir: str = "agent", plan_id: str) -> None:
    """Show one persisted operator plan record."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    plan = agent.get_operator_plan(plan_id)
    result = plan or {"error": f"Plan not found: {plan_id}"}
    print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def list_traces_command(
    *,
    data_dir: str = "agent",
    trace_kind: str = "",
    plan_id: str = "",
    job_id: str = "",
    workspace_id: str = "",
    bundle_id: str = "",
    limit: int = 50,
) -> None:
    """List persisted control-plane trace records."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    traces = agent.list_execution_traces(
        trace_kind=trace_kind,
        plan_id=plan_id,
        job_id=job_id,
        workspace_id=workspace_id,
        bundle_id=bundle_id,
        limit=limit,
    )
    print(orjson.dumps(traces, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def list_workspaces_command(
    *,
    data_dir: str = "agent",
    status: str = "",
    limit: int = 20,
) -> None:
    """List workspace records through the control-plane query surface."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    records = agent.list_workspace_records(status=status, limit=limit)
    print(orjson.dumps(records, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def show_workspace_command(
    *,
    data_dir: str = "agent",
    workspace_id: str,
) -> None:
    """Show one workspace record with linked jobs, artifacts, approvals, and bundles."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    record = agent.get_workspace_record(workspace_id)
    result = record or {"error": f"Workspace not found: {workspace_id}"}
    print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def list_deliveries_command(
    *,
    data_dir: str = "agent",
    status: str = "",
    job_id: str = "",
    workspace_id: str = "",
    limit: int = 20,
) -> None:
    """List persisted delivery lifecycle records."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    deliveries = agent.list_delivery_records(
        status=status,
        job_id=job_id,
        workspace_id=workspace_id,
        limit=limit,
    )
    print(orjson.dumps(deliveries, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def list_persisted_jobs_command(
    *,
    data_dir: str = "agent",
    job_kind: str = "",
    status: str = "",
    limit: int = 20,
) -> None:
    """List durable build/review job records."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    records = agent.list_persisted_product_jobs(
        job_kind=job_kind,
        status=status,
        limit=limit,
    )
    print(orjson.dumps(records, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def show_persisted_job_command(
    *,
    data_dir: str = "agent",
    job_id: str,
) -> None:
    """Show one durable build/review job record."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    record = agent.get_persisted_product_job(job_id)
    result = record or {"error": f"Persisted job not found: {job_id}"}
    print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def list_retained_artifacts_command(
    *,
    data_dir: str = "agent",
    status: str = "",
    job_id: str = "",
    artifact_kind: str = "",
    retention_policy_id: str = "",
    limit: int = 50,
) -> None:
    """List retained artifacts and delivery outputs."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    records = agent.list_retained_artifacts(
        status=status,
        job_id=job_id,
        artifact_kind=artifact_kind,
        retention_policy_id=retention_policy_id,
        limit=limit,
    )
    print(orjson.dumps(records, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def show_retained_artifact_command(
    *,
    data_dir: str = "agent",
    record_id: str,
) -> None:
    """Show one retained artifact or delivery-output record."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    record = agent.get_retained_artifact(record_id)
    result = record or {"error": f"Retained artifact not found: {record_id}"}
    print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def prune_retained_artifacts_command(
    *,
    data_dir: str = "agent",
    job_id: str = "",
    artifact_kind: str = "",
    retention_policy_id: str = "",
    limit: int = 50,
) -> None:
    """Prune expired retained artifacts and clear their stored snapshots."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    records = agent.prune_retained_artifacts(
        job_id=job_id,
        artifact_kind=artifact_kind,
        retention_policy_id=retention_policy_id,
        limit=limit,
    )
    print(orjson.dumps(records, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def list_cost_ledger_command(
    *,
    data_dir: str = "agent",
    job_id: str = "",
    job_kind: str = "",
    limit: int = 50,
) -> None:
    """List durable per-job cost and token records."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    records = agent.list_cost_ledger(
        job_id=job_id,
        job_kind=job_kind,
        limit=limit,
    )
    print(orjson.dumps(records, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def export_evidence_command(
    *,
    data_dir: str = "agent",
    job_id: str,
    kind: str = "",
    export_format: str = "json",
    export_mode: str = "internal",
) -> None:
    """Export a compliance-friendly evidence package for one job."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    result = agent.export_job_evidence(
        job_id,
        kind=kind or None,
        export_format=export_format,
        export_mode=export_mode,
    )
    if export_format == "markdown":
        print(result)
    else:
        print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def handoff_build_delivery_command(
    *,
    data_dir: str = "agent",
    job_id: str,
    note: str = "",
) -> None:
    """Mark a build delivery package as handed off."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    result = agent.mark_build_delivery_handed_off(job_id, note=note)
    print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def send_build_delivery_command(
    *,
    data_dir: str = "agent",
    job_id: str,
    target_url: str = "",
    auth_token: str = "",
    gateway_policy_id: str = "approval_before_gateway",
    provider_id: str = "",
    capability_id: str = "",
    route_id: str = "",
) -> None:
    """Send an approved build delivery package through the external gateway."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    result = await agent.send_build_delivery_via_gateway(
        job_id,
        target_url=target_url,
        auth_token=auth_token,
        gateway_policy_id=gateway_policy_id,
        provider_id=provider_id,
        capability_id=capability_id,
        route_id=route_id,
    )
    print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def send_review_delivery_command(
    *,
    data_dir: str = "agent",
    job_id: str,
    target_url: str = "",
    auth_token: str = "",
    gateway_policy_id: str = "approval_before_gateway",
    provider_id: str = "",
    capability_id: str = "",
    route_id: str = "",
) -> None:
    """Send an approved client-safe review package through the external gateway."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    result = await agent.send_review_delivery_via_gateway(
        job_id,
        target_url=target_url,
        auth_token=auth_token,
        gateway_policy_id=gateway_policy_id,
        provider_id=provider_id,
        capability_id=capability_id,
        route_id=route_id,
    )
    print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def evaluate_review_quality_command(
    *,
    data_dir: str = "agent",
    release_label: str = "",
) -> None:
    """Run deterministic golden review cases and print quality telemetry."""
    import orjson

    _apply_runtime_env_defaults(data_dir)
    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    result = await agent.evaluate_review_quality(release_label=release_label)
    print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def evaluate_release_readiness_command(
    *,
    data_dir: str = "agent",
    release_label: str = "",
    policy_id: str = "phase4_closure",
) -> None:
    """Run release-readiness checks and fail closed when the gate is not ready."""
    import orjson

    _apply_runtime_env_defaults(data_dir)
    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    try:
        result = await agent.evaluate_release_readiness(
            release_label=release_label,
            policy_id=policy_id,
        )
        print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    finally:
        await agent.stop()
    if not result.get("ready", False):
        raise SystemExit(1)


async def show_setup_doctor_command(
    *,
    data_dir: str = "agent",
    probe_llm: bool = True,
) -> None:
    """Show a self-host focused setup/configuration report."""
    import orjson

    _apply_runtime_env_defaults(data_dir)
    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    try:
        report = await agent.evaluate_setup_doctor(probe_llm=probe_llm)
        print(orjson.dumps(report, option=orjson.OPT_INDENT_2).decode())
    finally:
        await agent.stop()


async def show_artifact_command(
    *,
    data_dir: str = "agent",
    artifact_id: str,
    kind: str = "",
) -> None:
    """Show one shared build/review artifact."""
    import orjson

    _apply_runtime_env_defaults(data_dir)
    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    artifact = agent.get_product_artifact(artifact_id, kind=kind or None)
    result = artifact or {"error": f"Artifact not found: {artifact_id}"}
    print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


def _load_build_operation_plan(plan_file: str) -> list[object]:
    """Load a structured build implementation plan from JSON."""
    from agent.build.models import BuildOperation

    try:
        with open(plan_file, encoding="utf-8") as f:
            payload = json.load(f)
    except OSError as e:
        raise ValueError(f"Could not read implementation plan file: {e}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"Implementation plan file is not valid JSON: {e}") from e

    if isinstance(payload, dict):
        payload = (
            payload.get("implementation_plan")
            or payload.get("operations")
            or payload.get("plan")
            or []
        )
    if not isinstance(payload, list):
        raise ValueError("Implementation plan file must contain a JSON list of operations")
    if not all(isinstance(item, dict) for item in payload):
        raise ValueError("Implementation plan operations must be JSON objects")
    return [BuildOperation.from_dict(item) for item in payload]


def _load_acceptance_criteria(criteria_file: str) -> list[object]:
    """Load structured acceptance criteria from JSON."""
    from agent.build.models import AcceptanceCriterion

    try:
        with open(criteria_file, encoding="utf-8") as f:
            payload = json.load(f)
    except OSError as e:
        raise ValueError(f"Could not read acceptance criteria file: {e}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"Acceptance criteria file is not valid JSON: {e}") from e

    if isinstance(payload, dict):
        payload = (
            payload.get("acceptance_criteria")
            or payload.get("criteria")
            or payload.get("acceptance")
            or payload.get("requirements")
            or []
        )
    if not isinstance(payload, list):
        raise ValueError("Acceptance criteria file must contain a JSON list")
    return [AcceptanceCriterion.from_input(item) for item in payload]


async def run_build_command(
    *,
    data_dir: str = "agent",
    repo_path: str,
    description: str,
    target_files: list[str] | None = None,
    implementation_plan: list[Any] | None = None,
    acceptance_criteria: list[Any] | None = None,
    requester: str = "cli",
    context: str = "",
    skip_review: bool = False,
) -> None:
    """Run one build job through the shared orchestrator runtime."""
    import orjson

    from agent.build.models import AcceptanceCriterion, BuildIntake

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()

    intake = BuildIntake(
        repo_path=repo_path,
        description=description,
        target_files=target_files or [],
        implementation_plan=implementation_plan or [],
        acceptance_criteria=[
            AcceptanceCriterion.from_input(item)
            for item in (acceptance_criteria or [])
        ],
        run_post_build_review=not skip_review,
        requester=requester,
        context=context,
    )
    job = await agent.run_build_job(intake)
    result = agent.get_product_job(job.id, kind="build") or {"job_id": job.id}
    print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def resume_build_command(*, data_dir: str = "agent", job_id: str) -> None:
    """Resume a previously interrupted build job and print normalized output."""
    import orjson

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    job = await agent.resume_build_job(job_id)
    if job is None:
        result = {"error": f"Build job not found: {job_id}", "job_id": job_id}
    else:
        result = agent.get_product_job(job.id, kind="build") or {"job_id": job.id}
    print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def run_intake_command(
    *,
    data_dir: str = "agent",
    repo_path: str = "",
    git_url: str = "",
    diff_spec: str = "",
    work_type: str = "auto",
    build_type: str = "implementation",
    description: str = "",
    requester: str = "cli",
    context: str = "",
    focus_areas: list[str] | None = None,
    target_files: list[str] | None = None,
    implementation_plan: list[Any] | None = None,
    acceptance_criteria: list[Any] | None = None,
    preview_only: bool = False,
) -> None:
    """Qualify and optionally execute unified operator intake."""
    import orjson

    from agent.build.models import BuildJobType
    from agent.control.intake import OperatorIntake, OperatorWorkType

    intake = OperatorIntake(
        repo_path=repo_path,
        git_url=git_url,
        diff_spec=diff_spec,
        work_type=OperatorWorkType(work_type),
        build_type=BuildJobType(build_type),
        description=description,
        requester=requester,
        context=context,
        focus_areas=focus_areas or [],
        target_files=target_files or [],
        implementation_plan=implementation_plan or [],
        acceptance_criteria=acceptance_criteria or [],
    )

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    if preview_only:
        result = agent.preview_operator_intake(intake)
    else:
        result = await agent.submit_operator_intake(intake)
    print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agent Life Space — Self-hosted autonomous agent"
    )
    parser.add_argument(
        "--data-dir",
        default=DEFAULT_DATA_DIR,
        help="Data directory for agent storage (default: AGENT_DATA_DIR, legacy agent/, or .agent_runtime)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show agent status and exit",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Show system health and exit",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Show operator report/inbox snapshot and exit",
    )
    parser.add_argument(
        "--list-plans",
        action="store_true",
        help="List persisted operator plan records and exit",
    )
    parser.add_argument(
        "--plan-id",
        default="",
        help="Show one persisted operator plan record by id and exit",
    )
    parser.add_argument(
        "--plan-status",
        default="",
        choices=[
            "",
            "preview",
            "submitted",
            "awaiting_approval",
            "blocked",
            "executing",
            "completed",
        ],
        help="Optional status filter for --list-plans",
    )
    parser.add_argument(
        "--plan-limit",
        default=20,
        type=int,
        help="Limit for --list-plans (default: 20)",
    )
    parser.add_argument(
        "--list-traces",
        action="store_true",
        help="List persisted control-plane traces and exit",
    )
    parser.add_argument(
        "--trace-kind",
        default="",
        choices=[
            "",
            "qualification",
            "budget",
            "capability",
            "delivery",
            "review_policy",
            "verification_discovery",
            "execution",
            "gateway",
            "quality",
        ],
        help="Optional kind filter for --list-traces",
    )
    parser.add_argument(
        "--trace-plan-id",
        default="",
        help="Optional plan id filter for --list-traces",
    )
    parser.add_argument(
        "--trace-job-id",
        default="",
        help="Optional job id filter for --list-traces",
    )
    parser.add_argument(
        "--trace-workspace-id",
        default="",
        help="Optional workspace id filter for --list-traces",
    )
    parser.add_argument(
        "--trace-bundle-id",
        default="",
        help="Optional bundle id filter for --list-traces",
    )
    parser.add_argument(
        "--trace-limit",
        default=50,
        type=int,
        help="Limit for --list-traces (default: 50)",
    )
    parser.add_argument(
        "--list-workspaces",
        action="store_true",
        help="List workspace records through the control-plane query surface and exit",
    )
    parser.add_argument(
        "--workspace-id",
        default="",
        help="Show one workspace record by id and exit",
    )
    parser.add_argument(
        "--workspace-status",
        default="",
        choices=["", "created", "active", "completed", "failed", "cleaned"],
        help="Optional status filter for --list-workspaces",
    )
    parser.add_argument(
        "--workspace-limit",
        default=20,
        type=int,
        help="Limit for --list-workspaces (default: 20)",
    )
    parser.add_argument(
        "--list-deliveries",
        action="store_true",
        help="List persisted delivery lifecycle records and exit",
    )
    parser.add_argument(
        "--delivery-status",
        default="",
        choices=["", "prepared", "awaiting_approval", "approved", "rejected", "handed_off"],
        help="Optional status filter for --list-deliveries",
    )
    parser.add_argument(
        "--delivery-job-id",
        default="",
        help="Optional job id filter for --list-deliveries",
    )
    parser.add_argument(
        "--delivery-workspace-id",
        default="",
        help="Optional workspace id filter for --list-deliveries",
    )
    parser.add_argument(
        "--delivery-limit",
        default=20,
        type=int,
        help="Limit for --list-deliveries (default: 20)",
    )
    parser.add_argument(
        "--list-persisted-jobs",
        action="store_true",
        help="List durable build/review job records and exit",
    )
    parser.add_argument(
        "--persisted-job-id",
        default="",
        help="Show one durable build/review job record by id and exit",
    )
    parser.add_argument(
        "--persisted-job-kind",
        default="",
        choices=["", "build", "review"],
        help="Optional job kind filter for --list-persisted-jobs",
    )
    parser.add_argument(
        "--persisted-job-status",
        default="",
        help="Optional status filter for --list-persisted-jobs",
    )
    parser.add_argument(
        "--persisted-job-limit",
        default=20,
        type=int,
        help="Limit for --list-persisted-jobs (default: 20)",
    )
    parser.add_argument(
        "--list-retained-artifacts",
        action="store_true",
        help="List retained artifacts and delivery outputs and exit",
    )
    parser.add_argument(
        "--prune-expired-retained-artifacts",
        action="store_true",
        help="Prune expired retained artifacts and delivery outputs and exit",
    )
    parser.add_argument(
        "--retained-artifact-id",
        default="",
        help="Show one retained artifact or delivery-output record by id and exit",
    )
    parser.add_argument(
        "--retained-job-id",
        default="",
        help="Optional job id filter for --list-retained-artifacts",
    )
    parser.add_argument(
        "--retained-artifact-kind",
        default="",
        help="Optional artifact kind filter for --list-retained-artifacts",
    )
    parser.add_argument(
        "--retention-status",
        default="",
        choices=["", "active", "expired", "pruned"],
        help="Optional retention status filter for --list-retained-artifacts",
    )
    parser.add_argument(
        "--retention-policy-id",
        default="",
        help="Optional retention policy filter for --list-retained-artifacts",
    )
    parser.add_argument(
        "--retained-limit",
        default=50,
        type=int,
        help="Limit for --list-retained-artifacts (default: 50)",
    )
    parser.add_argument(
        "--list-cost-ledger",
        action="store_true",
        help="List durable per-job cost and token records and exit",
    )
    parser.add_argument(
        "--cost-job-id",
        default="",
        help="Optional job id filter for --list-cost-ledger",
    )
    parser.add_argument(
        "--cost-job-kind",
        default="",
        choices=["", "build", "review"],
        help="Optional job kind filter for --list-cost-ledger",
    )
    parser.add_argument(
        "--cost-limit",
        default=50,
        type=int,
        help="Limit for --list-cost-ledger (default: 50)",
    )
    parser.add_argument(
        "--export-evidence-job",
        default="",
        help="Export a compliance-friendly evidence package for this job id and exit",
    )
    parser.add_argument(
        "--export-evidence-kind",
        default="",
        choices=["", "build", "review"],
        help="Optional job kind hint for --export-evidence-job",
    )
    parser.add_argument(
        "--export-evidence-format",
        default="json",
        choices=["json", "markdown"],
        help="Output format for --export-evidence-job",
    )
    parser.add_argument(
        "--export-evidence-mode",
        default="internal",
        choices=["internal", "client_safe"],
        help="Safety/export mode for --export-evidence-job",
    )
    parser.add_argument(
        "--handoff-build-delivery",
        default="",
        help="Mark the build delivery for this job id as handed off and exit",
    )
    parser.add_argument(
        "--send-build-delivery",
        default="",
        help="Send the approved build delivery bundle for this job id through the external gateway and exit",
    )
    parser.add_argument(
        "--send-review-delivery",
        default="",
        help="Send the approved client-safe review delivery bundle for this job id through the external gateway and exit",
    )
    parser.add_argument(
        "--handoff-note",
        default="",
        help="Optional note for --handoff-build-delivery",
    )
    parser.add_argument(
        "--gateway-target",
        default="",
        help="Absolute target URL for gateway delivery commands",
    )
    parser.add_argument(
        "--gateway-auth-token",
        default="",
        help="Auth token for gateway delivery commands",
    )
    parser.add_argument(
        "--gateway-policy-id",
        default="",
        help="Optional gateway policy override for gateway delivery and provider API commands",
    )
    parser.add_argument(
        "--gateway-provider",
        default="",
        help="Provider id for gateway delivery commands, e.g. obolos.tech",
    )
    parser.add_argument(
        "--gateway-capability",
        default="",
        help="Provider capability id for gateway delivery commands",
    )
    parser.add_argument(
        "--gateway-route-id",
        default="",
        help="Optional explicit provider route id for gateway delivery commands",
    )
    parser.add_argument(
        "--gateway-export-mode",
        default="internal",
        choices=["internal", "client_safe"],
        help="Export mode when filtering --gateway-catalog",
    )
    parser.add_argument(
        "--gateway-catalog",
        action="store_true",
        help="Show configured external gateway providers/routes and exit",
    )
    parser.add_argument(
        "--call-provider-api",
        action="store_true",
        help="Call one configured external provider API capability and exit",
    )
    parser.add_argument(
        "--provider-api-provider",
        default="",
        help="Provider id for --call-provider-api, e.g. obolos.tech",
    )
    parser.add_argument(
        "--provider-api-capability",
        default="",
        help="Capability id for --call-provider-api, e.g. marketplace_catalog_v1",
    )
    parser.add_argument(
        "--provider-api-resource",
        default="",
        help="Optional resource or slug for --call-provider-api",
    )
    parser.add_argument(
        "--provider-api-method",
        default="",
        help="Optional HTTP method override for --call-provider-api",
    )
    parser.add_argument(
        "--provider-api-query",
        action="append",
        default=[],
        help="Repeatable key=value query parameter for --call-provider-api",
    )
    parser.add_argument(
        "--provider-api-json",
        default="",
        help="Inline JSON object payload for --call-provider-api",
    )
    parser.add_argument(
        "--provider-api-json-file",
        default="",
        help="Path to a JSON object payload file for --call-provider-api",
    )
    parser.add_argument(
        "--provider-api-requester",
        default="cli",
        help="Requester label for --call-provider-api",
    )
    parser.add_argument(
        "--provider-api-job-id",
        default="",
        help="Optional fixed job id for --call-provider-api",
    )
    parser.add_argument(
        "--provider-api-title",
        default="",
        help="Optional title override for --call-provider-api",
    )
    parser.add_argument(
        "--review-quality-eval",
        action="store_true",
        help="Run deterministic golden review cases and exit",
    )
    parser.add_argument(
        "--review-quality-release-label",
        default="",
        help="Optional release label to attach to --review-quality-eval",
    )
    parser.add_argument(
        "--release-readiness",
        action="store_true",
        help="Run release-readiness checks and exit non-zero when the gate is not ready",
    )
    parser.add_argument(
        "--release-readiness-release-label",
        default="",
        help="Optional release label to attach to --release-readiness",
    )
    parser.add_argument(
        "--release-readiness-policy-id",
        default="phase4_closure",
        help="Policy id for --release-readiness",
    )
    parser.add_argument(
        "--setup-doctor",
        action="store_true",
        help="Show self-host setup/configuration posture and exit",
    )
    parser.add_argument(
        "--setup-doctor-skip-live-llm",
        action="store_true",
        help="Skip the live LLM probe during --setup-doctor (useful for offline diagnostics)",
    )
    parser.add_argument(
        "--runtime-model",
        action="store_true",
        help="Show explicit runtime coexistence rules and exit",
    )
    parser.add_argument(
        "--llm-runtime-status",
        action="store_true",
        help="Show persistent runtime LLM controls and exit",
    )
    parser.add_argument(
        "--llm-runtime-enable",
        action="store_true",
        help="Attach/enable LLM-backed work at runtime and exit",
    )
    parser.add_argument(
        "--llm-runtime-disable",
        action="store_true",
        help="Detach/disable LLM-backed work at runtime and exit",
    )
    parser.add_argument(
        "--llm-runtime-backend",
        default="",
        choices=["", "cli", "api"],
        help="Optional runtime backend override for LLM controls",
    )
    parser.add_argument(
        "--llm-runtime-provider",
        default="",
        choices=["", "anthropic", "openai", "local"],
        help="Optional runtime provider override for LLM controls",
    )
    parser.add_argument(
        "--llm-runtime-follow-env",
        action="store_true",
        help="Clear runtime backend/provider overrides and follow .env again",
    )
    parser.add_argument(
        "--llm-runtime-note",
        default="",
        help="Optional audit note for runtime LLM control changes",
    )
    parser.add_argument(
        "--llm-runtime-updated-by",
        default="cli",
        help="Operator label stored in runtime LLM control audit trail",
    )
    parser.add_argument(
        "--build-repo",
        default="",
        help="Run a builder job against this repository path and exit",
    )
    parser.add_argument(
        "--build-resume",
        default="",
        help="Resume a previously interrupted build job by id and exit",
    )
    parser.add_argument(
        "--build-description",
        default="",
        help="Required description for --build-repo execution",
    )
    parser.add_argument(
        "--build-target-file",
        action="append",
        default=[],
        help="Optional target file/glob for the builder review scope; repeatable",
    )
    parser.add_argument(
        "--build-acceptance",
        action="append",
        default=[],
        help="Acceptance criterion for the build job; repeatable",
    )
    parser.add_argument(
        "--build-acceptance-file",
        default="",
        help="JSON file with structured acceptance criteria for --build-repo",
    )
    parser.add_argument(
        "--build-plan-file",
        default="",
        help="JSON file with a structured implementation plan for --build-repo",
    )
    parser.add_argument(
        "--build-requester",
        default="cli",
        help="Requester label for --build-repo execution",
    )
    parser.add_argument(
        "--build-context",
        default="",
        help="Optional free-text context for --build-repo execution",
    )
    parser.add_argument(
        "--build-skip-review",
        action="store_true",
        help="Disable the post-build reviewer pass for --build-repo execution",
    )
    parser.add_argument(
        "--artifact-id",
        default="",
        help="Show one shared build/review artifact by id and exit",
    )
    parser.add_argument(
        "--artifact-kind",
        default="",
        help="Filter shared artifact listing by artifact kind",
    )
    parser.add_argument(
        "--artifact-job-id",
        default="",
        help="Filter shared artifact listing by job id",
    )
    parser.add_argument(
        "--artifact-job-kind",
        default="",
        choices=["", "build", "review"],
        help="Filter shared artifact listing by job kind",
    )
    parser.add_argument(
        "--list-artifacts",
        action="store_true",
        help="List shared build/review artifacts and exit",
    )
    parser.add_argument(
        "--artifact-limit",
        default=20,
        type=int,
        help="Limit for --list-artifacts (default: 20)",
    )
    parser.add_argument(
        "--intake-repo",
        default="",
        help="Unified operator intake: local repository path",
    )
    parser.add_argument(
        "--intake-git-url",
        default="",
        help="Unified operator intake: supported git source for managed acquisition/import",
    )
    parser.add_argument(
        "--intake-diff",
        default="",
        help="Unified operator intake: optional git diff/range for review routing",
    )
    parser.add_argument(
        "--intake-work-type",
        default="auto",
        choices=["auto", "review", "build"],
        help="Unified operator intake: route selection",
    )
    parser.add_argument(
        "--intake-build-type",
        default="implementation",
        choices=["implementation", "integration", "devops", "testing"],
        help="Unified operator intake: build subtype when build routing is chosen",
    )
    parser.add_argument(
        "--intake-description",
        default="",
        help="Unified operator intake: description/context for the requested work",
    )
    parser.add_argument(
        "--intake-requester",
        default="cli",
        help="Unified operator intake: requester label",
    )
    parser.add_argument(
        "--intake-context",
        default="",
        help="Unified operator intake: optional free-text context",
    )
    parser.add_argument(
        "--intake-focus-area",
        action="append",
        default=[],
        help="Unified operator intake: review focus area; repeatable",
    )
    parser.add_argument(
        "--intake-target-file",
        action="append",
        default=[],
        help="Unified operator intake: target file/glob; repeatable",
    )
    parser.add_argument(
        "--intake-acceptance",
        action="append",
        default=[],
        help="Unified operator intake: acceptance criterion; repeatable",
    )
    parser.add_argument(
        "--intake-acceptance-file",
        default="",
        help="Unified operator intake: JSON file with structured acceptance criteria",
    )
    parser.add_argument(
        "--intake-plan-file",
        default="",
        help="Unified operator intake: JSON file with a structured implementation plan",
    )
    parser.add_argument(
        "--intake-preview",
        action="store_true",
        help="Unified operator intake: only show qualification, do not execute",
    )
    args = parser.parse_args()
    _apply_runtime_env_defaults(args.data_dir)

    if args.status:
        asyncio.run(show_status(args.data_dir))
    elif args.health:
        asyncio.run(show_health(args.data_dir))
    elif args.report:
        asyncio.run(show_operator_report(args.data_dir))
    elif args.list_plans:
        asyncio.run(
            list_plans_command(
                data_dir=args.data_dir,
                status=args.plan_status,
                limit=args.plan_limit,
            )
        )
    elif args.plan_id:
        asyncio.run(show_plan_command(data_dir=args.data_dir, plan_id=args.plan_id))
    elif args.list_traces:
        asyncio.run(
            list_traces_command(
                data_dir=args.data_dir,
                trace_kind=args.trace_kind,
                plan_id=args.trace_plan_id,
                job_id=args.trace_job_id,
                workspace_id=args.trace_workspace_id,
                bundle_id=args.trace_bundle_id,
                limit=args.trace_limit,
            )
        )
    elif args.list_workspaces:
        asyncio.run(
            list_workspaces_command(
                data_dir=args.data_dir,
                status=args.workspace_status,
                limit=args.workspace_limit,
            )
        )
    elif args.workspace_id:
        asyncio.run(
            show_workspace_command(
                data_dir=args.data_dir,
                workspace_id=args.workspace_id,
            )
        )
    elif args.list_deliveries:
        asyncio.run(
            list_deliveries_command(
                data_dir=args.data_dir,
                status=args.delivery_status,
                job_id=args.delivery_job_id,
                workspace_id=args.delivery_workspace_id,
                limit=args.delivery_limit,
            )
        )
    elif args.list_persisted_jobs:
        asyncio.run(
            list_persisted_jobs_command(
                data_dir=args.data_dir,
                job_kind=args.persisted_job_kind,
                status=args.persisted_job_status,
                limit=args.persisted_job_limit,
            )
        )
    elif args.persisted_job_id:
        asyncio.run(
            show_persisted_job_command(
                data_dir=args.data_dir,
                job_id=args.persisted_job_id,
            )
        )
    elif args.list_retained_artifacts:
        asyncio.run(
            list_retained_artifacts_command(
                data_dir=args.data_dir,
                status=args.retention_status,
                job_id=args.retained_job_id,
                artifact_kind=args.retained_artifact_kind,
                retention_policy_id=args.retention_policy_id,
                limit=args.retained_limit,
            )
        )
    elif args.prune_expired_retained_artifacts:
        asyncio.run(
            prune_retained_artifacts_command(
                data_dir=args.data_dir,
                job_id=args.retained_job_id,
                artifact_kind=args.retained_artifact_kind,
                retention_policy_id=args.retention_policy_id,
                limit=args.retained_limit,
            )
        )
    elif args.retained_artifact_id:
        asyncio.run(
            show_retained_artifact_command(
                data_dir=args.data_dir,
                record_id=args.retained_artifact_id,
            )
        )
    elif args.list_cost_ledger:
        asyncio.run(
            list_cost_ledger_command(
                data_dir=args.data_dir,
                job_id=args.cost_job_id,
                job_kind=args.cost_job_kind,
                limit=args.cost_limit,
            )
        )
    elif args.export_evidence_job:
        asyncio.run(
            export_evidence_command(
                data_dir=args.data_dir,
                job_id=args.export_evidence_job,
                kind=args.export_evidence_kind,
                export_format=args.export_evidence_format,
                export_mode=args.export_evidence_mode,
            )
        )
    elif args.handoff_build_delivery:
        asyncio.run(
            handoff_build_delivery_command(
                data_dir=args.data_dir,
                job_id=args.handoff_build_delivery,
                note=args.handoff_note,
            )
        )
    elif args.send_build_delivery:
        asyncio.run(
            send_build_delivery_command(
                data_dir=args.data_dir,
                job_id=args.send_build_delivery,
                target_url=args.gateway_target,
                auth_token=args.gateway_auth_token,
                gateway_policy_id=args.gateway_policy_id,
                provider_id=args.gateway_provider,
                capability_id=args.gateway_capability,
                route_id=args.gateway_route_id,
            )
        )
    elif args.send_review_delivery:
        asyncio.run(
            send_review_delivery_command(
                data_dir=args.data_dir,
                job_id=args.send_review_delivery,
                target_url=args.gateway_target,
                auth_token=args.gateway_auth_token,
                gateway_policy_id=args.gateway_policy_id,
                provider_id=args.gateway_provider,
                capability_id=args.gateway_capability,
                route_id=args.gateway_route_id,
            )
        )
    elif args.review_quality_eval:
        asyncio.run(
            evaluate_review_quality_command(
                data_dir=args.data_dir,
                release_label=args.review_quality_release_label,
            )
        )
    elif args.release_readiness:
        asyncio.run(
            evaluate_release_readiness_command(
                data_dir=args.data_dir,
                release_label=args.release_readiness_release_label,
                policy_id=args.release_readiness_policy_id,
            )
        )
    elif args.setup_doctor:
        asyncio.run(
            show_setup_doctor_command(
                data_dir=args.data_dir,
                probe_llm=not args.setup_doctor_skip_live_llm,
            )
        )
    elif args.gateway_catalog:
        asyncio.run(
            show_gateway_catalog(
                data_dir=args.data_dir,
                provider_id=args.gateway_provider,
                capability_id=args.gateway_capability,
                kind="",
                export_mode=args.gateway_export_mode,
            )
        )
    elif args.call_provider_api:
        if not args.provider_api_provider:
            parser.error("--provider-api-provider is required with --call-provider-api")
        if not args.provider_api_capability:
            parser.error("--provider-api-capability is required with --call-provider-api")
        provider_api_json: dict[str, object] = {}
        if args.provider_api_json and args.provider_api_json_file:
            parser.error("Use only one of --provider-api-json or --provider-api-json-file")
        if args.provider_api_json:
            try:
                provider_api_json = json.loads(args.provider_api_json)
            except json.JSONDecodeError as e:
                parser.error(f"--provider-api-json is not valid JSON: {e}")
            if not isinstance(provider_api_json, dict):
                parser.error("--provider-api-json must decode to a JSON object")
        elif args.provider_api_json_file:
            try:
                with open(args.provider_api_json_file, encoding="utf-8") as f:
                    provider_api_json = json.load(f)
            except OSError as e:
                parser.error(f"Could not read --provider-api-json-file: {e}")
            except json.JSONDecodeError as e:
                parser.error(f"--provider-api-json-file is not valid JSON: {e}")
            if not isinstance(provider_api_json, dict):
                parser.error("--provider-api-json-file must contain a JSON object")
        try:
            _parse_key_value_pairs(args.provider_api_query)
        except ValueError as e:
            parser.error(str(e))
        asyncio.run(
            call_provider_api_command(
                data_dir=args.data_dir,
                provider_id=args.provider_api_provider,
                capability_id=args.provider_api_capability,
                resource=args.provider_api_resource,
                method=args.provider_api_method,
                query_items=args.provider_api_query,
                json_payload=provider_api_json,
                route_id=args.gateway_route_id,
                auth_token=args.gateway_auth_token,
                gateway_policy_id=args.gateway_policy_id,
                requester=args.provider_api_requester,
                job_id=args.provider_api_job_id,
                title=args.provider_api_title,
            )
        )
    elif (
        args.llm_runtime_status
        or args.llm_runtime_enable
        or args.llm_runtime_disable
        or args.llm_runtime_backend
        or args.llm_runtime_provider
        or args.llm_runtime_follow_env
    ):
        if args.llm_runtime_status and not (
            args.llm_runtime_enable
            or args.llm_runtime_disable
            or args.llm_runtime_backend
            or args.llm_runtime_provider
            or args.llm_runtime_follow_env
        ):
            asyncio.run(show_llm_runtime_command(args.data_dir))
        else:
            if args.llm_runtime_enable and args.llm_runtime_disable:
                parser.error("Use only one of --llm-runtime-enable or --llm-runtime-disable")
            enabled: bool | None = None
            if args.llm_runtime_enable:
                enabled = True
            elif args.llm_runtime_disable:
                enabled = False
            asyncio.run(
                update_llm_runtime_command(
                    data_dir=args.data_dir,
                    enabled=enabled,
                    backend=args.llm_runtime_backend or None,
                    provider=args.llm_runtime_provider or None,
                    follow_env=args.llm_runtime_follow_env,
                    note=args.llm_runtime_note,
                    updated_by=args.llm_runtime_updated_by,
                )
            )
    elif args.runtime_model:
        asyncio.run(show_runtime_model(args.data_dir))
    elif args.artifact_id:
        asyncio.run(
            show_artifact_command(
                data_dir=args.data_dir,
                artifact_id=args.artifact_id,
                kind=args.artifact_job_kind,
            )
        )
    elif args.list_artifacts:
        asyncio.run(
            list_artifacts_command(
                data_dir=args.data_dir,
                kind=args.artifact_job_kind,
                job_id=args.artifact_job_id,
                artifact_kind=args.artifact_kind,
                limit=args.artifact_limit,
            )
        )
    elif args.build_resume:
        asyncio.run(resume_build_command(data_dir=args.data_dir, job_id=args.build_resume))
    elif args.build_repo:
        if not args.build_description:
            parser.error("--build-description is required with --build-repo")
        try:
            build_plan = (
                _load_build_operation_plan(args.build_plan_file)
                if args.build_plan_file
                else []
            )
            build_acceptance = (
                _load_acceptance_criteria(args.build_acceptance_file)
                if args.build_acceptance_file
                else []
            ) + list(args.build_acceptance)
        except ValueError as e:
            parser.error(str(e))
        asyncio.run(
            run_build_command(
                data_dir=args.data_dir,
                repo_path=args.build_repo,
                description=args.build_description,
                target_files=args.build_target_file,
                implementation_plan=build_plan,
                acceptance_criteria=build_acceptance,
                requester=args.build_requester,
                context=args.build_context,
                skip_review=args.build_skip_review,
            )
        )
    elif args.intake_repo or args.intake_git_url:
        try:
            intake_plan = (
                _load_build_operation_plan(args.intake_plan_file)
                if args.intake_plan_file
                else []
            )
            intake_acceptance = (
                _load_acceptance_criteria(args.intake_acceptance_file)
                if args.intake_acceptance_file
                else []
            ) + list(args.intake_acceptance)
        except ValueError as e:
            parser.error(str(e))
        asyncio.run(
            run_intake_command(
                data_dir=args.data_dir,
                repo_path=args.intake_repo,
                git_url=args.intake_git_url,
                diff_spec=args.intake_diff,
                work_type=args.intake_work_type,
                build_type=args.intake_build_type,
                description=args.intake_description,
                requester=args.intake_requester,
                context=args.intake_context,
                focus_areas=args.intake_focus_area,
                target_files=args.intake_target_file,
                implementation_plan=intake_plan,
                acceptance_criteria=intake_acceptance,
                preview_only=args.intake_preview,
            )
        )
    else:
        asyncio.run(run_agent(args.data_dir))


if __name__ == "__main__":
    main()
