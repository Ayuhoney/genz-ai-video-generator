from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

WorkflowStatus = Literal[
    "pending",
    "running",
    "paused",
    "completed",
    "failed",
]

StepName = Literal[
    "director_planning",
    "scene_planning",
    "shot_planning",
    "asset_planning",
    "video_generation_planning",
    "audio_planning",
    "assembly_planning",
    "finalization",
]

ItemStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
]

PIPELINE_STEPS: tuple[StepName, ...] = (
    "director_planning",
    "scene_planning",
    "shot_planning",
    "asset_planning",
    "video_generation_planning",
    "audio_planning",
    "assembly_planning",
    "finalization",
)


class SceneState(TypedDict):
    id: str
    order: int
    title: str
    description: str
    duration_seconds: int
    status: ItemStatus
    # Spoken narration for TTS (target language). Visual description stays English.
    narration: NotRequired[str]
    voice_over: NotRequired[list[dict[str, Any]]]


class ShotState(TypedDict):
    id: str
    scene_id: str
    order: int
    title: str
    description: str
    status: ItemStatus
    duration_seconds: NotRequired[float]


class WorkflowError(TypedDict):
    step: str
    item_id: NotRequired[str]
    message: str


class WorkflowState(TypedDict, total=False):
    project_id: str
    status: WorkflowStatus
    current_step: StepName | Literal[""]
    scenes: list[SceneState]
    shots: list[ShotState]
    scene_status: dict[str, ItemStatus]
    shot_status: dict[str, ItemStatus]
    retry_counts: dict[str, int]
    errors: list[WorkflowError]
    max_retries: int
    # Simulated failures for tests: scene ids that should fail until retried.
    fail_scene_ids: list[str]
    fail_shot_ids: list[str]
    # Partial regeneration targets.
    regenerate_scene_ids: list[str]
    regenerate_shot_ids: list[str]
    director_plan: dict[str, Any]
    pause_reason: str
    pending_job_ids: list[str]
    awaiting_jobs: bool
    # Unique token so regenerate creates new Celery jobs (breaks idempotency reuse).
    regen_token: NotRequired[str]


def empty_workflow_state(project_id: str, *, max_retries: int = 2) -> WorkflowState:
    return {
        "project_id": project_id,
        "status": "pending",
        "current_step": "",
        "scenes": [],
        "shots": [],
        "scene_status": {},
        "shot_status": {},
        "retry_counts": {},
        "errors": [],
        "max_retries": max_retries,
        "fail_scene_ids": [],
        "fail_shot_ids": [],
        "regenerate_scene_ids": [],
        "regenerate_shot_ids": [],
        "director_plan": {},
        "pause_reason": "",
        "pending_job_ids": [],
        "awaiting_jobs": False,
    }
