"""Support blocks — the flex period where students pick a teacher to work with.

Teachers publish what they're offering for a given date. Students choose one, unless an
administrator has placed them somewhere, in which case the placement is locked.
"""

from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Period, SupportSession, SupportSignup, User
from ..security import active_school, may_view_student, school_admin_required
from ..services import support_periods, support_signup_for

bp = Blueprint("support", __name__, url_prefix="/app/support")


def _parse_day(raw):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return date.today()


def _sessions_for(day, period, school):
    return (
        SupportSession.query.filter_by(session_date=day, period_id=period.id,
                                       school_id=school.id)
        .order_by(SupportSession.name)
        .all()
    )


@bp.route("/")
@login_required
def index():
    if current_user.is_student:
        return redirect(url_for("support.choose"))
    if current_user.is_teacher:
        return redirect(url_for("support.my_sessions"))
    return redirect(url_for("support.overview"))


# ------------------------------------------------------------------------- students


@bp.route("/choose", methods=["GET", "POST"])
@login_required
def choose():
    if not current_user.is_student:
        abort(403)

    school = active_school()
    blocks = support_periods(school)
    if not blocks:
        return render_template("app/support_none.html")

    day = _parse_day(request.args.get("date") or request.form.get("date"))
    period = blocks[0]
    period_id = request.args.get("period", type=int) or request.form.get("period_id", type=int)
    if period_id:
        chosen = db.session.get(Period, period_id)
        if chosen and chosen.is_support and chosen.school_id == school.id:
            period = chosen

    existing = support_signup_for(current_user, day, period)

    if request.method == "POST":
        if existing and existing.locked:
            flash(
                "An administrator placed you in this session, so it can't be changed. "
                "Talk to the office if you think that's wrong.",
                "error",
            )
            return redirect(url_for("support.choose", date=day.isoformat(),
                                    period=period.id))

        session_id = request.form.get("session_id", type=int)
        target = db.session.get(SupportSession, session_id) if session_id else None
        if target is None or target.session_date != day or target.period_id != period.id:
            flash("Pick one of the sessions offered for this block.", "error")
        elif target.is_full and not (existing and existing.session_id == target.id):
            flash(f"“{target.name}” is full.", "error")
        else:
            if existing:
                db.session.delete(existing)
                db.session.flush()
            db.session.add(SupportSignup(session_id=target.id, student_id=current_user.id))
            db.session.commit()
            flash(f"You're signed up for “{target.name}”.", "success")
        return redirect(url_for("support.choose", date=day.isoformat(), period=period.id))

    return render_template(
        "app/support_choose.html",
        day=day,
        period=period,
        blocks=blocks,
        sessions=_sessions_for(day, period, school),
        signup=existing,
        prev_day=day - timedelta(days=1),
        next_day=day + timedelta(days=1),
    )


@bp.route("/cancel/<int:signup_id>", methods=["POST"])
@login_required
def cancel(signup_id):
    signup = db.session.get(SupportSignup, signup_id)
    if signup is None:
        abort(404)
    if signup.student_id != current_user.id:
        abort(403)
    if signup.locked:
        flash("This placement was assigned by an administrator and can't be dropped.",
              "error")
        return redirect(url_for("support.choose"))

    day = signup.session.session_date
    db.session.delete(signup)
    db.session.commit()
    flash("Signup cancelled.", "success")
    return redirect(url_for("support.choose", date=day.isoformat()))


# ------------------------------------------------------------------------- teachers


@bp.route("/mine")
@login_required
def my_sessions():
    if not current_user.is_teacher:
        abort(403)

    day = _parse_day(request.args.get("date"))
    sessions = (
        SupportSession.query.filter_by(teacher_id=current_user.id, session_date=day)
        .order_by(SupportSession.name).all()
    )
    upcoming = (
        SupportSession.query.filter(
            SupportSession.teacher_id == current_user.id,
            SupportSession.session_date > day,
        ).order_by(SupportSession.session_date).limit(10).all()
    )
    return render_template(
        "app/support_teacher.html",
        day=day,
        sessions=sessions,
        upcoming=upcoming,
        blocks=support_periods(),
        prev_day=day - timedelta(days=1),
        next_day=day + timedelta(days=1),
    )


@bp.route("/offer", methods=["POST"])
@login_required
def offer():
    """Publish what this teacher is running for a support block."""
    if not current_user.is_teacher:
        abort(403)

    school = active_school()
    day = _parse_day(request.form.get("session_date"))
    period = db.session.get(Period, request.form.get("period_id", type=int))
    name = request.form.get("name", "").strip()

    try:
        capacity = int(request.form.get("capacity") or 25)
    except ValueError:
        capacity = -1

    if period is None or not period.is_support:
        flash("Choose a support block.", "error")
    elif not name:
        flash("Give the session a name so students know what it is.", "error")
    elif capacity < 1:
        flash("Capacity must be at least 1.", "error")
    else:
        db.session.add(SupportSession(
            school_id=school.id,
            teacher_id=current_user.id,
            period_id=period.id,
            session_date=day,
            name=name,
            description=request.form.get("description", "").strip(),
            kind="taught" if request.form.get("kind") == "taught" else "work",
            capacity=capacity,
            location=request.form.get("location", "").strip(),
        ))
        db.session.commit()
        flash(f"Published “{name}” for {day.isoformat()}.", "success")

    return redirect(url_for("support.my_sessions", date=day.isoformat()))


