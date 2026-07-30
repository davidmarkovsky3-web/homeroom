"""School administrator console — accounts, courses, bell schedule, rotation.

Sales lives in the Homeroom Staff console, not here: a school administrator runs a
school and has no business in the vendor's pipeline.
"""

import secrets
import string
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import (
    PERIOD_CLASS,
    PERIOD_KINDS,
    ROLES,
    ROLE_LABELS,
    ROTATION_MODES,
    WEEKDAY_NAMES,
    WEEKDAY_ORDER,
    Assignment,
    AttendanceRecord,
    BellPeriod,
    BellSchedule,
    Course,
    Enrollment,
    Grade,
    ParentLink,
    Period,
    School,
    SchoolDay,
    Term,
    User,
)
from ..security import active_school, school_admin_required
from ..services import periods as school_periods

bp = Blueprint("admin", __name__, url_prefix="/app/admin")

# Roles a school administrator may create.
CREATABLE_ROLES = ("student", "parent", "teacher", "admin")


def _temp_password(length=12):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _school_or_403():
    school = active_school()
    if school is None:
        abort(403)
    return school


def _same_school(user, school):
    if user.school_id != school.id:
        abort(404)
    return user


@bp.route("/")
@school_admin_required
def index():
    school = _school_or_403()
    return render_template(
        "app/admin_index.html",
        school=school,
        counts={
            "students": User.query.filter_by(role="student", school_id=school.id).count(),
            "parents": User.query.filter_by(role="parent", school_id=school.id).count(),
            "teachers": User.query.filter_by(role="teacher", school_id=school.id).count(),
            "admins": User.query.filter_by(role="admin", school_id=school.id).count(),
            "courses": Course.query.filter_by(school_id=school.id).count(),
            "periods": Period.query.filter_by(school_id=school.id).count(),
            "enrollments": (
                Enrollment.query.join(Course).filter(Course.school_id == school.id).count()
            ),
        },
    )


# --------------------------------------------------------------------------- users


@bp.route("/users")
@school_admin_required
def users():
    school = _school_or_403()
    role = request.args.get("role", "")
    search = request.args.get("q", "").strip()

    query = User.query.filter_by(school_id=school.id)
    if role in ROLES:
        query = query.filter_by(role=role)
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(
            User.first_name.ilike(like), User.last_name.ilike(like),
            User.email.ilike(like), User.student_number.ilike(like),
        ))

    return render_template(
        "app/admin_users.html",
        users=query.order_by(User.role, User.last_name, User.first_name).all(),
        role=role, search=search, roles=CREATABLE_ROLES, role_labels=ROLE_LABELS,
        school=school,
    )


@bp.route("/users/new", methods=["GET", "POST"])
@school_admin_required
def new_user():
    school = _school_or_403()
    students = (
        User.query.filter_by(role="student", school_id=school.id)
        .order_by(User.last_name, User.first_name).all()
    )

    if request.method == "POST":
        form = request.form
        email = form.get("email", "").strip().lower()
        role = form.get("role", "student")
        first_name = form.get("first_name", "").strip()
        last_name = form.get("last_name", "").strip()
        password = form.get("password", "").strip()
        link_ids = [int(v) for v in form.getlist("link_student") if v.isdigit()]

        errors = []
        if "@" not in email:
            errors.append("A valid email is required.")
        elif User.query.filter(db.func.lower(User.email) == email).first():
            errors.append("That email is already in use.")
        if role not in CREATABLE_ROLES:
            errors.append("Choose a valid role.")
        if not first_name or not last_name:
            errors.append("First and last name are required.")
        if password and len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if role == "parent" and not link_ids:
            errors.append("A parent account must be linked to at least one student.")

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template(
                "app/admin_user_form.html", roles=CREATABLE_ROLES,
                role_labels=ROLE_LABELS, students=students, school=school, form=form
            ), 400

        generated = None
        if not password:
            password = generated = _temp_password()

        user = User(
            email=email, role=role, first_name=first_name, last_name=last_name,
            school_id=school.id,
            department=form.get("department", "").strip() or None,
            homeroom=form.get("homeroom", "").strip() or None,
            title=form.get("title", "").strip() or None,
            phone=form.get("phone", "").strip(),
        )
        if role == "student":
            user.student_number = form.get("student_number", "").strip() or None
            grade = form.get("grade_level", "")
            user.grade_level = int(grade) if grade.isdigit() else None
        user.set_password(password)
        # A password the admin picked or generated is not the user's own yet.
        user.must_change_password = True
        db.session.add(user)
        db.session.flush()

        if role == "parent":
            for index, student_id in enumerate(link_ids):
                student = db.session.get(User, student_id)
                if student and student.school_id == school.id and student.is_student:
                    db.session.add(ParentLink(
                        parent_id=user.id, student_id=student.id,
                        relationship_label=form.get("relationship", "Parent").strip()
                        or "Parent",
                        is_primary=(index == 0),
                    ))

        db.session.commit()
        if generated:
            flash(f"Created {user.full_name}. Temporary password: {generated}", "success")
        else:
            flash(f"Created {user.full_name}.", "success")
        return redirect(url_for("admin.user_detail", user_id=user.id))

    return render_template(
        "app/admin_user_form.html", roles=CREATABLE_ROLES, role_labels=ROLE_LABELS,
        students=students, school=school, form={"role": "student"},
    )


