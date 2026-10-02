"""Director duration + validation invariants (no Mongo required)."""

from __future__ import annotations

from app.providers.fal.clip_timing import plan_shot_durations
from app.schemas.director import DirectorGenerateRequest
from app.services.director_service import _mock_plan
from app.services.director_validate import validate_director_plan


def test_plan_shot_durations_exact_and_capped() -> None:
    for total in (10, 15, 45, 60, 64, 70, 80, 90, 120):
        durs = plan_shot_durations(total)
        assert sum(durs) == total
        assert all(5 <= d <= 15 for d in durs)


def test_mock_director_exact_duration_and_screenplay() -> None:
    payload = DirectorGenerateRequest(
        idea=(
            "A young Mumbai chaiwala finds a forgotten diary that predicts "
            "tomorrow newspaper headlines then prevents a Platform 3 accident."
        ),
        duration_seconds=60,
        language="Hindi",
        genre="Drama",
        instructions="Family-friendly",
    )
    plan = _mock_plan(payload)
    assert plan.estimated_duration_seconds == 60
    assert sum(s.duration_seconds for s in plan.scenes) == 60
    for scene in plan.scenes:
        assert int(round(sum(s.duration_seconds for s in scene.shots))) == scene.duration_seconds
        assert all(s.duration_seconds <= 15 for s in scene.shots)
    assert any(tok in plan.script.upper() for tok in ("INT.", "EXT.", "FADE"))
    assert all(not c.face_locked and not c.reference_image_url for c in plan.characters)
    assert "Drama" in plan.concept and "Hindi" in plan.concept

    # Re-validate is idempotent on duration.
    again = validate_director_plan(plan, payload)
    assert again.estimated_duration_seconds == 60


def test_mock_director_non_multiple_of_clip() -> None:
    payload = DirectorGenerateRequest(
        idea="A stormy harbor mystery with a radio operator",
        duration_seconds=80,
        language="English",
        genre="Drama",
        instructions="Keep it tense",
    )
    plan = _mock_plan(payload)
    assert plan.estimated_duration_seconds == 80
    assert sum(s.duration_seconds for s in plan.scenes) == 80
    assert max(s.duration_seconds for sc in plan.scenes for s in sc.shots) <= 15
