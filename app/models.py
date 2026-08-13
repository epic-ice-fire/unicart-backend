import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow_naive() -> datetime:
    """UTC timestamp stored as a naive value for existing DB compatibility."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LobbyStatus(str, enum.Enum):
    open = "open"
    triggered = "triggered"
    processing = "processing"
    in_transit = "in_transit"
    completed = "completed"


class PassStatus(str, enum.Enum):
    active = "active"
    left = "left"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    success = "success"
    failed = "failed"
    abandoned = "abandoned"


class ItemPaymentStatus(str, enum.Enum):
    unpaid = "unpaid"
    pending = "pending"
    paid = "paid"
    failed = "failed"
    abandoned = "abandoned"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_sub: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    student_pau_email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    is_student_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    pau_verification_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pau_verification_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class AuthSession(Base):
    """Server-side record for one issued access token.

    Only a SHA-256 digest of the JWT jti is stored. Revoking this row makes the
    corresponding bearer token unusable immediately, even before JWT expiry.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    jti_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user = relationship("User")


class PauEmailVerification(Base):
    __tablename__ = "pau_email_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    pau_email: Mapped[str] = mapped_column(String(255), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user = relationship("User")


class Lobby(Base):
    __tablename__ = "lobbies"
    __table_args__ = (
        CheckConstraint("target_item_amount > 0", name="ck_lobbies_target_positive"),
        CheckConstraint("current_item_amount >= 0", name="ck_lobbies_current_nonnegative"),
        CheckConstraint("member_count >= 0", name="ck_lobbies_members_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(140))
    target_item_amount: Mapped[int] = mapped_column(Integer, default=30000)
    current_item_amount: Mapped[int] = mapped_column(Integer, default=0)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[LobbyStatus] = mapped_column(Enum(LobbyStatus), default=LobbyStatus.open)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

    passes = relationship("LobbyPass", back_populates="lobby", cascade="all, delete-orphan")
    items = relationship("LobbyItem", back_populates="lobby", cascade="all, delete-orphan")


class LobbyPass(Base):
    __tablename__ = "lobby_passes"
    __table_args__ = (
        CheckConstraint("entry_fee_amount > 0", name="ck_lobby_pass_fee_positive"),
        # At most one ACTIVE membership per user/lobby. Historical LEFT passes
        # remain possible, so leaving and paying again later still works.
        Index(
            "ux_lobby_pass_active_user",
            "lobby_id",
            "user_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lobby_id: Mapped[int] = mapped_column(ForeignKey("lobbies.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    entry_fee_amount: Mapped[int] = mapped_column(Integer, default=2000)
    status: Mapped[PassStatus] = mapped_column(Enum(PassStatus), default=PassStatus.active)
    paid_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    left_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lobby = relationship("Lobby", back_populates="passes")


class LobbyItem(Base):
    __tablename__ = "lobby_items"
    __table_args__ = (
        CheckConstraint("item_amount > 0", name="ck_lobby_items_amount_positive"),
        CheckConstraint(
            "item_payment_amount_ngn >= 0",
            name="ck_lobby_items_payment_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lobby_id: Mapped[int] = mapped_column(ForeignKey("lobbies.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    item_link: Mapped[str] = mapped_column(Text)
    item_amount: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    item_payment_amount_ngn: Mapped[int] = mapped_column(Integer, default=0)
    item_payment_status: Mapped[ItemPaymentStatus] = mapped_column(
        Enum(ItemPaymentStatus), default=ItemPaymentStatus.unpaid, index=True,
    )
    item_payment_reference: Mapped[str | None] = mapped_column(
        String(120), nullable=True, unique=True, index=True,
    )
    item_payment_access_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    item_payment_authorization_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_payment_gateway_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    item_payment_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    lobby = relationship("Lobby", back_populates="items")


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"
    __table_args__ = (
        CheckConstraint("amount_ngn > 0", name="ck_payment_transactions_amount_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    lobby_id: Mapped[int] = mapped_column(ForeignKey("lobbies.id"), index=True)
    amount_ngn: Mapped[int] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus), default=PaymentStatus.pending, index=True,
    )
    # Legacy column names retained for backwards compatibility. They contain
    # Flutterwave values in the current UniCart integration.
    paystack_access_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    paystack_authorization_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    paystack_transaction_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    gateway_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow_naive, onupdate=utcnow_naive,
    )


class GatewayTransactionClaim(Base):
    """One-to-one binding between a Flutterwave transaction and a UniCart reference.

    This table is the database-level replay/idempotency guard. A gateway
    transaction ID cannot be reused for another UniCart payment, and a UniCart
    payment reference cannot be fulfilled by a second gateway transaction.
    """

    __tablename__ = "gateway_transaction_claims"
    __table_args__ = (
        CheckConstraint("amount_ngn > 0", name="ck_gateway_claim_amount_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gateway: Mapped[str] = mapped_column(String(32), default="flutterwave", index=True)
    gateway_transaction_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    reference: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    payment_kind: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    lobby_id: Mapped[int] = mapped_column(Integer, index=True)
    item_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    amount_ngn: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="NGN")
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class FinancialAuditEvent(Base):
    """Append-only business audit trail for money and privileged actions."""

    __tablename__ = "financial_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    subject_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    lobby_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    item_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    payment_reference: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    gateway_transaction_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    amount_ngn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
