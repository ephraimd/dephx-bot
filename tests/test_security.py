from fastapi.testclient import TestClient

from tests.conftest import AUTH


def test_api_requires_token(client: TestClient) -> None:
    assert client.get("/api/health").status_code == 401


def test_api_accepts_token(client: TestClient) -> None:
    response = client.get("/api/health", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_wrong_token_rejected(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_wrong_host_rejected_even_with_token(client: TestClient) -> None:
    response = client.get("/api/health", headers={**AUTH, "Host": "evil.example:9999"})
    assert response.status_code == 421


def test_static_needs_no_token(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "placeholder" in response.text


def test_error_contract_shape(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.json() == {
        "error": {"code": "unauthorized", "message": "Missing or invalid launch token."}
    }
