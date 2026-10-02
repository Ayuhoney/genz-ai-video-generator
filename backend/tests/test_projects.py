import pytest
from httpx import AsyncClient


async def _register(
    client: AsyncClient,
    email: str,
    name: str = "User",
) -> str:
    response = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "secret123", "name": name},
    )
    assert response.status_code == 201
    return response.json()["accessToken"]


@pytest.mark.asyncio
async def test_project_crud(client: AsyncClient, auth_headers_factory) -> None:
    token = await _register(client, "owner@example.com", "Owner")
    headers = auth_headers_factory(token)

    created = await client.post(
        "/api/projects",
        headers=headers,
        json={
            "title": "Neon Alley",
            "durationSeconds": 60,
            "language": "English",
            "genre": "Action",
            "idea": "A courier races through neon streets",
            "status": "draft",
        },
    )
    assert created.status_code == 201
    project = created.json()
    assert project["title"] == "Neon Alley"
    assert project["durationSeconds"] == 60
    assert project["status"] == "draft"
    assert "directorResponse" in project
    project_id = project["id"]

    listed = await client.get("/api/projects", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    fetched = await client.get(f"/api/projects/{project_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == project_id

    updated = await client.put(
        f"/api/projects/{project_id}",
        headers=headers,
        json={
            "title": "Neon Alley Remix",
            "status": "review",
            "directorResponse": {"title": "Mock plan"},
        },
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Neon Alley Remix"
    assert updated.json()["status"] == "review"
    assert updated.json()["directorResponse"]["title"] == "Mock plan"

    deleted = await client.delete(f"/api/projects/{project_id}", headers=headers)
    assert deleted.status_code == 204

    missing = await client.get(f"/api/projects/{project_id}", headers=headers)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_project_ownership_isolation(
    client: AsyncClient,
    auth_headers_factory,
) -> None:
    owner_token = await _register(client, "alice@example.com", "Alice")
    other_token = await _register(client, "bob@example.com", "Bob")
    owner_headers = auth_headers_factory(owner_token)
    other_headers = auth_headers_factory(other_token)

    created = await client.post(
        "/api/projects",
        headers=owner_headers,
        json={"title": "Alice Film", "durationSeconds": 30},
    )
    assert created.status_code == 201
    project_id = created.json()["id"]

    forbidden_get = await client.get(
        f"/api/projects/{project_id}",
        headers=other_headers,
    )
    assert forbidden_get.status_code == 403

    forbidden_put = await client.put(
        f"/api/projects/{project_id}",
        headers=other_headers,
        json={"title": "Hijacked"},
    )
    assert forbidden_put.status_code == 403

    forbidden_delete = await client.delete(
        f"/api/projects/{project_id}",
        headers=other_headers,
    )
    assert forbidden_delete.status_code == 403

    other_list = await client.get("/api/projects", headers=other_headers)
    assert other_list.status_code == 200
    assert other_list.json() == []
