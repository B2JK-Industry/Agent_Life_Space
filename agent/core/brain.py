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

import asyncio
import os
from datetime import UTC, datetime
from typing import Any, ClassVar, cast

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

        # Per-chat conversation buffers (in-RAM tail).
        # 20 turns = ~10 user/assistant exchanges. The tail bound is
        # generous enough that natural multi-turn conversations stay
        # in scope without needing the LLM to re-fetch from SQLite.
        self._conversations: dict[str, list[dict[str, str]]] = {}
        self._max_conversation = 20
        # Set of chat_ids whose in-RAM buffer has already been
        # hydrated from the persistent conversation DB. We hydrate
        # exactly once per chat per process lifetime.
        self._hydrated_chats: set[str] = set()

        # Lazy-init components
        self._semantic_cache: Any = None
        self._rag_index: Any = None
        self._learner: Any = None
        self._persistent_conv: Any = None
        # Wired in by __main__ after construction so the brain can run
        # tool-use loops over the configured ToolExecutor.
        self._tool_executor: Any = None

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
           1.5 Deterministic Telegram intent layer (presence/version/skills/...)
            2. Internal dispatch
            3. Semantic cache (early return on hit)
            4. RAG retrieval (early return on direct hit)
            5. Classification + learning escalation
            6. LLM call (channel-enforced)
            7. Quality escalation (preserves execution mode)
            8. Learning feedback
            9. Channel policy + explanation

        Conversation persistence (history bug fix): every reply path —
        deterministic intent, dispatcher, cache, RAG, work-queue,
        deny-guard, main LLM — goes through ``_finalize_reply`` so the
        per-chat in-RAM tail and the SQLite persistent_conv both stay
        in sync. This is what makes "remember the previous message"
        actually work even when the prior reply was a fast-path intent.
        """
        if self._status:
            from agent.core.status import AgentState
            self._status.transition(AgentState.THINKING, f"processing from {message.channel_type}")

        try:
            text = message.text.strip()
            # Hydrate the in-RAM tail from the persistent SQLite store
            # the first time we see this chat after a process restart.
            # Otherwise the very first message after a restart loses
            # its conversational context entirely.
            if text:
                await self._hydrate_chat_conv_if_needed(message.chat_id)
            reply = await self._process_inner(message)
            if text and reply:
                # Idempotent: if the main LLM path already appended the
                # exchange, finalize is a no-op. For all the early-return
                # paths it does the bookkeeping that the main path used
                # to do at the bottom of _process_inner.
                try:
                    chat_conv = self._get_chat_conversation(message.chat_id)
                    conv_id = self._get_conversation_id(message.chat_id)
                    await self._finalize_reply(
                        message=message,
                        text=text,
                        reply=reply,
                        chat_conv=chat_conv,
                        conv_id=conv_id,
                    )
                except Exception as exc:
                    logger.error("brain_finalize_error", error=str(exc))
            return reply
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
            return "Empty message."

        # Per-chat conversation
        chat_conv = self._get_chat_conversation(message.chat_id)
        conv_id = self._get_conversation_id(message.chat_id)

        # ── Layer 1: Multi-task detection → work queue ──
        # The detector intentionally ONLY fires on explicit work-queue
        # intent. Previously any message containing "1. ... 2. ..."
        # numbered lines was forwarded to the work loop, which meant
        # echoing the agent's own numbered recommendation back at it
        # (e.g. quoting "1. git pull, 2. pip install, 3. restart")
        # spawned 3 background jobs. We now require:
        #   (a) an explicit intent header line ending with ":", OR
        #   (b) a clean numbered list with no interleaved prose AND
        #       the items do not match a recent assistant reply (so
        #       quoted/echoed text is excluded).
        numbered = self._detect_explicit_work_queue(text, chat_conv)

        if len(numbered) >= 2 and self._work_loop:
            if message.is_group and not message.is_owner:
                return "The work queue is owner-only."
            added = self._work_loop.add_work(numbered, chat_id=int(message.chat_id))
            return (
                f"Got {added} tasks queued. I'll process them sequentially "
                "and post results as they finish."
            )

        # Store user message in memory
        from agent.memory.store import MemoryEntry, MemoryType
        await self._agent.memory.store(MemoryEntry(
            content=f"{message.sender_name} wrote to me: {text}",
            memory_type=MemoryType.EPISODIC,
            tags=["message", "user_input", message.channel_type],
            source=message.channel_type,
            importance=0.6,
        ))

        # ── Layer 1.5: Deterministic Telegram intent layer ──
        # Several common Telegram requests must NOT fall through to
        # the generic LLM/provider flow:
        #   • presence pings  ("are you there?", "ahoj")
        #   • version queries ("what version?", "aká je verzia?")
        #   • skills / capability / limits introspection
        #   • comparison vs. unknown external systems
        #   • memory horizon / memory usage
        #   • autonomy / complex-task introspection
        #   • self-update question / imperative
        #   • natural-language web open ("open obolo.tech")
        #   • weather-report scheduling intent
        #
        # All of these are handled deterministically with no provider
        # call. Crucially this layer runs BEFORE the short-followup
        # guard below so a follow-up "skills?" still gets caught even
        # in a chat with prior history.
        intent_reply = await self._try_deterministic_intent(message, text)
        if intent_reply is not None:
            return intent_reply

        # ── Layer 2: Internal dispatch (no LLM) ──
        short_followup = len(chat_conv) > 0 and len(text.split()) <= 8
        if not short_followup:
            from agent.brain.dispatcher import InternalDispatcher
            dispatcher = InternalDispatcher(self._agent)
            internal_result = await dispatcher.try_handle(text)
            if internal_result:
                return internal_result

        # ── Layer 3: Semantic cache ──
        # model.encode() is CPU-bound; run off the event loop.
        cached_response = await asyncio.to_thread(self._try_semantic_cache, text)
        if cached_response:
            logger.info("brain_cache_hit", query=text[:50])
            return cached_response

        # ── Layer 4: RAG retrieval ──
        # model.encode() is CPU-bound; run off the event loop.
        rag_context = ""
        rag_direct = await asyncio.to_thread(self._try_rag_retrieval, text)
        if rag_direct is not None:
            if rag_direct.get("action") == "direct":
                return f"From knowledge base ({rag_direct.get('source', '')}):\n{rag_direct.get('context', '')}"
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

        # ── Layer 5.1: Auto-route programming tasks to build pipeline ──
        # Programming tasks from any channel go through the build pipeline
        # (codegen → Docker sandbox → verify) instead of the raw LLM.
        # The raw LLM path either blocks (CLI + sandbox) or times out.
        if task_type == "programming":
            return await self._route_to_build_pipeline(text, message)

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
                from agent.core.models import resolve_runtime_model_alias

                override = resolve_runtime_model_alias(adaptation["model_override"])
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

        # ── Layer 4.5: Memory retrieval — inject relevant stored memories ──
        # Only inject memories that are epistemically trustworthy:
        #   provenance: OBSERVED, USER_ASSERTED, VERIFIED (NOT inferred/stale)
        #   kind: FACT, PROCEDURE (NOT belief/claim)
        # No blind fallback to latest memories — only keyword-matched results.
        memory_context = ""
        try:
            from agent.memory.store import MemoryKind, MemoryType, ProvenanceStatus

            _TRUSTED_PROVENANCE = {
                ProvenanceStatus.OBSERVED,
                ProvenanceStatus.USER_ASSERTED,
                ProvenanceStatus.VERIFIED,
            }
            _TRUSTED_KINDS = {MemoryKind.FACT, MemoryKind.PROCEDURE}

            keywords = [w for w in text.split() if len(w) > 3][:3]
            memory_results: list[Any] = []
            for kw in keywords:
                results = await self._agent.memory.query(
                    keyword=kw, memory_type=MemoryType.SEMANTIC, limit=5,
                )
                for entry in results:
                    if entry.provenance not in _TRUSTED_PROVENANCE:
                        continue
                    if entry.kind not in _TRUSTED_KINDS:
                        continue
                    if entry.content not in [m.content for m in memory_results]:
                        memory_results.append(entry)
            # No fallback to unrelated latest memories — if nothing matched, inject nothing.
            if memory_results:
                lines = []
                for m in memory_results[:5]:
                    prov = m.provenance.value if hasattr(m.provenance, "value") else str(m.provenance)
                    lines.append(f"- [{prov}] {m.content[:150]}")
                memory_context = "Stored memories (may be outdated — verify before stating as current fact):\n" + "\n".join(lines)
        except Exception:
            pass

        # Build prompt based on task type
        # Memory context block (shared across all prompt types)
        memory_block = f"\n{memory_context}\n" if memory_context else ""

        if task_type == "programming":
            sandbox_mode = os.environ.get("AGENT_SANDBOX_ONLY", "1") != "0"
            sandbox_instruction = (
                "\nIMPORTANT: You are running in sandbox mode (no host file access). "
                "Generate all code as text in your response. Do NOT try to create, "
                "edit, or read files. Present the complete implementation plan and "
                "code inline. The operator can then use /build to execute it in a "
                "Docker sandbox.\n"
            ) if sandbox_mode else ""
            prompt = (
                f"{active_prompt}\n"
                f"Si programátor.\n\n"
                f"{sandbox_instruction}"
                f"{memory_block}"
                f"ÚLOHA: {text}\n\n"
                f"At the end always include a short summary. {get_response_language_instruction()}"
            )
        elif task_type in ("simple", "factual", "greeting"):
            # Even short follow-ups like "ano", "ďakujem", "ok" need the
            # prior conversation in scope — otherwise the model has no
            # idea what the user is agreeing to and replies "chýba mi
            # kontext". Inject persistent_context (preferred, longer
            # window) or fall back to in-memory chat_conv tail.
            history_block = ""
            if persistent_context:
                history_block = f"{persistent_context}\n\n"
            elif conv_context:
                history_block = f"Predchádzajúca konverzácia:\n{conv_context}\n\n"
            prompt = (
                f"{get_simple_prompt()}\n"
                f"{memory_block}"
                f"{history_block}"
                f"{message.sender_name}: {text}\n"
            )
        else:
            prompt = f"{active_prompt}\n"
            if memory_block:
                prompt += memory_block
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

        # ── Layer 5.5: Runtime facts injection (anti-confabulation) ──
        # Inject verified runtime state into the prompt so the LLM has real data
        # even when it cannot call agent tools (e.g. CLI backend).
        runtime_facts = self._collect_runtime_facts()
        if runtime_facts:
            prompt += f"\n\nCurrent runtime state (verified, do not contradict):\n{runtime_facts}"

        # ── Layer 5.6: Single-shot mode prompt hint ──
        # When running on CLI+sandbox with tools blocked, tell the LLM
        # to answer directly instead of attempting tool calls that will
        # exhaust the turn budget.
        from agent.control.llm_runtime import resolve_llm_runtime_state
        runtime = resolve_llm_runtime_state(environ=os.environ)
        _backend = str(runtime["effective_backend"]) or "cli"
        _sandbox = os.environ.get("AGENT_SANDBOX_ONLY", "1").strip() != "0"
        if _backend == "cli" and _sandbox and task_type not in ("programming",):
            prompt += (
                "\n\nIMPORTANT: Answer directly in text. "
                "Do NOT use any tools (Read, Bash, Glob, etc.). "
                "Provide your best answer from your existing knowledge and the context above."
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
        # Reuse runtime state computed in Layer 5.6 above.
        backend = _backend
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

            from agent.core.error_normalize import normalize_user_error
            tool_text = normalize_user_error(loop_result.text or "")
            reply = tool_text or "Sorry, I couldn't reach the LLM provider."
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
            # CLI backend or no tools: direct generate.
            # For non-programming tasks (chat, analysis, factual) on the CLI
            # backend with sandbox, cap max_turns to 1. The LLM cannot use
            # tools in this mode so extra turns just burn time until
            # errormaxturns — the root cause of analytical question timeouts.
            effective_max_turns = model.max_turns
            sandbox_active = os.environ.get("AGENT_SANDBOX_ONLY", "1").strip() != "0"
            single_shot = (
                backend == "cli"
                and sandbox_active
                and task_type not in ("programming",)
            )
            if single_shot:
                effective_max_turns = 1

            # Channel enforcement: restricted channels never get file access
            response = await provider.generate(GenerateRequest(
                messages=[{"role": "user", "content": prompt}],
                model=model.model_id,
                timeout=min(model.timeout, 90) if single_shot else model.timeout,
                max_turns=effective_max_turns,
                allow_file_access=cli_allow_file_access,
                no_tools=single_shot,
                cwd=project_root,
            ))

            if not response.success:
                from agent.core.error_normalize import normalize_user_error

                friendly = normalize_user_error(response.error or "")
                return friendly or "Sorry, I couldn't reach the LLM provider."

            from agent.core.error_normalize import normalize_user_error
            normalized_text = normalize_user_error(response.text or "")
            reply = normalized_text or "Sorry, I couldn't reach the LLM provider."
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
                    from agent.core.models import ModelTier, get_model_for_tier

                    escalated_model = get_model_for_tier(ModelTier.BALANCED)
                    esc_response = await provider.generate(GenerateRequest(
                        messages=[{"role": "user", "content": prompt}],
                        model=escalated_model.model_id,
                        timeout=escalated_model.timeout,
                        max_turns=escalated_model.max_turns,
                        allow_file_access=cli_allow_file_access,
                        cwd=project_root,
                    ))
                    if esc_response.success and esc_response.text:
                        reply = esc_response.text
                        model = escalated_model
                        usage_cost += esc_response.cost_usd
                        usage_input_tokens += esc_response.input_tokens
                        usage_output_tokens += esc_response.output_tokens
                        self._total_cost_usd += esc_response.cost_usd
                        self._total_input_tokens += esc_response.input_tokens
                        self._total_output_tokens += esc_response.output_tokens
                        logger.info("post_routing_escalation_success", model=escalated_model.model_id)
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

        # ── Layer 8.5: Marketplace rescue ──
        # If the LLM made excuses about tools/access for a marketplace-related
        # question, replace its answer with a real deterministic handler call.
        # This catches the case where intent regex didn't fire but the question
        # was clearly about work/bids/marketplace activity.
        reply = await self._rescue_marketplace_excuse(text, reply)

        # NOTE: in-RAM buffer + persistent_conv save are now handled
        # by `_finalize_reply` which the top-level `process()` wrapper
        # invokes for every reply path (intents, dispatcher, cache,
        # RAG, work-queue, deny-guard, main LLM). Doing it here would
        # double-append.
        clean_reply = reply.split("\n\n_💰")[0] if "_💰" in reply else reply

        # Store in semantic cache (not for programming tasks)
        # store() calls model.encode(); run off the event loop.
        if self._semantic_cache and task_type not in ("programming",):
            try:
                await asyncio.to_thread(self._semantic_cache.store, text, clean_reply)
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
            reply = "This information is not available on this channel."
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

    def _schedule_graceful_restart(self) -> None:
        """Schedule a graceful self-restart on the running event loop.

        Called only after a successful self-update when (a) the
        operator has opted in via ``AGENT_SELF_RESTART_AFTER_UPDATE``
        and (b) ``run_self_update`` detected a process supervisor.
        The detection + opt-in gating happens in ``self_update.py`` —
        this helper just schedules the work.

        Sequence:
          1. Wait ``AGENT_SELF_RESTART_GRACE_S`` seconds (default 3)
             so the Telegram bot has time to deliver the reply that
             this method's caller is about to return.
          2. Stop the agent orchestrator (drains cron loops, queue,
             closes DBs cleanly).
          3. Flush stdout/stderr and call ``os._exit(0)``.
             The supervisor (systemd / supervisord / docker
             ``restart=always``) brings up a fresh process with the
             newly pulled code.

        We deliberately use ``os._exit`` instead of ``sys.exit`` so
        no further Python finalizers can run after the agent has
        already drained — they would race with the supervisor.

        On any exception during shutdown we still call ``os._exit(1)``
        because the supervisor will bring us back regardless.
        """
        import asyncio as _asyncio
        import sys as _sys

        try:
            loop = _asyncio.get_running_loop()
        except RuntimeError:
            logger.error("self_restart_no_event_loop")
            return

        async def _do_restart() -> None:
            try:
                grace_s = float(
                    os.environ.get("AGENT_SELF_RESTART_GRACE_S", "3"),
                )
            except (TypeError, ValueError):
                grace_s = 3.0
            grace_s = max(0.0, min(grace_s, 30.0))

            try:
                await _asyncio.sleep(grace_s)
                logger.warning(
                    "self_restart_initiated",
                    grace_s=grace_s,
                    hint=(
                        "Self-update completed, draining and exiting "
                        "so the supervisor can start a fresh process."
                    ),
                )
                try:
                    if hasattr(self._agent, "stop"):
                        await self._agent.stop()
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "self_restart_drain_failed",
                        error=str(exc),
                    )
                try:
                    _sys.stdout.flush()
                    _sys.stderr.flush()
                except Exception:
                    pass
                logger.warning("self_restart_exiting", code=0)
                os._exit(0)
            except Exception as exc:  # noqa: BLE001
                logger.error("self_restart_failed", error=str(exc))
                try:
                    _sys.stdout.flush()
                    _sys.stderr.flush()
                except Exception:
                    pass
                os._exit(1)

        # Schedule the shutdown task. We don't await it — the caller
        # needs to return so the Telegram bot can send the reply.
        task = loop.create_task(_do_restart())
        # Hold a strong reference so the task isn't GC'd before it runs.
        if not hasattr(self, "_pending_shutdown_tasks"):
            self._pending_shutdown_tasks: set[Any] = set()
        self._pending_shutdown_tasks.add(task)
        task.add_done_callback(self._pending_shutdown_tasks.discard)

    async def _route_to_build_pipeline(
        self, text: str, message: IncomingMessage,
    ) -> str:
        """Route a programming task to the build pipeline.

        Instead of sending the request to a raw LLM (which either blocks
        on CLI permission prompts or times out), we create a BuildIntake
        and run it through the full codegen → Docker → verify pipeline.
        """
        try:
            from agent.control.intake import OperatorIntake, OperatorWorkType

            repo_root = os.environ.get(
                "AGENT_PROJECT_ROOT",
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            )

            intake = OperatorIntake(
                repo_path=repo_root,
                work_type=OperatorWorkType.BUILD,
                description=text,
                requester=message.sender_name or "operator",
                context=f"Telegram auto-routed programming task from {message.sender_name}",
            )

            errors = intake.validate()
            if errors:
                logger.warning("build_intake_validation_failed", errors=errors)
                return (
                    "I understand this is a programming task, but I couldn't "
                    "set up the build pipeline:\n"
                    + "\n".join(f"  • {e}" for e in errors)
                )

            logger.info(
                "telegram_programming_auto_routed_to_build",
                description=text[:100],
                requester=message.sender_name,
            )

            result = await self._agent.submit_operator_intake(intake)

            status = result.get("status", "unknown")
            if status == "completed":
                job = result.get("job", {})
                docker = job.get("docker_result", {})
                test_passed = docker.get("test_passed", False)
                lint_passed = docker.get("lint_passed", False)
                files_written = docker.get("files_written", 0)
                cost = job.get("total_cost_usd", result.get("cost_usd", 0))

                summary_parts = [f"Build **{status}**"]
                if files_written:
                    summary_parts.append(f"{files_written} files written")
                summary_parts.append(
                    f"tests: {'PASS' if test_passed else 'FAIL'}"
                )
                summary_parts.append(
                    f"lint: {'PASS' if lint_passed else 'FAIL'}"
                )
                if cost:
                    summary_parts.append(f"cost: ${cost:.4f}")

                reply = " | ".join(summary_parts)

                # Include test output if failed
                test_output = docker.get("test_output", "")
                if not test_passed and test_output:
                    reply += f"\n\nTest output:\n```\n{test_output[:1000]}\n```"

                return reply

            if status in ("blocked", "awaiting_approval"):
                error = result.get("error", "")
                return f"Build {status}: {error}" if error else f"Build {status}."

            # Submitted but not yet completed — async job
            job_id = result.get("plan_record", {}).get("plan_id", "")
            return (
                f"Build submitted (job: `{job_id[:16]}`).\n"
                "I'll work on it in the background. Check status with `/jobs`."
            )

        except Exception as exc:
            logger.exception("build_pipeline_auto_route_failed", error=str(exc))
            return (
                f"I tried to route this to the build pipeline but hit an error:\n"
                f"`{type(exc).__name__}: {exc!s:.200}`\n\n"
                f"You can try manually: `/build . --description \"{text[:100]}\"`"
            )

    async def _try_deterministic_intent(
        self, message: IncomingMessage, text: str,
    ) -> str | None:
        """Layer 1.5: deterministic Telegram intent dispatch.

        Returns a final reply string or ``None`` if the message did
        not match any deterministic intent. This layer runs BEFORE the
        generic dispatcher and BEFORE the short-followup skip, so
        intents are always caught regardless of conversation length.

        Owner-only intents (self-update imperative, weather setup) are
        denied for non-owner / group context.
        """
        from agent.brain import telegram_intents

        match = telegram_intents.detect_intent(text)
        if match is None:
            return None

        intent = match.intent
        payload = match.payload

        try:
            if intent == telegram_intents.PRESENCE:
                return await telegram_intents.handle_presence()

            if intent == telegram_intents.VERSION:
                return telegram_intents.handle_version()

            if intent == telegram_intents.SKILLS:
                return telegram_intents.handle_skills()

            if intent == telegram_intents.CAPABILITY:
                return telegram_intents.handle_capability()

            if intent == telegram_intents.LIMITS:
                return telegram_intents.handle_limits()

            if intent == telegram_intents.SELF_DESCRIPTION:
                return telegram_intents.handle_self_description(self._agent)

            if intent == telegram_intents.COMPARISON:
                subject = str(payload.get("subject", ""))
                return telegram_intents.handle_comparison(subject, self._agent)

            if intent == telegram_intents.MEMORY_USAGE:
                return telegram_intents.handle_memory_usage(self._agent)

            if intent == telegram_intents.MEMORY_LIST:
                return await telegram_intents.handle_memory_list(self._agent)

            if intent == telegram_intents.CONTEXT_RECALL:
                chat_conv = self._get_chat_conversation(message.chat_id)
                return telegram_intents.handle_context_recall(chat_conv)

            if intent == telegram_intents.MEMORY_HORIZON:
                # The handler reads the live tail size from this
                # brain instance via a tiny shim attribute on the
                # agent — no global state.
                try:
                    self._agent._brain = self  # type: ignore[attr-defined]
                except Exception:
                    pass
                return telegram_intents.handle_memory_horizon(self._agent)

            if intent == telegram_intents.AUTONOMY:
                return telegram_intents.handle_autonomy(self._agent)

            if intent == telegram_intents.COMPLEX_TASK:
                return telegram_intents.handle_complex_task(self._agent)

            if intent == telegram_intents.PROJECT_INVENTORY:
                return await telegram_intents.handle_project_inventory(self._agent)

            if intent == telegram_intents.WORKFLOW_INVENTORY:
                return telegram_intents.handle_workflow_inventory(self._agent)

            if intent == telegram_intents.MEDIUM_REASONING:
                return telegram_intents.handle_medium_reasoning(self._agent)

            if intent == telegram_intents.PROJECT_STATUS:
                return await telegram_intents.handle_project_status(self._agent)

            if intent == telegram_intents.RECURRING_CAPABILITY:
                return telegram_intents.handle_recurring_capability()

            if intent == telegram_intents.WEB_MONITOR_CAPABILITY:
                return telegram_intents.handle_web_monitor_capability()

            if intent == telegram_intents.REVIEW_REQUEST:
                return telegram_intents.handle_review_request()

            if intent == telegram_intents.REPO_VERIFICATION:
                return telegram_intents.handle_repo_verification()

            if intent == telegram_intents.PROJECT_DECOMPOSITION:
                return telegram_intents.handle_project_decomposition(self._agent)

            if intent == telegram_intents.WEB_ACCESS_CAPABILITY:
                return telegram_intents.handle_web_access_capability()

            if intent == telegram_intents.WORK_STATUS:
                return await telegram_intents.handle_work_status(self._agent)

            if intent == telegram_intents.WORK_SEARCH:
                return await telegram_intents.handle_work_search(self._agent)

            if intent == telegram_intents.SELF_UPDATE_QUESTION:
                return telegram_intents.handle_self_update_question()

            if intent == telegram_intents.SELF_UPDATE_IMPERATIVE:
                # Owner-only.
                if message.is_group or not message.is_owner:
                    return (
                        "Self-update is owner-only and cannot be triggered "
                        "from a group chat."
                    )
                from agent.core.self_update import run_self_update

                repo_root = os.environ.get(
                    "AGENT_PROJECT_ROOT",
                    str(self._agent._data_dir.parent)
                    if hasattr(self._agent, "_data_dir") else "",
                )
                result = await run_self_update(
                    repo_root=repo_root,
                    is_owner=message.is_owner,
                    is_group=message.is_group,
                )
                logger.info(
                    "self_update_completed",
                    status=result.status,
                    branch=result.branch,
                    fetched=result.fetched_commits,
                    will_self_restart=result.should_self_restart,
                )
                # Schedule the graceful restart AFTER we return so the
                # Telegram bot has a chance to send the reply first.
                # The shutdown task waits a few seconds (configurable),
                # drains the orchestrator, and calls os._exit(0) so
                # the supervisor brings up a fresh process.
                if result.should_self_restart:
                    self._schedule_graceful_restart()
                return result.message

            if intent == telegram_intents.WEB_OPEN:
                url = str(payload.get("url", "")).strip()
                if not url:
                    return "Open which page? Give me a URL or domain."
                return await telegram_intents.handle_web_open(url, self._agent)

            if intent == telegram_intents.WEATHER_REPORT_SETUP:
                if message.is_group and not message.is_owner:
                    return (
                        "Recurring weather reports are owner-only and "
                        "cannot be set up from a group chat."
                    )
                city = str(payload.get("city", ""))
                return telegram_intents.handle_weather_report_setup(
                    city, self._agent,
                )
        except Exception as exc:
            logger.error(
                "deterministic_intent_handler_error",
                intent=intent,
                error=str(exc),
            )
            return None

        return None

    def _try_semantic_cache(self, text: str) -> str | None:
        """Layer 3: Lookup semantic cache. Returns cached response or None."""
        try:
            if self._semantic_cache is None:
                from agent.memory.semantic_cache import SemanticCache
                self._semantic_cache = SemanticCache()
            return cast("str | None", self._semantic_cache.lookup(text))
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
                return cast("dict[str, Any] | None", result)
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

    # ── Marketplace rescue keywords ──
    _MARKETPLACE_QUESTION_SIGNALS = frozenset({
        "prácu", "pracu", "prác", "prac", "job", "jobs", "bid", "bids",
        "obolos", "marketplace", "listing", "ponuk", "ponúk",
        "prihlás", "prihlas", "zarob", "zarábať", "zarabat",
        "work", "gig", "earning",
    })
    _EXCUSE_SIGNALS = frozenset({
        "nemôžem", "nemozem", "nedokážem", "nedokazem",
        "nemám prístup", "nemam pristup", "bez prístupu",
        "tool execution", "tools", "nie som schopný",
        "can't", "cannot", "unable", "no access",
        "vypnutý", "vypnuty", "disabled",
    })

    async def _rescue_marketplace_excuse(self, question: str, reply: str) -> str:
        """Replace LLM excuses with real marketplace data for work-related questions.

        If the LLM said "I can't" for a marketplace question, we know that's
        wrong — /marketplace commands work in sandbox mode. Replace the excuse
        with a deterministic handler call.
        """
        q_lower = question.lower()
        r_lower = reply.lower()

        # Is the question about work/marketplace?
        q_relevant = any(sig in q_lower for sig in self._MARKETPLACE_QUESTION_SIGNALS)
        if not q_relevant:
            return reply

        # Is the reply an excuse?
        r_excuse = any(sig in r_lower for sig in self._EXCUSE_SIGNALS)
        if not r_excuse:
            return reply

        logger.warning("marketplace_excuse_rescued",
                       question=question[:80], excuse_snippet=reply[:100])

        # Decide: status question or search question?
        from agent.brain import telegram_intents
        status_signals = {"prihlás", "prihlas", "bidoval", "stav", "status", "did you", "have you"}
        is_status = any(sig in q_lower for sig in status_signals)

        try:
            if is_status:
                return await telegram_intents.handle_work_status(self._agent)
            return await telegram_intents.handle_work_search(self._agent)
        except Exception:
            logger.exception("marketplace_rescue_handler_failed")
            return reply

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

    # Explicit work-queue intent headers. The detector requires either
    # one of these on the first non-empty line, OR a clean numbered
    # list with no interleaved prose AND no overlap with a recent
    # assistant reply (anti-echo guard).
    _WORK_INTENT_HEADERS: ClassVar[frozenset[str]] = frozenset({
        # SK
        "urob", "urobím", "vykonaj", "spusti", "spravme", "uloh", "úloh",
        "úlohy", "uloha", "úloha", "todo", "kroky",
        # EN
        "do", "tasks", "task", "make", "execute", "run", "steps",
    })

    def _detect_explicit_work_queue(
        self, text: str, chat_conv: list[dict[str, str]],
    ) -> list[str]:
        """Return work-queue items only when the user explicitly asked
        for a multi-task pipeline. See the call site for the bug we
        are guarding against (echoed numbered lists running 3 jobs).

        Rules:
            1. No quoted-block markers (``>``, ``» ``, ``« ``) — these
               indicate the user is quoting somebody (typically the
               agent itself).
            2. EITHER the first non-empty line is a short intent header
               that ends with ``:`` and contains a work-intent verb,
               OR every non-empty line in the message is a numbered
               item (a "clean" list with no surrounding prose).
            3. At least 2 numbered items must result.
            4. Anti-echo: if every numbered item also appears verbatim
               in the most recent assistant reply, the user is most
               likely quoting the agent — return nothing.
            5. Comma-list shortcut: ``urob: a, b, c`` still works for
               operators who type a single-line task list.
        """
        import re

        # Rule 1: bail on quoted blocks.
        if any(line.lstrip().startswith((">", "» ", "« "))
               for line in text.splitlines()):
            return []

        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if not lines:
            return []

        numbered_re = re.compile(r"^\d+[\.\)]\s*")
        numbered_lines = [
            numbered_re.sub("", line) for line in lines if numbered_re.match(line)
        ]

        first_line_lower = lines[0].lower()
        first_words = first_line_lower.split()
        first_word_raw = first_words[0] if first_words else ""
        first_word = first_word_raw.rstrip(":,")

        # Header line: short imperative ending with ":" (e.g. "urob:")
        # OR a header without colon when followed by numbered lines
        # (e.g. "urob\n1. ...\n2. ...").
        header_with_colon = (
            ":" in lines[0]
            and first_word in self._WORK_INTENT_HEADERS
            and len(first_words) <= 4
        )
        header_without_colon = (
            len(first_words) == 1
            and first_word in self._WORK_INTENT_HEADERS
            and len(numbered_lines) >= 2
        )
        has_intent_header = header_with_colon or header_without_colon
        clean_numbered_list = (
            len(numbered_lines) >= 2 and len(numbered_lines) == len(lines)
        )

        items: list[str] = []
        if has_intent_header and numbered_lines:
            items = numbered_lines
        elif clean_numbered_list:
            items = numbered_lines
        elif header_with_colon and "," in lines[0] and len(lines) == 1:
            # Single-line "urob: a, b, c" shortcut.
            rest = lines[0].split(":", 1)[1].strip()
            items = [s.strip() for s in rest.split(",") if s.strip()]
        elif (
            first_word in self._WORK_INTENT_HEADERS
            and "," in lines[0]
            and len(lines) == 1
        ):
            # Legacy "urob a, b, c" without colon — still allowed.
            rest = lines[0][len(first_word_raw):].strip().lstrip(":,").strip()
            items = [s.strip() for s in rest.split(",") if s.strip()]

        if len(items) < 2:
            return []

        # Rule 4: anti-echo. Check the last assistant reply (if any).
        last_assistant = ""
        for entry in reversed(chat_conv):
            if entry.get("role") == "assistant":
                last_assistant = entry.get("content", "")
                break
        if last_assistant:
            normalized_assistant = last_assistant.lower()
            overlapping = sum(
                1 for it in items
                if it.lower().strip(".") in normalized_assistant
            )
            # If most items are quoted from the agent, treat as echo.
            if overlapping >= max(2, len(items) - 1):
                logger.info(
                    "work_queue_echo_suppressed",
                    items_total=len(items),
                    items_overlapping=overlapping,
                )
                return []

        return items

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

    async def _ensure_persistent_conv(self) -> Any:
        """Lazy-init the per-chat SQLite conversation store, then cache it.

        We do this once and reuse — both ``_get_persistent_context`` and
        ``_finalize_reply`` (and the hydration helper) need a live
        instance, and re-running ``initialize()`` on every call would
        be wasteful.
        """
        if self._persistent_conv is None:
            try:
                from agent.memory.persistent_conversation import PersistentConversation
                self._persistent_conv = PersistentConversation(
                    db_path=str(self._agent._data_dir / "memory" / "conversations.db"),
                )
                await self._persistent_conv.initialize()
            except Exception as exc:
                logger.error("persistent_conv_init_error", error=str(exc))
                return None
        return self._persistent_conv

    async def _get_persistent_context(self, conv_id: str, query: str) -> str:
        try:
            pc = await self._ensure_persistent_conv()
            if pc is None:
                return ""
            return cast("str", await pc.build_context(conv_id, query=query))
        except Exception as e:
            logger.error("persistent_conv_error", error=str(e))
            return ""

    async def _save_exchange(
        self, conv_id: str, text: str, reply: str, sender: str
    ) -> None:
        try:
            pc = await self._ensure_persistent_conv()
            if pc is not None:
                await pc.save_exchange(
                    conv_id, text, reply[:500], sender=sender,
                )
        except Exception as e:
            logger.error("persistent_save_error", error=str(e))

    async def _hydrate_chat_conv_if_needed(self, chat_id: str) -> None:
        """Hydrate the in-RAM conversation tail from the SQLite store.

        Runs at most once per chat per process lifetime. Without this,
        the very first message after a process restart loses all
        conversational context — chat_conv is empty until the first
        new exchange writes back to it.
        """
        if chat_id in self._hydrated_chats:
            return
        # Mark immediately so a slow / failing hydrate cannot retry
        # forever and block subsequent messages.
        self._hydrated_chats.add(chat_id)

        existing = self._conversations.get(chat_id, [])
        if existing:
            # Already populated this process lifetime — nothing to do.
            return

        try:
            pc = await self._ensure_persistent_conv()
            if pc is None:
                return
            conv_id = self._get_conversation_id(chat_id)
            # Use the public retrieval helper that respects the same
            # max_raw bound the SQL store enforces, so we never load
            # more than the configured number of recent exchanges.
            recent = await pc._get_recent_messages(conv_id)  # noqa: SLF001
        except Exception as exc:
            logger.warning("chat_conv_hydrate_failed", chat_id=chat_id, error=str(exc))
            return

        if not recent:
            return

        identity = get_agent_identity()
        owner_name = identity.owner_name
        agent_name = identity.agent_name
        buf = self._get_chat_conversation(chat_id)
        for sender, content in recent:
            role = "assistant" if sender == agent_name else "user"
            entry: dict[str, str] = {"role": role, "content": str(content)[:300]}
            if role == "user":
                entry["sender"] = sender or owner_name
            buf.append(entry)
        # Bound the hydrated tail to the configured max.
        while len(buf) > self._max_conversation:
            buf.pop(0)
        logger.info(
            "chat_conv_hydrated",
            chat_id=chat_id,
            entries=len(buf),
        )

    async def _finalize_reply(
        self,
        *,
        message: IncomingMessage,
        text: str,
        reply: str,
        chat_conv: list[dict[str, str]],
        conv_id: str,
    ) -> None:
        """Persist a finished exchange to the in-RAM tail and SQLite.

        Idempotent: if the last entries already match this exchange
        the helper is a no-op. This is what lets us call it from the
        top-level ``process()`` wrapper without conflicting with the
        per-path appends that the main LLM path used to do internally.

        Strips the cost/usage banner before storing so the persisted
        history doesn't contain the operator-only meter.
        """
        if not reply:
            return
        clean_reply = reply.split("\n\n_💰")[0] if "_💰" in reply else reply
        clean_reply = clean_reply.strip()
        if not clean_reply:
            return

        # 1. Idempotent in-RAM tail update.
        last = chat_conv[-1] if chat_conv else None
        already_recorded = (
            last is not None
            and last.get("role") == "assistant"
            and last.get("content") == clean_reply[:300]
        )
        if not already_recorded:
            # Append the user message if not already there as the
            # most-recent user entry.
            user_already_there = False
            for entry in reversed(chat_conv):
                if entry.get("role") == "user":
                    user_already_there = entry.get("content") == text
                    break
                if entry.get("role") == "assistant":
                    break
            if not user_already_there:
                chat_conv.append({
                    "role": "user",
                    "content": text,
                    "sender": message.sender_name,
                })
            chat_conv.append({"role": "assistant", "content": clean_reply[:300]})
            while len(chat_conv) > self._max_conversation:
                chat_conv.pop(0)

        # 2. Persist to SQLite (idempotent at the application level —
        # save_exchange always inserts a row, but the SQL store is the
        # single source of truth so we only call it once per process()
        # invocation via the wrapper).
        await self._save_exchange(conv_id, text, clean_reply, message.sender_name)

    def _collect_runtime_facts(self) -> str:
        """Collect verified runtime facts for anti-confabulation injection.

        Returns a compact string of current agent state. Injected into the LLM
        prompt so responses about tasks/budget/health use real data instead of
        confabulated answers.
        """
        facts: list[str] = []
        try:
            task_stats = self._agent.tasks.get_stats()
            facts.append(
                f"- Tasks: {task_stats['total_tasks']} total"
                + (f" ({', '.join(f'{s}: {c}' for s, c in task_stats['by_status'].items())})"
                   if task_stats.get("by_status") else "")
            )
        except Exception:
            pass
        try:
            mem_stats = self._agent.memory.get_stats()
            facts.append(f"- Memories: {mem_stats['total_memories']}")
        except Exception:
            pass
        try:
            health = self._agent.watchdog.get_system_health()
            facts.append(
                f"- System: CPU {health.cpu_percent:.0f}%, "
                f"RAM {health.memory_percent:.0f}%, "
                f"Disk {health.disk_percent:.0f}%"
            )
            if health.alerts:
                facts.append(f"- Alerts: {', '.join(health.alerts)}")
        except Exception:
            pass
        try:
            job_stats = self._agent.get_status().get("jobs", {})
            if job_stats:
                facts.append(
                    f"- Jobs: {job_stats.get('total_completed', 0)} completed, "
                    f"{job_stats.get('total_failed', 0)} failed"
                )
        except Exception:
            pass
        try:
            finance = self._agent.finance.get_stats()
            facts.append(
                f"- Budget: income ${finance['total_income']:.2f}, "
                f"expenses ${finance['total_expenses']:.2f}"
            )
        except Exception:
            pass
        return "\n".join(facts)

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
