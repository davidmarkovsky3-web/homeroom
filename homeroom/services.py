"""Shared query and derivation logic used by more than one view.

Everything here is scoped to the caller's active school unless a school is passed in.
"""

from datetime import date, datetime, time, timedelta

from flask import url_for
from sqlalchemy import func

from .extensions import db
from .models import (
    ROLE_STUDENT,
    AbsenceRequest,
    Announcement,
    AnnouncementRead,
    Assessment,
    AssessmentResult,
    Assignment,
    AttendanceRecord,
    BellSchedule,
    Notification,
    CalendarEvent,
    Course,
    Enrollment,
    Grade,
    Period,
    SchoolDay,
    SelectionWindow,
    SupportSession,
    SupportSignup,
    Term,
    User,
    format_range,
    letter_for,
    subject_for_department,
)
from .security import active_school


def _school_id(school=None):
    school = school or active_school()
    return school.id if school else None


def current_term(school=None):
    sid = _school_id(school)
    query = Term.query
    if sid:
        query = query.filter(Term.school_id == sid)
    return (
        query.filter_by(is_current=True).first()
        or query.order_by(Term.start_date.desc()).first()
    )


def periods(school=None):
    """Every slot defined at the school, in canonical order."""
    sid = _school_id(school)
    query = Period.query
    if sid:
        query = query.filter(Period.school_id == sid)
    return query.order_by(Period.ordinal).all()


def support_periods(school=None):
    return [p for p in periods(school) if p.is_support]


def bell_schedules(school=None):
    sid = _school_id(school)
    query = BellSchedule.query
    if sid:
        query = query.filter(BellSchedule.school_id == sid)
    return query.order_by(BellSchedule.name).all()


def bell_schedule_for(day, school=None):
    """Which day layout runs on `day`.

    A per-date override wins; otherwise the layout claiming that weekday; otherwise the
    school's default. Returns None if the school has no layouts at all, in which case
    callers fall back to the flat slot list.
    """
    school = school or active_school()
    if school is None:
        return None

    row = school_day_for(day, school)
    if row is not None and row.bell_schedule_id:
        override = db.session.get(BellSchedule, row.bell_schedule_id)
        if override is not None and override.school_id == school.id:
            return override

    available = bell_schedules(school)
    if not available:
        return None

    for layout in available:
        if layout.runs_on_weekday(day.weekday()):
            return layout

    return next((s for s in available if s.is_default), None)


def day_slots(day, school=None):
    """Ordered (period, start, end) rows that actually run on `day`.

    Falls back to the school's flat slot list when no layouts are defined, so a school
    that hasn't set any up still behaves like it did before.
    """
    school = school or active_school()
    layout = bell_schedule_for(day, school)
    if layout is None:
        return [
            {"period": p, "start_time": p.start_time, "end_time": p.end_time,
             "ordinal": p.ordinal}
            for p in periods(school)
        ]
    return [
        {"period": entry.period, "start_time": entry.start_time,
         "end_time": entry.end_time, "ordinal": entry.ordinal}
        for entry in layout.entries if entry.period is not None
    ]


def school_day_for(day, school=None):
    sid = _school_id(school)
    query = SchoolDay.query.filter_by(day_date=day)
    if sid:
        query = query.filter(SchoolDay.school_id == sid)
    return query.first()


def day_type_for(day, school=None):
    row = school_day_for(day, school)
    if row and row.in_session:
        return row.day_type
    return None


def students(school=None):
    sid = _school_id(school)
    query = User.query.filter_by(role=ROLE_STUDENT, active=True)
    if sid:
        query = query.filter(User.school_id == sid)
    return query.order_by(User.last_name, User.first_name).all()


# ------------------------------------------------------------------------ schedules


def student_courses(student, day=None):
    result = (
        Course.query.join(Enrollment, Enrollment.course_id == Course.id)
        .filter(Enrollment.student_id == student.id)
        .outerjoin(Period, Course.period_id == Period.id)
        .order_by(Period.ordinal)
        .all()
    )
    return _filter_to_day(result, day, student.school)


def teacher_courses(teacher, day=None):
    result = (
        Course.query.filter(Course.teacher_id == teacher.id)
        .outerjoin(Period, Course.period_id == Period.id)
        .order_by(Period.ordinal)
        .all()
    )
    return _filter_to_day(result, day, teacher.school)


