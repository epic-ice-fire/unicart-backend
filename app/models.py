import enum
from datetime import datetime
from sqlalchemy import (
    String, Integer, DateTime, ForeignKey, Enum, Text, Boolean,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PauEmailVerification(Base):
    __tablename__ = "pau_email_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    pau_email: Mapped[str] = mapped_column(String(255), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user = relationship("User")


class Lobby(Base):
    __tablename__ = "lobbies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(140))
    target_item_amount: Mapped[int] = mapped_column(Integer, default=30000)
    current_item_amount: Mapped[int] = mapped_column(Integer, default=0)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[LobbyStatus] = mapped_column(Enum(LobbyStatus), default=LobbyStatus.open)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    passes = relationship("LobbyPass", back_populates="lobby", cascade="all, delete-orphan")
    items = relationship("LobbyItem", back_populates="lobby", cascade="all, delete-orphan")


class LobbyPass(Base):
    __tablename__ = "lobby_passes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lobby_id: Mapped[int] = mapped_column(ForeignKey("lobbies.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    entry_fee_amount: Mapped[int] = mapped_column(Integer, default=2000)
    status: Mapped[PassStatus] = mapped_column(Enum(PassStatus), default=PassStatus.active)
    paid_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    left_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lobby = relationship("Lobby", back_populates="passes")


class LobbyItem(Base):
    __tablename__ = "lobby_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lobby_id: Mapped[int] = mapped_column(ForeignKey("lobbies.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    item_link: Mapped[str] = mapped_column(Text)
    item_amount: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
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

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    lobby_id: Mapped[int] = mapped_column(ForeignKey("lobbies.id"), index=True)
    amount_ngn: Mapped[int] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus), default=PaymentStatus.pending, index=True,
    )
    paystack_access_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    paystack_authorization_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    paystack_transaction_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    gateway_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
    )