"""Populate the database with a demo district, schools, and a full school year."""

import os
import random
from datetime import date, datetime, time, timedelta

from flask import current_app

from .extensions import db
from .models import (
    AbsenceRequest,
    Announcement,
    Assessment,
    AssessmentResult,
    Assignment,
    AttendanceRecord,
    HealthRecord,
    Notification,
    BellPeriod,
    BellSchedule,
    CalendarEvent,
    Course,
    CourseRequest,
    DemoRequest,
    District,
    Document,
    Enrollment,
    Grade,
    GradeCategory,
    ParentLink,
    Period,
    PurchaseRequest,
    School,
    SchoolDay,
    SelectionWindow,
    SupportSession,
    SupportSignup,
    Term,
    User,
)

RNG = random.Random(20260728)

# name, ordinal, default start, default end, kind
BELL = [
    ("Period 1", 1, time(8, 0), time(8, 50), "class"),
    ("Period 2", 2, time(8, 57), time(9, 47), "class"),
    ("Period 3", 3, time(9, 54), time(10, 44), "class"),
    ("Period 4", 4, time(10, 51), time(11, 41), "class"),
    ("Lunch", 5, time(11, 41), time(12, 16), "lunch"),
    ("Support", 6, time(12, 23), time(13, 3), "support"),
    ("Period 5", 7, time(13, 10), time(14, 0), "class"),
    ("Period 6", 8, time(14, 7), time(14, 57), "class"),
]

# Eastgate runs a different layout on most days — the point of day layouts.
EMS_SLOTS = [
    ("P1", 1, time(8, 0), time(8, 50), "class"),
    ("P2", 2, time(8, 57), time(9, 47), "class"),
    ("Break", 3, time(9, 47), time(10, 2), "break"),
    ("P3", 4, time(10, 9), time(10, 59), "class"),
    ("P4", 5, time(11, 6), time(11, 56), "class"),
    ("Lunch", 6, time(11, 56), time(12, 31), "lunch"),
    ("Flex", 7, time(12, 38), time(13, 13), "support"),
    ("P5", 8, time(13, 20), time(14, 10), "class"),
    ("P6", 9, time(14, 17), time(15, 7), "class"),
    ("P7", 10, time(15, 14), time(16, 4), "class"),
]

# layout name, weekday tokens, description, [(slot name, start, end), ...]
EMS_LAYOUTS = [
    ("Mon/Tue", "M,T", "Full seven-period day", [
        ("P1", time(8, 0), time(8, 50)),
        ("P2", time(8, 57), time(9, 47)),
        ("Break", time(9, 47), time(10, 2)),
        ("P3", time(10, 9), time(10, 59)),
        ("P4", time(11, 6), time(11, 56)),
        ("Lunch", time(11, 56), time(12, 31)),
        ("P5", time(12, 38), time(13, 28)),
        ("P6", time(13, 35), time(14, 25)),
        ("P7", time(14, 32), time(15, 22)),
    ]),
    ("Wednesday", "W", "Late start, even periods, flex block", [
        ("P2", time(9, 30), time(10, 40)),
        ("Break", time(10, 40), time(10, 55)),
        ("P4", time(11, 2), time(12, 12)),
        ("Lunch", time(12, 12), time(12, 47)),
        ("Flex", time(12, 54), time(13, 34)),
        ("P6", time(13, 41), time(14, 51)),
    ]),
    ("Thursday", "R", "Odd periods on a block", [
        ("P1", time(8, 0), time(9, 20)),
        ("Break", time(9, 20), time(9, 35)),
        ("P3", time(9, 42), time(11, 2)),
        ("Lunch", time(11, 2), time(11, 37)),
        ("P5", time(11, 44), time(13, 4)),
        ("P7", time(13, 11), time(14, 31)),
    ]),
    ("Friday", "F", "Full day with a flex block after break", [
        ("P1", time(8, 0), time(8, 45)),
        ("P2", time(8, 52), time(9, 37)),
        ("Break", time(9, 37), time(9, 52)),
        ("Flex", time(9, 59), time(10, 34)),
        ("P3", time(10, 41), time(11, 26)),
        ("P4", time(11, 33), time(12, 18)),
        ("Lunch", time(12, 18), time(12, 53)),
        ("P5", time(13, 0), time(13, 45)),
        ("P6", time(13, 52), time(14, 37)),
        ("P7", time(14, 44), time(15, 29)),
    ]),
]

TEACHERS = [
    ("Marcus", "Delacroix", "Mathematics"),
    ("Priya", "Raghunathan", "Science"),
    ("Elena", "Vasquez", "English"),
    ("Tobias", "Okonkwo", "Social Studies"),
    ("Hannah", "Lindqvist", "World Languages"),
    ("Devon", "Whitaker", "Fine Arts"),
    ("Naomi", "Ashford", "Technology"),
    ("Rafael", "Montoya", "Physical Education"),
]

