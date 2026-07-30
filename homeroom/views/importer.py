"""Bulk import of students, staff and courses from a pasted or uploaded file.

Accepts CSV or tab-separated text. Every row is validated before anything is written,
and the result is reported row by row — a bad row is skipped, not fatal.
"""

import csv
import io
import secrets
import string
from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import (
    Course,
    Enrollment,
    Grade,
    ParentLink,
    Period,
    User,
    subject_for_department,
)
from ..security import active_school, school_admin_required

bp = Blueprint("importer", __name__, url_prefix="/app/admin/import")

TEMPLATES = {
    "students": {
        "label": "Students",
        "required": ["first_name", "last_name", "email", "student_number",
                     "grade_level", "guardian_email", "guardian_name"],
        "optional": ["homeroom", "phone", "home_language", "counselor"],
        "sample": (
            "first_name,last_name,email,student_number,grade_level,guardian_email,guardian_name\n"
            "Avery,Nakamura,avery.nakamura@students.example.edu,20260001,11,"
            "rowan.nakamura@example.com,Rowan Nakamura\n"
            "Beckett,Bergstrom,beckett.bergstrom@students.example.edu,20260002,9,"
            "dana.bergstrom@example.com,Dana Bergstrom\n"
        ),
    },
    "staff": {
        "label": "Teachers & administrators",
        "required": ["first_name", "last_name", "email", "role", "department", "title"],
        "optional": ["phone"],
        "sample": (
            "first_name,last_name,email,role,department,title\n"
            "Marcus,Delacroix,mdelacroix@example.edu,teacher,Mathematics,Teacher\n"
            "Gwendolyn,Fairbairn,principal@example.edu,admin,Administration,Principal\n"
        ),
    },
    "courses": {
        "label": "Courses & sections",
        "required": ["code", "name"],
        "optional": ["department", "teacher_email", "period", "room", "capacity",
                     "credits", "meeting_days", "rigor", "prerequisite"],
        "sample": (
            "code,name,department,teacher_email,period,room,capacity,credits,rigor\n"
            "MTH210,Algebra II,Mathematics,mdelacroix@example.edu,Period 1,212,28,1.0,regular\n"
            "MTH410,AP Calculus AB,Mathematics,mdelacroix@example.edu,Period 5,214,24,1.0,ap\n"
        ),
    },
    "enrollments": {
        "label": "Enrollments",
        "required": ["student_email", "course_code"],
        "optional": [],
        "sample": (
            "student_email,course_code\n"
            "avery.nakamura@students.example.edu,MTH210\n"
            "avery.nakamura@students.example.edu,SCI230\n"
        ),
    },
}

RIGOR_BONUS = {"regular": 0.0, "honors": 0.5, "ap": 1.0}

# How initial passwords are set for a batch. Everyone imported is forced to choose a new
# one at first sign-in regardless, so these only cover the gap before that happens.
PASSWORD_MODES = {
    "shared": {
        "label": "One password for the whole batch",
        "hint": "Easiest to hand out — tell everyone the same thing. Until each person "
                "signs in and changes it, anyone holding it can open another "
                "not-yet-used account, so treat it as short-lived.",
    },
    "identifier": {
        "label": "Built from the student number",
        "hint": "Different for every student, and you only have to communicate the rule "
                "rather than 200 secrets. Staff still get a random one.",
    },
    "random": {
        "label": "Random, one per person",
        "hint": "Safest, but you have to deliver each password individually.",
    },
}


def _temp_password(length=12):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _initial_password(mode, shared, identifier=None):
    """The password a newly-imported account starts with, and how to describe it."""
    if mode == "shared" and shared:
        return shared, "batch password"
    if mode == "identifier" and identifier:
        return f"{identifier}", "from their student number"
    generated = _temp_password()
    return generated, f"password {generated}"


def _read_rows(text):
    """Parse CSV or tab-separated text into dicts with normalised keys."""
    text = text.strip()
    if not text:
        return [], "The file was empty."

    sample = text[:2000]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        rows = []
        for raw in reader:
            if raw is None:
                continue
            row = {}
            for key, value in raw.items():
                if key is None:
                    continue
                row[key.strip().lower().replace(" ", "_")] = (value or "").strip()
            if any(row.values()):
                rows.append(row)
        return rows, None
    except csv.Error as exc:
        return [], f"Couldn't parse that file: {exc}"


