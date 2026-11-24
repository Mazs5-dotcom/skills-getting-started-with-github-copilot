"""
High School Management System API

A super simple FastAPI application that allows students to view and sign up
for extracurricular activities at Mergington High School.
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import os
import threading
from pathlib import Path

MAX_EMAIL_LENGTH = 254
EMAIL_PATTERN = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$"

signup_lock = threading.Lock()

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


@app.get("/")
def root():
    return RedirectResponse(url="/static/index.html")


@app.get("/activities")
def get_activities():
    return activities


@app.post("/activities/{activity_name}/signup")
def signup_for_activity(
    activity_name: str,
    email: str = Query(..., max_length=MAX_EMAIL_LENGTH, pattern=EMAIL_PATTERN),
):
    """Sign up a student for an activity"""
    # Sync endpoints run in a thread pool, so the capacity and duplicate
    # checks must be held together with the append.
    with signup_lock:
        activity = activities.get(activity_name)
        if activity is None:
            raise HTTPException(status_code=404, detail="Activity not found")

        participants = activity["participants"]
        normalized = email.casefold()
        if any(p.casefold() == normalized for p in participants):
            raise HTTPException(status_code=400, detail="Student is already signed up for this activity")

        if len(participants) >= activity["max_participants"]:
            raise HTTPException(status_code=400, detail="Activity is full")

        participants.append(email)
    return {"message": f"Signed up {email} for {activity_name}"}
