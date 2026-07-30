"""Home dashboards, week schedule, and the responsive 'today' schedule."""

from datetime import date, datetime, timedelta

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import (
    Assignment,
    AttendanceRecord,
    Course,
    CourseRequest,
    Grade,
    SupportSession,
    User,
    subject_for_department,
)
from ..security import active_school
from ..services import (
    active_window,
    admin_day_overview,
    build_day_schedule,
    course_average,
    course_risk_list,
    courses_for,
    current_and_next,
    current_term,
    day_type_for,
    events_between,
    in_session,
    minutes_until,
    notification_feed,
    periods,
    school_assessment_summary,
    school_attendance_rate,
    student_courses,
    student_grade_report,
    support_periods,
    support_signup_for,
    upcoming_assignments,
)

bp = Blueprint("main", __name__)


def _parse_day(raw, fallback=None):
    fallback = fallback or date.today()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return fallback


@bp.route("/app")
@login_required
def home():
    if current_user.is_parent:
        return redirect(url_for("parents.index"))
    if current_user.is_homeroom_staff:
        return redirect(url_for("staff.index"))
    if current_user.is_district_admin:
        return redirect(url_for("district.index"))

    today = date.today()
    school = active_school()
    context = {
        "today": today,
        "day_type": day_type_for(today, school),
        "day_label": school.day_label(day_type_for(today, school)) if school else "",
        "upcoming_events": events_between(today, today + timedelta(days=21), current_user)[:8],
        "term": current_term(school),
        "notices": notification_feed(current_user, school, limit=5),
        "notice_total": len(notification_feed(current_user, school)),
    }

    if current_user.is_student:
        current_row, next_row, schedule = current_and_next(current_user)
        report = student_grade_report(current_user)
        window = active_window(school)
        blocks = support_periods(school)
        support_today = support_signup_for(current_user, today) if blocks else None

        context.update(
            current_row=current_row, next_row=next_row, schedule=schedule,
            report=report,
            recent_attendance=(
                AttendanceRecord.query.filter_by(student_id=current_user.id)
                .order_by(AttendanceRecord.record_date.desc()).limit(5).all()
            ),
            absences=AttendanceRecord.query.filter_by(
                student_id=current_user.id, status="absent").count(),
            tardies=AttendanceRecord.query.filter_by(
                student_id=current_user.id, status="tardy").count(),
            window=window,
            my_request_count=(
                CourseRequest.query.filter_by(
                    student_id=current_user.id, window_id=window.id).count()
                if window else 0
            ),
            course_count=len(student_courses(current_user)),
            upcoming_work=upcoming_assignments(current_user)[:6],
            support_blocks=blocks,
            support_today=support_today,
        )
        return render_template("app/home_student.html", **context)

    if current_user.is_teacher:
        current_row, next_row, schedule = current_and_next(current_user)
        my_courses = courses_for(current_user)
        today_courses = courses_for(current_user, day=today)

        needs_help = []
        for course in my_courses:
            for entry in course_risk_list(course):
                if entry["risk"]["level"] == "high":
                    needs_help.append({"course": course, **entry})
        needs_help.sort(key=lambda e: -e["risk"]["score"])

        ungraded = (
            Grade.query.join(Assignment)
            .filter(Assignment.course_id.in_([c.id for c in my_courses] or [0]),
                    Grade.status == "ungraded")
            .count()
        )

        context.update(
            current_row=current_row, next_row=next_row, schedule=schedule,
            my_courses=my_courses, today_courses=today_courses,
            roster_total=sum(c.seats_taken for c in my_courses),
            attendance_taken_today=AttendanceRecord.query.filter(
                AttendanceRecord.recorded_by_id == current_user.id,
                AttendanceRecord.record_date == today).count(),
            averages=[(c, course_average(c)) for c in my_courses],
            needs_help=needs_help[:8],
            subject_label=subject_for_department(current_user.department),
            ungraded=ungraded,
            my_support=SupportSession.query.filter_by(
                teacher_id=current_user.id, session_date=today).all(),
        )
        return render_template("app/home_teacher.html", **context)

    # School administrator
    school_id = school.id if school else None
    context.update(
        student_count=User.query.filter_by(
            role="student", active=True, school_id=school_id).count(),
        teacher_count=User.query.filter_by(
            role="teacher", active=True, school_id=school_id).count(),
        admin_count=User.query.filter_by(
            role="admin", active=True, school_id=school_id).count(),
        parent_count=User.query.filter_by(
            role="parent", active=True, school_id=school_id).count(),
        attendance_rate=school_attendance_rate(today, school),
        pending_requests=(
            CourseRequest.query.join(Course)
            .filter(Course.school_id == school_id,
                    CourseRequest.status == "pending").count()
            if school_id else 0
        ),
        window=active_window(school),
        overview=admin_day_overview(today, school),
        assessments=school_assessment_summary(school)[:4],
        course_count=Course.query.filter_by(school_id=school_id).count(),
    )
    return render_template("app/home_admin.html", **context)


