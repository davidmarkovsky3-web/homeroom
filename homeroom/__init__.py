"""Homeroom — a student information system (marketing site + SIS app)."""

import os
from datetime import date, datetime

from flask import Flask, render_template

from .extensions import db, login_manager


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("HOMEROOM_SECRET_KEY", "dev-secret-change-me"),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "HOMEROOM_DATABASE_URI",
            "sqlite:///" + os.path.join(app.instance_path, "homeroom.sqlite"),
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,   # cap uploads
    )
    if config:
        app.config.update(config)

    db.init_app(app)
    login_manager.init_app(app)

    from . import models  # noqa: F401  (registers models with SQLAlchemy)
    from .views import (
        admin, assignments, attendance, auth, calendar, courses, district,
        documents, grades, importer, main, more, notices, parents, public,
        staff, support,
    )

    for blueprint in (
        public.bp, auth.bp, main.bp, calendar.bp, courses.bp, attendance.bp,
        grades.bp, assignments.bp, documents.bp, support.bp, parents.bp,
        notices.bp, more.bp, importer.bp, admin.bp, district.bp, staff.bp,
    ):
        app.register_blueprint(blueprint)

    register_cli(app)
    register_template_helpers(app)
    register_error_handlers(app)
    register_password_gate(app)

    with app.app_context():
        db.create_all()
        sync_added_columns(app)

    return app


def sync_added_columns(app):
    """Add columns that exist on a model but not yet in the database.

    `create_all()` creates missing tables but never alters existing ones, which used to
    mean every new column forced a full reseed and destroyed real data. SQLite supports
    ADD COLUMN, so additive changes can be applied in place. Anything harder — dropping
    or retyping a column — still needs a proper migration tool.
    """
    from sqlalchemy import inspect, text

    if not db.engine.url.drivername.startswith("sqlite"):
        return   # only safe to do this blind on the throwaway SQLite database

    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    added = []

    for table in db.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue

            column_type = column.type.compile(db.engine.dialect)
            default = getattr(column.default, "arg", None)
            if isinstance(default, bool):
                clause = f" DEFAULT {1 if default else 0}"
            elif isinstance(default, (int, float)):
                clause = f" DEFAULT {default}"
            elif isinstance(default, str):
                escaped = default.replace("'", "''")
                clause = f" DEFAULT '{escaped}'"
            else:
                clause = ""

            with db.engine.begin() as conn:
                conn.execute(text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" '
                    f"{column_type}{clause}"
                ))
            added.append(f"{table.name}.{column.name}")

    if added:
        app.logger.warning("Added missing columns in place: %s", ", ".join(added))
    return added


def register_template_helpers(app):
    from flask_login import current_user

    from .models import ROLE_LABELS
    from .security import active_school, switchable_schools

    @app.context_processor
    def inject_globals():
        school = active_school() if current_user.is_authenticated else None
        badge = 0
        if current_user.is_authenticated:
            from .services import unread_count
            try:
                badge = unread_count(current_user)
            except Exception:      # never let the badge break a page render
                badge = 0
        return {
            "school": school,
            "school_name": school.name if school else "Homeroom",
            # Whatever this school calls its flex block. Templates must use this
            # rather than the word "Support" so a rename reaches every surface.
            "support_label": school.support_label if school else "Support",
            # Not every school has homerooms; when it doesn't, the field is hidden
            # rather than shown empty.
            "uses_homeroom": school.uses_homeroom if school else False,
            "homeroom_label": school.homeroom_label if school else "Homeroom",
            "switchable_schools": (
                switchable_schools() if current_user.is_authenticated else []
            ),
            "role_labels": ROLE_LABELS,
            "notice_count": badge,
            "today": date.today(),
            "now": datetime.now(),
        }

    @app.template_filter("pretty_date")
    def pretty_date(value):
        if not value:
            return "—"
        return value.strftime("%b %d, %Y").replace(" 0", " ")

    @app.template_filter("pretty_time")
    def pretty_time(value):
        if not value:
            return "—"
        return value.strftime("%I:%M %p").lstrip("0")

    @app.template_filter("money")
    def money(value):
        return "—" if value is None else f"${value:,.2f}"

    @app.template_filter("pct")
    def pct(value, places=1):
        return "—" if value is None else f"{round(value, places)}%"

    @app.template_filter("grade_color")
    def grade_color(percent):
        """CSS variable name for a percentage, used for grade pills and meters."""
        if percent is None:
            return "var(--ink-3)"
        if percent >= 90:
            return "var(--ok)"
        if percent >= 80:
            return "var(--brand)"
        if percent >= 70:
            return "var(--warn)"
        return "var(--bad)"


def register_password_gate(app):
    """Nothing works until an imported account picks its own password.

    This is what makes a shared batch password tolerable: the window in which it opens
    an account is only as long as it takes that person to sign in the first time.
    """
    from flask import redirect, request, url_for
    from flask_login import current_user

    ALLOWED = {"auth.set_password", "auth.logout", "static"}

    @app.before_request
    def force_password_change():
        if not current_user.is_authenticated:
            return None
        if not getattr(current_user, "must_change_password", False):
            return None
        if request.endpoint in ALLOWED or request.endpoint is None:
            return None
        return redirect(url_for("auth.set_password"))


def register_error_handlers(app):
    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("errors/error.html", code=403,
                               message="You don't have access to that page."), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("errors/error.html", code=404,
                               message="We couldn't find that page."), 404

    @app.errorhandler(413)
    def too_large(_e):
        return render_template("errors/error.html", code=413,
                               message="That file is too large. The limit is 15 MB."), 413


def register_cli(app):
    import click

    @app.cli.command("seed")
    @click.option("--reset", is_flag=True, help="Drop all tables before seeding.")
    def seed_command(reset):
        """Populate the database with a demo district."""
        from .seed import seed_database

        seed_database(reset=reset)
        click.echo("Seeded Homeroom demo data.")
