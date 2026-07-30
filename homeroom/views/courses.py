"""Course requests and selection.

The course catalog lives inside this section rather than as its own tab — browsing
courses is something you do while deciding what to request.
"""

from collections import defaultdict
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import (
    Announcement,
    Assignment,
    Course,
    CourseRequest,
    Enrollment,
    Grade,
    SelectionWindow,
    User,
)
from ..security import active_school, school_admin_required, staff_required, student_required
from ..services import active_window, course_average, current_term

bp = Blueprint("courses", __name__, url_prefix="/app/courses")


class PendingPick:
    """An un-saved selection, used to re-render the form after a validation error."""

    id = None
    status = "pending"
    reviewer_note = ""

    def __init__(self, course, slot, rank):
        self.course = course
        self.course_id = course.id
        self.slot = slot
        self.rank = rank

    @property
    def rank_label(self):
        return "1st pick" if self.rank == 1 else "2nd pick"


def _school_courses(selectable_only=False):
    school = active_school()
    query = Course.query
    if school:
        query = query.filter(Course.school_id == school.id)
    if selectable_only:
        query = query.filter(Course.selectable.is_(True))
    return query.order_by(Course.department, Course.name)


def _catalog():
    grouped = defaultdict(list)
    for course in _school_courses(selectable_only=True).all():
        grouped[course.department].append(course)
    return dict(sorted(grouped.items()))


@bp.route("/")
@login_required
def index():
    if current_user.is_student:
        return redirect(url_for("courses.selection"))
    return redirect(url_for("courses.review_requests"))


@bp.route("/catalog")
@login_required
def catalog():
    """Browsable catalog — reachable from inside Course Requests."""
    department = request.args.get("department", "")
    search = request.args.get("q", "").strip()

    query = _school_courses()
    if department:
        query = query.filter(Course.department == department)
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(Course.name.ilike(like), Course.code.ilike(like)))

    school = active_school()
    dept_query = db.session.query(Course.department).distinct()
    if school:
        dept_query = dept_query.filter(Course.school_id == school.id)

    return render_template(
        "app/catalog.html",
        courses=query.all(),
        departments=sorted(row[0] for row in dept_query.all()),
        department=department, search=search, term=current_term(),
    )


@bp.route("/<int:course_id>")
@login_required
def detail(course_id):
    course = db.session.get(Course, course_id)
    if course is None:
        abort(404)
    school = active_school()
    if school and course.school_id != school.id:
        abort(404)

    is_teacher = current_user.is_teacher and course.teacher_id == current_user.id
    manages = current_user.is_admin or current_user.is_district_admin

    roster = course.students if (is_teacher or manages) else None
    enrolled = current_user.is_student and Enrollment.query.filter_by(
        student_id=current_user.id, course_id=course.id).first() is not None

    # The class hub: what's been set for this class specifically.
    announcements = [
        a for a in Announcement.query.filter_by(course_id=course.id)
        .order_by(Announcement.starts_on.desc()).all()
        if is_teacher or manages or a.reaches(current_user)
    ]
    assignments = (
        Assignment.query.filter_by(course_id=course.id)
        .order_by(Assignment.due_on.is_(None), Assignment.due_on.desc())
        .limit(6).all()
    )
    if not (is_teacher or manages):
        assignments = [a for a in assignments if a.published]

    return render_template(
        "app/course_detail.html", course=course, roster=roster, enrolled=enrolled,
        announcements=announcements[:5], assignments=assignments,
        is_class_teacher=is_teacher, manages=manages,
        average=course_average(course) if (is_teacher or manages) else None,
    )


# ------------------------------------------------------------------------ selection


@bp.route("/selection", methods=["GET", "POST"])
@student_required
def selection():
    window = active_window()
    if window is None:
        return render_template("app/selection_closed.html", window=None)

    existing = CourseRequest.query.filter_by(
        student_id=current_user.id, window_id=window.id).all()
    picks = {(r.slot, r.rank): r for r in existing}

    if request.method == "POST":
        if not window.accepting:
            flash("Course selection is closed. Contact your counselor for changes.", "error")
            return redirect(url_for("courses.selection"))

        errors = []
        chosen = {}
        for slot in range(1, window.required_slots + 1):
            first_raw = request.form.get(f"slot_{slot}_first", "")
            second_raw = request.form.get(f"slot_{slot}_second", "")
            first = int(first_raw) if first_raw.isdigit() else None
            second = int(second_raw) if second_raw.isdigit() else None

            if first is None and second is not None:
                errors.append(f"Choice {slot}: pick a 1st choice before an alternate.")
                continue
            if first is not None and second is not None and first == second:
                errors.append(f"Choice {slot}: the 1st and 2nd picks must be different courses.")
                continue
            chosen[slot] = (first, second)

        firsts = [c[0] for c in chosen.values() if c[0]]
        if len(firsts) != len(set(firsts)):
            errors.append("The same course is listed as a 1st pick more than once.")

        filled = sum(1 for first, _ in chosen.values() if first)
        if filled < window.required_slots:
            errors.append(
                f"All {window.required_slots} choices need a 1st pick "
                f"({filled} of {window.required_slots} complete)."
            )

        if errors:
            for error in errors:
                flash(error, "error")
        else:
            for record in existing:
                db.session.delete(record)
            db.session.flush()
            for slot, (first, second) in chosen.items():
                if first:
                    db.session.add(CourseRequest(
                        student_id=current_user.id, course_id=first,
                        window_id=window.id, slot=slot, rank=1))
                if second:
                    db.session.add(CourseRequest(
                        student_id=current_user.id, course_id=second,
                        window_id=window.id, slot=slot, rank=2))
            db.session.commit()
            flash("Your course requests were submitted.", "success")
            return redirect(url_for("courses.selection"))

        picks = {}
        for slot in range(1, window.required_slots + 1):
            for rank, field in ((1, "first"), (2, "second")):
                raw = request.form.get(f"slot_{slot}_{field}", "")
                if raw.isdigit():
                    course = db.session.get(Course, int(raw))
                    if course:
                        picks[(slot, rank)] = PendingPick(course, slot, rank)

    return render_template(
        "app/selection.html",
        window=window, catalog=_catalog(), picks=picks,
        slots=range(1, window.required_slots + 1), submitted=bool(existing),
    )


