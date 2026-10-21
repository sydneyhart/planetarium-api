"""Check a demo deployment via real HTTP, including persistence across restarts."""

import json
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8000"


def request(path, method="GET", payload=None, token=None):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(payload).encode() if payload is not None else None
    with urlopen(
        Request(BASE + path, data=data, method=method, headers=headers), timeout=10
    ) as response:
        body = response.read()
        return json.loads(body) if body else None


def main():
    for attempt in range(60):
        try:
            request("/api/")
            break
        except (URLError, ConnectionError, TimeoutError):
            if attempt == 59:
                raise
            time.sleep(1)
    tokens = request(
        "/api/user/token/",
        "POST",
        {"email": "visitor@example.com", "password": "Planetarium-demo-2026!"},
    )
    access = tokens["access"]
    sessions = request("/api/planetarium/sessions/?upcoming=true", token=access)["results"]
    assert sessions
    session = sessions[0]
    if sys.argv[1] == "create":
        reservation = request(
            "/api/planetarium/reservations/",
            "POST",
            {"tickets": [{"show_session": session["id"], "row": 1, "seat": 1}]},
            access,
        )
        assert reservation["status"] == "confirmed"
    reservations = request("/api/planetarium/reservations/", token=access)["results"]
    assert reservations and reservations[0]["tickets"][0]["seat"] == 1
    availability = request(f"/api/planetarium/sessions/{session['id']}/availability/", token=access)
    assert {"row": 1, "seat": 1} in availability["booked_seats"]
    assert availability["available"] == session["capacity"] - 1
    with urlopen(BASE + "/static/rest_framework/css/bootstrap.min.css") as response:
        assert response.status == 200
    print("Verified JWT login, sessions, seat booking, static assets, and reservation persistence.")


if __name__ == "__main__":
    main()
