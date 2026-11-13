# Smart Library

Smart Library is a cloud-hosted library operations platform for institutions and independent libraries. The initial release provides institution-scoped accounts, catalogue management, member administration, circulation, reservations, dashboard metrics and audit events.

## Stack
- Python 3.12 / Flask
- SQLAlchemy with PostgreSQL (SQLite for local development only)
- Flask-Login and Flask-WTF CSRF protection
- Gunicorn and Docker for Render

## Local development
1. Create a virtual environment and install `requirements.txt`.
2. Copy `.env.example` to `.env` and set a strong `SECRET_KEY`.
3. Configure `DATABASE_URL` and bootstrap administrator variables.
4. Run `flask --app app run`.

A bootstrap owner is created when `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` are configured. Use a unique password of at least 12 characters and keep it out of source control.

## Render deployment
The service uses `render.yaml`. Configure `DATABASE_URL`, `BOOTSTRAP_ADMIN_EMAIL`, and `BOOTSTRAP_ADMIN_PASSWORD` as Render environment variables. Use managed PostgreSQL for durable production data. The container filesystem is not durable storage.

## Current release scope
- Institution-scoped users and catalogue records
- Owner, admin, librarian and member roles
- Catalogue search and resource creation
- Member administration
- Loan issue/return tracking and due dates
- Member reservations
- Audit events and database health endpoint

## Production readiness gaps
This is a functional foundation, not yet a fully hardened institutional deployment. Before real institutional onboarding, add Alembic schema migrations, email verification and password reset, invitation workflows, rate limiting, automated tests, secure object storage with signed access, file malware scanning, fine policies, MARC 21 / Dublin Core metadata mapping, backup/restore procedures and operational monitoring. Do not use the demo SQLite database for production.
