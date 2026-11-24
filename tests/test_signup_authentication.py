"""Security spec for activity signup.

The signup route must not enroll an address just because the caller typed it.
These tests fail while that is still possible, and they pass once enrollment
is tied to an authenticated session.

Session contract
----------------
POST /sessions
    JSON body: {"email": "<account email>", "password": "<password>"}
    200: {"token": "<opaque>", "email": "<same email>", "role": "student" | "staff"}
    401: unknown email, or a password other than the placeholder below.

Accounts that must accept the placeholder password
``placeholder-password`` (an obvious placeholder, not a production secret):

- michael@mergington.edu, role student
- emma@mergington.edu, role student
- registrar@mergington.edu, role staff

POST /activities/{activity_name}/signup
    Header: Authorization: Bearer <token from POST /sessions>
    No valid session: 401, and every roster is unchanged.
    Student: the enrolled address is the session email. A query ``email``
    that names someone else must not be added. Either respond 403 and leave
    the roster unchanged, or enroll only the session email.
    Staff: the query ``email`` is the student who is enrolled. That address
    is added. The staff address is not added in its place, and no other
    address is added.

GET /activities stays readable without a session. It is the roster oracle.
No other route enrolls or removes a participant; this file does not cover
GET /, static files, or src/static/app.js, which only calls this same POST.
"""

import copy
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from src.app import activities, app

PASSWORD_PLACEHOLDER = "placeholder-password"
STUDENT_EMAIL = "michael@mergington.edu"
OTHER_STUDENT_EMAIL = "emma@mergington.edu"
STAFF_EMAIL = "registrar@mergington.edu"

# Not a member of the activity the matching test enrolls them in.
STUDENT_ACTIVITY = "Gym Class"
OTHER_STUDENT_ACTIVITY = "Chess Club"


@pytest.fixture
def client():
    snapshot = copy.deepcopy(activities)
    with TestClient(app) as test_client:
        yield test_client
    activities.clear()
    activities.update(snapshot)


def _rosters(client: TestClient) -> dict:
    response = client.get("/activities")
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


def _participants(client: TestClient, activity: str) -> list:
    rosters = _rosters(client)
    assert activity in rosters, rosters.keys()
    return list(rosters[activity]["participants"])


def _signup(client: TestClient, activity: str, email: str | None = None, token: str | None = None):
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    params = {}
    if email is not None:
        params["email"] = email
    # One encoding of the activity name, including spaces, as the attack sends it.
    path = f"/activities/{quote(activity, safe='')}/signup"
    return client.post(path, params=params, headers=headers)


def _open_session(client: TestClient, email: str, password: str = PASSWORD_PLACEHOLDER):
    return client.post("/sessions", json={"email": email, "password": password})


def _token(client: TestClient, email: str) -> str:
    response = _open_session(client, email)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("email") == email
    assert isinstance(body.get("token"), str) and body["token"]
    return body["token"]


def test_attack_path_unenrolled_query_email_is_not_added(client: TestClient):
    """POST /activities/Gym%20Class/signup?email=victim@mergington.edu with no session."""
    activity = "Gym Class"
    victim = "victim@mergington.edu"
    before = _participants(client, activity)
    assert victim not in before

    response = _signup(client, activity, email=victim)

    after = _participants(client, activity)
    assert response.status_code == 401, response.text
    assert after == before
    assert victim not in after


@pytest.mark.parametrize(
    "activity,supplied_email",
    [
        ("Gym Class", "sophia@mergington.edu"),
        ("Chess Club", "fake.student@mergington.edu"),
        ("Programming Class", "john@mergington.edu"),
    ],
)
def test_each_activity_rejects_unauthenticated_signup(
    client: TestClient, activity: str, supplied_email: str
):
    before = _rosters(client)
    response = _signup(client, activity, email=supplied_email)
    after = _rosters(client)
    assert response.status_code == 401, response.text
    assert after == before


