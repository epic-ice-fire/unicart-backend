import ipaddress
from urllib.parse import urlsplit

from pydantic import BaseModel, EmailStr, Field, field_validator


# =========================
# AUTH SCHEMAS
# =========================

_COMMON_PASSWORDS = {
    "password123",
    "password1234",
    "123456789012",
    "qwertyuiop12",
    "letmein123456",
    "admin12345678",
    "unicart123456",
}


def _validate_new_password(value: str) -> str:
    if len(value) > 128:
        raise ValueError("Password must be at most 128 characters.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("Password cannot contain control characters.")
    if value.lower() in _COMMON_PASSWORDS:
        raise ValueError("Choose a less common password.")
    return value


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _validate_new_password(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return _validate_new_password(value)


class MeResponse(BaseModel):
    id: int
    email: EmailStr
    is_admin: bool
    is_student_verified: bool
    student_pau_email: EmailStr | None


class PauLinkRequest(BaseModel):
    pau_email: EmailStr


class PauVerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class PauLinkResponse(BaseModel):
    message: str
    expires_in_seconds: int
    dev_code: str | None = None


class PauVerifyResponse(BaseModel):
    message: str
    student_pau_email: EmailStr
    is_student_verified: bool


# =========================
# PAYMENT SCHEMAS
# =========================

class EntryFeeInitializeResponse(BaseModel):
    message: str
    reference: str
    amount_ngn: int
    authorization_url: str
    access_code: str | None = None
    lobby_id: int


class PaymentVerifyResponse(BaseModel):
    message: str
    reference: str
    status: str
    amount_ngn: int
    lobby_id: int
    joined_lobby: bool


class PaymentHistoryEntryResponse(BaseModel):
    payment_id: int
    reference: str
    status: str
    amount_ngn: int
    lobby_id: int
    created_at: str
    paid_at: str | None
    verified_at: str | None


class PaymentHistoryListResponse(BaseModel):
    payment_count: int
    payments: list[PaymentHistoryEntryResponse]


class ItemPaymentInitializeResponse(BaseModel):
    message: str
    item_id: int
    lobby_id: int
    reference: str
    amount_ngn: int
    authorization_url: str
    access_code: str | None = None


class ItemPaymentVerifyResponse(BaseModel):
    message: str
    item_id: int
    lobby_id: int
    reference: str
    payment_status: str
    is_locked: bool


# =========================
# LOBBY SCHEMAS
# =========================

class LobbySnapshotResponse(BaseModel):
    lobby_id: int
    status: str
    current_item_amount: int
    target_item_amount: int
    member_count: int


class MainLobbyDetailsResponse(BaseModel):
    lobby_id: int
    status: str
    current_item_amount: int
    target_item_amount: int
    member_count: int
    has_joined: bool
    my_active_item_count: int
    my_total_item_amount: int
    entry_fee_amount: int
    has_pending_payment: bool
    pending_payment_reference: str | None
    pending_payment_authorization_url: str | None = None
    latest_payment_status: str | None
    latest_payment_reference: str | None
    has_successful_payment_for_current_lobby: bool


class CreateMainLobbyResponse(BaseModel):
    message: str
    lobby_id: int


class JoinLobbyResponse(BaseModel):
    message: str
    lobby_id: int
    entry_fee_amount: int
    member_count: int


class AddItemRequest(BaseModel):
    item_link: str = Field(min_length=8, max_length=2048)
    item_amount: int = Field(gt=0, le=10_000_000)

    @field_validator("item_link")
    @classmethod
    def validate_public_product_url(cls, value: str) -> str:
        url = value.strip()
        if any(ch in url for ch in ("\r", "\n", "\t")):
            raise ValueError("Product link contains invalid control characters.")

        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ValueError("Product link must start with http:// or https://.")
        if not parsed.hostname:
            raise ValueError("Product link must contain a valid hostname.")
        if parsed.username or parsed.password:
            raise ValueError("Product links containing embedded credentials are not allowed.")

        hostname = parsed.hostname.rstrip(".").lower()
        if len(hostname) > 253:
            raise ValueError("Product link hostname is too long.")
        if any(ord(ch) > 127 for ch in hostname):
            raise ValueError("Internationalized/Unicode product hostnames are not allowed.")
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
            raise ValueError("Local/private product links are not allowed.")

        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("Product link contains an invalid port.") from exc

        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            ip = None

        if ip and (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError("Private or local-network product links are not allowed.")

        return url


class AddItemResponse(BaseModel):
    message: str
    lobby_id: int
    current_item_amount: int
    target_item_amount: int
    member_count: int


class RemoveItemResponse(BaseModel):
    message: str
    lobby_id: int
    removed_item_id: int
    current_item_amount: int
    target_item_amount: int
    member_count: int


class LeaveLobbyResponse(BaseModel):
    message: str
    lobby_id: int
    current_item_amount: int
    member_count: int


class UpdateTargetRequest(BaseModel):
    target_item_amount: int = Field(gt=0, le=100_000_000)


class MyLobbyItemResponse(BaseModel):
    item_id: int
    item_link: str
    item_amount: int
    is_active: bool
    is_paid: bool
    is_locked: bool
    item_payment_status: str
    item_payment_amount_ngn: int
    item_payment_reference: str | None
    item_payment_authorization_url: str | None = None
    item_label: str
    created_at: str
    removed_at: str | None


class MyLobbyItemsListResponse(BaseModel):
    lobby_id: int
    item_count: int
    total_item_amount: int
    items: list[MyLobbyItemResponse]


class UserBatchHistoryEntryResponse(BaseModel):
    lobby_id: int
    status: str
    target_item_amount: int
    final_item_amount: int
    member_count: int
    my_item_count: int
    my_total_item_amount: int
    created_at: str
    items: list[MyLobbyItemResponse]


class UserBatchHistoryListResponse(BaseModel):
    batch_count: int
    batches: list[UserBatchHistoryEntryResponse]


# =========================
# ADMIN SCHEMAS
# =========================

class AdminBatchItemResponse(BaseModel):
    item_id: int
    item_link: str
    item_amount: int
    is_active: bool
    is_paid: bool
    is_locked: bool
    item_payment_status: str
    item_payment_amount_ngn: int
    item_payment_reference: str | None
    item_label: str
    user_email: EmailStr
    created_at: str
    removed_at: str | None


class AdminBatchEntryResponse(BaseModel):
    lobby_id: int
    status: str
    target_item_amount: int
    final_item_amount: int
    member_count: int
    paid_member_count: int
    paid_total_ngn: int
    created_at: str
    is_underfunded: bool = False
    underfunded_gap: int = 0
    items: list[AdminBatchItemResponse]


class AdminDashboardResponse(BaseModel):
    current_open_lobby: LobbySnapshotResponse
    triggered_batch_count: int
    triggered_batches: list[AdminBatchEntryResponse]

# =========================
# FINANCIAL INTEGRITY / ADMIN
# =========================

class PaymentReconciliationResponse(BaseModel):
    reference: str
    payment_kind: str
    unicart_status: str
    expected_amount_ngn: int
    flutterwave_status: str
    flutterwave_amount_ngn: str | None
    flutterwave_currency: str | None
    gateway_transaction_id: str | None
    gateway_claimed: bool
    matches: bool
    issues: list[str]


class FinancialAuditEventResponse(BaseModel):
    id: int
    event_type: str
    actor_user_id: int | None
    subject_user_id: int | None
    lobby_id: int | None
    item_id: int | None
    payment_reference: str | None
    gateway_transaction_id: str | None
    amount_ngn: int | None
    details_json: str
    created_at: str


class FinancialAuditListResponse(BaseModel):
    event_count: int
    events: list[FinancialAuditEventResponse]
