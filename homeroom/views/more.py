"""The "More" section: the reference and office screens that don't need a top tab."""

from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import (
    ABSENCE_KINDS,
    ABSENCE_REASON_LABELS,
    ABSENCE_REASONS,
    ABSENCE_REPORT,
    ABSENCE_REPORT_REASONS,
    ABSENCE_REQUEST,
    ABSENCE_REQUEST_REASONS,
    ABSENCE_STATUSES,
    AbsenceRequest,
    Assessment,
    AssessmentResult,
    CalendarEvent,
    Course,
    HealthRecord,
    User,
)
from ..security import (
    active_school,
    may_view_student,
    require_student_access,
    school_admin_required,
)
from ..services import events_between, notify, student_courses

bp = Blueprint("more", __name__, url_prefix="/app/more")


def _parse_date(raw):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _staff():
    return current_user.is_teacher or current_user.is_admin \
        or current_user.is_district_admin


@bp.route("/")
@login_required
def index():
    school = active_school()
    pending_absences = 0
    if current_user.is_admin and school:
        pending_absences = AbsenceRequest.query.filter_by(
            school_id=school.id, status="pending").count()

    return render_template("app/more_index.html", school=school,
                           is_staff=_staff(), pending_absences=pending_absences)


# ------------------------------------------------------------------- assessments


@bp.route("/assessments")
@login_required
def assessments():
    """Every standardized assessment, with the viewer's own results where relevant."""
    rows = Assessment.query.order_by(Assessment.administered_on.desc()).all()

    my_results = {}
    students = []
    if current_user.is_student:
        students = [current_user]
    elif current_user.is_parent:
        students = current_user.children

    for student in students:
        for result in AssessmentResult.query.filter_by(student_id=student.id).all():
            my_results.setdefault(result.assessment_id, []).append(result)

    return render_template("app/more_assessments.html", assessments=rows,
                           my_results=my_results, students=students,
                           is_staff=_staff())


# ---------------------------------------------------------------- contact list


@bp.route("/contacts")
@login_required
def contacts():
    """Who to talk to — staff directory, scoped by what the viewer should see."""
    school = active_school()
    if school is None:
        abort(403)

    query = User.query.filter(User.school_id == school.id, User.active.is_(True))

    if current_user.is_student or current_user.is_parent:
        # Families see the staff who actually teach them, plus administration.
        relevant = set()
        for student in ([current_user] if current_user.is_student else current_user.children):
            for course in student_courses(student):
                if course.teacher_id:
                    relevant.add(course.teacher_id)
        people = [
            person for person in query.filter(User.role.in_(("teacher", "admin"))).all()
            if person.role == "admin" or person.id in relevant
        ]
    else:
        people = query.filter(User.role.in_(("teacher", "admin"))).all()

    people.sort(key=lambda p: (p.role != "admin", p.last_name, p.first_name))
    return render_template("app/more_contacts.html", people=people, school=school)


# ------------------------------------------------------------------ demographics


@bp.route("/demographics")
@school_admin_required
def demographics():
    """Office view of the student body."""
    school = active_school()
    grade = request.args.get("grade", type=int)
    search = request.args.get("q", "").strip()

    query = User.query.filter_by(role="student", school_id=school.id)
    if grade:
        query = query.filter(User.grade_level == grade)
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(User.first_name.ilike(like), User.last_name.ilike(like),
                                    User.student_number.ilike(like)))
    students = query.order_by(User.last_name, User.first_name).all()

    all_students = User.query.filter_by(role="student", school_id=school.id).all()
    by_grade = {}
    for student in all_students:
        by_grade[student.grade_level] = by_grade.get(student.grade_level, 0) + 1

    return render_template(
        "app/more_demographics.html",
        students=students, school=school, grade=grade, search=search,
        by_grade=dict(sorted(by_grade.items(), key=lambda kv: (kv[0] is None, kv[0]))),
        totals={
            "students": len(all_students),
            "iep": sum(1 for s in all_students if s.has_iep),
            "plan504": sum(1 for s in all_students if s.has_504),
            "languages": len({s.home_language for s in all_students if s.home_language}),
        },
    )


