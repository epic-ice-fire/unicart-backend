from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)


def replace_function(text: str, name: str, new_block: str, *, async_def: bool = True) -> str:
    prefix = "async def " if async_def else "def "
    start = text.index(f"{prefix}{name}(")
    candidates = [
        p
        for p in (
            text.find("\n\n@router.", start + 1),
            text.find("\n\nasync def ", start + 1),
            text.find("\n\ndef ", start + 1),
        )
        if p != -1
    ]
    end = min(candidates) if candidates else len(text)
    return text[:start] + new_block.rstrip() + text[end:]


recipient_path = Path("app/notification_recipients.py")
if recipient_path.exists():
    raise SystemExit("app/notification_recipients.py already exists")
recipient_path.write_text(
    '''from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("unicart.notifications")


def notification_emails_for_user(user: Any) -> list[str]:
    """Return deduplicated transactional addresses for a user.

    The primary UniCart account email always receives operational notifications.
    The PAU address is added only after PAU verification has succeeded. This
    deliberately does not change where the PAU verification code itself goes.
    """
    candidates = [getattr(user, "email", None)]
    if bool(getattr(user, "is_student_verified", False)):
        candidates.append(getattr(user, "student_pau_email", None))

    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        address = str(candidate or "").strip()
        if not address:
            continue
        key = address.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(address)
    return result


async def send_to_user_addresses(
    user: Any,
    sender: Callable[..., bool],
    **kwargs: Any,
) -> dict[str, bool]:
    """Best-effort delivery to every eligible address for one verified user."""
    results: dict[str, bool] = {}
    for address in notification_emails_for_user(user):
        try:
            results[address] = bool(
                await asyncio.to_thread(sender, user_email=address, **kwargs)
            )
        except Exception:
            logger.exception("Transactional notification sender raised unexpectedly.")
            results[address] = False
    return results
''',
    encoding="utf-8",
)

lobbies_path = Path("app/routers/lobbies.py")
lobbies = lobbies_path.read_text(encoding="utf-8")
lobbies = replace_once(
    lobbies,
    "from app.models import (\n",
    "from app.notification_recipients import send_to_user_addresses\nfrom app.models import (\n",
    "lobbies notification helper import",
)

trigger_block = '''async def send_trigger_emails(db: AsyncSession, lobby: Lobby) -> None:
    """Send MOV/target notification to admin and both verified user addresses."""
    try:
        successful_payments = (
            await db.execute(
                select(PaymentTransaction)
                .where(
                    PaymentTransaction.lobby_id == lobby.id,
                    PaymentTransaction.status == PaymentStatus.success,
                )
            )
        ).scalars().all()

        total_revenue = sum(p.amount_ngn for p in successful_payments)
        unique_paying = len({p.user_id for p in successful_payments})

        if ADMIN_EMAIL:
            sent_admin = send_admin_lobby_triggered(
                admin_email=ADMIN_EMAIL,
                lobby_id=lobby.id,
                target_amount=lobby.target_item_amount,
                final_amount=lobby.current_item_amount,
                member_count=lobby.member_count,
                total_revenue_ngn=total_revenue,
                unique_paying_members=unique_paying,
            )
            if not sent_admin:
                logger.error("MOV admin email delivery failed for lobby_id=%s", lobby.id)

        paid_items_rows = (
            await db.execute(
                select(LobbyItem, User)
                .join(User, User.id == LobbyItem.user_id)
                .where(
                    LobbyItem.lobby_id == lobby.id,
                    LobbyItem.is_active.is_(True),
                    LobbyItem.item_payment_status == ItemPaymentStatus.paid,
                )
            )
        ).all()

        user_items: dict[int, tuple[User, list[LobbyItem]]] = {}
        for item, owner in paid_items_rows:
            if owner.id not in user_items:
                user_items[owner.id] = (owner, [])
            user_items[owner.id][1].append(item)

        for owner, items in user_items.values():
            paid_total = sum(i.item_amount for i in items)
            results = await send_to_user_addresses(
                owner,
                send_user_lobby_triggered,
                lobby_id=lobby.id,
                target_amount=lobby.target_item_amount,
                final_amount=lobby.current_item_amount,
                my_paid_item_count=len(items),
                my_paid_total=paid_total,
                item_links=[i.item_link for i in items],
            )
            if not results or not all(results.values()):
                logger.error(
                    "MOV user notification incomplete lobby_id=%s user_id=%s sent=%s/%s",
                    lobby.id,
                    owner.id,
                    sum(1 for ok in results.values() if ok),
                    len(results),
                )
    except Exception:
        logger.exception("Failed to send trigger emails for lobby %s", lobby.id)
'''
lobbies = replace_function(lobbies, "send_trigger_emails", trigger_block)

