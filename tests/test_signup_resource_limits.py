"""Spec for the public activity signup endpoint.

These tests fail while POST /activities/{activity_name}/signup appends the
query-string email with no capacity, duplicate, or length check. They pass
once a signup that would exceed max_participants, repeat an enrolled email, or
carry an address that is not a single bounded email is refused and leaves the
in-memory roster unchanged.

Rate limiting is not asserted here. The participant list is the resource that
grows without a bound; a proxy-level limit lives outside this process.
"""

import socket
import subprocess
import sys
import threading
import time
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from src.app import activities, app

# Pydantic EmailStr rejects addresses longer than this (pydantic.networks.MAX_EMAIL_LENGTH).
# A pattern-plus-maximum-length check must reject them too. 10_000 is still under
# h11's default 16 KiB incomplete-event cap, so the bytes reach the application.
OVERSIZED_EMAIL = "a" * 10_000

# Addresses the endpoint must refuse. Each one is stored verbatim today.
INVALID_EMAILS = [
    pytest.param("not-an-email", id="missing-at"),
    pytest.param("", id="empty"),
    pytest.param(" ", id="blank"),
    pytest.param("student@", id="missing-domain"),
    pytest.param("@mergington.edu", id="missing-local"),
    pytest.param("student @mergington.edu", id="embedded-space"),
    pytest.param(OVERSIZED_EMAIL, id="ten-thousand-chars"),
    pytest.param(OVERSIZED_EMAIL + "@mergington.edu", id="ten-thousand-local-part"),
]


@pytest.fixture
def client():
    snapshot = {
        name: {
            key: (value.copy() if isinstance(value, list) else value)
            for key, value in details.items()
        }
        for name, details in activities.items()
    }
    with TestClient(app) as test_client:
        yield test_client
    activities.clear()
    activities.update(snapshot)


def _roster(client: TestClient, activity_name: str) -> list[str]:
    response = client.get("/activities")
    assert response.status_code == 200, response.text
    body = response.json()
    assert activity_name in body
    return list(body[activity_name]["participants"])


def _signup(client: TestClient, activity_name: str, email: str) -> httpx.Response:
    # Same shape as the static client: POST /activities/{name}/signup?email=
    return client.post(f"/activities/{quote(activity_name)}/signup", params={"email": email})


def test_duplicate_of_existing_participant_is_rejected_and_not_stored(client: TestClient):
    activity_name = "Chess Club"
    email = "michael@mergington.edu"
    before = _roster(client, activity_name)

    response = _signup(client, activity_name, email)

    assert response.status_code == 400, response.text
    after = _roster(client, activity_name)
    assert after == before
    assert after.count(email) == 1


def test_second_signup_of_a_new_email_is_rejected_and_not_stored(client: TestClient):
    activity_name = "Programming Class"
    email = "new.student@mergington.edu"
    first = _signup(client, activity_name, email)
    assert first.status_code == 200, first.text
    before = _roster(client, activity_name)

    second = _signup(client, activity_name, email)

    assert second.status_code == 400, second.text
    after = _roster(client, activity_name)
    assert after == before
    assert after.count(email) == 1


@pytest.mark.parametrize("activity_name", ["Chess Club", "Programming Class", "Gym Class"])
def test_signup_past_max_participants_is_rejected_and_not_stored(client: TestClient, activity_name: str):
    details = client.get("/activities").json()[activity_name]
    max_participants = details["max_participants"]
    already = len(details["participants"])
    assert already < max_participants

    for index in range(max_participants - already):
        response = _signup(client, activity_name, f"fill-{activity_name.replace(' ', '-').lower()}-{index}@mergington.edu")
        assert response.status_code == 200, response.text

    overflow = f"overflow-{activity_name.replace(' ', '-').lower()}@mergington.edu"
    before = _roster(client, activity_name)
    assert len(before) == max_participants

    response = _signup(client, activity_name, overflow)

    assert response.status_code == 400, response.text
    after = _roster(client, activity_name)
    assert overflow not in after
    assert after == before
    assert len(after) == max_participants


@pytest.mark.parametrize("email", INVALID_EMAILS)
def test_invalid_or_oversized_email_is_rejected_and_not_stored(client: TestClient, email: str):
    activity_name = "Gym Class"
    before = _roster(client, activity_name)

    response = _signup(client, activity_name, email)

    assert response.status_code in (400, 422), response.status_code
    after = _roster(client, activity_name)
    assert email not in after
    assert after == before


