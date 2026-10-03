"""Validate and normalize AI Director plans before FE / production."""

from __future__ import annotations

from typing import Any

from app.providers.fal.clip_timing import (
    FAL_WAN_MAX_CLIP_SECONDS,
    clamp_clip_seconds,
    plan_shot_durations,
)
from app.schemas.director import (
    Character,
    DirectorGenerateRequest,
    DirectorResponse,
    Scene,
    ShotPlan,
    VoiceLine,
)


def _clip_default() -> int:
    from app.providers.fal.clip_timing import (
        DEFAULT_CLIP_SECONDS,
        clip_seconds_from_settings,
    )

    try:
        from app.core.config import get_settings

        return clip_seconds_from_settings(get_settings())
    except Exception:
        pass
    try:
        from app.workers.settings import get_worker_settings

        return clip_seconds_from_settings(get_worker_settings())
    except Exception:
        return DEFAULT_CLIP_SECONDS


def _is_cinematic_script(script: str) -> bool:
    text = (script or "").strip()
    if len(text) < 280:
        return False
    upper = text.upper()
    has_heading = any(token in upper for token in ("INT.", "EXT.", "INT ", "EXT ", "SCENE "))
    has_dialogue = ":" in text or "\n" in text
    return has_heading and has_dialogue


def build_cinematic_screenplay(
    *,
    title: str,
    genre: str,
    language: str,
    idea: str,
    characters: list[Character],
    scenes: list[Scene],
) -> str:
    cast = ", ".join(f"{c.name} ({c.role})" for c in characters) or "Ensemble"
    lines = [
        f"TITLE: {title}",
        f"GENRE: {genre}",
        f"LANGUAGE: {language}",
        f"LOG LINE: {idea.strip()}",
        f"CAST: {cast}",
        "",
        "FADE IN:",
        "",
    ]
    for scene in scenes:
        heading = scene.title.strip() or f"Scene {scene.order}"
        if not heading.upper().startswith(("INT.", "EXT.", "INT ", "EXT ")):
            heading = f"INT./EXT. {heading.upper()} — {genre.upper()}"
        lines.append(heading)
        lines.append("")
        action = scene.description.strip() or heading
        lines.append(action)
        lines.append("")
        for shot in scene.shots:
            cam = f" ({shot.camera})" if shot.camera else ""
            lines.append(f"SHOT {shot.order}{cam}: {shot.description}".strip())
        if scene.sfx_notes:
            lines.append(f"SFX: {scene.sfx_notes}")
        lines.append("")
        for vo in scene.voice_over:
            name = (vo.character_name or "NARRATOR").upper()
            lines.append(f"{name}")
            lines.append(f"    {vo.text}")
            lines.append("")
        lines.append("")
    lines.append("FADE OUT.")
    lines.append("")
    lines.append("THE END")
    return "\n".join(lines).strip()


def _flatten_shots(scenes: list[Scene]) -> list[tuple[Scene, ShotPlan]]:
    pairs: list[tuple[Scene, ShotPlan]] = []
    for scene in scenes:
        for shot in scene.shots:
            pairs.append((scene, shot))
    return pairs


