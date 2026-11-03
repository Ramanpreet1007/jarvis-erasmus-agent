"""Tests the website's API with test data, without calling Gemini."""
import json

import pytest
from fastapi.testclient import TestClient

from erasmus_inbox.ai import FixtureAI
from erasmus_inbox.config import APPLICATIONS_EXPECTED
from erasmus_inbox.schemas import ApplicationRead, FaqAnswer
from erasmus_inbox.tools import Store
from web_app import create_app


class Chat(FixtureAI):
    def answer(self, question):
        return FaqAnswer(can_answer=True, faq_id="F7", language="English", reply="At least 6 out of 10.", reply_english="At least 6 out of 10.")

    def read_application(self, email):
        return ApplicationRead(is_application=True, first_name="Eva", last_semester_average=8.0, grade_quote="8.0", failed_status_known=True,
                               failed_status_quote="no failed", field_of_study="Business", english_level="B2", preferred_countries=["Italy"], confidence=0.9)


class Calls(FixtureAI):
    made = 0

    def read_application(self, email):
        Calls.made += 1
        return super().read_application(email)


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Store(tmp_path / "web"), Store(tmp_path / "docs"), replay=Calls, live=Calls, chat=Chat, pace=0))


def run(client):
    response = client.post("/api/process")
    assert response.status_code == 200
    return [json.loads(line) for line in response.text.splitlines()]


def test_process_streams_every_email_and_matches_the_answer_key(client):
    lines = run(client)
    assert lines[-1] == {"finished": True} and lines[-2]["done"] == 100
    expected = json.loads(APPLICATIONS_EXPECTED.read_text(encoding="utf-8"))["emails"]
    assert all(row["decision"] == expected[row["id"]]["decision"] for row in client.get("/api/state").json()["records"])


def test_list_does_not_carry_email_contents(client):
    run(client)
    row = client.get("/api/state").json()["records"][0]
    assert "source" not in row and "body" not in row
    assert "source" not in client.get(f"/api/record/{row['id']}").json()


def test_rule_change_makes_no_ai_calls(client):
    run(client)
    made = Calls.made
    result = client.post("/api/rules", json={"min_average": 7, "allow_failed_subjects": False}).json()
    assert Calls.made == made and result["changed"] > 0 and result["rules"]["min_average"] == 7
    assert client.post("/api/rules", json={"min_average": 70, "allow_failed_subjects": False}).status_code == 422


def test_not_eligible_needs_confirmation_and_groups_stay_separate(client):
    run(client)
    assert client.post("/api/approve", json={"decision": "not_eligible"}).status_code == 400
    state = client.post("/api/approve", json={"decision": "eligible"}).json()
    assert state["approved_now"] == state["counts"]["eligible"]
    assert all(r["approved"] for r in state["records"] if r["decision"] == "eligible")
    assert not any(r["approved"] for r in state["records"] if r["decision"] != "eligible")
    assert client.post("/api/approve", json={"decision": "review"}).status_code == 400
    rejected = next(r for r in state["records"] if r["decision"] == "not_eligible")
    assert client.post("/api/approve", json={"id": rejected["id"]}).status_code == 400
    assert client.post("/api/approve", json={"id": rejected["id"], "checked": True}).status_code == 200


def test_person_item_has_no_reply_to_approve(client):
    run(client)
    person = next(r for r in client.get("/api/state").json()["records"] if r["decision"] == "review")
    assert client.post("/api/approve", json={"id": person["id"], "checked": True}).status_code == 404


def test_reset_clears_results_and_rules(client):
    run(client)
    client.post("/api/rules", json={"min_average": 8, "allow_failed_subjects": False})
    state = client.post("/api/reset").json()
    assert state["counts"]["total"] == 0 and state["rules"]["min_average"] == 6


def test_chat_and_advice_use_the_real_flow(client):
    assert client.post("/api/chat", json={"question": "grade?"}).json()["faq_id"] == "F7"
    assert client.post("/api/chat", json={"question": ""}).status_code == 422
    form = client.post("/api/advice/read", json={"text": "my average 8.0, no failed subjects, eva@student.example"}).json()
    assert form["grade"] == 8.0 and form["email"] == "eva@student.example"
    result = client.post("/api/advice/check", json={"first_name": "Eva", "grade": 8.0, "failed_status": "Yes", "failed_subjects": "Physics",
                                                     "field": "Business", "level": "B2"}).json()
    assert result["decision"] == "not_eligible" and "Physics" in result["body"]
    assert client.post("/api/advice/check", json={"grade": 8, "failed_status": "Maybe", "field": "Business", "level": "B2"}).status_code == 422


def test_cheat_separates_hidden_text(client):
    assert client.get("/api/cheat").json()["found"] is False
    run(client)
    result = client.get("/api/cheat").json()
    assert "Ignore" in result["hidden"][0] and not any("Ignore" in line for line in result["visible"])


def test_empty_documents_view_works(client):
    assert client.get("/api/documents").json() == {"items": [], "students": []}
