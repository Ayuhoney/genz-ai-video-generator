import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_director_generate_requires_auth(client: AsyncClient) -> None:
    response = await client.post(
        "/api/director/generate",
        json={
            "idea": "A stormy harbor mystery",
            "durationSeconds": 80,
            "language": "English",
            "genre": "Drama",
            "instructions": "Keep it tense",
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_director_generate_mock_shape(
    client: AsyncClient,
    auth_headers_factory,
) -> None:
    register = await client.post(
        "/api/auth/register",
        json={
            "email": "director@example.com",
            "password": "secret123",
            "name": "Director User",
        },
    )
    token = register.json()["accessToken"]
    headers = auth_headers_factory(token)

    response = await client.post(
        "/api/director/generate",
        headers=headers,
        json={
            "idea": "A stormy harbor mystery with a radio operator",
            "durationSeconds": 80,
            "language": "English",
            "genre": "Drama",
            "instructions": "Keep it tense",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["title"], str) and body["title"]
    assert isinstance(body["concept"], str) and body["concept"]
    assert isinstance(body.get("script"), str)
    assert isinstance(body["characters"], list) and len(body["characters"]) >= 1
    assert isinstance(body["storyStructure"], list) and len(body["storyStructure"]) >= 1
    assert isinstance(body["scenes"], list) and len(body["scenes"]) >= 1
    assert body["estimatedDurationSeconds"] == 80

    scene = body["scenes"][0]
    assert "id" in scene
    assert "order" in scene
    assert "title" in scene
    assert "description" in scene
    assert "durationSeconds" in scene
    assert scene["status"] in {"pending", "running", "completed", "failed"}
    assert isinstance(scene.get("shots"), list)
    assert isinstance(scene.get("voiceOver"), list)

    invalid = await client.post(
        "/api/director/generate",
        headers=headers,
        json={
            "idea": "",
            "durationSeconds": 5,
            "language": "English",
            "genre": "Drama",
        },
    )
    assert invalid.status_code == 422
