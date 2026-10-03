from pydantic import BaseModel, ConfigDict, Field


class ProductionStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    fail_scene_ids: list[str] = Field(
        default_factory=list,
        alias="failSceneIds",
        description="Optional scene ids that should fail once (testing).",
    )
    max_retries: int = Field(default=2, ge=0, le=10, alias="maxRetries")


class ProductionRegenerateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    scene_ids: list[str] = Field(default_factory=list, alias="sceneIds")
    shot_ids: list[str] = Field(default_factory=list, alias="shotIds")
    include_stills: bool = Field(
        default=False,
        alias="includeStills",
        description="When true with shotIds, re-run Flux still before Wan clip.",
    )


class ProductionFixShotRequest(BaseModel):
    """User-friendly fix for a failed shot (clip / still / AI safe motion)."""

    model_config = ConfigDict(populate_by_name=True)

    shot_id: str = Field(alias="shotId")
    mode: str = Field(
        default="auto_fix",
        description=(
            "auto_fix = AI/safe motion re-try clip; "
            "guided = use guidance text as camera/mood; "
            "retry = motion-only clip retry; "
            "new_still = regenerate Flux still + clip"
        ),
    )
    guidance: str | None = Field(
        default=None,
        description="Optional camera/mood notes (weapons/story will be stripped).",
    )


class ProductionResumeMissingRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    force: list[str] = Field(
        default_factory=list,
        description="Shot ids to regenerate even if assets already exist.",
    )
    confirm: bool = Field(
        default=False,
        description="Must be true (or CONFIRM_RESUME env) to enqueue jobs.",
    )
    assemble: bool = Field(
        default=True,
        description="When all shots exist, force scene_render + final_assembly.",
    )


class ProductionAcceptedResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    project_id: str = Field(serialization_alias="projectId")
    status: str
    message: str