@bp.route("/users/<int:user_id>", methods=["GET", "POST"])
@school_admin_required
def user_detail(user_id):
    school = _school_or_403()
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    _same_school(user, school)

    if request.method == "POST":
        form = request.form
        user.first_name = form.get("first_name", user.first_name).strip()
        user.last_name = form.get("last_name", user.last_name).strip()
        user.department = form.get("department", "").strip() or None
        user.homeroom = form.get("homeroom", "").strip() or None
        user.title = form.get("title", "").strip() or None
        user.phone = form.get("phone", "").strip()
        if user.is_student:
            grade = form.get("grade_level", "")
            user.grade_level = int(grade) if grade.isdigit() else None
            user.student_number = form.get("student_number", "").strip() or None
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("admin.user_detail", user_id=user.id))

    enrolled = [e.course for e in user.enrollments] if user.is_student else []
    available = None
    if user.is_student:
        taken = {c.id for c in enrolled}
        available = [
            c for c in Course.query.filter_by(school_id=school.id)
            .order_by(Course.name).all() if c.id not in taken
        ]

    all_students = (
        User.query.filter_by(role="student", school_id=school.id)
        .order_by(User.last_name, User.first_name).all()
        if user.is_parent else []
    )

    return render_template(
        "app/admin_user_detail.html",
        user=user, school=school,
        enrolled=sorted(enrolled, key=lambda c: (c.period.ordinal if c.period else 99)),
        available=available,
        taught=user.taught_courses if user.is_teacher else [],
        children=user.parent_links if user.is_parent else [],
        guardians=user.guardian_links if user.is_student else [],
        all_students=all_students,
    )


@bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@school_admin_required
def toggle_user(user_id):
    school = _school_or_403()
    user = _same_school(db.session.get(User, user_id) or abort(404), school)
    if user.id == current_user.id:
        flash("You can't deactivate your own account.", "error")
        return redirect(url_for("admin.user_detail", user_id=user.id))
    user.active = not user.active
    db.session.commit()
    flash(f"{user.full_name} is now {'active' if user.active else 'inactive'}.", "success")
    return redirect(url_for("admin.user_detail", user_id=user.id))


@bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@school_admin_required
def reset_password(user_id):
    school = _school_or_403()
    user = _same_school(db.session.get(User, user_id) or abort(404), school)
    temp = _temp_password()
    user.set_password(temp)
    user.must_change_password = True
    db.session.commit()
    flash(f"New temporary password for {user.full_name}: {temp}", "success")
    return redirect(url_for("admin.user_detail", user_id=user.id))


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
@school_admin_required
def delete_user(user_id):
    school = _school_or_403()
    user = _same_school(db.session.get(User, user_id) or abort(404), school)
    if user.id == current_user.id:
        flash("You can't delete your own account.", "error")
        return redirect(url_for("admin.user_detail", user_id=user.id))
    if user.is_teacher and user.taught_courses:
        flash(
            f"{user.full_name} still teaches {len(user.taught_courses)} section(s). "
            "Reassign them first.", "error",
        )
        return redirect(url_for("admin.user_detail", user_id=user.id))

    name = user.full_name
    Grade.query.filter_by(student_id=user.id).delete(synchronize_session=False)
    AttendanceRecord.query.filter_by(student_id=user.id).delete(synchronize_session=False)
    db.session.delete(user)
    db.session.commit()
    flash(f"Deleted {name} and their records.", "success")
    return redirect(url_for("admin.users"))


