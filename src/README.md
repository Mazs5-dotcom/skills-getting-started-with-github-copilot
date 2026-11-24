# Mergington High School Activities API

A super simple FastAPI application that allows students to view and sign up for extracurricular activities.

## Features

- View all available extracurricular activities
- Sign up for activities

## Getting Started

1. Install the dependencies:

   ```
   pip install fastapi uvicorn
   ```

2. Run the application:

   ```
   python app.py
   ```

3. Open your browser and go to:
   - API documentation: http://localhost:8000/docs
   - Alternative documentation: http://localhost:8000/redoc

## API Endpoints

| Method | Endpoint                                                          | Description                                                         |
| ------ | ----------------------------------------------------------------- | ------------------------------------------------------------------- |
| GET    | `/activities`                                                     | Get all activities with their details and current participant count |
| POST   | `/sessions`                                                       | Log in with `{"email", "password"}` and receive a bearer token      |
| POST   | `/activities/{activity_name}/signup?email=student@mergington.edu` | Sign up for an activity (requires `Authorization: Bearer <token>`)  |

Signing up requires a session from `POST /sessions`. Students always sign
themselves up; an `email` that names anyone else is rejected with 403. Staff
sign up the student named by `email`.

The built-in accounts are demo accounts for the in-memory database. Set
`MERGINGTON_ACCOUNTS_FILE` to a JSON list of accounts (`email`, `role` of
`student` or `staff`, hex `salt`, and hex PBKDF2-SHA256 `password_hash` with
600,000 iterations) to replace them.

## Data Model

The application uses a simple data model with meaningful identifiers:

1. **Activities** - Uses activity name as identifier:

   - Description
   - Schedule
   - Maximum number of participants allowed
   - List of student emails who are signed up

2. **Students** - Uses email as identifier:
   - Name
   - Grade level

All data is stored in memory, which means data will be reset when the server restarts.
