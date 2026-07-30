"""Grades: student report card, teacher gradebook, and test-score analytics."""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Assessment, Course, Grade, User
from ..security import may_view_student, require_student_access
from ..services import (
    course_assessment_summary,
    course_average,
    course_gradebook,
    course_grade,
    course_risk_list,
    courses_for,
    current_term,
    school_assessment_summary,
    student_grade_report,
    student_risk,
)

bp = Blueprint("grades", __name__, url_prefix="/app/grades")


@bp.route("/")
@login_required
def index():
    if current_user.is_student:
        return redirect(url_for("grades.report"))
    if current_user.is_parent:
        return redirect(url_for("parents.index"))
    if current_user.is_teacher:
        return redirect(url_for("grades.gradebook"))
    if current_user.is_admin or current_user.is_district_admin:
        return redirect(url_for("grades.analytics"))
    abort(403)


# ------------------------------------------------------------------------- students


@bp.route("/report")
@login_required
def report():
    student = current_user
    if not current_user.is_student:
        student_id = request.args.get("student", type=int)
        student = db.session.get(User, student_id) if student_id else None
        require_student_access(student)

    report_data = student_grade_report(student)
    return render_template(
        "app/grades_student.html",
        student=student,
        report=report_data,
        term=current_term(),
        risk=student_risk(student) if not current_user.is_student else None,
        staff_view=not current_user.is_student,
    )


@bp.route("/course/<int:course_id>")
@login_required
def course_detail(course_id):
    """One student's assignment-by-assignment breakdown in a course."""
    course = db.session.get(Course, course_id)
    if course is None:
        abort(404)

    student = current_user
    if not current_user.is_student:
        student_id = request.args.get("student", type=int)
        student = db.session.get(User, student_id) if student_id else None
        require_student_access(student)

    if course.id not in {c.id for c in courses_for(student)}:
        abort(404)

    grades = sorted(
        Grade.query.join(Grade.assignment)
        .filter(Grade.student_id == student.id)
        .filter_by(course_id=course.id)
        .all(),
        key=lambda g: (g.assignment.due_on is None, g.assignment.due_on or g.assignment.id),
    )

    return render_template(
        "app/grades_course.html",
        course=course,
        student=student,
        grades=grades,
        summary=course_grade(student, course),
        staff_view=not current_user.is_student,
    )


# ------------------------------------------------------------------------- teachers


@bp.route("/gradebook")
@login_required
def gradebook():
    if not (current_user.is_teacher or current_user.is_admin):
        abort(403)

    if current_user.is_teacher:
        my_courses = courses_for(current_user)
    else:
        my_courses = (
            Course.query.filter_by(school_id=current_user.school_id)
            .order_by(Course.name).all()
        )

    course_id = request.args.get("course", type=int)
    course = db.session.get(Course, course_id) if course_id else (
        my_courses[0] if my_courses else None
    )
    if course and current_user.is_teacher and course.teacher_id != current_user.id:
        abort(403)

    book = course_gradebook(course) if course else {"assignments": [], "rows": []}
    return render_template(
        "app/gradebook.html",
        my_courses=my_courses,
        course=course,
        book=book,
        average=course_average(course) if course else None,
        assessments=course_assessment_summary(course) if course else [],
        at_risk=course_risk_list(course) if course else [],
    )


@bp.route("/gradebook/<int:course_id>/quick", methods=["POST"])
@login_required
def quick_grade(course_id):
    """Inline edits from the gradebook matrix."""
    course = db.session.get(Course, course_id)
    if course is None:
        abort(404)
    if current_user.is_teacher and course.teacher_id != current_user.id:
        abort(403)
    if not (current_user.is_teacher or current_user.is_admin):
        abort(403)

    changed = 0
    for key, raw in request.form.items():
        if not key.startswith("g_"):
            continue
        try:
            grade_id = int(key[2:])
        except ValueError:
            continue
        grade = db.session.get(Grade, grade_id)
        if grade is None or grade.assignment.course_id != course.id:
            continue
        raw = raw.strip()
        if raw == "":
            if grade.points_earned is not None:
                grade.points_earned = None
                grade.status = "ungraded"
                changed += 1
            continue
        if raw.upper() in ("M", "MISSING"):
            grade.status, grade.points_earned = "missing", None
            changed += 1
            continue
        if raw.upper() in ("E", "EX", "EXCUSED"):
            grade.status, grade.points_earned = "excused", None
            changed += 1
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        if grade.points_earned != value or grade.status != "graded":
            grade.points_earned = value
            grade.status = "graded"
            changed += 1

    db.session.commit()
    flash(f"Updated {changed} grade{'s' if changed != 1 else ''}.", "success")
    return redirect(url_for("grades.gradebook", course=course.id))


# ------------------------------------------------------------ assessments/analytics


@bp.route("/analytics")
@login_required
def analytics():
    """Standardized test performance: school vs district vs state."""
    if not (current_user.is_admin or current_user.is_district_admin):
        abort(403)

    summaries = school_assessment_summary()
    overall = {
        "school": None, "district": None, "state": None, "proficient": None,
    }
    school_vals = [s["school_average"] for s in summaries if s["school_average"] is not None]
    district_vals = [s["district_average"] for s in summaries if s["district_average"] is not None]
    state_vals = [s["state_average"] for s in summaries if s["state_average"] is not None]
    prof_vals = [s["proficient_rate"] for s in summaries if s["proficient_rate"] is not None]

    if school_vals:
        overall["school"] = round(sum(school_vals) / len(school_vals), 1)
    if district_vals:
        overall["district"] = round(sum(district_vals) / len(district_vals), 1)
    if state_vals:
        overall["state"] = round(sum(state_vals) / len(state_vals), 1)
    if prof_vals:
        overall["proficient"] = round(sum(prof_vals) / len(prof_vals), 1)

    return render_template("app/grades_analytics.html", summaries=summaries,
                           overall=overall)


@bp.route("/analytics/<int:assessment_id>")
@login_required
def assessment_detail(assessment_id):
    if not (current_user.is_admin or current_user.is_district_admin):
        abort(403)

    assessment = db.session.get(Assessment, assessment_id)
    if assessment is None:
        abort(404)

    from ..security import active_school
    school = active_school()
    rows = sorted(
        [r for r in assessment.results
         if r.student.school and school and r.student.school.id == school.id],
        key=lambda r: r.score,
    )
    return render_template("app/assessment_detail.html", assessment=assessment, rows=rows)