# ------------------------------------------------------------------- parent linking


@bp.route("/users/<int:user_id>/link", methods=["POST"])
@school_admin_required
def link_child(user_id):
    school = _school_or_403()
    parent = _same_school(db.session.get(User, user_id) or abort(404), school)
    if not parent.is_parent:
        abort(400)

    student = db.session.get(User, request.form.get("student_id", type=int))
    if student is None or not student.is_student or student.school_id != school.id:
        flash("Pick a student at this school.", "error")
    elif ParentLink.query.filter_by(parent_id=parent.id, student_id=student.id).first():
        flash(f"{parent.first_name} is already linked to {student.full_name}.", "warning")
    else:
        db.session.add(ParentLink(
            parent_id=parent.id, student_id=student.id,
            relationship_label=request.form.get("relationship", "Parent").strip() or "Parent",
            is_primary=not parent.parent_links,
        ))
        db.session.commit()
        flash(f"Linked {parent.full_name} to {student.full_name}.", "success")
    return redirect(url_for("admin.user_detail", user_id=parent.id))


@bp.route("/link/<int:link_id>/unlink", methods=["POST"])
@school_admin_required
def unlink_child(link_id):
    link = db.session.get(ParentLink, link_id)
    if link is None:
        abort(404)
    parent_id = link.parent_id
    db.session.delete(link)
    db.session.commit()
    flash("Link removed.", "success")
    return redirect(url_for("admin.user_detail", user_id=parent_id))


# ------------------------------------------------------------------------ enrollment


@bp.route("/users/<int:user_id>/enroll", methods=["POST"])
@school_admin_required
def enroll(user_id):
    school = _school_or_403()
    user = _same_school(db.session.get(User, user_id) or abort(404), school)
    if not user.is_student:
        abort(400)

    course = db.session.get(Course, request.form.get("course_id", type=int))
    if course is None or course.school_id != school.id:
        flash("Pick a course to enroll in.", "error")
        return redirect(url_for("admin.user_detail", user_id=user.id))

    if Enrollment.query.filter_by(student_id=user.id, course_id=course.id).first():
        flash(f"{user.first_name} is already in {course.code}.", "warning")
    elif course.is_full:
        flash(f"{course.code} is at capacity ({course.capacity}).", "error")
    else:
        conflict = next(
            (e.course for e in user.enrollments
             if e.course.period_id and e.course.period_id == course.period_id
             and set(e.course.day_tokens) & set(course.day_tokens)),
            None,
        )
        if conflict:
            flash(
                f"Schedule conflict: {conflict.code} already occupies "
                f"{course.period.name if course.period else 'that period'}.", "error",
            )
        else:
            db.session.add(Enrollment(student_id=user.id, course_id=course.id))
            db.session.flush()
            # Backfill gradebook rows so existing work shows up for them.
            for assignment in course.assignments:
                if not Grade.query.filter_by(assignment_id=assignment.id,
                                             student_id=user.id).first():
                    db.session.add(Grade(assignment_id=assignment.id,
                                         student_id=user.id, status="ungraded"))
            db.session.commit()
            flash(f"Enrolled {user.first_name} in {course.code}.", "success")

    return redirect(url_for("admin.user_detail", user_id=user.id))


@bp.route("/users/<int:user_id>/unenroll/<int:course_id>", methods=["POST"])
@school_admin_required
def unenroll(user_id, course_id):
    record = Enrollment.query.filter_by(student_id=user_id, course_id=course_id).first()
    if record is None:
        abort(404)
    db.session.delete(record)
    db.session.commit()
    flash("Removed from course.", "success")
    return redirect(url_for("admin.user_detail", user_id=user_id))


