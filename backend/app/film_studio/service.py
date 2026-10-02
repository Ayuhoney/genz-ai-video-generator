"""Film generation pipeline (ported from working Flask studio — same behavior)."""

from __future__ import annotations

import base64
import json
import os
import random
import re
import shutil
import subprocess
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from app.film_studio.settings import film_output_root, get_film_settings

try:
    from json_repair import repair_json
except ImportError:
    repair_json = None  # type: ignore[misc, assignment]

lock = threading.RLock()
jobs: dict[str, dict[str, Any]] = {}
running: set[tuple[str, int]] = set()
asm_lock = threading.Lock()

LANGS = {
    "Hindi": "hi-IN",
    "English (India)": "en-IN",
    "English": "en-IN",
    "Bengali": "bn-IN",
    "Gujarati": "gu-IN",
    "Kannada": "kn-IN",
    "Malayalam": "ml-IN",
    "Marathi": "mr-IN",
    "Odia": "od-IN",
    "Punjabi": "pa-IN",
    "Tamil": "ta-IN",
    "Telugu": "te-IN",
}
VOICES = {"male": ["shubh", "manan"], "female": ["ritu", "shreya"]}

MOODS = {
    "epic_war": "epic cinematic orchestral war music, powerful war drums, brass, intense, no vocals",
    "action_chase": "fast urgent cinematic action music, driving percussion, strings, no vocals",
    "tense": "dark tense suspense cinematic music, low strings, pulsing bass, no vocals",
    "calm": "calm peaceful cinematic music, soft flute and strings, gentle, no vocals",
    "sad": "emotional sad cinematic music, slow piano and cello, no vocals",
    "victory": "triumphant heroic cinematic orchestral music, uplifting brass, no vocals",
    "mystery": "mysterious ethereal cinematic ambient music, soft choir pads, no vocals",
    "devotional": "serene devotional Indian classical music, sitar, tanpura, flute, no vocals",
}
STYLE = (
    "cinematic film still, dramatic lighting, highly detailed, 16:9 wide shot, "
    "no text, no watermark, no subtitles"
)


class PolicyError(RuntimeError):
    pass


def _cost_map() -> dict[str, float]:
    s = get_film_settings()
    return {
        "img": s.cost_img,
        "vid": s.cost_vid,
        "sfx": s.cost_sfx,
        "music": s.cost_music,
        "llm": s.cost_llm,
    }


def run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if p.returncode:
        raise RuntimeError("ffmpeg: " + (p.stderr or "")[-600:])
    return p.stdout


def dur(path: str | Path) -> float:
    return float(
        run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ]
        ).strip()
    )


