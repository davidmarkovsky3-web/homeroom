"""Attendance: student view, teacher period entry, admin overview."""

from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from ..extensions import db
from ..models import ATTENDANCE_STATUSES, AttendanceRecord, Course, User
from ..security import active_school, require_student_access, school_admin_required
from ..services import (
    attendance_summary,
    courses_for,
    current_term,
    day_type_for,
    in_session,
    student_risk,
)

bp = Blueprint("attendance", __name__, url_prefix="/app/attendance")


def _parse_day(raw):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return date.today()


@bp.route("/")
@login_required
def index():
    if current_user.is_student:
        return redirect(url_for("attendance.my_attendance"))
    if current_user.is_parent:
        return redirect(url_for("parents.index"))
    if current_user.is_teacher:
        return redirect(url_for("attendance.take"))
    return redirect(url_for("attendance.overview"))


@bp.route("/me")
@login_required
def my_attendance():
    if not current_user.is_student:
        return redirect(url_for("attendance.index"))
    return render_template("app/attendance_student.html",
                           summary=attendance_summary(current_user, current_term()),
                           term=current_term(), student=current_user)


@bp.route("/take", methods=["GET", "POST"])
@login_required
def take():
    if not (current_user.is_teacher or current_user.is_admin):
        abort(403)

    school = active_school()
    day = _parse_day(request.args.get("date") or request.form.get("date"))

    if current_user.is_teacher:
        my_courses = courses_for(current_user, day=day)
    else:
        my_courses = (
            Course.query.filter_by(school_id=school.id if school else None)
            .order_by(Course.name).all()
        )

    course_id = request.args.get("course", type=int) or request.form.get("course_id", type=int)
    course = db.session.get(Course, course_id) if course_id else (
        my_courses[0] if my_courses else None
    )

    if course:
        if current_user.is_teacher and course.teacher_id != current_user.id:
            abort(403)
        if school and course.school_id != school.id:
            abort(403)

    if request.method == "POST":
        if course is None:
            abort(400)
        saved = 0
        for enrollment in course.enrollments:
            status = request.form.get(f"status_{enrollment.student_id}")
            if status not in ATTENDANCE_STATUSES:
                continue
            record = AttendanceRecord.query.filter_by(
                student_id=enrollment.student_id, course_id=course.id, record_date=day
            ).first()
            if record is None:
                record = AttendanceRecord(student_id=enrollment.student_id,
                                          course_id=course.id, record_date=day)
                db.session.add(record)
            record.status = status
            record.note = request.form.get(f"note_{enrollment.student_id}", "")[:255]
            record.recorded_by_id = current_user.id
            record.recorded_at = datetime.utcnow()
            saved += 1
        db.session.commit()
        flash(f"Attendance saved for {saved} students in {course.code}.", "success")
        return redirect(url_for("attendance.take", course=course.id, date=day.isoformat()))

    roster = []
    if course:
        existing = {
            r.student_id: r
            for r in AttendanceRecord.query.filter_by(course_id=course.id,
                                                      record_date=day).all()
        }
        for student in course.students:
            record = existing.get(student.id)
            roster.append({
                "student": student,
                "status": record.status if record else "present",
                "note": record.note if record else "",
                "recorded": record is not None,
            })

    return render_template(
        "app/attendance_take.html",
        day=day, day_type=day_type_for(day, school),
        day_label=school.day_label(day_type_for(day, school)) if school else "",
        my_courses=my_courses, course=course, roster=roster,
        statuses=ATTENDANCE_STATUSES,
        prev_day=day - timedelta(days=1), next_day=day + timedelta(days=1),
        already_taken=any(r["recorded"] for r in roster),
        open_today=in_session(day, school),
    )


@bp.route("/overview")
@school_admin_required
def overview():
    school = active_school()
    day = _parse_day(request.args.get("date"))
    school_id = school.id if school else None

    rows = (
        db.session.query(AttendanceRecord.status, func.count(AttendanceRecord.id))
        .join(Course, AttendanceRecord.course_id == Course.id)
        .filter(AttendanceRecord.record_date == day, Course.school_id == school_id)
        .group_by(AttendanceRecord.status).all()
    )
    counts = {status: 0 for status in ATTENDANCE_STATUSES}
    counts.update(dict(rows))
    total = sum(counts.values())
    rate = round((counts["present"] + counts["excused"]) / total * 100, 1) if total else None

    absent_today = (
        AttendanceRecord.query.join(Course)
        .filter(AttendanceRecord.record_date == day,
                Course.school_id == school_id,
                AttendanceRecord.status.in_(("absent", "tardy")))
        .order_by(AttendanceRecord.status).all()
    )

    dtype = day_type_for(day, school)
    scheduled = [
        c for c in Course.query.filter_by(school_id=school_id).all()
        if in_session(day, school) and c.meets_on(dtype, weekday=day.weekday())
    ]
    submitted_ids = {
        row[0] for row in db.session.query(AttendanceRecord.course_id)
        .filter(AttendanceRecord.record_date == day).distinct()
    }
    missing = [c for c in scheduled if c.id not in submitted_ids and c.enrollments]

    term = current_term(school)
    chronic_query = (
        db.session.query(User, func.count(AttendanceRecord.id))
        .join(AttendanceRecord, AttendanceRecord.student_id == User.id)
        .filter(AttendanceRecord.status == "absent", User.school_id == school_id)
    )
    if term:
        chronic_query = chronic_query.filter(
            AttendanceRecord.record_date >= term.start_date,
            AttendanceRecord.record_date <= term.end_date)
    chronic = (
        chronic_query.group_by(User.id)
        .order_by(func.count(AttendanceRecord.id).desc()).limit(10).all()
    )

    return render_template(
        "app/attendance_overview.html",
        day=day, day_type=dtype,
        day_label=school.day_label(dtype) if school else "",
        counts=counts, total=total, rate=rate, absent_today=absent_today,
        missing=missing, chronic=chronic,
        prev_day=day - timedelta(days=1), next_day=day + timedelta(days=1),
        term=term, open_today=in_session(day, school),
    )


@bp.route("/student/<int:student_id>")
@login_required
def student_detail(student_id):
    student = db.session.get(User, student_id)
    if student is None or not student.is_student:
        abort(404)
    require_student_access(student)

    return render_template(
        "app/attendance_student.html",
        summary=attendance_summary(student, current_term(student.school)),
        term=current_term(student.school), student=student, staff_view=True,
        risk=student_risk(student),
    )
