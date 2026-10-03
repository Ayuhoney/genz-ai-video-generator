"""Unit tests for SHOT_DURATION_SECONDS scene→shot splitting."""

from __future__ import annotations

from app.providers.fal.clip_timing import plan_shot_durations, shot_beat_description


def test_plan_shot_durations_15() -> None:
    assert plan_shot_durations(15, max_clip=5) == [5, 5, 5]


def test_plan_shot_durations_12() -> None:
    assert plan_shot_durations(12, max_clip=5) == [5, 5, 2]


def test_plan_shot_durations_7() -> None:
    assert plan_shot_durations(7, max_clip=5) == [5, 2]


def test_plan_shot_durations_5() -> None:
    assert plan_shot_durations(5, max_clip=5) == [5]


def test_plan_shot_durations_3() -> None:
    assert plan_shot_durations(3, max_clip=5) == [3]


def test_plan_shot_durations_sums_and_cap() -> None:
    for total in (10, 15, 45, 60, 64, 70, 80, 90, 120):
        durs = plan_shot_durations(total, max_clip=5)
        assert sum(durs) == total
        assert all(1 <= d <= 5 for d in durs)


def test_shot_beat_descriptions_are_distinct() -> None:
    texts = [shot_beat_description("hero finds diary", i, 3) for i in (1, 2, 3)]
    assert len(set(texts)) == 3
    assert texts[0].startswith("wide")
    assert texts[1].startswith("medium")
    assert texts[2].startswith("close-up")