@bp.route("/", methods=["GET", "POST"])
@school_admin_required
def index():
    school = active_school()
    if school is None:
        abort(403)

    kind = request.args.get("kind") or request.form.get("kind") or "students"
    if kind not in TEMPLATES:
        kind = "students"

    results = None
    mode = request.form.get("password_mode", "shared")
    shared = request.form.get("shared_password", "").strip()

    # Only these two create accounts, so only these two care about passwords.
    makes_accounts = kind in ("students", "staff")

    if request.method == "POST":
        if mode not in PASSWORD_MODES:
            mode = "shared"
        if not makes_accounts:
            mode = "random"   # irrelevant here; never prompts for a batch password

        text = request.form.get("pasted", "")
        upload = request.files.get("file")
        if upload and upload.filename:
            raw = upload.read()
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                try:
                    text = raw.decode("cp1252")
                except UnicodeDecodeError:
                    flash("Couldn't read that file — save it as UTF-8 CSV and retry.",
                          "error")
                    text = ""

        rows, error = _read_rows(text)
        if error:
            flash(error, "error")
        elif not rows:
            flash("No data rows found. Include a header line.", "error")
        else:
            missing = [c for c in TEMPLATES[kind]["required"]
                       if c not in (rows[0].keys())]
            if missing:
                flash("Missing required column(s): " + ", ".join(missing), "error")
            elif makes_accounts and mode == "shared" and len(shared) < 6:
                # Checked after the file, so a broken sheet reports its real problem
                # rather than being masked by the password prompt.
                flash("The file looks fine. Set a batch password of at least 6 "
                      "characters, or choose another password option.", "error")
            else:
                handler = {
                    "students": _import_students,
                    "staff": _import_staff,
                    "courses": _import_courses,
                    "enrollments": _import_enrollments,
                }[kind]
                dry_run = request.form.get("dry_run") == "on"
                results = handler(rows, school, dry_run, mode=mode, shared=shared)
                if dry_run:
                    db.session.rollback()
                    flash(f"Preview only — nothing was saved. "
                          f"{results['created']} row(s) would be created.", "info")
                else:
                    db.session.commit()
                    flash(f"Imported {results['created']} of {results['total']} rows.",
                          "success")

    return render_template("app/admin_import.html", kind=kind, templates=TEMPLATES,
                           results=results, school=school,
                           password_modes=PASSWORD_MODES, mode=mode, shared=shared)


def _blank_result(rows):
    return {"total": len(rows), "created": 0, "skipped": 0, "rows": []}


def _record(result, index, row_label, status, message):
    result["rows"].append({"line": index + 2, "label": row_label,
                           "status": status, "message": message})
    if status == "created":
        result["created"] += 1
    else:
        result["skipped"] += 1