@bp.route("/demographics/<int:student_id>", methods=["POST"])
@school_admin_required
def save_demographics(student_id):
    school = active_school()
    student = db.session.get(User, student_id)
    if student is None or student.school_id != school.id:
        abort(404)

    form = request.form
    student.home_language = form.get("home_language", "").strip()[:60]
    student.counselor = form.get("counselor", "").strip()[:120]
    student.locker = form.get("locker", "").strip()[:20]
    student.bus_route = form.get("bus_route", "").strip()[:20]
    student.has_iep = form.get("has_iep") == "on"
    student.has_504 = form.get("has_504") == "on"
    student.notes = form.get("notes", "").strip()
    enrolled = _parse_date(form.get("enrolled_on"))
    if enrolled:
        student.enrolled_on = enrolled
    db.session.commit()
    flash(f"Updated {student.full_name}'s record.", "success")
    return redirect(url_for("more.demographics"))


# ------------------------------------------------------------------------ health


@bp.route("/health")
@login_required
def health():
    """Nurse office. Staff see the school; families see their own students."""
    school = active_school()

    if current_user.is_student:
        students = [current_user]
    elif current_user.is_parent:
        students = current_user.children
    elif _staff() and school:
        students = (
            User.query.filter_by(role="student", school_id=school.id)
            .order_by(User.last_name, User.first_name).all()
        )
    else:
        abort(403)

    records = {r.student_id: r for r in HealthRecord.query.filter(
        HealthRecord.student_id.in_([s.id for s in students] or [0])).all()}

    flagged = [s for s in students
               if records.get(s.id) and records[s.id].has_action_plan]

    return render_template("app/more_health.html", students=students, records=records,
                           flagged=flagged, can_edit=_staff())


@bp.route("/health/<int:student_id>", methods=["POST"])
@login_required
def save_health(student_id):
    if not _staff():
        abort(403)
    student = db.session.get(User, student_id)
    if student is None or not may_view_student(student):
        abort(404)

    record = HealthRecord.query.filter_by(student_id=student.id).first()
    if record is None:
        record = HealthRecord(student_id=student.id)
        db.session.add(record)

    form = request.form
    record.allergies = form.get("allergies", "").strip()
    record.medications = form.get("medications", "").strip()
    record.conditions = form.get("conditions", "").strip()
    record.dietary_notes = form.get("dietary_notes", "").strip()
    record.physician_name = form.get("physician_name", "").strip()[:120]
    record.physician_phone = form.get("physician_phone", "").strip()[:40]
    record.insurance_provider = form.get("insurance_provider", "").strip()[:120]
    record.has_action_plan = form.get("has_action_plan") == "on"
    record.action_plan_note = form.get("action_plan_note", "").strip()[:255]
    record.updated_by_id = current_user.id
    record.updated_at = datetime.utcnow()
    db.session.commit()
    flash(f"Saved health record for {student.full_name}.", "success")
    return redirect(url_for("more.health"))


# ---------------------------------------------------------------- important dates


@bp.route("/dates")
@login_required
def important_dates():
    """The year at a glance — deadlines, exams, holidays, no-school days."""
    school = active_school()
    today = date.today()
    horizon = int(request.args.get("days", 180))

    events = events_between(today - timedelta(days=30), today + timedelta(days=horizon),
                            current_user, school)
    key_categories = ("exam", "holiday", "deadline")
    key = [e for e in events if e.category in key_categories]
    other = [e for e in events if e.category not in key_categories]

    upcoming = [e for e in key if e.event_date >= today]
    past = [e for e in key if e.event_date < today]

    return render_template("app/more_dates.html", upcoming=upcoming, past=past[-10:],
                           other=[e for e in other if e.event_date >= today][:15],
                           today=today, horizon=horizon)


# --------------------------------------------------------------- absence requests