# code, name, dept, teacher idx, period ordinal, capacity, credits, room, prereq
COURSES = [
    ("MTH210", "Algebra II", "Mathematics", 0, 1, 28, 1.0, "212", "Algebra I"),
    ("MTH310", "Pre-Calculus", "Mathematics", 0, 3, 26, 1.0, "212", "Algebra II"),
    ("MTH410", "AP Calculus AB", "Mathematics", 0, 7, 24, 1.0, "214", "Pre-Calculus"),
    ("SCI120", "Biology", "Science", 1, 1, 30, 1.0, "301", ""),
    ("SCI230", "Chemistry", "Science", 1, 2, 26, 1.0, "303", "Biology"),
    ("SCI340", "AP Physics 1", "Science", 1, 8, 22, 1.0, "305", "Algebra II"),
    ("ENG110", "English 9", "English", 2, 2, 30, 1.0, "108", ""),
    ("ENG320", "American Literature", "English", 2, 4, 28, 1.0, "108", "English 9"),
    ("ENG450", "AP English Literature", "English", 2, 7, 24, 1.0, "110", "American Literature"),
    ("HIS210", "World History", "Social Studies", 3, 3, 30, 1.0, "204", ""),
    ("HIS330", "US History", "Social Studies", 3, 4, 30, 1.0, "204", "World History"),
    ("HIS440", "AP Government", "Social Studies", 3, 8, 25, 0.5, "206", ""),
    ("SPN110", "Spanish I", "World Languages", 4, 2, 28, 1.0, "115", ""),
    ("SPN220", "Spanish II", "World Languages", 4, 7, 26, 1.0, "115", "Spanish I"),
    ("ART130", "Studio Art", "Fine Arts", 5, 1, 24, 0.5, "Art 1", ""),
    ("MUS150", "Concert Band", "Fine Arts", 5, 8, 40, 1.0, "Band", "Audition"),
    ("CSC210", "Intro to Computer Science", "Technology", 6, 3, 26, 1.0, "Lab B", ""),
    ("CSC340", "AP Computer Science A", "Technology", 6, 8, 24, 1.0, "Lab B", "Intro to CS"),
    ("PED100", "Physical Education", "Physical Education", 7, 1, 40, 0.5, "Gym", ""),
    ("HLT110", "Health", "Physical Education", 7, 4, 32, 0.5, "118", ""),
]

CATEGORIES = [("Tests", 0.40), ("Quizzes", 0.20), ("Homework", 0.25), ("Participation", 0.15)]

ASSIGNMENT_TITLES = {
    "Tests": ["Unit 1 Exam", "Unit 2 Exam", "Midterm Exam"],
    "Quizzes": ["Quiz 1", "Quiz 2", "Quiz 3", "Quiz 4"],
    "Homework": ["Problem Set 1", "Problem Set 2", "Problem Set 3", "Reading Response",
                 "Practice Set"],
    "Participation": ["Class Discussion", "Lab Participation"],
}

FIRST_NAMES = [
    "Amara", "Beckett", "Camila", "Desmond", "Elowen", "Finnegan", "Giselle", "Hendrix",
    "Imani", "Jasper", "Keiko", "Lucian", "Mireille", "Nikolai", "Ottoline", "Paloma",
    "Quentin", "Rosalind", "Soren", "Thandiwe", "Ulysses", "Verity", "Wren", "Xavier",
    "Yusuf", "Zadie", "Anouk", "Bodhi", "Cassius", "Delphine", "Ezra", "Fiona",
    "Gideon", "Harlow", "Isolde", "Joaquin", "Kalindi", "Leocadia", "Matteo", "Noor",
]
LAST_NAMES = [
    "Abernathy", "Bergstrom", "Castellanos", "Dubois", "Eriksen", "Fontaine", "Gallagher",
    "Hollingsworth", "Iversen", "Jayaraman", "Kowalczyk", "Lindgren", "Marchetti", "Nakamura",
    "Oyelaran", "Petrossian", "Quintero", "Rasmussen", "Sandoval", "Thibodeaux", "Ustinov",
    "Villanueva", "Waterhouse", "Ximenes", "Yamamoto", "Zielinski",
]

EVENTS = [
    ("Fall picture day", "activity", "Retakes scheduled in October."),
    ("Homecoming assembly", "assembly", "Periods shortened by 10 minutes."),
    ("Progress reports posted", "deadline", "Available in the portal by 4:00 PM."),
    ("Varsity soccer vs. Ridgeline", "sports", "Home field. Gates open at 5:30 PM."),
    ("Midterm exams begin", "exam", "Two exam blocks per day through Friday."),
    ("Teacher in-service — no school", "holiday", "Campus closed to students."),
    ("College application workshop", "activity", "Library, open to juniors and seniors."),
    ("Course selection opens", "deadline", "Submit 1st and 2nd picks in the portal."),
]

SUPPORT_OFFERINGS = [
    ("work", "Open Work Time", "Quiet room to finish assignments. Bring what you need."),
    ("taught", "Algebra II Re-teach", "Walkthrough of this week's unit for anyone who wants it."),
    ("taught", "Essay Workshop", "Bring a draft; we'll work through structure and thesis."),
    ("work", "Makeup Test Room", "Sit missed quizzes and tests here."),
    ("taught", "Lab Report Clinic", "Help writing up this week's lab."),
    ("work", "Reading Room", "Silent independent reading."),
]