# -------------------------------------------------------------------------- courses


@bp.route("/courses", methods=["GET", "POST"])
@school_admin_required
def courses():
    school = _school_or_403()

    if request.method == "POST":
        form = request.form
        code = form.get("code", "").strip().upper()
        name = form.get("name", "").strip()
        if not code or not name:
            flash("Course code and name are required.", "error")
            return redirect(url_for("admin.courses"))

        tokens = form.getlist("meeting_days")
        course = Course(
            school_id=school.id,
            code=code, name=name,
            description=form.get("description", "").strip(),
            department=form.get("department", "General").strip() or "General",
            credits=float(form.get("credits") or 1.0),
            capacity=int(form.get("capacity") or 30),
            room=form.get("room", "").strip(),
            meeting_days=",".join(tokens) if tokens else "ALL",
            teacher_id=form.get("teacher_id", type=int) or None,
            period_id=form.get("period_id", type=int) or None,
            term_id=form.get("term_id", type=int) or None,
            prerequisite=form.get("prerequisite", "").strip(),
            selectable=form.get("selectable") == "on",
        )
        db.session.add(course)
        db.session.commit()
        flash(f"Created {course.code} — {course.name}.", "success")
        return redirect(url_for("admin.courses"))

    return render_template(
        "app/admin_courses.html",
        school=school,
        courses=Course.query.filter_by(school_id=school.id)
        .order_by(Course.department, Course.code).all(),
        teachers=User.query.filter_by(role="teacher", school_id=school.id)
        .order_by(User.last_name).all(),
        periods=school_periods(school),
        terms=Term.query.filter_by(school_id=school.id)
        .order_by(Term.start_date.desc()).all(),
    )


@bp.route("/courses/<int:course_id>/edit", methods=["POST"])
@school_admin_required
def edit_course(course_id):
    school = _school_or_403()
    course = db.session.get(Course, course_id)
    if course is None or course.school_id != school.id:
        abort(404)

    form = request.form
    course.name = form.get("name", course.name).strip()
    course.department = form.get("department", course.department).strip()
    course.capacity = int(form.get("capacity") or course.capacity)
    course.room = form.get("room", "").strip()
    tokens = form.getlist("meeting_days")
    if tokens:
        course.meeting_days = ",".join(tokens)
    course.teacher_id = form.get("teacher_id", type=int) or None
    course.period_id = form.get("period_id", type=int) or None
    course.selectable = form.get("selectable") == "on"
    db.session.commit()
    flash(f"Updated {course.code}.", "success")
    return redirect(url_for("admin.courses"))


@bp.route("/courses/<int:course_id>/roster", methods=["GET", "POST"])
@school_admin_required
def course_roster(course_id):
    """Assign students to a class in bulk, rather than one at a time per student."""
    school = _school_or_403()
    course = db.session.get(Course, course_id)
    if course is None or course.school_id != school.id:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action", "add")
        ids = [int(v) for v in request.form.getlist("student_id") if v.isdigit()]

        if action == "remove":
            removed = 0
            for student_id in ids:
                record = Enrollment.query.filter_by(
                    student_id=student_id, course_id=course.id).first()
                if record is not None:
                    db.session.delete(record)
                    removed += 1
            db.session.commit()
            flash(f"Removed {removed} student(s) from {course.code}.", "success")
            return redirect(url_for("admin.course_roster", course_id=course.id))

        added, skipped = 0, []
        for student_id in ids:
            student = db.session.get(User, student_id)
            if student is None or not student.is_student or student.school_id != school.id:
                continue
            if Enrollment.query.filter_by(student_id=student.id,
                                          course_id=course.id).first():
                continue
            if course.is_full:
                skipped.append(f"{student.last_name} (class full)")
                continue
            conflict = next(
                (e.course for e in student.enrollments
                 if e.course.period_id and e.course.period_id == course.period_id
                 and set(e.course.day_tokens) & set(course.day_tokens)),
                None,
            )
            if conflict:
                skipped.append(f"{student.last_name} (clashes with {conflict.code})")
                continue

            db.session.add(Enrollment(student_id=student.id, course_id=course.id))
            db.session.flush()
            for assignment in course.assignments:
                if not Grade.query.filter_by(assignment_id=assignment.id,
                                             student_id=student.id).first():
                    db.session.add(Grade(assignment_id=assignment.id,
                                         student_id=student.id, status="ungraded"))
            added += 1

        db.session.commit()
        message = f"Added {added} student(s) to {course.code}."
        if skipped:
            message += " Skipped: " + ", ".join(skipped[:5])
            if len(skipped) > 5:
                message += f" and {len(skipped) - 5} more"
        flash(message, "success" if added else "warning")
        return redirect(url_for("admin.course_roster", course_id=course.id))

    enrolled_ids = {e.student_id for e in course.enrollments}
    available = [
        s for s in User.query.filter_by(role="student", school_id=school.id, active=True)
        .order_by(User.grade_level, User.last_name, User.first_name).all()
        if s.id not in enrolled_ids
    ]
    return render_template("app/admin_roster.html", course=course,
                           roster=course.students, available=available, school=school)


