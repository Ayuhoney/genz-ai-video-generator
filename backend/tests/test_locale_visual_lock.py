"""Locale lock + cast selection for Indian stories."""

from __future__ import annotations

from app.workers import project_context as pc


def test_locale_lock_detects_mumbai(monkeypatch) -> None:
    monkeypatch.setattr(
        pc,
        "get_project_doc",
        lambda _pid: {
            "language": "Hindi",
            "title": "मुंबई डायमंड चोरी ब्रेकिंग न्यूज़",
            "idea": "Mumbai jewellery robbery news report",
        },
    )
    lock = pc.locale_visual_lock("p1")
    assert "Mumbai" in lock
    assert "Indian" in lock
    assert "Chinese" in lock  # explicit negative constraint


def test_cctv_broll_does_not_force_cast(monkeypatch) -> None:
    monkeypatch.setattr(
        pc,
        "list_characters",
        lambda _pid: [
            {
                "id": "char-1",
                "name": "सुष्मिता मेहता",
                "description": "महिला एंकर",
                "role": "News Anchor",
            },
            {
                "id": "char-2",
                "name": "राजेश कुमार",
                "description": "पुरुष रिपोर्टर",
                "role": "Field Reporter",
            },
        ],
    )
    monkeypatch.setattr(pc, "get_project_doc", lambda _pid: {"director_response": {}})
    present = pc.characters_present_in_shot(
        "p1",
        shot={
            "title": "Grainy CCTV",
            "description": "black-and-white CCTV with police cars",
            "camera": "static",
        },
        scene_id="scene-2",
    )
    assert present == []


def test_reporter_role_keyword_selects_reporter(monkeypatch) -> None:
    monkeypatch.setattr(
        pc,
        "list_characters",
        lambda _pid: [
            {
                "id": "char-1",
                "name": "सुष्मिता मेहता",
                "description": "महिला एंकर",
                "role": "News Anchor",
            },
            {
                "id": "char-2",
                "name": "राजेश कुमार",
                "description": "पुरुष रिपोर्टर",
                "role": "Field Reporter",
            },
        ],
    )
    present = pc.characters_present_in_shot(
        "p1",
        shot={
            "title": "Reporter on location",
            "description": "Reporter under LED light holding mic",
            "camera": "handheld",
        },
    )
    assert len(present) == 1
    assert present[0]["id"] == "char-2"
