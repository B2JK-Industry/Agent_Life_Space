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
import os
import signal
import sys
from datetime import UTC, datetime

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


PIDFILE = "/tmp/agent-life-space.pid"


def _check_pidfile() -> None:
    """Prevent duplicate agent instances. Fail-fast if already running."""
    if os.path.exists(PIDFILE):
        try:
            old_pid = int(open(PIDFILE).read().strip())
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
    _check_pidfile()

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
        # Store docker status for runtime checks
        os.environ.update({"_DOCKER_AVAILABLE": "1" if docker_available else "0"})
        # SECURITY: Sandbox-only mode is DEFAULT. Host file access blocked unless
        # explicitly overridden with AGENT_SANDBOX_ONLY=0.
        if os.environ.get("AGENT_SANDBOX_ONLY") is None:
            os.environ.update({"AGENT_SANDBOX_ONLY": "1"})
            logger.info("sandbox_only_mode", status="enabled (default)")
        elif os.environ.get("AGENT_SANDBOX_ONLY") == "0":
            logger.warning("sandbox_only_disabled", hint="Host file access enabled. CLI has full FS access.")

        # Start agent in background
        agent_task = asyncio.create_task(agent.start())

        # Start Telegram bot if token is available
        telegram_task = None
        tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        tg_user_id = os.environ.get("TELEGRAM_USER_ID", "")
        if tg_token:
            from agent.social.telegram_bot import TelegramBot
            from agent.social.telegram_handler import TelegramHandler

            # Support comma-separated user IDs: "123,456,789"
            allowed_ids = [int(x.strip()) for x in tg_user_id.split(",") if x.strip()] if tg_user_id else []
            owner_name = os.environ.get("AGENT_OWNER_NAME", "Daniel")
            bot = TelegramBot(token=tg_token, allowed_user_ids=allowed_ids, owner_name=owner_name)
            # Start agent work loop
            from agent.core.agent_loop import AgentLoop
            owner_id = int(tg_user_id.split(",")[0]) if tg_user_id else 0
            work_loop = AgentLoop(telegram_bot=bot)
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

            # Preload semantic models in background
            async def _preload_models():
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
            asyncio.create_task(_preload_models())

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

            # Start cron (John's initiative)
            from agent.core.cron import AgentCron
            cron = AgentCron(agent, telegram_bot=bot, owner_chat_id=owner_id)
            cron_task = asyncio.create_task(cron.start())
            logger.info("cron_enabled", owner_chat_id=owner_id)
        else:
            work_loop = None
            work_loop_task = None
            cron = None
            cron_task = None
            agent_api = None
            agent_api_task = None
            logger.info("telegram_bot_disabled", reason="no TELEGRAM_BOT_TOKEN")

        # Wait for shutdown signal
        await shutdown_event.wait()

        # Graceful shutdown
        if work_loop:
            await work_loop.stop()
            if work_loop_task:
                work_loop_task.cancel()

        if cron:
            await cron.stop()
            if cron_task:
                cron_task.cancel()

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

    agent = AgentOrchestrator(data_dir=data_dir)
    await agent.initialize()
    status = agent.get_status()
    print(orjson.dumps(status, option=orjson.OPT_INDENT_2).decode())
    await agent.stop()


async def show_health(data_dir: str = "agent") -> None:
    """Show system health."""
    import orjson

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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agent Life Space — Self-hosted autonomous agent"
    )
    parser.add_argument(
        "--data-dir",
        default="agent",
        help="Data directory for agent storage (default: agent)",
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
    args = parser.parse_args()

    if args.status:
        asyncio.run(show_status(args.data_dir))
    elif args.health:
        asyncio.run(show_health(args.data_dir))
    else:
        asyncio.run(run_agent(args.data_dir))


if __name__ == "__main__":
    main()
