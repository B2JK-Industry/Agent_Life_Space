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


async def run_agent(data_dir: str = "agent") -> None:
    """Main agent loop with graceful shutdown."""
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
            bot = TelegramBot(token=tg_token, allowed_user_ids=allowed_ids)
            # Start agent work loop
            from agent.core.agent_loop import AgentLoop
            owner_id = int(tg_user_id.split(",")[0]) if tg_user_id else 0
            work_loop = AgentLoop(telegram_bot=bot)
            work_loop_task = asyncio.create_task(work_loop.start())

            handler = TelegramHandler(agent, bot=bot, work_loop=work_loop, owner_chat_id=owner_id)
            bot.on_message(handler.handle)
            telegram_task = asyncio.create_task(bot.start())
            logger.info("telegram_bot_enabled")

            # Start cron (John's initiative)
            from agent.core.cron import AgentCron
            cron = AgentCron(agent, telegram_bot=bot, owner_chat_id=owner_id)
            cron_task = asyncio.create_task(cron.start())
            logger.info("cron_enabled", owner_chat_id=owner_id)
        else:
            cron = None
            cron_task = None
            logger.info("telegram_bot_disabled", reason="no TELEGRAM_BOT_TOKEN")

        # Wait for shutdown signal
        await shutdown_event.wait()

        # Graceful shutdown
        if work_loop:
            await work_loop.stop()

        if cron:
            await cron.stop()
            if cron_task:
                cron_task.cancel()

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

    except Exception:
        logger.exception("agent_fatal_error")
        await agent.stop()
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