def _import_students(rows, school, dry_run, mode="random", shared=""):
    """Every student needs an ID, a grade, and a named guardian with an email.

    A student record with no guardian contact is close to useless — there's nobody to
    notify about an absence or a failing grade — so those columns are required rather
    than a nicety.
    """
    result = _blank_result(rows)
    seen_emails = set()
    seen_numbers = set()
    grades = set(school.grade_range)

    for index, row in enumerate(rows):
        email = row.get("email", "").lower()
        name = f"{row.get('first_name', '')} {row.get('last_name', '')}".strip()
        label = name or email or f"row {index + 2}"

        if not row.get("first_name") or not row.get("last_name"):
            _record(result, index, label, "skipped", "First and last name are required.")
            continue
        if "@" not in email:
            _record(result, index, label, "skipped", "A valid email is required.")
            continue
        if email in seen_emails:
            _record(result, index, label, "skipped", "Duplicate email within this file.")
            continue
        if User.query.filter(db.func.lower(User.email) == email).first():
            _record(result, index, label, "skipped", "That email already exists.")
            continue

        # Student number — required, and unique across the whole system.
        number = row.get("student_number", "").strip()
        if not number:
            _record(result, index, label, "skipped", "Student number is required.")
            continue
        if number in seen_numbers:
            _record(result, index, label, "skipped",
                    f"Student number {number} appears twice in this file.")
            continue
        if User.query.filter_by(student_number=number).first():
            _record(result, index, label, "skipped",
                    f"Student number {number} is already in use.")
            continue

        # Grade level — required, numeric, and one this school actually teaches.
        grade_raw = row.get("grade_level", "").strip()
        if not grade_raw:
            _record(result, index, label, "skipped", "Grade level is required.")
            continue
        if not grade_raw.lstrip("-").isdigit():
            _record(result, index, label, "skipped",
                    f"Grade level “{grade_raw}” isn't a number.")
            continue
        grade = int(grade_raw)
        if grades and grade not in grades:
            _record(result, index, label, "skipped",
                    f"This school teaches grades {school.low_grade}–{school.high_grade}, "
                    f"not {grade}.")
            continue

        # Guardian — required: a name and a working email, and not the student's own.
        guardian_email = row.get("guardian_email", "").strip().lower()
        guardian_name = row.get("guardian_name", "").strip()
        if not guardian_email:
            _record(result, index, label, "skipped", "Guardian email is required.")
            continue
        if "@" not in guardian_email:
            _record(result, index, label, "skipped",
                    f"“{guardian_email}” isn't a valid guardian email.")
            continue
        if not guardian_name:
            _record(result, index, label, "skipped", "Guardian name is required.")
            continue
        if guardian_email == email:
            _record(result, index, label, "skipped",
                    "The guardian email can't be the student's own address.")
            continue

        seen_emails.add(email)
        seen_numbers.add(number)

        password, described = _initial_password(mode, shared, identifier=number)
        student = User(
            email=email, role="student", school_id=school.id,
            first_name=row["first_name"], last_name=row["last_name"],
            student_number=number, grade_level=grade,
            # Ignored outright at schools that don't have homerooms.
            homeroom=(row.get("homeroom") or None) if school.uses_homeroom else None,
            phone=row.get("phone", ""),
            home_language=row.get("home_language", "")[:60],
            counselor=row.get("counselor", "")[:120],
        )
        student.set_password(password)
        student.must_change_password = True
        db.session.add(student)
        db.session.flush()

        note = f"grade {grade}; {described}"

        guardian = User.query.filter(
            db.func.lower(User.email) == guardian_email).first()
        if guardian is None:
            parts = guardian_name.split()
            guardian_password, guardian_described = _initial_password(mode, shared)
            guardian = User(
                email=guardian_email, role="parent", school_id=school.id,
                first_name=parts[0],
                last_name=parts[-1] if len(parts) > 1 else student.last_name,
                must_change_password=True,
            )
            guardian.set_password(guardian_password)
            db.session.add(guardian)
            db.session.flush()
            note += f"; guardian created, {guardian_described}"
        else:
            note += "; linked to existing guardian"

        if not ParentLink.query.filter_by(parent_id=guardian.id,
                                          student_id=student.id).first():
            db.session.add(ParentLink(parent_id=guardian.id, student_id=student.id,
                                      is_primary=not guardian.parent_links))

        _record(result, index, label, "created", note)

    return result


def _import_staff(rows, school, dry_run, mode="random", shared=""):
    """Role, department and title are all required, and none of them is cosmetic.

    Role decides what the account can do — silently defaulting someone to "teacher"
    is the kind of guess that hands out the wrong access. Department drives which
    standardized-test subject a teacher is shown for at-risk students, so a blank one
    leaves them with no subject at all.
    """
    result = _blank_result(rows)
    seen = set()

    for index, row in enumerate(rows):
        email = row.get("email", "").lower()
        label = f"{row.get('first_name', '')} {row.get('last_name', '')}".strip() or email

        if not row.get("first_name") or not row.get("last_name"):
            _record(result, index, label, "skipped", "First and last name are required.")
            continue
        if "@" not in email:
            _record(result, index, label, "skipped", "A valid email is required.")
            continue
        if email in seen or User.query.filter(db.func.lower(User.email) == email).first():
            _record(result, index, label, "skipped", "That email already exists.")
            continue

        role = row.get("role", "").strip().lower()
        if not role:
            _record(result, index, label, "skipped",
                    "Role is required — teacher or admin. It decides what the account can do.")
            continue
        if role not in ("teacher", "admin"):
            _record(result, index, label, "skipped",
                    f"Role must be teacher or admin, got “{role}”.")
            continue

        department = row.get("department", "").strip()
        if not department:
            _record(result, index, label, "skipped",
                    "Department is required — it decides which test subject a teacher "
                    "sees for struggling students.")
            continue

        title = row.get("title", "").strip()
        if not title:
            _record(result, index, label, "skipped", "Title is required.")
            continue

        seen.add(email)
        # Staff never get an identifier-derived password — they have no student number,
        # and a shared one is riskier on an account that can change grades.
        password, described = _initial_password(
            "shared" if mode == "shared" else "random", shared)
        person = User(
            email=email, role=role, school_id=school.id,
            first_name=row["first_name"], last_name=row["last_name"],
            department=department[:80], title=title[:80],
            phone=row.get("phone", ""),
            must_change_password=True,
        )
        person.set_password(password)
        db.session.add(person)

        note = f"{role}, {department}; {described}"
        if role == "teacher" and subject_for_department(department) is None:
            note += (f" — note: “{department}” maps to no test subject, so at-risk flags "
                     "for this teacher won't include standardized results")
        _record(result, index, label, "created", note)

    return result