def test_get_activities_does_not_grow_when_oversized_signup_is_refused(client: TestClient):
    """GET /activities serializes every stored participant. Refusing the write keeps the listing bounded."""
    before_response = client.get("/activities")
    before_body = before_response.content

    response = _signup(client, "Chess Club", OVERSIZED_EMAIL)

    assert response.status_code in (400, 422), response.status_code
    after_response = client.get("/activities")
    assert OVERSIZED_EMAIL.encode() not in after_response.content
    assert len(after_response.content) <= len(before_body) + 64


def test_concurrent_signups_cannot_pass_a_stale_capacity_check(client: TestClient):
    """One slot remains. Append is delayed so a check that is not held across the append admits every racer."""
    activity_name = "Chess Club"
    details = client.get("/activities").json()[activity_name]
    max_participants = details["max_participants"]
    already = len(details["participants"])

    for index in range(max_participants - already - 1):
        response = _signup(client, activity_name, f"seat{index}@mergington.edu")
        assert response.status_code == 200, response.text
    assert len(_roster(client, activity_name)) == max_participants - 1

    class SlowAppendList(list):
        def append(self, item):
            if isinstance(item, str) and item.startswith("race"):
                time.sleep(0.2)
            super().append(item)

    activities[activity_name]["participants"] = SlowAppendList(activities[activity_name]["participants"])

    racer_count = 8
    statuses: list[int] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(racer_count)

    def attempt(index: int) -> None:
        try:
            barrier.wait(timeout=5)
            response = _signup(client, activity_name, f"race{index}@mergington.edu")
            statuses.append(response.status_code)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(racer_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert errors == []
    assert len(statuses) == racer_count
    roster = _roster(client, activity_name)
    assert len(roster) == max_participants
    assert statuses.count(200) == 1
    assert set(statuses) <= {200, 400}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_ready(base_url: str, process: subprocess.Popen, log_path: str) -> None:
    deadline = time.time() + 15
    last_error = ""
    while time.time() < deadline:
        if process.poll() is not None:
            with open(log_path, encoding="utf-8", errors="replace") as log_file:
                output = log_file.read()
            raise RuntimeError(f"uvicorn exited {process.returncode}: {output}")
        try:
            httpx.get(f"{base_url}/activities", timeout=0.5)
            return
        except httpx.HTTPError as exc:
            last_error = str(exc)
            time.sleep(0.05)
    with open(log_path, encoding="utf-8", errors="replace") as log_file:
        output = log_file.read()
    raise RuntimeError(f"uvicorn did not accept connections ({last_error}): {output}")


def test_live_uvicorn_signup_rejects_oversized_query_and_capacity_overflow(tmp_path):
    """Drive the attack path through uvicorn's HTTP parser, not only the in-process client."""
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_path = str(tmp_path / "uvicorn.log")
    with open(log_path, "w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "src.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--http",
                "h11",
                "--log-level",
                "warning",
            ],
            cwd="/workspace",
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    try:
        _wait_until_ready(base_url, process, log_path)
        with httpx.Client(base_url=base_url, timeout=10) as http:
            before = http.get("/activities")
            assert before.status_code == 200
            chess = before.json()["Chess Club"]
            assert len(chess["participants"]) == 2

            oversized = http.post(
                "/activities/Chess%20Club/signup",
                params={"email": OVERSIZED_EMAIL},
            )
            assert oversized.status_code in (400, 422), oversized.status_code
            listed = http.get("/activities").json()["Chess Club"]["participants"]
            assert OVERSIZED_EMAIL not in listed
            assert len(listed) == 2

            max_participants = chess["max_participants"]
            for index in range(max_participants - 2):
                accepted = http.post(
                    "/activities/Chess%20Club/signup",
                    params={"email": f"live{index}@mergington.edu"},
                )
                assert accepted.status_code == 200, accepted.text

            overflow = http.post(
                "/activities/Chess%20Club/signup",
                params={"email": "live-overflow@mergington.edu"},
            )
            assert overflow.status_code == 400, overflow.text
            final = http.get("/activities").json()["Chess Club"]["participants"]
            assert "live-overflow@mergington.edu" not in final
            assert len(final) == max_participants
            assert OVERSIZED_EMAIL not in final
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