ASSESSMENTS = [
    ("State Algebra I End-of-Course", "Mathematics", 500.0, 350.0, 372.4),
    ("State ELA Grade 10", "English Language Arts", 500.0, 350.0, 358.1),
    ("State Biology End-of-Course", "Science", 500.0, 350.0, 364.7),
    ("District Math Benchmark — Fall", "Mathematics", 100.0, 70.0, 71.5),
]

DEMO_SEEDS = [
    ("Danielle Okafor", "d.okafor@westbrookusd.org", "Westbrook Unified School District",
     "Director of Technology", "5,000–10,000", "scheduling, attendance", "contacted"),
    ("Peter Nyholm", "pnyholm@stagnes-academy.edu", "St. Agnes Academy",
     "Head of School", "500–1,000", "course selection", "new"),
    ("Rosa Villalobos", "rvillalobos@harborcity.k12.us", "Harbor City Schools",
     "Assistant Superintendent", "10,000+", "scheduling, reporting", "scheduled"),
    ("Ken Matsumoto", "k.matsumoto@pinecrest.org", "Pinecrest Charter Network",
     "Operations Manager", "1,000–5,000", "attendance", "new"),
]

PURCHASE_SEEDS = [
    ("Westbrook Unified School District", "Danielle Okafor", "d.okafor@westbrookusd.org",
     "district", 7400, "3 years", "purchase_order", "PO-2026-4417", "sso, import", "quoted"),
    ("St. Agnes Academy", "Peter Nyholm", "pnyholm@stagnes-academy.edu",
     "core", 620, "1 year", "credit_card", "", "training", "new"),
    ("Pinecrest Charter Network", "Ken Matsumoto", "k.matsumoto@pinecrest.org",
     "campus", 2150, "2 years", "purchase_order", "PCN-88213", "api", "invoiced"),
]

PLAN_PRICES = {"core": 4.50, "campus": 7.25, "district": 11.00}


