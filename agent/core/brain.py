"""
Agent Life Space — Agent Brain

Channel-agnostic message processing. THE core intelligence.
Extracted from TelegramHandler to enable multi-channel support.

Runtime pipeline (every layer actually executes in process()):
    1. Multi-task detection → work queue
    2. Internal dispatch (no LLM)
    3. Semantic cache lookup → early return on hit
    4. RAG retrieval → direct answer or context augmentation
    5. Task classification → model selection + learning-based escalation
    6. LLM call (via provider abstraction) with augmented prompt
    7. Post-routing quality escalation (re-run with stronger model if needed)
    8. Learning feedback + skill auto-update
    9. Channel policy filter + explanation log

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
from agent.core.identity import get_agent_identity, get_response_language_instruction
from agent.core.persona import get_agent_prompt, get_simple_prompt, get_system_prompt
from agent.social.channel import IncomingMessage

logger = structlog.get_logger(__name__)


class AgentBrain:
    """
    Channel-agnostic message processing engine.
    Processes IncomingMessage, returns response text.

    Full pipeline: dispatch → cache → RAG → classify → LLM → escalation → learning → filter
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
        self._semantic_cache: Any = None
        self._rag_index: Any = None
        self._learner: Any = None
        self._persistent_conv: Any = None

        # Status model (optional)
        self._status: Any = None
        try:
            from agent.core.status import AgentStatusModel
            self._status = AgentStatusModel()
        except ImportError:
            pass

        # Explanation log (optional)
        self._explanation_log: Any = None
        try:
            from agent.core.explanation import ExplanationLog
            self._explanation_log = ExplanationLog()
        except ImportError:
            pass

    async def process(self, message: IncomingMessage) -> str:
        """
        Process an incoming message from any channel.
        Returns response text.

        Pipeline (layers 1-4 can early-return, layers 5-9 always run together):
            1. Work queue (multi-task)
            2. Internal dispatch
            3. Semantic cache (early return on hit)
            4. RAG retrieval (early return on direct hit)
            5. Classification + learning escalation
            6. LLM call (channel-enforced)
            7. Quality escalation (preserves execution mode)
            8. Learning feedback
            9. Channel policy + explanation
        """
        if self._status:
            from agent.core.status import AgentState
            self._status.transition(AgentState.THINKING, f"processing from {message.channel_type}")

        try:
            return await self._process_inner(message)
        finally:
            # Reset status to IDLE unless in a meaningful terminal state
            # (BLOCKED, WAITING_APPROVAL should persist for operator visibility)
            if self._status:
                from agent.core.status import AgentState
                terminal_states = {AgentState.BLOCKED, AgentState.WAITING_APPROVAL}
                if self._status._state not in terminal_states:
                    self._status.transition(AgentState.IDLE, "process complete")

    async def _process_inner(self, message: IncomingMessage) -> str:
        """Inner processing — separated so try/finally in process() always resets status."""
        text = message.text.strip()
        if not text:
            return "Prázdna správa."

        # Per-chat conversation
        chat_conv = self._get_chat_conversation(message.chat_id)
        conv_id = self._get_conversation_id(message.chat_id)

        # ── Layer 1: Multi-task detection → work queue ──
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

        # ── Layer 2: Internal dispatch (no LLM) ──
        short_followup = len(chat_conv) > 0 and len(text.split()) <= 8
        if not short_followup:
            from agent.brain.dispatcher import InternalDispatcher
            dispatcher = InternalDispatcher(self._agent)
            internal_result = await dispatcher.try_handle(text)
            if internal_result:
                return internal_result

        # ── Layer 3: Semantic cache ──
        cached_response = self._try_semantic_cache(text)
        if cached_response:
            logger.info("brain_cache_hit", query=text[:50])
            return f"{cached_response}\n\n_📦 cache hit_"

        # ── Layer 4: RAG retrieval ──
        rag_context = ""
        rag_direct = self._try_rag_retrieval(text)
        if rag_direct is not None:
            if rag_direct.get("action") == "direct":
                return f"Z knowledge base ({rag_direct.get('source', '')}):\n{rag_direct.get('context', '')}"
            if rag_direct.get("action") == "augment":
                rag_context = rag_direct.get("context", "")

        # ── Layer 5: Task classification + model selection ──
        from agent.core.models import classify_task, get_model
        task_type = classify_task(text)
        model = get_model(task_type)

        # Security: non-owner nemôže programming
        if message.is_group and not message.is_owner and task_type == "programming":
            task_type = "chat"
            model = get_model(task_type)

        # Channel enforcement for CLI path — restricted channels block file access
        # regardless of task_type (prevents API callers from getting host access)
        restricted_channels = {"agent_api", "webhook", "public"}
        cli_allow_file_access = (
            task_type == "programming"
            and message.channel_type not in restricted_channels
        )

        # Learning-based model escalation
        learner = self._get_learner()
        learning_escalation = None
        if learner:
            adaptation = learner.adapt_model(task_type, text)
            if adaptation.get("model_override"):
                from agent.core.models import OPUS, SONNET
                override_map = {
                    "claude-sonnet-4-6": SONNET,
                    "claude-opus-4-6": OPUS,
                }
                override = override_map.get(adaptation["model_override"])
                if override:
                    allowed, blocked_reason = self._budget_allows_escalation()
                    if allowed:
                        logger.info("learning_override_model",
                                    original=model.model_id,
                                    override=override.model_id,
                                    reason=adaptation.get("reason", ""))
                        model = override
                        learning_escalation = adaptation.get("reason", "")
                    else:
                        logger.info("learning_override_budget_blocked",
                                    original=model.model_id,
                                    blocked_override=override.model_id,
                                    reason=blocked_reason)
                        learning_escalation = blocked_reason

        # Build prompt
        is_agent_chat = message.channel_type == "agent_api"
        active_prompt = get_agent_prompt() if is_agent_chat else get_system_prompt()

        # Learning-augmented prompt (add past errors if relevant)
        if learner:
            active_prompt = learner.augment_prompt(text, active_prompt)

        # Persistent conversation context
        persistent_context = await self._get_persistent_context(conv_id, text)

        # Conversation history
        conv_context = ""
        if chat_conv:
            conv_lines = []
            identity = get_agent_identity()
            owner_name = identity.owner_name
            for msg in chat_conv[-self._max_conversation:]:
                role = (
                    msg.get("sender", owner_name)
                    if msg["role"] == "user"
                    else identity.agent_name
                )
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
                f"At the end always include a short summary. {get_response_language_instruction()}"
            )
        elif task_type in ("simple", "factual", "greeting"):
            prompt = f"{get_simple_prompt()}\n{message.sender_name}: {text}\n"
        else:
            prompt = f"{active_prompt}\n"
            if rag_context:
                prompt += f"Relevantný kontext z knowledge base:\n{rag_context}\n\n"
            if persistent_context:
                prompt += f"{persistent_context}\n\n"
            elif conv_context:
                prompt += f"Predchádzajúca konverzácia:\n{conv_context}\n\n"
            prompt += (
                f"{message.sender_name}: {text}\n"
                f"{get_response_language_instruction()}"
            )

        # ── Layer 6: LLM call via provider ──
        from agent.core.llm_provider import GenerateRequest, get_provider

        if self._status:
            from agent.core.status import AgentState
            self._status.transition(AgentState.EXECUTING, f"LLM call: {model.model_id}")

        project_root = os.environ.get(
            "AGENT_PROJECT_ROOT",
            str(self._agent._data_dir.parent) if hasattr(self._agent, "_data_dir") else "",
        )

        provider = get_provider()
        backend = os.environ.get("LLM_BACKEND", "cli")
        usage_cost = 0.0
        usage_input_tokens = 0
        usage_output_tokens = 0
        used_tool_loop = False

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
            used_tool_loop = bool(loop_result.tool_calls)

            if used_tool_loop:
                logger.info("brain_tool_use", tools_called=len(loop_result.tool_calls),
                            turns=loop_result.turns)
        else:
            # CLI backend or no tools: direct generate
            # Channel enforcement: restricted channels never get file access
            response = await provider.generate(GenerateRequest(
                messages=[{"role": "user", "content": prompt}],
                model=model.model_id,
                timeout=model.timeout,
                max_turns=model.max_turns,
                allow_file_access=cli_allow_file_access,
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

        # ── Layer 7: Post-routing quality escalation ──
        # If original used tool loop, escalation must also use tool loop
        # to preserve tool context (not fall back to single-shot generate)
        try:
            from agent.core.response_quality import assess_quality
            quality = assess_quality(text, reply, model.model_id)
            if quality.should_escalate and not used_tool_loop:
                # Only escalate single-shot responses; tool-loop results
                # already have rich context and re-running would lose it
                allowed, blocked_reason = self._budget_allows_escalation()
                if not allowed:
                    logger.info("post_routing_escalation_budget_blocked",
                                from_model=model.model_id,
                                score=quality.score,
                                reason=blocked_reason)
                else:
                    logger.info("post_routing_escalation",
                                from_model=model.model_id,
                                score=quality.score,
                                reason=quality.reason)
                    from agent.core.models import SONNET
                    esc_response = await provider.generate(GenerateRequest(
                        messages=[{"role": "user", "content": prompt}],
                        model=SONNET.model_id,
                        timeout=SONNET.timeout,
                        max_turns=SONNET.max_turns,
                        allow_file_access=cli_allow_file_access,
                        cwd=project_root,
                    ))
                    if esc_response.success and esc_response.text:
                        reply = esc_response.text
                        model = SONNET
                        usage_cost += esc_response.cost_usd
                        usage_input_tokens += esc_response.input_tokens
                        usage_output_tokens += esc_response.output_tokens
                        self._total_cost_usd += esc_response.cost_usd
                        self._total_input_tokens += esc_response.input_tokens
                        self._total_output_tokens += esc_response.output_tokens
                        logger.info("post_routing_escalation_success", model=SONNET.model_id)
        except Exception as e:
            logger.error("quality_escalation_error", error=str(e))

        # ── Layer 8: Learning feedback + skill auto-update ──
        if learner:
            try:
                learner.process_outcome(
                    task_description=text,
                    reply=reply,
                    model_used=model.model_id,
                )
            except Exception as e:
                logger.error("learning_feedback_error", error=str(e))

        await self._auto_update_skills(reply)

        # Store response in conversation buffer
        clean_reply = reply.split("\n\n_💰")[0] if "_💰" in reply else reply
        chat_conv.append({"role": "assistant", "content": clean_reply[:300]})
        if len(chat_conv) > self._max_conversation:
            chat_conv.pop(0)

        # Persist exchange
        await self._save_exchange(conv_id, text, clean_reply, message.sender_name)

        # Store in semantic cache (not for programming tasks)
        if self._semantic_cache and task_type not in ("programming",):
            try:
                self._semantic_cache.store(text, clean_reply)
            except Exception:
                pass

        # ── Layer 9: Channel policy filter + explanation ──
        from agent.social.channel_policy import (
            can_send_response,
            classify_response,
            get_channel_capabilities,
        )
        channel_caps = get_channel_capabilities(
            message.channel_type, is_owner=message.is_owner, is_group=message.is_group,
        )
        response_class = classify_response(reply)
        if not can_send_response(response_class, channel_caps):
            reply = "Táto informácia nie je dostupná na tomto kanáli."
            logger.warning("response_filtered",
                           response_class=response_class.value,
                           channel=message.channel_type)

        # Record explanation with full context
        if self._explanation_log is not None:
            from agent.core.explanation import DecisionExplanation
            from agent.core.models import classify_task_detailed
            classification = classify_task_detailed(text)

            # Gather policy context from tool executor if available
            policy_decisions: list[dict[str, Any]] = []
            if hasattr(self, "_tool_executor") and self._tool_executor:
                try:
                    recent = self._tool_executor.action_log.get_recent(5)
                    policy_decisions = [
                        {"tool": a.get("tool_name", ""), "allowed": a.get("policy_allowed", True),
                         "risk": a.get("policy_risk_level", "")}
                        for a in recent
                    ]
                except Exception:
                    pass

            # Gather learning context
            skill_confidence: dict[str, float] = {}
            past_errors: list[str] = []
            if learner:
                try:
                    advice = learner.get_advice_for_task(text)
                    past_errors = advice.get("past_errors", [])[:3]
                    for s in advice.get("relevant_skills", []):
                        if isinstance(s, dict):
                            skill_confidence[s.get("name", "")] = s.get("confidence", 0.0)
                except Exception:
                    pass

            # Gather memory provenance breakdown
            provenance_breakdown: dict[str, int] = {}
            try:
                mem_stats = self._agent.memory.get_stats()
                provenance_breakdown = mem_stats.get("by_provenance", {})
            except Exception:
                pass

            self._explanation_log.record(DecisionExplanation(
                action_type="message_response",
                action_summary=f"Odpovedal na '{text[:50]}'",
                routing_task_type=classification.task_type,
                routing_score=classification.score,
                routing_signals=classification.signals,
                model_used=model.model_id,
                policy_decisions=policy_decisions,
                learning_escalation=learning_escalation or "",
                past_errors_used=past_errors,
                skill_confidence=skill_confidence,
                memories_recalled=1 if rag_context else 0,
                provenance_breakdown=provenance_breakdown,
            ))

        # Usage info
        model_short = model.model_id.split("-")[1] if "-" in model.model_id else model.model_id
        reply += (
            f"\n\n_💰 ${usage_cost:.4f} | {model_short} | "
            f"⬆{usage_input_tokens:,} ⬇{usage_output_tokens:,} tokens_"
        )

        return reply

    # ─────────────────────────────────────────────
    # Pipeline components
    # ─────────────────────────────────────────────

    def _try_semantic_cache(self, text: str) -> str | None:
        """Layer 3: Lookup semantic cache. Returns cached response or None."""
        try:
            if self._semantic_cache is None:
                from agent.memory.semantic_cache import SemanticCache
                self._semantic_cache = SemanticCache()
            return self._semantic_cache.lookup(text)
        except Exception:
            return None

    def _try_rag_retrieval(self, text: str) -> dict[str, Any] | None:
        """Layer 4: RAG retrieval. Returns action dict or None."""
        try:
            if self._rag_index is None:
                from agent.memory.rag import RAGIndex
                self._rag_index = RAGIndex()
                if not self._rag_index._built:
                    self._rag_index.build_index()
            result = self._rag_index.retrieve_for_llm(text)
            if result.get("action") in ("direct", "augment"):
                return result
        except Exception as e:
            logger.error("rag_retrieval_error", error=str(e))
        return None

    def _get_learner(self) -> Any:
        """Lazy-init LearningSystem."""
        if self._learner is None:
            try:
                from agent.brain.learning import LearningSystem
                self._learner = LearningSystem()
            except Exception:
                pass
        return self._learner

    async def _auto_update_skills(self, reply: str) -> None:
        """Scan reply for evidence of skill usage and auto-update skills.json."""
        try:

            from agent.brain.skills import SkillRegistry
            from agent.core.paths import get_project_root
            project_dir = (
                str(self._agent._data_dir.parent) if hasattr(self._agent, "_data_dir")
                else get_project_root()
            )
            registry = SkillRegistry(f"{project_dir}/agent/brain/skills.json")
            reply_lower = reply.lower()

            skill_signals = {
                "curl": ["curl ", "curl -s", "http request", "api call"],
                "web_scraping": ["scraping", "beautifulsoup", "requests.get"],
                "git_commit": ["git commit", "git push", "commitol", "pushol"],
                "git_status": ["git status", "git log", "git diff"],
                "python_run": ["python3 -c", "spustil skript", "python3 -m"],
                "pytest": ["pytest", "testov prešlo", "tests passed"],
                "docker_run": ["docker run", "docker build", "kontajner"],
                "system_health": ["free -h", "df -h"],
                "maintenance": ["cache", "čistenie", "stale proces"],
                "pip_install": ["pip install", "pip3 install"],
                "memory_store": ["uložil do pamäte", "memory.store"],
                "memory_query": ["memory.query", "prehľadal pamäť"],
                "task_create": ["vytvoril úlohu", "create_task"],
            }

            success_markers = ["ok", "funguje", "hotovo", "úspešne", "success", "done", "passed", "✅"]
            failure_markers = ["chyba", "error", "failed", "nefunguje", "timeout", "❌"]
            has_success = any(m in reply_lower for m in success_markers)
            has_failure = any(m in reply_lower for m in failure_markers)

            if not has_success and not has_failure:
                return

            updated = []
            for skill_name, patterns in skill_signals.items():
                if any(p in reply_lower for p in patterns):
                    if has_success and not has_failure:
                        registry.record_success(skill_name)
                        updated.append(f"{skill_name}:success")
                    elif has_failure and not has_success:
                        registry.record_failure(skill_name, reply[:200])
                        updated.append(f"{skill_name}:failure")

            if updated:
                logger.info("skills_auto_updated", skills=updated)
        except Exception as e:
            logger.error("skills_auto_update_error", error=str(e))

    # ─────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────

    def _get_chat_conversation(self, chat_id: str) -> list[dict[str, str]]:
        if chat_id not in self._conversations:
            self._conversations[chat_id] = []
        return self._conversations[chat_id]

    def _budget_allows_escalation(self) -> tuple[bool, str]:
        try:
            from agent.control.policy import allow_budget_escalation

            budget_status = self._agent.finance.check_budget(1.0)
            return allow_budget_escalation(budget_status)
        except Exception:
            return True, ""

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

    def get_agent_status(self) -> dict[str, Any]:
        """Get current agent status including state model."""
        result = self.get_usage()
        if self._status:
            result["status"] = self._status.get_status()
            result["status_history"] = self._status.get_history(limit=10)
        return result
