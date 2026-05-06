# Sports Court Booking System

Hệ thống đặt sân thể thao (pickleball / cầu lông / tennis) — slot-based booking với
xử lý concurrency, payment integration, và state machine cho booking lifecycle.

> **Project status:** Phase 1 — CLARIFY ✅ | Phase 2 — DESIGN (next)

---

## 1. Personas

### Customer (Khách hàng)

- **Đại diện:** Anh Hùng, 28 tuổi, dân văn phòng, chơi pickleball với nhóm bạn mỗi tối
- **Vai trò:** Người đặt sân, người trả tiền
- **Mục tiêu:**
  - Tìm sân trống nhanh, đặt được ngay
  - Biết chắc sân đã giữ chỗ sau khi đặt
  - Huỷ / đổi lịch khi có việc đột xuất
- **Pain point:**
  - Phải gọi điện chủ sân nhiều lần mới biết còn slot
  - Đặt rồi tới nơi sân đã có người (double-booking)
  - Chuyển khoản xong không biết booking có được xác nhận

### Field Owner (Chủ sân)

- **Đại diện:** Anh Minh, 40 tuổi, sở hữu 2 cơ sở, mỗi cơ sở 4–6 sân pickleball
- **Vai trò:** Quản lý toàn bộ hệ thống của mình (kiêm luôn vai trò staff trong MVP)
- **Mục tiêu:**
  - Set giá theo time block (peak / off-peak / cuối tuần)
  - Mở/đóng slot khi cần (bảo trì, sự kiện)
  - Xem lịch booking và doanh thu
  - Tạo booking thủ công cho khách walk-in
  - Check-in khách khi tới sân
- **Pain point:**
  - Quản lý bằng Excel + Zalo → dễ sai, dễ trùng lịch
  - Không biết doanh thu thực tế từng sân
  - Khách báo huỷ qua tin nhắn → khó theo dõi

### Out-of-scope personas (Phase 2)

- Staff (nhân viên trực) — owner kiêm trong MVP
- Platform Admin — không có (single-tenant deployment)

---

## 2. User Stories

> 16 stories total — 7 core (concurrency-critical) + 9 CRUD.

### Customer (8 stories)

1. **Auth:** As a customer, I want to register and log in to my account, so that I can manage my bookings.
2. **Search:** As a customer, I want to search for available courts by date and facility, so that I can quickly find a suitable slot.
3. **Reserve:** As a customer, I want to reserve a slot and have time to complete payment, so that I don't lose the slot to someone else while paying.
4. **Pay:** As a customer, I want to pay for my booking online, so that my reservation is confirmed.
5. **My bookings:** As a customer, I want to view my bookings list and details, so that I can track my reservations.
6. **Cancel:** As a customer, I want to cancel a booking before the deadline, so that I can adjust my plans.
7. **Notification:** As a customer, I want to receive notifications about my booking status, so that I don't miss my game.

### Field Owner (8 stories)

8. **Auth:** As a field owner, I want to log in to my admin account, so that I can manage the system.
9. **Manage facility/court:** As a field owner, I want to CRUD facilities and courts, so that I can keep my inventory accurate.
10. **Pricing config:** As a field owner, I want to configure pricing per time block, so that I can charge appropriate rates.
11. **Slot control:** As a field owner, I want to open or close time slots, so that I can handle maintenance.
12. **View bookings:** As a field owner, I want to view all bookings of my courts with filters, so that I can monitor usage.
13. **Walk-in:** As a field owner, I want to create bookings manually for walk-in customers, so that I can serve customers who don't use the app.
14. **Check-in:** As a field owner, I want to check in customers when they arrive, so that I can track court usage state.
15. **Force cancel:** As a field owner, I want to cancel a booking on behalf of a customer, so that I can handle exceptional situations.
16. **Revenue report:** As a field owner, I want to view revenue reports, so that I can understand business performance.

---

## 3. Business Rules

### Slot

- Đơn vị: **1 tiếng**
- Business hour: **6:00 – 22:00** (giờ VN, UTC+7)
- Booking: **1–4 slot liên tiếp**, BẮT BUỘC cùng 1 sân
- Cấu trúc: 1 booking entity → N booking_slots (parent-child)
- Customer được tạo **nhiều booking độc lập** (không giới hạn số booking khác nhau)

### Hold

- Hold time: **10 phút** kể từ khi click "Đặt"
- Hết hạn: slot → `available`, booking → `expired`
- Giới hạn chống abuse: customer max **5 booking pending_payment** đồng thời