def jdir(j: dict[str, Any]) -> Path:
    d = film_output_root() / j["id"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(j: dict[str, Any]) -> None:
    path = jdir(j) / "job.json"
    for _ in range(5):
        try:
            with lock:
                data = json.dumps(j, ensure_ascii=False, default=str)
            path.write_text(data, encoding="utf-8")
            return
        except RuntimeError:
            time.sleep(0.05)


def get_job(jid: str) -> dict[str, Any] | None:
    jid = re.sub(r"\W", "", jid)
    with lock:
        if jid in jobs:
            return jobs[jid]
        p = film_output_root() / jid / "job.json"
        if p.is_file():
            jj = json.loads(p.read_text(encoding="utf-8"))
            if jj.get("stage") not in ("Ready", "Error"):
                jj["stage"] = "Error"
                jj["error"] = "Server restart se ruk gaya - 'Retry failed scenes' dabao"
            jobs[jid] = jj
            return jj
    return None


def add_cost(j: dict[str, Any], key: str, n: int = 1) -> None:
    with lock:
        j["cost"] = round(j.get("cost", 0) + _cost_map()[key] * n, 3)


def warn(j: dict[str, Any], msg: str) -> None:
    with lock:
        j.setdefault("warnings", []).append(msg)


def download(url: str, path: Path) -> None:
    with httpx.Client(timeout=180.0, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        path.write_bytes(r.content)


def fal_headers() -> dict[str, str]:
    key = (get_film_settings().fal_key or "").strip()
    return {"Authorization": f"Key {key}", "Content-Type": "application/json"}


def fal_run(model: str, payload: dict[str, Any], timeout: int = 900) -> dict[str, Any]:
    if not model.strip():
        raise RuntimeError("fal model id missing — set FAL_*_MODEL in .env")
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            f"https://queue.fal.run/{model}",
            headers=fal_headers(),
            json=payload,
        )
        if r.status_code not in (200, 201):
            if "content_policy_violation" in r.text:
                raise PolicyError(r.text[:250])
            raise RuntimeError(f"fal submit {r.status_code}: {r.text[:250]}")
        sub = r.json()
        t0 = time.time()
        while True:
            st = client.get(sub["status_url"], headers=fal_headers()).json()
            if st.get("status") == "COMPLETED":
                break
            if st.get("status") in ("FAILED", "ERROR"):
                raise RuntimeError(f"fal failed: {str(st)[:250]}")
            if time.time() - t0 > timeout:
                raise RuntimeError("fal timeout")
            time.sleep(4)
        res = client.get(sub["response_url"], headers=fal_headers()).json()
        if "detail" in res and len(res) == 1:
            if "content_policy_violation" in str(res):
                raise PolicyError(str(res)[:250])
            raise RuntimeError(f"fal error: {str(res)[:250]}")
        return res


def gemini_chat(
    content: str,
    system: str = "",
    max_tokens: int = 8000,
    json_mode: bool = True,
) -> str:
    s = get_film_settings()
    key = (s.gemini_key or "").strip()
    model = (s.gemini_model or "").strip()
    if not key or not model:
        raise RuntimeError("GEMINI_KEY and GEMINI_MODEL required")
    cfg: dict[str, Any] = {"temperature": 0.3, "maxOutputTokens": max_tokens}
    if json_mode:
        cfg["responseMimeType"] = "application/json"
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": content}]}],
        "generationConfig": cfg,
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    with httpx.Client(timeout=120.0) as client:
        r = client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=body,
        )
        if r.status_code != 200:
            raise RuntimeError(f"gemini {r.status_code}: {r.text[:250]}")
        cands = r.json().get("candidates") or []
        parts = (cands[0].get("content") or {}).get("parts", []) if cands else []
        text = "".join(p.get("text", "") for p in parts)
        if not text.strip():
            raise RuntimeError("gemini: empty response")
        return text


def parse_json(t: str) -> dict[str, Any]:
    if not t:
        raise ValueError("LLM ne khali response diya")
    t = re.sub(r"```(?:json)?", "", t)
    if "{" not in t:
        raise ValueError("response mein JSON nahi mila")
    s = t[t.index("{") :]
    try:
        return json.loads(s[: s.rindex("}") + 1])
    except Exception:
        if repair_json:
            return json.loads(repair_json(s))
        raise


DIRECTOR_SYS = f"""You are a film director + screenwriter for a short AI-generated movie.
Return ONLY valid JSON, no commentary, with this schema:
{{"title": str,
 "characters": [{{"id": "short_lowercase_id", "name": str, "gender": "male"|"female",
    "look": "FIXED visual description in English, 30-45 words: age, skin tone, hair, face, exact clothing and colors, accessories, species. Reused verbatim in every scene."}}],
 "scenes": [{{"action": "English: what we SEE, camera angle, setting (no character looks here)",
    "motion": "English: how things move in these 5 seconds (camera + action)",
    "characters": ["ids present in this scene"],
    "mood": one of {list(MOODS)},
    "sfx": "English comma separated sounds actually heard (e.g. swords clashing, arrows whooshing, crowd roar, fire crackling, wind)",
    "lines": [{{"who": "narrator" or a character id, "text": "spoken line in the target language"}}]}}]}}
Rules: max 4 characters; each scene is a 5 second clip so total spoken text per scene max 20 words;
scenes must tell a clear story with beginning, build-up, climax, ending; vary shots; no on-screen text;
use the target language and its native script for spoken lines; narrator is male.
Never use double quotes inside text values.
SAFETY: the video generator rejects graphic content. Never describe blood, gore, killing, wounds, dead bodies,
torture, nudity or weapons striking people. Show conflict through wide shots, silhouettes, fire, smoke, sparks,
clashing shields and reaction shots."""


