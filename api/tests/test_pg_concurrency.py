"""PostgreSQL concurrency tests for last-owner protection and duplicate-email
creation. SQLite ignores SELECT ... FOR UPDATE, so these run only against a
real disposable PostgreSQL database:

    TEST_POSTGRES_URL=postgresql+psycopg://user:pass@host:port/db \
        uv run pytest tests/test_pg_concurrency.py
"""

import os
import threading

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.models import Base, User
from app.security.passwords import hash_password
from app.services import users as user_service
from app.services.users import UserManagementError

TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not TEST_POSTGRES_URL,
    reason="TEST_POSTGRES_URL not set; PostgreSQL concurrency tests need a disposable database",
)

TEMP_PASSWORD = "temporary password 123"


def apply_postgres_only_constraints(engine) -> None:
    """Mirror the DDL that create_all cannot express.

    The appointment exclusion constraint is installed by the Alembic migration
    as raw PostgreSQL DDL, so a metadata-built schema would silently lack the
    database-level guarantee these tests exist to prove.
    """
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        connection.execute(
            text(
                "ALTER TABLE appointments ADD CONSTRAINT ex_appointments_no_overlap "
                "EXCLUDE USING gist ("
                "assigned_to WITH =, tstzrange(start_at, end_at) WITH &&"
                ") WHERE (status = 'scheduled' AND assigned_to IS NOT NULL)"
            )
        )


