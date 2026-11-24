"""
High School Management System API

A super simple FastAPI application that allows students to view and sign up
for extracurricular activities at Mergington High School.
"""

import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
import os
from pathlib import Path

app = FastAPI(title="Mergington High School API",
              description="API for viewing and signing up for extracurricular activities")

# Mount the static files directory
current_dir = Path(__file__).parent
app.mount("/static", StaticFiles(directory=os.path.join(Path(__file__).parent,
          "static")), name="static")

# In-memory activity database
activities = {
    "Chess Club": {
        "description": "Learn strategies and compete in chess tournaments",
        "schedule": "Fridays, 3:30 PM - 5:00 PM",
        "max_participants": 12,
        "participants": ["michael@mergington.edu", "daniel@mergington.edu"]
    },
    "Programming Class": {
        "description": "Learn programming fundamentals and build software projects",
        "schedule": "Tuesdays and Thursdays, 3:30 PM - 4:30 PM",
        "max_participants": 20,
        "participants": ["emma@mergington.edu", "sophia@mergington.edu"]
    },
    "Gym Class": {
        "description": "Physical education and sports activities",
        "schedule": "Mondays, Wednesdays, Fridays, 2:00 PM - 3:00 PM",
        "max_participants": 30,
        "participants": ["john@mergington.edu", "olivia@mergington.edu"]
    }
}


ROLE_STUDENT = "student"
ROLE_STAFF = "staff"
PBKDF2_ITERATIONS = 600_000
SESSION_TTL_SECONDS = 8 * 60 * 60

# Demo accounts for the in-memory database. Only salted PBKDF2-SHA256 hashes
# are stored. Set MERGINGTON_ACCOUNTS_FILE to a JSON list of objects with
# "email", "role", "salt" (hex) and "password_hash" (hex) to replace them.
DEMO_ACCOUNTS = [
    {
        "email": "michael@mergington.edu",
        "role": ROLE_STUDENT,
        "salt": "b86f14c83619df0f6445a4a94defff8e",
        "password_hash": "4c7dc577d8a18d9bf9222a1aeba16c070aad5049107e1112ba1274c0e1e755bd",
    },
    {
        "email": "emma@mergington.edu",
        "role": ROLE_STUDENT,
        "salt": "1e56db46cc13568402786a696b379a55",
        "password_hash": "7c6f76d7169025502dbedc9a45a9cde60ac5072762f2b7be2fe132375f4d95a8",
    },
    {
        "email": "registrar@mergington.edu",
        "role": ROLE_STAFF,
        "salt": "2edef4af009326f90aa0be7f117a1d89",
        "password_hash": "69081151961763fb5677aa21aaf043d550343f9877d40d035a137159febca525",
    },
]


@dataclass(frozen=True)
class Account:
    email: str
    role: str
    salt: bytes
    password_hash: bytes


@dataclass(frozen=True)
class Session:
    email: str
    role: str
    expires_at: float


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)


def _load_accounts() -> dict[str, Account]:
    accounts_file = os.environ.get("MERGINGTON_ACCOUNTS_FILE")
    if accounts_file:
        with open(accounts_file, encoding="utf-8") as f:
            records = json.load(f)
    else:
        records = DEMO_ACCOUNTS

    accounts = {}
    for record in records:
        if record["role"] not in (ROLE_STUDENT, ROLE_STAFF):
            raise ValueError(f"Unknown role for account {record['email']!r}")
        email = _normalize_email(record["email"])
        accounts[email] = Account(
            email=email,
            role=record["role"],
            salt=bytes.fromhex(record["salt"]),
            password_hash=bytes.fromhex(record["password_hash"]),
        )
    return accounts


accounts = _load_accounts()

# Used for unknown emails so a failed login costs the same as a wrong password.
_DUMMY_SALT = secrets.token_bytes(16)
_DUMMY_HASH = _hash_password(secrets.token_urlsafe(16), _DUMMY_SALT)

# Keyed by the SHA-256 of the bearer token so the raw token is never stored.
sessions: dict[str, Session] = {}
_sessions_lock = threading.Lock()

bearer_scheme = HTTPBearer(auto_error=False)


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail,
                         headers={"WWW-Authenticate": "Bearer"})


def require_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> Session:
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Not authenticated")

    key = _token_key(credentials.credentials)
    with _sessions_lock:
        session = sessions.get(key)
        if session is not None and session.expires_at <= time.time():
            del sessions[key]
            session = None

    if session is None:
        raise _unauthorized("Invalid or expired session")
    return session


class SessionRequest(BaseModel):
    email: str
    password: str


@app.get("/")
def root():
    return RedirectResponse(url="/static/index.html")


@app.post("/sessions")
def create_session(credentials: SessionRequest):
    """Log in and receive a bearer token for authenticated requests"""
    account = accounts.get(_normalize_email(credentials.email))
    if account is None:
        hmac.compare_digest(_hash_password(credentials.password, _DUMMY_SALT), _DUMMY_HASH)
        raise _unauthorized("Invalid email or password")

    if not hmac.compare_digest(_hash_password(credentials.password, account.salt),
                               account.password_hash):
        raise _unauthorized("Invalid email or password")

    token = secrets.token_urlsafe(32)
    session = Session(email=account.email, role=account.role,
                      expires_at=time.time() + SESSION_TTL_SECONDS)
    with _sessions_lock:
        sessions[_token_key(token)] = session
    return {"token": token, "email": session.email, "role": session.role}


@app.get("/activities")
def get_activities():
    return activities


@app.post("/activities/{activity_name}/signup")
def signup_for_activity(activity_name: str, email: str | None = None,
                        session: Session = Depends(require_session)):
    """Sign up a student for an activity.

    Students always enroll themselves. Staff enroll the student named by `email`.
    """
    # Validate activity exists
    if activity_name not in activities:
        raise HTTPException(status_code=404, detail="Activity not found")

    if session.role == ROLE_STAFF:
        if email is None or "@" not in email:
            raise HTTPException(status_code=400,
                                detail="A student email is required for staff signups")
        student_email = _normalize_email(email)
    elif session.role == ROLE_STUDENT:
        if email is not None and _normalize_email(email) != session.email:
            raise HTTPException(status_code=403,
                                detail="Students can only sign themselves up")
        student_email = session.email
    else:
        raise HTTPException(status_code=403, detail="Not allowed to sign up students")

    # Get the specific activity
    activity = activities[activity_name]

    # Add student
    activity["participants"].append(student_email)
    return {"message": f"Signed up {student_email} for {activity_name}"}