### Payment

- Gateway online: **VNPay sandbox**
- Walk-in: **offline cash** (owner thu trực tiếp)
- **Source of truth: VNPay** (booking giữ pending cho tới khi nhận callback)
- **Reconciliation job:** chạy mỗi 10 phút, query VNPay status cho payments pending > 30 phút
- Webhook handler **idempotent** (callback đến nhiều lần chỉ apply state change 1 lần)

### Refund Policy

| Tình huống | Refund |
|---|---|
| Customer cancel **> 24h** trước slot | 100% |
| Customer cancel **24h – 2h** trước slot | 50% |
| Customer cancel **< 2h** trước slot | 0% |
| Customer cancel **sau khi slot bắt đầu** | REJECT |
| Owner force cancel | 100% (luôn luôn) |

### Conflict Rules

- Owner **KHÔNG override** được hold của customer online
- Walk-in **bypass hold** (đi thẳng từ create → confirmed)
- Nếu slot đang held bởi customer, owner phải đợi expire hoặc customer cancel

### Notification

- Kênh: **Email + In-app**
- Trigger events: booking confirmed, booking cancelled, refund processed, slot reminder (1–2h trước), owner cancel kèm reason
- Email fail → push vào **retry queue (Redis)**, retry max 3 lần với exponential backoff (1m, 5m, 25m)

### Auth & Account

- **Customer:** email verification BẮT BUỘC trước khi book; có thể self-register
- **Owner:** 1 owner duy nhất, được seed lúc setup (không có register API trong MVP); login flow đầy đủ

### Pricing

- Model: **disjoint time blocks** (không overlap)
- Phải cover toàn bộ business hour 6:00–22:00 (no gap)
- Default price ở court level nếu chưa config rule

### Data

- **Soft delete** cho facility & court (giữ history booking)
- Hard delete cho: notification, session token

### Other

- **Không track no-show** (đơn giản hoá MVP)
- **Timezone:** DB lưu UTC, API trả ISO 8601 + offset, business rule dùng UTC+7
- **Multi-tenant ready:** Mọi entity nghiệp vụ có `tenant_id` (MVP seed 1 row), JWT chứa `tenant_id` để dễ nâng cấp sau

---

## 4. Acceptance Criteria

> Format: Given / When / Then. Mỗi AC = ít nhất 1 test case ở Phase BUILD.

### Group A — Core stories (concurrency-critical)

#### Story 2: Search available slots

1. Given ngày + cơ sở hợp lệ, when search, then trả về danh sách sân kèm các slot 1-tiếng từ 6:00–22:00 với status: `available`, `held`, `booked`, `closed`.
2. Given slot đang `held`, when search, then hiển thị **không thể đặt** (không leak info ai hold).
3. Given ngày trong quá khứ hoặc > 30 ngày tương lai, when search, then trả về `400`.
4. Given không có slot available, when search, then trả về danh sách rỗng + message gợi ý đổi ngày.
5. Given customer search lại, when có thay đổi, then kết quả phản ánh trạng thái mới (real-time = on-demand fetch).

#### Story 3: Reserve slot + hold ⭐ CORE CONCURRENCY

1. Given customer đã verify email + chọn 1–4 slot liên tiếp cùng sân **đều available**, when click "Đặt", then booking → `pending_payment`, slot → `held`, response chứa `booking_id` + `hold_expires_at`.
2. Given có ít nhất 1 slot không available, when click "Đặt", then `409`, **không tạo booking**, **không hold slot nào**.
3. Given slot > 4 hoặc không liên tiếp hoặc không cùng sân, when click "Đặt", then `400 validation`.
4. Given **2 customer click "Đặt" cùng lúc** cho cùng slot, when xử lý, then **chỉ 1 thành công**, còn lại `409`.
5. Given booking ở `pending_payment` quá 10 phút chưa initiate payment, when job chạy, then booking → `expired`, slot → `available`.
6. Given customer >= 5 booking `pending_payment` chưa thanh toán, when click "Đặt" tiếp, then `429`.
7. Given customer chưa verify email, when click "Đặt", then `403` với message yêu cầu verify trước.

#### Story 4: Pay for booking

