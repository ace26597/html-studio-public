"""Tests for the back end. The studio runs them with run_tests (pytest); keep the run fast.

Tests never touch the live app's data: when the app keeps a state/ folder, it takes its place
from STUDIO_TEST_STATE, which the studio sets to an empty folder for each run.
"""
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402

client = TestClient(server.app)


def test_health_answers():
    answer = client.get("/api/health")
    assert answer.status_code == 200 and answer.json()["ok"] is True


def test_the_page_is_served():
    answer = client.get("/")
    assert answer.status_code == 200 and "text/html" in answer.headers["content-type"]


def test_an_unknown_route_is_not_found():
    assert client.get("/api/nothing-here").status_code == 404
