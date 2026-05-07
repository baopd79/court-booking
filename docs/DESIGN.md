# Phase 2 — DESIGN

> **Project:** Sports Court Booking System
> **Status:** Phase 2 — DESIGN ✅ | Phase 3 — BUILD (next)
> **Last updated:** 2026-05-05

---

## Mục lục

1. [Tổng quan kiến trúc](#1-tổng-quan-kiến-trúc)
2. [ERD — Entity Relationship Diagram](#2-erd--entity-relationship-diagram)
3. [State Machine](#3-state-machine)
4. [API Contract](#4-api-contract)
5. [Project Structure](#5-project-structure)
6. [Quyết định kỹ thuật & Trade-offs](#6-quyết-định-kỹ-thuật--trade-offs)

---

## 1. Tổng quan kiến trúc

### 1.1. Tech stack

| Layer | Technology |
|---|---|
| Backend framework | FastAPI |
| ORM | SQLModel (SQLAlchemy 2.x underneath) |
| Database | PostgreSQL 15+ |
| Migration | Alembic |
| Cache / Queue / Lock | Redis |
| Auth | JWT (access 15ph + refresh 7 ngày) + RBAC |
| Payment | VNPay sandbox |
| Email | SMTP (Gmail/Mailtrap dev) |
| Container | Docker + Docker Compose |
| Test | pytest + pytest-asyncio |
| CI | GitHub Actions |

### 1.2. Bounded contexts

Hệ thống chia thành **6 modules**:

| Module | Trách nhiệm |
|---|---|
| `auth` | User register/login, JWT, email verification, refresh token |
| `facility` | Facility, Court, Pricing rules, Slot management |
| `booking` | Booking lifecycle (core), slot reservation với concurrency control |
| `payment` | VNPay integration, payment callback, refund |
| `notification` | Email + in-app notification, retry queue |
| `report` | Revenue reporting |

---

## 2. ERD — Entity Relationship Diagram

### 2.1. Tổng quan các bảng (15 bảng)

| Nhóm | Bảng |
|---|---|
| Tenant & Auth | `tenants`, `users`, `email_verification_tokens`, `refresh_tokens`, `audit_logs` |
| Inventory | `facilities`, `courts`, `pricing_rules` |
| Booking core | `slots`, `bookings`, `booking_slots` |
| Payment | `payments`, `refunds` |
| Notification | `notifications` |
| Infrastructure | `idempotency_keys` |

### 2.2. Schema chi tiết

#### 2.2.1. `tenants`
Multi-tenant ready, MVP seed 1 row.

| Column | Type | Constraint | Note |
|---|---|---|---|
| id | UUID | PK | |
| name | string | NOT NULL | |
| created_at | timestamp | NOT NULL | |

#### 2.2.2. `users`
Gộp customer + owner, phân biệt qua `role`.

| Column | Type | Constraint | Note |
|---|---|---|---|
| id | UUID | PK | |
| tenant_id | UUID | FK → tenants | |
| email | string | UNIQUE, NOT NULL | |
| password_hash | string | NOT NULL | bcrypt |
| role | enum | NOT NULL | `customer` \| `owner` |
| status | enum | NOT NULL | `unverified` \| `verified` \| `suspended` |
| full_name | string | | |
| phone | string | | |
| created_at | timestamp | NOT NULL | |

**Index:** `email` (UNIQUE).

#### 2.2.3. `email_verification_tokens`
Token verify email, hash trước khi lưu.

| Column | Type | Constraint |
|---|---|---|
| id | UUID | PK |
| user_id | UUID | FK → users |
| token_hash | string | UNIQUE, NOT NULL |
| expires_at | timestamp | NOT NULL (24h TTL) |
| used_at | timestamp | NULLABLE |
| created_at | timestamp | NOT NULL |

#### 2.2.4. `refresh_tokens`
JWT refresh token, có thể revoke.

| Column | Type | Constraint |
|---|---|---|
| id | UUID | PK |
| user_id | UUID | FK → users |
| token_hash | string | UNIQUE, NOT NULL |
| expires_at | timestamp | NOT NULL (7 ngày TTL) |
| revoked_at | timestamp | NULLABLE |
| user_agent | string | |
| ip | string | |
| created_at | timestamp | NOT NULL |

#### 2.2.5. `audit_logs`
Log auth events (login success/fail, sensitive actions).

| Column | Type | Constraint |
|---|---|---|
| id | UUID | PK |
| user_id | UUID | FK → users, NULLABLE |
| event_type | string | NOT NULL |
| ip | string | |
| user_agent | string | |
| outcome | enum | `success` \| `failed` |
| metadata | jsonb | |
| created_at | timestamp | NOT NULL |

`user_id` nullable vì login fail có thể chưa biết user.

#### 2.2.6. `facilities`
Soft delete để giữ booking history.

| Column | Type | Constraint |
|---|---|---|
| id | UUID | PK |
| tenant_id | UUID | FK → tenants |
| name | string | NOT NULL |
| address | string | |
| deleted_at | timestamp | NULLABLE (soft delete) |

#### 2.2.7. `courts`
Soft delete như facility.

| Column | Type | Constraint |
|---|---|---|
| id | UUID | PK |
| facility_id | UUID | FK → facilities |
| name | string | NOT NULL |
| sport_type | enum | `pickleball` \| `badminton` \| `tennis` |
| default_price | decimal | NOT NULL, CHECK > 0 |
| deleted_at | timestamp | NULLABLE |

#### 2.2.8. `pricing_rules`
Disjoint time blocks, cover toàn business hour.

| Column | Type | Constraint |
|---|---|---|
| id | UUID | PK |
| court_id | UUID | FK → courts |
| day_of_week | smallint | 0-6 (0 = Sunday) |
| start_time | time | NOT NULL |
| end_time | time | NOT NULL |
| price | decimal | NOT NULL, CHECK > 0 |

**Validation ở app layer**: no-overlap, no-gap.

#### 2.2.9. `slots` ⭐ HOT TABLE
Pre-generated, là nơi diễn ra concurrency. Primary key `bigint` (không UUID) cho perf + ordered locking.

| Column | Type | Constraint | Note |
|---|---|---|---|
| id | bigint | PK | Auto-increment |
| court_id | UUID | FK → courts | |
| slot_start | timestamp | NOT NULL | UTC |
| slot_end | timestamp | NOT NULL | |
| status | enum | NOT NULL | `available` \| `held` \| `booked` \| `closed` |
| held_until | timestamp | NULLABLE | TTL hold |
| held_by_booking_id | UUID | FK → bookings, NULLABLE, ON DELETE SET NULL | Debug + cleanup |
| version | integer | NOT NULL DEFAULT 0 | Optimistic lock fallback |

**Indexes:**
```sql
-- Unique: chống generate trùng
CREATE UNIQUE INDEX idx_slots_court_start ON slots(court_id, slot_start);

-- Search slot theo court + thời gian
CREATE INDEX idx_slots_court_time ON slots(court_id, slot_start);

-- Cleanup expired hold (partial index)
CREATE INDEX idx_slots_held_until ON slots(held_until) WHERE status = 'held';
```

#### 2.2.10. `bookings`
Parent của booking_slots. Có CHECK constraint phân biệt online vs walk-in.

| Column | Type | Constraint |
|---|---|---|
| id | UUID | PK |
| tenant_id | UUID | FK → tenants |
| customer_id | UUID | FK → users, NULLABLE (walk-in) |
| court_id | UUID | FK → courts |
| status | enum | `pending_payment` \| `payment_processing` \| `confirmed` \| `in_use` \| `completed` \| `expired` \| `payment_failed` \| `cancelled` |
| total_amount | decimal | NOT NULL |
| booking_type | enum | `online` \| `walkin` |
| walkin_name | string | NULLABLE |
| walkin_phone | string | NULLABLE |
| hold_expires_at | timestamp | NULLABLE |
| cancelled_by | enum | NULLABLE — `customer` \| `owner` \| `system` |
| cancellation_reason | text | NULLABLE |
| cancelled_at | timestamp | NULLABLE |
| created_at | timestamp | NOT NULL |

**CHECK constraint** (online vs walk-in — exhaustive cả 2 chiều):
```sql
ALTER TABLE bookings ADD CONSTRAINT chk_booking_owner CHECK (
  (booking_type = 'online' 
    AND customer_id IS NOT NULL 
    AND walkin_name IS NULL 
    AND walkin_phone IS NULL) 
  OR
  (booking_type = 'walkin' 
    AND customer_id IS NULL 
    AND walkin_name IS NOT NULL 
    AND walkin_phone IS NOT NULL)
);

-- Defensive: đảm bảo amount dương
ALTER TABLE bookings ADD CONSTRAINT chk_booking_amount CHECK (total_amount > 0);
```

#### 2.2.11. `booking_slots`
Junction table booking ↔ slots. Lưu giá lúc đặt cho audit.

| Column | Type | Constraint |
|---|---|---|
| id | UUID | PK |
| booking_id | UUID | FK → bookings |
| slot_id | bigint | FK → slots |
| price_at_booking | decimal | NOT NULL, CHECK > 0 |

#### 2.2.12. `payments`

| Column | Type | Constraint |
|---|---|---|
| id | UUID | PK |
| booking_id | UUID | FK → bookings |
| method | enum | `vnpay` \| `offline_cash` |
| amount | decimal | NOT NULL |
| status | enum | `pending` \| `success` \| `failed` |
| vnpay_txn_ref | string | UNIQUE NULLABLE — idempotency key |
| vnpay_response_code | string | NULLABLE |
| vnpay_payment_url | text | NULLABLE — cho idempotent initiate |
| url_expires_at | timestamp | NULLABLE — VNPay URL TTL ~15ph |
| paid_at | timestamp | NULLABLE |

**Idempotency**: `vnpay_txn_ref UNIQUE` → webhook trùng → DB conflict → đã xử lý.

**Constraints bổ sung:**
```sql
-- Defense-in-depth: 1 booking chỉ có DUY NHẤT 1 payment success
-- (cho phép nhiều pending/failed nếu retry)
CREATE UNIQUE INDEX idx_payments_booking_success 
  ON payments(booking_id) 
  WHERE status = 'success';

-- Amount phải dương
ALTER TABLE payments ADD CONSTRAINT chk_payment_amount CHECK (amount > 0);
```

#### 2.2.13. `refunds`
Tách bảng để hỗ trợ partial refund tương lai.

| Column | Type | Constraint |
|---|---|---|
| id | UUID | PK |
| payment_id | UUID | FK → payments |
| amount | decimal | NOT NULL, CHECK > 0 |
| status | enum | `pending` \| `success` \| `failed` |
| reason | string | |
| refunded_at | timestamp | NULLABLE |

```sql
ALTER TABLE refunds ADD CONSTRAINT chk_refund_amount CHECK (amount > 0);
```

#### 2.2.14. `notifications`

| Column | Type | Constraint |
|---|---|---|
| id | UUID | PK |
| user_id | UUID | FK → users |
| booking_id | UUID | FK → bookings, NULLABLE |
| channel | enum | `email` \| `in_app` |
| event_type | string | NOT NULL — `booking_confirmed`, `booking_cancelled`, etc. |
| status | enum | `pending` \| `sent` \| `failed` |
| payload | jsonb | Snapshot data tại thời điểm trigger |
| retry_count | integer | DEFAULT 0 |
| sent_at | timestamp | NULLABLE |

#### 2.2.15. `idempotency_keys`
Chống duplicate request từ client (double-click, network retry). Áp dụng cho mutation endpoint critical.

| Column | Type | Constraint | Note |
|---|---|---|---|
| key | varchar(64) | NOT NULL | UUID do client tự generate |
| user_id | UUID | NOT NULL, FK → users | Scope per user |
| endpoint | varchar(100) | NOT NULL | VD: `POST /bookings` |
| request_hash | varchar(64) | | SHA256 của body — detect tampering |
| response_status | integer | | HTTP status đã trả |
| response_body | jsonb | | Response cũ để replay |
| created_at | timestamp | NOT NULL | |
| expires_at | timestamp | NOT NULL | TTL 24h |

**Primary key composite:** `(key, user_id)` — cùng key của 2 user khác nhau là độc lập.

**Indexes:**
```sql
CREATE INDEX idx_idempotency_expires ON idempotency_keys(expires_at);
```

**Áp dụng cho endpoints:**
- `POST /bookings` (chống tạo 2 booking cùng ý định)
- `POST /payments/initiate` (chống tạo 2 VNPay URL — cộng với row lock)
- `POST /bookings/{id}/cancel` (chống cancel 2 lần → refund 2 lần)
- `POST /bookings/walk-in` (chống tạo trùng walk-in)

**Không áp dụng:** VNPay webhook đã có `vnpay_txn_ref UNIQUE` riêng.

**Cleanup:** Cron job mỗi giờ xóa row có `expires_at < now()`.

### 2.3. Relationships

```
tenants ──┬─< users ──< bookings ──< booking_slots >── slots >── courts
          │                              │
          └─< facilities ──< courts ─────┤
                              │          │
                              └──< pricing_rules
                              
bookings ─< payments ─< refunds
users ─< notifications >─ bookings
users ─< email_verification_tokens
users ─< refresh_tokens
users ─< audit_logs
```

### 2.4. Multi-tenant strategy

**Có `tenant_id`:** `users`, `facilities`, `bookings` (entity gốc).
**Không có:** `courts`, `slots`, `pricing_rules`, `booking_slots` — trace qua FK chain.

Trade-off: query tenant-scoped phải JOIN nhưng tránh redundant data.

---

## 3. State Machine

### 3.1. Booking lifecycle (8 states)

| State | Ý nghĩa | Terminal? |
|---|---|---|
| `pending_payment` | Đã hold slot, chờ payment | ✗ |
| `payment_processing` | Đã initiate VNPay, chờ callback | ✗ |
| `confirmed` | Payment thành công, slot booked | ✗ |
| `in_use` | Customer đã check-in | ✗ |
| `completed` | Slot kết thúc | ✓ |
| `expired` | Hết 10 phút chưa pay | ✓ |
| `payment_failed` | VNPay callback fail | ✓ |
| `cancelled` | Cancel (by customer/owner/system) | ✓ |

### 3.2. Transitions

```
                    ┌────────[walk-in]──────────────────────────┐
                    │                                            ▼
START ──[reserve]──> pending_payment ──[initiate]──> payment_processing ──[OK]──> confirmed
                          │                              │                          │
                          │ [TTL 10ph]                   │ [FAIL]                   │
                          ▼                              ▼                          │
                       expired                    payment_failed                    │
                                                                                    │
                          ┌─────────────────────────────────────────────────────────┤
                          │                                                          │
                          ▼                                                          ▼
                       cancelled <────[customer/owner cancel]──── confirmed ──[check-in]──> in_use
                                                                      │                       │
                                                                      │ [auto: no-show]       │ [auto: end]
                                                                      ▼                       ▼
                                                                  completed              completed
```

**Bảng transitions chi tiết:**

| # | From | To | Trigger | Side effect |
|---|---|---|---|---|
| T1 | START | `pending_payment` | Customer reserve | Lock slots `FOR UPDATE` → `held` + `held_until` + create booking |
| T2 | START | `confirmed` | Owner walk-in | Lock slots → `booked` + create booking + payment(offline) |
| T3 | `pending_payment` | `payment_processing` | Customer initiate VNPay | Tạo VNPay URL |
| T4 | `payment_processing` | `confirmed` | VNPay webhook success | Slot → `booked`, send notification |
| T5 | `payment_processing` | `payment_failed` | VNPay webhook fail | Slot → `available`, notify customer |
| T6 | `pending_payment` | `expired` | Cron job (TTL 10ph) | Slot → `available` |
| T7 | `confirmed` | `in_use` | Owner check-in | (state only) |
| T8 | `in_use` | `completed` | Background job (last_slot_end < now) | (state only) |
| T9 | `pending_payment`/`confirmed` | `cancelled` | Customer/Owner cancel | Slot → `available`, tạo refund record |
| T10 | `confirmed` | `completed` | Background job (no-show case) | (state only, không tạo refund) |

### 3.3. Invariants (bất biến)

1. **Forward-only**: Không có transition ngược (cancelled → confirmed là invalid)
2. **Atomic**: 1 transition = 1 DB transaction
3. **Slot status đồng bộ với booking status**: Cùng transaction, không có sync job
4. **Idempotent**: T4, T5 (webhook) phải idempotent
5. **Terminal là chết**: 4 state (`completed`, `expired`, `payment_failed`, `cancelled`) không có outgoing transition

### 3.4. Edge cases

#### Edge case 1: Webhook đến sau khi cron expired
- Booking `pending_payment` quá 10 phút → cron set `expired`, slot → `available`
- VNPay callback **vẫn đến** với status success
- **Xử lý**: Webhook check current state, nếu `expired` → reject + tạo refund record + alert admin

#### Edge case 2: Cancel khi đang `payment_processing`
- AC 6.6 nói rõ: trả 409, customer phải đợi webhook về
- **Lý do**: không biết payment kết quả thế nào, sợ refund nhầm

#### Edge case 3: Late check-in / no-show
- Booking `confirmed`, slot đã bắt đầu nhưng customer chưa đến
- Background job: nếu `last_slot_end < now` mà vẫn `confirmed` → set `completed` (T10)

### 3.5. Slot lifecycle (4 states)

| State | Trigger vào | Trigger ra |
|---|---|---|
| `available` | Default / booking cancel/expired | Customer reserve / Owner close / Walk-in |
| `held` | Customer reserve | TTL expire / Confirm pay / Cancel |
| `booked` | Pay success / Walk-in | Cancel / Force cancel |
| `closed` | Owner close | Owner reopen |

Slot status thay đổi **luôn đi kèm với booking transition** (trừ `closed` độc lập).

### 3.6. Payment lifecycle (3 states)

| State | Ý nghĩa |
|---|---|
| `pending` | Đã tạo VNPay URL, chờ kết quả |
| `success` | Webhook xác nhận thành công |
| `failed` | Webhook báo fail |

Walk-in: tạo trực tiếp với status `success`, method `offline_cash`.

### 3.7. Implementation pattern

```python
# Định nghĩa transition rule trong code (không hardcode if/else)
ALLOWED_TRANSITIONS = {
    'pending_payment': {'payment_processing', 'expired', 'cancelled'},
    'payment_processing': {'confirmed', 'payment_failed'},
    'confirmed': {'in_use', 'completed', 'cancelled'},
    'in_use': {'completed'},
    # Terminal states → empty set
    'completed': set(),
    'expired': set(),
    'payment_failed': set(),
    'cancelled': set(),
}

def transition(booking: Booking, new_status: str, actor: str):
    if new_status not in ALLOWED_TRANSITIONS[booking.status]:
        raise InvalidTransitionError(
            f"Cannot transition {booking.status} → {new_status}"
        )
    # ... atomic update + side effects
```

---

## 4. API Contract

### 4.1. Common patterns

#### Error response format

```python
class ErrorResponse(BaseModel):
    error: ErrorDetail

class ErrorDetail(BaseModel):
    code: str           # VD: 'SLOT_NOT_AVAILABLE'
    message: str        # Human-readable
    details: dict | None = None
```

Ví dụ 409:
```json
{
  "error": {
    "code": "SLOT_NOT_AVAILABLE",
    "message": "Một số slot đã được đặt bởi khách khác",
    "details": {"unavailable_slot_ids": [123, 124]}
  }
}
```

#### Pagination

```python
class PaginatedResponse[T](BaseModel):
    items: list[T]
    total: int
    page: int
    limit: int
    has_next: bool
```

#### Authentication

```
Authorization: Bearer <access_token>
```

JWT payload chứa: `user_id`, `tenant_id`, `role`, `exp`.

#### Rate limiting (Phase HARDEN)

| Endpoint | Limit |
|---|---|
| POST /auth/login | 5 req/phút/IP |
| POST /bookings | 10 req/phút/customer |
| POST /payments/initiate | 5 req/phút/customer |

### 4.2. Endpoints mapping (~30 endpoints)

#### Module: auth

| Method | Path | Story | Auth |
|---|---|---|---|
| POST | `/auth/register` | S1 | — |
| POST | `/auth/verify-email` | S1 | — |
| POST | `/auth/resend-verification` | S1 | — |
| POST | `/auth/login` | S1, S8 | — |
| POST | `/auth/refresh` | — | refresh JWT |
| POST | `/auth/logout` | — | JWT |
| GET | `/auth/me` | — | JWT |

#### Module: facility (owner only)

| Method | Path | Story | Auth |
|---|---|---|---|
| GET / POST / PATCH / DELETE | `/facilities` | S9 | owner |
| GET / POST / PATCH / DELETE | `/courts` | S9 | owner |
| GET | `/courts/{id}/pricing` | S10 | owner |
| PUT | `/courts/{id}/pricing` | S10 | owner — replace toàn bộ |
| POST | `/courts/{id}/slots/close` | S11 | owner — bulk |
| POST | `/courts/{id}/slots/reopen` | S11 | owner — bulk |

#### Module: booking (core)

| Method | Path | Story | Auth |
|---|---|---|---|
| GET | `/courts/availability` | S2 | public |
| POST | `/bookings` | S3 | customer ⭐ |
| POST | `/bookings/walk-in` | S13 | owner |
| GET | `/bookings/me` | S5 | customer |
| GET | `/bookings/{id}` | S5, S12 | customer/owner |
| GET | `/bookings` | S12 | owner |
| POST | `/bookings/{id}/cancel` | S6 | customer |
| POST | `/bookings/{id}/force-cancel` | S15 | owner |
| POST | `/bookings/{id}/check-in` | S14 | owner |

#### Module: payment

| Method | Path | Story | Auth |
|---|---|---|---|
| POST | `/payments/initiate` | S4 | customer |
| GET | `/payments/vnpay-return` | S4 | public (browser redirect) |
| POST | `/payments/vnpay-ipn` | S4 | public + signature ⭐ |

#### Module: notification

| Method | Path | Story | Auth |
|---|---|---|---|
| GET | `/notifications` | S7 | JWT |
| POST | `/notifications/{id}/read` | S7 | JWT |

#### Module: report

| Method | Path | Story | Auth |
|---|---|---|---|
| GET | `/reports/revenue` | S16 | owner |

### 4.3. Critical endpoints — Full schema

#### 4.3.1. POST `/bookings` — Reserve slot ⭐ CORE

**Headers:**
```
Idempotency-Key: <uuid_v4>   # BẮT BUỘC, client tự generate
Authorization: Bearer <token>
```

**Request:**
```python
class CreateBookingRequest(BaseModel):
    court_id: UUID
    slot_ids: list[int] = Field(min_length=1, max_length=4)
```

**Response 201:**
```python
class BookingResponse(BaseModel):
    id: UUID
    status: BookingStatus  # 'pending_payment'
    court_id: UUID
    total_amount: Decimal
    hold_expires_at: datetime
    slots: list[SlotInfo]
    created_at: datetime

class SlotInfo(BaseModel):
    id: int
    slot_start: datetime
    slot_end: datetime
    price: Decimal
```

**Errors:**
| Status | Code |
|---|---|
| 400 | `INVALID_SLOTS` (không liên tiếp / không cùng court / > 4) |
| 400 | `MISSING_IDEMPOTENCY_KEY` |
| 403 | `EMAIL_NOT_VERIFIED` |
| 409 | `SLOT_NOT_AVAILABLE` |
| 409 | `IDEMPOTENCY_KEY_REUSED_DIFFERENT_BODY` (cùng key nhưng body khác) |
| 429 | `TOO_MANY_PENDING` (>= 5 booking pending) |

**Concurrency + idempotency pseudocode:**
```python
async def create_booking(
    request: CreateBookingRequest,
    customer_id: UUID,
    idempotency_key: str,
):
    request_hash = sha256(request.model_dump_json())
    
    # ===== STEP 1: Idempotency check =====
    existing = await idempotency_repo.find(idempotency_key, customer_id)
    if existing:
        # Detect tampering: cùng key nhưng body khác → reject
        if existing.request_hash != request_hash:
            raise ConflictError('IDEMPOTENCY_KEY_REUSED_DIFFERENT_BODY')
        # Replay response cũ — KHÔNG tạo booking mới
        return existing.response_body
    
    # Reserve key (INSERT, fail nếu duplicate do race)
    try:
        await idempotency_repo.reserve(
            key=idempotency_key,
            user_id=customer_id,
            endpoint='POST /bookings',
            request_hash=request_hash,
        )
    except UniqueViolation:
        # Race với chính mình — đợi và return response cũ
        return await idempotency_repo.wait_and_get(idempotency_key, customer_id)
    
    # ===== STEP 2: Reserve slots với pessimistic lock =====
    async with db.transaction():
        # Lock slots (ORDER BY id chống deadlock)
        slots = await db.execute(
            select(Slot)
            .where(Slot.id.in_(request.slot_ids), Slot.court_id == request.court_id)
            .order_by(Slot.id)
            .with_for_update()
        )

        # Validate sau khi lock
        if len(slots) != len(request.slot_ids):
            raise NotFoundError('Some slots not found')
        if any(s.status != 'available' for s in slots):
            raise ConflictError('SLOT_NOT_AVAILABLE')
        if not is_consecutive(slots):
            raise ValidationError('INVALID_SLOTS')

        # Check pending count
        if await booking_repo.count_pending(customer_id) >= 5:
            raise RateLimitError('TOO_MANY_PENDING')

        # Create booking + update slots (atomic)
        booking = await booking_repo.create(...)
        await slot_repo.mark_held(slots, booking.id, expires_in_minutes=10)
    
    # ===== STEP 3: Lưu response vào idempotency key =====
    response = BookingResponse.from_orm(booking)
    await idempotency_repo.save_response(
        idempotency_key, customer_id, status=201, body=response.model_dump()
    )
    return response
```

**Lưu ý:** Idempotency-Key TTL 24h (cấu hình ở `idempotency_keys.expires_at`). Sau 24h client có thể reuse key cũ.

#### 4.3.2. POST `/payments/initiate`

**Request:**
```python
class InitiatePaymentRequest(BaseModel):
    booking_id: UUID
    return_url: str
```

**Response 200:**
```python
class InitiatePaymentResponse(BaseModel):
    payment_url: str
    expires_at: datetime
    is_existing: bool  # True nếu trả URL đã tạo trước (idempotent)
```

**Errors:**
| Status | Code |
|---|---|
| 404 | `BOOKING_NOT_FOUND` |
| 409 | `BOOKING_NOT_PAYABLE` |
| 410 | `BOOKING_EXPIRED` |

**Idempotent logic** (AC 4.8 + chống race condition):
```python
async def initiate_payment(booking_id, customer_id):
    async with db.transaction():
        # ⭐ LOCK booking row trước khi check (chống 2 request song song)
        booking = await booking_repo.get_owned_by_locked(booking_id, customer_id)
        # SQL: SELECT ... FROM bookings WHERE id=? AND customer_id=? FOR UPDATE

        # Nếu đã có URL còn hạn → trả lại (idempotent — AC 4.8)
        if booking.status == 'payment_processing':
            existing = await payment_repo.get_by_booking(booking_id)
            if existing and existing.url_expires_at > now():
                return InitiatePaymentResponse(
                    payment_url=existing.vnpay_payment_url,
                    expires_at=existing.url_expires_at,
                    is_existing=True
                )

        if booking.status != 'pending_payment':
            raise ConflictError('BOOKING_NOT_PAYABLE')
        if booking.hold_expires_at < now():
            raise GoneError('BOOKING_EXPIRED')

        url, expires = vnpay_client.create_payment_url(booking)
        await payment_repo.create(booking_id, url, expires)
        await booking_repo.update_status(booking_id, 'payment_processing')

    return InitiatePaymentResponse(payment_url=url, expires_at=expires, is_existing=False)
```

**2 lớp bảo vệ:**
1. **Row lock (`FOR UPDATE`)**: chống 2 request song song cùng pass check `pending_payment`
2. **Idempotency-Key header** (cộng thêm): chống client double-click ở tầng cao hơn

#### 4.3.3. POST `/payments/vnpay-ipn` — Webhook ⭐

**Request:** VNPay gửi query params với signature.

**Response:** VNPay format `{"RspCode": "00", "Message": "..."}`.

**Logic:**
```python
async def handle_vnpay_ipn(params: dict):
    # 1. Verify signature
    if not vnpay_client.verify_signature(params):
        return {"RspCode": "97", "Message": "Invalid signature"}

    txn_ref = params['vnp_TxnRef']
    response_code = params['vnp_ResponseCode']
    amount = int(params['vnp_Amount']) / 100

    async with db.transaction():
        # 2. Lock payment row
        payment = await payment_repo.get_by_txn_ref_locked(txn_ref)

        # 3. Idempotency: nếu đã processed → return success
        if payment.status in ('success', 'failed'):
            return {"RspCode": "00", "Message": "Already processed"}

        booking = await booking_repo.get_locked(payment.booking_id)

        # 4. Edge case: webhook đến sau expire
        if booking.status == 'expired':
            await refund_repo.create_for_orphan_payment(payment, amount)
            await alert_admin(f"Webhook arrived after expire: {txn_ref}")
            return {"RspCode": "00", "Message": "Refund initiated"}

        # 5. Verify amount
        if amount != booking.total_amount:
            await alert_admin(f"Amount mismatch: {txn_ref}")
            return {"RspCode": "04", "Message": "Amount mismatch"}

        # 6. Apply state change
        if response_code == '00':
            await payment_repo.mark_success(payment.id)
            await booking_service.transition_to_confirmed(booking)
            await notification_service.send_booking_confirmed(booking)
        else:
            await payment_repo.mark_failed(payment.id)
            await booking_service.transition_to_payment_failed(booking)
            await notification_service.send_payment_failed(booking)

    return {"RspCode": "00", "Message": "Confirm Success"}
```

#### 4.3.4. GET `/courts/availability`

**Query params:** `?facility_id=<uuid>&date=2026-05-04&court_id=<uuid>` (court_id optional).

**Response 200:**
```python
class AvailabilityResponse(BaseModel):
    date: date
    facility_id: UUID
    courts: list[CourtAvailability]

class CourtAvailability(BaseModel):
    id: UUID
    name: str
    sport_type: str
    slots: list[SlotAvailability]

class SlotAvailability(BaseModel):
    id: int
    slot_start: datetime
    slot_end: datetime
    status: Literal['available', 'unavailable', 'closed']  # Không leak 'held'
    price: Decimal
```

**Lưu ý:** AC 2.2 — không leak `held`. Backend `held` và `booked` đều map ra `unavailable`.

#### 4.3.5. POST `/bookings/{id}/cancel`

**Request:**
```python
class CancelBookingRequest(BaseModel):
    confirm_no_refund: bool = False  # required nếu < 2h
```

**Response 200:**
```python
class CancelBookingResponse(BaseModel):
    booking_id: UUID
    status: BookingStatus  # 'cancelled'
    refund_amount: Decimal
    refund_percentage: int  # 100 / 50 / 0
    refund_status: Literal['pending', 'success', 'failed']
```

**Errors:**
| Status | Code |
|---|---|
| 403 | `NOT_BOOKING_OWNER` |
| 409 | `BOOKING_NOT_CANCELLABLE` (terminal / payment_processing / đã start) |
| 400 | `CONFIRMATION_REQUIRED` (< 2h chưa confirm_no_refund) |

---

## 5. Project Structure

```
app/
├── core/                    # Shared infrastructure
│   ├── config.py           # Settings (env vars)
│   ├── database.py         # DB session
│   ├── redis.py            # Redis client
│   ├── security.py         # JWT, password hashing
│   ├── exceptions.py       # Custom exceptions
│   └── deps.py             # FastAPI dependencies (get_current_user, ...)
│
├── modules/                 # Vertical slices — mỗi module = 1 bounded context
│   ├── auth/
│   │   ├── models.py       # SQLModel: User, RefreshToken, EmailToken
│   │   ├── schemas.py      # Pydantic: RegisterRequest, LoginResponse, ...
│   │   ├── repository.py   # DB queries
│   │   ├── service.py      # Business logic
│   │   └── routes.py       # FastAPI router
│   │
│   ├── facility/           # Facility + Court + Pricing
│   ├── booking/            # Booking + Slot (core)
│   ├── payment/            # Payment + Refund + VNPay
│   └── notification/       # Email + In-app + Retry
│
├── jobs/                    # Background jobs
│   ├── expire_holds.py     # Cron 1ph: pending_payment > 10ph → expired
│   ├── reconcile_payments.py  # Cron 10ph: query VNPay cho payment pending > 30ph
│   ├── auto_complete.py    # Cron 5ph: confirmed/in_use last_slot_end < now → completed
│   └── notification_retry.py  # Worker: retry failed notifications
│
├── main.py                  # FastAPI app entry
└── alembic/                 # Migrations
```

**Pattern: Vertical slice + layered**
- **Vertical slice** (auth/, booking/) → mỗi feature self-contained
- **Layered trong module** (routes → service → repository) → tách concern

---

## 6. Quyết định kỹ thuật & Trade-offs

### 6.1. Pessimistic vs Optimistic lock → **Pessimistic**

| Tiêu chí | Lý do chọn pessimistic |
|---|---|
| Conflict rate | Cao trên slot hot (19h cuối tuần) |
| Multi-row atomic | Booking 1-4 slot, all-or-nothing |
| Lock duration | Ngắn (vài chục ms), không phải giữ 10 phút |
| Đơn giản | Ít edge case, dễ giải thích |

`held_until` là **business state** (DB column), không phải DB lock — sau commit lock thả ngay.

### 6.2. Slot pre-generated vs computed-on-fly → **Pre-generated**

Pre-generate 30 ngày tới, cron mỗi đêm sinh ngày thứ 31.
- **Storage**: ~5000 row/10 court — nhẹ
- **Query nhanh** + concurrency rõ ràng (lock thật trên row thật)

### 6.3. UUID vs bigint cho PK

| Entity | Type | Lý do |
|---|---|---|
| `users`, `bookings`, `payments` | UUID | Public-facing, không lộ business info |
| `slots`, `booking_slots` | bigint | Internal, perf tốt + ordered locking |

### 6.4. Soft delete — `facilities` + `courts` only

Booking entity không soft delete (immutable history).
Repository layer có method `find_active()` mặc định filter `deleted_at IS NULL`.

### 6.5. Multi-tenant ready

`tenant_id` ở entity gốc (`users`, `facilities`, `bookings`).
JWT chứa `tenant_id` để middleware filter tự động.
MVP single-tenant nhưng schema sẵn sàng.

### 6.6. State machine — gộp `cancelled` + `cancelled_by_owner`

1 state `cancelled` + cột phụ `cancelled_by` (`customer | owner | system`).
- **Ưu**: Clean state machine, query "tất cả cancelled" dễ
- **Nhược**: Mất tính rõ ràng "nhìn status biết ai cancel" — bù bằng `cancelled_by`

### 6.7. Webhook idempotency strategy

`payments.vnpay_txn_ref UNIQUE` → INSERT conflict = đã xử lý.
Webhook handler luôn check current state trước khi apply transition.

### 6.8. Idempotency cho client mutation — `idempotency_keys` table

Pattern industry-standard (Stripe, AWS) chống duplicate request từ client (double-click, retry network).

**Áp dụng**: `POST /bookings`, `POST /payments/initiate`, `POST /bookings/{id}/cancel`, `POST /bookings/walk-in`.

**Cách hoạt động**:
1. Client tự generate UUID v4, gửi qua header `Idempotency-Key`
2. Server lưu `(key, user_id, request_hash, response)` vào DB
3. Request tiếp theo cùng key → replay response cũ, không xử lý lại
4. TTL 24h, cron clean expired

**Defense-in-depth**: Cộng thêm với pessimistic lock ở DB. Lock chống concurrent ở tầng DB, idempotency chống duplicate ở tầng app.

### 6.9. Skip ở MVP (Phase HARDEN sẽ revisit)

- GIST exclusion constraint cho pricing_rules (validate ở app layer)
- Partial indexes cho cleanup jobs (thêm khi scale)
- `TIMESTAMPTZ` thay `TIMESTAMP` (sửa khi multi-region)
- Index cho `audit_logs(user_id, created_at)` (thêm khi cần query history)

---

## Phase 3 — BUILD Roadmap

Vertical slice theo thứ tự dependency:

| # | Slice | Output |
|---|---|---|
| 1 | Setup | Docker Compose + Postgres + Redis + Alembic + base structure |
| 2 | Auth | User register/login/JWT/refresh/email verify |
| 3 | Facility | CRUD facility + court + pricing |
| 4 | Slot | Pre-generate cron + search availability |
| 5 | Booking | ⭐ Core: reserve + concurrency test |
| 6 | Payment | VNPay integration + webhook + reconciliation |
| 7 | Lifecycle | Cancel + refund + check-in + auto-complete |
| 8 | Notification | Email + in-app + retry |
| 9 | Report | Revenue |

Mỗi slice: **Model → Migration → Repository → Service → API → Test** end-to-end.

---

*Phase 2 — DESIGN complete. Ready for Phase 3 — BUILD.*