@bp.route("/session/<int:session_id>")
@login_required
def session_detail(session_id):
    session_row = db.session.get(SupportSession, session_id)
    if session_row is None:
        abort(404)

    is_owner = current_user.is_teacher and session_row.teacher_id == current_user.id
    is_admin = current_user.is_admin or current_user.is_district_admin
    if not (is_owner or is_admin):
        abort(403)

    roster = sorted(session_row.signups,
                    key=lambda s: (s.student.last_name, s.student.first_name))
    return render_template("app/support_session.html", session=session_row,
                           roster=roster, can_manage=is_owner or is_admin)


@bp.route("/session/<int:session_id>/delete", methods=["POST"])
@login_required
def delete_session(session_id):
    session_row = db.session.get(SupportSession, session_id)
    if session_row is None:
        abort(404)
    is_owner = current_user.is_teacher and session_row.teacher_id == current_user.id
    if not (is_owner or current_user.is_admin):
        abort(403)

    day = session_row.session_date
    locked = [s for s in session_row.signups if s.locked]
    if locked and not current_user.is_admin:
        flash(
            f"{len(locked)} student(s) were placed here by an administrator. "
            "Ask the office to move them before removing this session.",
            "error",
        )
        return redirect(url_for("support.session_detail", session_id=session_row.id))

    name = session_row.name
    db.session.delete(session_row)
    db.session.commit()
    flash(f"Removed “{name}”.", "success")
    return redirect(url_for("support.my_sessions", date=day.isoformat()))


# --------------------------------------------------------------------------- admins


@bp.route("/overview")
@school_admin_required
def overview():
    school = active_school()
    day = _parse_day(request.args.get("date"))
    blocks = support_periods(school)

    period = blocks[0] if blocks else None
    period_id = request.args.get("period", type=int)
    if period_id:
        chosen = db.session.get(Period, period_id)
        if chosen and chosen.is_support:
            period = chosen

    sessions = _sessions_for(day, period, school) if period else []
    placed = {s.student_id for row in sessions for s in row.signups}
    unassigned = [
        student for student in
        User.query.filter_by(role="student", school_id=school.id, active=True)
        .order_by(User.last_name, User.first_name).all()
        if student.id not in placed
    ]

    return render_template(
        "app/support_overview.html",
        day=day,
        period=period,
        blocks=blocks,
        sessions=sessions,
        unassigned=unassigned,
        prev_day=day - timedelta(days=1),
        next_day=day + timedelta(days=1),
        total_placed=len(placed),
    )


@bp.route("/assign", methods=["POST"])
@school_admin_required
def assign():
    """Place a student into a session and lock them there."""
    session_row = db.session.get(SupportSession, request.form.get("session_id", type=int))
    student = db.session.get(User, request.form.get("student_id", type=int))

    if session_row is None or student is None or not student.is_student:
        flash("Pick both a student and a session.", "error")
        return redirect(request.referrer or url_for("support.overview"))
    if not may_view_student(student):
        abort(403)

    day = session_row.session_date
    # One placement per student per block — drop any existing choice first.
    existing = (
        SupportSignup.query.join(SupportSession)
        .filter(SupportSignup.student_id == student.id,
                SupportSession.session_date == day,
                SupportSession.period_id == session_row.period_id)
        .first()
    )
    if existing:
        db.session.delete(existing)
        db.session.flush()

    db.session.add(SupportSignup(
        session_id=session_row.id,
        student_id=student.id,
        locked=True,
        assigned_by_id=current_user.id,
        note=request.form.get("note", "")[:255],
    ))
    db.session.commit()
    flash(
        f"{student.full_name} is assigned to “{session_row.name}”. "
        "They can't move themselves out.",
        "success",
    )
    return redirect(request.referrer or url_for("support.overview", date=day.isoformat()))


@bp.route("/signup/<int:signup_id>/unlock", methods=["POST"])
@school_admin_required
def unlock(signup_id):
    signup = db.session.get(SupportSignup, signup_id)
    if signup is None:
        abort(404)
    signup.locked = not signup.locked
    db.session.commit()
    flash(
        f"{signup.student.full_name} is now "
        f"{'locked into' if signup.locked else 'free to change'} this session.",
        "success",
    )
    return redirect(request.referrer or url_for("support.overview"))


@bp.route("/signup/<int:signup_id>/remove", methods=["POST"])
@school_admin_required
def remove_signup(signup_id):
    signup = db.session.get(SupportSignup, signup_id)
    if signup is None:
        abort(404)
    name = signup.student.full_name
    db.session.delete(signup)
    db.session.commit()
    flash(f"Removed {name} from the session.", "success")
    return redirect(request.referrer or url_for("support.overview"))
