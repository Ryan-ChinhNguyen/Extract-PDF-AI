"""Engines, health, and the generated API description."""


async def test_engines_lists_the_registry(api_client, engine_key):
    response = await api_client.get("/api/v1/engines")

    assert response.status_code == 200
    engines = response.json()
    assert engine_key in {e["key"] for e in engines}
    assert {"key", "provider", "model_name", "version", "unit", "is_active"} <= set(engines[0])


async def test_engines_can_be_limited_to_the_active_ones(api_client, engine_key):
    everything = (await api_client.get("/api/v1/engines")).json()

    response = await api_client.get("/api/v1/engines", params={"active_only": "true"})

    active = response.json()
    assert engine_key in {e["key"] for e in active}
    assert all(e["is_active"] for e in active)
    assert len(active) <= len(everything)


async def test_health_needs_no_database(api_client):
    response = await api_client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_db_reaches_the_database(api_client):
    response = await api_client.get("/api/v1/health/db")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "reachable"}


async def test_the_openapi_document_describes_every_route(api_client):
    response = await api_client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/documents" in paths
    assert "/api/v1/documents/{document_id}/pages/{page_no}/retry" in paths
    # The browser screens are not part of the API description.
    assert "/upload" not in paths
