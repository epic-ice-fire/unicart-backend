from pydantic import BaseModel, EmailStr, Field


# =========================
# AUTH SCHEMAS
# =========================

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    id: int
    email: EmailStr
    is_admin: bool
    is_student_verified: bool
    student_pau_email: EmailStr | None


class PauLinkRequest(BaseModel):
    pau_email: EmailStr


class PauVerifyRequest(BaseModel):
    code: str = Field(min_length=4, max_length=12)


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
    # No max_length — Temu links can be very long
    item_link: str = Field(min_length=5)
    item_amount: int = Field(gt=0)


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
    target_item_amount: int = Field(gt=0)


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
    items: list[AdminBatchItemResponse]


class AdminDashboardResponse(BaseModel):
    current_open_lobby: LobbySnapshotResponse
    triggered_batch_count: int
    triggered_batches: list[AdminBatchEntryResponse]