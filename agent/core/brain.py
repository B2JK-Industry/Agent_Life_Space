"""
Agent Life Space — Agent Brain

Channel-agnostic message processing. THE core intelligence.
Extracted from TelegramHandler to enable multi-channel support.

What it does:
    1. Multi-task detection → work queue
    2. Internal dispatch (no LLM)
    3. Semantic cache + RAG
    4. Persistent conversation (per-chat)
    5. Task classification → model selection
    6. LLM call (via provider abstraction)
    7. Learning feedback + skill auto-update
    8. Post-routing quality escalation

What it does NOT do:
    - Telegram-specific formatting
    - Channel-specific commands (/start, /help, etc.)
    - Typing indicators
    - Channel authentication
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import structlog

from agent.core.agent import AgentOrchestrator
from agent.core.persona import AGENT_PROMPT, SIMPLE_PROMPT, SYSTEM_PROMPT
from agent.social.channel import IncomingMessage

logger = structlog.get_logger(__name__)


class AgentBrain:
    """
    Channel-agnostic message processing engine.
    Processes IncomingMessage, returns response text.
    """

    def __init__(
        self,
        agent: AgentOrchestrator,
        work_loop: Any = None,
        owner_chat_id: int = 0,
    ) -> None:
        self._agent = agent
        self._work_loop = work_loop
        self._owner_chat_id = owner_chat_id

        # Usage tracking
        self._total_cost_usd: float = 0.0
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._total_requests: int = 0

        # Per-chat conversation buffers
        self._conversations: dict[str, list[dict[str, str]]] = {}
        self._max_conversation = 10

        # Lazy-init components
        self._semantic_cache = None
        self._rag_index = None
        self._persistent_conv: Any = None

    async def process(self, message: IncomingMessage) -> str:
        """
        Process an incoming message from any channel.
        Returns response text.
        """
        text = message.text.strip()
        if not text:
            return "Prázdna správa."

        # Per-chat conversation
        chat_conv = self._get_chat_conversation(message.chat_id)
        conv_id = self._get_conversation_id(message.chat_id)

        # Multi-task detection → work queue
        import re
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        numbered = [re.sub(r"^\d+[\.\)]\s*", "", line) for line in lines if re.match(r"^\d+[\.\)]", line)]

        if not numbered:
            action_prefixes = ["otestuj", "spusti", "urob", "skontroluj", "vytvor"]
            for prefix in action_prefixes:
                if text.lower().startswith(prefix) and "," in text:
                    rest = text[len(prefix):].strip().lstrip(":")
                    items = [f"{prefix} {item.strip()}" for item in rest.split(",") if item.strip()]
                    if len(items) >= 2:
                        numbered = items
                        break

        if len(numbered) >= 2 and self._work_loop:
            if message.is_group and not message.is_owner:
                return "Pracovnú frontu môže používať len owner."
            added = self._work_loop.add_work(numbered, chat_id=int(message.chat_id))
            return f"Mám {added} úloh. Spracúvam postupne, výsledky posielam priebežne."

        # Store user message in memory
        from agent.memory.store import MemoryEntry, MemoryType
        await self._agent.memory.store(MemoryEntry(
            content=f"{message.sender_name} mi napísal: {text}",
            memory_type=MemoryType.EPISODIC,
            tags=["message", "user_input", message.channel_type],
            source=message.channel_type,
            importance=0.6,
        ))

        # Internal dispatch (no LLM)
        short_followup = len(chat_conv) > 0 and len(text.split()) <= 8
        if not short_followup:
            from agent.brain.dispatcher import InternalDispatcher
            dispatcher = InternalDispatcher(self._agent)
            internal_result = await dispatcher.try_handle(text)
            if internal_result:
                return internal_result

        # Task classification + model selection
        from agent.core.models import classify_task, get_model
        task_type = classify_task(text)
        model = get_model(task_type)

        # Security: non-owner nemôže programming
        if message.is_group and not message.is_owner and task_type == "programming":
            task_type = "chat"
            model = get_model(task_type)

        # Build prompt
        is_agent_chat = message.channel_type == "agent_api"
        active_prompt = AGENT_PROMPT if is_agent_chat else SYSTEM_PROMPT

        # Persistent conversation context
        persistent_context = await self._get_persistent_context(conv_id, text)

        # Conversation history
        conv_context = ""
        if chat_conv:
            conv_lines = []
            owner_name = os.environ.get("AGENT_OWNER_NAME", "Daniel")
            for msg in chat_conv[-self._max_conversation:]:
                role = msg.get("sender", owner_name) if msg["role"] == "user" else "John"
                conv_lines.append(f"{role}: {msg['content'][:200]}")
            conv_context = "\n".join(conv_lines)

        # Store user message in buffer
        chat_conv.append({"role": "user", "content": text, "sender": message.sender_name})
        if len(chat_conv) > self._max_conversation:
            chat_conv.pop(0)

        # Build prompt based on task type
        if task_type == "programming":
            prompt = (
                f"{active_prompt}\n"
                f"Si programátor.\n\n"
                f"ÚLOHA: {text}\n\n"
                f"Na konci VŽDY napíš zhrnutie. Odpovedaj po slovensky."
            )
        elif task_type in ("simple", "factual", "greeting"):
            prompt = f"{SIMPLE_PROMPT}\n{message.sender_name}: {text}\n"
        else:
            prompt = f"{active_prompt}\n"
            if persistent_context:
                prompt += f"{persistent_context}\n\n"
            elif conv_context:
                prompt += f"Predchádzajúca konverzácia:\n{conv_context}\n\n"
            prompt += f"{message.sender_name}: {text}\nOdpovedaj po slovensky."

        # LLM call via provider
        from agent.core.llm_provider import GenerateRequest, get_provider

        project_root = os.environ.get(
            "AGENT_PROJECT_ROOT",
            str(self._agent._data_dir.parent) if hasattr(self._agent, "_data_dir") else "",
        )

        provider = get_provider()
        backend = os.environ.get("LLM_BACKEND", "cli")
        usage_cost = 0.0
        usage_input_tokens = 0
        usage_output_tokens = 0

        # API backend: use ToolUseLoop (multi-turn with function calling)
        if backend == "api" and provider.supports_tools() and hasattr(self, "_tool_executor") and self._tool_executor:
            from agent.core.tool_loop import ToolUseLoop
            from agent.core.tool_policy import ToolExecutionContext
            from agent.core.tools import AGENT_TOOLS

            tool_loop = ToolUseLoop(provider, self._tool_executor, max_turns=10)
            loop_result = await tool_loop.run(
                messages=[{"role": "user", "content": prompt}],
                system=active_prompt,
                model=model.model_id,
                tools=AGENT_TOOLS,
                timeout=model.timeout,
                tool_context=ToolExecutionContext(
                    is_owner=message.is_owner,
                    safe_mode=message.is_group and not message.is_owner,
                    channel_type=message.channel_type,
                ),
            )

            reply = loop_result.text or "Prepáč, nepodarilo sa mi odpovedať."
            self._total_cost_usd += loop_result.total_cost
            self._total_input_tokens += loop_result.total_input_tokens
            self._total_output_tokens += loop_result.total_output_tokens
            self._total_requests += 1
            usage_cost = loop_result.total_cost
            usage_input_tokens = loop_result.total_input_tokens
            usage_output_tokens = loop_result.total_output_tokens

            if loop_result.tool_calls:
                logger.info("brain_tool_use", tools_called=len(loop_result.tool_calls),
                            turns=loop_result.turns)
        else:
            # CLI backend or no tools: direct generate
            response = await provider.generate(GenerateRequest(
                messages=[{"role": "user", "content": prompt}],
                model=model.model_id,
                timeout=model.timeout,
                max_turns=model.max_turns,
                allow_file_access=task_type == "programming",
                cwd=project_root,
            ))

            if not response.success:
                return f"Chyba: {response.error[:200]}"

            reply = response.text or "Prepáč, nepodarilo sa mi odpovedať."
            self._total_cost_usd += response.cost_usd
            self._total_input_tokens += response.input_tokens
            self._total_output_tokens += response.output_tokens
            self._total_requests += 1
            usage_cost = response.cost_usd
            usage_input_tokens = response.input_tokens
            usage_output_tokens = response.output_tokens

        # Store response in conversation buffer
        clean_reply = reply.split("\n\n_💰")[0] if "_💰" in reply else reply
        chat_conv.append({"role": "assistant", "content": clean_reply[:300]})
        if len(chat_conv) > self._max_conversation:
            chat_conv.pop(0)

        # Persist exchange
        await self._save_exchange(conv_id, text, clean_reply, message.sender_name)

        # Usage info
        model_short = model.model_id.split("-")[1] if "-" in model.model_id else model.model_id
        reply += (
            f"\n\n_💰 ${usage_cost:.4f} | {model_short} | "
            f"⬆{usage_input_tokens:,} ⬇{usage_output_tokens:,} tokens_"
        )

        return reply

    # ─────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────

    def _get_chat_conversation(self, chat_id: str) -> list[dict[str, str]]:
        if chat_id not in self._conversations:
            self._conversations[chat_id] = []
        return self._conversations[chat_id]

    @staticmethod
    def _get_conversation_id(chat_id: str) -> str:
        return f"chat-{chat_id}-{datetime.now(UTC).strftime('%Y-%m-%d')}"

    async def _get_persistent_context(self, conv_id: str, query: str) -> str:
        try:
            if self._persistent_conv is None:
                from agent.memory.persistent_conversation import PersistentConversation
                self._persistent_conv = PersistentConversation(
                    db_path=str(self._agent._data_dir / "memory" / "conversations.db")
                )
                await self._persistent_conv.initialize()
            return await self._persistent_conv.build_context(conv_id, query=query)
        except Exception as e:
            logger.error("persistent_conv_error", error=str(e))
            return ""

    async def _save_exchange(
        self, conv_id: str, text: str, reply: str, sender: str
    ) -> None:
        try:
            if self._persistent_conv:
                await self._persistent_conv.save_exchange(
                    conv_id, text, reply[:500], sender=sender,
                )
        except Exception as e:
            logger.error("persistent_save_error", error=str(e))

    def get_usage(self) -> dict[str, Any]:
        return {
            "total_requests": self._total_requests,
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_cost_usd": round(self._total_cost_usd, 4),
        }
