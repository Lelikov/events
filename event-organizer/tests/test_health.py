def test_health_ok(client) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_metrics_exposed(client) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text