def _ensure_shot_shells(
    scenes: list[Scene],
    needed: int,
    *,
    clip: int,
) -> list[Scene]:
    """Grow/shrink shot list to match required duration slots while keeping story."""
    scenes = [Scene.model_validate(s.model_dump(by_alias=True)) for s in scenes]
    if not scenes:
        return scenes

    pairs = _flatten_shots(scenes)
    if not pairs:
        # One shot per scene as a starting shell.
        for scene in scenes:
            scene.shots = [
                ShotPlan(
                    id=f"{scene.id}-shot-1",
                    order=1,
                    title="Establish",
                    description=scene.description or scene.title,
                    duration_seconds=float(clip),
                    camera="Wide",
                )
            ]
        pairs = _flatten_shots(scenes)

    while len(pairs) < needed:
        # Clone coverage from the last scene.
        scene = scenes[-1]
        order = len(scene.shots) + 1
        base = scene.shots[-1] if scene.shots else None
        scene.shots.append(
            ShotPlan(
                id=f"{scene.id}-shot-{order}",
                order=order,
                title=f"{scene.title} — Coverage {order}",
                description=(
                    f"Closer coverage: {base.description}"
                    if base
                    else scene.description or scene.title
                ),
                duration_seconds=float(clip),
                camera="Medium / subtle move" if order > 1 else "Wide",
            )
        )
        pairs = _flatten_shots(scenes)

    if len(pairs) > needed:
        # Drop trailing shots, preferring to keep at least one shot per scene.
        extra = len(pairs) - needed
        for scene in reversed(scenes):
            while extra > 0 and len(scene.shots) > 1:
                scene.shots.pop()
                extra -= 1
            if extra <= 0:
                break
        if extra > 0:
            # Still too many: remove empty trailing scenes' extra only.
            while extra > 0 and scenes:
                scene = scenes[-1]
                if len(scene.shots) > 1:
                    scene.shots.pop()
                    extra -= 1
                elif len(scenes) > 1 and len(scene.shots) == 1:
                    # Merge last scene beat into previous description, drop scene.
                    prev = scenes[-2]
                    prev.description = (
                        f"{prev.description} Later: {scene.description}"
                    ).strip()
                    scenes.pop()
                    extra -= 1
                else:
                    break
    return scenes


def apply_exact_durations(
    scenes: list[Scene],
    target_seconds: int,
    *,
    clip: int | None = None,
) -> list[Scene]:
    clip = clip or _clip_default()
    target = max(10, int(target_seconds))
    durations = plan_shot_durations(target, max_clip=clip)
    scenes = _ensure_shot_shells(scenes, len(durations), clip=clip)
    pairs = _flatten_shots(scenes)

    if len(pairs) != len(durations):
        # Rebuild an exact plan sized to the narrative shot count.
        n = max(1, len(pairs))
        durations = []
        base = target // n
        extra = target % n
        for i in range(n):
            durations.append(clamp_clip_seconds(base + (1 if i < extra else 0), default=clip))
        diff = target - sum(durations)
        guard = 0
        while diff != 0 and durations and guard < target * 3:
            idx = guard % len(durations)
            if diff > 0 and durations[idx] < clip:
                durations[idx] += 1
                diff -= 1
            elif diff < 0 and durations[idx] > 1:
                durations[idx] -= 1
                diff += 1
            guard += 1
        if sum(durations) != target:
            durations = plan_shot_durations(target, max_clip=clip)
            scenes = _ensure_shot_shells(scenes, len(durations), clip=clip)
            pairs = _flatten_shots(scenes)

    flat_idx = 0
    for scene in scenes:
        for shot in scene.shots:
            shot.duration_seconds = float(durations[flat_idx])
            flat_idx += 1
        scene.duration_seconds = int(round(sum(s.duration_seconds for s in scene.shots)))
        if not scene.sfx_notes:
            scene.sfx_notes = "Soft ambient bed, no speech"
    return scenes


def normalize_characters(characters: list[Character]) -> list[Character]:
    out: list[Character] = []
    for index, char in enumerate(characters, start=1):
        url = (char.reference_image_url or "").strip() or None
        out.append(
            Character(
                id=char.id or f"char-{index}",
                name=char.name or f"Character {index}",
                role=char.role or "Cast",
                description=char.description or "",
                reference_image_url=url,
                face_locked=bool(url),
            )
        )
    return out