@bp.route("/courses/<int:course_id>/delete", methods=["POST"])
@school_admin_required
def delete_course(course_id):
    """Remove a section along with everything hanging off it."""
    school = _school_or_403()
    course = db.session.get(Course, course_id)
    if course is None or course.school_id != school.id:
        abort(404)

    label = f"{course.code} — {course.name}"
    enrolled = course.seats_taken
    AttendanceRecord.query.filter_by(course_id=course.id).delete(synchronize_session=False)
    for assignment in list(course.assignments):
        Grade.query.filter_by(assignment_id=assignment.id).delete(synchronize_session=False)
    db.session.delete(course)   # cascades enrollments, assignments, requests
    db.session.commit()
    flash(
        f"Deleted {label}"
        + (f", unenrolling {enrolled} student(s)." if enrolled else "."),
        "success",
    )
    return redirect(url_for("admin.courses"))


# ------------------------------------------------------------ bell schedule / days


@bp.route("/schedule", methods=["GET", "POST"])
@school_admin_required
def bell_schedule():
    """Slots (what a course can occupy) and the school-wide timetable settings."""
    school = _school_or_403()

    if request.method == "POST":
        form = request.form
        try:
            start = datetime.strptime(form.get("start_time", ""), "%H:%M").time()
            end = datetime.strptime(form.get("end_time", ""), "%H:%M").time()
        except ValueError:
            flash("Valid default start and end times are required.", "error")
            return redirect(url_for("admin.bell_schedule"))
        if end <= start:
            flash("The end time must be after the start time.", "error")
            return redirect(url_for("admin.bell_schedule"))

        kind = form.get("kind", PERIOD_CLASS)
        db.session.add(Period(
            school_id=school.id,
            name=form.get("name", "").strip() or "Period",
            ordinal=int(form.get("ordinal")
                        or (Period.query.filter_by(school_id=school.id).count() + 1)),
            start_time=start, end_time=end,
            kind=kind if kind in PERIOD_KINDS else PERIOD_CLASS,
        ))
        db.session.commit()
        flash("Slot added. Add it to a day layout for it to actually run.", "success")
        return redirect(url_for("admin.bell_schedule"))

    upcoming = (
        SchoolDay.query.filter(SchoolDay.school_id == school.id,
                               SchoolDay.day_date >= date.today() - timedelta(days=7))
        .order_by(SchoolDay.day_date).limit(40).all()
    )
    return render_template(
        "app/admin_bell.html",
        school=school,
        periods=school_periods(school),
        layouts=BellSchedule.query.filter_by(school_id=school.id)
        .order_by(BellSchedule.name).all(),
        school_days=upcoming,
        rotation_modes=ROTATION_MODES,
        tokens=school.rotation_tokens,
        period_kinds=PERIOD_KINDS,
        weekday_order=WEEKDAY_ORDER,
        weekday_names=WEEKDAY_NAMES,
    )