status_block = '''async def send_status_update_emails(db: AsyncSession, lobby: Lobby) -> None:
    """Send batch-stage updates to account email and verified PAU email."""
    try:
        paid_items_rows = (
            await db.execute(
                select(LobbyItem, User)
                .join(User, User.id == LobbyItem.user_id)
                .where(
                    LobbyItem.lobby_id == lobby.id,
                    LobbyItem.is_active.is_(True),
                    LobbyItem.item_payment_status == ItemPaymentStatus.paid,
                )
            )
        ).all()

        user_items: dict[int, tuple[User, list[LobbyItem]]] = {}
        for item, owner in paid_items_rows:
            if owner.id not in user_items:
                user_items[owner.id] = (owner, [])
            user_items[owner.id][1].append(item)

        for owner, items in user_items.values():
            paid_total = sum(i.item_amount for i in items)
            results = await send_to_user_addresses(
                owner,
                send_user_batch_status_update,
                lobby_id=lobby.id,
                new_status=lobby.status.value,
                my_paid_item_count=len(items),
                my_paid_total=paid_total,
                item_links=[i.item_link for i in items],
            )
            if not results or not all(results.values()):
                logger.error(
                    "Batch status email incomplete lobby_id=%s user_id=%s status=%s sent=%s/%s",
                    lobby.id,
                    owner.id,
                    lobby.status.value,
                    sum(1 for ok in results.values() if ok),
                    len(results),
                )
    except Exception:
        logger.exception("Failed to send status update emails for lobby %s", lobby.id)
'''
lobbies = replace_function(lobbies, "send_status_update_emails", status_block)

old_force_user = '''    # Email the affected user
    if owner_email:
        try:
            send_user_item_force_removed(
                user_email=owner_email,
                item_id=item_id,
                lobby_id=lobby_id,
                item_link=item_link,
                item_amount=item_amount,
                was_paid=was_paid,
            )
        except Exception as e:
            logger.error(f"Failed to send force-remove user email: {e}")
'''
new_force_user = '''    # Email both the account address and verified PAU address.
    if item_owner:
        results = await send_to_user_addresses(
            item_owner,
            send_user_item_force_removed,
            item_id=item_id,
            lobby_id=lobby_id,
            item_link=item_link,
            item_amount=item_amount,
            was_paid=was_paid,
        )
        if not results or not all(results.values()):
            logger.error(
                "Force-remove user notification incomplete item_id=%s sent=%s/%s",
                item_id,
                sum(1 for ok in results.values() if ok),
                len(results),
            )
'''
lobbies = replace_once(lobbies, old_force_user, new_force_user, "force-remove dual email")
lobbies_path.write_text(lobbies, encoding="utf-8")

payments_path = Path("app/routers/payments.py")
payments = payments_path.read_text(encoding="utf-8")
payments = replace_once(
    payments,
    "from app.email_service import send_user_payment_receipt\n",
    "from app.email_service import send_user_payment_receipt\nfrom app.notification_recipients import send_to_user_addresses\n",
    "payments notification helper import",
)

receipt_block = '''async def _send_verified_payment_receipt(
    db: AsyncSession,
    *,
    user_id: int,
    payment_type: str,
    amount_ngn: int,
    lobby_id: int,
    reference: str,
    item_id: int | None = None,
    item_link: str | None = None,
) -> None:
    """Best-effort receipt to account email + verified PAU email."""
    try:
        owner = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if not owner:
            logger.error("Payment receipt skipped: user_id=%s not found", user_id)
            return

        results = await send_to_user_addresses(
            owner,
            send_user_payment_receipt,
            payment_type=payment_type,
            amount_ngn=int(amount_ngn),
            lobby_id=lobby_id,
            reference=reference,
            item_id=item_id,
            item_link=item_link,
        )
        if not results or not all(results.values()):
            logger.error(
                "Payment receipt delivery incomplete user_id=%s lobby_id=%s type=%s sent=%s/%s",
                user_id,
                lobby_id,
                payment_type,
                sum(1 for ok in results.values() if ok),
                len(results),
            )
    except Exception:
        logger.exception(
            "Unexpected payment receipt failure user_id=%s lobby_id=%s type=%s",
            user_id,
            lobby_id,
            payment_type,
        )
'''
payments = replace_function(payments, "_send_verified_payment_receipt", receipt_block)
payments_path.write_text(payments, encoding="utf-8")

