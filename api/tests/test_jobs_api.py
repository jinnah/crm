"""Jobs: lifecycle, numbering, visibility, archival and appointment links."""

import uuid
from datetime import timedelta

from app.models import Job, Lead, LeadActivity, utcnow
from tests.conftest import csrf_headers, login


def owner_session(client, make_user, email="owner@example.com"):
    make_user(email=email, role="owner")
    response = login(client, email)
    assert response.status_code == 200
    return csrf_headers(response)


def make_lead(db, name="Pat Customer", **kwargs) -> Lead:
    lead = Lead(name=name, source="manual", **kwargs)
    db.add(lead)
    db.commit()
    return lead


def create_job(client, headers, lead_id, **overrides):
    payload = {"lead_id": str(lead_id), "title": "Roof repair", **overrides}
    response = client.post("/api/v1/jobs", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_customer_can_own_multiple_jobs_with_unique_numbers(client, db, make_user):
    headers = owner_session(client, make_user)
    lead = make_lead(db)
    first = create_job(client, headers, lead.id, title="Roof repair")
    second = create_job(client, headers, lead.id, title="Gutter replacement")

    year = utcnow().year
    assert first["job_number"] == f"J-{year}-0001"
    assert second["job_number"] == f"J-{year}-0002"
    assert first["lead_id"] == second["lead_id"] == str(lead.id)

    listing = client.get(f"/api/v1/jobs?lead_id={lead.id}").json()
    assert listing["total"] == 2
    # Creation is on the customer timeline.
    activities = db.query(LeadActivity).filter_by(lead_id=lead.id, type="job_created").all()
    assert len(activities) == 2


def test_job_lifecycle_is_centrally_enforced(client, db, make_user):
    headers = owner_session(client, make_user)
    lead = make_lead(db)
    job = create_job(client, headers, lead.id)

    ok = client.post(
        f"/api/v1/jobs/{job['id']}/status", json={"status": "in_progress"}, headers=headers
    )
    assert ok.status_code == 200
    assert ok.json()["started_at"] is not None

    done = client.post(
        f"/api/v1/jobs/{job['id']}/status", json={"status": "completed"}, headers=headers
    )
    assert done.status_code == 200

    # Completed is terminal: no way back.
    reopened = client.post(
        f"/api/v1/jobs/{job['id']}/status", json={"status": "in_progress"}, headers=headers
    )
    assert reopened.status_code == 409

    unknown = client.post(
        f"/api/v1/jobs/{job['id']}/status", json={"status": "doing_stuff"}, headers=headers
    )
    assert unknown.status_code == 400


def test_team_member_sees_only_their_jobs(client, db, make_user):
    headers = owner_session(client, make_user)
    member = make_user(email="member@example.com", role="team_member")
    mine = make_lead(db, name="Mine", assigned_to=member.id)
    other = make_lead(db, name="Someone else")
    mine_job = create_job(client, headers, mine.id, title="Visible")
    other_job = create_job(client, headers, other.id, title="Hidden")
    client.post("/api/v1/auth/logout")

    member_login = login(client, "member@example.com")
    member_headers = csrf_headers(member_login)

    listing = client.get("/api/v1/jobs").json()
    numbers = [item["job_number"] for item in listing["items"]]
    assert mine_job["job_number"] in numbers
    assert other_job["job_number"] not in numbers

    assert client.get(f"/api/v1/jobs/{other_job['id']}").status_code == 404
    # Team members cannot create jobs for customers that are not theirs.
    refused = client.post(
        "/api/v1/jobs",
        json={"lead_id": str(other.id), "title": "Nope"},
        headers=member_headers,
    )
    assert refused.status_code in (403, 404)


def test_jobs_archive_and_never_delete(client, db, make_user):
    headers = owner_session(client, make_user)
    lead = make_lead(db)
    job = create_job(client, headers, lead.id)

    refused = client.delete(f"/api/v1/jobs/{job['id']}", headers=headers)
    assert refused.status_code == 405

    archived = client.post(f"/api/v1/jobs/{job['id']}/archive", headers=headers)
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None

    # Archived jobs leave the default list but stay reachable.
    assert client.get("/api/v1/jobs").json()["total"] == 0
    assert client.get("/api/v1/jobs?archived=true").json()["total"] == 1

    restored = client.post(f"/api/v1/jobs/{job['id']}/restore", headers=headers)
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None


def test_appointment_links_only_within_the_same_customer(client, db, make_user):
    headers = owner_session(client, make_user)
    lead = make_lead(db)
    stranger = make_lead(db, name="Stranger")
    job = create_job(client, headers, lead.id)

    from app.models import Appointment

    start = utcnow() + timedelta(days=3)
    own = Appointment(
        lead_id=lead.id, start_at=start, end_at=start + timedelta(hours=1), timezone="UTC"
    )
    foreign = Appointment(
        lead_id=stranger.id, start_at=start, end_at=start + timedelta(hours=1), timezone="UTC"
    )
    db.add_all([own, foreign])
    db.commit()

    linked = client.post(
        f"/api/v1/jobs/{job['id']}/link-appointment",
        json={"appointment_id": str(own.id)},
        headers=headers,
    )
    assert linked.status_code == 200
    db.refresh(own)
    assert own.job_id == uuid.UUID(job["id"])
    # The scheduling fields and revision were untouched by linking.
    assert own.revision == 1

    refused = client.post(
        f"/api/v1/jobs/{job['id']}/link-appointment",
        json={"appointment_id": str(foreign.id)},
        headers=headers,
    )
    assert refused.status_code == 409


def test_job_search_finds_number_customer_and_address(client, db, make_user):
    headers = owner_session(client, make_user)
    lead = make_lead(db, name="Hector Alvarez")
    job = create_job(client, headers, lead.id, title="Attic fan", service_address="12 Elm Street")
    for term in (job["job_number"], "Hector", "Elm Street", "Attic"):
        found = client.get(f"/api/v1/jobs?query={term}").json()
        assert found["total"] == 1, term


def test_unauthenticated_requests_are_rejected(client, db, make_user):
    lead_id = uuid.uuid4()
    assert client.get("/api/v1/jobs").status_code == 401
    assert client.post("/api/v1/jobs", json={"lead_id": str(lead_id)}).status_code == 401


def test_archived_job_refuses_changes(client, db, make_user):
    headers = owner_session(client, make_user)
    lead = make_lead(db)
    job = create_job(client, headers, lead.id)
    client.post(f"/api/v1/jobs/{job['id']}/archive", headers=headers)

    edited = client.patch(f"/api/v1/jobs/{job['id']}", json={"title": "New title"}, headers=headers)
    assert edited.status_code == 409
    moved = client.post(
        f"/api/v1/jobs/{job['id']}/status", json={"status": "in_progress"}, headers=headers
    )
    assert moved.status_code == 409


def test_job_delete_with_history_explains_archival(client, db, make_user):
    headers = owner_session(client, make_user)
    lead = make_lead(db)
    job_data = create_job(client, headers, lead.id)
    job = db.get(Job, uuid.UUID(job_data["id"]))
    from app.models import CommercialDocument

    db.add(CommercialDocument(kind="quote", job_id=job.id, currency="USD"))
    db.commit()

    refused = client.delete(f"/api/v1/jobs/{job.id}", headers=headers)
    assert refused.status_code == 409
    assert "archived" in refused.json()["detail"]
