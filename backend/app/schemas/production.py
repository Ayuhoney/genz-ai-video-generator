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


class ProductionAcceptedResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    project_id: str = Field(serialization_alias="projectId")
    status: str
    message: str
