"""Story-order scene cursor + duration scaling."""

from __future__ import annotations

from app.orchestration.story_flow import (
    active_scene,
    next_active_scene_id,
    recommended_scene_count,
    scene_needs_images,
    scenes_to_produce,
    story_beats_remaining,
)
from app.orchestration.state import empty_workflow_state


def test_recommended_scene_count_scales() -> None:
    assert recommended_scene_count(30) <= 3
    assert recommended_scene_count(90) >= 3
    assert recommended_scene_count(600) >= 6
    assert recommended_scene_count(1800) >= 12
    assert recommended_scene_count(1800) <= 24


def test_story_cursor_one_scene_at_a_time() -> None:
    state = empty_workflow_state("p1")
    state["scenes"] = [
        {
            "id": "scene-1",
            "order": 1,
            "title": "A",
            "description": "a",
            "duration_seconds": 10,
            "status": "pending",
        },
        {
            "id": "scene-2",
            "order": 2,
            "title": "B",
            "description": "b",
            "duration_seconds": 10,
            "status": "pending",
        },
    ]
    state["shots"] = [
        {
            "id": "s1a",
            "scene_id": "scene-1",
            "order": 1,
            "title": "s1",
            "description": "d",
            "status": "pending",
            "duration_seconds": 5,
        },
        {
            "id": "s2a",
            "scene_id": "scene-2",
            "order": 1,
            "title": "s2",
            "description": "d",
            "status": "pending",
            "duration_seconds": 5,
        },
    ]
    assert active_scene(state)["id"] == "scene-1"
    assert scene_needs_images(state, "scene-1")
    assert story_beats_remaining(state)

    # After stills for scene-1:
    state["image_ready_shot_ids"] = ["s1a"]
    assert not scene_needs_images(state, "scene-1")

    # After clips for scene-1:
    state["shot_status"] = {"s1a": "completed"}
    state["shots"][0]["status"] = "completed"
    state["scene_status"] = {"scene-1": "completed"}
    state["scenes"][0]["status"] = "completed"
    assert next_active_scene_id(state) == "scene-2"
    assert [s["id"] for s in scenes_to_produce(state)] == ["scene-2"]
