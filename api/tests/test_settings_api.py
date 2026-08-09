from tests.conftest import csrf_headers, login


def owner_headers(client, make_user):
    make_user(email="owner@example.com", role="owner")
    return csrf_headers(login(client, "owner@example.com"))


def test_owner_reads_and_updates_settings(client, make_user) -> None:
    headers = owner_headers(client, make_user)
    initial = client.get("/api/v1/settings/communication", headers=headers)
    assert initial.status_code == 200
    assert initial.json()["response_target_minutes"] == 5

    updated = client.patch(
        "/api/v1/settings/communication",
        json={
            "business_name": "Acme Roofing",
            "form_title": "Get your free estimate",
            "response_target_minutes": 10,
            "acknowledgment_template": "Hi {{lead_name}}, {{business_name}} received your request.",
        },
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["business_name"] == "Acme Roofing"
    assert updated.json()["response_target_minutes"] == 10


def test_non_owners_cannot_read_or_change_settings(client, make_user) -> None:
    for email, role in (("manager@example.com", "manager"), ("member@example.com", "team_member")):
        make_user(email=email, role=role)
        headers = csrf_headers(login(client, email))
        assert client.get("/api/v1/settings/communication", headers=headers).status_code == 403
        assert (
            client.patch(
                "/api/v1/settings/communication",
                json={"business_name": "Hijacked"},
                headers=headers,
            ).status_code
            == 403
        )
        client.post("/api/v1/auth/logout", headers=headers)


def test_template_variables_are_validated(client, make_user) -> None:
    headers = owner_headers(client, make_user)
    bad = client.patch(
        "/api/v1/settings/communication",
        json={"acknowledgment_template": "Hi {{lead_name}}, call {{secret_field}}"},
        headers=headers,
    )
    assert bad.status_code == 400
    assert "secret_field" in bad.json()["detail"]

    good = client.patch(
        "/api/v1/settings/communication",
        json={"alert_template": "New {{source}} lead {{lead_name}} ref {{lead_id}}"},
        headers=headers,
    )
    assert good.status_code == 200


def test_notification_phone_is_validated_conservatively(client, make_user) -> None:
    headers = owner_headers(client, make_user)
    # No country code: rejected rather than guessed.
    bad = client.patch(
        "/api/v1/settings/communication",
        json={"alert_destination_phone": "555-010-2000"},
        headers=headers,
    )
    assert bad.status_code == 400
    good = client.patch(
        "/api/v1/settings/communication",
        json={"alert_destination_phone": "+1 (555) 020-9999"},
        headers=headers,
    )
    assert good.status_code == 200
    assert good.json()["alert_destination_phone"] == "+15550209999"


def test_alert_requires_destination_before_enabling(client, make_user) -> None:
    headers = owner_headers(client, make_user)
    response = client.patch(
        "/api/v1/settings/communication", json={"alert_enabled": True}, headers=headers
    )
    assert response.status_code == 400


def test_public_form_info_exposes_only_safe_fields(client, make_user) -> None:
    headers = owner_headers(client, make_user)
    client.patch(
        "/api/v1/settings/communication",
        json={
            "form_title": "Request a quote",
            "business_name": "Acme Roofing",
            "alert_destination_phone": "+15550209999",
        },
        headers=headers,
    )
    # Unauthenticated request: no session, no CSRF token.
    public = client.get("/api/v1/public/form-info")
    assert public.status_code == 200
    assert public.json() == {
        "form_title": "Request a quote",
        "form_intro": "",
        "business_name": "Acme Roofing",
    }
    assert "alert_destination_phone" not in public.text
    assert "template" not in public.text