@pytest.fixture()
def pg_session_factory():
    engine = create_engine(TEST_POSTGRES_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)  # test-only; the app itself migrates via Alembic
    apply_postgres_only_constraints(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.drop_all(engine)
    engine.dispose()


def make_owner(session, email: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password(TEMP_PASSWORD),
        role="owner",
        is_active=True,
    )
    session.add(user)
    session.commit()
    return user


def test_concurrent_owner_removals_cannot_leave_zero_owners(pg_session_factory) -> None:
    """Two transactions each demote a different owner; the row locks must
    serialize them so exactly one succeeds and one active owner remains."""
    setup = pg_session_factory()
    owner_a = make_owner(setup, "owner-a@example.com")
    owner_b = make_owner(setup, "owner-b@example.com")
    setup.close()

    session1 = pg_session_factory()
    session2 = pg_session_factory()
    try:
        # Transaction 1 evaluates the demotion of owner A and holds the
        # FOR UPDATE locks (no commit yet).
        target_a = session1.get(User, owner_a.id)
        acting_1 = session1.get(User, owner_b.id)
        user_service.update_user(session1, acting_1, target_a, role="manager")

        # Transaction 2 concurrently demotes owner B; it must block on the
        # locked owner rows rather than seeing the stale two-owner count.
        outcome: dict[str, object] = {}

        def demote_b() -> None:
            try:
                target_b = session2.get(User, owner_b.id)
                acting_2 = session2.get(User, owner_a.id)
                user_service.update_user(session2, acting_2, target_b, role="manager")
                session2.commit()
                outcome["result"] = "committed"
            except UserManagementError as error:
                session2.rollback()
                outcome["result"] = "conflict"
                outcome["status"] = error.status_code

        thread = threading.Thread(target=demote_b)
        thread.start()
        thread.join(timeout=2)
        assert thread.is_alive(), "transaction 2 should be blocked on the row locks"

        session1.commit()  # releases the locks; transaction 2 re-evaluates
        thread.join(timeout=10)
        assert not thread.is_alive()

        assert outcome["result"] == "conflict"
        assert outcome["status"] == 409
    finally:
        session1.close()
        session2.close()

    check = pg_session_factory()
    active_owners = check.scalars(select(User).where(User.role == "owner", User.is_active)).all()
    check.close()
    assert len(active_owners) == 1, "concurrent removals must never leave zero active owners"


def test_concurrent_duplicate_email_creation_yields_one_conflict(pg_session_factory) -> None:
    """Two transactions insert the same normalized email; the unique index
    serializes them and the loser gets the safe 409, not an unhandled error."""
    setup = pg_session_factory()
    acting_owner = make_owner(setup, "owner@example.com")
    setup.close()

    session1 = pg_session_factory()
    session2 = pg_session_factory()
    try:
        acting_1 = session1.get(User, acting_owner.id)
        user_service.create_user(
            session1, acting_1, "New.Hire@Example.com", "team_member", TEMP_PASSWORD
        )  # flushed, uncommitted: holds the unique-index claim

        outcome: dict[str, object] = {}

        def create_duplicate() -> None:
            try:
                acting_2 = session2.get(User, acting_owner.id)
                user_service.create_user(
                    session2, acting_2, "new.hire@example.com", "manager", TEMP_PASSWORD
                )
                session2.commit()
                outcome["result"] = "committed"
            except UserManagementError as error:
                outcome["result"] = "conflict"
                outcome["status"] = error.status_code

        thread = threading.Thread(target=create_duplicate)
        thread.start()
        thread.join(timeout=2)
        assert thread.is_alive(), "transaction 2 should be blocked on the unique index"

        session1.commit()
        thread.join(timeout=10)
        assert not thread.is_alive()

        assert outcome["result"] == "conflict"
        assert outcome["status"] == 409
    finally:
        session1.close()
        session2.close()

    check = pg_session_factory()
    rows = check.scalars(select(User).where(User.email == "new.hire@example.com")).all()
    check.close()
    assert len(rows) == 1, "exactly one creation must succeed"


def test_concurrent_inbound_duplicates_yield_one_lead_and_activity(pg_session_factory) -> None:
    """Two simultaneous inbound submissions with the same Idempotency-Key must
    produce exactly one lead, one activity, and identical responses."""
    from sqlalchemy import func

    from app.api.v1.schemas import InboundEventRequest
    from app.config import get_settings
    from app.models import InboundEvent, Lead, LeadActivity
    from app.services.inbound import process_inbound_event

    payload = InboundEventRequest(
        channel="sms",
        provider="twilio",
        external_event_id="SM-concurrent",
        sender_phone="+15550107777",
        content="Concurrent hello",
    )
    settings = get_settings()
    barrier = threading.Barrier(2)
    results: list[tuple[str, str, bool]] = []
    errors: list[Exception] = []

    def submit() -> None:
        session = pg_session_factory()
        try:
            barrier.wait(timeout=5)
            result = process_inbound_event(session, payload, "evt-concurrent-1", settings)
            results.append((str(result.lead.id), str(result.activity.id), result.replayed))
        except Exception as error:  # pragma: no cover - failure diagnostics
            errors.append(error)
        finally:
            session.close()

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert not errors, errors
    assert len(results) == 2
    assert results[0][:2] == results[1][:2], "both requests must return the same lead/activity"

    check = pg_session_factory()
    assert check.scalar(select(func.count()).select_from(Lead)) == 1
    assert check.scalar(select(func.count()).select_from(LeadActivity)) == 1
    assert check.scalar(select(func.count()).select_from(InboundEvent)) == 1
    check.close()


def test_concurrent_settings_creation_yields_one_row(pg_session_factory) -> None:
    """Two transactions racing to create the settings row must leave exactly
    one; the unique singleton key decides the winner."""
    from sqlalchemy import func

    from app.models import CommunicationSettings
    from app.services.messaging import get_settings_row

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def create() -> None:
        session = pg_session_factory()
        try:
            barrier.wait(timeout=5)
            get_settings_row(session)
            session.commit()
        except Exception as error:  # pragma: no cover - failure diagnostics
            errors.append(error)
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert not errors, errors

    check = pg_session_factory()
    assert check.scalar(func.count(CommunicationSettings.id)) == 1
    # Reads stay deterministic afterwards.
    first = get_settings_row(check).id
    assert get_settings_row(check).id == first
    check.close()


class _CountingSender:
    """Records every provider contact; deliberately slow so concurrent
    requests overlap the window between reservation and outcome."""

    def __init__(self, delay=0.4):
        self.delay = delay
        self.sent = []
        self._lock = threading.Lock()

    def send(self, message):
        import time as _time

        from app.services.messaging import SendOutcome

        with self._lock:
            self.sent.append(str(message.id))
            index = len(self.sent)
        _time.sleep(self.delay)
        return SendOutcome(status="submitted", provider_sid=f"SMconc{index:026d}")


def _seed_lead(session, phone="+15550990001"):
    from app.models import Lead

    lead = Lead(name="Concurrent Lead", phone=phone, source="manual")
    session.add(lead)
    session.commit()
    return lead


def test_concurrent_same_key_contacts_provider_once(pg_session_factory) -> None:
    from sqlalchemy import func

    from app.config import get_settings
    from app.models import OutboundMessage
    from app.services.messaging import send_lead_sms

    setup = pg_session_factory()
    owner = make_owner(setup, "owner@example.com")
    lead = _seed_lead(setup)
    setup.close()

    sender = _CountingSender()
    settings = get_settings()
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    errors: list[Exception] = []

    def send() -> None:
        session = pg_session_factory()
        try:
            acting = session.get(User, owner.id)
            target = session.get(Lead, lead.id)
            barrier.wait(timeout=5)
            message = send_lead_sms(
                session, acting, target, "same key", "conc-same-key", settings, sender
            )
            outcomes.append(str(message.id))
        except Exception as error:
            errors.append(error)
            session.rollback()
        finally:
            session.close()

    from app.models import Lead

    threads = [threading.Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(set(outcomes)) == 1, "same key must return the same record"
    assert len(sender.sent) == 1, "the provider must be contacted exactly once"

    check = pg_session_factory()
    assert check.scalar(func.count(OutboundMessage.id)) == 1
    check.close()


def test_concurrent_different_keys_yield_one_send_and_a_conflict(pg_session_factory) -> None:
    """A second request with a different key must see the durable pending row
    and be refused without contacting the provider."""
    from sqlalchemy import func

    from app.config import get_settings
    from app.models import Lead, OutboundMessage
    from app.services.messaging import MessagingError, send_lead_sms

    setup = pg_session_factory()
    owner = make_owner(setup, "owner@example.com")
    lead = _seed_lead(setup, "+15550990002")
    setup.close()

    sender = _CountingSender(delay=1.5)
    settings = get_settings()
    barrier = threading.Barrier(2)
    conflicts: list[int] = []
    sent_ok: list[str] = []
    errors: list[Exception] = []

    def send(key: str) -> None:
        session = pg_session_factory()
        try:
            acting = session.get(User, owner.id)
            target = session.get(Lead, lead.id)
            barrier.wait(timeout=5)
            message = send_lead_sms(session, acting, target, f"body {key}", key, settings, sender)
            sent_ok.append(str(message.id))
        except MessagingError as error:
            conflicts.append(error.status_code)
            session.rollback()
        except Exception as error:
            errors.append(error)
            session.rollback()
        finally:
            session.close()

    threads = [
        threading.Thread(target=send, args=("conc-key-a",)),
        threading.Thread(target=send, args=("conc-key-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert conflicts == [409], f"expected exactly one 409, got {conflicts}"
    assert len(sent_ok) == 1
    assert len(sender.sent) == 1, "the provider must be contacted exactly once"

    check = pg_session_factory()
    assert check.scalar(func.count(OutboundMessage.id)) == 1
    check.close()


def test_concurrent_recovery_records_one_transition(pg_session_factory) -> None:
    """Two simultaneous recoveries of one abandoned pending message must
    produce a single transition and at most one explanatory activity."""
    from datetime import timedelta

    from sqlalchemy import func
    from sqlalchemy import select as sa_select

    from app.models import LeadActivity, OutboundMessage, utcnow
    from app.services.messaging import PENDING_RECOVERY_MINUTES, recover_abandoned_pending

    setup = pg_session_factory()
    lead = _seed_lead(setup, "+15550990003")
    stranded = OutboundMessage(
        lead_id=lead.id,
        purpose="human_reply",
        to_phone="+15550990003",
        body="abandoned by a crash",
        status="pending",
        idempotency_key_digest="digest-abandoned-conc",
        created_at=utcnow() - timedelta(minutes=PENDING_RECOVERY_MINUTES + 5),
    )
    setup.add(stranded)
    setup.commit()
    setup.close()

    barrier = threading.Barrier(2)
    recovered_counts: list[int] = []
    errors: list[Exception] = []

    def recover() -> None:
        session = pg_session_factory()
        try:
            barrier.wait(timeout=5)
            count = recover_abandoned_pending(session, lead.id)
            session.commit()
            recovered_counts.append(count)
        except Exception as error:
            errors.append(error)
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=recover) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert sum(recovered_counts) == 1, f"exactly one transition expected, got {recovered_counts}"

    check = pg_session_factory()
    row = check.scalar(
        sa_select(OutboundMessage).where(
            OutboundMessage.idempotency_key_digest == "digest-abandoned-conc"
        )
    )
    assert row.status == "unknown"  # never "failed"
    activities = check.scalars(
        sa_select(LeadActivity).where(LeadActivity.type == "message_status")
    ).all()
    assert len(activities) <= 1
    # Recovery never resends: no new outbound row appeared.
    assert check.scalar(func.count(OutboundMessage.id)) == 1
    check.close()


def test_recovered_message_is_not_resent_automatically(pg_session_factory) -> None:
    from datetime import timedelta

    from app.config import get_settings
    from app.models import Lead, OutboundMessage, utcnow
    from app.services.messaging import PENDING_RECOVERY_MINUTES, send_lead_sms

    setup = pg_session_factory()
    owner = make_owner(setup, "owner@example.com")
    lead = _seed_lead(setup, "+15550990004")
    setup.add(
        OutboundMessage(
            lead_id=lead.id,
            purpose="human_reply",
            to_phone="+15550990004",
            body="interrupted original",
            status="pending",
            idempotency_key_digest="digest-not-resent",
            created_at=utcnow() - timedelta(minutes=PENDING_RECOVERY_MINUTES + 5),
        )
    )
    setup.commit()
    setup.close()

    sender = _CountingSender(delay=0)
    session = pg_session_factory()
    acting = session.get(User, owner.id)
    target = session.get(Lead, lead.id)
    # A fresh, deliberate send unblocks the lead but must not resend the old body.
    send_lead_sms(
        session, acting, target, "a new deliberate message", "fresh-key", get_settings(), sender
    )
    session.close()

    assert len(sender.sent) == 1
    check = pg_session_factory()
    from sqlalchemy import select as sa_select

    stranded = check.scalar(
        sa_select(OutboundMessage).where(
            OutboundMessage.idempotency_key_digest == "digest-not-resent"
        )
    )
    assert stranded.status == "unknown"
    assert stranded.error_code == "abandoned"
    check.close()


# --- appointment scheduling and reminders -------------------------------


def seed_scheduling_settings(session):
    """Round-the-clock availability so a fixed future instant is always bookable."""
    from app.services.messaging import get_settings_row

    row = get_settings_row(session)
    row.business_timezone = "UTC"
    row.min_booking_notice_minutes = 0
    row.max_booking_days_ahead = 365
    row.buffer_before_minutes = 0
    row.buffer_after_minutes = 0
    row.business_hours = {
        key: [["00:00", "23:59"]] for key in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    }
    session.commit()
    return row


def seed_appointment_lead(session, phone="+15550600001"):
    from app.models import Lead

    lead = Lead(name="Concurrent Booking", phone=phone, source="manual")
    session.add(lead)
    session.commit()
    return lead


def test_concurrent_overlapping_bookings_cannot_both_succeed(pg_session_factory) -> None:
    """Two requests for the same staff member at overlapping times: one wins,
    the other is told the slot is gone rather than double-booking."""
    from datetime import timedelta

    from sqlalchemy import func

    from app.models import Appointment, Lead, utcnow
    from app.services import scheduling
    from app.services.messaging import get_settings_row

    setup = pg_session_factory()
    staff = make_owner(setup, "tech@example.com")
    lead = seed_appointment_lead(setup)
    seed_scheduling_settings(setup)
    setup.close()

    start = (utcnow() + timedelta(days=3)).replace(microsecond=0)
    barrier = threading.Barrier(2)
    booked: list[str] = []
    conflicts: list[int] = []
    errors: list[Exception] = []

    def book(offset_minutes: int) -> None:
        session = pg_session_factory()
        try:
            settings_row = get_settings_row(session)
            target = session.get(Lead, lead.id)
            barrier.wait(timeout=5)
            scheduling.lock_staff_calendar(session, staff.id)
            appointment = scheduling.create_appointment(
                session,
                None,
                target,
                settings_row,
                start_at=start + timedelta(minutes=offset_minutes),
                duration_minutes=60,
                staff_id=staff.id,
            )
            session.commit()
            booked.append(str(appointment.id))
        except scheduling.SlotUnavailableError as error:
            session.rollback()
            conflicts.append(error.status_code)
        except Exception as error:  # pragma: no cover - failure diagnostics
            session.rollback()
            errors.append(error)
        finally:
            session.close()

    threads = [
        threading.Thread(target=book, args=(0,)),
        threading.Thread(target=book, args=(30,)),  # overlaps the first by half an hour
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(booked) == 1, f"exactly one booking may win: {booked} {conflicts}"
    assert conflicts == [409], f"the loser must get a conflict, got {conflicts}"

    check = pg_session_factory()
    assert check.scalar(func.count(Appointment.id)) == 1
    check.close()


def test_database_refuses_overlap_even_without_the_application_check(pg_session_factory) -> None:
    """The exclusion constraint is the last line of defence: an insert that
    bypasses the service layer entirely is still rejected."""
    from datetime import timedelta

    from sqlalchemy.exc import IntegrityError

    from app.models import Appointment, utcnow

    setup = pg_session_factory()
    staff = make_owner(setup, "tech@example.com")
    lead = seed_appointment_lead(setup, "+15550600005")
    start = (utcnow() + timedelta(days=4)).replace(microsecond=0)

    def raw_appointment(offset_minutes: int, subject: str) -> Appointment:
        return Appointment(
            lead_id=lead.id,
            assigned_to=staff.id,
            subject=subject,
            start_at=start + timedelta(minutes=offset_minutes),
            end_at=start + timedelta(minutes=offset_minutes + 60),
            timezone="UTC",
            status="scheduled",
            origin="staff",
        )

    setup.add(raw_appointment(0, "First"))
    setup.commit()

    setup.add(raw_appointment(30, "Overlapping"))
    with pytest.raises(IntegrityError):
        setup.commit()
    setup.rollback()

    # A canceled appointment releases its time, so the same window reopens.
    first = setup.scalar(select(Appointment))
    first.status = "canceled"
    setup.commit()
    setup.add(raw_appointment(30, "After cancellation"))
    setup.commit()
    setup.close()


def test_concurrent_notification_claims_contact_the_provider_once(pg_session_factory) -> None:
    """Overlapping scheduled dispatch runs must not send the same reminder twice."""
    from datetime import timedelta

    from app.config import get_settings
    from app.models import Appointment, AppointmentNotification, utcnow
    from app.services import appointment_notifications as notifications

    setup = pg_session_factory()
    seed_scheduling_settings(setup)
    lead = seed_appointment_lead(setup, "+15550600002")
    start = utcnow() + timedelta(days=2)
    appointment = Appointment(
        lead_id=lead.id,
        subject="Reminder target",
        start_at=start,
        end_at=start + timedelta(hours=1),
        timezone="UTC",
        status="scheduled",
        origin="staff",
    )
    setup.add(appointment)
    setup.commit()
    setup.add(
        AppointmentNotification(
            appointment_id=appointment.id,
            type="reminder",
            occurrence="1:1",
            scheduled_at=utcnow() - timedelta(minutes=1),
            state="pending",
            idempotency_key_digest="digest-concurrent-reminder",
        )
    )
    setup.commit()
    setup.close()

    sender = _CountingSender(delay=0.6)
    settings = get_settings()
    barrier = threading.Barrier(2)
    claims: list[int] = []
    errors: list[Exception] = []

    def dispatch() -> None:
        session = pg_session_factory()
        try:
            barrier.wait(timeout=5)
            counts = notifications.dispatch_due(session, settings, sender)
            claims.append(counts["claimed"])
        except Exception as error:  # pragma: no cover - failure diagnostics
            errors.append(error)
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=dispatch) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert sum(claims) == 1, f"the reminder may be claimed once only: {claims}"
    assert len(sender.sent) == 1, "the provider must be contacted exactly once"

    check = pg_session_factory()
    row = check.scalar(select(AppointmentNotification))
    assert row.state == "sent"
    assert row.outbound_message_id is not None
    check.close()


# --- corrective-milestone races ------------------------------------------


def test_concurrent_same_key_bookings_return_one_appointment_and_capability(
    pg_session_factory,
) -> None:
    """Two racing submissions of one booking key: one appointment, and both
    callers can be handed the same working manage capability."""
    from datetime import timedelta

    from sqlalchemy import func

    from app.config import get_settings
    from app.models import Appointment, BookingLink, Lead, utcnow
    from app.security.tokens import digest_token, generate_token
    from app.services import booking as booking_service
    from app.services import scheduling

    settings = get_settings()
    setup = pg_session_factory()
    staff = make_owner(setup, "tech@example.com")
    lead = seed_appointment_lead(setup, "+15550600020")
    seed_scheduling_settings(setup)
    link = BookingLink(
        lead_id=lead.id,
        assigned_to=staff.id,
        token_digest=digest_token(generate_token(), settings.session_token_pepper),
        expires_at=utcnow() + timedelta(days=7),
    )
    setup.add(link)
    setup.commit()
    link_id = link.id
    setup.close()

    start = (utcnow() + timedelta(days=3)).replace(microsecond=0, minute=0, second=0)
    booking_key = "race-booking-key-0001"
    reference_digest = digest_token(
        f"booking:{link_id}:{booking_key}", settings.session_token_pepper
    )
    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, str | None]] = []
    errors: list[Exception] = []

    def book() -> None:
        session = pg_session_factory()
        try:
            from app.services.messaging import get_settings_row

            row = get_settings_row(session)
            target = session.get(Lead, lead.id)
            barrier.wait(timeout=5)
            scheduling.lock_staff_calendar(session, staff.id)
            existing = session.scalar(
                select(Appointment).where(Appointment.booking_key_digest == reference_digest)
            )
            if existing is not None:
                raw, digest = booking_service.derive_manage_token(settings, link_id, booking_key)
                session.commit()
                outcomes.append(("replay", raw if digest == existing.manage_token_digest else None))
                return
            raw, digest = booking_service.derive_manage_token(settings, link_id, booking_key)
            appointment = scheduling.create_appointment(
                session,
                None,
                target,
                row,
                start_at=start,
                duration_minutes=60,
                staff_id=staff.id,
                origin="customer",
                booking_reference=booking_service.new_booking_reference(),
                manage_token_digest=digest,
            )
            appointment.booking_key_digest = reference_digest
            session.commit()
            outcomes.append(("created", raw))
        except Exception as error:
            session.rollback()
            errors.append(error)
        finally:
            session.close()

    threads = [threading.Thread(target=book) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert sorted(kind for kind, _ in outcomes) == ["created", "replay"]
    capabilities = {raw for _, raw in outcomes}
    assert len(capabilities) == 1 and None not in capabilities, (
        "both callers must receive the same working capability"
    )
    check = pg_session_factory()
    assert check.scalar(func.count(Appointment.id)) == 1
    check.close()


def _seed_claimable_reminder(pg_session_factory, phone: str):
    """One scheduled appointment with a due reminder, ready to claim."""
    from datetime import timedelta

    from app.models import Appointment, AppointmentNotification, utcnow

    setup = pg_session_factory()
    seed_scheduling_settings(setup)
    lead = seed_appointment_lead(setup, phone)
    start = utcnow() + timedelta(days=2)
    appointment = Appointment(
        lead_id=lead.id,
        subject="Race target",
        start_at=start,
        end_at=start + timedelta(hours=1),
        timezone="UTC",
        status="scheduled",
        origin="staff",
        revision=1,
    )
    setup.add(appointment)
    setup.commit()
    setup.add(
        AppointmentNotification(
            appointment_id=appointment.id,
            type="reminder",
            occurrence="r1:1",
            schedule_revision=1,
            scheduled_at=utcnow() - timedelta(minutes=1),
            state="pending",
            idempotency_key_digest=f"digest-race-{phone}",
        )
    )
    setup.commit()
    appointment_id = appointment.id
    setup.close()
    return appointment_id


def test_claim_versus_cancel_never_sends_an_obsolete_reminder(pg_session_factory) -> None:
    """A cancellation racing the scheduler: whichever wins, the customer never
    receives a reminder for an appointment that was canceled first."""
    from app.config import get_settings
    from app.models import AppointmentNotification
    from app.services import appointment_notifications as notifications
    from app.services import scheduling

    appointment_id = _seed_claimable_reminder(pg_session_factory, "+15550600021")
    settings = get_settings()
    sender = _CountingSender(delay=0.4)
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def dispatch() -> None:
        session = pg_session_factory()
        try:
            barrier.wait(timeout=5)
            notifications.dispatch_due(session, settings, sender)
        except Exception as error:
            errors.append(error)
            session.rollback()
        finally:
            session.close()

    def cancel() -> None:
        session = pg_session_factory()
        try:
            barrier.wait(timeout=5)
            appointment = scheduling.lock_appointment(session, appointment_id)
            appointment, changed = scheduling.set_disposition(
                session, None, appointment, "canceled", "race cancel"
            )
            if changed:
                notifications.suppress_pending(
                    session, appointment_id, ("reminder", "confirmation")
                )
            session.commit()
        except Exception as error:
            errors.append(error)
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=dispatch), threading.Thread(target=cancel)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, errors

    check = pg_session_factory()
    row = check.scalar(select(AppointmentNotification))
    # Either the reminder was suppressed before sending, or delivery began
    # before the cancel landed — in which case the deliver-side guard checked
    # a still-scheduled appointment. What must NEVER happen is a send that
    # starts after the cancellation was durable.
    if sender.sent:
        assert row.state == "sent"
        assert row.attempted_at is not None
    else:
        assert row.state == "suppressed"
        assert row.attempted_at is None, "a suppressed reminder must never have started sending"
    check.close()


def test_claim_versus_reschedule_never_sends_an_old_time(pg_session_factory) -> None:
    """A reschedule racing the scheduler: a reminder describing the old time
    is suppressed, and only content for the current revision may send."""
    from datetime import timedelta

    from app.config import get_settings
    from app.models import AppointmentNotification, utcnow
    from app.services import appointment_notifications as notifications
    from app.services import scheduling
    from app.services.messaging import get_settings_row

    appointment_id = _seed_claimable_reminder(pg_session_factory, "+15550600022")
    settings = get_settings()
    sender = _CountingSender(delay=0.4)
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def dispatch() -> None:
        session = pg_session_factory()
        try:
            barrier.wait(timeout=5)
            notifications.dispatch_due(session, settings, sender)
        except Exception as error:
            errors.append(error)
            session.rollback()
        finally:
            session.close()

    def reschedule() -> None:
        session = pg_session_factory()
        try:
            row = get_settings_row(session)
            barrier.wait(timeout=5)
            appointment = scheduling.lock_appointment(session, appointment_id)
            scheduling.lock_staff_calendar(session, appointment.assigned_to)
            appointment, changed = scheduling.reschedule_appointment(
                session,
                None,
                appointment,
                row,
                start_at=utcnow() + timedelta(days=4),
                expected_revision=1,
                enforce_notice=False,
            )
            if changed:
                notifications.suppress_pending(session, appointment_id, ("reminder",))
            session.commit()
        except Exception as error:
            errors.append(error)
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=dispatch), threading.Thread(target=reschedule)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors, errors

    check = pg_session_factory()
    row = check.scalar(
        select(AppointmentNotification).where(AppointmentNotification.occurrence == "r1:1")
    )
    if sender.sent:
        # Delivery won the race: the locks serialize it against the
        # reschedule, so the revision it verified was still current when the
        # message content was produced.
        assert row.state == "sent"
    else:
        assert row.state == "suppressed"
        assert row.attempted_at is None
    check.close()


def test_concurrent_duplicate_callsid_produces_one_record(pg_session_factory) -> None:
    from sqlalchemy import func

    from app.api.v1.schemas import VoiceCallCompletedRequest
    from app.config import get_settings
    from app.models import Lead, LeadActivity, VoiceCall
    from app.services.voice import process_voice_completion

    setup = pg_session_factory()
    seed_scheduling_settings(setup)
    setup.close()

    payload = VoiceCallCompletedRequest(
        call_sid="CApgrace0000000000000000000001",
        caller_phone="+15550600023",
        caller_name="PG Caller",
        summary="Race summary",
    )
    settings = get_settings()
    barrier = threading.Barrier(2)
    results = []
    errors: list[Exception] = []

    def submit() -> None:
        session = pg_session_factory()
        try:
            barrier.wait(timeout=5)
            result = process_voice_completion(session, payload, settings)
            results.append((str(result.call.id), result.replayed))
        except Exception as error:
            errors.append(error)
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(results) == 2
    assert results[0][0] == results[1][0], "both must resolve to one call record"
    assert sorted(replayed for _, replayed in results) == [False, True]

    check = pg_session_factory()
    assert check.scalar(func.count(VoiceCall.id)) == 1
    assert (
        check.scalar(
            select(func.count()).select_from(LeadActivity).where(LeadActivity.type == "voice_call")
        )
        == 1
    )
    assert check.scalar(func.count(Lead.id)) == 1
    check.close()


# --- jobs, commercial documents, payments and email ----------------------


def _commercial_setup(pg_session_factory):
    """Owner, customer, job and the settings row, committed and ready."""
    import tempfile

    from app.models import Job, Lead
    from app.services.messaging import get_settings_row
    from app.services.numbering import allocate_number
    from app.services.storage import LocalDiskStorage

    session = pg_session_factory()
    owner = make_owner(session, "commercial-owner@example.com")
    get_settings_row(session)
    lead = Lead(name="Concurrent Customer", email="concurrent@example.com", source="manual")
    session.add(lead)
    session.commit()
    job = Job(job_number=allocate_number(session, "job", "J"), lead_id=lead.id)
    session.add(job)
    session.commit()
    storage = LocalDiskStorage(tempfile.mkdtemp(prefix="crm-pg-docs-"))
    return session, owner, lead, job, storage


def _issued_quote(session, owner, job, storage):
    from app.config import get_settings
    from app.services import commercial
    from app.services.messaging import get_settings_row

    quote = commercial.create_draft(session, owner, job, get_settings_row(session), kind="quote")
    commercial.replace_lines(
        session,
        quote,
        [{"description": "Work", "quantity_milli": 1000, "unit_price_minor": 10000}],
        discount_bp=0,
        customer_notes="",
        terms="",
    )
    commercial.issue(session, owner, storage, quote, get_settings_row(session), get_settings())
    session.commit()
    return quote


def test_concurrent_number_allocation_is_unique(pg_session_factory) -> None:
    from app.models import NumberSequence
    from app.services.numbering import allocate_number

    setup = pg_session_factory()
    setup.close()

    barrier = threading.Barrier(4)
    numbers: list[str] = []
    errors: list[Exception] = []

    def allocate() -> None:
        session = pg_session_factory()
        try:
            barrier.wait(timeout=5)
            value = allocate_number(session, "invoice", "INV")
            session.commit()
            numbers.append(value)
        except Exception as error:  # noqa: BLE001
            errors.append(error)
            session.rollback()
        finally:
            session.close()

    threads = [threading.Thread(target=allocate) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(numbers) == 4
    assert len(set(numbers)) == 4, "every allocation must be unique"
    suffixes = sorted(int(number.rsplit("-", 1)[1]) for number in numbers)
    assert suffixes == [1, 2, 3, 4]

    check = pg_session_factory()
    row = check.scalar(select(NumberSequence).where(NumberSequence.kind == "invoice"))
    assert row is not None and row.last_value == 4
    check.close()


def test_concurrent_accept_versus_decline_has_one_winner(pg_session_factory) -> None:
    from app.models import CommercialDocument, CommercialDocumentVersion
    from app.services import commercial
    from app.services.commercial import CommercialError

    session, owner, _lead, job, storage = _commercial_setup(pg_session_factory)
    quote = _issued_quote(session, owner, job, storage)
    quote_id = quote.id
    session.close()

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    refusals: list[Exception] = []

    def respond(accept: bool) -> None:
        worker = pg_session_factory()
        try:
            version = worker.scalar(
                select(CommercialDocumentVersion).where(
                    CommercialDocumentVersion.document_id == quote_id,
                    CommercialDocumentVersion.superseded_at.is_(None),
                )
            )
            barrier.wait(timeout=5)
            document = commercial.respond_to_quote(
                worker, quote_id, version, accept=accept, typed_name="Race Customer"
            )
            worker.commit()
            outcomes.append(document.status)
        except CommercialError as error:
            refusals.append(error)
            worker.rollback()
        except Exception as error:  # noqa: BLE001
            refusals.append(error)
            worker.rollback()
        finally:
            worker.close()

    threads = [
        threading.Thread(target=respond, args=(True,)),
        threading.Thread(target=respond, args=(False,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    # Exactly one deterministic winner; the loser got a controlled refusal.
    assert len(outcomes) == 1, (outcomes, refusals)
    assert len(refusals) == 1
    assert all(isinstance(error, CommercialError) for error in refusals)

    check = pg_session_factory()
    final = check.get(CommercialDocument, quote_id)
    assert final.status == outcomes[0]
    assert final.responded_at is not None
    check.close()


def test_concurrent_payments_cannot_exceed_invoice_balance(pg_session_factory) -> None:
    from sqlalchemy import func

    from app.config import get_settings
    from app.models import CommercialDocument, Payment, utcnow
    from app.services import commercial
    from app.services.commercial import CommercialError
    from app.services.messaging import get_settings_row

    session, owner, _lead, job, storage = _commercial_setup(pg_session_factory)
    invoice = commercial.create_draft(
        session, owner, job, get_settings_row(session), kind="invoice"
    )
    commercial.replace_lines(
        session,
        invoice,
        [{"description": "Work", "quantity_milli": 1000, "unit_price_minor": 10000}],
        discount_bp=0,
        customer_notes="",
        terms="",
    )
    commercial.issue(session, owner, storage, invoice, get_settings_row(session), get_settings())
    session.commit()
    invoice_id = invoice.id
    owner_id = owner.id
    session.close()

    barrier = threading.Barrier(2)
    successes: list[str] = []
    refusals: list[Exception] = []

    def pay(key: str) -> None:
        worker = pg_session_factory()
        try:
            from app.models import User as UserModel

            acting = worker.get(UserModel, owner_id)
            barrier.wait(timeout=5)
            payment, _receipt = commercial.record_payment(
                worker,
                acting,
                storage,
                invoice_id,
                get_settings_row(worker),
                get_settings(),
                amount_minor=8000,  # two of these exceed the 10000 total
                currency="USD",
                method="cash",
                paid_on=utcnow(),
                idempotency_key=key,
            )
            worker.commit()
            successes.append(str(payment.id))
        except CommercialError as error:
            refusals.append(error)
            worker.rollback()
        finally:
            worker.close()

    threads = [
        threading.Thread(target=pay, args=("race-pay-A",)),
        threading.Thread(target=pay, args=("race-pay-B",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(successes) == 1, (successes, refusals)
    assert len(refusals) == 1

    check = pg_session_factory()
    final = check.get(CommercialDocument, invoice_id)
    assert final.amount_paid_minor == 8000
    assert check.scalar(select(func.count()).select_from(Payment)) == 1
    check.close()


def test_concurrent_quote_conversion_creates_one_invoice(pg_session_factory) -> None:
    from sqlalchemy import func

    from app.config import get_settings
    from app.models import CommercialDocument, CommercialDocumentVersion
    from app.services import commercial
    from app.services.messaging import get_settings_row

    session, owner, _lead, job, storage = _commercial_setup(pg_session_factory)
    quote = _issued_quote(session, owner, job, storage)
    version = session.scalar(
        select(CommercialDocumentVersion).where(CommercialDocumentVersion.document_id == quote.id)
    )
    commercial.respond_to_quote(session, quote.id, version, accept=True, typed_name="Race Customer")
    session.commit()
    quote_id = quote.id
    owner_id = owner.id
    session.close()

    barrier = threading.Barrier(2)
    invoice_ids: list[str] = []
    errors: list[Exception] = []

    def convert() -> None:
        worker = pg_session_factory()
        try:
            from app.models import User as UserModel

            acting = worker.get(UserModel, owner_id)
            fresh_quote = worker.get(CommercialDocument, quote_id)
            barrier.wait(timeout=5)
            invoice = commercial.convert_quote_to_invoice(
                worker, acting, storage, fresh_quote, get_settings_row(worker), get_settings()
            )
            worker.commit()
            invoice_ids.append(str(invoice.id))
        except Exception as error:  # noqa: BLE001
            errors.append(error)
            worker.rollback()
        finally:
            worker.close()

    threads = [threading.Thread(target=convert) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(set(invoice_ids)) == 1, "both conversions must resolve to one invoice"

    check = pg_session_factory()
    count = check.scalar(
        select(func.count())
        .select_from(CommercialDocument)
        .where(CommercialDocument.kind == "invoice")
    )
    assert count == 1
    check.close()


def test_concurrent_email_claims_divide_work_without_overlap(pg_session_factory) -> None:
    from app.config import get_settings
    from app.services import commercial, document_access, document_email
    from app.services.messaging import get_settings_row

    session, owner, _lead, job, storage = _commercial_setup(pg_session_factory)
    quote = _issued_quote(session, owner, job, storage)
    version = commercial.active_version(session, quote)
    settings = get_settings()
    settings_row = get_settings_row(session)
    for index in range(4):
        capability, _raw = document_access.issue_capability(
            session, settings, settings_row, version.id, purpose="view"
        )
        document_email.create_delivery(
            session,
            owner,
            settings,
            settings_row,
            version=version,
            recipient=f"race-{index}@customer.test",
            secure_link="https://crm.test/document/x",
            capability_id=capability.id,
            send_key=f"race-send-{index}",
        )
    session.commit()
    session.close()

    barrier = threading.Barrier(2)
    claims: list[set[str]] = []

    def claim() -> None:
        worker = pg_session_factory()
        try:
            barrier.wait(timeout=5)
            rows = document_email.claim_pending(worker, limit=10)
            worker.commit()
            claims.append({str(row.id) for row in rows})
        finally:
            worker.close()

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(claims) == 2
    assert not (claims[0] & claims[1]), "no delivery may be claimed twice"
    assert len(claims[0] | claims[1]) == 4, "every delivery is claimed exactly once"