def _import_courses(rows, school, dry_run, mode="random", shared=""):
    result = _blank_result(rows)
    periods = {p.name.lower(): p for p in Period.query.filter_by(school_id=school.id).all()}
    seen = set()

    for index, row in enumerate(rows):
        code = (row.get("code") or "").upper()
        label = f"{code} {row.get('name', '')}".strip() or f"row {index + 2}"

        if not code or not row.get("name"):
            _record(result, index, label, "skipped", "Code and name are required.")
            continue
        if code in seen:
            _record(result, index, label, "skipped", "Duplicate code within this file.")
            continue
        if Course.query.filter_by(school_id=school.id, code=code).first():
            _record(result, index, label, "skipped", "That course code already exists.")
            continue
        seen.add(code)

        teacher = None
        teacher_email = row.get("teacher_email", "").lower()
        if teacher_email:
            teacher = User.query.filter(db.func.lower(User.email) == teacher_email,
                                        User.school_id == school.id).first()
            if teacher is None:
                _record(result, index, label, "skipped",
                        f"No staff account for {teacher_email}.")
                continue

        period = None
        period_name = row.get("period", "").lower()
        if period_name:
            period = periods.get(period_name)
            if period is None:
                _record(result, index, label, "skipped",
                        f"No slot named “{row['period']}” — create it first.")
                continue

        rigor = (row.get("rigor") or "regular").lower()
        if rigor not in RIGOR_BONUS:
            rigor = "regular"

        try:
            capacity = int(row.get("capacity") or 30)
            credits = float(row.get("credits") or 1.0)
        except ValueError:
            _record(result, index, label, "skipped", "Capacity and credits must be numbers.")
            continue

        db.session.add(Course(
            school_id=school.id, code=code, name=row["name"],
            department=row.get("department") or "General",
            teacher_id=teacher.id if teacher else None,
            period_id=period.id if period else None,
            room=row.get("room", ""), capacity=capacity, credits=credits,
            meeting_days=row.get("meeting_days") or "ALL",
            prerequisite=row.get("prerequisite", ""),
            rigor=rigor, gpa_bonus=RIGOR_BONUS[rigor],
        ))
        _record(result, index, label, "created",
                f"{rigor}{'' if not teacher else ', ' + teacher.last_name}")

    return result


def _import_enrollments(rows, school, dry_run, mode="random", shared=""):
    result = _blank_result(rows)

    for index, row in enumerate(rows):
        email = row.get("student_email", "").lower()
        code = (row.get("course_code") or "").upper()
        label = f"{email} → {code}"

        student = User.query.filter(db.func.lower(User.email) == email,
                                    User.role == "student",
                                    User.school_id == school.id).first()
        if student is None:
            _record(result, index, label, "skipped", "No student with that email.")
            continue

        course = Course.query.filter_by(school_id=school.id, code=code).first()
        if course is None:
            _record(result, index, label, "skipped", "No course with that code.")
            continue
        if Enrollment.query.filter_by(student_id=student.id, course_id=course.id).first():
            _record(result, index, label, "skipped", "Already enrolled.")
            continue
        if course.is_full:
            _record(result, index, label, "skipped",
                    f"{code} is full ({course.capacity}).")
            continue

        conflict = next(
            (e.course for e in student.enrollments
             if e.course.period_id and e.course.period_id == course.period_id
             and set(e.course.day_tokens) & set(course.day_tokens)),
            None,
        )
        if conflict:
            _record(result, index, label, "skipped",
                    f"Clashes with {conflict.code} in the same period.")
            continue

        db.session.add(Enrollment(student_id=student.id, course_id=course.id))
        db.session.flush()
        for assignment in course.assignments:
            if not Grade.query.filter_by(assignment_id=assignment.id,
                                         student_id=student.id).first():
                db.session.add(Grade(assignment_id=assignment.id,
                                     student_id=student.id, status="ungraded"))
        _record(result, index, label, "created", "enrolled")

    return result