gmail_path = Path("app/gmail_api_transport.py")
gmail = gmail_path.read_text(encoding="utf-8")
gmail = replace_once(
    gmail,
    "import logging\nimport os\nimport time\n",
    "import logging\nimport os\nimport re\nimport time\nfrom html import unescape\n",
    "gmail plain text imports",
)
gmail = replace_once(
    gmail,
    '''logger = logging.getLogger("unicart.email")
FROM_NAME = "UniCart"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
''',
    '''logger = logging.getLogger("unicart.email")
FROM_NAME = "UniCart"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


def _plain_text_from_html(html_body: str) -> str:
    """Create a readable text alternative for mail clients and spam filters."""
    text = re.sub(r"(?i)<br\\s*/?>|</p>|</div>|</h[1-6]>", "\\n", html_body)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\\n".join(line for line in lines if line).strip()
''',
    "gmail plain text helper",
)
gmail = replace_once(
    gmail,
    '''    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{gmail_user}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))
''',
    '''    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{gmail_user}>"
    msg["To"] = to_email
    msg["Reply-To"] = gmail_user
    msg.attach(MIMEText(_plain_text_from_html(html_body), "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
''',
    "gmail multipart alternative",
)
gmail_path.write_text(gmail, encoding="utf-8")

test_path = Path("tests/test_dual_email_notifications.py")
if test_path.exists():
    raise SystemExit("tests/test_dual_email_notifications.py already exists")
test_path.write_text(
    '''import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "dual-email-test-secret-key-that-is-long-enough")
os.environ.setdefault("ENVIRONMENT", "development")

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import ItemPaymentStatus, Lobby, LobbyItem, LobbyStatus, User
from app.notification_recipients import notification_emails_for_user
from app.routers import lobbies, payments


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


def test_verified_student_gets_primary_and_pau_notification_addresses():
    user = User(
        email="student@gmail.com",
        password_hash="x",
        student_pau_email="student@pau.edu.ng",
        is_student_verified=True,
    )
    assert notification_emails_for_user(user) == [
        "student@gmail.com",
        "student@pau.edu.ng",
    ]


def test_unverified_pau_address_is_not_used_for_operational_mail():
    user = User(
        email="student@gmail.com",
        password_hash="x",
        student_pau_email="student@pau.edu.ng",
        is_student_verified=False,
    )
    assert notification_emails_for_user(user) == ["student@gmail.com"]


def test_duplicate_addresses_are_sent_once_case_insensitively():
    user = User(
        email="Student@pau.edu.ng",
        password_hash="x",
        student_pau_email="student@pau.edu.ng",
        is_student_verified=True,
    )
    assert notification_emails_for_user(user) == ["Student@pau.edu.ng"]


@pytest.mark.asyncio
async def test_completed_batch_update_goes_to_both_verified_addresses(db_session, monkeypatch):
    user = User(
        email="buyer@gmail.com",
        password_hash="x",
        student_pau_email="buyer@pau.edu.ng",
        is_student_verified=True,
    )
    lobby = Lobby(
        title="MAIN",
        target_item_amount=5000,
        current_item_amount=5000,
        status=LobbyStatus.completed,
    )
    db_session.add_all([user, lobby])
    await db_session.flush()
    db_session.add(
        LobbyItem(
            lobby_id=lobby.id,
            user_id=user.id,
            item_link="https://example.com/item",
            item_amount=5000,
            item_payment_amount_ngn=5000,
            item_payment_status=ItemPaymentStatus.paid,
            is_active=True,
        )
    )
    await db_session.commit()

    recipients = []

    def fake_status_sender(*, user_email, **_kwargs):
        recipients.append(user_email)
        return True

    monkeypatch.setattr(lobbies, "send_user_batch_status_update", fake_status_sender)
    await lobbies.send_status_update_emails(db_session, lobby)
    assert recipients == ["buyer@gmail.com", "buyer@pau.edu.ng"]


@pytest.mark.asyncio
async def test_verified_payment_receipt_goes_to_both_verified_addresses(db_session, monkeypatch):
    user = User(
        email="payer@gmail.com",
        password_hash="x",
        student_pau_email="payer@pau.edu.ng",
        is_student_verified=True,
    )
    db_session.add(user)
    await db_session.commit()

    recipients = []

    def fake_receipt(*, user_email, **_kwargs):
        recipients.append(user_email)
        return True

    monkeypatch.setattr(payments, "send_user_payment_receipt", fake_receipt)
    await payments._send_verified_payment_receipt(
        db_session,
        user_id=user.id,
        payment_type="entry_fee",
        amount_ngn=2000,
        lobby_id=1,
        reference="safe-ref",
    )
    assert recipients == ["payer@gmail.com", "payer@pau.edu.ng"]
''',
    encoding="utf-8",
)