def validate_director_plan(
    plan: DirectorResponse,
    payload: DirectorGenerateRequest,
) -> DirectorResponse:
    """Enforce production invariants on a director plan."""
    clip = _clip_default()
    target = max(10, int(payload.duration_seconds))
    characters = normalize_characters(plan.characters)
    if not characters:
        characters = [
            Character(
                id="char-1",
                name="Lead",
                role="Protagonist",
                description="Central character grounded in the user's idea.",
            )
        ]

    scenes = list(plan.scenes or [])
    if not scenes:
        raise ValueError("Director plan has no scenes")

    # Clamp any wild shot lengths before redistribution.
    for scene in scenes:
        for shot in scene.shots:
            shot.duration_seconds = float(
                clamp_clip_seconds(shot.duration_seconds, default=clip)
            )
        if not scene.shots:
            scene.shots = [
                ShotPlan(
                    id=f"{scene.id}-shot-1",
                    order=1,
                    title="Establish",
                    description=scene.description or scene.title,
                    duration_seconds=float(clip),
                    camera="Wide",
                )
            ]
        # Ensure voice continuity for production-ready dialogue.
        if not scene.voice_over:
            from app.workers.project_context import fallback_spoken_line

            lead = characters[0]
            scene.voice_over = [
                VoiceLine(
                    id=f"{scene.id}-vo-1",
                    character_id=lead.id,
                    character_name=lead.name,
                    text=fallback_spoken_line(
                        language=payload.language,
                        scene_title=scene.title or scene.id,
                    ),
                    estimated_seconds=3.0,
                )
            ]

    scenes = apply_exact_durations(scenes, target, clip=clip)

    total = sum(s.duration_seconds for s in scenes)
    if total != target:
        # Absolute guarantee: rebuild from flat duration plan + scene shells.
        durations = plan_shot_durations(target, max_clip=clip)
        # Map durations onto existing narrative order.
        scenes = apply_exact_durations(scenes, target, clip=clip)
        total = sum(s.duration_seconds for s in scenes)

    for scene in scenes:
        shot_sum = int(round(sum(s.duration_seconds for s in scene.shots)))
        scene.duration_seconds = shot_sum
        for shot in scene.shots:
            if shot.duration_seconds > FAL_WAN_MAX_CLIP_SECONDS:
                shot.duration_seconds = float(FAL_WAN_MAX_CLIP_SECONDS)

    total = sum(s.duration_seconds for s in scenes)
    if total != target:
        raise ValueError(
            f"Director duration mismatch: planned {total}s != requested {target}s"
        )

    title = (plan.title or "").strip() or "Untitled"
    concept = (plan.concept or "").strip() or payload.idea.strip()
    if payload.genre.lower() not in concept.lower():
        concept = f"{concept} Genre: {payload.genre}."
    if payload.language.lower() not in concept.lower():
        concept = f"{concept} Language: {payload.language}."

    script = (plan.script or "").strip()
    if not _is_cinematic_script(script):
        script = build_cinematic_screenplay(
            title=title,
            genre=payload.genre,
            language=payload.language,
            idea=payload.idea,
            characters=characters,
            scenes=scenes,
        )

    structure = [str(b).strip() for b in (plan.story_structure or []) if str(b).strip()]
    if len(structure) < len(scenes):
        structure = [s.title for s in scenes]

    # Per-scene shot sum invariant
    for scene in scenes:
        if int(round(sum(s.duration_seconds for s in scene.shots))) != scene.duration_seconds:
            scene.duration_seconds = int(
                round(sum(s.duration_seconds for s in scene.shots))
            )

    return DirectorResponse(
        title=title,
        concept=concept,
        script=script,
        characters=characters,
        story_structure=structure,
        scenes=scenes,
        estimated_duration_seconds=target,
    )


def director_plan_from_dict(
    data: dict[str, Any],
    payload: DirectorGenerateRequest,
) -> DirectorResponse:
    """Parse loose AI/FE JSON then validate."""
    raw = DirectorResponse.model_validate(
        {
            "title": data.get("title") or "Untitled",
            "concept": data.get("concept") or payload.idea,
            "script": data.get("script") or "",
            "characters": data.get("characters") or [],
            "storyStructure": data.get("storyStructure")
            or data.get("story_structure")
            or [],
            "scenes": data.get("scenes") or [],
            "estimatedDurationSeconds": data.get("estimatedDurationSeconds")
            or data.get("estimated_duration_seconds")
            or payload.duration_seconds,
        }
    )
    return validate_director_plan(raw, payload)
