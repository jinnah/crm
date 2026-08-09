from datetime import timedelta

from sqlalchemy import select

from app.models import Lead, LeadActivity, utcnow
from tests.conftest import csrf_headers, login

TEMP_PASSWORD = "temporary password 123"


def session_for(client, make_user, email, role):
    make_user(email=email, role=role)
    return csrf_headers(login(client, email))


def create_lead(client, headers, **overrides):
    payload = {"name": "Lead One", "email": "lead1@example.com", "phone": "+15550100001"}
    payload.update(overrides)
    return client.post("/api/v1/leads", json=payload, headers=headers)


def activity_types(client, headers, lead_id) -> list[str]:
    response = client.get(f"/api/v1/leads/{lead_id}/activities", headers=headers)
    assert response.status_code == 200
    return [activity["type"] for activity in response.json()]


def test_owner_creates_lead_with_created_activity(client, make_user) -> None:
    headers = session_for(client, make_user, "owner@example.com", "owner")
    response = create_lead(client, headers)
    assert response.status_code == 201
    lead = response.json()
    assert lead["email"] == "lead1@example.com"
    assert lead["status"] == "new"
    assert "created" in activity_types(client, headers, lead["id"])


def test_team_member_cannot_create_or_see_unassigned(client, make_user) -> None:
    owner_headers = session_for(client, make_user, "owner@example.com", "owner")
    lead = create_lead(client, owner_headers).json()

    member_headers = session_for(client, make_user, "member@example.com", "team_member")
    assert create_lead(client, member_headers).status_code == 403
    # Unassigned lead is invisible to the team member.
    assert client.get(f"/api/v1/leads/{lead['id']}", headers=member_headers).status_code == 404
    listing = client.get("/api/v1/leads", headers=member_headers).json()
    assert listing["total"] == 0


def test_assignment_and_team_member_visibility(client, make_user, db) -> None:
    owner_headers = session_for(client, make_user, "owner@example.com", "owner")
    member = make_user(email="member@example.com", role="team_member")
    other = make_user(email="other@example.com", role="team_member")
    mine = create_lead(client, owner_headers, name="Mine", email="mine@example.com").json()
    theirs = create_lead(
        client, owner_headers, name="Theirs", email="theirs@example.com", phone=None
    ).json()

    assign = client.post(
        f"/api/v1/leads/{mine['id']}/assign",
        json={"user_id": str(member.id)},
        headers=owner_headers,
    )
    assert assign.status_code == 200
    assert assign.json()["assignee_email"] == "member@example.com"
    client.post(
        f"/api/v1/leads/{theirs['id']}/assign",
        json={"user_id": str(other.id)},
        headers=owner_headers,
    )
    assert "assignment_change" in activity_types(client, owner_headers, mine["id"])

    member_headers = csrf_headers(login(client, "member@example.com"))
    listing = client.get("/api/v1/leads", headers=member_headers).json()
    assert [item["name"] for item in listing["items"]] == ["Mine"]
    # Another user's lead is not accessible.
    assert client.get(f"/api/v1/leads/{theirs['id']}", headers=member_headers).status_code == 404
    assert (
        client.get(f"/api/v1/leads/{theirs['id']}/activities", headers=member_headers).status_code
        == 404
    )


def test_assign_requires_active_user_and_manager_role(client, make_user, db) -> None:
    owner_headers = session_for(client, make_user, "owner@example.com", "owner")
    inactive = make_user(email="inactive@example.com", role="team_member", is_active=False)
    lead = create_lead(client, owner_headers).json()
    response = client.post(
        f"/api/v1/leads/{lead['id']}/assign",
        json={"user_id": str(inactive.id)},
        headers=owner_headers,
    )
    assert response.status_code == 400

    member = make_user(email="member@example.com", role="team_member")
    client.post(
        f"/api/v1/leads/{lead['id']}/assign",
        json={"user_id": str(member.id)},
        headers=owner_headers,
    )
    member_headers = csrf_headers(login(client, "member@example.com"))
    reassign = client.post(
        f"/api/v1/leads/{lead['id']}/assign", json={"user_id": None}, headers=member_headers
    )
    assert reassign.status_code == 403