def test_forged_bearer_set_to_the_victim_email_is_not_a_session(client: TestClient):
    activity = "Chess Club"
    victim = "victim@mergington.edu"
    before = _participants(client, activity)
    response = client.post(
        f"/activities/{quote(activity, safe='')}/signup",
        params={"email": victim},
        headers={"Authorization": f"Bearer {victim}"},
    )
    after = _participants(client, activity)
    assert response.status_code == 401, response.text
    assert after == before
    assert victim not in after


def test_unknown_email_cannot_open_a_session(client: TestClient):
    response = _open_session(client, "victim@mergington.edu")
    assert response.status_code == 401, response.text
    before = _rosters(client)
    # A rejected login must not leave a usable credential behind. Calling
    # signup with the victim address still has to be refused.
    signup = _signup(client, "Gym Class", email="victim@mergington.edu")
    assert signup.status_code == 401, signup.text
    assert _rosters(client) == before


def test_wrong_password_does_not_open_a_session(client: TestClient):
    response = _open_session(client, STUDENT_EMAIL, password="not-the-placeholder")
    assert response.status_code == 401, response.text


def test_student_session_does_not_enroll_a_different_query_email(client: TestClient):
    """Authorizing one student must not enroll a different address."""
    token = _token(client, STUDENT_EMAIL)
    activity = STUDENT_ACTIVITY
    victim = "victim@mergington.edu"
    before = _participants(client, activity)
    assert STUDENT_EMAIL not in before
    assert victim not in before

    response = _signup(client, activity, email=victim, token=token)
    after = _participants(client, activity)

    assert victim not in after
    if response.status_code < 400:
        assert STUDENT_EMAIL in after
        added = [email for email in after if email not in before]
        assert added == [STUDENT_EMAIL]
    else:
        assert response.status_code == 403, response.text
        assert after == before


def test_student_session_enrolls_only_the_session_email_when_query_matches(client: TestClient):
    token = _token(client, OTHER_STUDENT_EMAIL)
    activity = OTHER_STUDENT_ACTIVITY
    before = _participants(client, activity)
    assert OTHER_STUDENT_EMAIL not in before

    response = _signup(client, activity, email=OTHER_STUDENT_EMAIL, token=token)
    after = _participants(client, activity)

    assert response.status_code == 200, response.text
    added = [email for email in after if email not in before]
    assert added == [OTHER_STUDENT_EMAIL]


def test_student_session_enrolls_the_session_email_without_a_query_email(client: TestClient):
    token = _token(client, STUDENT_EMAIL)
    activity = STUDENT_ACTIVITY
    before = _participants(client, activity)
    assert STUDENT_EMAIL not in before

    response = _signup(client, activity, email=None, token=token)
    after = _participants(client, activity)

    assert response.status_code == 200, response.text
    added = [email for email in after if email not in before]
    assert added == [STUDENT_EMAIL]


def test_staff_session_enrolls_the_named_student_and_not_itself(client: TestClient):
    token = _token(client, STAFF_EMAIL)
    activity = "Programming Class"
    student = "new.student@mergington.edu"
    before = _participants(client, activity)
    assert student not in before
    assert STAFF_EMAIL not in before

    response = _signup(client, activity, email=student, token=token)
    after = _participants(client, activity)

    assert response.status_code == 200, response.text
    added = [email for email in after if email not in before]
    assert added == [student]
    assert STAFF_EMAIL not in after


def test_staff_session_does_not_enroll_a_second_unrequested_address(client: TestClient):
    token = _token(client, STAFF_EMAIL)
    activity = "Gym Class"
    requested = "requested.student@mergington.edu"
    other = "other.student@mergington.edu"
    before = _participants(client, activity)

    response = _signup(client, activity, email=requested, token=token)
    after = _participants(client, activity)

    assert response.status_code == 200, response.text
    assert requested in after
    assert other not in after
    added = [email for email in after if email not in before]
    assert added == [requested]