def seed_database(reset=False):
    if reset:
        db.drop_all()
    db.create_all()

    if User.query.first() is not None and not reset:
        return

    today = date.today()

    # ------------------------------------------------------------ districts
    riverside = District(
        name="Riverside Unified School District", code="RUSD", state="CA",
        contact_name="Alicia Brennan", contact_email="abrennan@riversideusd.org",
        phone="(555) 402-1100",
    )
    lakeshore = District(
        name="Lakeshore County Schools", code="LCS", state="CA",
        contact_name="Gerald Pham", contact_email="gpham@lakeshore.k12.us",
        phone="(555) 771-8800",
    )
    db.session.add_all([riverside, lakeshore])
    db.session.flush()

    northfield = School(
        name="Northfield High School", code="NHS", district_id=riverside.id,
        city="Riverside", state="CA", address="1400 Northfield Ave",
        phone="(555) 402-1200", principal_name="Gwendolyn Fairbairn",
        low_grade=9, high_grade=12, rotation_mode="ab",
    )
    eastgate = School(
        name="Eastgate Middle School", code="EMS", district_id=riverside.id,
        city="Riverside", state="CA", address="88 Eastgate Rd",
        phone="(555) 402-1350", principal_name="Harold Nwosu",
        low_grade=6, high_grade=8, rotation_mode="daily",
        # A school that runs a different layout most days, calls its flex block
        # something else, and has no homerooms at all.
        support_label="Flex", uses_homeroom=False,
    )
    lakeshore_high = School(
        name="Lakeshore High School", code="LHS", district_id=lakeshore.id,
        city="Lakeshore", state="CA", address="9 Harbor Way",
        phone="(555) 771-8900", principal_name="Marta Quintanilla",
        low_grade=9, high_grade=12, rotation_mode="weekday",
    )
    db.session.add_all([northfield, eastgate, lakeshore_high])
    db.session.flush()

    # -------------------------------------------------------------- accounts
    def make(email, first, last, role, password, **kwargs):
        user = User(email=email, first_name=first, last_name=last, role=role, **kwargs)
        user.set_password(password)
        db.session.add(user)
        return user

    make("hq@homeroom.example", "Sasha", "Belmonte", "homeroom_staff", "homeroom1234",
         title="Account Executive")
    make("support@homeroom.example", "Owen", "Fitzgerald", "homeroom_staff", "homeroom1234",
         title="Implementation Manager")

    make("district@riversideusd.org", "Alicia", "Brennan", "district_admin", "district1234",
         district_id=riverside.id, title="Superintendent")
    make("district@lakeshore.k12.us", "Gerald", "Pham", "district_admin", "district1234",
         district_id=lakeshore.id, title="Superintendent")

    principal = make("principal@homeroom.edu", "Gwendolyn", "Fairbairn", "admin",
                     "admin1234", school_id=northfield.id, title="Principal",
                     department="Administration")
    make("registrar@homeroom.edu", "Samuel", "Adeyemi", "admin", "admin1234",
         school_id=northfield.id, title="Registrar", department="Registrar")
    make("principal@eastgate.edu", "Harold", "Nwosu", "admin", "admin1234",
         school_id=eastgate.id, title="Principal")
    make("principal@lakeshore.edu", "Marta", "Quintanilla", "admin", "admin1234",
         school_id=lakeshore_high.id, title="Principal")

    teachers = []
    for first, last, dept in TEACHERS:
        teachers.append(make(f"{first[0].lower()}{last.lower()}@homeroom.edu", first, last,
                             "teacher", "teach1234", school_id=northfield.id,
                             department=dept, title="Teacher"))
    db.session.flush()

    # -------------------------------------------------------------- periods
    def add_periods(school, spec=BELL):
        rows = []
        for name, ordinal, start, end, kind in spec:
            period = Period(school_id=school.id, name=name, ordinal=ordinal,
                            start_time=start, end_time=end, kind=kind)
            rows.append(period)
            db.session.add(period)
        return rows

    nhs_periods = add_periods(northfield)
    add_periods(lakeshore_high)
    # Eastgate gets its own slots and day layouts further down.
    db.session.flush()
    by_ordinal = {p.ordinal: p for p in nhs_periods}

    # Northfield keeps one layout for every day — the simple case.
    nhs_layout = BellSchedule(
        school_id=northfield.id, name="Regular Day",
        description="The standard bell schedule.",
        default_weekdays="M,T,W,R,F", is_default=True,
    )
    db.session.add(nhs_layout)
    db.session.flush()
    for index, period in enumerate(nhs_periods, start=1):
        db.session.add(BellPeriod(
            bell_schedule_id=nhs_layout.id, period_id=period.id, ordinal=index,
            start_time=period.start_time, end_time=period.end_time,
        ))
    db.session.flush()

    for school in (northfield, eastgate, lakeshore_high):
        db.session.add(Term(
            school_id=school.id, name="Semester 1",
            school_year=f"{today.year}–{today.year + 1}",
            start_date=today - timedelta(days=60), end_date=today + timedelta(days=110),
            is_current=True,
        ))
    db.session.flush()
    nhs_term = Term.query.filter_by(school_id=northfield.id).first()

    # -------------------------------------------------------------- students
    students, used = [], set()
    for i in range(80):
        while True:
            first, last = RNG.choice(FIRST_NAMES), RNG.choice(LAST_NAMES)
            if (first, last) not in used:
                used.add((first, last))
                break
        students.append(make(
            f"{first.lower()}.{last.lower()}@students.homeroom.edu", first, last,
            "student", "study1234", school_id=northfield.id,
            student_number=f"{20260100 + i}", grade_level=RNG.choice([9, 10, 11, 12]),
            homeroom=f"{RNG.choice('ABC')}-{RNG.randint(101, 128)}",
        ))

    avery = make("student@homeroom.edu", "Avery", "Delacroix-Nakamura", "student",
                 "study1234", school_id=northfield.id, student_number="20260001",
                 grade_level=11, homeroom="B-114", pronouns="they/them")
    students.insert(0, avery)
    db.session.flush()

    # Parents — every parent account is linked to at least one student.
    parent = make("parent@homeroom.edu", "Rowan", "Delacroix-Nakamura", "parent",
                  "parent1234", school_id=northfield.id, phone="(555) 402-7781")
    db.session.flush()
    db.session.add(ParentLink(parent_id=parent.id, student_id=avery.id,
                              relationship_label="Parent", is_primary=True))

    for student in RNG.sample(students[1:], 12):
        guardian = make(
            f"parent.{student.last_name.lower()}{student.id}@families.homeroom.edu",
            RNG.choice(FIRST_NAMES), student.last_name, "parent", "parent1234",
            school_id=northfield.id,
        )
        db.session.flush()
        db.session.add(ParentLink(parent_id=guardian.id, student_id=student.id,
                                  relationship_label="Guardian", is_primary=True))
    db.session.commit()

    # --------------------------------------------------------------- courses
    courses = []
    for code, name, dept, t_idx, p_ord, cap, credits, room, prereq in COURSES:
        days = RNG.choice(["A,B", "A,B", "A,B", "A", "B"])
        # AP and Honours courses carry a weighted-GPA bonus.
        if name.startswith("AP "):
            rigor, bonus = "ap", 1.0
        elif "Honors" in name or code in ("MTH310", "ENG320"):
            rigor, bonus = "honors", 0.5
        else:
            rigor, bonus = "regular", 0.0

        course = Course(
            school_id=northfield.id, code=code, name=name, department=dept,
            description=f"{name} with {teachers[t_idx].full_name} in room {room}.",
            teacher_id=teachers[t_idx].id, period_id=by_ordinal[p_ord].id,
            term_id=nhs_term.id, meeting_days=days, capacity=cap, credits=credits,
            room=room, prerequisite=prereq, rigor=rigor, gpa_bonus=bonus,
            grading_mode="weighted" if RNG.random() < 0.6 else "points",
        )
        courses.append(course)
        db.session.add(course)
    db.session.flush()

    for course in courses:
        if course.grading_mode == "weighted":
            for name, weight in CATEGORIES:
                db.session.add(GradeCategory(course_id=course.id, name=name, weight=weight))
    db.session.flush()

    # ----------------------------------------------------------- enrollments
    by_period = {}
    for course in courses:
        by_period.setdefault(course.period_id, []).append(course)
    schedulable = [pid for pid in by_period
                   if pid not in (by_ordinal[5].id, by_ordinal[6].id)]

    for student in students:
        RNG.shuffle(schedulable)
        for period_id in schedulable[:6]:
            options = [c for c in by_period[period_id] if not c.is_full]
            if options:
                db.session.add(Enrollment(student_id=student.id,
                                          course_id=RNG.choice(options).id))
                db.session.flush()
    db.session.commit()

    # ------------------------------------------------------- rotation calendar
    for school in (northfield, eastgate, lakeshore_high):
        tokens = school.rotation_tokens or ["A"]
        cursor, index = today - timedelta(days=60), 0
        while cursor <= today + timedelta(days=110):
            if cursor.weekday() < 5:
                db.session.add(SchoolDay(
                    school_id=school.id, day_date=cursor,
                    day_type=tokens[index % len(tokens)] if tokens else "A",
                    in_session=True,
                ))
                index += 1
            cursor += timedelta(days=1)
    db.session.flush()

    for offset in (18, 45):
        holiday = SchoolDay.query.filter_by(school_id=northfield.id,
                                            day_date=today + timedelta(days=offset)).first()
        if holiday:
            holiday.in_session = False
            holiday.note = "No school — professional development"
    db.session.commit()

    # ---------------------------------------------------------------- events
    for title, category, description in EVENTS:
        event_date = today + timedelta(days=RNG.randint(-25, 60))
        all_day = category in ("holiday", "deadline")
        db.session.add(CalendarEvent(
            school_id=northfield.id, title=title, description=description,
            event_date=event_date, category=category, all_day=all_day,
            start_time=None if all_day else time(RNG.choice([9, 13, 18, 19]), 0),
            end_time=None if all_day else time(RNG.choice([10, 14, 20, 21]), 30),
            created_by_id=principal.id,
        ))
    for course in RNG.sample(courses, 6):
        db.session.add(CalendarEvent(
            school_id=northfield.id, title=f"{course.code} unit test",
            description=f"Covers the current unit in {course.name}.",
            event_date=today + timedelta(days=RNG.randint(2, 21)),
            category="exam", all_day=True, course_id=course.id,
            created_by_id=course.teacher_id,
        ))
    db.session.commit()

    # ----------------------------------------------------- assignments + grades
    for course in courses:
        cats = {c.name: c for c in course.categories}
        for cat_name, titles in ASSIGNMENT_TITLES.items():
            for title in RNG.sample(titles, RNG.randint(1, len(titles))):
                assigned = today - timedelta(days=RNG.randint(3, 50))
                due = assigned + timedelta(days=RNG.randint(3, 10))
                points = 100.0 if cat_name == "Tests" else RNG.choice([10.0, 20.0, 25.0, 50.0])
                assignment = Assignment(
                    course_id=course.id, title=f"{title}",
                    description=f"{title} for {course.name}.",
                    points_possible=points,
                    category_id=cats[cat_name].id if cat_name in cats else None,
                    assigned_on=assigned, due_on=due, published=True, graded=True,
                    created_by_id=course.teacher_id,
                )
                db.session.add(assignment)
                db.session.flush()

                for student in course.students:
                    if due > today:
                        db.session.add(Grade(assignment_id=assignment.id,
                                             student_id=student.id, status="ungraded"))
                        continue
                    roll = RNG.random()
                    if roll < 0.06:
                        db.session.add(Grade(assignment_id=assignment.id,
                                             student_id=student.id, status="missing"))
                    elif roll < 0.09:
                        db.session.add(Grade(assignment_id=assignment.id,
                                             student_id=student.id, status="excused"))
                    else:
                        base = RNG.gauss(0.85, 0.13)
                        earned = round(max(0.35, min(base, 1.0)) * points, 1)
                        db.session.add(Grade(
                            assignment_id=assignment.id, student_id=student.id,
                            points_earned=earned, status="graded",
                            graded_by_id=course.teacher_id,
                            graded_at=datetime.now() - timedelta(days=RNG.randint(1, 20)),
                        ))
        db.session.flush()
    db.session.commit()

    # -------------------------------------------------------------- attendance
    session_days = (
        SchoolDay.query.filter(SchoolDay.school_id == northfield.id,
                               SchoolDay.day_date <= today,
                               SchoolDay.in_session.is_(True))
        .order_by(SchoolDay.day_date.desc()).limit(20).all()
    )
    statuses = ["present", "absent", "tardy", "excused"]
    weights = [0.90, 0.04, 0.04, 0.02]
    for school_day in session_days:
        for course in courses:
            if not course.meets_on(school_day.day_type, weekday=school_day.day_date.weekday()):
                continue
            for enrollment in course.enrollments:
                status = RNG.choices(statuses, weights)[0]
                db.session.add(AttendanceRecord(
                    student_id=enrollment.student_id, course_id=course.id,
                    record_date=school_day.day_date, status=status,
                    note="Parent note on file" if status == "excused" else "",
                    recorded_by_id=course.teacher_id,
                ))
        db.session.flush()
    db.session.commit()

    # ------------------------------------------------------------- assessments
    all_students = students
    for name, subject, max_score, cutoff, state_avg in ASSESSMENTS:
        assessment = Assessment(
            name=name, subject=subject, max_score=max_score,
            proficient_cutoff=cutoff, state_average=state_avg,
            administered_on=today - timedelta(days=RNG.randint(30, 90)),
            term_label="Spring 2026",
        )
        db.session.add(assessment)
        db.session.flush()
        for student in all_students:
            score = RNG.gauss(state_avg * 1.02, max_score * 0.11)
            db.session.add(AssessmentResult(
                assessment_id=assessment.id, student_id=student.id,
                score=round(max(max_score * 0.3, min(score, max_score)), 1),
            ))
        db.session.flush()
    db.session.commit()

    # ---------------------------------------------------------------- support
    support_period = by_ordinal[6]
    for offset in range(0, 7):
        day = today + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        chosen = RNG.sample(SUPPORT_OFFERINGS, 5)
        sessions = []
        for i, (kind, name, description) in enumerate(chosen):
            session_row = SupportSession(
                school_id=northfield.id, teacher_id=teachers[i % len(teachers)].id,
                period_id=support_period.id, session_date=day, name=name,
                description=description, kind=kind,
                capacity=RNG.choice([15, 20, 25]),
                location=RNG.choice(["212", "301", "108", "Lab B", "Library"]),
            )
            sessions.append(session_row)
            db.session.add(session_row)
        db.session.flush()

        for student in RNG.sample(students, 45):
            target = RNG.choice(sessions)
            if target.is_full:
                continue
            db.session.add(SupportSignup(session_id=target.id, student_id=student.id))
        db.session.flush()

        # One locked placement so the "can't get out of it" rule is visible.
        if offset == 0 and sessions:
            makeup = next((s for s in sessions if s.kind == "work"), sessions[0])
            existing = SupportSignup.query.filter_by(student_id=avery.id).join(
                SupportSession).filter(SupportSession.session_date == day).first()
            if existing:
                db.session.delete(existing)
                db.session.flush()
            db.session.add(SupportSignup(
                session_id=makeup.id, student_id=avery.id, locked=True,
                assigned_by_id=principal.id,
                note="Assigned by the office to make up a missed assessment.",
            ))
    db.session.commit()

    # -------------------------------------------------------- course selection
    window = SelectionWindow(
        school_id=northfield.id,
        name=f"{today.year + 1}–{today.year + 2} Course Selection",
        opens_on=today - timedelta(days=5), closes_on=today + timedelta(days=25),
        required_slots=6, is_open=True,
        instructions=(
            "Rank six course choices. For each choice, submit a 1st pick and a 2nd pick "
            "alternate in case your first pick fills or conflicts with your schedule. "
            "Your counselor reviews every request before it becomes a seat."
        ),
    )
    db.session.add(window)
    db.session.flush()

    selectable = [c for c in courses if c.selectable]
    for student in RNG.sample(students, 45):
        picks = RNG.sample(selectable, 12)
        for slot in range(1, 7):
            first, second = picks[(slot - 1) * 2], picks[(slot - 1) * 2 + 1]
            status = RNG.choices(["pending", "approved", "denied"], [0.6, 0.32, 0.08])[0]
            db.session.add(CourseRequest(student_id=student.id, course_id=first.id,
                                         window_id=window.id, slot=slot, rank=1,
                                         status=status))
            db.session.add(CourseRequest(student_id=student.id, course_id=second.id,
                                         window_id=window.id, slot=slot, rank=2))
    db.session.commit()

    # --------------------------------------- announcements, absences, health
    _seed_office_data(northfield, principal, students, courses)

    # -------------------------------------------- Eastgate: per-day layouts
    _seed_eastgate(eastgate)

    # -------------------------------------------------------------- documents
    _seed_documents(northfield, principal, courses[0])

    # ----------------------------------------------------------- sales pipeline
    for i, (name, email, org, title, size, interests, status) in enumerate(DEMO_SEEDS):
        db.session.add(DemoRequest(
            name=name, email=email, organization=org, job_title=title,
            student_count=size, interests=interests, status=status,
            preferred_date=today + timedelta(days=7 + i * 3),
            message="Looking to replace our current SIS before the next school year.",
            submitted_at=datetime.now() - timedelta(days=12 - i * 3),
        ))
    for i, (org, contact, email, plan, seats, term_len, method, po, add_ons, status) in \
            enumerate(PURCHASE_SEEDS):
        db.session.add(PurchaseRequest(
            organization=org, contact_name=contact, contact_email=email, plan=plan,
            seats=seats, term_length=term_len, payment_method=method, po_number=po,
            add_ons=add_ons, status=status,
            # Left unset on purpose — a quote is a real commitment, not demo filler.
            quoted_total=None,
            billing_address="Attn: Business Office",
            submitted_at=datetime.now() - timedelta(days=9 - i * 3),
        ))
    db.session.commit()


