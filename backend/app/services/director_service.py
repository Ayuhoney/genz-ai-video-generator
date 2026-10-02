"""Director plan generation (Groq primary + idea-aware mock fallback)."""

from __future__ import annotations

import re

from app.orchestration.director_ai import director_chat, groq_enabled, _parse_json
from app.providers.fal.clip_timing import (
    DEFAULT_CLIP_SECONDS,
    clamp_clip_seconds,
    clip_seconds_from_settings,
    plan_shot_count,
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
from app.services.director_validate import (
    build_cinematic_screenplay,
    validate_director_plan,
)

GENERATE_SYS = """You are an expert film director and screenwriter for an AI video pipeline.
Write a NEW screenplay that matches the user's idea EXACTLY — do not reuse unrelated stock stories.
Ground every scene in concrete nouns from the user's idea (places, props, jobs, relationships).
The `script` field MUST be a proper cinematic screenplay (not a shot list): use INT./EXT. scene
headings, present-tense action lines, character cues, and dialogue in the TARGET LANGUAGE.
Mention the selected GENRE in the concept. Story must have a clear beginning, middle, and ending.
Each SHOT becomes one fal image-to-video clip. Every shot durationSeconds MUST be 5-15 (prefer 15).
Sum of ALL shot durations MUST equal the target total duration exactly.
Return ONLY valid JSON (no markdown):
{"title": str,
 "concept": "1-3 sentences including genre + language",
 "script": "full cinematic screenplay with INT./EXT. headings, action, and dialogue",
 "storyStructure": ["beat 1", ...],
 "characters": [{"id": "char-1", "name": str, "role": str, "description": "look + personality 20-40 words"}],
 "scenes": [{"id": "scene-1", "order": 1, "title": str,
   "description": "what we SEE (family-friendly, no gore)",
   "durationSeconds": number (sum of its shots),
   "sfxNotes": "ambient sounds, no speech",
   "voiceOver": [{"id": "vo-1", "characterId": "char-1 or null", "characterName": str, "text": "spoken line in target language", "estimatedSeconds": number}],
   "shots": [{"id": "shot-1", "order": 1, "title": str, "description": str, "durationSeconds": 15, "camera": "angle/move"}]}]}
Rules: follow the user's idea; each shot durationSeconds <= 15; 1-4 shots per scene;
shot durations must sum to the target duration; dialogue ONLY in the target language;
cinematic continuity across scenes; no gore/blood/nudity; never use double quotes inside string values;
do NOT set faceLocked or referenceImageUrl (face lock is user-uploaded later)."""

def _clip_len() -> int:
    try:
        from app.core.config import get_settings

        return clip_seconds_from_settings(get_settings())
    except Exception:
        return DEFAULT_CLIP_SECONDS


def _short_title(idea: str, language: str) -> str:
    words = re.findall(r"[A-Za-z\u0900-\u097F0-9]+", idea)
    if len(words) >= 4:
        return " ".join(words[:5]).title()
    if words:
        return " ".join(words[:3]).title()
    return f"Short Film ({language})"


def _beat_plan(idea: str, n_scenes: int) -> list[tuple[str, str, str, str, str, int]]:
    """Return (title, action, sfx, line, camera, character_index) beats grounded in the idea."""
    idea_l = idea.lower()
    if any(k in idea_l for k in ("chai", "railway", "diary", "platform", "mumbai", "train")):
        pool: list[tuple[str, str, str, str, str, int]] = [
            (
                "Forgotten Diary",
                "Evening rush at a Mumbai railway chai stall. A customer leaves; a worn diary slips from their bag onto the counter.",
                "Train whistle, crowd murmur, kettle hiss",
                "Arre, diary reh gaya…",
                "Wide / push toward stall",
                0,
            ),
            (
                "Impossible Headline",
                "Late night in a small room. The chaiwala opens one page — tomorrow's newspaper headline is already written.",
                "Ceiling fan, distant traffic, page turn",
                "Kal ki khabar… aaj kaise?",
                "Close-up diary / soft lamp",
                0,
            ),
            (
                "It Comes True",
                "Next morning at a newspaper stand. The printed headline matches the diary page exactly.",
                "City morning, paper rustle, scooters",
                "Sach ho gaya…",
                "Over-shoulder newspaper",
                0,
            ),
            (
                "Platform Warning",
                "He rushes to Platform 3, following the next diary line, watching for a small accident he can prevent.",
                "Platform announcements, footsteps, soft rain",
                "Platform teen… abhi rokna hai.",
                "Handheld follow through crowd",
                0,
            ),
            (
                "Quiet Rescue",
                "He gently warns a passenger in time. No one is hurt. He returns the diary without asking for thanks.",
                "Soft rain on tracks, relieved crowd, warm light",
                "Lo, yeh tumhari diary. Bas… safe raho.",
                "Medium two-shot / hopeful dawn",
                0,
            ),
        ]
    elif any(k in idea_l for k in ("harbor", "fog", "radio", "storm", "signal")):
        pool = [
            (
                "Harbor Dusk",
                "Fog over the harbor. A radio operator begins the night checklist.",
                "Ocean wind, buoy bell",
                "Checklist complete.",
                "Wide / slow push-in",
                0,
            ),
            (
                "Strange Signal",
                "Static interrupts a routine bulletin. A faint voice emerges.",
                "Radio static, soft rain",
                "Koi hai wahan?",
                "Medium / handheld",
                0,
            ),
            (
                "Doubt",
                "Conflicting advice arrives as weather worsens.",
                "Wind rising, radio chatter",
                "Protocol ke against hai…",
                "Over-shoulder",
                1,
            ),
            (
                "The Reply",
                "The operator answers the signal. Lights flicker softly.",
                "Power hum, rain on glass",
                "Main sun raha hoon.",
                "Close-up / gentle push",
                0,
            ),
            (
                "Morning Clear",
                "Morning light. An unexpected safe arrival validates the risk.",
                "Seagulls, soft waves",
                "Fog clear… mystery remains.",
                "Wide / hopeful",
                0,
            ),
        ]
    else:
        seed = idea.strip()
        if len(seed) > 140:
            seed = seed[:137] + "..."
        pool = [
            (
                "Opening",
                f"Establish the world of the story: {seed}",
                "Soft ambient bed",
                "Shuruaat yahin se hoti hai.",
                "Wide establish",
                0,
            ),
            (
                "Discovery",
                "The protagonist notices the key detail that changes everything.",
                "Subtle tension riser",
                "Yeh… normal nahi hai.",
                "Medium / detail insert",
                0,
            ),
            (
                "Rising Stakes",
                "Pressure builds as the next choice becomes unavoidable.",
                "City or room ambience",
                "Ab sochne ka time nahi.",
                "Handheld follow",
                0,
            ),
            (
                "Decision",
                "The protagonist acts — careful, hopeful, without harm.",
                "Heartbeat-soft percussion, no violence",
                "Main sahi karunga.",
                "Close emotional",
                0,
            ),
            (
                "Resolution",
                "A quiet hopeful ending that resolves the idea with dignity.",
                "Warm morning bed",
                "Bas itna kaafi hai.",
                "Wide / soft light",
                0,
            ),
        ]

    n = max(1, n_scenes)
    if n >= len(pool):
        return pool[:n] if n <= len(pool) else [pool[i % len(pool)] for i in range(n)]

    # Evenly sample across the full arc so short films still get beginning + end.
    if n == 1:
        return [pool[0]]
    indexes = sorted({int(round(i * (len(pool) - 1) / (n - 1))) for i in range(n)})
    while len(indexes) < n:
        for j in range(len(pool)):
            if j not in indexes:
                indexes.append(j)
            if len(indexes) >= n:
                break
        indexes = sorted(indexes)
    return [pool[i] for i in indexes[:n]]


def _mock_plan(payload: DirectorGenerateRequest) -> DirectorResponse:
    clip = _clip_len()
    idea = payload.idea.strip()
    total = max(10, int(payload.duration_seconds))
    shot_durs = plan_shot_durations(total, max_clip=clip)
    n_shots = len(shot_durs)
    shots_per_scene = 1 if n_shots <= 6 else 2
    n_scenes = max(2, (n_shots + shots_per_scene - 1) // shots_per_scene)
    beats = _beat_plan(idea, n_scenes)

    idea_l = idea.lower()
    if any(k in idea_l for k in ("chai", "railway", "diary", "mumbai", "train")):
        characters = [
            Character(
                id="char-1",
                name="Aarav",
                role="Protagonist",
                description="Young Mumbai chaiwala, early 20s, kind eyes, stained apron, curious and brave.",
            ),
            Character(
                id="char-2",
                name="Diary Owner",
                role="Supporting",
                description="Middle-aged passenger in a simple shirt, hurried, forgetful, warm face.",
            ),
            Character(
                id="char-3",
                name="Platform Worker",
                role="Supporting",
                description="Railway staff in uniform, alert, helps keep Platform 3 safe.",
            ),
        ]
        title = f"Kal Ki Diary ({payload.language})"
    else:
        characters = [
            Character(
                id="char-1",
                name="Lead",
                role="Protagonist",
                description=(
                    f"Central {payload.genre} protagonist matching the user's idea; "
                    "expressive, cinematic presence."
                ),
            ),
            Character(
                id="char-2",
                name="Ally",
                role="Supporting",
                description="Supports the lead through the story beat with quiet strength.",
            ),
        ]
        title = f"{_short_title(idea, payload.language)}"

    # Distribute exact shot durations across scenes.
    scene_shot_counts: list[int] = []
    remaining = n_shots
    for scene_i in range(1, n_scenes + 1):
        remaining_scenes = n_scenes - scene_i + 1
        take = max(
            1,
            min(
                shots_per_scene,
                (remaining + remaining_scenes - 1) // remaining_scenes,
            ),
        )
        take = min(take, remaining) if remaining else 1
        scene_shot_counts.append(take)
        remaining -= take

    scenes: list[Scene] = []
    cursor = 0
    for scene_i, beat in enumerate(beats, start=1):
        b_title, description, sfx, line, camera, speaker_idx = beat
        sid = f"scene-{scene_i}"
        take = scene_shot_counts[scene_i - 1] if scene_i - 1 < len(scene_shot_counts) else 1
        chunk = shot_durs[cursor : cursor + take]
        cursor += take
        if not chunk:
            chunk = [clip]

        shots: list[ShotPlan] = []
        for s_i, dur in enumerate(chunk, start=1):
            label = "Establish" if s_i == 1 else f"Beat {s_i}"
            shots.append(
                ShotPlan(
                    id=f"{sid}-shot-{s_i}",
                    order=s_i,
                    title=f"{b_title} — {label}",
                    description=description if s_i == 1 else f"Closer coverage: {description}",
                    duration_seconds=float(dur),
                    camera=camera if s_i == 1 else "Medium / subtle move",
                )
            )

        scene_dur = int(sum(chunk))
        who = characters[min(max(0, speaker_idx), len(characters) - 1)]
        # Keep dialogue language-aware for mock continuity.
        spoken = line
        if payload.language.lower().startswith("english") and any(
            ch in line for ch in ("…", "Kal", "Arre", "Yeh", "Main", "Bas")
        ):
            spoken = f"({payload.language}) {line}"
        voice = [
            VoiceLine(
                id=f"{sid}-vo-1",
                character_id=who.id,
                character_name=who.name,
                text=spoken,
                estimated_seconds=min(8.0, float(chunk[0]) * 0.4),
            )
        ]
        scenes.append(
            Scene(
                id=sid,
                order=scene_i,
                title=f"{b_title} — {payload.genre}" if scene_i == 1 else b_title,
                description=description,
                duration_seconds=scene_dur,
                status="pending",
                shots=shots,
                voice_over=voice,
                sfx_notes=sfx,
            )
        )

    note = (
        f" Extra notes: {payload.instructions.strip()}"
        if payload.instructions.strip()
        else ""
    )
    script = build_cinematic_screenplay(
        title=title,
        genre=payload.genre,
        language=payload.language,
        idea=idea,
        characters=characters,
        scenes=scenes,
    )
    plan = DirectorResponse(
        title=title,
        concept=(
            f"{idea} Genre: {payload.genre}. Language: {payload.language}."
            f" Soft cinematic treatment.{note}"
        ),
        script=script,
        characters=characters,
        story_structure=[b[0] for b in beats],
        scenes=scenes,
        estimated_duration_seconds=total,
    )
    return validate_director_plan(plan, payload)


def _normalize_shots(
    sid: str,
    sc: dict,
    *,
    clip: int,
    fallback_desc: str,
) -> tuple[list[ShotPlan], int]:
    shots_raw = sc.get("shots") or []
    shots: list[ShotPlan] = []
    if shots_raw:
        for s_i, sh in enumerate(shots_raw[:6], start=1):
            shots.append(
                ShotPlan(
                    id=str(sh.get("id") or f"{sid}-shot-{s_i}"),
                    order=int(sh.get("order") or s_i),
                    title=str(sh.get("title") or f"Shot {s_i}"),
                    description=str(sh.get("description") or fallback_desc),
                    duration_seconds=float(
                        clamp_clip_seconds(
                            sh.get("durationSeconds") or sh.get("duration_seconds") or clip,
                            default=clip,
                        )
                    ),
                    camera=str(sh.get("camera") or ""),
                )
            )
    if not shots:
        shots = [
            ShotPlan(
                id=f"{sid}-shot-1",
                order=1,
                title="Establish",
                description=fallback_desc,
                duration_seconds=float(clip),
                camera="Wide",
            ),
            ShotPlan(
                id=f"{sid}-shot-2",
                order=2,
                title="Detail",
                description=fallback_desc,
                duration_seconds=float(clip),
                camera="Medium",
            ),
        ]
    scene_dur = int(round(sum(s.duration_seconds for s in shots)))
    return shots, scene_dur


def _from_groq(payload: DirectorGenerateRequest) -> DirectorResponse:
    clip = _clip_len()
    n_shots = plan_shot_count(payload.duration_seconds, clip)
    prompt = (
        f"Genre: {payload.genre}\nLanguage for spoken lines: {payload.language}\n"
        f"Target total duration: {payload.duration_seconds}s\n"
        f"Each shot MUST be {clip} seconds (fal clip max 15s). "
        f"Create about {n_shots} shots total across enough scenes.\n"
        f"USER IDEA (follow exactly, do not invent a different story):\n{payload.idea.strip()}\n"
        f"Extra instructions: {payload.instructions.strip() or 'none'}\n"
        "Keep visuals family-friendly; no gore or graphic violence.\n"
        "Accuracy check: title, characters, scene descriptions, and dialogue must clearly "
        "reflect the USER IDEA above — not a generic harbor/radio/storm template.\n"
        "For image/video safety: avoid words like accident, crash, blood, weapon, injury; "
        "describe calm help / care moments without danger imagery."
    )
    raw = director_chat(prompt, GENERATE_SYS, temperature=0.15)
    data = _parse_json(raw)
    characters: list[Character] = []
    for index, c in enumerate(data.get("characters") or [], start=1):
        characters.append(
            Character(
                id=str(c.get("id") or f"char-{index}"),
                name=str(c.get("name") or f"Character {index}"),
                role=str(c.get("role") or "Cast"),
                description=str(c.get("description") or ""),
            )
        )
    if not characters:
        characters = _mock_plan(payload).characters

    scenes: list[Scene] = []
    for index, sc in enumerate((data.get("scenes") or [])[:40], start=1):
        sid = str(sc.get("id") or f"scene-{index}")
        desc = str(sc.get("description") or "")
        shots, scene_dur = _normalize_shots(sid, sc, clip=clip, fallback_desc=desc)
        vos: list[VoiceLine] = []
        for v_i, vo in enumerate(sc.get("voiceOver") or sc.get("voice_over") or [], start=1):
            vos.append(
                VoiceLine(
                    id=str(vo.get("id") or f"{sid}-vo-{v_i}"),
                    character_id=vo.get("characterId") or vo.get("character_id"),
                    character_name=str(
                        vo.get("characterName") or vo.get("character_name") or "Narrator"
                    ),
                    text=str(vo.get("text") or ""),
                    estimated_seconds=float(
                        vo.get("estimatedSeconds")
                        or vo.get("estimated_seconds")
                        or 3.0
                    ),
                )
            )
        scenes.append(
            Scene(
                id=sid,
                order=int(sc.get("order") or index),
                title=str(sc.get("title") or f"Scene {index}"),
                description=desc,
                duration_seconds=scene_dur,
                status="pending",
                shots=shots,
                voice_over=vos,
                sfx_notes=str(sc.get("sfxNotes") or sc.get("sfx_notes") or ""),
            )
        )

    if not scenes:
        return _mock_plan(payload)

    script = str(data.get("script") or "").strip()
    structure = data.get("storyStructure") or data.get("story_structure") or []
    planned = sum(s.duration_seconds for s in scenes)
    plan = DirectorResponse(
        title=str(data.get("title") or _short_title(payload.idea, payload.language)),
        concept=str(data.get("concept") or payload.idea.strip()),
        script=script,
        characters=characters,
        story_structure=[str(b) for b in structure] or [s.title for s in scenes],
        scenes=scenes,
        estimated_duration_seconds=planned or payload.duration_seconds,
    )
    return validate_director_plan(plan, payload)


async def generate_director_response(
    payload: DirectorGenerateRequest,
) -> DirectorResponse:
    if groq_enabled():
        try:
            return _from_groq(payload)
        except Exception:
            return _mock_plan(payload)
    return _mock_plan(payload)