@bp.route("/schedule/terminology", methods=["POST"])
@school_admin_required
def set_terminology():
    """Schools use different words, and some concepts they don't use at all."""
    school = _school_or_403()
    messages = []

    label = request.form.get("support_label", "").strip()[:40]
    if not label:
        flash("The flex block needs a name.", "error")
        return redirect(url_for("admin.bell_schedule"))
    if label != school.support_label:
        school.support_label = label
        messages.append(f"flex block is now “{label}”")

    uses_homeroom = request.form.get("uses_homeroom") == "on"
    homeroom_label = request.form.get("homeroom_label", "").strip()[:40] or "Homeroom"

    if uses_homeroom != school.uses_homeroom:
        messages.append("homeroom turned " + ("on" if uses_homeroom else
                                              "off and hidden everywhere"))
    elif uses_homeroom and homeroom_label != school.homeroom_label:
        messages.append(f"homeroom is now “{homeroom_label}”")

    school.uses_homeroom = uses_homeroom
    school.homeroom_label = homeroom_label

    db.session.commit()
    flash("Saved — " + ", ".join(messages) + "." if messages
          else "Nothing changed.", "success")
    return redirect(url_for("admin.bell_schedule"))


# ------------------------------------------------------------------- day layouts


@bp.route("/schedule/layouts", methods=["POST"])
@school_admin_required
def create_layout():
    school = _school_or_403()
    name = request.form.get("name", "").strip()
    if not name:
        flash("Give the layout a name, e.g. “Wednesday”.", "error")
        return redirect(url_for("admin.bell_schedule"))

    weekdays = [w for w in request.form.getlist("default_weekdays") if w in WEEKDAY_ORDER]
    layout = BellSchedule(
        school_id=school.id,
        name=name,
        description=request.form.get("description", "").strip()[:200],
        default_weekdays=",".join(weekdays),
        is_default=request.form.get("is_default") == "on",
    )
    db.session.add(layout)
    db.session.flush()

    if layout.is_default:
        for other in BellSchedule.query.filter(BellSchedule.school_id == school.id,
                                               BellSchedule.id != layout.id).all():
            other.is_default = False

    # Optionally start from every slot at its default times.
    if request.form.get("seed_all") == "on":
        for index, period in enumerate(school_periods(school), start=1):
            db.session.add(BellPeriod(
                bell_schedule_id=layout.id, period_id=period.id, ordinal=index,
                start_time=period.start_time, end_time=period.end_time,
            ))

    db.session.commit()
    flash(f"Created the “{layout.name}” layout.", "success")
    return redirect(url_for("admin.edit_layout", layout_id=layout.id))


@bp.route("/schedule/layouts/<int:layout_id>", methods=["GET", "POST"])
@school_admin_required
def edit_layout(layout_id):
    """Choose which slots run in this layout, in what order, at what times."""
    school = _school_or_403()
    layout = db.session.get(BellSchedule, layout_id)
    if layout is None or layout.school_id != school.id:
        abort(404)

    if request.method == "POST":
        layout.name = request.form.get("name", layout.name).strip() or layout.name
        layout.description = request.form.get("description", "").strip()[:200]
        weekdays = [w for w in request.form.getlist("default_weekdays") if w in WEEKDAY_ORDER]
        layout.default_weekdays = ",".join(weekdays)

        if request.form.get("is_default") == "on":
            layout.is_default = True
            for other in BellSchedule.query.filter(BellSchedule.school_id == school.id,
                                                   BellSchedule.id != layout.id).all():
                other.is_default = False
        else:
            layout.is_default = False

        # Rebuild the slot list from the submitted rows.
        chosen = request.form.getlist("slot")
        existing = {entry.period_id: entry for entry in layout.entries}
        keep = set()
        ordinal = 1
        errors = []

        for raw in chosen:
            if not raw.isdigit():
                continue
            period_id = int(raw)
            period = db.session.get(Period, period_id)
            if period is None or period.school_id != school.id:
                continue
            try:
                start = datetime.strptime(request.form.get(f"start_{period_id}", ""),
                                          "%H:%M").time()
                end = datetime.strptime(request.form.get(f"end_{period_id}", ""),
                                        "%H:%M").time()
            except ValueError:
                errors.append(f"{period.name}: needs a valid start and end time.")
                continue
            if end <= start:
                errors.append(f"{period.name}: the end time must be after the start.")
                continue

            entry = existing.get(period_id)
            if entry is None:
                entry = BellPeriod(bell_schedule_id=layout.id, period_id=period_id)
                db.session.add(entry)
            entry.start_time, entry.end_time = start, end
            entry.ordinal = ordinal
            ordinal += 1
            keep.add(period_id)

        for period_id, entry in existing.items():
            if period_id not in keep:
                db.session.delete(entry)

        if errors:
            for error in errors:
                flash(error, "error")
        db.session.commit()

        if not errors:
            flash(f"Saved the “{layout.name}” layout — {len(keep)} slots.", "success")
        return redirect(url_for("admin.edit_layout", layout_id=layout.id))

    entries = {entry.period_id: entry for entry in layout.entries}
    return render_template(
        "app/admin_layout.html",
        school=school, layout=layout,
        periods=school_periods(school), entries=entries,
        weekday_order=WEEKDAY_ORDER, weekday_names=WEEKDAY_NAMES,
        other_layouts=BellSchedule.query.filter(BellSchedule.school_id == school.id,
                                                BellSchedule.id != layout.id).all(),
    )


