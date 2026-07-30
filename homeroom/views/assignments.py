"""Assignments: teacher authoring, student and parent views."""

from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import Assignment, Course, Grade, GradeCategory
from ..security import may_view_student, require_student_access
from ..services import courses_for, student_courses, upcoming_assignments
from ..models import User

bp = Blueprint("assignments", __name__, url_prefix="/app/assignments")


def _parse_date(raw):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _owned_course(course_id):
    """Fetch a course the current user is allowed to author assignments for."""
    course = db.session.get(Course, course_id)
    if course is None:
        abort(404)
    if current_user.is_teacher and course.teacher_id != current_user.id:
        abort(403)
    if current_user.is_admin and course.school_id != current_user.school_id:
        abort(403)
    if not (current_user.is_teacher or current_user.is_admin):
        abort(403)
    return course


@bp.route("/")
@login_required
def index():
    if current_user.is_student:
        return redirect(url_for("assignments.my_assignments"))
    if current_user.is_parent:
        return redirect(url_for("parents.index"))
    if current_user.is_teacher or current_user.is_admin:
        return redirect(url_for("assignments.teacher_index"))
    abort(403)


# ------------------------------------------------------------------------- students


@bp.route("/me")
@login_required
def my_assignments():
    student = current_user
    if current_user.is_parent:
        student_id = request.args.get("student", type=int)
        student = db.session.get(User, student_id) if student_id else None
        require_student_access(student)
    elif not current_user.is_student:
        abort(403)

    show = request.args.get("show", "open")
    course_ids = [c.id for c in student_courses(student)]
    rows = []

    if course_ids:
        query = Assignment.query.filter(
            Assignment.course_id.in_(course_ids), Assignment.published.is_(True)
        )
        assignments = query.order_by(Assignment.due_on.is_(None), Assignment.due_on).all()
        grades = {
            g.assignment_id: g
            for g in Grade.query.filter(Grade.student_id == student.id).all()
        }
        today = date.today()
        for assignment in assignments:
            grade = grades.get(assignment.id)
            status = "graded" if grade and grade.status == "graded" else (
                grade.status if grade else "assigned"
            )
            overdue = bool(
                assignment.due_on and assignment.due_on < today and status in
                ("assigned", "ungraded", "missing")
            )
            rows.append({"assignment": assignment, "grade": grade,
                         "status": status, "overdue": overdue})

        if show == "open":
            rows = [r for r in rows if r["status"] in ("assigned", "ungraded", "missing")]
        elif show == "graded":
            rows = [r for r in rows if r["status"] == "graded"]
        elif show == "missing":
            rows = [r for r in rows if r["status"] == "missing" or r["overdue"]]

    return render_template(
        "app/assignments_student.html",
        rows=rows,
        student=student,
        show=show,
        upcoming=upcoming_assignments(student),
        viewing_as_parent=current_user.is_parent,
    )


# ------------------------------------------------------------------------- teachers