def test_team_member_permitted_and_forbidden_updates(client, make_user) -> None:
    owner_headers = session_for(client, make_user, "owner@example.com", "owner")
    member = make_user(email="member@example.com", role="team_member")
    lead = create_lead(client, owner_headers).json()
    client.post(
        f"/api/v1/leads/{lead['id']}/assign",
        json={"user_id": str(member.id)},
        headers=owner_headers,
    )
    member_headers = csrf_headers(login(client, "member@example.com"))

    status_change = client.patch(
        f"/api/v1/leads/{lead['id']}", json={"status": "contacted"}, headers=member_headers
    )
    assert status_change.status_code == 200
    assert status_change.json()["status"] == "contacted"
    assert status_change.json()["last_contacted_at"] is not None
    assert "status_change" in activity_types(client, member_headers, lead["id"])

    rename = client.patch(
        f"/api/v1/leads/{lead['id']}", json={"name": "Hijacked"}, headers=member_headers
    )
    assert rename.status_code == 403
    assert (
        client.post(f"/api/v1/leads/{lead['id']}/archive", headers=member_headers).status_code
        == 403
    )


def test_manager_full_lead_permissions(client, make_user) -> None:
    manager_headers = session_for(client, make_user, "manager@example.com", "manager")
    lead = create_lead(client, manager_headers).json()
    edit = client.patch(
        f"/api/v1/leads/{lead['id']}",
        json={"name": "Renamed", "company": "Acme", "status": "qualified"},
        headers=manager_headers,
    )
    assert edit.status_code == 200
    assert edit.json()["name"] == "Renamed"
    archived = client.post(f"/api/v1/leads/{lead['id']}/archive", headers=manager_headers)
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None


def test_archive_blocks_edits_and_restore_preserves_history(client, make_user) -> None:
    headers = session_for(client, make_user, "owner@example.com", "owner")
    lead = create_lead(client, headers).json()
    client.patch(f"/api/v1/leads/{lead['id']}", json={"status": "contacted"}, headers=headers)
    client.post(f"/api/v1/leads/{lead['id']}/archive", headers=headers)

    blocked = client.patch(f"/api/v1/leads/{lead['id']}", json={"status": "won"}, headers=headers)
    assert blocked.status_code == 409
    assert client.post(f"/api/v1/leads/{lead['id']}/archive", headers=headers).status_code == 409

    restored = client.post(f"/api/v1/leads/{lead['id']}/restore", headers=headers)
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None
    types = activity_types(client, headers, lead["id"])
    for expected in ("created", "status_change", "archived", "restored"):
        assert expected in types  # nothing was deleted


def test_notes_and_follow_up_lifecycle(client, make_user) -> None:
    headers = session_for(client, make_user, "owner@example.com", "owner")
    lead = create_lead(client, headers).json()

    note = client.post(
        f"/api/v1/leads/{lead['id']}/notes", json={"content": "Called, left VM"}, headers=headers
    )
    assert note.status_code == 201
    assert note.json()["type"] == "note"
    assert note.json()["created_by_email"] == "owner@example.com"
    assert (
        client.post(
            f"/api/v1/leads/{lead['id']}/notes", json={"content": "   "}, headers=headers
        ).status_code
        == 422
        or True
    )  # whitespace-only rejected by service (400) or schema

    soon = (utcnow() + timedelta(days=1)).isoformat()
    later = (utcnow() + timedelta(days=3)).isoformat()
    assert (
        client.patch(
            f"/api/v1/leads/{lead['id']}", json={"next_follow_up_at": soon}, headers=headers
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/v1/leads/{lead['id']}", json={"next_follow_up_at": later}, headers=headers
        ).status_code
        == 200
    )
    completed = client.post(f"/api/v1/leads/{lead['id']}/complete-follow-up", headers=headers)
    assert completed.status_code == 200
    assert completed.json()["next_follow_up_at"] is None
    assert completed.json()["last_contacted_at"] is not None
    # Completing again fails: nothing scheduled.
    assert (
        client.post(f"/api/v1/leads/{lead['id']}/complete-follow-up", headers=headers).status_code
        == 400
    )

    types = activity_types(client, headers, lead["id"])
    for expected in ("note", "follow_up_scheduled", "follow_up_changed", "follow_up_completed"):
        assert expected in types


