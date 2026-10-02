from app.schemas.director import (
    Character,
    DirectorGenerateRequest,
    DirectorResponse,
    Scene,
)


async def generate_director_response(
    payload: DirectorGenerateRequest,
) -> DirectorResponse:
    """Return mock structured director data matching the frontend DirectorResponse."""
    base_scenes = [
        (
            "Harbor at Dusk",
            "Wide shot of boats rocking as fog rolls in. The protagonist starts a checklist.",
        ),
        (
            "First Crackles",
            "Static interrupts a routine bulletin. A faint voice emerges from the noise.",
        ),
        (
            "Rising Doubt",
            "Conflicting advice arrives as weather and stakes intensify.",
        ),
        (
            "The Choice",
            "Against protocol, the protagonist answers the signal. Lights flicker.",
        ),
        (
            "Clearing Skies",
            "Morning light breaks. An unexpected arrival validates the risk — with lingering mystery.",
        ),
    ]

    scene_count = len(base_scenes)
    base = max(1, payload.duration_seconds // scene_count)
    remaining = payload.duration_seconds - base * scene_count

    scenes: list[Scene] = []
    for index, (title, description) in enumerate(base_scenes, start=1):
        extra = 1 if remaining > 0 else 0
        if remaining > 0:
            remaining -= 1
        scenes.append(
            Scene(
                id=f"scene-{index}",
                order=index,
                title=f"{title} — {payload.genre}" if index == 1 else title,
                description=description,
                duration_seconds=base + extra,
                status="pending",
            )
        )

    instructions_note = (
        f" Instructions noted: {payload.instructions.strip()}"
        if payload.instructions.strip()
        else ""
    )

    return DirectorResponse(
        title=f"Signal in the Fog ({payload.language})",
        concept=(
            f"Based on your idea — {payload.idea.strip()} — a coastal operator receives "
            f"a mysterious signal during a storm and must decide whether to trust it."
            f"{instructions_note}"
        ),
        characters=[
            Character(
                id="char-1",
                name="Mara Ellison",
                role="Protagonist",
                description="Night-shift radio operator; calm under pressure, quietly curious.",
            ),
            Character(
                id="char-2",
                name="Jonah Price",
                role="Supporting",
                description="Harbor dispatcher who urges caution and wants a quiet night.",
            ),
            Character(
                id="char-3",
                name="The Signal",
                role="Antagonist / Mystery",
                description="An unidentified voice that knows too much about tomorrow’s weather.",
            ),
        ],
        story_structure=[
            "Setup — introduce the stormy harbor and routine",
            "Inciting signal — the first unusual transmission arrives",
            "Rising tension — conflicting advice and rising stakes",
            "Climax — the protagonist chooses to act on the signal",
            "Resolution — the fog clears and consequences settle",
        ],
        scenes=scenes,
        estimated_duration_seconds=payload.duration_seconds,
    )
