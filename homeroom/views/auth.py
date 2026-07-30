"""Sign in, sign out, and account settings."""

from datetime import datetime
from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..models import User
from ..security import switchable_schools

bp = Blueprint("auth", __name__, url_prefix="/auth")

DIGEST_CHOICES = ("realtime", "daily", "weekly", "none")


def _safe_next(target):
    """Only follow relative redirects, so ?next= can't send users off-site."""
    if not target:
        return None
    parsed = urlparse(target)
    if parsed.netloc or parsed.scheme or not target.startswith("/"):
        return None
    return target


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter(db.func.lower(User.email) == email).first()

        if user is None or not user.check_password(password):
            flash("That email and password combination didn't work.", "error")
            return render_template("auth/login.html", email=email), 401
        if not user.active:
            flash("This account has been deactivated. Contact your administrator.", "error")
            return render_template("auth/login.html", email=email), 403

        login_user(user, remember=bool(request.form.get("remember")))
        user.last_login_at = datetime.utcnow()
        db.session.commit()
        flash(f"Welcome back, {user.known_as}.", "success")
        return redirect(_safe_next(request.args.get("next")) or url_for("main.home"))

    return render_template("auth/login.html", email="")


@bp.route("/set-password", methods=["GET", "POST"])
@login_required
def set_password():
    """Forced on first sign-in for imported and admin-created accounts."""
    if not current_user.must_change_password:
        return redirect(url_for("main.home"))

    if request.method == "POST":
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if len(new) < 8:
            flash("Your new password must be at least 8 characters.", "error")
        elif new != confirm:
            flash("The two passwords don't match.", "error")
        elif current_user.check_password(new):
            flash("Pick something different from the password you were given.", "error")
        else:
            current_user.set_password(new)
            current_user.must_change_password = False
            db.session.commit()
            flash("Password set. You're all done.", "success")
            return redirect(url_for("main.home"))

    return render_template("auth/set_password.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You've been signed out.", "success")
    return redirect(url_for("public.home"))


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    section = request.args.get("section", "profile")

    if request.method == "POST":
        action = request.form.get("action", "profile")

        if action == "password":
            current = request.form.get("current_password", "")
            new = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")
            if not current_user.check_password(current):
                flash("Your current password is incorrect.", "error")
            elif len(new) < 8:
                flash("New password must be at least 8 characters.", "error")
            elif new == current:
                flash("The new password must differ from the current one.", "error")
            elif new != confirm:
                flash("The new passwords don't match.", "error")
            else:
                current_user.set_password(new)
                db.session.commit()
                flash("Password updated.", "success")
            return redirect(url_for("auth.account", section="security"))

        if action == "notifications":
            current_user.notify_grades = request.form.get("notify_grades") == "on"
            current_user.notify_attendance = request.form.get("notify_attendance") == "on"
            current_user.notify_assignments = request.form.get("notify_assignments") == "on"
            current_user.notify_announcements = (
                request.form.get("notify_announcements") == "on"
            )
            digest = request.form.get("notify_digest", "daily")
            if digest in DIGEST_CHOICES:
                current_user.notify_digest = digest
            db.session.commit()
            flash("Notification preferences saved.", "success")
            return redirect(url_for("auth.account", section="notifications"))

        # Default: profile + contact details.
        current_user.preferred_name = request.form.get("preferred_name", "").strip()[:80]
        current_user.pronouns = request.form.get("pronouns", "").strip()[:40]
        current_user.phone = request.form.get("phone", "").strip()[:40]
        current_user.address = request.form.get("address", "").strip()[:200]
        current_user.emergency_contact_name = (
            request.form.get("emergency_contact_name", "").strip()[:120]
        )
        current_user.emergency_contact_phone = (
            request.form.get("emergency_contact_phone", "").strip()[:40]
        )
        current_user.emergency_contact_relation = (
            request.form.get("emergency_contact_relation", "").strip()[:60]
        )
        birthdate = request.form.get("birthdate", "")
        if birthdate:
            try:
                current_user.birthdate = datetime.strptime(birthdate, "%Y-%m-%d").date()
            except ValueError:
                flash("That birthdate wasn't a valid date.", "warning")
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("auth.account", section="profile"))

    return render_template(
        "auth/account.html",
        section=section,
        digest_choices=DIGEST_CHOICES,
        schools=switchable_schools(),
        guardians=[link for link in current_user.guardian_links],
        children=[link for link in current_user.parent_links],
    )