def direct(story: str, n: int, lang_name: str) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    d: dict[str, Any] | None = None
    err: Exception | None = None
    for _ in range(2):
        try:
            t = gemini_chat(
                f"Story/idea: {story}\nNumber of scenes: {n}\nTarget language for spoken lines: {lang_name}",
                DIRECTOR_SYS,
                8000,
            )
            d = parse_json(t)
            if d.get("characters") is not None and d.get("scenes"):
                break
        except Exception as e:
            err = e
            print("[DIRECTOR RETRY]", e)
    if not d or not d.get("scenes"):
        raise RuntimeError(f"Director script nahi bani: {err}")

    chars = {c["id"]: c for c in d.get("characters", []) if c.get("id")}
    cnt = {"male": 0, "female": 0}
    for c in chars.values():
        g = c.get("gender") if c.get("gender") in VOICES else "male"
        c["gender"] = g
        c["voice"] = VOICES[g][cnt[g] % len(VOICES[g])]
        cnt[g] += 1

    max_scenes = get_film_settings().film_studio_max_scenes
    scenes: list[dict[str, Any]] = []
    for sc in d["scenes"][: min(n, max_scenes)]:
        ids = [x for x in sc.get("characters", []) if x in chars]
        looks = " ".join(
            f"{chars[x].get('name', x)}: {chars[x].get('look', '')}" for x in ids
        )
        mood = sc.get("mood") if sc.get("mood") in MOODS else "epic_war"
        action = sc.get("action", "")
        scenes.append(
            {
                "action": action,
                "motion": sc.get("motion", ""),
                "chars": ids,
                "mood": mood,
                "sfx_prompt": sc.get("sfx", ""),
                "lines": sc.get("lines", []),
                "looks": looks,
                "kprompt": f"{action}. {looks} {STYLE}",
                "status": "queued",
                "ver": 0,
                "key": None,
                "clip": None,
                "dirty": True,
                "clip_url": None,
                "key_url": None,
                "voice": None,
                "sfx_wav": None,
            }
        )
    return d.get("title", "Film"), chars, scenes


def soften(j: dict[str, Any], s: dict[str, Any]) -> None:
    t = gemini_chat(
        "Rewrite this film scene so an AI video safety filter accepts it. Remove blood, gore, killing, wounds, "
        "dead bodies, torture, nudity and weapons hitting people. Show conflict through wide shots, silhouettes, "
        "fire, smoke, sparks, dust and reaction shots. Keep the story beat, location and characters. "
        'Return ONLY JSON: {"action": str, "motion": str}\n'
        f"action: {s['action']}\nmotion: {s['motion']}",
        "",
        2000,
    )
    d = parse_json(t)
    s["action"] = d.get("action") or s["action"]
    s["motion"] = d.get("motion") or s["motion"]
    s["kprompt"] = f"{s['action']}. {s.get('looks', '')} {STYLE}"


def do_keyframe(j: dict[str, Any], i: int, note: str) -> None:
    s_cfg = get_film_settings()
    s, wd = j["scenes"][i], jdir(j)
    s["status"] = "keyframe"
    p = s["kprompt"]
    if note:
        p += f" IMPORTANT: {note}."
    res = fal_run(
        s_cfg.fal_image_model,
        {
            "prompt": p,
            "image_size": "landscape_16_9",
            "num_images": 1,
            "num_inference_steps": 4,
            "seed": random.randint(1, 10**9),
        },
    )
    add_cost(j, "img")
    url = res["images"][0]["url"]
    download(url, wd / f"k{i}.jpg")
    s["key"], s["key_url"] = f"k{i}.jpg", url
    s["ver"] += 1
    save(j)


def do_video(j: dict[str, Any], i: int, note: str) -> None:
    s_cfg = get_film_settings()
    s, wd = j["scenes"][i], jdir(j)
    s["status"] = "video (2-5 min)"
    p = f"{s['motion']}. {s['action']}. natural fluid motion, cinematic camera movement."
    if note:
        p += f" {note}."
    payload: dict[str, Any] = {
        "prompt": p,
        "image_url": s["key_url"],
        "seed": random.randint(1, 10**9),
    }
    if s_cfg.fal_video_resolution:
        payload["resolution"] = s_cfg.fal_video_resolution
    res = fal_run(s_cfg.fal_video_model, payload)
    add_cost(j, "vid")
    url = res["video"]["url"]
    download(url, wd / f"c{i}.mp4")
    s["clip"], s["clip_url"] = f"c{i}.mp4", url
    s["ver"] += 1
    save(j)