1. Given booking ở `pending_payment` còn trong hold time, when initiate payment, then tạo VNPay URL + booking → `payment_processing`. Slot vẫn `held`.
2. Given booking đã `expired`, when initiate payment, then `410 Gone`.
3. Given VNPay callback success, when webhook nhận, then booking → `confirmed`, slot → `booked`, payment record tạo, gửi notification.
4. Given VNPay callback failed, when webhook nhận, then booking → `payment_failed`, slot → `available`, gửi notification lỗi.
5. Given VNPay callback đến 2 lần, when xử lý, then chỉ apply state change 1 lần (idempotent), lần 2 trả 200 nhưng không đổi state.
6. Given customer đóng tab + VNPay callback chưa đến, when sau 30 phút, then reconciliation job query VNPay → cập nhật đúng state.
7. Given payment amount không khớp booking amount, when webhook xử lý, then từ chối, log warning, alert admin.
8. Given booking đã `payment_processing`, when customer cố initiate payment lần 2, then trả về **VNPay URL đã tạo trước đó** (không tạo URL mới — idempotent).

#### Story 6: Cancel booking (Customer)

1. Given booking `confirmed` + time-to-slot > 24h, when cancel, then booking → `cancelled`, slot → `available`, refund 100%, gửi notification.
2. Given booking `confirmed` + 2h <= time-to-slot < 24h, when cancel, then booking → `cancelled`, refund 50%.
3. Given booking `confirmed` + time-to-slot < 2h, when cancel, then booking → `cancelled`, không refund, customer được cảnh báo trước khi xác nhận.
4. Given booking ở status không cancelable (`completed`, `cancelled`, `expired`), when cancel, then `409`.
5. Given booking thuộc customer khác, when cancel, then `403`.
6. Given booking ở `payment_processing`, when cancel, then `409` "đang xử lý thanh toán".
7. Given VNPay refund API fail, when xử lý, then booking vẫn → `cancelled`, slot → `available`, refund record `pending`, retry job thử lại.
8. Given thời gian hiện tại >= first slot start time, when cancel, then `409` "booking đã bắt đầu, không thể huỷ".

#### Story 13: Walk-in booking (Owner)

1. Given owner login + chọn 1–4 slot liên tiếp cùng sân **đều available**, when tạo walk-in với customer info (tên + SĐT), then booking → `confirmed` ngay (**bypass hold**), slot → `booked`, payment record `method=offline_cash`, `paid_at=now`.
2. Given có slot không available, when tạo walk-in, then `409`, không tạo booking.
3. Given slot đang `held` bởi customer online, when owner cố tạo walk-in, then `409`, **không có quyền override**.
4. Given walk-in tạo thành công, when kiểm tra notification, then **không gửi email** (không có account).
5. Given SĐT walk-in trùng SĐT đã có, when tạo, then vẫn cho phép, **không link tự động** vào account.

#### Story 15: Owner force cancel

1. Given booking ở status active (`pending_payment`, `payment_processing`, `confirmed`), when owner force cancel + nhập reason, then booking → `cancelled_by_owner`, slot → `available`, refund 100%, customer nhận email kèm reason.
2. Given booking `completed`, when owner cancel, then `409`.
3. Given booking là walk-in, when owner cancel, then booking → `cancelled_by_owner`, refund record `method=offline_cash` + `pending` (owner tự xử lý).
4. Given không kèm reason, when submit, then `400 validation`.

#### Story 11: Slot control (Owner close slot)

1. Given slot `available`, when owner close + reason, then slot → `closed`.
2. Given slot đang `held`, when close, then `409`, owner phải đợi.
3. Given slot `booked`, when close, then `409`, owner phải cancel booking trước.
4. Given slot `closed`, when reopen, then slot → `available`.
5. Given bulk close, when submit, then **non-atomic**: xử lý từng slot độc lập, response trả `{success: [], failed: [{slot_id, reason}]}`.

### Group B — CRUD stories

#### Story 1: Customer auth

1. Given valid registration data, when sign up, then tạo account với status `unverified` + gửi email verification.
2. Given email đã tồn tại, when register, then `409`. Given password < 8 hoặc email sai format, then `422`.
3. Given valid credentials + email đã verify, when login, then trả JWT access token + refresh token.
4. Given valid credentials nhưng chưa verify email, when login, then `403` với message "vui lòng xác thực email".
5. Given invalid credentials, when login, then `401`.
6. Given customer click verification link, when token hợp lệ + chưa expire (24h), then account → `verified`.
7. Given verification token expired, when click, then `410` + button "gửi lại email".