def _filter_to_day(courses, day, school):
    """A section meets on a day when both are true:

    its slot appears in that day's layout, and its rotation days include that day type.
    The layout check is what makes a Wednesday that skips P1 skip every P1 course.
    """
    if day is None:
        return courses
    if not in_session(day, school):
        return []

    dtype = day_type_for(day, school)
    layout = bell_schedule_for(day, school)
    running = layout.slot_ids if layout is not None else None

    result = []
    for course in courses:
        if running is not None and course.period_id not in running:
            continue
        if not course.meets_on(dtype, weekday=day.weekday()):
            continue
        result.append(course)
    return result


def in_session(day, school=None):
    """Whether school runs on this date.

    An explicit SchoolDay wins. With no row on file we fall back to weekdays, so a
    school that hasn't populated its calendar still shows a sensible timetable.
    """
    row = school_day_for(day, school)
    if row is not None:
        return row.in_session
    return day.weekday() < 5


def courses_for(user, day=None):
    if user.is_teacher:
        return teacher_courses(user, day=day)
    if user.is_student:
        return student_courses(user, day=day)
    return []


def support_signup_for(student, day, period=None):
    """The support session a student is booked into on a date."""
    query = (
        SupportSignup.query.join(SupportSession)
        .filter(SupportSignup.student_id == student.id,
                SupportSession.session_date == day)
    )
    if period is not None:
        query = query.filter(SupportSession.period_id == period.id)
    return query.first()


def build_day_schedule(user, day, school=None):
    """Ordered schedule rows for `day`, following whichever layout runs that day.

    Only the slots in that day's layout appear — so a Wednesday layout that omits P1
    simply has no P1 row, and P1 courses don't meet. Support slots resolve to the
    student's chosen session rather than a fixed course.
    """
    school = school or (user.school if user.is_school_user else active_school())
    dtype = day_type_for(day, school)
    open_today = in_session(day, school)
    day_courses = {c.period_id: c for c in courses_for(user, day=day)}
    now = datetime.now().time()
    is_today = day == date.today()
    layout = bell_schedule_for(day, school)

    rows = []
    for slot in day_slots(day, school):
        period = slot["period"]
        start, end = slot["start_time"], slot["end_time"]
        course = day_courses.get(period.id) if open_today else None

        support = None
        if period.is_support and open_today:
            if user.is_student:
                support = support_signup_for(user, day, period)
            elif user.is_teacher:
                support = SupportSession.query.filter_by(
                    teacher_id=user.id, period_id=period.id, session_date=day
                ).first()

        if is_today:
            if end < now:
                state = "past"
            elif start <= now <= end:
                state = "current"
            else:
                state = "upcoming"
        else:
            state = "upcoming"

        rows.append({
            "period": period, "course": course, "state": state, "support": support,
            "start_time": start, "end_time": end,
            "time_range": format_range(start, end),
        })

    return {
        "date": day,
        "day_type": dtype,
        "day_label": school.day_label(dtype) if school else (dtype or "No school"),
        "layout": layout,
        "layout_name": layout.name if layout else None,
        "rows": rows,
        "in_session": open_today,
    }


def admin_day_overview(day, school=None):
    """What the whole school is doing, slot by slot — the admin's schedule view."""
    school = school or active_school()
    dtype = day_type_for(day, school)
    now = datetime.now().time()
    is_today = day == date.today()
    layout = bell_schedule_for(day, school)
    open_today = in_session(day, school)

    all_courses = Course.query.filter_by(school_id=_school_id(school)).all()
    rows = []
    for slot in day_slots(day, school):
        period = slot["period"]
        start, end = slot["start_time"], slot["end_time"]

        meeting = [
            c for c in all_courses
            if c.period_id == period.id and open_today
            and c.meets_on(dtype, weekday=day.weekday())
        ]
        sessions = SupportSession.query.filter_by(
            period_id=period.id, session_date=day
        ).all() if (period.is_support and open_today) else []

        if is_today:
            state = "past" if end < now else ("current" if start <= now <= end else "upcoming")
        else:
            state = "upcoming"

        rows.append({
            "period": period,
            "state": state,
            "start_time": start,
            "end_time": end,
            "time_range": format_range(start, end),
            "sections": meeting,
            "section_count": len(meeting),
            "students_in_class": sum(c.seats_taken for c in meeting),
            "support_sessions": sessions,
            "support_seats": sum(s.seats_taken for s in sessions),
        })

    return {"date": day, "day_type": dtype, "rows": rows,
            "layout": layout, "layout_name": layout.name if layout else None,
            "in_session": open_today}