def do_sfx(j: dict[str, Any], i: int) -> None:
    s_cfg = get_film_settings()
    s, wd = j["scenes"][i], jdir(j)
    s["status"] = "sound effects"
    s["sfx_wav"] = None
    if not s["sfx_prompt"]:
        return
    try:
        d = dur(wd / s["clip"])
        res = fal_run(
            s_cfg.fal_sfx_model,
            {
                "video_url": s["clip_url"],
                "prompt": s["sfx_prompt"],
                "negative_prompt": "music, speech, voice, talking, singing, humming",
                "duration": round(min(d, 30), 1),
                "num_steps": 25,
            },
        )
        add_cost(j, "sfx")
        mp = wd / f"m{i}.mp4"
        download(res["video"]["url"], mp)
        wav = wd / f"sfx{i}.wav"
        run(["ffmpeg", "-y", "-i", str(mp), "-vn", "-ac", "2", "-ar", "44100", str(wav)])
        s["sfx_wav"] = f"sfx{i}.wav"
    except Exception as e:
        warn(j, f"Scene {i + 1}: SFX nahi bana ({str(e)[:100]})")


def tts(text: str, lang: str, speaker: str, path: Path) -> None:
    key = (get_film_settings().sarvam_key or "").strip()
    if not key:
        raise RuntimeError("SARVAM_KEY required")
    last: Exception | None = None
    for _ in range(3):
        try:
            with httpx.Client(timeout=60.0) as client:
                r = client.post(
                    "https://api.sarvam.ai/text-to-speech",
                    headers={
                        "api-subscription-key": key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "text": text[:2500],
                        "target_language_code": lang,
                        "speaker": speaker,
                        "model": "bulbul:v3",
                    },
                )
                if r.status_code != 200:
                    raise RuntimeError(f"tts {r.status_code}: {r.text[:150]}")
                path.write_bytes(base64.b64decode("".join(r.json()["audios"])))
                return
        except Exception as e:
            last = e
            time.sleep(2)
    raise RuntimeError(str(last))


def has_lines(s: dict[str, Any]) -> bool:
    return any(
        isinstance(ln, dict) and str(ln.get("text", "")).strip() for ln in s["lines"]
    )


def do_voice(j: dict[str, Any], i: int) -> None:
    s, wd = j["scenes"][i], jdir(j)
    s["status"] = "voice"
    s["voice"] = None
    parts: list[Path] = []
    try:
        for k, ln in enumerate(s["lines"]):
            if not isinstance(ln, dict) or not str(ln.get("text", "")).strip():
                continue
            who = ln.get("who", "narrator")
            sp = j["chars"][who]["voice"] if who in j["chars"] else VOICES["male"][0]
            p = wd / f"v{i}_{k}.wav"
            tts(str(ln["text"]), j["lang_code"], sp, p)
            parts.append(p)
        if not parts:
            return
        out = wd / f"v{i}.wav"
        cmd: list[str] = ["ffmpeg", "-y"]
        for p in parts:
            cmd += ["-i", str(p)]
        fc = "".join(
            f"[{n}:a]aresample=44100,aformat=channel_layouts=stereo,apad=pad_dur=0.3[a{n}];"
            for n in range(len(parts))
        )
        fc += (
            "".join(f"[a{n}]" for n in range(len(parts)))
            + f"concat=n={len(parts)}:v=0:a=1[o]"
        )
        run(cmd + ["-filter_complex", fc, "-map", "[o]", str(out)])
        s["voice"] = f"v{i}.wav"
    except Exception as e:
        warn(j, f"Scene {i + 1}: voice nahi bani ({str(e)[:100]})")


def get_music(j: dict[str, Any], mood: str) -> Path | None:
    s_cfg = get_film_settings()
    wd = jdir(j)
    p = wd / f"music_{mood}.wav"
    if p.is_file():
        return p
    try:
        res = fal_run(s_cfg.fal_music_model, {"prompt": MOODS[mood], "duration": 40})
        add_cost(j, "music")
        url = None
        if isinstance(res.get("audio_file"), dict):
            url = res["audio_file"].get("url")
        elif isinstance(res.get("audio"), dict):
            url = res["audio"].get("url")
        elif isinstance(res.get("url"), str):
            url = res["url"]
        if not url:
            raise RuntimeError(f"music result missing url: {list(res.keys())}")
        download(str(url), p)
        return p
    except Exception as e:
        warn(j, f"Music '{mood}' nahi bana ({str(e)[:100]})")
        return None


