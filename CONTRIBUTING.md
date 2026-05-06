# Contributing

## Commit convention

Theo [Conventional Commits](https://www.conventionalcommits.org/).

Format:

```
<type>(<scope>): <subject>

[optional body]

[optional footer]
```

### Types

| Type       | Khi nào dùng                                  |
| ---------- | --------------------------------------------- |
| `feat`     | Thêm feature mới (user-facing)                |
| `fix`      | Fix bug                                       |
| `docs`     | Sửa documentation (README, comment, ADR)      |
| `style`    | Format code, không đổi logic (ruff format)    |
| `refactor` | Refactor code, không đổi behavior             |
| `test`     | Thêm/sửa test                                 |
| `chore`    | Maintenance (update deps, config)             |
| `ci`       | Sửa CI/CD (.github/workflows)                 |
| `perf`     | Cải thiện performance                         |
| `build`    | Sửa build system (Dockerfile, pyproject.toml) |
| `revert`   | Revert commit cũ                              |

### Scope (optional)

Tên module hoặc khu vực: `auth`, `booking`, `payment`, `db`, `infra`...

### Examples

```
feat(auth): add email verification flow
fix(booking): release slot when payment timeout
docs: update DESIGN.md state machine diagram
refactor(payment): extract VNPay client to separate module
test(booking): add concurrency test for slot reservation
chore(deps): bump fastapi to 0.115
ci: add docker build job
```

### Bad examples

```
❌ "update code"           — không có type
❌ "Fixed bug"             — capitalize sai, type sai format
❌ "feat: stuff"           — subject không rõ ràng
❌ "feat(auth): added..."  — past tense (dùng imperative: "add")
```

---

## Branch naming

```
<type>/<short-description>
```

Examples:

```
feat/auth-email-verification
fix/booking-race-condition
refactor/payment-vnpay-client
chore/update-deps
```

---

## Pre-commit hooks

Tự động chạy trước mỗi `git commit`:

- Ruff format + lint
- Trailing whitespace, end-of-file fixer
- YAML/TOML/JSON syntax check
- Conventional commit message check

Setup lần đầu:

```bash
uv run pre-commit install
uv run pre-commit install --hook-type commit-msg
```

Bypass khẩn cấp (KHÔNG khuyến khích):

```bash
git commit --no-verify -m "..."
```

---

## Pull Request

> MVP solo dev: làm trên `main` cho nhanh. Đoạn này áp dụng khi có collaborator hoặc muốn workflow chuẩn.

1. Branch từ `develop` hoặc `main`
2. Commit theo convention
3. Push + open PR
4. CI phải pass (lint + test + docker build)
5. Self-review trước khi merge

---

## Workflow điển hình

```bash
# Tạo branch mới
git checkout -b feat/auth-login

# Code...
# Stage + commit (pre-commit chạy tự động)
git add .
git commit -m "feat(auth): add login endpoint with JWT"

# Push
git push origin feat/auth-login

# CI sẽ chạy. Nếu pass, merge.
```

---

## Sub-slice workflow (chuẩn cho mọi feature)

```bash
# 1. Sync main
git checkout main && git pull origin main

# 2. Tạo branch
git checkout -b feat/<scope>-<description>

# 3. Code + commit (commit nhỏ, thường xuyên)
git add <files>
git commit -m "feat(<scope>): <description>"

# 4. Push
git push -u origin feat/<scope>-<description>

# 5. Tạo PR
gh pr create --title "feat(<scope>): <description>" --body "..."
# Hoặc qua GitHub UI

# 6. Đợi CI pass

# 7. Merge (squash)
gh pr merge --squash --delete-branch

# 8. Sync main + cleanup
git checkout main && git pull origin main
git branch -d feat/<scope>-<description>
```
