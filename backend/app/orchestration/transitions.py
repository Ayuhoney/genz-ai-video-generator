from __future__ import annotations

from typing import Literal

from app.orchestration.state import WorkflowState

Route = Literal[
    "continue",
    "pause_gate",
    "retry_asset",
    "retry_video",
    "retry_audio",
    "retry_assembly",
    "end_failed",
]


def _after_exec_step(state: WorkflowState) -> Route:
    status = state.get("status")
    if status == "failed":
        return "end_failed"
    if status == "paused":
        return "pause_gate"
    return "continue"


def after_asset_planning(state: WorkflowState) -> Route:
    return _after_exec_step(state)


def after_video_planning(state: WorkflowState) -> Route:
    return _after_exec_step(state)


def after_audio_planning(state: WorkflowState) -> Route:
    return _after_exec_step(state)


def after_assembly_planning(state: WorkflowState) -> Route:
    return _after_exec_step(state)


def after_pause_gate(state: WorkflowState) -> Route:
    if state.get("status") == "failed":
        return "end_failed"
    step = state.get("current_step") or ""
    if step == "asset_planning":
        return "retry_asset"
    if step == "audio_planning":
        return "retry_audio"
    if step == "assembly_planning":
        return "retry_assembly"
    return "retry_video"
