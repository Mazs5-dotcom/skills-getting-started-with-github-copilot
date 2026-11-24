"""Public activities responses must not disclose student email addresses.

These tests exercise the running FastAPI app: an unauthenticated client signs a
student up, then a second unauthenticated client reads the activities list.
They fail while GET /activities returns the participants roster, and they pass
once that response no longer contains email addresses.

The in-memory activities dict is the oracle for what was stored. The HTTP
response is the observation. Signup is covered because it is the write path
that adds addresses to the same roster the list returns. No other route
returns that roster.
"""

import copy

import pytest
from fastapi.testclient import TestClient

from src.app import activities, app

# Not present in the repository. The disclosure under test is an address
# enrolled through the signup endpoint while the app is running.
RUNTIME_EMAIL = "runtime-enrolled@students.example.test"


@pytest.fixture
def roster():
    saved = copy.deepcopy(activities)
    try:
        yield activities
    finally:
        activities.clear()
        activities.update(saved)


@pytest.fixture
def anon_client(roster):
    with TestClient(app) as client:
        yield client


def _emails_in(value):
    found = []
    if isinstance(value, str):
        if "@" in value:
            found.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            found.extend(_emails_in(key))
            found.extend(_emails_in(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_emails_in(item))
    return found


def _assert_public_catalog(response, roster):
    """Anonymous catalog: same activities, counts only, no email addresses."""
    payload = response.json()
    leaked = _emails_in(payload)
    assert leaked == [], f"public activities response disclosed addresses: {leaked}"
    stored_emails = [
        email
        for details in roster.values()
        for email in details["participants"]
    ]
    still_present = [email for email in stored_emails if email in response.text]
    assert still_present == []
    assert set(payload) == set(roster)
    for name, stored in roster.items():
        published = payload[name]
        assert "participants" not in published
        assert published["description"] == stored["description"]
        assert published["schedule"] == stored["schedule"]
        assert published["max_participants"] == stored["max_participants"]
        assert published["participant_count"] == len(stored["participants"])
    return payload


def _signup(client, activity_name, email):
    response = client.post(
        f"/activities/{activity_name}/signup",
        params={"email": email},
    )
    assert response.status_code == 200, response.text
    assert email in activities[activity_name]["participants"]
    return response


def test_unauthenticated_list_hides_email_enrolled_at_runtime(roster):
    """A caller with no cookie, token, or Authorization header enrolls an
    address, and a second such caller must not receive that address."""
    activity_name = "Chess Club"
    before = len(roster[activity_name]["participants"])
    assert RUNTIME_EMAIL not in roster[activity_name]["participants"]

    enroller = TestClient(app)
    enrollment = _signup(enroller, activity_name, RUNTIME_EMAIL)
    assert enrollment.request.headers.get("authorization") is None
    assert "cookie" not in {key.lower() for key in enrollment.request.headers}

    reader = TestClient(app)
    response = reader.get("/activities")
    assert response.request.headers.get("authorization") is None
    assert "cookie" not in {key.lower() for key in response.request.headers}
    assert response.status_code == 200

    _assert_public_catalog(response, roster)
    assert len(roster[activity_name]["participants"]) == before + 1


def test_unauthenticated_list_hides_every_stored_roster(anon_client, roster):
    """Addresses already stored when the process starts are the same field the
    list serializes. An anonymous GET must not return any of them."""
    stored_emails = [
        email
        for details in roster.values()
        for email in details["participants"]
    ]
    assert stored_emails, "seed roster is empty; the test would not observe a leak"

    response = anon_client.get("/activities")
    assert response.status_code == 200
    _assert_public_catalog(response, roster)


def test_signup_on_every_activity_stays_out_of_the_list(anon_client, roster):
    """Each activity stores participants in the object GET /activities returns.
    Enrolling one new address per activity must not reveal any of them."""
    enrolled = {}
    for index, activity_name in enumerate(roster):
        email = f"student-{index}@students.example.test"
        enrolled[activity_name] = email
        _signup(anon_client, activity_name, email)

    response = anon_client.get("/activities")
    assert response.status_code == 200
    _assert_public_catalog(response, roster)


def test_unrecognized_authorization_header_does_not_unlock_the_roster(anon_client, roster):
    """A header the app does not issue must not select the roster object.
    The public catalog is the only representation an unauthenticated caller gets."""
    activity_name = "Programming Class"
    email = "header-probe@students.example.test"
    _signup(anon_client, activity_name, email)

    response = anon_client.get(
        "/activities",
        headers={"Authorization": "Bearer not-a-session"},
    )
    assert response.status_code == 200
    _assert_public_catalog(response, roster)