@bp.route("/schedule/layouts/<int:layout_id>/duplicate", methods=["POST"])
@school_admin_required
def duplicate_layout(layout_id):
    school = _school_or_403()
    layout = db.session.get(BellSchedule, layout_id)
    if layout is None or layout.school_id != school.id:
        abort(404)

    copy = BellSchedule(
        school_id=school.id, name=f"{layout.name} (copy)",
        description=layout.description, default_weekdays="", is_default=False,
    )
    db.session.add(copy)
    db.session.flush()
    for entry in layout.entries:
        db.session.add(BellPeriod(
            bell_schedule_id=copy.id, period_id=entry.period_id,
            ordinal=entry.ordinal, start_time=entry.start_time, end_time=entry.end_time,
        ))
    db.session.commit()
    flash(f"Duplicated as “{copy.name}”. Assign it some weekdays.", "success")
    return redirect(url_for("admin.edit_layout", layout_id=copy.id))


@bp.route("/schedule/layouts/<int:layout_id>/delete", methods=["POST"])
@school_admin_required
def delete_layout(layout_id):
    school = _school_or_403()
    layout = db.session.get(BellSchedule, layout_id)
    if layout is None or layout.school_id != school.id:
        abort(404)

    name = layout.name
    SchoolDay.query.filter_by(bell_schedule_id=layout.id).update(
        {"bell_schedule_id": None}, synchronize_session=False)
    db.session.delete(layout)
    db.session.commit()
    flash(f"Deleted the “{name}” layout. Dates using it fall back to their weekday default.",
          "success")
    return redirect(url_for("admin.bell_schedule"))


@bp.route("/schedule/rotation", methods=["POST"])
@school_admin_required
def set_rotation():
    """Change how this school's timetable repeats."""
    school = _school_or_403()
    mode = request.form.get("rotation_mode", "daily")
    if mode not in ROTATION_MODES:
        flash("Choose a valid timetable type.", "error")
        return redirect(url_for("admin.bell_schedule"))

    try:
        cycle = max(2, min(int(request.form.get("cycle_length") or 6), 20))
    except ValueError:
        cycle = 6

    school.rotation_mode = mode
    school.cycle_length = cycle
    db.session.commit()
    flash(
        f"Timetable set to “{school.rotation_label}”. "
        "Check that each section's meeting days still make sense.",
        "success",
    )
    return redirect(url_for("admin.bell_schedule"))


@bp.route("/schedule/periods/<int:period_id>/delete", methods=["POST"])
@school_admin_required
def delete_period(period_id):
    """Delete a bell period.

    Sections in the period are unscheduled rather than destroyed, so nothing is lost
    silently — an admin can reassign them afterwards.
    """
    school = _school_or_403()
    period = db.session.get(Period, period_id)
    if period is None or period.school_id != school.id:
        abort(404)

    affected = list(period.courses)
    for course in affected:
        course.period_id = None

    name = period.name
    db.session.delete(period)   # cascades its support sessions
    db.session.commit()

    if affected:
        flash(
            f"Deleted {name}. {len(affected)} section(s) are now unscheduled — "
            "assign them a new period from Manage courses.",
            "warning",
        )
    else:
        flash(f"Deleted {name}.", "success")
    return redirect(url_for("admin.bell_schedule"))