def current_and_next(user, moment=None):
    moment = moment or datetime.now()
    schedule = build_day_schedule(user, moment.date())
    current_row = next_row = None
    for row in schedule["rows"]:
        if row["start_time"] <= moment.time() <= row["end_time"]:
            current_row = row
        elif row["start_time"] > moment.time() and next_row is None:
            next_row = row
    return current_row, next_row, schedule


def minutes_until(target: time, moment=None):
    moment = moment or datetime.now()
    delta = datetime.combine(moment.date(), target) - moment
    return max(int(delta.total_seconds() // 60), 0)


# ------------------------------------------------------------------------- calendar


def events_between(start, end, user=None, school=None):
    sid = _school_id(school)
    query = CalendarEvent.query.filter(
        CalendarEvent.event_date >= start, CalendarEvent.event_date <= end
    )
    if sid:
        query = query.filter(CalendarEvent.school_id == sid)
    events = query.order_by(CalendarEvent.event_date, CalendarEvent.start_time).all()

    if user is None or not user.is_school_user or user.is_admin:
        return events

    if user.is_student:
        my_ids = {c.id for c in student_courses(user)}
        grade = user.grade_level
    elif user.is_teacher:
        my_ids = {c.id for c in teacher_courses(user)}
        grade = None
    else:  # parent — union across children
        my_ids = set()
        grade = None
        for child in user.children:
            my_ids |= {c.id for c in student_courses(child)}

    visible = []
    for event in events:
        if event.course_id and event.course_id not in my_ids:
            continue
        if event.grade_level and grade and event.grade_level != grade:
            continue
        visible.append(event)
    return visible


def month_grid(year, month):
    first = date(year, month, 1)
    lead = (first.weekday() + 1) % 7
    cursor = first - timedelta(days=lead)
    weeks = []
    while True:
        weeks.append([cursor + timedelta(days=i) for i in range(7)])
        cursor += timedelta(days=7)
        if cursor.month != month and cursor > first:
            break
    return weeks


# ----------------------------------------------------------------------- attendance


def attendance_summary(student, term=None):
    query = AttendanceRecord.query.filter(AttendanceRecord.student_id == student.id)
    if term:
        query = query.filter(
            AttendanceRecord.record_date >= term.start_date,
            AttendanceRecord.record_date <= term.end_date,
        )
    records = query.all()

    totals = {"present": 0, "absent": 0, "tardy": 0, "excused": 0}
    by_course = {}
    for record in records:
        totals[record.status] = totals.get(record.status, 0) + 1
        bucket = by_course.setdefault(
            record.course_id,
            {"course": record.course, "present": 0, "absent": 0, "tardy": 0, "excused": 0},
        )
        bucket[record.status] = bucket.get(record.status, 0) + 1

    total = sum(totals.values())
    rate = round((totals["present"] + totals["excused"]) / total * 100, 1) if total else 100.0

    for bucket in by_course.values():
        seen = sum(bucket[k] for k in ("present", "absent", "tardy", "excused"))
        bucket["total"] = seen
        bucket["rate"] = (
            round((bucket["present"] + bucket["excused"]) / seen * 100, 1) if seen else 100.0
        )

    return {
        "totals": totals,
        "total": total,
        "rate": rate,
        "by_course": sorted(by_course.values(), key=lambda b: b["course"].name),
        "recent": sorted(records, key=lambda r: r.record_date, reverse=True)[:15],
    }


def school_attendance_rate(on_date=None, school=None):
    on_date = on_date or date.today()
    sid = _school_id(school)
    query = (
        db.session.query(AttendanceRecord.status, func.count(AttendanceRecord.id))
        .join(Course, AttendanceRecord.course_id == Course.id)
        .filter(AttendanceRecord.record_date == on_date)
    )
    if sid:
        query = query.filter(Course.school_id == sid)
    counts = dict(query.group_by(AttendanceRecord.status).all())
    total = sum(counts.values())
    if not total:
        return None
    return round((counts.get("present", 0) + counts.get("excused", 0)) / total * 100, 1)


# --------------------------------------------------------------------------- grades


def course_grade(student, course):
    """A student's grade in one course.

    Honours the course's grading mode: straight points, or category weights when the
    course defines them.
    """
    grades = (
        Grade.query.join(Assignment)
        .filter(Assignment.course_id == course.id, Grade.student_id == student.id)
        .all()
    )
    counted = [g for g in grades if g.counts and g.effective_points is not None]

    missing = sum(1 for g in grades if g.status == "missing")
    ungraded = sum(1 for g in grades if g.status == "ungraded")

    if not counted:
        return {"course": course, "percent": None, "letter": "—", "earned": 0.0,
                "possible": 0.0, "counted": 0, "missing": missing,
                "ungraded": ungraded, "categories": []}

    category_rows = []
    percent = None

    if course.grading_mode == "weighted" and course.categories:
        weighted_total = 0.0
        weight_used = 0.0
        for category in course.categories:
            in_cat = [g for g in counted if g.assignment.category_id == category.id]
            if not in_cat:
                continue
            earned = sum(g.effective_points for g in in_cat)
            possible = sum(g.assignment.points_possible for g in in_cat)
            if not possible:
                continue
            cat_pct = earned / possible * 100
            category_rows.append({
                "name": category.name, "weight": category.weight,
                "percent": round(cat_pct, 1), "earned": earned, "possible": possible,
                "count": len(in_cat),
            })
            weighted_total += cat_pct * category.weight
            weight_used += category.weight
        if weight_used:
            percent = round(weighted_total / weight_used, 1)

    earned = sum(g.effective_points for g in counted)
    possible = sum(g.assignment.points_possible for g in counted)
    if percent is None:
        percent = round(earned / possible * 100, 1) if possible else None

    return {
        "course": course, "percent": percent, "letter": letter_for(percent),
        "earned": earned, "possible": possible, "counted": len(counted),
        "missing": missing, "ungraded": ungraded, "categories": category_rows,
    }


def _gpa_points(percent):
    return (4.0 if percent >= 90 else 3.0 if percent >= 80 else
            2.0 if percent >= 70 else 1.0 if percent >= 60 else 0.0)


def student_grade_report(student):
    """Course grades plus both GPA scales.

    Unweighted is the plain 4.0 scale. Weighted adds each course's rigor bonus (Honours
    +0.5, AP +1.0) — but only to a passing grade, since a failed AP course shouldn't
    outrank a passed regular one.
    """
    rows = [course_grade(student, course) for course in student_courses(student)]
    scored = [r["percent"] for r in rows if r["percent"] is not None]

    unweighted, weighted = [], []
    for row in rows:
        if row["percent"] is None:
            continue
        base = _gpa_points(row["percent"])
        unweighted.append(base)
        bonus = row["course"].gpa_bonus or 0.0
        weighted.append(base + bonus if base > 0 else base)

    return {
        "rows": rows,
        "average": round(sum(scored) / len(scored), 1) if scored else None,
        "gpa": round(sum(unweighted) / len(unweighted), 2) if unweighted else None,
        "gpa_weighted": round(sum(weighted) / len(weighted), 2) if weighted else None,
        "has_weighted": any((r["course"].gpa_bonus or 0) > 0 for r in rows),
        "missing_total": sum(r["missing"] for r in rows),
    }


def course_gradebook(course):
    """Assignments x students matrix for a teacher's gradebook."""
    assignments = (
        Assignment.query.filter_by(course_id=course.id)
        .order_by(Assignment.due_on.is_(None), Assignment.due_on, Assignment.id)
        .all()
    )
    roster = course.students
    existing = {
        (g.assignment_id, g.student_id): g
        for g in Grade.query.join(Assignment).filter(Assignment.course_id == course.id).all()
    }
    rows = []
    for student in roster:
        cells = [existing.get((a.id, student.id)) for a in assignments]
        rows.append({
            "student": student,
            "cells": cells,
            "summary": course_grade(student, course),
        })
    return {"assignments": assignments, "rows": rows}


def course_average(course):
    """Mean course grade across the roster, ignoring students with nothing graded."""
    scored = []
    for student in course.students:
        pct = course_grade(student, course)["percent"]
        if pct is not None:
            scored.append(pct)
    if not scored:
        return None
    return round(sum(scored) / len(scored), 1)


# ---------------------------------------------------------------------- assessments


def assessment_breakdown(assessment, school=None):
    """School / district / state averages for one assessment."""
    school = school or active_school()
    results = assessment.results

    def mean(values):
        values = [v for v in values if v is not None]
        return round(sum(values) / len(values), 1) if values else None

    school_scores, district_scores = [], []
    for result in results:
        student_school = result.student.school
        if student_school is None:
            continue
        if school and student_school.id == school.id:
            school_scores.append(result.score)
        if school and student_school.district_id == school.district_id:
            district_scores.append(result.score)

    school_avg = mean(school_scores)
    district_avg = mean(district_scores)
    proficient = [r for r in results
                  if school and r.student.school and r.student.school.id == school.id
                  and r.is_proficient]

    return {
        "assessment": assessment,
        "school_average": school_avg,
        "district_average": district_avg,
        "state_average": assessment.state_average,
        "school_n": len(school_scores),
        "district_n": len(district_scores),
        "proficient_rate": (
            round(len(proficient) / len(school_scores) * 100, 1) if school_scores else None
        ),
        "vs_district": (
            round(school_avg - district_avg, 1)
            if school_avg is not None and district_avg is not None else None
        ),
        "vs_state": (
            round(school_avg - assessment.state_average, 1)
            if school_avg is not None and assessment.state_average is not None else None
        ),
    }


def school_assessment_summary(school=None):
    school = school or active_school()
    return [assessment_breakdown(a, school)
            for a in Assessment.query.order_by(Assessment.administered_on.desc()).all()]


def course_assessment_summary(course):
    """Assessment averages for the students in one section — the teacher's view."""
    roster_ids = {s.id for s in course.students}
    if not roster_ids:
        return []

    summaries = []
    for assessment in Assessment.query.order_by(Assessment.administered_on.desc()).all():
        mine = [r for r in assessment.results if r.student_id in roster_ids]
        if not mine:
            continue
        scores = [r.score for r in mine]
        avg = round(sum(scores) / len(scores), 1)
        proficient = sum(1 for r in mine if r.is_proficient)
        summaries.append({
            "assessment": assessment,
            "average": avg,
            "count": len(mine),
            "proficient_rate": round(proficient / len(mine) * 100, 1),
            "state_average": assessment.state_average,
            "vs_state": (
                round(avg - assessment.state_average, 1)
                if assessment.state_average is not None else None
            ),
        })
    return summaries


# ------------------------------------------------------------------- at-risk signals


def student_risk(student, course=None, subject=None):
    """Flags suggesting a student needs extra support.

    Combines standardized test performance, course grade, missing work and attendance.

    When called for a specific course, only that course's own subject is considered —
    a maths teacher is shown maths scores, not an ELA result they can do nothing about.
    Pass `subject` to scope it explicitly; pass neither for the whole-student view.
    """
    reasons = []
    score = 0

    if subject is None and course is not None:
        subject = subject_for_department(course.department)

    results = AssessmentResult.query.filter_by(student_id=student.id).all()
    if subject:
        results = [r for r in results if r.assessment.subject == subject]

    below = [r for r in results if not r.is_proficient]
    if results and below:
        worst = min(below, key=lambda r: r.percent or 0)
        reasons.append(
            f"Below proficient on {worst.assessment.subject} "
            f"({worst.score:g}/{worst.assessment.max_score:g})"
        )
        score += 2 if len(below) > 1 else 1

    if course is not None:
        summary = course_grade(student, course)
        if summary["percent"] is not None and summary["percent"] < 70:
            reasons.append(f"Course grade {summary['percent']}% ({summary['letter']})")
            score += 2 if summary["percent"] < 60 else 1
        if summary["missing"] >= 2:
            reasons.append(f"{summary['missing']} missing assignments")
            score += 1
    else:
        report = student_grade_report(student)
        failing = [r for r in report["rows"]
                   if r["percent"] is not None and r["percent"] < 70]
        if failing:
            reasons.append(f"Below 70% in {len(failing)} course"
                           f"{'s' if len(failing) != 1 else ''}")
            score += 2 if len(failing) > 1 else 1
        if report["missing_total"] >= 3:
            reasons.append(f"{report['missing_total']} missing assignments")
            score += 1

    attendance = attendance_summary(student)
    if attendance["total"] and attendance["rate"] < 90:
        reasons.append(f"Attendance {attendance['rate']}%")
        score += 1

    level = "high" if score >= 3 else "watch" if score >= 1 else "none"
    return {"level": level, "score": score, "reasons": reasons}


def course_risk_list(course):
    """Roster entries flagged as needing support in THIS course, worst first.

    Scoped to the course's own subject, so what a teacher sees is something they can
    actually act on in their own room.
    """
    flagged = []
    for student in course.students:
        risk = student_risk(student, course)
        if risk["level"] != "none":
            flagged.append({"student": student, "risk": risk,
                            "grade": course_grade(student, course)})
    return sorted(flagged, key=lambda f: -f["risk"]["score"])


# -------------------------------------------------------------------- misc lookups


def active_window(school=None):
    sid = _school_id(school)
    query = SelectionWindow.query
    if sid:
        query = query.filter(SelectionWindow.school_id == sid)
    windows = query.order_by(SelectionWindow.opens_on.desc()).all()
    for window in windows:
        if window.accepting:
            return window
    return windows[0] if windows else None


# ------------------------------------------------------------ notification centre


def stored_notifications(user, unread_only=False, limit=None):
    query = Notification.query.filter_by(user_id=user.id)
    if unread_only:
        query = query.filter(Notification.read_at.is_(None))
    query = query.order_by(Notification.urgent.desc(), Notification.created_at.desc())
    if limit:
        query = query.limit(limit)
    return query.all()


def live_announcements(user, school=None):
    """School announcements this user is in the audience for."""
    school = school or (user.school if user.is_school_user else active_school())
    if school is None:
        return []
    rows = (
        Announcement.query.filter_by(school_id=school.id)
        .order_by(Announcement.urgent.desc(), Announcement.starts_on.desc())
        .all()
    )
    return [a for a in rows if a.reaches(user)]


def upcoming_tests(student, days=10):
    """Exam-category calendar events on the student's own courses or school-wide."""
    today = date.today()
    events = events_between(today, today + timedelta(days=days), student,
                            school=student.school)
    return [e for e in events if e.category == "exam"]


def _derived_items(user):
    """Reminders computed live rather than stored, so they're never stale."""
    items = []
    today = date.today()

    students = []
    if user.is_student:
        students = [user]
    elif user.is_parent:
        students = user.children

    for student in students:
        who = "" if user.is_student else f"{student.known_as}: "

        # Upcoming tests — the "you have a test soon, study" nudge.
        for event in upcoming_tests(student):
            days_out = (event.event_date - today).days
            when = ("today" if days_out == 0 else "tomorrow" if days_out == 1
                    else f"in {days_out} days")
            items.append({
                "kind": "reminder",
                "urgent": days_out <= 1,
                "title": f"{who}{event.title} is {when}",
                "body": (event.description or "")
                        + (" Time to start studying." if days_out > 1 else " Good luck!"),
                "link": url_for("calendar.day_detail", day=event.event_date.isoformat()),
                "at": datetime.combine(event.event_date, time(0, 0)),
                "sort": days_out,
            })

        # Missing work.
        report = student_grade_report(student)
        if report["missing_total"]:
            items.append({
                "kind": "assignment",
                "urgent": report["missing_total"] >= 3,
                "title": (f"{who}{report['missing_total']} missing "
                          f"assignment{'s' if report['missing_total'] != 1 else ''}"),
                "body": "Missing work counts as zero until it's turned in.",
                "link": url_for("assignments.my_assignments", show="missing")
                        if user.is_student else url_for("parents.child",
                                                        student_id=student.id),
                "at": datetime.now(),
                "sort": -1,
            })

        # Absence request outcomes.
        for req in AbsenceRequest.query.filter(
            AbsenceRequest.student_id == student.id,
            AbsenceRequest.status != "pending",
            AbsenceRequest.reviewed_at.isnot(None),
        ).order_by(AbsenceRequest.reviewed_at.desc()).limit(3).all():
            items.append({
                "kind": "absence",
                "urgent": False,
                "title": f"{who}Absence {req.decision_label.lower()} — {req.date_label}",
                "body": req.reviewer_note or "",
                "link": url_for("more.absences"),
                "at": req.reviewed_at,
                "sort": 5,
            })

    # Staff: absence requests waiting on them.
    if user.is_admin:
        pending = AbsenceRequest.query.filter_by(
            school_id=user.school_id, status="pending").count()
        if pending:
            items.append({
                "kind": "absence", "urgent": pending >= 5,
                "title": f"{pending} absence{'s' if pending != 1 else ''} awaiting a decision",
                "body": "Families are waiting to hear whether these are excused.",
                "link": url_for("more.absences"),
                "at": datetime.now(), "sort": -2,
            })

    # Teachers: ungraded work sitting in the gradebook.
    if user.is_teacher:
        course_ids = [c.id for c in teacher_courses(user)]
        if course_ids:
            ungraded = (
                Grade.query.join(Assignment)
                .filter(Assignment.course_id.in_(course_ids),
                        Grade.status == "ungraded",
                        Assignment.due_on <= today)
                .count()
            )
            if ungraded:
                items.append({
                    "kind": "grade", "urgent": False,
                    "title": f"{ungraded} submissions still ungraded",
                    "body": "Work that's past due and not yet scored.",
                    "link": url_for("grades.gradebook"),
                    "at": datetime.now(), "sort": 0,
                })

    return items


def read_announcement_ids(user):
    return {
        row.announcement_id
        for row in AnnouncementRead.query.filter_by(user_id=user.id).all()
    }


def notification_feed(user, school=None, limit=None):
    """Everything for the notification centre: announcements, stored notices, reminders.

    Announcements and derived reminders are computed rather than materialised, so they
    stay correct without a background job.
    """
    feed = []
    read_ids = read_announcement_ids(user)

    for announcement in live_announcements(user, school):
        is_read = announcement.id in read_ids
        feed.append({
            "kind": "announcement",
            "urgent": announcement.urgent and not is_read,
            "title": announcement.title,
            "body": announcement.body,
            "link": announcement.action_url or None,
            "action_label": announcement.action_label or None,
            "due_on": announcement.due_on,
            "at": datetime.combine(announcement.starts_on, time(8, 0)),
            "from": announcement.created_by.full_name if announcement.created_by else None,
            "announcement_id": announcement.id,
            "read": is_read,
            "sort": 1 if is_read else (-3 if announcement.urgent else 1),
        })

    for note in stored_notifications(user):
        feed.append({
            "kind": note.kind,
            "urgent": note.urgent,
            "title": note.title,
            "body": note.body,
            "link": note.link or None,
            "at": note.created_at,
            "from": note.created_by.full_name if note.created_by else None,
            "notification_id": note.id,
            "read": note.is_read,
            "sort": -2 if note.urgent else 2,
        })

    feed.extend(_derived_items(user))
    feed.sort(key=lambda i: (not i.get("urgent"), i.get("sort", 0),
                             -(i.get("at") or datetime.min).timestamp()))
    return feed[:limit] if limit else feed


def unread_count(user):
    """Badge count — only things the user can actually clear.

    Derived reminders (missing work, a test on Friday) are deliberately excluded: they
    have no stored row to dismiss, so counting them left a red dot that could never be
    cleared no matter how many times you pressed "mark all read". They still appear in
    the centre; they just don't nag on the bell.
    """
    stored = Notification.query.filter_by(user_id=user.id, read_at=None).count()
    read_ids = read_announcement_ids(user)
    unread_announcements = sum(
        1 for a in live_announcements(user) if a.id not in read_ids
    )
    return stored + unread_announcements


def notify(user, title, body="", kind="reminder", link="", urgent=False,
           created_by=None, announcement=None):
    """Create one stored notification. Caller commits."""
    note = Notification(
        user_id=user.id if hasattr(user, "id") else user,
        title=title[:180], body=body, kind=kind, link=link[:300] if link else "",
        urgent=urgent,
        created_by_id=created_by.id if created_by is not None else None,
        announcement_id=announcement.id if announcement is not None else None,
    )
    db.session.add(note)
    return note


def upcoming_assignments(student, days=14):
    today = date.today()
    course_ids = [c.id for c in student_courses(student)]
    if not course_ids:
        return []
    return (
        Assignment.query.filter(
            Assignment.course_id.in_(course_ids),
            Assignment.published.is_(True),
            Assignment.due_on >= today,
            Assignment.due_on <= today + timedelta(days=days),
        )
        .order_by(Assignment.due_on)
        .all()
    )
