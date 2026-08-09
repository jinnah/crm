from tests.conftest import csrf_headers, login


def owner_session(client, make_user):
    make_user(email="owner@example.com", role="owner")
    return csrf_headers(login(client, "owner@example.com"))


def define_field(client, headers, **overrides):
    payload = {
        "key": "roof_type",
        "label": "Roof type",
        "type": "select",
        "options": ["Shingle", "Metal", "Flat"],
    }
    payload.update(overrides)
    return client.post("/api/v1/custom-fields", json=payload, headers=headers)


def create_lead(client, headers, **overrides):
    payload = {"name": "Lead", "email": "lead@example.com"}
    payload.update(overrides)
    return client.post("/api/v1/leads", json=payload, headers=headers)


def test_owner_manages_definitions_others_cannot(client, make_user) -> None:
    headers = owner_session(client, make_user)
    created = define_field(client, headers)
    assert created.status_code == 201
    assert created.json()["key"] == "roof_type"

    duplicate = define_field(client, headers)
    assert duplicate.status_code == 409

    for email, role in (("manager@example.com", "manager"), ("member@example.com", "team_member")):
        make_user(email=email, role=role)
        other_headers = csrf_headers(login(client, email))
        assert define_field(client, other_headers, key=f"x_{role}").status_code == 403
        # Everyone can read active definitions to render forms.
        listing = client.get("/api/v1/custom-fields", headers=other_headers)
        assert listing.status_code == 200
        assert [field["key"] for field in listing.json()] == ["roof_type"]
        client.post("/api/v1/auth/logout", headers=other_headers)


def test_definition_validation(client, make_user) -> None:
    headers = owner_session(client, make_user)
    assert define_field(client, headers, key="Bad Key!").status_code == 400
    assert define_field(client, headers, key="ok_key", type="telepathy").status_code == 400
    assert define_field(client, headers, key="sel", type="select", options=[]).status_code == 400
    assert define_field(client, headers, key="num", type="number", options=None).status_code == 201


def test_value_validation_per_type(client, make_user) -> None:
    headers = owner_session(client, make_user)
    define_field(client, headers)  # select roof_type
    define_field(client, headers, key="sq_footage", label="Square footage", type="number")
    define_field(client, headers, key="visit_date", label="Visit date", type="date")
    define_field(client, headers, key="insured", label="Insured", type="boolean")

    bad_cases = [
        {"roof_type": "Straw"},
        {"sq_footage": "big"},
        {"visit_date": "tomorrow"},
        {"insured": "yes"},
        {"unknown_key": 1},
    ]
    for case in bad_cases:
        response = create_lead(client, headers, custom_values=case)
        assert response.status_code == 400, case

    good = create_lead(
        client,
        headers,
        custom_values={
            "roof_type": "Metal",
            "sq_footage": 2400,
            "visit_date": "2026-08-15",
            "insured": True,
        },
    )
    assert good.status_code == 201
    assert good.json()["custom_values"] == {
        "roof_type": "Metal",
        "sq_footage": 2400,
        "visit_date": "2026-08-15",
        "insured": True,
    }


def test_required_field_enforced_on_create(client, make_user) -> None:
    headers = owner_session(client, make_user)
    define_field(client, headers, key="job_type", label="Job type", type="text", required=True)
    missing = create_lead(client, headers)
    assert missing.status_code == 400
    assert "Job type" in missing.json()["detail"]
    provided = create_lead(client, headers, custom_values={"job_type": "Repair"})
    assert provided.status_code == 201


def test_deactivation_keeps_stored_values(client, make_user) -> None:
    headers = owner_session(client, make_user)
    field = define_field(client, headers).json()
    lead = create_lead(client, headers, custom_values={"roof_type": "Metal"}).json()

    deactivate = client.patch(
        f"/api/v1/custom-fields/{field['id']}", json={"is_active": False}, headers=headers
    )
    assert deactivate.status_code == 200

    # Existing data is still visible on the lead…
    detail = client.get(f"/api/v1/leads/{lead['id']}", headers=headers).json()
    assert detail["custom_values"] == {"roof_type": "Metal"}
    # …but the inactive field no longer accepts new writes.
    rejected = client.patch(
        f"/api/v1/leads/{lead['id']}",
        json={"custom_values": {"roof_type": "Flat"}},
        headers=headers,
    )
    assert rejected.status_code == 400
    # And it is hidden from the default definitions list.
    assert client.get("/api/v1/custom-fields", headers=headers).json() == []
    full = client.get("/api/v1/custom-fields?include_inactive=true", headers=headers).json()
    assert [definition["key"] for definition in full] == ["roof_type"]


def test_team_member_can_set_values_on_assigned_lead(client, make_user, db) -> None:
    headers = owner_session(client, make_user)
    define_field(client, headers)
    member = make_user(email="member@example.com", role="team_member")
    lead = create_lead(client, headers).json()
    client.post(
        f"/api/v1/leads/{lead['id']}/assign", json={"user_id": str(member.id)}, headers=headers
    )

    member_headers = csrf_headers(login(client, "member@example.com"))
    response = client.patch(
        f"/api/v1/leads/{lead['id']}",
        json={"custom_values": {"roof_type": "Shingle"}},
        headers=member_headers,
    )
    assert response.status_code == 200
    assert response.json()["custom_values"] == {"roof_type": "Shingle"}
    # But managing definitions stays owner-only (tested above) and inactive
    # listing is denied.
    assert (
        client.get(
            "/api/v1/custom-fields?include_inactive=true", headers=member_headers
        ).status_code
        == 403
    )


def test_required_active_field_cannot_be_cleared(client, make_user) -> None:
    headers = owner_session(client, make_user)
    define_field(client, headers, key="job_type", label="Job type", type="text", required=True)
    define_field(client, headers, key="notes_opt", label="Optional note", type="text")
    lead = create_lead(
        client, headers, custom_values={"job_type": "Repair", "notes_opt": "keep me"}
    ).json()

    cleared = client.patch(
        f"/api/v1/leads/{lead['id']}",
        json={"custom_values": {"job_type": None}},
        headers=headers,
    )
    assert cleared.status_code == 400
    assert "cannot be cleared" in cleared.json()["detail"]

    # Optional values can still be cleared, and the required value survives.
    ok = client.patch(
        f"/api/v1/leads/{lead['id']}",
        json={"custom_values": {"notes_opt": None}},
        headers=headers,
    )
    assert ok.status_code == 200
    assert ok.json()["custom_values"] == {"job_type": "Repair"}