#### Story 5: Customer view bookings

1. Given customer request list không filter, when call API, then trả về **upcoming bookings** (slot_start > now), sort theo slot_start ASC, **pagination** mặc định 20/page.
2. Given customer truyền `?status=cancelled&page=2&limit=20`, when call, then trả về booking khớp filter.
3. Given booking thuộc customer, when request detail, then trả về full info (slots, payment, status, refund nếu có).
4. Given booking không thuộc customer, when request detail, then `403`.

#### Story 7: Customer notifications

Trigger events: confirmed, cancelled, refund processed, slot reminder (1–2h), owner cancel với reason.

1. Given event xảy ra, when trigger, then gửi in-app notification + email song song.
2. Given owner cancel với reason, when notify, then email **chứa reason**.
3. Given slot bắt đầu trong 1–2 giờ, when reminder job chạy, then gửi notification cho confirmed bookings.
4. Given email send fail, when xảy ra, then push vào retry queue (Redis), retry max 3 lần với exponential backoff (1m, 5m, 25m).
5. Given retry hết 3 lần vẫn fail, when xử lý, then mark notification `failed`, log alert.

#### Story 8: Owner login

1. Given valid owner credentials, when login, then trả JWT với role=owner.
2. Given invalid credentials, when login, then `401`.
3. Given login (success or fail), when xảy ra, then ghi audit log (timestamp, IP, user_agent, outcome).

#### Story 9: Owner manage facility/court

1. Given owner request list, when không filter, then trả về toàn bộ facility + court (single-tenant).
2. Given valid input, when create facility/court, then lưu với `tenant_id` + `created_at`.
3. Given existing entity, when update, then save changes + cập nhật `updated_at`.
4. Given facility/court có booking active, when delete, then `409`.
5. Given facility/court không có booking active, when delete, then **soft delete** (`deleted_at = now`), entity ẩn khỏi list nhưng booking history vẫn reference được.

#### Story 10: Owner pricing config

Model: disjoint time blocks, cover toàn bộ business hour.

1. Given valid pricing rules cho 1 court (cover 6:00–22:00 cho cả 7 ngày), when save, then lưu thành công.
2. Given rules có **overlap**, when save, then `422 validation` với chi tiết overlap.
3. Given rules có **gap** (vd: chỉ cover 6:00–20:00, thiếu 20:00–22:00), when save, then `422 validation`.
4. Given slot được query, when có pricing rule áp dụng, then trả về đúng giá theo time block.
5. Given court chưa có pricing config, when query slot, then trả về **default_price** ở court level.

#### Story 12: Owner view bookings (all)

1. Given owner request list không filter, when call, then trả về bookings, sort theo slot_start DESC, pagination 50/page.
2. Given filter `?date=2026-05-04&court_id=1&status=confirmed`, when call, then trả về booking khớp filter.
3. Given booking thuộc facility owner, when request detail, then trả về full info (customer, payment, history).

#### Story 14: Owner check-in customer

State machine: `confirmed` → `in_use` → `completed`

1. Given booking `confirmed` + thời gian hiện tại trong khoảng [first_slot_start - 15ph, last_slot_end], when owner check-in, then booking → `in_use`.
2. Given booking ở status khác `confirmed`, when check-in, then `409`.
3. Given thời gian quá xa first_slot_start, when check-in, then `409` với message "chưa đến giờ".
4. Given booking ở `in_use` và `last_slot_end < now`, when background job chạy mỗi 5–10 phút, then booking → `completed` **tự động**.

#### Story 16: Owner revenue report

1. Given date range hợp lệ, when request, then trả về tổng revenue cho facilities của owner trong khoảng đó.
2. Given bookings tồn tại, when generate, then chỉ tính `confirmed`, `in_use`, `completed`. Loại `pending_payment`, `expired`, `cancelled`, `payment_failed`.
3. Given booking đã refund (full hoặc partial), when tính, then **revenue = paid_amount - refund_amount** (revenue thực).
4. Given không có booking, when generate, then trả về 0.
5. Given date range > 1 năm, when request, then `400` (giới hạn để tránh query nặng).

---

## 5. Constraints

### Technical