@bp.route("/teaching")
@login_required
def teacher_index():
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

    assignments = []
    if course:
        assignments = (
            Assignment.query.filter_by(course_id=course.id)
            .order_by(Assignment.due_on.is_(None), Assignment.due_on.desc())
            .all()
        )

    return render_template(
        "app/assignments_teacher.html",
        my_courses=my_courses,
        course=course,
        assignments=assignments,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new_assignment():
    course_id = request.args.get("course", type=int) or request.form.get("course_id", type=int)
    if not course_id:
        abort(400)
    course = _owned_course(course_id)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        try:
            points = float(request.form.get("points_possible") or 100)
        except ValueError:
            points = -1

        if not title:
            flash("An assignment title is required.", "error")
        elif points < 0:
            flash("Points possible must be zero or more.", "error")
        else:
            assignment = Assignment(
                course_id=course.id,
                title=title,
                description=request.form.get("description", "").strip(),
                points_possible=points,
                category_id=request.form.get("category_id", type=int) or None,
                assigned_on=_parse_date(request.form.get("assigned_on")) or date.today(),
                due_on=_parse_date(request.form.get("due_on")),
                published=request.form.get("published") == "on",
                graded=request.form.get("graded") == "on",
                created_by_id=current_user.id,
            )
            db.session.add(assignment)
            db.session.flush()

            # Seed an ungraded row per enrolled student so the gradebook is complete.
            for student in course.students:
                db.session.add(Grade(assignment_id=assignment.id, student_id=student.id,
                                     status="ungraded"))
            db.session.commit()
            flash(f"Created “{assignment.title}”.", "success")
            return redirect(url_for("assignments.detail", assignment_id=assignment.id))

    return render_template(
        "app/assignment_form.html",
        course=course,
        form=request.form if request.method == "POST" else
        {"assigned_on": date.today().isoformat(),
         "due_on": (date.today() + timedelta(days=7)).isoformat(),
         "points_possible": 100, "published": "on", "graded": "on"},
    )


@bp.route("/<int:assignment_id>")
@login_required
def detail(assignment_id):
    assignment = db.session.get(Assignment, assignment_id)
    if assignment is None:
        abort(404)
    course = assignment.course

    is_owner = (
        (current_user.is_teacher and course.teacher_id == current_user.id)
        or (current_user.is_admin and course.school_id == current_user.school_id)
    )
    if not is_owner:
        # Students and parents may see a published assignment they're enrolled in.
        student = current_user if current_user.is_student else None
        if current_user.is_parent:
            student_id = request.args.get("student", type=int)
            student = db.session.get(User, student_id) if student_id else None
        if student is None or not may_view_student(student):
            abort(403)
        if not assignment.published:
            abort(403)
        if course.id not in {c.id for c in student_courses(student)}:
            abort(403)
        grade = Grade.query.filter_by(assignment_id=assignment.id,
                                      student_id=student.id).first()
        return render_template("app/assignment_student_detail.html",
                               assignment=assignment, grade=grade, student=student)

    grades = sorted(
        Grade.query.filter_by(assignment_id=assignment.id).all(),
        key=lambda g: (g.student.last_name, g.student.first_name),
    )
    return render_template("app/assignment_detail.html", assignment=assignment,
                           grades=grades, course=course)


@bp.route("/<int:assignment_id>/edit", methods=["POST"])
@login_required
def edit_assignment(assignment_id):
    assignment = db.session.get(Assignment, assignment_id)
    if assignment is None:
        abort(404)
    _owned_course(assignment.course_id)

    assignment.title = request.form.get("title", assignment.title).strip()
    assignment.description = request.form.get("description", "").strip()
    try:
        assignment.points_possible = float(request.form.get("points_possible")
                                           or assignment.points_possible)
    except ValueError:
        pass
    assignment.due_on = _parse_date(request.form.get("due_on"))
    assignment.category_id = request.form.get("category_id", type=int) or None
    assignment.published = request.form.get("published") == "on"
    assignment.graded = request.form.get("graded") == "on"
    db.session.commit()
    flash("Assignment updated.", "success")
    return redirect(url_for("assignments.detail", assignment_id=assignment.id))


@bp.route("/<int:assignment_id>/delete", methods=["POST"])
@login_required
def delete_assignment(assignment_id):
    assignment = db.session.get(Assignment, assignment_id)
    if assignment is None:
        abort(404)
    course = _owned_course(assignment.course_id)
    title = assignment.title
    db.session.delete(assignment)
    db.session.commit()
    flash(f"Deleted “{title}” and its grades.", "success")
    return redirect(url_for("assignments.teacher_index", course=course.id))


@bp.route("/<int:assignment_id>/grade", methods=["POST"])
@login_required
def save_grades(assignment_id):
    """Score every student on one assignment."""
    assignment = db.session.get(Assignment, assignment_id)
    if assignment is None:
        abort(404)
    _owned_course(assignment.course_id)

    saved = 0
    for grade in Grade.query.filter_by(assignment_id=assignment.id).all():
        status = request.form.get(f"status_{grade.student_id}", grade.status)
        raw = request.form.get(f"points_{grade.student_id}", "").strip()

        if status in ("graded", "ungraded", "missing", "excused", "late"):
            grade.status = status
        if raw == "":
            grade.points_earned = None
            if grade.status == "graded":
                grade.status = "ungraded"
        else:
            try:
                grade.points_earned = float(raw)
                if grade.status in ("ungraded",):
                    grade.status = "graded"
            except ValueError:
                pass
        grade.comment = request.form.get(f"comment_{grade.student_id}", "")[:255]
        grade.graded_by_id = current_user.id
        grade.graded_at = datetime.utcnow()
        saved += 1

    db.session.commit()
    flash(f"Saved {saved} grades for “{assignment.title}”.", "success")
    return redirect(url_for("assignments.detail", assignment_id=assignment.id))


# ------------------------------------------------------------------ grade categories


@bp.route("/categories/<int:course_id>", methods=["GET", "POST"])
@login_required
def categories(course_id):
    course = _owned_course(course_id)

    if request.method == "POST":
        action = request.form.get("action", "add")
        if action == "mode":
            mode = request.form.get("grading_mode", "points")
            course.grading_mode = "weighted" if mode == "weighted" else "points"
            db.session.commit()
            flash(f"Grading set to {course.grading_mode}.", "success")
        elif action == "delete":
            category = db.session.get(GradeCategory, request.form.get("category_id", type=int))
            if category and category.course_id == course.id:
                for assignment in category.assignments:
                    assignment.category_id = None
                db.session.delete(category)
                db.session.commit()
                flash("Category removed.", "success")
        else:
            name = request.form.get("name", "").strip()
            try:
                weight = float(request.form.get("weight") or 0)
            except ValueError:
                weight = 0
            if not name:
                flash("Category name is required.", "error")
            else:
                db.session.add(GradeCategory(course_id=course.id, name=name, weight=weight))
                db.session.commit()
                flash(f"Added category “{name}”.", "success")
        return redirect(url_for("assignments.categories", course_id=course.id))

    total_weight = sum(c.weight for c in course.categories)
    return render_template("app/grade_categories.html", course=course,
                           total_weight=total_weight)
