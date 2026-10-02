import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_register_login_me_flow(client: AsyncClient, auth_headers_factory) -> None:
    register = await client.post(
        "/api/auth/register",
        json={
            "email": "creator@example.com",
            "password": "secret123",
            "name": "Creator",
        },
    )
    assert register.status_code == 201
    body = register.json()
    assert "accessToken" in body
    assert body["user"]["email"] == "creator@example.com"
    token = body["accessToken"]

    duplicate = await client.post(
        "/api/auth/register",
        json={
            "email": "creator@example.com",
            "password": "secret123",
            "name": "Creator",
        },
    )
    assert duplicate.status_code == 409

    bad_login = await client.post(
        "/api/auth/login",
        json={"email": "creator@example.com", "password": "wrong-pass"},
    )
    assert bad_login.status_code == 401

    login = await client.post(
        "/api/auth/login",
        json={"email": "creator@example.com", "password": "secret123"},
    )
    assert login.status_code == 200
    assert login.json()["accessToken"]

    me = await client.get("/api/auth/me", headers=auth_headers_factory(token))
    assert me.status_code == 200
    assert me.json()["email"] == "creator@example.com"

    unauth = await client.get("/api/auth/me")
    assert unauth.status_code == 401


@pytest.mark.asyncio
async def test_register_validation_error(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/register",
        json={"email": "not-an-email", "password": "123", "name": ""},
    )
    assert response.status_code == 422