def build_scene(j: dict[str, Any], i: int, music_path: Path | None, out: Path) -> None:
    s_cfg = get_film_settings()
    w, h, fps = s_cfg.film_studio_width, s_cfg.film_studio_height, s_cfg.film_studio_fps
    s, wd = j["scenes"][i], jdir(j)
    clip = wd / s["clip"]
    voice = (wd / s["voice"]) if s["voice"] else None
    sfx = (wd / s["sfx_wav"]) if s["sfx_wav"] else None
    d = dur(clip)
    L = max(d, (dur(voice) + 0.5) if voice else 0)
    pad = max(0.0, L - d)
    cmd: list[str] = ["ffmpeg", "-y", "-i", str(clip)]
    for f in (voice, sfx, music_path):
        if f:
            cmd += ["-i", str(f)]
        else:
            cmd += ["-f", "lavfi", "-t", f"{L:.2f}", "-i", "anullsrc=r=44100:cl=stereo"]
    st = "aresample=44100,aformat=channel_layouts=stereo"
    fo = max(L - 0.8, 0)
    fc = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps},format=yuv420p,tpad=stop_mode=clone:stop_duration={pad:.2f}[v];"
        f"[1:a]{st},apad,asplit=2[vo1][vo2];"
        f"[2:a]{st},volume=0.85,apad[sf];"
        f"[3:a]{st},volume=0.30,afade=t=in:st=0:d=0.5,"
        f"afade=t=out:st={fo:.2f}:d=0.8,apad[mu];"
        f"[mu][vo1]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[md];"
        f"[vo2][sf][md]amix=inputs=3:duration=longest:normalize=0[a]"
    )
    run(
        cmd
        + [
            "-filter_complex",
            fc,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-t",
            f"{L:.2f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ar",
            "44100",
            "-ac",
            "2",
            str(out),
        ]
    )


def assemble(j: dict[str, Any]) -> None:
    with asm_lock:
        wd = jdir(j)
        j["stage"] = "Mix: dialogue + SFX + music"
        save(j)
        parts: list[Path] = []
        for i, s in enumerate(j["scenes"]):
            if not s["clip"]:
                continue
            o = wd / f"s{i}.mp4"
            if s.get("dirty", True) or not o.is_file():
                s["dirty"] = False
                try:
                    build_scene(j, i, get_music(j, s["mood"]), o)
                except Exception:
                    s["dirty"] = True
                    raise
            parts.append(o)
        if not parts:
            raise RuntimeError("Koi clip nahi bani (sab scenes fail hui). Warnings dekho.")
        lst = wd / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
        joined = wd / "joined.mp4"
        run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(lst),
                "-c",
                "copy",
                str(joined),
            ]
        )
        final = wd / "film.mp4"
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(joined),
                "-c:v",
                "copy",
                "-af",
                "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(final),
            ]
        )
        j["film_ver"] = j.get("film_ver", 0) + 1
        j["stage"] = "Ready"
        save(j)


def file_ok(wd: Path, name: str | None) -> bool:
    return bool(name) and (wd / name).is_file()


