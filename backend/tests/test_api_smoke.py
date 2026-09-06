from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ACTIVE_PROVIDER", "sample-dev-only")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


def test_list_matches_smoke():
    resp = client.get("/api/v1/matches")
    assert resp.status_code == 200
    matches = resp.json()
    assert isinstance(matches, list)
    assert len(matches) >= 1
    assert "match_id" in matches[0]


def test_get_match_smoke():
    resp = client.get("/api/v1/matches/M1")
    assert resp.status_code == 200
    assert resp.json()["match_id"] == "M1"


def test_get_match_not_found():
    resp = client.get("/api/v1/matches/DOES_NOT_EXIST")
    assert resp.status_code == 404


def test_match_analysis_smoke():
    resp = client.get("/api/v1/matches/M1/analysis")
    assert resp.status_code == 200
    data = resp.json()
    assert data["match_id"] == "M1"
    assert "one_x_two" in data
    ox = data["one_x_two"]
    total = ox["home_win"] + ox["draw"] + ox["away_win"]
    assert abs(total - 1.0) < 1e-3
    assert "confidence" in data
    assert data["confidence"]["confidence"] <= 0.90


def test_analyze_force_refresh_smoke():
    resp = client.post("/api/v1/matches/M1/analyze")
    assert resp.status_code == 200
    assert resp.json()["match_id"] == "M1"


def test_predictions_list_smoke():
    resp = client.get("/api/v1/predictions")
    assert resp.status_code == 200
    predictions = resp.json()
    assert isinstance(predictions, list)


def test_surprises_endpoint_excludes_routine_combos():
    resp = client.get("/api/v1/surprises", params={"match_id": "M1"})
    assert resp.status_code == 200
    surprises = resp.json()
    combos = {s["combination"] for s in surprises}
    assert combos.isdisjoint({"1/1", "X/X", "2/2"})


def test_openapi_docs_available():
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert "/api/v1/matches" in schema["paths"]
    assert "/api/v1/chat" in schema["paths"]
