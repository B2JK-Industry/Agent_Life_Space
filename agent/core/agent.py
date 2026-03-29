"""
Agent Life Space — Agent Orchestrator

The main agent that ties all modules together.
Manages the complete lifecycle: startup → run → shutdown.

Architecture:
    ┌──────────────────────────────────────┐
    │         AgentOrchestrator            │
    │                                      │
    │  ┌──────────┐  ┌─────────────────┐  │
    │  │ Watchdog  │  │ Decision Engine │  │
    │  └─────┬────┘  └───────┬─────────┘  │
    │        │               │             │
    │  ┌─────┴───────────────┴──────────┐  │
    │  │        Message Router          │  │
    │  └─┬───┬───┬───┬───┬───┬───┬───┬─┘  │
    │    │   │   │   │   │   │   │   │     │
    │  Brain Mem Task Work Proj Soc Fin Log│
    │                                      │
    │  ┌──────────┐  ┌────────────────┐   │
    │  │LLM Router│  │  Job Runner    │   │
    │  └──────────┘  └────────────────┘   │
    └──────────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import structlog

from agent.brain.decision_engine import DecisionEngine
from agent.control.acquisition import RepoAcquisitionService
from agent.control.artifact_queries import ArtifactQueryService
from agent.control.denials import make_denial
from agent.control.evidence_export import EvidenceExportService
from agent.control.gateway import ExternalGatewayService
from agent.control.intake import OperatorIntake, OperatorIntakeService, OperatorWorkType
from agent.control.job_queries import JobQueryService
from agent.control.models import JobKind, PlanRecordStatus, TraceRecordKind
from agent.control.policy import evaluate_release_readiness
from agent.control.reporting import OperatorReportService
from agent.control.runtime_model import RuntimeModelService
from agent.control.state import ControlPlaneStateService
from agent.control.storage import ControlPlaneStorage
from agent.control.workspace_queries import WorkspaceQueryService
from agent.core.approval import ApprovalCategory, ApprovalQueue
from agent.core.approval_storage import ApprovalStorage
from agent.core.identity import get_identity_onboarding_warnings
from agent.core.job_runner import JobConfig, JobRunner
from agent.core.llm_router import LLMRouter
from agent.core.messages import Message, MessageType, ModuleID
from agent.core.operator import OperatorControls
from agent.core.router import MessageRouter
from agent.core.watchdog import Watchdog
from agent.finance.tracker import FinanceTracker
from agent.memory.store import MemoryEntry, MemoryStore, MemoryType
from agent.projects.manager import ProjectManager
from agent.review.quality import ReviewQualityService
from agent.tasks.manager import TaskManager
from agent.work.workspace import WorkspaceManager

logger = structlog.get_logger(__name__)


class AgentOrchestrator:
    """
    Main agent process. Initializes all modules, starts the event loop,
    and manages graceful shutdown.
    """

    def __init__(
        self,
        data_dir: str = "agent",
        watchdog_interval: float = 10.0,
    ) -> None:
        if not data_dir:
            msg = "data_dir cannot be empty"
            raise ValueError(msg)
        if watchdog_interval <= 0:
            msg = f"watchdog_interval must be > 0, got {watchdog_interval}"
            raise ValueError(msg)

        self._data_dir = Path(data_dir)
        self._running = False
        self._initialized = False

        # Core infrastructure
        self.router = MessageRouter()  # FUTURE: inter-module messaging
        self.watchdog = Watchdog(check_interval=watchdog_interval)
        self.job_runner = JobRunner(max_concurrent=4)
        self.llm_router = LLMRouter()  # FUTURE: direct API calls (now using Claude CLI)

        # Agent modules
        self.brain = DecisionEngine()
        self.memory = MemoryStore(
            db_path=str(self._data_dir / "memory" / "memories.db")
        )
        self.tasks = TaskManager(
            db_path=str(self._data_dir / "tasks" / "tasks.db")
        )
        # Governance
        self.approval_queue = ApprovalQueue(
            storage=ApprovalStorage(
                db_path=str(self._data_dir / "approval" / "approvals.db")
            )
        )
        self.operator_controls = OperatorControls()

        self.finance = FinanceTracker(
            db_path=str(self._data_dir / "finance" / "finance.db"),
            approval_queue=self.approval_queue,
        )
        self.projects = ProjectManager(
            db_path=str(self._data_dir / "projects" / "projects.db")
        )
        self.workspaces = WorkspaceManager()
        self.agent_loop: Any = None
        self._secrets_manager: Any = None
        self._secrets_lookup_disabled = False
        self.control_plane = ControlPlaneStateService(
            storage=ControlPlaneStorage(
                db_path=str(self._data_dir / "control" / "control.db")
            )
        )

        # Review service
        from agent.review.service import ReviewService
        from agent.review.storage import ReviewStorage
        self.review = ReviewService(
            storage=ReviewStorage(
                db_path=str(self._data_dir / "review" / "reviews.db")
            ),
            workspace_manager=self.workspaces,
            approval_queue=self.approval_queue,
            control_plane_state=self.control_plane,
        )

        # Build service
        from agent.build.service import BuildService
        from agent.build.storage import BuildStorage
        self.build = BuildService(
            storage=BuildStorage(
                db_path=str(self._data_dir / "build" / "builds.db")
            ),
            workspace_manager=self.workspaces,
            review_service=self.review,
            approval_queue=self.approval_queue,
            control_plane_state=self.control_plane,
        )
        self.jobs = JobQueryService(
            build_service=self.build,
            review_service=self.review,
            task_manager=self.tasks,
            job_runner=self.job_runner,
            agent_loop_provider=lambda: self.agent_loop,
        )
        self.artifacts = ArtifactQueryService(
            build_service=self.build,
            review_service=self.review,
            control_plane_state=self.control_plane,
        )
        self.intake_router = OperatorIntakeService(
            budget_status_provider=self.finance.check_budget,
        )
        self.runtime_model = RuntimeModelService()
        self.repo_acquisition = RepoAcquisitionService(
            root_path=str(self._data_dir / "control" / "acquired_repos")
        )
        self.workspace_queries = WorkspaceQueryService(
            workspace_manager=self.workspaces,
            build_service=self.build,
            review_service=self.review,
            approval_queue=self.approval_queue,
            control_plane_state=self.control_plane,
        )
        self.gateway = ExternalGatewayService(
            control_plane_state=self.control_plane,
            approval_queue=self.approval_queue,
            environment=os.environ,
            secret_lookup=self._lookup_secret,
        )
        self.reporting = OperatorReportService(
            job_queries=self.jobs,
            artifact_queries=self.artifacts,
            approval_queue=self.approval_queue,
            operator_controls=self.operator_controls,
            status_provider=self.get_status,
            control_plane_state=self.control_plane,
            workspace_queries=self.workspace_queries,
            gateway_service=self.gateway,
        )
        self.review_quality = ReviewQualityService(
            control_plane_state=self.control_plane,
        )
        self.evidence_exports = EvidenceExportService(
            job_queries=self.jobs,
            artifact_queries=self.artifacts,
            control_plane_state=self.control_plane,
            review_service=self.review,
            workspace_queries=self.workspace_queries,
            approval_queue=self.approval_queue,
            runtime_model=self.runtime_model,
        )

        # Background tasks
        self._background_tasks: list[asyncio.Task[Any]] = []

    async def initialize(self) -> None:
        """Initialize all modules and register handlers."""
        if self._initialized:
            logger.warning("agent_already_initialized")
            return
        logger.info("agent_initializing")

        # Ensure data directories exist
        (self._data_dir / "memory").mkdir(parents=True, exist_ok=True)
        (self._data_dir / "tasks").mkdir(parents=True, exist_ok=True)
        (self._data_dir / "finance").mkdir(parents=True, exist_ok=True)
        (self._data_dir / "projects").mkdir(parents=True, exist_ok=True)
        (self._data_dir / "logs").mkdir(parents=True, exist_ok=True)
        (self._data_dir / "approval").mkdir(parents=True, exist_ok=True)
        (self._data_dir / "build").mkdir(parents=True, exist_ok=True)
        (self._data_dir / "control").mkdir(parents=True, exist_ok=True)
        (self._data_dir / "review").mkdir(parents=True, exist_ok=True)

        # Initialize persistent stores
        await self.memory.initialize()
        await self.tasks.initialize()
        await self.finance.initialize()
        await self.projects.initialize()
        self.workspaces.initialize()
        self.build.initialize()
        self.review.initialize()
        self.control_plane.initialize()

        # Register message handlers
        self.router.register_handler(ModuleID.BRAIN, self._handle_brain_message)
        self.router.register_handler(ModuleID.MEMORY, self._handle_memory_message)
        self.router.register_handler(ModuleID.TASKS, self._handle_tasks_message)
        self.router.register_handler(ModuleID.LLM_ROUTER, self._handle_llm_message)
        self.router.register_handler(ModuleID.LOGS, self._handle_log_message)
        self.router.register_handler(ModuleID.WATCHDOG, self._handle_watchdog_message)

        # Register modules with watchdog
        self.watchdog.register_module("brain", heartbeat_timeout=60.0)
        self.watchdog.register_module("memory", heartbeat_timeout=60.0)
        self.watchdog.register_module("tasks", heartbeat_timeout=60.0)
        self.watchdog.register_module("llm_router", heartbeat_timeout=120.0)
        self.watchdog.register_module("job_runner", heartbeat_timeout=60.0)

        # Register built-in job types
        self.job_runner.register_job_type("memory_decay", self._job_memory_decay)
        self.job_runner.register_job_type("health_check", self._job_health_check)
        self.job_runner.register_job_type("process_next_task", self._job_process_next_task)

        # Store startup memory
        await self.memory.store(
            MemoryEntry(
                content="Agent started successfully. All modules initialized.",
                memory_type=MemoryType.EPISODIC,
                tags=["system", "startup"],
                source="orchestrator",
                importance=0.3,
            )
        )

        self._initialized = True
        logger.info(
            "agent_initialized",
            modules=["brain", "memory", "tasks", "llm_router", "watchdog", "job_runner"],
        )

    async def start(self) -> None:
        """Start the agent event loop."""
        if self._running:
            logger.warning("agent_already_running")
            return

        self._running = True
        logger.info("agent_starting")

        # Start background services
        self._background_tasks.append(
            asyncio.create_task(self.router.start())
        )
        self._background_tasks.append(
            asyncio.create_task(self.watchdog.start())
        )
        self._background_tasks.append(
            asyncio.create_task(self._heartbeat_loop())
        )
        self._background_tasks.append(
            asyncio.create_task(self._maintenance_loop())
        )

        logger.info("agent_running")

        # Keep running until stopped
        try:
            while self._running:
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Gracefully shut down all modules."""
        logger.info("agent_stopping")
        self._running = False

        # Stop background tasks
        for task in self._background_tasks:
            task.cancel()

        await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

        # Stop services
        await self.router.stop()
        await self.watchdog.stop()

        # Close persistent stores
        await self.memory.close()
        await self.tasks.close()
        await self.finance.close()
        await self.projects.close()

        # Store shutdown memory (in a new connection since we closed)
        logger.info("agent_stopped")

    # --- Message Handlers ---

    async def _handle_brain_message(self, message: Message) -> Message | None:
        """Handle messages directed to the brain module."""
        self.watchdog.heartbeat("brain")

        if message.msg_type == MessageType.DECISION_REQUEST:
            task_desc = message.payload.get("task_description", "")
            decision = self.brain.should_use_llm(task_desc)
            return message.create_response(
                payload={
                    "action": decision.action,
                    "method": decision.method.value,
                    "confidence": decision.confidence,
                    "reasoning": decision.reasoning,
                },
                msg_type=MessageType.DECISION_RESULT,
            )

        return None

    async def _handle_memory_message(self, message: Message) -> Message | None:
        """Handle memory operations."""
        self.watchdog.heartbeat("memory")

        if message.msg_type == MessageType.MEMORY_STORE:
            entry = MemoryEntry(
                content=message.payload.get("content", ""),
                memory_type=MemoryType(message.payload.get("type", "semantic")),
                tags=message.payload.get("tags", []),
                source=message.source.value,
                importance=message.payload.get("importance", 0.5),
            )
            mem_id = await self.memory.store(entry)
            return message.create_response(
                payload={"memory_id": mem_id, "status": "stored"},
                msg_type=MessageType.MEMORY_RESULT,
            )

        elif message.msg_type == MessageType.MEMORY_QUERY:
            results = await self.memory.query(
                tags=message.payload.get("tags"),
                keyword=message.payload.get("keyword"),
                limit=message.payload.get("limit", 5),
            )
            return message.create_response(
                payload={
                    "results": [r.to_dict() for r in results],
                    "count": len(results),
                },
                msg_type=MessageType.MEMORY_RESULT,
            )

        return None

    async def _handle_tasks_message(self, message: Message) -> Message | None:
        """Handle task operations."""
        self.watchdog.heartbeat("tasks")

        if message.msg_type == MessageType.TASK_CREATE:
            task = await self.tasks.create_task(
                name=message.payload.get("name", "Unnamed"),
                description=message.payload.get("description", ""),
                priority=message.payload.get("priority", 0.5),
                importance=message.payload.get("importance", 0.5),
                urgency=message.payload.get("urgency", 0.5),
                tags=message.payload.get("tags", []),
            )
            return message.create_response(
                payload={"task_id": task.id, "status": task.status.value},
            )

        elif message.msg_type == MessageType.TASK_COMPLETE:
            task_id = message.payload.get("task_id", "")
            result = message.payload.get("result")
            task = await self.tasks.complete_task(task_id, result)
            return message.create_response(
                payload={"task_id": task.id, "status": task.status.value},
            )

        return None

    async def _handle_llm_message(self, message: Message) -> Message | None:
        """Handle LLM requests."""
        self.watchdog.heartbeat("llm_router")
        # LLM calls go through the LLM Router with full validation
        # Actual API calls happen here
        return message.create_response(
            payload={"status": "llm_request_received"},
            msg_type=MessageType.LLM_RESPONSE,
        )

    async def _handle_log_message(self, message: Message) -> Message | None:
        """Handle log messages."""
        logger.info(
            "agent_log",
            source=message.source.value,
            log_type=message.payload.get("type", "info"),
            log_message=message.payload.get("message", ""),
        )
        return None

    async def _handle_watchdog_message(self, message: Message) -> Message | None:
        """Handle watchdog messages."""
        if message.msg_type == MessageType.HEALTH_CHECK:
            health = self.watchdog.get_system_health()
            return message.create_response(
                payload={
                    "cpu": health.cpu_percent,
                    "memory": health.memory_percent,
                    "modules": health.modules,
                    "alerts": health.alerts,
                },
                msg_type=MessageType.HEALTH_REPORT,
            )
        return None

    # --- Background Jobs ---

    async def _job_memory_decay(self) -> dict[str, Any]:
        """Periodic memory decay job."""
        deleted = await self.memory.apply_decay(decay_rate=0.005)
        stats = self.memory.get_stats()
        return {
            "deleted_memories": deleted,
            "total_memories": stats["total_memories"],
        }

    async def _job_health_check(self) -> dict[str, Any]:
        """Periodic health check job."""
        health = self.watchdog.get_system_health()
        return {
            "cpu": health.cpu_percent,
            "memory": health.memory_percent,
            "modules": health.modules,
            "alerts": health.alerts,
        }

    async def _job_process_next_task(self) -> dict[str, Any]:
        """Process the next queued task."""
        next_task = self.tasks.get_next_task()
        if next_task is None:
            return {"status": "no_tasks"}

        # Start the task
        await self.tasks.start_task(next_task.id)

        # Decide how to process
        decision = self.brain.should_use_llm(
            f"{next_task.name}: {next_task.description}"
        )

        return {
            "task_id": next_task.id,
            "task_name": next_task.name,
            "decision": decision.action,
            "method": decision.method.value,
        }

    # --- Background Loops ---

    async def _heartbeat_loop(self) -> None:
        """Send heartbeats from all modules periodically."""
        while self._running:
            for name in ["brain", "memory", "tasks", "llm_router", "job_runner"]:
                self.watchdog.heartbeat(name)
            await asyncio.sleep(15.0)

    async def _maintenance_loop(self) -> None:
        """Periodic maintenance tasks."""
        while self._running:
            # Every 6 hours: memory decay
            await self.job_runner.schedule(
                "memory_decay",
                config=JobConfig(timeout_seconds=30, max_retries=1),
            )
            # Every hour: health check
            await self.job_runner.schedule(
                "health_check",
                config=JobConfig(timeout_seconds=10, max_retries=0),
            )
            await asyncio.sleep(3600)  # 1 hour

    # --- Public API ---

    async def run_build_job(self, intake: Any):
        """Run a build job through the shared orchestrator runtime."""
        if not self._initialized:
            await self.initialize()
        return await self.build.run_build(intake)

    async def run_review_job(self, intake: Any):
        """Run a review job through the shared orchestrator runtime."""
        if not self._initialized:
            await self.initialize()
        return await self.review.run_review(intake)

    async def resume_build_job(self, job_id: str):
        """Resume a previously interrupted build job."""
        if not self._initialized:
            await self.initialize()
        return await self.build.resume_build(job_id)

    def qualify_operator_intake(self, intake: Any) -> dict[str, Any]:
        """Return routing/qualification result for unified operator intake."""
        return self.intake_router.qualify(intake).to_dict()

    def preview_operator_intake(self, intake: Any) -> dict[str, Any]:
        """Return qualification plus planner output for unified intake."""
        qualification = self.intake_router.qualify(intake)
        plan = self.intake_router.create_plan(intake, qualification=qualification)
        status = "preview" if qualification.supported else "blocked"
        record = self.control_plane.record_plan(
            intake=intake.to_dict(),
            qualification=qualification.to_dict(),
            plan=plan.to_dict(),
            status=self._plan_status(status),
        )
        traces = self.control_plane.capture_plan_traces(record)
        return {
            "accepted": qualification.supported,
            "qualification": qualification.to_dict(),
            "plan": plan.to_dict(),
            "plan_record": record.to_dict(),
            "plan_traces": [trace.to_dict() for trace in traces],
        }

    async def submit_operator_intake(self, intake: Any) -> dict[str, Any]:
        """Route unified operator intake into review/build runtime flows."""
        if not self._initialized:
            await self.initialize()

        qualification = self.intake_router.qualify(intake)
        plan = self.intake_router.create_plan(intake, qualification=qualification)
        result: dict[str, Any] = {
            "accepted": qualification.supported,
            "qualification": qualification.to_dict(),
            "plan": plan.to_dict(),
            "status": "blocked" if not qualification.supported else "submitted",
        }
        plan_record = self.control_plane.record_plan(
            intake=intake.to_dict(),
            qualification=qualification.to_dict(),
            plan=plan.to_dict(),
            status=self._plan_status(
                "submitted" if qualification.supported else "blocked"
            ),
        )
        traces = self.control_plane.capture_plan_traces(plan_record)
        result["plan_record"] = plan_record.to_dict()
        result["plan_traces"] = [trace.to_dict() for trace in traces]
        if not qualification.supported:
            denial = make_denial(
                code="operator_intake_blocked",
                summary="Operator intake blocked",
                detail="; ".join(qualification.blockers),
                scope=getattr(intake, "repo_path", "") or getattr(intake, "git_url", ""),
                suggested_action="Resolve the intake blockers and rerun preview or submit.",
            )
            result["error"] = denial.message
            result["denial"] = denial.to_dict()
            return result

        budget = plan.budget
        if budget.hard_cap_hit or budget.stop_loss_hit or not budget.within_budget:
            detail = self._budget_block_detail(budget)
            self.control_plane.update_plan_status(
                plan_record.plan_id,
                status=self._plan_status("blocked"),
            )
            self.control_plane.record_trace(
                trace_kind=TraceRecordKind.BUDGET,
                title="Runtime budget block",
                detail=detail,
                plan_id=plan_record.plan_id,
                metadata=budget.to_dict(),
            )
            result["status"] = "blocked"
            denial = make_denial(
                code="budget_blocked",
                summary="Runtime execution blocked by budget policy",
                detail=detail,
                scope=plan_record.plan_id,
                policy_id="budget_policy",
                suggested_action="Reduce scope, wait for budget reset, or request explicit approval.",
            )
            result["error"] = denial.message
            result["denial"] = denial.to_dict()
            return result

        approval = self._build_runtime_approval(
            intake=intake,
            qualification=qualification,
            plan=plan,
            plan_id=plan_record.plan_id,
        )
        if approval is not None:
            self.control_plane.update_plan_status(
                plan_record.plan_id,
                status=self._plan_status("awaiting_approval"),
            )
            self.control_plane.record_trace(
                trace_kind=TraceRecordKind.EXECUTION,
                title="Runtime approval requested",
                detail=approval["reason"],
                plan_id=plan_record.plan_id,
                metadata=approval,
            )
            result["status"] = "awaiting_approval"
            result["approval_request"] = approval
            result["error"] = approval["reason"]
            result["denial"] = make_denial(
                code="approval_required",
                summary="Runtime execution paused for approval",
                detail=approval["reason"],
                scope=plan_record.plan_id,
                suggested_action="Approve the request before execution can continue.",
                metadata={"approval_request_id": approval["approval_request_id"]},
            ).to_dict()
            return result

        effective_intake = intake
        if getattr(intake, "git_url", "") and not getattr(intake, "repo_path", ""):
            effective_intake = OperatorIntake(**intake.to_dict())
            acquisition = self.repo_acquisition.acquire(intake.git_url)
            self.control_plane.record_trace(
                trace_kind=TraceRecordKind.EXECUTION,
                title="Repository acquisition",
                detail=(
                    f"git_url acquisition {'succeeded' if acquisition.acquired else 'failed'} "
                    f"for {intake.git_url}"
                ),
                plan_id=plan_record.plan_id,
                metadata=acquisition.to_dict(),
            )
            result["acquisition"] = acquisition.to_dict()
            if not acquisition.acquired:
                self.control_plane.update_plan_status(
                    plan_record.plan_id,
                    status=self._plan_status("blocked"),
                )
                result["status"] = "blocked"
                denial = make_denial(
                    code="repository_acquisition_failed",
                    summary="Repository acquisition blocked",
                    detail=acquisition.error or "Repository acquisition failed.",
                    scope=getattr(intake, "git_url", ""),
                    environment_profile_id="repo_import_mirror",
                    suggested_action="Use a supported git_url source or fix host git/network availability.",
                )
                result["error"] = denial.message
                result["denial"] = denial.to_dict()
                return result
            effective_intake.repo_path = acquisition.repo_path

        self.control_plane.update_plan_status(
            plan_record.plan_id,
            status=self._plan_status("executing"),
        )
        self.control_plane.record_trace(
            trace_kind=TraceRecordKind.EXECUTION,
            title="Runtime execution started",
            detail=(
                f"starting {qualification.resolved_work_type.value} execution for "
                f"plan {plan_record.plan_id}"
            ),
            plan_id=plan_record.plan_id,
            metadata={
                "resolved_work_type": qualification.resolved_work_type.value,
                "risk_level": qualification.risk_level,
                "budget": budget.to_dict(),
                "git_url": getattr(intake, "git_url", ""),
                "effective_repo_path": getattr(effective_intake, "repo_path", ""),
            },
        )

        if qualification.resolved_work_type == OperatorWorkType.BUILD:
            job = await self.run_build_job(self.intake_router.to_build_intake(effective_intake))
            plan_status = (
                "completed"
                if job.status.value == "completed"
                else "blocked"
            )
            self.control_plane.update_plan_status(
                plan_record.plan_id,
                status=self._plan_status(plan_status),
                linked_job_id=job.id,
            )
            self.control_plane.record_trace(
                trace_kind=TraceRecordKind.EXECUTION,
                title="Runtime build execution finished",
                detail=f"job {job.id} finished with status {job.status.value}",
                plan_id=plan_record.plan_id,
                job_id=job.id,
                workspace_id=job.workspace_id,
                metadata={"job_status": job.status.value},
            )
            result.update(
                {
                    "status": plan_status,
                    "job_id": job.id,
                    "job_kind": "build",
                    "job": self.get_product_job(job.id, kind="build"),
                }
            )
            return result

        job = await self.run_review_job(self.intake_router.to_review_intake(effective_intake))
        plan_status = (
            "completed"
            if job.status.value == "completed"
            else "blocked"
        )
        self.control_plane.update_plan_status(
            plan_record.plan_id,
            status=self._plan_status(plan_status),
            linked_job_id=job.id,
        )
        self.control_plane.record_trace(
            trace_kind=TraceRecordKind.EXECUTION,
            title="Runtime review execution finished",
            detail=f"job {job.id} finished with status {job.status.value}",
            plan_id=plan_record.plan_id,
            job_id=job.id,
            workspace_id=job.workspace_id,
            metadata={"job_status": job.status.value, "verdict": job.report.verdict},
        )
        result.update(
            {
                "status": plan_status,
                "job_id": job.id,
                "job_kind": "review",
                "job": self.get_product_job(job.id, kind="review"),
            }
        )
        return result

    def _budget_block_detail(self, budget: Any) -> str:
        if budget.hard_cap_hit:
            return "Budget hard cap blocks execution for this intake."
        if budget.stop_loss_hit:
            return "Budget stop-loss blocks execution to preserve remaining runway."
        return "Budget posture blocks execution for this intake."

    def _build_runtime_approval(
        self,
        *,
        intake: Any,
        qualification: Any,
        plan: Any,
        plan_id: str,
    ) -> dict[str, Any] | None:
        category: ApprovalCategory | None = None
        description = ""
        reason = ""
        if plan.budget.requires_approval:
            category = ApprovalCategory.FINANCE
            description = (
                f"Approve budget for {qualification.resolved_work_type.value} "
                f"plan {plan_id[:8]}"
            )
            reason = (
                f"Estimated cost ${plan.budget.estimated_cost_usd:.2f} exceeds the "
                "single-transaction approval cap."
            )
        elif qualification.risk_level == "high":
            category = ApprovalCategory.TOOL
            description = (
                f"Approve high-risk {qualification.resolved_work_type.value} "
                f"execution for plan {plan_id[:8]}"
            )
            reason = (
                f"Qualification marked this intake as high risk: "
                f"{', '.join(qualification.risk_factors[:3]) or 'no factors provided'}."
            )

        if category is None:
            return None

        required_approvals = 2 if (
            qualification.risk_level == "high"
            or getattr(intake, "git_url", "")
            or (
                plan.budget.requires_approval
                and plan.budget.estimated_cost_usd >= plan.budget.single_tx_approval_cap_usd
            )
        ) else 1

        approval = self.approval_queue.propose(
            category=category,
            description=description,
            risk_level=qualification.risk_level,
            reason=reason,
            context={
                "plan_id": plan_id,
                "repo_path": getattr(intake, "repo_path", ""),
                "git_url": getattr(intake, "git_url", ""),
                "work_type": qualification.resolved_work_type.value,
                "estimated_cost_usd": plan.budget.estimated_cost_usd,
                "budget": plan.budget.to_dict(),
                "risk_level": qualification.risk_level,
                "risk_factors": list(qualification.risk_factors),
            },
            required_approvals=required_approvals,
        )
        return {
            "approval_request_id": approval.id,
            "approval_status": approval.status.value,
            "category": approval.category.value,
            "description": approval.description,
            "reason": approval.reason,
            "required_approvals": approval.required_approvals,
        }

    def list_operator_plans(
        self,
        *,
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List persisted planner handoff records."""
        return [
            record.to_dict()
            for record in self.control_plane.list_plans(status=status, limit=limit)
        ]

    def get_operator_plan(self, plan_id: str) -> dict[str, Any] | None:
        """Load one persisted planner handoff record."""
        record = self.control_plane.get_plan(plan_id)
        if record is None:
            return None
        return record.to_dict()

    def list_execution_traces(
        self,
        *,
        trace_kind: str = "",
        plan_id: str = "",
        job_id: str = "",
        workspace_id: str = "",
        bundle_id: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """List shared control-plane trace records."""
        return [
            record.to_dict()
            for record in self.control_plane.list_traces(
                trace_kind=trace_kind,
                plan_id=plan_id,
                job_id=job_id,
                workspace_id=workspace_id,
                bundle_id=bundle_id,
                limit=limit,
            )
        ]

    def list_workspace_records(
        self,
        *,
        status: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List workspace records through the shared query surface."""
        return [
            record.to_dict()
            for record in self.workspace_queries.list_workspaces(
                status=status,
                limit=limit,
            )
        ]

    def get_workspace_record(self, workspace_id: str) -> dict[str, Any] | None:
        """Load one workspace record with linked job/artifact/approval/bundle joins."""
        record = self.workspace_queries.get_workspace(workspace_id)
        if record is None:
            return None
        return record.to_dict()

    def list_delivery_records(
        self,
        *,
        status: str = "",
        job_id: str = "",
        workspace_id: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List persisted delivery lifecycle records."""
        return [
            record.to_dict()
            for record in self.control_plane.list_deliveries(
                status=status,
                job_id=job_id,
                workspace_id=workspace_id,
                limit=limit,
            )
        ]

    def list_persisted_product_jobs(
        self,
        *,
        job_kind: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List durable build/review job records from the shared control plane."""
        return [
            record.to_dict()
            for record in self.control_plane.list_product_jobs(
                job_kind=job_kind,
                status=status,
                limit=limit,
            )
        ]

    def get_persisted_product_job(self, job_id: str) -> dict[str, Any] | None:
        """Load one durable build/review job record from the shared control plane."""
        record = self.control_plane.get_product_job(job_id)
        if record is None:
            return None
        return record.to_dict()

    def list_retained_artifacts(
        self,
        *,
        status: str = "",
        job_id: str = "",
        artifact_kind: str = "",
        retention_policy_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List retained artifact and delivery-output records."""
        return [
            record.to_dict()
            for record in self.control_plane.list_retained_artifacts(
                status=status,
                job_id=job_id,
                artifact_kind=artifact_kind,
                retention_policy_id=retention_policy_id,
                limit=limit,
            )
        ]

    def get_retained_artifact(self, record_id: str) -> dict[str, Any] | None:
        """Load one retained artifact or delivery-output record."""
        record = self.control_plane.get_retained_artifact(record_id)
        if record is None:
            return None
        return record.to_dict()

    def prune_retained_artifacts(
        self,
        *,
        job_id: str = "",
        artifact_kind: str = "",
        retention_policy_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Prune expired retained artifacts under the configured retention rules."""
        return [
            record.to_dict()
            for record in self.control_plane.prune_retained_artifacts(
                job_id=job_id,
                artifact_kind=artifact_kind,
                retention_policy_id=retention_policy_id,
                limit=limit,
            )
        ]

    def list_cost_ledger(
        self,
        *,
        job_id: str = "",
        job_kind: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List durable per-job cost and token records."""
        return [
            entry.to_dict()
            for entry in self.control_plane.list_cost_entries(
                job_id=job_id,
                job_kind=job_kind,
                limit=limit,
            )
        ]

    def export_job_evidence(
        self,
        job_id: str,
        *,
        kind: str | None = None,
        export_format: str = "json",
        export_mode: str = "internal",
    ) -> dict[str, Any] | str:
        """Export a compliance-friendly evidence package for one job."""
        if export_format == "markdown":
            return self.evidence_exports.export_job_markdown(
                job_id,
                kind=kind,
                export_mode=export_mode,
            )
        return self.evidence_exports.export_job(
            job_id,
            kind=kind,
            export_mode=export_mode,
        )

    def get_review_delivery_bundle(self, job_id: str) -> dict[str, Any] | None:
        """Return a reviewer delivery package preview."""
        return self.review.get_delivery_bundle(job_id)

    def get_review_delivery_record(self, job_id: str) -> dict[str, Any] | None:
        """Return persisted delivery lifecycle state for a review package."""
        return self.review.get_delivery_record(job_id)

    def request_review_delivery_approval(self, job_id: str) -> dict[str, Any]:
        """Request approval for external delivery of a review package."""
        return self.review.request_delivery_approval(job_id)

    def mark_review_delivery_handed_off(
        self,
        job_id: str,
        *,
        note: str = "",
    ) -> dict[str, Any]:
        """Mark a review delivery package as handed off after approval."""
        return self.review.mark_delivery_handed_off(job_id, note=note)

    def get_review_client_safe_bundle(self, job_id: str) -> dict[str, Any] | None:
        """Return the client-safe review delivery bundle."""
        return self.review.get_client_safe_bundle(job_id)

    async def send_review_delivery_via_gateway(
        self,
        job_id: str,
        *,
        target_url: str = "",
        auth_token: str = "",
        gateway_policy_id: str = "approval_before_gateway",
        provider_id: str = "",
        capability_id: str = "",
        route_id: str = "",
    ) -> dict[str, Any]:
        """Send an approved review package through the explicit gateway boundary."""
        bundle = self.get_review_client_safe_bundle(job_id)
        record = self.get_review_delivery_record(job_id)
        if bundle is None or record is None:
            denial = make_denial(
                code="review_gateway_bundle_missing",
                summary="Review gateway delivery blocked",
                detail=f"Delivery bundle or record not found for review job '{job_id}'",
                scope=job_id,
                suggested_action="Assemble the review delivery bundle and approval record before gateway delivery.",
            )
            return {"error": denial.message, "denial": denial.to_dict()}
        if record["status"] not in {"approved", "handed_off"}:
            denial = make_denial(
                code="review_gateway_not_approved",
                summary="Review gateway delivery blocked",
                detail=(
                    f"Delivery record '{record['bundle_id']}' is {record['status']}, "
                    "not approved for gateway send"
                ),
                scope=record["bundle_id"],
                suggested_action="Approve the review delivery request before sending it through the gateway.",
            )
            return {"error": denial.message, "denial": denial.to_dict()}
        if not target_url and not (provider_id and capability_id):
            denial = make_denial(
                code="review_gateway_target_missing",
                summary="Review gateway delivery blocked",
                detail=(
                    "Provide either a direct gateway target URL or a configured "
                    "provider + capability route."
                ),
                scope=record["bundle_id"],
                suggested_action=(
                    "Use --gateway-target or --gateway-provider plus "
                    "--gateway-capability."
                ),
            )
            return {"error": denial.message, "denial": denial.to_dict()}

        request_detail = (
            f"Gateway delivery requested for {target_url}"
            if target_url
            else (
                "Gateway delivery requested for provider "
                f"{provider_id} capability {capability_id}"
            )
        )
        self.control_plane.record_delivery_event(
            record["bundle_id"],
            event_type="gateway_requested",
            detail=request_detail,
            metadata={
                "gateway_policy_id": gateway_policy_id,
                "target_url": target_url,
                "provider_id": provider_id,
                "capability_id": capability_id,
                "route_id": route_id,
            },
        )
        if provider_id and capability_id:
            result = await self.gateway.send_delivery_via_capability(
                bundle=bundle,
                job_kind=JobKind.REVIEW,
                provider_id=provider_id,
                capability_id=capability_id,
                route_id=route_id,
                target_url=target_url,
                auth_token=auth_token,
                approval_request_id=record.get("approval_request_id", ""),
                delivery_policy_id="approval_required",
                export_mode="client_safe",
            )
        else:
            result = await self.gateway.send_delivery(
                bundle=bundle,
                job_kind=JobKind.REVIEW,
                target_url=target_url,
                approval_request_id=record.get("approval_request_id", ""),
                gateway_policy_id=gateway_policy_id,
                auth_token=auth_token,
                delivery_policy_id="approval_required",
                export_mode="client_safe",
            )
        if result.get("ok"):
            sent_target = result.get("target_url", target_url)
            self.control_plane.record_delivery_event(
                record["bundle_id"],
                event_type="gateway_succeeded",
                detail=(
                    f"Gateway delivery sent to {sent_target}"
                    if sent_target
                    else (
                        "Gateway delivery sent through provider "
                        f"{result.get('provider_id', provider_id)} "
                        f"capability {result.get('capability_id', capability_id)}"
                    )
                ),
                metadata=result,
            )
            result["delivery_record"] = self.mark_review_delivery_handed_off(
                job_id,
                note=(
                    f"Gateway delivery sent to {sent_target}"
                    if sent_target
                    else (
                        "Gateway delivery sent through provider "
                        f"{result.get('provider_id', provider_id)} "
                        f"capability {result.get('capability_id', capability_id)}"
                    )
                ),
            )
            return result
        self.control_plane.record_delivery_event(
            record["bundle_id"],
            event_type="gateway_failed",
            detail=result.get("error", "Gateway delivery failed"),
            metadata=result,
        )
        return result

    def list_approval_requests(
        self,
        *,
        status: str = "",
        category: str = "",
        job_id: str = "",
        artifact_id: str = "",
        workspace_id: str = "",
        bundle_id: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Query approval requests with optional job/artifact linkage filters."""
        return self.approval_queue.list_requests(
            status=status or None,
            category=category or None,
            job_id=job_id,
            artifact_id=artifact_id,
            workspace_id=workspace_id,
            bundle_id=bundle_id,
            limit=limit,
        )

    def get_build_delivery_bundle(self, job_id: str) -> dict[str, Any] | None:
        """Return a builder delivery package preview."""
        return self.build.get_delivery_bundle(job_id)

    def get_build_delivery_record(self, job_id: str) -> dict[str, Any] | None:
        """Return persisted delivery lifecycle state for a build package."""
        return self.build.get_delivery_record(job_id)

    def request_build_delivery_approval(self, job_id: str) -> dict[str, Any]:
        """Request approval for external delivery of a build package."""
        return self.build.request_delivery_approval(job_id)

    def mark_build_delivery_handed_off(
        self,
        job_id: str,
        *,
        note: str = "",
    ) -> dict[str, Any]:
        """Mark a build delivery package as handed off after approval."""
        return self.build.mark_delivery_handed_off(job_id, note=note)

    async def send_build_delivery_via_gateway(
        self,
        job_id: str,
        *,
        target_url: str = "",
        auth_token: str = "",
        gateway_policy_id: str = "approval_before_gateway",
        provider_id: str = "",
        capability_id: str = "",
        route_id: str = "",
    ) -> dict[str, Any]:
        """Send an approved build package through the explicit gateway boundary."""
        bundle = self.get_build_delivery_bundle(job_id)
        record = self.get_build_delivery_record(job_id)
        job = self.build.load_job(job_id)
        if bundle is None or record is None or job is None:
            denial = make_denial(
                code="build_gateway_bundle_missing",
                summary="Build gateway delivery blocked",
                detail=f"Delivery bundle or record not found for build job '{job_id}'",
                scope=job_id,
                suggested_action="Assemble the build delivery bundle and approval record before gateway delivery.",
            )
            return {"error": denial.message, "denial": denial.to_dict()}
        if record["status"] not in {"approved", "handed_off"}:
            denial = make_denial(
                code="build_gateway_not_approved",
                summary="Build gateway delivery blocked",
                detail=(
                    f"Delivery record '{record['bundle_id']}' is {record['status']}, "
                    "not approved for gateway send"
                ),
                scope=record["bundle_id"],
                suggested_action="Approve the build delivery request before sending it through the gateway.",
            )
            return {"error": denial.message, "denial": denial.to_dict()}
        if not target_url and not (provider_id and capability_id):
            denial = make_denial(
                code="build_gateway_target_missing",
                summary="Build gateway delivery blocked",
                detail=(
                    "Provide either a direct gateway target URL or a configured "
                    "provider + capability route."
                ),
                scope=record["bundle_id"],
                suggested_action=(
                    "Use --gateway-target or --gateway-provider plus "
                    "--gateway-capability."
                ),
            )
            return {"error": denial.message, "denial": denial.to_dict()}

        request_detail = (
            f"Gateway delivery requested for {target_url}"
            if target_url
            else (
                "Gateway delivery requested for provider "
                f"{provider_id} capability {capability_id}"
            )
        )
        self.control_plane.record_delivery_event(
            record["bundle_id"],
            event_type="gateway_requested",
            detail=request_detail,
            metadata={
                "gateway_policy_id": gateway_policy_id,
                "target_url": target_url,
                "provider_id": provider_id,
                "capability_id": capability_id,
                "route_id": route_id,
            },
        )
        if provider_id and capability_id:
            result = await self.gateway.send_delivery_via_capability(
                bundle=bundle,
                job_kind=JobKind.BUILD,
                provider_id=provider_id,
                capability_id=capability_id,
                route_id=route_id,
                target_url=target_url,
                auth_token=auth_token,
                approval_request_id=record.get("approval_request_id", ""),
                delivery_policy_id=job.intake.delivery_policy_id,
                export_mode="internal",
            )
        else:
            result = await self.gateway.send_delivery(
                bundle=bundle,
                job_kind=JobKind.BUILD,
                target_url=target_url,
                approval_request_id=record.get("approval_request_id", ""),
                gateway_policy_id=gateway_policy_id,
                auth_token=auth_token,
                delivery_policy_id=job.intake.delivery_policy_id,
                export_mode="internal",
            )
        if result.get("ok"):
            sent_target = result.get("target_url", target_url)
            self.control_plane.record_delivery_event(
                record["bundle_id"],
                event_type="gateway_succeeded",
                detail=(
                    f"Gateway delivery sent to {sent_target}"
                    if sent_target
                    else (
                        "Gateway delivery sent through provider "
                        f"{result.get('provider_id', provider_id)} "
                        f"capability {result.get('capability_id', capability_id)}"
                    )
                ),
                metadata=result,
            )
            result["delivery_record"] = self.mark_build_delivery_handed_off(
                job_id,
                note=(
                    f"Gateway delivery sent to {sent_target}"
                    if sent_target
                    else (
                        "Gateway delivery sent through provider "
                        f"{result.get('provider_id', provider_id)} "
                        f"capability {result.get('capability_id', capability_id)}"
                    )
                ),
            )
            return result
        self.control_plane.record_delivery_event(
            record["bundle_id"],
            event_type="gateway_failed",
            detail=result.get("error", "Gateway delivery failed"),
            metadata=result,
        )
        return result

    def list_product_jobs(
        self,
        kind: JobKind | str | None = None,
        status: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List build/review jobs through one shared query layer."""
        return [
            job.to_dict()
            for job in self.jobs.list_jobs(kind=kind, status=status, limit=limit)
        ]

    async def evaluate_review_quality(
        self,
        *,
        release_label: str = "",
    ) -> dict[str, Any]:
        """Run deterministic golden reviewer cases and return quality telemetry."""
        return await self.review_quality.evaluate_goldens(release_label=release_label)

    async def evaluate_release_readiness(
        self,
        *,
        release_label: str = "",
        policy_id: str = "phase2_closure",
    ) -> dict[str, Any]:
        """Run release-readiness checks over quality telemetry and gateway posture."""
        quality = await self.review_quality.evaluate_goldens(release_label=release_label)
        gateway_catalog = self.gateway.describe_capability_catalog()
        readiness = evaluate_release_readiness(
            quality_summary=quality,
            gateway_catalog=gateway_catalog,
            policy_id=policy_id,
        )
        identity_warnings = get_identity_onboarding_warnings()
        if identity_warnings:
            readiness["warnings"] = [*readiness.get("warnings", []), *identity_warnings]
            readiness["identity_onboarding_warnings"] = identity_warnings
        self.control_plane.record_trace(
            trace_kind=TraceRecordKind.RELEASE,
            title="Release readiness evaluation",
            detail=(
                f"ready={readiness['ready']}; "
                f"blocking_reasons={len(readiness['blocking_reasons'])}; "
                f"warnings={len(readiness['warnings'])}"
            ),
            metadata=readiness,
        )
        return readiness

    def get_gateway_catalog(
        self,
        *,
        provider_id: str = "",
        capability_id: str = "",
        kind: JobKind | str | None = None,
        export_mode: str = "",
    ) -> dict[str, Any]:
        """Describe provider-ready external gateway catalog and readiness."""
        return self.gateway.describe_capability_catalog(
            provider_id=provider_id,
            capability_id=capability_id,
            job_kind=kind,
            export_mode=export_mode,
        )

    async def call_external_api(
        self,
        *,
        provider_id: str,
        capability_id: str,
        resource: str = "",
        method: str = "",
        query_params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        route_id: str = "",
        auth_token: str = "",
        gateway_policy_id: str = "",
        job_id: str = "",
        requester: str = "operator",
        title: str = "",
    ) -> dict[str, Any]:
        """Call a provider-backed API capability through the explicit gateway."""
        return await self.gateway.call_api_via_capability(
            provider_id=provider_id,
            capability_id=capability_id,
            resource=resource,
            method=method,
            query_params=query_params or {},
            json_payload=json_payload or {},
            route_id=route_id,
            auth_token=auth_token,
            gateway_policy_id=gateway_policy_id,
            job_id=job_id,
            requester=requester,
            title=title,
        )

    def get_product_job(
        self,
        job_id: str,
        kind: JobKind | str | None = None,
    ) -> dict[str, Any] | None:
        """Load one build/review job through the shared query layer."""
        job = self.jobs.get_job(job_id=job_id, kind=kind)
        if job is None:
            return None
        return job.to_dict()

    def _lookup_secret(self, name: str) -> str:
        """Resolve a secret lazily from the local encrypted vault when available."""
        if not name or self._secrets_lookup_disabled:
            return ""
        if self._secrets_manager is None:
            secrets_file = self._data_dir / "vault" / "secrets.enc"
            if not os.environ.get("AGENT_VAULT_KEY") and not secrets_file.exists():
                self._secrets_lookup_disabled = True
                return ""
            try:
                from agent.vault.secrets import SecretsManager

                self._secrets_manager = SecretsManager(
                    vault_dir=str(self._data_dir / "vault"),
                )
            except Exception:
                self._secrets_lookup_disabled = True
                return ""
        return str(self._secrets_manager.get_secret(name) or "")

    def list_product_artifacts(
        self,
        *,
        kind: JobKind | str | None = None,
        job_id: str = "",
        artifact_kind: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List build/review artifacts through one shared query layer."""
        return [
            artifact.to_dict()
            for artifact in self.artifacts.list_artifacts(
                kind=kind,
                job_id=job_id,
                artifact_kind=artifact_kind,
                limit=limit,
            )
        ]

    def get_product_artifact(
        self,
        artifact_id: str,
        *,
        kind: JobKind | str | None = None,
    ) -> dict[str, Any] | None:
        """Load one build/review artifact through the shared query layer."""
        artifact = self.artifacts.get_artifact(artifact_id=artifact_id, kind=kind)
        if artifact is None:
            return None
        return artifact.to_dict()

    def get_operator_report(self, limit: int = 20) -> dict[str, Any]:
        """Return a compact operator-facing report/inbox."""
        return self.reporting.get_report(limit=limit)

    def get_runtime_model(self) -> dict[str, Any]:
        """Return explicit coexistence rules for runtime surfaces."""
        return self.runtime_model.get_model()

    def get_status(self) -> dict[str, Any]:
        """Get overall agent status."""
        recent_worker_jobs = [
            job.to_dict() for job in self.job_runner.get_recent_jobs(limit=10)
        ]
        active_worker_jobs = [
            job.to_dict() for job in self.job_runner.get_active_jobs()
        ]
        recent_workspaces = [
            workspace.to_dict()
            for workspace in sorted(
                self.workspaces.list_workspaces(),
                key=lambda item: item.created_at,
                reverse=True,
            )[:10]
        ]
        control_stats = self.control_plane.get_stats()
        return {
            "running": self._running,
            "memory": self.memory.get_stats(),
            "tasks": self.tasks.get_stats(),
            "brain": self.brain.get_stats(),
            "finance": self.finance.get_stats(),
            "approvals": self.approval_queue.get_stats(),
            "build": self.build.get_stats(),
            "review": self.review.get_stats(),
            "workspaces": {
                **self.workspaces.get_stats(),
                "recent": recent_workspaces,
            },
            "control_plane": {
                "queryable_job_kinds": ["build", "review", "operate"],
                "queryable_artifact_kinds": [
                    "review_report",
                    "finding_list",
                    "diff_analysis",
                    "security_report",
                    "executive_summary",
                    "patch",
                    "diff",
                    "verification_report",
                    "acceptance_report",
                    "delivery_bundle",
                    "execution_trace",
                ],
                "persisted_plans": control_stats["plans"],
                "persisted_traces": control_stats["traces"],
                "persisted_deliveries": control_stats["deliveries"],
                "persisted_product_jobs": control_stats["product_jobs"],
                "retained_artifacts": control_stats["retained_artifacts"],
                "cost_ledger_entries": control_stats["cost_entries"],
                "recorded_cost_usd": control_stats["recorded_cost_usd"],
                "operator_intake_work_types": ["auto", "review", "build"],
                "runtime_model_status": self.runtime_model.get_model()["status"],
            },
            "jobs": self.job_runner.get_stats(),
            "worker_execution": {
                "active_jobs": len(active_worker_jobs),
                "recent_jobs": recent_worker_jobs,
                "circuit_breaker_open": self.job_runner.circuit_breaker_open,
            },
            "watchdog": self.watchdog.get_stats(),
            "router": self.router.get_metrics(),
        }

    def _plan_status(self, value: str) -> PlanRecordStatus:
        return PlanRecordStatus(value)
