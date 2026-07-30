"""Parent / guardian portal — everything is scoped to the linked children."""

from datetime import date, timedelta

from flask import Blueprint, abort, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import ParentLink, User
from ..security import parent_link_for, parent_required
from ..services import (
    attendance_summary,
    build_day_schedule,
    current_term,
    events_between,
    student_grade_report,
    student_risk,
    upcoming_assignments,
)

bp = Blueprint("parents", __name__, url_prefix="/app/family")


def _child_or_404(student_id):
    student = db.session.get(User, student_id)
    if student is None or not student.is_student:
        abort(404)
    link = parent_link_for(student)
    if link is None:
        abort(403)
    return student, link


@bp.route("/")
@parent_required
def index():
    today = date.today()
    children = []
    for link in current_user.parent_links:
        student = link.student
        report = student_grade_report(student) if link.can_view_grades else None
        attendance = attendance_summary(student) if link.can_view_attendance else None
        children.append({
            "student": student,
            "link": link,
            "report": report,
            "attendance": attendance,
            "risk": student_risk(student),
            "schedule": build_day_schedule(student, today, school=student.school),
            "upcoming": upcoming_assignments(student)[:5],
        })

    return render_template("app/parent_home.html", children=children, today=today)


@bp.route("/student/<int:student_id>")
@parent_required
def child(student_id):
    student, link = _child_or_404(student_id)
    today = date.today()

    return render_template(
        "app/parent_child.html",
        student=student,
        link=link,
        term=current_term(student.school),
        report=student_grade_report(student) if link.can_view_grades else None,
        attendance=attendance_summary(student) if link.can_view_attendance else None,
        risk=student_risk(student),
        schedule=build_day_schedule(student, today, school=student.school),
        upcoming=upcoming_assignments(student),
        events=events_between(today, today + timedelta(days=21), student,
                              school=student.school)[:8],
    )
