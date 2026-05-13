import pytest


class TestSessionCreate:
    def test_creates_session_with_email(self, client):
        r = client.post("/api/v1/sessions",
                        headers={"X-Auth-Request-Email": "user@test.com"})
        assert r.status_code == 200
        body = r.json()
        assert "session_id" in body
        assert body["incident_idx"] == 0
        assert body["phase_id"] == "initial"

    def test_missing_email_header_returns_400(self, client):
        r = client.post("/api/v1/sessions")
        assert r.status_code == 400


class TestSessionResume:
    def test_get_existing_session(self, client, session_factory):
        sid = session_factory()
        r = client.get(f"/api/v1/sessions/{sid}")
        assert r.status_code == 200
        assert r.json()["session_id"] == sid

    def test_get_nonexistent_session_returns_404(self, client):
        r = client.get("/api/v1/sessions/does-not-exist")
        assert r.status_code == 404


class TestAction:
    def test_valid_action_returns_consequence(self, client, session_factory):
        sid = session_factory()
        # advance to incident 1 first
        client.post(f"/api/v1/sessions/{sid}/next")  # past orientation
        r = client.post(f"/api/v1/sessions/{sid}/action",
                        json={"action_id": "check_grafana"})
        assert r.status_code == 200
        body = r.json()
        assert "consequence" in body
        assert "score_delta" in body

    def test_invalid_action_id_returns_400(self, client, session_factory):
        sid = session_factory()
        client.post(f"/api/v1/sessions/{sid}/next")
        r = client.post(f"/api/v1/sessions/{sid}/action",
                        json={"action_id": "not_a_real_action"})
        assert r.status_code == 400

    def test_action_on_completed_session_returns_400(self, client, session_factory):
        sid = session_factory()
        from backend.session import mark_completed
        mark_completed(sid)
        r = client.post(f"/api/v1/sessions/{sid}/action",
                        json={"action_id": "check_grafana"})
        assert r.status_code == 400


class TestFreetext:
    def test_freetext_on_wrong_phase_returns_422(self, client, session_factory):
        sid = session_factory()
        client.post(f"/api/v1/sessions/{sid}/next")
        # incident 1, initial phase — freetext not expected here
        r = client.post(f"/api/v1/sessions/{sid}/freetext",
                        json={"text": "some update"})
        assert r.status_code == 422

    def test_empty_freetext_returns_400(self, client, session_factory):
        sid = session_factory()
        r = client.post(f"/api/v1/sessions/{sid}/freetext",
                        json={"text": ""})
        assert r.status_code == 400


class TestScoreEndpoint:
    def test_score_returns_breakdown_by_incident(self, client, session_factory):
        sid = session_factory()
        r = client.get(f"/api/v1/sessions/{sid}/score")
        assert r.status_code == 200
        body = r.json()
        assert "by_incident" in body
        assert "total" in body


class TestLogEndpoint:
    def test_existing_log_returns_200(self, client):
        r = client.get("/api/v1/logs/02_thermal_initial.log")
        assert r.status_code == 200
        assert len(r.text) > 0

    def test_nonexistent_log_returns_404(self, client):
        r = client.get("/api/v1/logs/does_not_exist.log")
        assert r.status_code == 404

    def test_path_traversal_blocked(self, client):
        r = client.get("/api/v1/logs/../../etc/passwd")
        assert r.status_code in (400, 404)
