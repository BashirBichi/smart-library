from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable
from urllib.parse import urlparse

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import Index, UniqueConstraint, func, or_, select
from werkzeug.security import check_password_hash, generate_password_hash


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///smart_library.db").replace(
            "postgres://", "postgresql+psycopg://", 1
        ).replace("postgresql://", "postgresql+psycopg://", 1),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True, "pool_recycle": 300},
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "true").lower() == "true",
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE="Lax",
        WTF_CSRF_TIME_LIMIT=3600,
        LIBRARY_NAME=os.environ.get("LIBRARY_NAME", "Smart Library"),
    )
    if app.config["SECRET_KEY"] == "dev-only-change-me" and os.environ.get("FLASK_ENV") == "production":
        raise RuntimeError("SECRET_KEY must be set in production")

    db = SQLAlchemy(app)
    csrf = CSRFProtect(app)
    login_manager = LoginManager(app)
    login_manager.login_view = "login"
    login_manager.login_message_category = "warning"

    class Institution(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(160), nullable=False)
        created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    class User(UserMixin, db.Model):
        id = db.Column(db.Integer, primary_key=True)
        institution_id = db.Column(db.Integer, db.ForeignKey("institution.id"), nullable=False, index=True)
        full_name = db.Column(db.String(140), nullable=False)
        email = db.Column(db.String(254), nullable=False)
        password_hash = db.Column(db.String(255), nullable=False)
        role = db.Column(db.String(24), nullable=False, default="member")
        active = db.Column(db.Boolean, nullable=False, default=True)
        created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
        institution = db.relationship("Institution")
        __table_args__ = (UniqueConstraint("institution_id", "email", name="uq_user_institution_email"),)

        def set_password(self, password: str) -> None:
            self.password_hash = generate_password_hash(password)

        def check_password(self, password: str) -> bool:
            return check_password_hash(self.password_hash, password)

        @property
        def is_active(self) -> bool:
            return self.active

    class Book(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        institution_id = db.Column(db.Integer, db.ForeignKey("institution.id"), nullable=False, index=True)
        title = db.Column(db.String(240), nullable=False, index=True)
        author = db.Column(db.String(180), nullable=False, default="Unknown")
        isbn = db.Column(db.String(32))
        publisher = db.Column(db.String(180))
        category = db.Column(db.String(100), nullable=False, default="General")
        publication_year = db.Column(db.Integer)
        shelf_location = db.Column(db.String(80))
        description = db.Column(db.Text)
        total_copies = db.Column(db.Integer, nullable=False, default=1)
        available_copies = db.Column(db.Integer, nullable=False, default=1)
        resource_type = db.Column(db.String(20), nullable=False, default="physical")
        institution = db.relationship("Institution")
        created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
        __table_args__ = (UniqueConstraint("institution_id", "isbn", name="uq_book_institution_isbn"),)

    class Loan(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        institution_id = db.Column(db.Integer, db.ForeignKey("institution.id"), nullable=False, index=True)
        book_id = db.Column(db.Integer, db.ForeignKey("book.id"), nullable=False)
        member_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
        issued_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
        issued_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
        due_date = db.Column(db.Date, nullable=False)
        returned_at = db.Column(db.DateTime(timezone=True))
        book = db.relationship("Book")
        member = db.relationship("User", foreign_keys=[member_id])
        issuer = db.relationship("User", foreign_keys=[issued_by_id])

    class Reservation(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        institution_id = db.Column(db.Integer, db.ForeignKey("institution.id"), nullable=False, index=True)
        book_id = db.Column(db.Integer, db.ForeignKey("book.id"), nullable=False)
        member_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
        status = db.Column(db.String(20), nullable=False, default="pending")
        created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
        book = db.relationship("Book")
        member = db.relationship("User")

    class AuditLog(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        institution_id = db.Column(db.Integer, db.ForeignKey("institution.id"), nullable=False, index=True)
        actor_id = db.Column(db.Integer, db.ForeignKey("user.id"))
        action = db.Column(db.String(100), nullable=False)
        entity_type = db.Column(db.String(60), nullable=False)
        entity_id = db.Column(db.Integer)
        created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    Index("ix_loan_open_due", Loan.institution_id, Loan.returned_at, Loan.due_date)
    Index("ix_reservation_member_status", Reservation.institution_id, Reservation.member_id, Reservation.status)

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    def staff_required(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        @login_required
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if current_user.role not in {"owner", "admin", "librarian"}:
                abort(403)
            return fn(*args, **kwargs)
        return wrapped

    def log_action(action: str, entity_type: str, entity_id: int | None = None) -> None:
        db.session.add(AuditLog(
            institution_id=current_user.institution_id,
            actor_id=current_user.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
        ))

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {"library_name": app.config["LIBRARY_NAME"], "today": date.today()}

    @app.get("/health")
    def health() -> Any:
        try:
            db.session.execute(select(1))
            return jsonify(status="ok", database="connected"), 200
        except Exception:
            db.session.rollback()
            return jsonify(status="degraded", database="unavailable"), 503

    @app.get("/")
    def index() -> Any:
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        total_books = db.session.scalar(select(func.count(Book.id))) or 0
        total_libraries = db.session.scalar(select(func.count(Institution.id))) or 0
        return render_template("landing.html", total_books=total_books, total_libraries=total_libraries)

    @app.route("/register", methods=["GET", "POST"])
    def register() -> Any:
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            institution_name = request.form.get("institution_name", "").strip()
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")
            if len(institution_name) < 2 or len(full_name) < 2 or "@" not in email:
                flash("Enter a valid library name, full name and email address.", "danger")
            elif len(password) < 12:
                flash("Use a password with at least 12 characters.", "danger")
            elif password != confirm_password:
                flash("The passwords do not match.", "danger")
            else:
                institution = Institution(name=institution_name)
                db.session.add(institution)
                try:
                    db.session.flush()
                    owner = User(
                        institution_id=institution.id,
                        full_name=full_name,
                        email=email,
                        role="owner",
                    )
                    owner.set_password(password)
                    db.session.add(owner)
                    db.session.commit()
                    login_user(owner)
                    flash("Your library workspace has been created.", "success")
                    return redirect(url_for("dashboard"))
                except Exception:
                    db.session.rollback()
                    flash("Registration could not be completed. Check the details and try again.", "danger")
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login() -> Any:
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            institution_id = request.form.get("institution_id", type=int)
            query = select(User).where(func.lower(User.email) == email, User.active.is_(True))
            if institution_id:
                query = query.where(User.institution_id == institution_id)
            users = db.session.scalars(query.limit(2)).all()
            user = users[0] if len(users) == 1 else None
            if user and user.check_password(password):
                login_user(user, remember=request.form.get("remember") == "on")
                next_url = request.args.get("next", "")
                parsed_next = urlparse(next_url)
                if parsed_next.scheme or parsed_next.netloc or not next_url.startswith("/"):
                    next_url = url_for("dashboard")
                return redirect(next_url)
            flash("Invalid credentials. Check your email and password.", "danger")
        institutions = db.session.scalars(select(Institution).order_by(Institution.name)).all()
        return render_template("login.html", institutions=institutions)

    @app.post("/logout")
    @login_required
    def logout() -> Any:
        logout_user()
        return redirect(url_for("login"))

    @app.get("/dashboard")
    @login_required
    def dashboard() -> Any:
        iid = current_user.institution_id
        books_count = db.session.scalar(select(func.count(Book.id)).where(Book.institution_id == iid)) or 0
        members_count = db.session.scalar(select(func.count(User.id)).where(User.institution_id == iid, User.role == "member")) or 0
        active_loans = db.session.scalar(select(func.count(Loan.id)).where(Loan.institution_id == iid, Loan.returned_at.is_(None))) or 0
        overdue = db.session.scalar(select(func.count(Loan.id)).where(Loan.institution_id == iid, Loan.returned_at.is_(None), Loan.due_date < date.today())) or 0
        recent = db.session.scalars(select(Loan).where(Loan.institution_id == iid).order_by(Loan.issued_at.desc()).limit(8)).all()
        return render_template("dashboard.html", books_count=books_count, members_count=members_count,
                               active_loans=active_loans, overdue=overdue, recent=recent)

    @app.get("/catalog")
    def catalog() -> Any:
        term = request.args.get("q", "").strip()[:120]
        query = select(Book).join(Institution, Book.institution_id == Institution.id)
        if term:
            pattern = f"%{term}%"
            query = query.where(or_(Book.title.ilike(pattern), Book.author.ilike(pattern),
                                    Book.isbn.ilike(pattern), Book.category.ilike(pattern),
                                    Institution.name.ilike(pattern)))
        books = db.session.scalars(query.order_by(Book.title).limit(250)).all()
        return render_template("catalog.html", books=books, q=term, public_view=not current_user.is_authenticated)

    @app.route("/books/new", methods=["GET", "POST"])
    @staff_required
    def new_book() -> Any:
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            author = request.form.get("author", "").strip()
            isbn = request.form.get("isbn", "").strip() or None
            copies = request.form.get("copies", type=int, default=1)
            if not title or not author or copies < 1 or copies > 10000:
                flash("Enter a title, author and a valid copy count.", "danger")
            else:
                book = Book(
                    institution_id=current_user.institution_id, title=title, author=author,
                    isbn=isbn, publisher=request.form.get("publisher", "").strip() or None,
                    category=request.form.get("category", "General").strip() or "General",
                    publication_year=request.form.get("publication_year", type=int),
                    shelf_location=request.form.get("shelf_location", "").strip() or None,
                    description=request.form.get("description", "").strip() or None,
                    total_copies=copies, available_copies=copies,
                    resource_type=request.form.get("resource_type", "physical")
                    if request.form.get("resource_type") in {"physical", "digital", "reference"} else "physical",
                )
                db.session.add(book)
                try:
                    db.session.flush()
                    log_action("book.created", "book", book.id)
                    db.session.commit()
                    flash("Book added to the catalogue.", "success")
                    return redirect(url_for("catalog"))
                except Exception:
                    db.session.rollback()
                    flash("Could not save this book. Check whether its ISBN already exists.", "danger")
        return render_template("book_form.html")

    @app.post("/books/<int:book_id>/reserve")
    @login_required
    def reserve_book(book_id: int) -> Any:
        book = db.session.scalar(select(Book).where(Book.id == book_id, Book.institution_id == current_user.institution_id))
        if not book:
            abort(404)
        existing = db.session.scalar(select(Reservation).where(
            Reservation.book_id == book.id, Reservation.member_id == current_user.id,
            Reservation.status == "pending"))
        if existing:
            flash("You already have an active reservation for this title.", "info")
        else:
            db.session.add(Reservation(institution_id=current_user.institution_id, book_id=book.id, member_id=current_user.id))
            log_action("reservation.created", "book", book.id)
            db.session.commit()
            flash("Reservation request submitted.", "success")
        return redirect(url_for("catalog"))

    @app.route("/members", methods=["GET", "POST"])
    @staff_required
    def members() -> Any:
        if request.method == "POST":
            name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            role = request.form.get("role", "member")
            if current_user.role == "librarian" and role != "member":
                flash("Librarians can create member accounts only.", "danger")
            elif not name or "@" not in email or len(password) < 12 or role not in {"member", "librarian", "admin"}:
                flash("Provide valid details. Passwords must be at least 12 characters.", "danger")
            else:
                user = User(institution_id=current_user.institution_id, full_name=name, email=email, role=role)
                user.set_password(password)
                db.session.add(user)
                try:
                    db.session.flush()
                    log_action("member.created", "user", user.id)
                    db.session.commit()
                    flash("Account created successfully.", "success")
                except Exception:
                    db.session.rollback()
                    flash("Could not create account. This email may already exist in this institution.", "danger")
        people = db.session.scalars(select(User).where(
            User.institution_id == current_user.institution_id).order_by(User.created_at.desc()).limit(300)).all()
        return render_template("members.html", people=people)

    @app.get("/loans")
    @login_required
    def loans() -> Any:
        query = select(Loan).where(Loan.institution_id == current_user.institution_id)
        if current_user.role == "member":
            query = query.where(Loan.member_id == current_user.id)
        records = db.session.scalars(query.order_by(Loan.issued_at.desc()).limit(300)).all()
        reservation_query = select(Reservation).where(
            Reservation.institution_id == current_user.institution_id,
            Reservation.status == "pending",
        )
        if current_user.role == "member":
            reservation_query = reservation_query.where(Reservation.member_id == current_user.id)
        reservations = db.session.scalars(
            reservation_query.order_by(Reservation.created_at.desc()).limit(100)
        ).all()
        issue_books = db.session.scalars(select(Book).where(
            Book.institution_id == current_user.institution_id,
            Book.available_copies > 0,
        ).order_by(Book.title).limit(500)).all() if current_user.role in {"owner", "admin", "librarian"} else []
        return render_template("loans.html", loans=records, reservations=reservations, issue_books=issue_books)

    @app.post("/loans/issue")
    @staff_required
    def issue_loan() -> Any:
        book_id = request.form.get("book_id", type=int)
        member_id = request.form.get("member_id", type=int)
        days = request.form.get("days", type=int, default=14)
        if not book_id or not member_id or not 1 <= days <= 90:
            flash("Select a valid book, member and loan period.", "danger")
            return redirect(url_for("loans"))
        book = db.session.scalar(select(Book).where(Book.id == book_id, Book.institution_id == current_user.institution_id).with_for_update())
        member = db.session.scalar(select(User).where(User.id == member_id, User.institution_id == current_user.institution_id, User.role == "member", User.active.is_(True)))
        if not book or not member or book.available_copies < 1:
            flash("Book unavailable or member not found in this library.", "danger")
            return redirect(url_for("loans"))
        book.available_copies -= 1
        loan = Loan(institution_id=current_user.institution_id, book_id=book.id, member_id=member.id,
                    issued_by_id=current_user.id, due_date=date.today() + timedelta(days=days))
        db.session.add(loan)
        log_action("loan.issued", "loan")
        db.session.commit()
        flash("Book issued successfully.", "success")
        return redirect(url_for("loans"))

    @app.post("/loans/<int:loan_id>/return")
    @staff_required
    def return_loan(loan_id: int) -> Any:
        loan = db.session.scalar(select(Loan).where(
            Loan.id == loan_id, Loan.institution_id == current_user.institution_id).with_for_update())
        if not loan:
            abort(404)
        if loan.returned_at is None:
            loan.returned_at = datetime.now(timezone.utc)
            book = db.session.scalar(select(Book).where(Book.id == loan.book_id).with_for_update())
            if book:
                book.available_copies = min(book.total_copies, book.available_copies + 1)
            log_action("loan.returned", "loan", loan.id)
            db.session.commit()
            flash("Return recorded.", "success")
        return redirect(url_for("loans"))

    @app.errorhandler(403)
    def forbidden(_: Any) -> Any:
        return render_template("error.html", code=403, message="You do not have permission to access this area."), 403

    @app.errorhandler(404)
    def not_found(_: Any) -> Any:
        return render_template("error.html", code=404, message="The requested resource was not found."), 404

    @app.cli.command("init-db")
    def init_db() -> None:
        """Create the initial schema and optional bootstrap owner in a single process."""
        db.create_all()
        bootstrap_email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
        bootstrap_password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
        if bootstrap_email and bootstrap_password:
            institution_name = os.environ.get("BOOTSTRAP_INSTITUTION", "Smart Library Demo")
            institution = db.session.scalar(select(Institution).where(Institution.name == institution_name))
            if institution is None:
                institution = Institution(name=institution_name)
                db.session.add(institution)
                db.session.flush()
            admin = db.session.scalar(select(User).where(
                User.institution_id == institution.id, func.lower(User.email) == bootstrap_email))
            if admin is None:
                admin = User(institution_id=institution.id, full_name="Library Administrator",
                             email=bootstrap_email, role="owner")
                admin.set_password(bootstrap_password)
                db.session.add(admin)
                db.session.commit()
        db.session.remove()

    return app


app = create_app()