@bp.route("/app/schedule")
@login_required
def schedule():
    """Full week grid."""
    if current_user.is_parent:
        return redirect(url_for("parents.index"))

    school = active_school()
    anchor = _parse_day(request.args.get("week"))
    monday = anchor - timedelta(days=anchor.weekday())
    days = [monday + timedelta(days=i) for i in range(5)]

    # Days can run different layouts, so the grid's rows are the union of every slot
    # appearing that week, ordered canonically. A cell is blank when its slot doesn't
    # run that day at all.
    day_views = [build_day_schedule(current_user, day, school) for day in days] \
        if not (current_user.is_admin or current_user.is_district_admin) else None

    if day_views is None:
        week = [admin_day_overview(day, school) for day in days]
        slots, seen = [], set()
        for view in week:
            for row in view["rows"]:
                if row["period"].id not in seen:
                    seen.add(row["period"].id)
                    slots.append(row["period"])
        slots.sort(key=lambda p: p.ordinal)
        by_day = [{row["period"].id: row for row in view["rows"]} for view in week]

        return render_template(
            "app/schedule_admin.html",
            week=week, by_day=by_day, slots=slots, days=days, monday=monday,
            prev_week=monday - timedelta(days=7),
            next_week=monday + timedelta(days=7),
            term=current_term(school), school=school,
            layouts=[v["layout_name"] for v in week],
        )

    slots, seen = [], set()
    for view in day_views:
        for row in view["rows"]:
            if row["period"].id not in seen:
                seen.add(row["period"].id)
                slots.append(row["period"])
    slots.sort(key=lambda p: p.ordinal)
    by_day = [{row["period"].id: row for row in view["rows"]} for view in day_views]

    grid = []
    for slot in slots:
        row = {"period": slot, "cells": []}
        for index, day in enumerate(days):
            entry = by_day[index].get(slot.id)
            row["cells"].append({
                "date": day,
                "runs": entry is not None,
                "course": entry["course"] if entry else None,
                "support": entry["support"] if entry else None,
                "time_range": entry["time_range"] if entry else None,
                "open": day_views[index]["in_session"],
            })
        grid.append(row)

    return render_template(
        "app/schedule.html",
        grid=grid, days=days, monday=monday,
        prev_week=monday - timedelta(days=7),
        next_week=monday + timedelta(days=7),
        all_courses=courses_for(current_user),
        term=current_term(school), school=school,
        day_labels={d: (school.day_label(day_type_for(d, school)) if school else "")
                    for d in days},
        layouts=[v["layout_name"] for v in day_views],
    )


@bp.route("/app/today")
@login_required
def responsive_schedule():
    """Mobile-first single-day view."""
    if current_user.is_parent:
        return redirect(url_for("parents.index"))

    school = active_school()
    day = _parse_day(request.args.get("date"))

    if current_user.is_admin or current_user.is_district_admin:
        return render_template(
            "app/today_admin.html",
            overview=admin_day_overview(day, school), day=day,
            prev_day=day - timedelta(days=1), next_day=day + timedelta(days=1),
            events=events_between(day, day, current_user), school=school,
        )

    schedule_data = build_day_schedule(current_user, day)
    current_row, next_row, _ = current_and_next(current_user)

    countdown = None
    if day == date.today():
        if current_row:
            countdown = {"label": "left in this period",
                         "minutes": minutes_until(current_row["end_time"])}
        elif next_row:
            countdown = {"label": f"until {next_row['period'].label}",
                         "minutes": minutes_until(next_row["start_time"])}

    return render_template(
        "app/responsive_schedule.html",
        schedule=schedule_data, day=day,
        prev_day=day - timedelta(days=1), next_day=day + timedelta(days=1),
        current_row=current_row if day == date.today() else None,
        next_row=next_row if day == date.today() else None,
        countdown=countdown,
        events=events_between(day, day, current_user),
        school=school,
    )


@bp.route("/app/api/now")
@login_required
def api_now():
    if current_user.is_parent or not current_user.is_school_user:
        abort(403)

    school = active_school()
    current_row, next_row, _ = current_and_next(current_user)

    def pack(row):
        if not row:
            return None
        course = row["course"]
        support = row["support"]
        title = room = teacher = None
        if course:
            title, room = course.name, course.room
            teacher = course.teacher.full_name if course.teacher else None
        elif support is not None:
            session_row = getattr(support, "session", support)
            title, room = session_row.name, session_row.location
            teacher = session_row.teacher.full_name if session_row.teacher else None
        return {
            "period": row["period"].label,
            "starts": row["start_time"].strftime("%I:%M %p").lstrip("0"),
            "ends": row["end_time"].strftime("%I:%M %p").lstrip("0"),
            "course": title, "room": room, "teacher": teacher,
        }

    today = date.today()
    return jsonify({
        "server_time": datetime.now().strftime("%I:%M %p").lstrip("0"),
        "day_type": day_type_for(today, school),
        "day_label": school.day_label(day_type_for(today, school)) if school else "",
        "current": pack(current_row),
        "next": pack(next_row),
        "minutes_left": minutes_until(current_row["end_time"]) if current_row else None,
        "minutes_until_next": (
            minutes_until(next_row["start_time"]) if next_row else None
        ),
    })
