"""Voice-line timeline placement and character → Sarvam voice mapping."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Fixed Sarvam bulbul speakers (stable per character for the project).
MALE_VOICES: tuple[str, ...] = ("shubh", "manan")
FEMALE_VOICES: tuple[str, ...] = ("ritu", "shreya")
NARRATOR_VOICE = "shubh"

DEFAULT_LINE_GAP_SECONDS = 0.3


@dataclass(slots=True)
class VoicePlacement:
    line_id: str
    start_seconds: float
    duration_seconds: float
    shot_id: str | None = None

    @property
    def end_seconds(self) -> float:
        return self.start_seconds + self.duration_seconds


@dataclass(slots=True)
class ShotWindow:
    shot_id: str
    start_seconds: float
    duration_seconds: float

    @property
    def end_seconds(self) -> float:
        return self.start_seconds + self.duration_seconds


@dataclass(slots=True)
class VoiceTimeline:
    placements: list[VoicePlacement]
    scene_duration_seconds: float
    gap_seconds: float = DEFAULT_LINE_GAP_SECONDS
    overflow: bool = False
    overflow_seconds: float = 0.0
    shot_overflows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_voice_seconds(self) -> float:
        if not self.placements:
            return 0.0
        return max(p.end_seconds for p in self.placements)


def build_voice_timeline(
    line_durations: list[tuple[str, float]],
    *,
    scene_duration_seconds: float,
    gap_seconds: float = DEFAULT_LINE_GAP_SECONDS,
) -> VoiceTimeline:
    """Place lines in script order with fixed gaps; never shorten a line.

    Flags overflow when the last line ends after the planned scene duration.
    """
    gap = max(0.0, float(gap_seconds))
    scene_dur = max(0.0, float(scene_duration_seconds))
    placements: list[VoicePlacement] = []
    cursor = 0.0
    for index, (line_id, raw_dur) in enumerate(line_durations):
        dur = max(0.01, float(raw_dur or 0.01))
        placements.append(
            VoicePlacement(
                line_id=str(line_id),
                start_seconds=round(cursor, 4),
                duration_seconds=round(dur, 4),
            )
        )
        cursor = cursor + dur
        if index < len(line_durations) - 1:
            cursor += gap
    total = float(placements[-1].end_seconds) if placements else 0.0
    overflow = bool(placements) and total > scene_dur + 1e-6
    overflow_by = max(0.0, total - scene_dur) if overflow else 0.0
    return VoiceTimeline(
        placements=placements,
        scene_duration_seconds=scene_dur,
        gap_seconds=gap,
        overflow=overflow,
        overflow_seconds=round(overflow_by, 4),
    )


def build_shot_windows(
    shot_durations: list[tuple[str, float]],
) -> list[ShotWindow]:
    """Cumulative timeline windows for ordered shots."""
    windows: list[ShotWindow] = []
    cursor = 0.0
    for shot_id, raw_dur in shot_durations:
        dur = max(0.1, float(raw_dur or 0.1))
        windows.append(
            ShotWindow(
                shot_id=str(shot_id),
                start_seconds=round(cursor, 4),
                duration_seconds=round(dur, 4),
            )
        )
        cursor += dur
    return windows


def assign_lines_to_shots(
    line_ids: list[str],
    shot_ids: list[str],
    *,
    explicit: dict[str, str] | None = None,
) -> dict[str, str]:
    """Map each dialogue line → a shot id (stable, order-preserving).

    Priority:
    1. Explicit per-line shot_id when it belongs to this scene's shots
    2. Even order split across shots (2 lines / 2 shots → 1:1)
    3. If no shots, leave unassigned (empty string)
    """
    shots = [str(s) for s in shot_ids if str(s).strip()]
    explicit = explicit or {}
    mapping: dict[str, str] = {}
    if not line_ids:
        return mapping
    if not shots:
        return {str(lid): "" for lid in line_ids}

    shot_set = set(shots)
    unassigned: list[str] = []
    for lid in line_ids:
        key = str(lid)
        linked = str(explicit.get(key) or "").strip()
        if linked and linked in shot_set:
            mapping[key] = linked
        else:
            unassigned.append(key)

    n = len(unassigned)
    m = len(shots)
    for i, lid in enumerate(unassigned):
        # Even pack preserving script order: 3 lines / 2 shots → [0,0,1]
        idx = min((i * m) // max(1, n), m - 1) if n else 0
        mapping[lid] = shots[idx]
    return mapping


def build_shot_linked_voice_timeline(
    line_durations: list[tuple[str, float]],
    *,
    shot_windows: list[ShotWindow],
    line_shot_map: dict[str, str],
    gap_seconds: float = DEFAULT_LINE_GAP_SECONDS,
    shot_lead_in_seconds: float = 0.1,
) -> VoiceTimeline:
    """Place each line inside its linked shot window (movie-style).

    Lines for the same shot are sequential with gaps, starting near shot start.
    Lines are never shortened; overflow past the shot/scene is flagged.
    """
    gap = max(0.0, float(gap_seconds))
    lead = max(0.0, float(shot_lead_in_seconds))
    scene_dur = (
        float(shot_windows[-1].end_seconds) if shot_windows else 0.0
    )

    # Group lines in script order per shot.
    per_shot: dict[str, list[tuple[str, float]]] = {w.shot_id: [] for w in shot_windows}
    orphan: list[tuple[str, float]] = []
    for line_id, raw_dur in line_durations:
        lid = str(line_id)
        dur = max(0.01, float(raw_dur or 0.01))
        sid = str(line_shot_map.get(lid) or "").strip()
        if sid in per_shot:
            per_shot[sid].append((lid, dur))
        else:
            orphan.append((lid, dur))

    placements: list[VoicePlacement] = []
    shot_overflows: list[dict[str, Any]] = []

    for window in shot_windows:
        cursor = window.start_seconds + lead
        lines = per_shot.get(window.shot_id) or []
        for index, (lid, dur) in enumerate(lines):
            placements.append(
                VoicePlacement(
                    line_id=lid,
                    start_seconds=round(cursor, 4),
                    duration_seconds=round(dur, 4),
                    shot_id=window.shot_id,
                )
            )
            end = cursor + dur
            if end > window.end_seconds + 1e-6:
                shot_overflows.append(
                    {
                        "shot_id": window.shot_id,
                        "line_id": lid,
                        "shot_end": round(window.end_seconds, 4),
                        "line_end": round(end, 4),
                        "overflow_seconds": round(end - window.end_seconds, 4),
                    }
                )
            cursor = end
            if index < len(lines) - 1:
                cursor += gap

    # Orphans (unknown shot): append after last shot / at 0 like legacy.
    if orphan:
        cursor = scene_dur if shot_windows else 0.0
        for index, (lid, dur) in enumerate(orphan):
            placements.append(
                VoicePlacement(
                    line_id=lid,
                    start_seconds=round(cursor, 4),
                    duration_seconds=round(dur, 4),
                    shot_id=None,
                )
            )
            cursor = cursor + dur
            if index < len(orphan) - 1:
                cursor += gap

    # Keep script order in placements list for metadata readability.
    order = {str(lid): i for i, (lid, _) in enumerate(line_durations)}
    placements.sort(key=lambda p: (order.get(p.line_id, 10_000), p.start_seconds))

    total = max((p.end_seconds for p in placements), default=0.0)
    overflow = bool(placements) and total > scene_dur + 1e-6
    overflow_by = max(0.0, total - scene_dur) if overflow else 0.0
    return VoiceTimeline(
        placements=placements,
        scene_duration_seconds=scene_dur,
        gap_seconds=gap,
        overflow=overflow,
        overflow_seconds=round(overflow_by, 4),
        shot_overflows=shot_overflows,
    )


def _normalize_gender(raw: str | None) -> str | None:
    g = str(raw or "").strip().lower()
    if g in {"f", "female", "woman", "girl", "lady", "she", "her"}:
        return "female"
    if g in {"m", "male", "man", "boy", "gentleman", "he", "him"}:
        return "male"
    return None


def infer_gender(
    name: str = "",
    description: str = "",
    role: str = "",
    *,
    gender: str | None = None,
) -> str:
    """Infer male/female for Sarvam voice pick.

    Explicit gender wins. Hindi descriptions often use feminine markers like
    ``वाली`` with Devanagari names (ऐशा/मीरा) that Latin-ending heuristics miss.
    """
    explicit = _normalize_gender(gender)
    if explicit:
        return explicit

    blob = f"{name} {description} {role}".lower()
    # Keep original script for Devanagari / Hindi markers (lower() is ASCII-only).
    blob_raw = f"{name} {description} {role}"

    female_hints = (
        "female",
        "woman",
        "girl",
        "mother",
        "maa",
        "devi",
        "lady",
        "she ",
        "her ",
        "wife",
        "daughter",
        "sister",
        "heroine",
        "actress",
    )
    male_hints = (
        "male",
        "man ",
        "boy",
        "father",
        "baba",
        "he ",
        "his ",
        "husband",
        "son",
        "brother",
        "king",
        "hero",
        "actor",
    )
    # Hindi / Devanagari cues (common in director_response descriptions).
    # Check पुरुष/महिला and वाला/वाली carefully — "वाला" is a prefix of nothing in वाली.
    female_hi = (
        "महिला",
        "स्त्री",
        "नारी",
        "औरत",
        "लड़की",
        "लडकी",
        "बेटी",
        "बहन",
        "माता",
        "माँ",
        "पत्नी",
        "देवी",
        "कुमारी",
        "हीरोइन",
        "वाली",
    )
    male_hi = (
        "पुरुष",
        "आदमी",
        "लड़का",
        "लडका",
        "बेटा",
        "भाई",
        "पिता",
        "पति",
        "राजकुमार",
        "हीरो",
        "वाला",
    )
    # Explicit gender words first (avoid name-ending false positives).
    if "महिला" in blob_raw or "स्त्री" in blob_raw:
        return "female"
    if "पुरुष" in blob_raw or "आदमी" in blob_raw:
        return "male"
    if any(h in blob for h in female_hints) or any(h in blob_raw for h in female_hi):
        return "female"
    if any(h in blob for h in male_hints) or any(h in blob_raw for h in male_hi):
        return "male"
    # Role cues (news / film).
    role_l = (role or "").lower()
    if any(k in role_l for k in ("anchor", "actress", "heroine", "witness")):
        # Witness/anchor often female in our Hindi news packs, but only when name
        # also looks feminine — otherwise fall through to name heuristics.
        pass
    if any(k in role_l for k in ("reporter", "hero", "policeman", "soldier")):
        # Weak prior only when no other signal; handled after name endings.
        role_male_prior = True
    else:
        role_male_prior = False

    n = (name or "").strip()
    # Prefer first token for multi-word Indic names (e.g. "राजेश कुमार").
    first = n.split()[0] if n else ""
    n_lower = first.lower() if first.isascii() else first
    # Latin Indic feminine name endings.
    if first.isascii() and n_lower.endswith(("a", "i", "ee", "ya", "aa")) and n_lower not in {
        "rama",
        "krishna",
        "shiva",
    }:
        return "female"
    # Devanagari feminine-leaning name endings on the given name (ा / ी / ि).
    token = first or n
    if token and token[-1] in {"ा", "ी", "ि", "ई"}:
        return "female"
    if role_male_prior:
        return "male"
    return "male"


def build_character_voice_map(
    characters: list[dict[str, Any]] | None,
) -> dict[str, str]:
    """Stable character_id / name → Sarvam speaker. Narrator always shubh."""
    mapping: dict[str, str] = {
        "": NARRATOR_VOICE,
        "narrator": NARRATOR_VOICE,
        "narration": NARRATOR_VOICE,
    }
    male_i = 0
    female_i = 0
    for char in characters or []:
        if not isinstance(char, dict):
            continue
        cid = str(char.get("id") or "").strip()
        name = str(char.get("name") or "").strip()
        desc = str(char.get("description") or "").strip()
        role = str(char.get("role") or "").strip()
        gender = infer_gender(
            name,
            desc,
            role,
            gender=str(char.get("gender") or "") or None,
        )
        if gender == "female":
            voice = FEMALE_VOICES[female_i % len(FEMALE_VOICES)]
            female_i += 1
        else:
            voice = MALE_VOICES[male_i % len(MALE_VOICES)]
            male_i += 1
        if cid:
            mapping[cid] = voice
        if name:
            mapping[name.lower()] = voice
    return mapping


def resolve_character_voice(
    voice_map: dict[str, str],
    *,
    character_id: str | None = None,
    character_name: str | None = None,
) -> str:
    cid = str(character_id or "").strip()
    if cid and cid in voice_map:
        return voice_map[cid]
    cname = str(character_name or "").strip().lower()
    if cname and cname in voice_map:
        return voice_map[cname]
    return voice_map.get("narrator", NARRATOR_VOICE)


def dialogue_lines_from_scene(scene: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Normalize voiceOver entries into ordered line dicts."""
    if not isinstance(scene, dict):
        return []
    raw = scene.get("voice_over") or scene.get("voiceOver") or []
    if not isinstance(raw, list):
        return []
    lines: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        line_id = str(item.get("id") or f"line-{index + 1}").strip()
        linked_shot = str(
            item.get("shot_id")
            or item.get("shotId")
            or item.get("linked_shot_id")
            or item.get("linkedShotId")
            or ""
        ).strip() or None
        lines.append(
            {
                "id": line_id,
                "index": index,
                "text": text,
                "character_id": str(
                    item.get("character_id")
                    or item.get("characterId")
                    or ""
                ).strip()
                or None,
                "character_name": str(
                    item.get("character_name")
                    or item.get("characterName")
                    or "Narrator"
                ).strip()
                or "Narrator",
                "shot_id": linked_shot,
                "estimated_seconds": float(
                    item.get("estimated_seconds")
                    or item.get("estimatedSeconds")
                    or 3.0
                ),
            }
        )
    return lines