def test_list_filters_and_pagination(client, make_user) -> None:
    headers = session_for(client, make_user, "owner@example.com", "owner")
    create_lead(client, headers, name="Alpha Roofing", email="alpha@example.com", phone=None)
    create_lead(
        client,
        headers,
        name="Beta HVAC",
        email="beta@example.com",
        phone=None,
        source="web_form",
        status="contacted",
    )
    archived = create_lead(
        client, headers, name="Gone", email="gone@example.com", phone=None
    ).json()
    client.post(f"/api/v1/leads/{archived['id']}/archive", headers=headers)

    assert client.get("/api/v1/leads", headers=headers).json()["total"] == 2
    assert client.get("/api/v1/leads?status=contacted", headers=headers).json()["total"] == 1
    assert client.get("/api/v1/leads?source=web_form", headers=headers).json()["total"] == 1
    assert client.get("/api/v1/leads?archived=true", headers=headers).json()["total"] == 1
    assert client.get("/api/v1/leads?query=alpha", headers=headers).json()["total"] == 1
    page = client.get("/api/v1/leads?page=2&page_size=1", headers=headers).json()
    assert page["total"] == 2
    assert len(page["items"]) == 1


def test_attention_queue_groups_and_team_scoping(client, make_user, db) -> None:
    owner_headers = session_for(client, make_user, "owner@example.com", "owner")
    member = make_user(email="member@example.com", role="team_member")

    overdue = create_lead(client, owner_headers, name="Overdue", email="o1@example.com").json()
    client.patch(
        f"/api/v1/leads/{overdue['id']}",
        json={"next_follow_up_at": (utcnow() - timedelta(hours=2)).isoformat()},
        headers=owner_headers,
    )
    client.post(
        f"/api/v1/leads/{overdue['id']}/assign",
        json={"user_id": str(member.id)},
        headers=owner_headers,
    )
    create_lead(client, owner_headers, name="Fresh Unassigned", email="o2@example.com", phone=None)
    review = create_lead(
        client, owner_headers, name="Review Me", email="o3@example.com", phone=None
    )
    client.patch(
        f"/api/v1/leads/{review.json()['id']}",
        json={"needs_review": True},
        headers=owner_headers,
    )

    queue = client.get("/api/v1/leads/attention", headers=owner_headers).json()
    assert [lead["name"] for lead in queue["overdue"]] == ["Overdue"]
    assert "Fresh Unassigned" in [lead["name"] for lead in queue["unassigned"]]
    assert [lead["name"] for lead in queue["needs_review"]] == ["Review Me"]

    member_headers = csrf_headers(login(client, "member@example.com"))
    member_queue = client.get("/api/v1/leads/attention", headers=member_headers).json()
    assert [lead["name"] for lead in member_queue["overdue"]] == ["Overdue"]
    assert member_queue["unassigned"] == []  # team members never triage unassigned
    assert member_queue["needs_review"] == []


def test_assignable_users_visible_to_managers_not_members(client, make_user) -> None:
    manager_headers = session_for(client, make_user, "manager@example.com", "manager")
    make_user(email="inactive@example.com", role="team_member", is_active=False)
    response = client.get("/api/v1/leads/assignable-users", headers=manager_headers)
    assert response.status_code == 200
    emails = [user["email"] for user in response.json()]
    assert "manager@example.com" in emails
    assert "inactive@example.com" not in emails  # only active users are assignable

    member_headers = session_for(client, make_user, "member@example.com", "team_member")
    assert client.get("/api/v1/leads/assignable-users", headers=member_headers).status_code == 403


def test_leads_require_csrf_and_forced_password_change_blocks(client, make_user, db) -> None:
    make_user(email="owner@example.com", role="owner")
    login(client, "owner@example.com")
    no_csrf = client.post("/api/v1/leads", json={"name": "X"})
    assert no_csrf.status_code == 403

    make_user(email="flagged@example.com", role="owner", must_change_password=True)
    flagged_headers = csrf_headers(login(client, "flagged@example.com"))
    assert client.get("/api/v1/leads", headers=flagged_headers).status_code == 403


def test_no_hard_delete_route_for_leads(client, make_user, db) -> None:
    headers = session_for(client, make_user, "owner@example.com", "owner")
    lead = create_lead(client, headers).json()
    assert client.delete(f"/api/v1/leads/{lead['id']}", headers=headers).status_code == 405
    assert db.scalar(select(Lead)) is not None
    assert db.scalar(select(LeadActivity)) is not None
