"""
Agent Life Space — Job Pipeline Orchestrator

Multi-job pipeline orchestration: review→build→verify→deliver chains
where each stage executes based on the previous stage's outcome.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from agent.control.models import JobKind, JobPipeline, PipelineStage

logger = structlog.get_logger(__name__)


class PipelineOrchestrator:
    """Manages multi-job pipeline creation and sequential stage execution."""

    def __init__(self, agent: Any = None) -> None:
        self._agent = agent
        self._pipelines: dict[str, JobPipeline] = {}
        self._load_from_storage()

    def _load_from_storage(self) -> None:
        """Load persisted pipelines from SQLite on startup."""
        if not self._agent or not hasattr(self._agent, "control_plane"):
            return
        try:
            storage = self._agent.control_plane._storage
            rows = storage.list_job_pipelines(limit=200)
            for data in rows:
                pl = JobPipeline.from_dict(data)
                self._pipelines[pl.pipeline_id] = pl
            if rows:
                logger.info("pipelines_loaded", count=len(rows))
        except Exception:
            logger.exception("pipelines_load_error")

    def _persist(self, pipeline: JobPipeline) -> None:
        """Persist a single pipeline to SQLite."""
        if not self._agent or not hasattr(self._agent, "control_plane"):
            return
        try:
            self._agent.control_plane._storage.save_job_pipeline(pipeline)
        except Exception:
            logger.exception("pipeline_persist_error", pipeline_id=pipeline.pipeline_id)

    def create_pipeline(
        self,
        *,
        name: str,
        stages: list[dict[str, Any]],
        triggered_by: str = "manual",
        metadata: dict[str, Any] | None = None,
    ) -> JobPipeline:
        """Create a pipeline from a list of stage definitions.

        Each stage dict should have: name, job_kind, intake_template,
        and optionally condition (default: "on_success").
        """
        pipeline_stages = []
        for stage_def in stages:
            stage = PipelineStage(
                name=stage_def.get("name", ""),
                job_kind=(
                    stage_def["job_kind"]
                    if isinstance(stage_def.get("job_kind"), JobKind)
                    else JobKind(str(stage_def.get("job_kind", "review")))
                ),
                intake_template=dict(stage_def.get("intake_template", {})),
                condition=stage_def.get("condition", "on_success"),
            )
            pipeline_stages.append(stage)

        pipeline = JobPipeline(
            name=name,
            stages=pipeline_stages,
            triggered_by=triggered_by,
            metadata=metadata or {},
        )
        self._pipelines[pipeline.pipeline_id] = pipeline
        self._persist(pipeline)
        logger.info(
            "pipeline_created",
            pipeline_id=pipeline.pipeline_id,
            name=name,
            stage_count=len(pipeline_stages),
        )
        return pipeline

    def get(self, pipeline_id: str) -> JobPipeline | None:
        return self._pipelines.get(pipeline_id)

    def list_pipelines(self, status: str = "") -> list[JobPipeline]:
        pipelines = list(self._pipelines.values())
        if status:
            pipelines = [p for p in pipelines if p.status == status]
        return sorted(pipelines, key=lambda p: p.created_at, reverse=True)

    async def execute_pipeline(self, pipeline_id: str) -> dict[str, Any]:
        """Execute all stages in a pipeline sequentially.

        Each stage is evaluated against its condition before execution.
        The pipeline stops on the first stage failure (unless condition is "always").
        """
        pipeline = self._pipelines.get(pipeline_id)
        if not pipeline:
            return {"ok": False, "error": f"Pipeline '{pipeline_id}' not found."}
        if not pipeline.stages:
            return {"ok": False, "error": "Pipeline has no stages."}

        pipeline.status = "executing"
        pipeline.started_at = datetime.now(UTC).isoformat()

        last_status = "completed"
        executed_stages = 0

        for i, stage in enumerate(pipeline.stages):
            pipeline.current_stage_index = i

            # Evaluate condition
            if not self._should_execute_stage(stage.condition, last_status):
                stage.status = "skipped"
                continue

            # Execute stage
            stage.status = "running"
            try:
                result = await self._execute_stage(stage, pipeline)
                if result.get("ok", False):
                    stage.status = "completed"
                    stage.job_id = str(result.get("job_id", ""))
                    last_status = "completed"
                    executed_stages += 1
                else:
                    stage.status = "failed"
                    stage.error = str(result.get("error", ""))[:200]
                    last_status = "failed"
                    executed_stages += 1
            except Exception as e:
                stage.status = "failed"
                stage.error = str(e)[:200]
                last_status = "failed"
                logger.error(
                    "pipeline_stage_error",
                    pipeline_id=pipeline_id,
                    stage=stage.name,
                    error=str(e),
                )

        # Finalize pipeline
        pipeline.completed_at = datetime.now(UTC).isoformat()
        all_completed = all(s.status in ("completed", "skipped") for s in pipeline.stages)
        pipeline.status = "completed" if all_completed else "failed"

        self._persist(pipeline)
        logger.info(
            "pipeline_completed",
            pipeline_id=pipeline_id,
            status=pipeline.status,
            executed_stages=executed_stages,
            total_stages=len(pipeline.stages),
        )

        return {
            "ok": all_completed,
            "pipeline_id": pipeline_id,
            "status": pipeline.status,
            "stages_executed": executed_stages,
            "stages_total": len(pipeline.stages),
            "stages": [s.to_dict() for s in pipeline.stages],
        }

    @staticmethod
    def _should_execute_stage(condition: str, previous_status: str) -> bool:
        """Evaluate whether a stage should execute based on its condition."""
        if condition == "always":
            return True
        if condition == "on_success":
            return previous_status == "completed"
        if condition == "on_failure":
            return previous_status == "failed"
        return True  # Unknown condition → execute

    async def _execute_stage(
        self,
        stage: PipelineStage,
        pipeline: JobPipeline,
    ) -> dict[str, Any]:
        """Execute a single pipeline stage by submitting intake to the agent."""
        if self._agent is None:
            return {"ok": False, "error": "No agent configured for pipeline execution."}

        # Build intake from template
        from agent.control.intake import OperatorIntake

        template = dict(stage.intake_template)
        intake = OperatorIntake(
            repo_path=template.get("repo_path", ""),
            git_url=template.get("git_url", ""),
            work_type=template.get("work_type", stage.job_kind.value),
            description=template.get("description", stage.name),
            requester=template.get("requester", "pipeline"),
        )

        result = await self._agent.submit_operator_intake(intake)
        job_id = result.get("job_id", "")

        # Link job to pipeline
        if job_id and hasattr(self._agent, "control_plane_state"):
            try:
                job = self._agent.control_plane_state.get_product_job(job_id)
                if job:
                    job.pipeline_id = pipeline.pipeline_id
                    self._agent.control_plane_state._storage.save_product_job_record(job)
            except Exception:
                pass

        return {
            "ok": result.get("status") == "completed",
            "job_id": job_id,
            "status": result.get("status", "unknown"),
            "error": result.get("error", ""),
        }