@bp.route("/absences", methods=["GET", "POST"])
@login_required
def absences():
    school = active_school()

    if request.method == "POST":
        if not (current_user.is_student or current_user.is_parent):
            abort(403)

        if current_user.is_student:
            student = current_user
        else:
            student = db.session.get(User, request.form.get("student_id", type=int))
            require_student_access(student)

        today = date.today()
        kind = request.form.get("kind", ABSENCE_REPORT)
        start = _parse_date(request.form.get("start_date"))
        end = _parse_date(request.form.get("end_date")) or start
        reason = request.form.get("reason", "illness")

        errors = []
        if kind not in ABSENCE_KINDS:
            errors.append("Choose whether you're reporting or requesting.")
        if start is None:
            errors.append("Pick a start date.")
        elif end < start:
            errors.append("The end date can't be before the start date.")
        if reason not in ABSENCE_REASONS:
            errors.append("Choose a reason.")

        # The bit that was nonsense before: you can't file an illness that hasn't
        # happened yet, and there's no point "requesting" a day that's already gone.
        if start is not None and not errors:
            if kind == ABSENCE_REPORT and start > today:
                errors.append(
                    "You're reporting an absence that hasn't happened yet. If it's "
                    "planned — an appointment or a trip — switch to a planned request."
                )
            elif kind == ABSENCE_REQUEST and start <= today:
                errors.append(
                    "That date has already started, so there's nothing to approve in "
                    "advance. Report it as an absence instead."
                )
            elif kind == ABSENCE_REPORT and reason not in ABSENCE_REPORT_REASONS:
                errors.append(
                    f"“{ABSENCE_REASON_LABELS.get(reason, reason)}” is something you plan "
                    "ahead — file it as a planned request."
                )
            elif kind == ABSENCE_REQUEST and reason not in ABSENCE_REQUEST_REASONS:
                errors.append(
                    f"“{ABSENCE_REASON_LABELS.get(reason, reason)}” isn't something you "
                    "can plan for. Report it once it happens."
                )

        if errors:
            for error in errors:
                flash(error, "error")
        else:
            db.session.add(AbsenceRequest(
                school_id=student.school_id, student_id=student.id,
                submitted_by_id=current_user.id, kind=kind,
                start_date=start, end_date=end, reason=reason,
                detail=request.form.get("detail", "").strip(),
            ))
            db.session.commit()
            flash(
                "Absence reported. The office will decide whether it's excused."
                if kind == ABSENCE_REPORT
                else "Request submitted. The office will approve or deny it.",
                "success",
            )
        return redirect(url_for("more.absences"))

    if current_user.is_student:
        rows = AbsenceRequest.query.filter_by(student_id=current_user.id)
        students = [current_user]
    elif current_user.is_parent:
        child_ids = [c.id for c in current_user.children] or [0]
        rows = AbsenceRequest.query.filter(AbsenceRequest.student_id.in_(child_ids))
        students = current_user.children
    elif _staff() and school:
        rows = AbsenceRequest.query.filter_by(school_id=school.id)
        students = []
    else:
        abort(403)

    status = request.args.get("status", "")
    if status in ABSENCE_STATUSES:
        rows = rows.filter(AbsenceRequest.status == status)
    kind = request.args.get("kind", "")
    if kind in ABSENCE_KINDS:
        rows = rows.filter(AbsenceRequest.kind == kind)

    return render_template(
        "app/more_absences.html",
        requests=rows.order_by(AbsenceRequest.submitted_at.desc()).all(),
        students=students, statuses=ABSENCE_STATUSES, status=status,
        kinds=ABSENCE_KINDS, kind=kind,
        report_reasons=ABSENCE_REPORT_REASONS,
        request_reasons=ABSENCE_REQUEST_REASONS,
        reason_labels=ABSENCE_REASON_LABELS,
        can_review=current_user.is_admin,
        can_submit=current_user.is_student or current_user.is_parent,
    )


@bp.route("/absences/<int:request_id>/<action>", methods=["POST"])
@school_admin_required
def decide_absence(request_id, action):
    if action not in ("approve", "deny"):
        abort(400)

    row = db.session.get(AbsenceRequest, request_id)
    if row is None:
        abort(404)

    row.status = "approved" if action == "approve" else "denied"
    row.reviewer_note = request.form.get("note", "")[:255]
    row.reviewed_by_id = current_user.id
    row.reviewed_at = datetime.utcnow()

    # Wording follows the kind: a report gets excused, a plan gets approved.
    outcome = row.decision_label.lower()
    notify(row.student, f"Absence {outcome} — {row.date_label}",
           row.reviewer_note, kind="absence", created_by=current_user,
           link=url_for("more.absences"))
    for link in row.student.guardian_links:
        notify(link.parent,
               f"{row.student.known_as}'s absence {outcome} — {row.date_label}",
               row.reviewer_note, kind="absence", created_by=current_user,
               link=url_for("more.absences"))

    db.session.commit()
    flash(f"{row.student.full_name}: {row.date_label} marked {outcome}.", "success")
    return redirect(url_for("more.absences"))