@bp.route("/schedule/periods/<int:period_id>/edit", methods=["POST"])
@school_admin_required
def edit_period(period_id):
    school = _school_or_403()
    period = db.session.get(Period, period_id)
    if period is None or period.school_id != school.id:
        abort(404)

    period.name = request.form.get("name", period.name).strip() or period.name
    try:
        period.ordinal = int(request.form.get("ordinal") or period.ordinal)
    except ValueError:
        pass
    for field in ("start_time", "end_time"):
        raw = request.form.get(field, "")
        if raw:
            try:
                setattr(period, field, datetime.strptime(raw, "%H:%M").time())
            except ValueError:
                pass
    kind = request.form.get("kind", period.kind)
    if kind in PERIOD_KINDS:
        period.kind = kind

    if period.end_time <= period.start_time:
        flash("The end time must be after the start time.", "error")
        db.session.rollback()
    else:
        db.session.commit()
        flash(f"Updated {period.name}.", "success")
    return redirect(url_for("admin.bell_schedule"))


@bp.route("/schedule/days", methods=["POST"])
@school_admin_required
def set_school_day():
    school = _school_or_403()
    try:
        day = datetime.strptime(request.form.get("day_date", ""), "%Y-%m-%d").date()
    except ValueError:
        flash("Pick a valid date.", "error")
        return redirect(url_for("admin.bell_schedule"))

    row = SchoolDay.query.filter_by(school_id=school.id, day_date=day).first()
    if row is None:
        row = SchoolDay(school_id=school.id, day_date=day)
        db.session.add(row)
        db.session.flush()

    row.day_type = request.form.get("day_type", "A")
    row.in_session = request.form.get("in_session") == "on"
    row.note = request.form.get("note", "").strip()[:160]

    # Optional per-date layout override — an assembly schedule on one Tuesday, say.
    layout_id = request.form.get("bell_schedule_id", type=int)
    if layout_id:
        layout = db.session.get(BellSchedule, layout_id)
        row.bell_schedule_id = layout.id if layout and layout.school_id == school.id else None
    else:
        row.bell_schedule_id = None

    db.session.commit()
    flash(
        f"{day.isoformat()} set to "
        f"{school.day_label(row.day_type) if row.in_session else 'no school'}.",
        "success",
    )
    return redirect(url_for("admin.bell_schedule"))


@bp.route("/schedule/days/generate", methods=["POST"])
@school_admin_required
def generate_days():
    """Fill a date range with the school's rotation, skipping weekends."""
    school = _school_or_403()
    try:
        start = datetime.strptime(request.form.get("start", ""), "%Y-%m-%d").date()
        end = datetime.strptime(request.form.get("end", ""), "%Y-%m-%d").date()
    except ValueError:
        flash("Pick a valid start and end date.", "error")
        return redirect(url_for("admin.bell_schedule"))

    if end < start:
        start, end = end, start
    if (end - start).days > 400:
        flash("Generate at most about a year at a time.", "error")
        return redirect(url_for("admin.bell_schedule"))

    tokens = school.rotation_tokens or ["A"]
    cursor, index, created = start, 0, 0
    while cursor <= end:
        if cursor.weekday() < 5:
            row = SchoolDay.query.filter_by(school_id=school.id, day_date=cursor).first()
            if row is None:
                row = SchoolDay(school_id=school.id, day_date=cursor, in_session=True)
                db.session.add(row)
                created += 1
            row.day_type = tokens[index % len(tokens)]
            index += 1
        cursor += timedelta(days=1)

    db.session.commit()
    flash(f"Generated {created} school day(s) using the {school.rotation_label} pattern.",
          "success")
    return redirect(url_for("admin.bell_schedule"))