def _seed_office_data(school, principal, students, courses):
    """Announcements, absence requests and a few health records."""
    today = date.today()

    announcements = [
        ("Field trip permission forms due Friday",
         "Grade 11 forms for the museum trip must be returned to the front office by "
         "Friday afternoon. Students without a signed form can't board the bus.",
         "families", True, "Open the form", "/app/documents/", 5),
        ("Picture retakes next Tuesday",
         "Retakes run during all lunch periods in the auditorium. Bring your original "
         "photo package if you have one.", "students", False, "", "", None),
        ("Parent-teacher conferences open for booking",
         "Slots are available the week after next. Book through the front office.",
         "parents", False, "", "", 12),
        ("Staff meeting moved to Thursday",
         "The Wednesday meeting is moving to Thursday at 3:30 in the library.",
         "staff", False, "", "", None),
        ("Early dismissal on the last day of the term",
         "School ends at 12:15. Buses run on the early schedule.",
         "all", False, "", "", None),
    ]

    for title, body, audience, urgent, label, url, due_in in announcements:
        db.session.add(Announcement(
            school_id=school.id, title=title, body=body, audience=audience,
            urgent=urgent, action_label=label, action_url=url,
            due_on=today + timedelta(days=due_in) if due_in else None,
            starts_on=today - timedelta(days=RNG.randint(0, 4)),
            expires_on=today + timedelta(days=30),
            created_by_id=principal.id,
        ))
    db.session.flush()

    # A couple of personal notifications so the centre isn't only announcements.
    avery = next((s for s in students if s.email == "student@homeroom.edu"), students[0])
    db.session.add(Notification(
        user_id=avery.id, kind="support",
        title="You've been placed in a support session",
        body="The office assigned you to make up a missed assessment.",
        link="/app/support/choose", created_by_id=principal.id,
    ))

    # Absences: reports are always in the past, planned requests always in the future.
    filings = [
        ("report", "illness", -3, "Fever and sore throat, kept home."),
        ("report", "illness", -8, ""),
        ("report", "emergency", -1, "Family emergency, back tomorrow."),
        ("report", "bereavement", -12, "Funeral out of state."),
        ("request", "appointment", 4, "Orthodontist at 9am, in by second period."),
        ("request", "travel", 11, "Family wedding abroad."),
        ("request", "college_visit", 7, "Campus tour and interview."),
        ("request", "religious", 16, ""),
    ]

    for index, (student, (kind, reason, offset, detail)) in enumerate(
            zip(RNG.sample(students, len(filings)), filings)):
        start = today + timedelta(days=offset)
        status = ["approved", "pending", "approved", "denied"][index % 4]
        row = AbsenceRequest(
            school_id=school.id, student_id=student.id, submitted_by_id=student.id,
            kind=kind, start_date=start,
            end_date=start + timedelta(days=RNG.choice([0, 0, 1, 2])),
            reason=reason, detail=detail, status=status,
        )
        if status != "pending":
            row.reviewed_by_id = principal.id
            row.reviewed_at = datetime.now() - timedelta(days=1)
            if kind == "report":
                row.reviewer_note = ("Excused." if status == "approved"
                                     else "No note from home received.")
            else:
                row.reviewer_note = ("Approved — please collect work in advance."
                                     if status == "approved"
                                     else "Too close to exams; please reschedule.")
        db.session.add(row)

    # Health records for a handful of students.
    health = [
        ("Peanuts, tree nuts", "EpiPen", "Anaphylaxis", True,
         "EpiPen in the nurse's office and in the classroom kit."),
        ("", "Albuterol inhaler", "Asthma", True,
         "Inhaler carried by student; spare in nurse's office."),
        ("Penicillin", "", "", False, ""),
        ("", "", "Type 1 diabetes", True,
         "Glucose monitor; snacks permitted in class."),
        ("Latex", "", "", False, ""),
    ]
    for student, (allergies, meds, conditions, plan, note) in zip(
            RNG.sample(students, len(health)), health):
        db.session.add(HealthRecord(
            student_id=student.id, allergies=allergies, medications=meds,
            conditions=conditions, has_action_plan=plan, action_plan_note=note,
            physician_name="Dr. Imogen Halloway", physician_phone="(555) 402-9900",
            insurance_provider="Statewide Health", updated_by_id=principal.id,
        ))

    # Demographics for everyone, so the office screen isn't empty.
    languages = ["English", "English", "English", "Spanish", "Mandarin",
                 "Tagalog", "Vietnamese", "Portuguese"]
    counselors = ["A. Reyes", "M. Okafor", "T. Lindholm"]
    for index, student in enumerate(students):
        student.home_language = languages[index % len(languages)]
        student.counselor = counselors[index % len(counselors)]
        student.locker = str(1000 + index)
        student.bus_route = f"R{(index % 6) + 1}"
        student.enrolled_on = today - timedelta(days=RNG.randint(200, 1200))
        student.has_iep = index % 17 == 0
        student.has_504 = index % 23 == 0

    db.session.commit()


