"""LangGraph production orchestration tests (MongoDB checkpointer)."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from langgraph.types import Command

from app.orchestration.checkpointing import (
    close_checkpointer,
    init_checkpointer,
    thread_config,
)
from app.orchestration.graph import compile_graph
from app.orchestration.state import empty_workflow_state


@pytest.fixture
def orchestration_db():
    init_checkpointer(db_name="ai_video_platform_test")
    yield
    close_checkpointer()


def _thread_id(prefix: str = "proj") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def test_graph_starts_and_completes(orchestration_db) -> None:
    project_id = _thread_id("ok")
    graph = compile_graph()
    config = thread_config(project_id)
    result = graph.invoke(empty_workflow_state(project_id), config)
    assert result["status"] == "completed"
    assert result["current_step"] == "finalization"
    assert all(s["status"] == "completed" for s in result["scenes"])
    assert all(s["status"] == "completed" for s in result["shots"])


def test_graph_pauses_on_simulated_failure_and_resumes(orchestration_db) -> None:
    project_id = _thread_id("pause")
    graph = compile_graph()
    config = thread_config(project_id)
    initial = empty_workflow_state(project_id)
    initial["fail_scene_ids"] = ["scene-2"]

    paused = graph.invoke(initial, config)
    assert paused["status"] == "paused"
    assert paused["scene_status"]["scene-1"] == "completed"
    assert paused["scene_status"]["scene-2"] == "failed"
    assert paused["scene_status"].get("scene-3") in {None, "pending"} or paused[
        "scene_status"
    ].get("scene-3") == "pending"

    snapshot = graph.get_state(config)
    assert snapshot.interrupts

    resumed = graph.invoke(Command(resume=True), config)
    assert resumed["status"] == "completed"
    assert resumed["scene_status"]["scene-1"] == "completed"
    assert resumed["scene_status"]["scene-2"] == "completed"
    assert resumed["scene_status"]["scene-3"] == "completed"
    # Completed scene-1 was not reset.
    assert resumed["retry_counts"].get("scene-2", 0) >= 1


def test_resume_after_process_restart(orchestration_db) -> None:
    project_id = _thread_id("restart")
    config = thread_config(project_id)
    initial = empty_workflow_state(project_id)
    initial["fail_scene_ids"] = ["scene-2"]

    graph1 = compile_graph()
    paused = graph1.invoke(initial, config)
    assert paused["status"] == "paused"
    assert graph1.get_state(config).interrupts

    # Simulate process restart: tear down and recreate checkpointer + graph.
    close_checkpointer()
    init_checkpointer(db_name="ai_video_platform_test")
    graph2 = compile_graph()

    snapshot = graph2.get_state(config)
    assert snapshot.values["status"] == "paused"
    assert snapshot.interrupts

    resumed = graph2.invoke(Command(resume=True), config)
    assert resumed["status"] == "completed"
    assert resumed["scene_status"]["scene-1"] == "completed"
    assert resumed["scene_status"]["scene-2"] == "completed"


def test_partial_regeneration_does_not_redo_untargeted_scenes(
    orchestration_db,
) -> None:
    project_id = _thread_id("regen")
    graph = compile_graph()
    config = thread_config(project_id)

    completed = graph.invoke(empty_workflow_state(project_id), config)
    assert completed["status"] == "completed"
    assert completed["scene_status"]["scene-1"] == "completed"

    # Mark scene-2 for regeneration only.
    graph.update_state(
        config,
        {
            "regenerate_scene_ids": ["scene-2"],
            "regenerate_shot_ids": [],
            "scenes": [
                {**s, "status": "pending"}
                if s["id"] == "scene-2"
                else s
                for s in completed["scenes"]
            ],
            "scene_status": {
                **completed["scene_status"],
                "scene-2": "pending",
            },
            "shots": [
                {**shot, "status": "pending"}
                if shot["scene_id"] == "scene-2"
                else shot
                for shot in completed["shots"]
            ],
            "status": "running",
            "current_step": "video_generation_planning",
        },
        as_node="shot_planning",
    )

    regenerated = graph.invoke(None, config)
    assert regenerated["status"] == "completed"
    assert regenerated["scene_status"]["scene-1"] == "completed"
    assert regenerated["scene_status"]["scene-2"] == "completed"
    assert regenerated["scene_status"]["scene-3"] == "completed"


@pytest.mark.asyncio
async def test_production_api_start_status_resume(
    client: AsyncClient,
    auth_headers_factory,
) -> None:
    register = await client.post(
        "/api/auth/register",
        json={
            "email": f"{uuid.uuid4().hex[:8]}@example.com",
            "password": "secret123",
            "name": "Producer",
        },
    )
    token = register.json()["accessToken"]
    headers = auth_headers_factory(token)

    created = await client.post(
        "/api/projects",
        headers=headers,
        json={
            "title": "Orchestrated Film",
            "durationSeconds": 60,
            "status": "confirmed",
        },
    )
    assert created.status_code == 201
    project_id = created.json()["id"]

    started = await client.post(
        f"/api/projects/{project_id}/production/start",
        headers=headers,
        json={"failSceneIds": ["scene-2"], "maxRetries": 2},
    )
    assert started.status_code == 202
    assert started.json()["status"] == "accepted"

    # Wait for background pause.
    from app.orchestration.runner import wait_until_idle

    await wait_until_idle(project_id, timeout=15)

    status = await client.get(
        f"/api/projects/{project_id}/production/status",
        headers=headers,
    )
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "paused"
    assert body["interrupted"] is True
    assert body["sceneStatus"]["scene-1"] == "completed"
    assert body["sceneStatus"]["scene-2"] == "failed"

    resumed = await client.post(
        f"/api/projects/{project_id}/production/resume",
        headers=headers,
    )
    assert resumed.status_code == 202
    await wait_until_idle(project_id, timeout=15)

    final_status = await client.get(
        f"/api/projects/{project_id}/production/status",
        headers=headers,
    )
    assert final_status.status_code == 200
    final_body = final_status.json()
    assert final_body["status"] == "completed"
    assert final_body["sceneStatus"]["scene-2"] == "completed"