@bp.route("/selection/summary")
@student_required
def selection_summary():
    window = active_window()
    requests_by_slot = defaultdict(dict)
    if window:
        for record in CourseRequest.query.filter_by(
                student_id=current_user.id, window_id=window.id).all():
            requests_by_slot[record.slot][record.rank] = record
    return render_template("app/selection_summary.html", window=window,
                           requests_by_slot=dict(sorted(requests_by_slot.items())))


# --------------------------------------------------------------------- review (staff)


@bp.route("/requests")
@school_admin_required
def review_requests():
    """Counselor/admin review. Teachers don't belong here — this is a counseling task."""
    school = active_school()
    window_id = request.args.get("window", type=int)

    window_query = SelectionWindow.query
    if school:
        window_query = window_query.filter(SelectionWindow.school_id == school.id)
    windows = window_query.order_by(SelectionWindow.opens_on.desc()).all()
    window = (
        db.session.get(SelectionWindow, window_id) if window_id
        else (windows[0] if windows else None)
    )

    status = request.args.get("status", "")
    grouped = defaultdict(list)
    counts = {"pending": 0, "approved": 0, "denied": 0}

    if window:
        for record in CourseRequest.query.filter_by(window_id=window.id).all():
            counts[record.status] = counts.get(record.status, 0) + 1
        query = CourseRequest.query.filter_by(window_id=window.id)
        if status:
            query = query.filter_by(status=status)
        for record in query.all():
            grouped[record.student].append(record)

    ordered = sorted(grouped.items(), key=lambda kv: (kv[0].last_name, kv[0].first_name))
    for _, records in ordered:
        records.sort(key=lambda r: (r.slot, r.rank))

    return render_template("app/requests_review.html", windows=windows, window=window,
                           grouped=ordered, counts=counts, status=status)


@bp.route("/requests/<int:request_id>/<action>", methods=["POST"])
@school_admin_required
def decide_request(request_id, action):
    if action not in ("approve", "deny", "reset"):
        abort(400)
    record = db.session.get(CourseRequest, request_id)
    if record is None:
        abort(404)

    if action == "approve":
        record.status = "approved"
        if not Enrollment.query.filter_by(student_id=record.student_id,
                                          course_id=record.course_id).first():
            if record.course.is_full:
                flash(f"{record.course.name} is full — approved without a seat.", "warning")
            else:
                db.session.add(Enrollment(student_id=record.student_id,
                                          course_id=record.course_id))
                db.session.flush()
                for assignment in record.course.assignments:
                    if not Grade.query.filter_by(assignment_id=assignment.id,
                                                 student_id=record.student_id).first():
                        db.session.add(Grade(assignment_id=assignment.id,
                                             student_id=record.student_id,
                                             status="ungraded"))
    elif action == "deny":
        record.status = "denied"
    else:
        record.status = "pending"

    record.reviewer_note = request.form.get("note", "")[:255]
    db.session.commit()
    flash(f"{record.student.full_name}: {record.course.code} marked {record.status}.",
          "success")
    return redirect(request.referrer or url_for("courses.review_requests"))


@bp.route("/windows", methods=["GET", "POST"])
@school_admin_required
def windows():
    school = active_school()

    if request.method == "POST":
        form = request.form
        try:
            opens = datetime.strptime(form.get("opens_on", ""), "%Y-%m-%d").date()
            closes = datetime.strptime(form.get("closes_on", ""), "%Y-%m-%d").date()
        except ValueError:
            flash("Both open and close dates are required.", "error")
            return redirect(url_for("courses.windows"))
        if closes < opens:
            flash("The close date must be on or after the open date.", "error")
            return redirect(url_for("courses.windows"))

        db.session.add(SelectionWindow(
            school_id=school.id if school else None,
            name=form.get("name", "Course Selection").strip(),
            opens_on=opens, closes_on=closes,
            required_slots=int(form.get("required_slots", 6)),
            instructions=form.get("instructions", "").strip(), is_open=True,
        ))
        db.session.commit()
        flash("Selection window created.", "success")
        return redirect(url_for("courses.windows"))

    window_query = SelectionWindow.query
    if school:
        window_query = window_query.filter(SelectionWindow.school_id == school.id)
    all_windows = window_query.order_by(SelectionWindow.opens_on.desc()).all()

    student_total = User.query.filter_by(
        role="student", active=True,
        school_id=school.id if school else None).count()
    stats = {
        w.id: {
            "submitted": db.session.query(CourseRequest.student_id)
            .filter_by(window_id=w.id).distinct().count(),
            "total": student_total,
        }
        for w in all_windows
    }
    return render_template("app/windows.html", windows=all_windows, stats=stats)


@bp.route("/windows/<int:window_id>/toggle", methods=["POST"])
@school_admin_required
def toggle_window(window_id):
    window = db.session.get(SelectionWindow, window_id)
    if window is None:
        abort(404)
    window.is_open = not window.is_open
    db.session.commit()
    flash(f"{window.name} is now {'open' if window.is_open else 'closed'}.", "success")
    return redirect(url_for("courses.windows"))