def _seed_eastgate(school):
    """A school whose bell schedule differs by day — the four-layout example.

    Mon/Tue run all seven periods; Wednesday is a late start on even periods with a flex
    block; Thursday is odd periods on a block; Friday is a full day with flex after break.
    It also has no homerooms, so that setting is visible somewhere too.
    """
    slots = {}
    for name, ordinal, start, end, kind in EMS_SLOTS:
        period = Period(school_id=school.id, name=name, ordinal=ordinal,
                        start_time=start, end_time=end, kind=kind)
        db.session.add(period)
        slots[name] = period
    db.session.flush()

    for name, weekdays, description, entries in EMS_LAYOUTS:
        layout = BellSchedule(
            school_id=school.id, name=name, description=description,
            default_weekdays=weekdays, is_default=(name == "Mon/Tue"),
        )
        db.session.add(layout)
        db.session.flush()
        for index, (slot_name, start, end) in enumerate(entries, start=1):
            db.session.add(BellPeriod(
                bell_schedule_id=layout.id, period_id=slots[slot_name].id,
                ordinal=index, start_time=start, end_time=end,
            ))
    db.session.flush()

    # The principal already exists from the main account block.
    teachers = []
    for first, last, dept in [
        ("Colette", "Ferreira", "Mathematics"), ("Amos", "Bricklebank", "Science"),
        ("Petra", "Halvorsen", "English"), ("Emeka", "Adeniyi", "Social Studies"),
    ]:
        teacher = User(
            email=f"{first[0].lower()}{last.lower()}@eastgate.edu",
            first_name=first, last_name=last, role="teacher",
            school_id=school.id, department=dept, title="Teacher",
        )
        teacher.set_password("teach1234")
        teachers.append(teacher)
        db.session.add(teacher)
    db.session.flush()

    # One section per class slot, so every layout visibly changes the day.
    class_slots = [s for s in EMS_SLOTS if s[4] == "class"]
    ems_courses = []
    for index, (slot_name, _, _, _, _) in enumerate(class_slots):
        subject, code, dept = [
            ("Math 7", "MTH7", "Mathematics"), ("Life Science", "SCI7", "Science"),
            ("English 7", "ENG7", "English"), ("World Cultures", "SOC7", "Social Studies"),
            ("Pre-Algebra", "MTH7B", "Mathematics"), ("Earth Science", "SCI7B", "Science"),
            ("Reading Workshop", "ENG7B", "English"),
        ][index % 7]
        course = Course(
            school_id=school.id, code=f"{code}-{slot_name}", name=subject,
            department=dept, description=f"{subject} in {slot_name}.",
            teacher_id=teachers[index % len(teachers)].id,
            period_id=slots[slot_name].id, meeting_days="ALL",
            capacity=28, credits=1.0, room=f"{100 + index}",
        )
        ems_courses.append(course)
        db.session.add(course)
    db.session.flush()

    students = []
    for i in range(24):
        first = FIRST_NAMES[(i * 3) % len(FIRST_NAMES)]
        last = LAST_NAMES[(i * 5) % len(LAST_NAMES)]
        student = User(
            email=f"{first.lower()}.{last.lower()}{i}@students.eastgate.edu",
            first_name=first, last_name=last, role="student", school_id=school.id,
            student_number=f"{20267000 + i}", grade_level=RNG.choice([6, 7, 8]),
        )
        student.set_password("study1234")
        students.append(student)
        db.session.add(student)
    db.session.flush()

    for student in students:
        for course in ems_courses:
            if not course.is_full:
                db.session.add(Enrollment(student_id=student.id, course_id=course.id))
        db.session.flush()

    db.session.commit()