def process_scene(
    j: dict[str, Any],
    i: int,
    level: str = "auto",
    note: str = "",
) -> bool | None:
    s, wd = j["scenes"][i], jdir(j)
    key = (j["id"], i)
    with lock:
        if key in running:
            return False
        running.add(key)
    try:
        missing_only = False
        if level == "auto":
            if not file_ok(wd, s.get("key")) or not s.get("key_url"):
                level = "keyframe"
            elif not file_ok(wd, s.get("clip")) or not s.get("clip_url"):
                level = "video"
            else:
                level, missing_only = "audio", True
        s["status"] = "queued"
        for attempt in range(3):
            try:
                if level == "keyframe" or attempt > 0:
                    do_keyframe(j, i, note)
                    level = "video"
                if level == "video":
                    do_video(j, i, note)
                    level, missing_only = "audio", False
                if level == "audio":
                    if not s["clip"]:
                        raise RuntimeError("pehle video banao, phir audio redo karo")
                    if not missing_only or (not s["sfx_wav"] and s["sfx_prompt"]):
                        do_sfx(j, i)
                    if not missing_only or (not s["voice"] and has_lines(s)):
                        do_voice(j, i)
                break
            except PolicyError:
                if attempt == 2:
                    raise
                warn(j, f"Scene {i + 1}: filter ne roka, script soft karke retry {attempt + 1}")
                s["status"] = "rewriting (safe)"
                soften(j, s)
                level, missing_only = "keyframe", False
        s["status"] = "done"
        s["dirty"] = True
    except Exception as e:
        traceback.print_exc()
        s["status"] = "error"
        warn(j, f"Scene {i + 1} fail: {str(e)[:150]}")
    finally:
        with lock:
            running.discard(key)
    s["ver"] += 1
    save(j)
    return True


def run_job(j: dict[str, Any]) -> None:
    try:
        j["stage"] = "Director script bana raha hai"
        save(j)
        title, chars, scenes = direct(j["story"], j["n"], j["lang_name"])
        add_cost(j, "llm")
        j.update(title=title, chars=chars, scenes=scenes)
        save(j)
        j["stage"] = "Scenes ban rahi hain"
        parallel = get_film_settings().film_studio_parallel
        with ThreadPoolExecutor(parallel) as ex:
            list(ex.map(lambda i: process_scene(j, i), range(len(scenes))))
        assemble(j)
    except Exception as e:
        traceback.print_exc()
        j["error"], j["stage"] = str(e), "Error"
        save(j)


def run_regen(j: dict[str, Any], i: int, level: str, note: str) -> None:
    try:
        if process_scene(j, i, level, note) is False:
            return
        assemble(j)
    except Exception as e:
        traceback.print_exc()
        j["error"], j["stage"] = str(e), "Error"
        save(j)


def run_retry(j: dict[str, Any], targets: list[int]) -> None:
    try:
        parallel = get_film_settings().film_studio_parallel
        with ThreadPoolExecutor(parallel) as ex:
            list(ex.map(lambda i: process_scene(j, i, "auto"), targets))
        assemble(j)
    except Exception as e:
        traceback.print_exc()
        j["error"], j["stage"] = str(e), "Error"
        save(j)


def missing_keys() -> list[str]:
    s = get_film_settings()
    miss: list[str] = []
    if not (s.fal_key or "").strip():
        miss.append("FAL_KEY")
    if not (s.sarvam_key or "").strip():
        miss.append("SARVAM_KEY")
    if not (s.gemini_key or "").strip():
        miss.append("GEMINI_KEY")
    for label, val in (
        ("FAL_IMAGE_MODEL", s.fal_image_model),
        ("FAL_VIDEO_MODEL", s.fal_video_model),
        ("FAL_SFX_MODEL", s.fal_sfx_model),
        ("FAL_MUSIC_MODEL", s.fal_music_model),
        ("GEMINI_MODEL", s.gemini_model),
    ):
        if not (val or "").strip():
            miss.append(label)
    return miss


def create_and_start_job(story: str, n: int, lang: str) -> dict[str, Any]:
    max_scenes = get_film_settings().film_studio_max_scenes
    n = max(1, min(max_scenes, n))
    lang_name = lang if lang in LANGS else "Hindi"
    j: dict[str, Any] = {
        "id": uuid.uuid4().hex[:8],
        "story": story,
        "n": n,
        "lang_name": lang_name,
        "lang_code": LANGS[lang_name],
        "stage": "Starting",
        "error": None,
        "warnings": [],
        "scenes": [],
        "chars": {},
        "cost": 0,
        "title": "",
        "film_ver": 0,
    }
    with lock:
        jobs[j["id"]] = j
    threading.Thread(target=run_job, args=(j,), daemon=True).start()
    return j


def media_path(jid: str, name: str) -> Path | None:
    if not re.fullmatch(r"(k\d+|c\d+|film)\.(jpg|mp4)", name):
        return None
    p = film_output_root() / re.sub(r"\W", "", jid) / name
    return p if p.is_file() else None
