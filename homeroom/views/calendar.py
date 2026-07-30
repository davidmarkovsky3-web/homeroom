"""School calendar: month grid, day detail, and staff event management."""

from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import CalendarEvent, Course, SchoolDay
from ..security import active_school
from ..services import courses_for, day_type_for, events_between, in_session, month_grid

bp = Blueprint("calendar", __name__, url_prefix="/app/calendar")

CATEGORIES = ["activity", "assembly", "exam", "holiday", "deadline", "sports"]


def _may_manage_events():
    return not (current_user.is_student or current_user.is_parent)


@bp.route("/")
@login_required
def index():
    school = active_school()
    today = date.today()
    try:
        anchor = date(int(request.args.get("year", today.year)),
                      int(request.args.get("month", today.month)), 1)
    except (ValueError, TypeError):
        anchor = date(today.year, today.month, 1)

    weeks = month_grid(anchor.year, anchor.month)
    span_start, span_end = weeks[0][0], weeks[-1][-1]

    by_day = {}
    for event in events_between(span_start, span_end, current_user, school):
        by_day.setdefault(event.event_date, []).append(event)

    day_query = SchoolDay.query.filter(SchoolDay.day_date >= span_start,
                                       SchoolDay.day_date <= span_end)
    if school:
        day_query = day_query.filter(SchoolDay.school_id == school.id)
    day_types = {row.day_date: row for row in day_query.all()}

    return render_template(
        "app/calendar.html",
        anchor=anchor, weeks=weeks, events_by_day=by_day, day_types=day_types,
        prev_month=(anchor - timedelta(days=1)).replace(day=1),
        next_month=(anchor + timedelta(days=32)).replace(day=1),
        today=today, categories=CATEGORIES, school=school,
        can_manage=_may_manage_events(),
    )


@bp.route("/day/<day>")
@login_required
def day_detail(day):
    try:
        target = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        abort(404)

    school = active_school()
    row = SchoolDay.query.filter_by(day_date=target,
                                    school_id=school.id if school else None).first()
    return render_template(
        "app/calendar_day.html",
        day=target,
        events=events_between(target, target, current_user, school),
        day_type=day_type_for(target, school),
        day_label=school.day_label(day_type_for(target, school)) if school else "",
        school_day=row, school=school,
        open_today=in_session(target, school),
        prev_day=target - timedelta(days=1), next_day=target + timedelta(days=1),
        can_manage=_may_manage_events(),
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new_event():
    if not _may_manage_events():
        abort(403)

    school = active_school()
    if school is None:
        abort(403)

    my_courses = (
        courses_for(current_user) if current_user.is_teacher
        else Course.query.filter_by(school_id=school.id).order_by(Course.name).all()
    )

    if request.method == "POST":
        form = request.form
        title = form.get("title", "").strip()
        try:
            event_date = datetime.strptime(form.get("event_date", ""), "%Y-%m-%d").date()
        except ValueError:
            event_date = None

        if not title or event_date is None:
            flash("A title and a valid date are required.", "error")
            return render_template("app/calendar_form.html", categories=CATEGORIES,
                                   my_courses=my_courses, school=school, form=form), 400

        all_day = form.get("all_day") == "on"
        start_time = end_time = None
        if not all_day:
            for field, name in ((form.get("start_time", ""), "start"),
                                (form.get("end_time", ""), "end")):
                try:
                    parsed = datetime.strptime(field, "%H:%M").time()
                except ValueError:
                    parsed = None
                if name == "start":
                    start_time = parsed
                else:
                    end_time = parsed

        event = CalendarEvent(
            school_id=school.id,
            title=title,
            description=form.get("description", "").strip(),
            event_date=event_date, all_day=all_day,
            start_time=start_time, end_time=end_time,
            category=form.get("category", "activity"),
            course_id=form.get("course_id", type=int) or None,
            grade_level=form.get("grade_level", type=int) or None,
            created_by_id=current_user.id,
        )
        db.session.add(event)
        db.session.commit()
        flash(f"Added “{event.title}” to the calendar.", "success")
        return redirect(url_for("calendar.day_detail", day=event_date.isoformat()))

    return render_template(
        "app/calendar_form.html", categories=CATEGORIES, my_courses=my_courses,
        school=school,
        form={"event_date": request.args.get("date", date.today().isoformat()),
              "all_day": "on"},
    )


@bp.route("/<int:event_id>/delete", methods=["POST"])
@login_required
def delete_event(event_id):
    if not _may_manage_events():
        abort(403)
    event = db.session.get(CalendarEvent, event_id)
    if event is None:
        abort(404)
    if current_user.is_teacher and event.created_by_id != current_user.id:
        abort(403)

    day = event.event_date
    db.session.delete(event)
    db.session.commit()
    flash("Event removed.", "success")
    return redirect(url_for("calendar.day_detail", day=day.isoformat()))
