# UniCart API

Commercial multi-university backend for [UniCart](https://github.com/epic-ice-fire/unicart_app), supporting Pan-Atlantic University, Augustine University and Babcock University campus marketplaces.

[![Backend CI](https://github.com/epic-ice-fire/unicart-backend/actions/workflows/backend-ci.yml/badge.svg)](https://github.com/epic-ice-fire/unicart-backend/actions/workflows/backend-ci.yml)

**Live client:** https://unicartbytekena.onrender.com  
**API health:** https://unicart-backend-v6u9.onrender.com/health

## What the API handles

- Account registration, login, logout and revocable remembered sessions
- Account registration/login followed by campus selection; no school-email OTP, matric number, ID upload or manual student approval is required
- Shared lobby membership, item submission and batch lifecycle
- Flutterwave entry-fee and item payment initialization/verification
- Immutable gateway transaction claims and financial audit events
- Admin review, item removal, target updates and batch status transitions
- Rate limiting, request-size limits, strict CORS/host checks and production fail-closed validation

## Stack

| Area | Technology |
| --- | --- |
| API | FastAPI / Python |
| ORM | SQLAlchemy Async |
| Database | PostgreSQL |
| Payments | Flutterwave |
| Email | Gmail API |
| Authentication | JWT + server-side session registry |
| Tests | pytest |
| Hosting | Render |
| CI | GitHub Actions |

## Structure

```text
app/
├── routers/
│   ├── auth.py
│   ├── lobbies.py
│   └── payments.py
├── audit.py
├── config.py
├── db.py
├── email_service.py
├── gmail_api_transport.py
├── models.py
├── rate_limit.py
└── security.py

scripts/    migrations, integrity checks, backup/restore and launch audit

docs/MULTI_UNIVERSITY_ROLLOUT.md documents the campus model and migration.
tests/      authorization, sessions, checkout recovery and financial integrity
```

## Local setup

```bash
python -m venv .venv
# activate the virtual environment for your shell
pip install -r requirements.txt
```

Copy `.env.example` to `.env`, fill only local/test credentials, then run:

```bash
uvicorn app.main:app --reload
```

Production credentials belong in the hosting environment, never in source control.

## Verification

```bash
python -m pytest -q
python scripts/security_preflight.py
```

The production launch audit is intentionally fail-closed and additionally verifies runtime configuration, Gmail API access, PostgreSQL integrity, a real verified Flutterwave payment, financial audit records, and production readiness attestations.

## Security

Production disables the interactive API docs, authenticates payment webhooks, verifies payment amount/currency/reference independently, uses revocable server-side sessions, and keeps financial gateway claims/audit records immutable at the database layer.

See [SECURITY.md](SECURITY.md) for responsible disclosure.