def _seed_documents(school, author, course):
    """Write a couple of real files so downloads work out of the box."""
    upload_dir = os.path.join(current_app.instance_path, "uploads")
    os.makedirs(upload_dir, exist_ok=True)

    samples = [
        ("Student Handbook 2026-2027", "handbook",
         "Attendance, conduct and grading policies for the school year.",
         "student-handbook.txt",
         "NORTHFIELD HIGH SCHOOL — STUDENT HANDBOOK\n\n"
         "1. Attendance\n   Students are expected in every scheduled class.\n"
         "2. Grading\n   Courses report on a weighted-category basis.\n"
         "3. Support Block\n   Students choose a support session daily unless placed.\n",
         None),
        ("Field Trip Permission Form", "form",
         "Return a signed copy to the main office before the trip.",
         "permission-form.txt",
         "FIELD TRIP PERMISSION FORM\n\nStudent name: ______________________\n"
         "Guardian signature: ________________\nDate: ____________\n",
         None),
        (f"{course.code} Syllabus", "syllabus",
         f"Course outline and grading breakdown for {course.name}.",
         "syllabus.txt",
         f"{course.code} — {course.name}\n\nGrading: Tests 40%, Quizzes 20%, "
         "Homework 25%, Participation 15%.\n",
         course.id),
    ]

    for title, category, description, filename, body, course_id in samples:
        stored = f"seed_{filename}"
        path = os.path.join(upload_dir, stored)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        db.session.add(Document(
            school_id=school.id, title=title, description=description, category=category,
            stored_name=stored, original_name=filename, content_type="text/plain",
            size_bytes=len(body.encode("utf-8")), course_id=course_id,
            uploaded_by_id=author.id,
        ))
    db.session.commit()
