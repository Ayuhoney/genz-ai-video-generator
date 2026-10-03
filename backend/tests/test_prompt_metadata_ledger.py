"""Asset metadata must record scene_text and final image/video prompts."""

from __future__ import annotations

from app.providers.base import ImageRequest
from app.providers.fal.clip_timing import motion_only_prompt
from app.providers.fal.image_video import FalImageProvider


def test_image_metadata_stores_scene_and_final_prompt(monkeypatch) -> None:
    scene = "Rainy chai stall, Raju finds a diary, no gore"

    class FakeClient:
        def run(self, model, arguments, **kwargs):
            assert "chai" in arguments["prompt"].lower()
            assert "gore" not in arguments["prompt"].lower()
            return (
                {
                    "images": [
                        {
                            "url": "https://cdn.example/out.png",
                            "content_type": "image/png",
                        }
                    ]
                },
                {"inference_time": 0.1},
            )

        def download(self, url):
            return b"PNGDATA"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        "app.providers.fal.image_video._make_client",
        lambda: FakeClient(),
    )
    monkeypatch.setenv("FAL_IMAGE_MODEL", "test-owner/test-image")
    from app.core.config import get_settings
    from app.workers.settings import get_worker_settings

    get_settings.cache_clear()
    get_worker_settings.cache_clear()

    result = FalImageProvider().generate(
        ImageRequest(project_id="p1", prompt=scene)
    )
    meta = result.metadata
    assert meta["scene_text"] == scene
    assert meta["image_prompt_final"]
    assert "chai" in meta["image_prompt_final"].lower()
    assert "gore" not in meta["image_prompt_final"].lower()
    assert "video_prompt_final" in meta
    # Wan path is motion/camera only — story stays in the still, not the clip prompt.
    video_final = motion_only_prompt(scene)
    assert "cinematic slow motion" in video_final.lower()
    assert "gore" not in video_final.lower()
    assert "fight" not in video_final.lower()