| Khía cạnh | Quyết định |
|---|---|
| Backend | FastAPI + SQLModel + PostgreSQL 15+ |
| Migration | Alembic |
| Cache/Queue | Redis (cache + retry queue + reconciliation lock) |
| Auth | JWT (access 15ph + refresh 7 ngày) + RBAC |
| Payment | VNPay sandbox |
| Email | SMTP (Gmail/Mailtrap cho dev) |
| Container | Docker + Docker Compose |
| Test | pytest + pytest-asyncio |
| CI | GitHub Actions (lint + test + build) |
| Timezone | DB lưu UTC, API trả ISO 8601 + offset, business rule dùng UTC+7 |

### Non-functional

| Khía cạnh | Yêu cầu |
|---|---|
| Performance | API response p95 < 500ms (search, list); p95 < 1s (booking creation) |
| Concurrency | Hỗ trợ ≥ 50 concurrent booking attempts không double-book |
| Availability | MVP: best-effort (1 server). Không SLA cụ thể. |
| Data integrity | Không double-book là **hard constraint** (DB-level, không chỉ app-level) |
| Idempotency | Webhook + payment initiation phải idempotent |
| Logging | Structured log (JSON), log mọi state transition của booking |
| Security | Password bcrypt, không log sensitive data, validate mọi input |

---

## 6. Out-of-Scope

### Cắt khỏi MVP (Phase 2 nếu có thời gian)

- Reschedule booking — customer phải cancel + book lại
- Promotion code / voucher
- Member discount / loyalty program
- Multi-tenant / Marketplace (đã có data model dự phòng)
- Mobile app — chỉ có REST API
- Real-time push (WebSocket) — search chỉ on-demand fetch
- SMS / Zalo OA notification — chỉ Email + In-app
- Multi-court trong 1 booking — 1 booking = 1 sân
- Deposit/partial payment — chỉ full payment online hoặc offline cash
- Staff persona riêng — owner kiêm
- Platform admin — không có
- Owner self-registration — owner seed qua script
- No-show tracking
- Recurring booking (đặt cố định mỗi tuần)
- Review / rating sân
- Court image upload / gallery
- Reports nâng cao — chỉ có total revenue theo date range
- Email verification production-grade (SES/SendGrid) — MVP dùng Gmail SMTP
- Rate limiting toàn cục — chỉ rate limit endpoint critical (login, booking creation)
- Audit log đầy đủ — chỉ log auth + booking state change

### Không bao giờ làm

- Trở thành Marketplace như Booking.com — đó là project khác
- Tích hợp camera / IoT check-in tự động
- Mobile app native

---

## 7. Success Metrics

### Functional (đạt = MVP done)

- ✅ 16 user stories có endpoint hoạt động đúng AC
- ✅ Test coverage ≥ 70% cho service layer
- ✅ **Concurrency test:** 100 request đồng thời cố book cùng 1 slot → đúng 1 thành công, 99 nhận `409`
- ✅ **Idempotency test:** webhook VNPay nhận 10 lần cùng 1 payload → state thay đổi đúng 1 lần
- ✅ **Reconciliation test:** booking pending > 30 phút → job tự xử lý đúng (confirm hoặc cancel theo VNPay status)
- ✅ Toàn bộ flow chạy được end-to-end qua Postman: register → verify email → search → reserve → pay → cancel → refund

### Engineering (đạt = code quality OK)

- ✅ CI pipeline pass: lint (ruff/black) + type check (mypy optional) + test
- ✅ Docker compose up 1 lệnh là chạy được toàn bộ stack
- ✅ Có README setup ≤ 5 phút cho người mới clone repo
- ✅ Có ERD + state machine diagram trong repo

### Portfolio (đạt = sẵn sàng phỏng vấn)

- ✅ Repo public trên GitHub có README rõ ràng (problem, design, trade-offs)
- ✅ Có demo video 3–5 phút hoặc deploy live (Railway/Render free tier)
- ✅ Tự kể được trong 5 phút: domain, kiến trúc, 3 thách thức kỹ thuật đã giải quyết
- ✅ Trả lời được câu hỏi: "Vì sao chọn pessimistic lock thay vì optimistic ở đây?"

---

## SDLC Roadmap

| Phase | Status | Output |
|---|---|---|
| **1. CLARIFY** | ✅ Done | README này |
| **2. DESIGN** | ⏳ Next | ERD + State Machine + API Contract (FastAPI skeleton) |
| **3. BUILD** | — | Vertical slice mỗi feature: Model → Migration → Repo → Service → API → Test |
| **4. HARDEN** | — | Concurrency test, edge case, logging, deploy |

---

*Maintained by: Bảo | Last updated: Phase 1 complete*
