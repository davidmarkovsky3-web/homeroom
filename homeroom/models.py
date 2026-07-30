"""Database models for the Homeroom SIS.

Tenancy runs District -> School -> everything else. A user belongs to a school (students,
teachers, school admins), to a district (district admins), or to neither (Homeroom's own
staff, who work across all tenants).
"""

from datetime import date, datetime, time

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db, login_manager

ROLE_ADMIN = "admin"
ROLE_TEACHER = "teacher"
ROLE_STUDENT = "student"
ROLE_PARENT = "parent"
ROLE_DISTRICT = "district_admin"
ROLE_STAFF = "homeroom_staff"

ROLES = (ROLE_STUDENT, ROLE_PARENT, ROLE_TEACHER, ROLE_ADMIN, ROLE_DISTRICT, ROLE_STAFF)

ROLE_LABELS = {
    ROLE_STUDENT: "Student",
    ROLE_PARENT: "Parent / Guardian",
    ROLE_TEACHER: "Teacher",
    ROLE_ADMIN: "School Administrator",
    ROLE_DISTRICT: "District Administrator",
    ROLE_STAFF: "Homeroom Staff",
}

ATTENDANCE_STATUSES = ("present", "absent", "tardy", "excused")
GRADE_STATUSES = ("graded", "ungraded", "missing", "excused", "late")

# How a school's timetable repeats. Schools differ a lot here, so it's per-school
# configuration rather than something baked into the scheduling code.
ROTATION_DAILY = "daily"        # every section meets every school day
ROTATION_AB = "ab"              # A/B block rotation
ROTATION_WEEKDAY = "weekday"    # sections meet on named weekdays (MWF, TR, ...)
ROTATION_CYCLE = "cycle"        # numbered cycle, Day 1..N

ROTATION_MODES = {
    ROTATION_DAILY: {
        "label": "Same every day",
        "hint": "Every section meets on every school day. The simplest setup.",
        "tokens": ["ALL"],
    },
    ROTATION_AB: {
        "label": "A / B block rotation",
        "hint": "Alternating A and B days. A section can meet on A days, B days, or both.",
        "tokens": ["A", "B"],
    },
    ROTATION_WEEKDAY: {
        "label": "Fixed weekdays",
        "hint": "Sections meet on set weekdays, e.g. Mon/Wed/Fri or Tue/Thu.",
        "tokens": ["M", "T", "W", "R", "F"],
    },
    ROTATION_CYCLE: {
        "label": "Numbered day cycle",
        "hint": "A repeating cycle of numbered days (Day 1 … Day N) that ignores weekends.",
        "tokens": [],  # generated from the school's cycle_length
    },
}

WEEKDAY_TOKENS = {0: "M", 1: "T", 2: "W", 3: "R", 4: "F", 5: "S", 6: "U"}
WEEKDAY_NAMES = {"M": "Monday", "T": "Tuesday", "W": "Wednesday",
                 "R": "Thursday", "F": "Friday", "S": "Saturday", "U": "Sunday"}
WEEKDAY_ORDER = ["M", "T", "W", "R", "F"]

# What a slot in the bell schedule is for. Only "class" slots hold course sections.
PERIOD_CLASS = "class"
PERIOD_SUPPORT = "support"
PERIOD_BREAK = "break"
PERIOD_LUNCH = "lunch"

PERIOD_KINDS = {
    PERIOD_CLASS: "Class period",
    PERIOD_SUPPORT: "Support / flex block",
    PERIOD_BREAK: "Break",
    PERIOD_LUNCH: "Lunch",
}


def format_range(start, end):
    if not start or not end:
        return "—"
    return f"{start.strftime('%I:%M').lstrip('0')} – {end.strftime('%I:%M %p').lstrip('0')}"

LETTER_SCALE = [
    (97, "A+"), (93, "A"), (90, "A-"),
    (87, "B+"), (83, "B"), (80, "B-"),
    (77, "C+"), (73, "C"), (70, "C-"),
    (67, "D+"), (63, "D"), (60, "D-"),
    (0, "F"),
]


def letter_for(percent):
    if percent is None:
        return "—"
    for floor, letter in LETTER_SCALE:
        if percent >= floor:
            return letter
    return "F"


class District(db.Model):
    __tablename__ = "districts"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)
    state = db.Column(db.String(40), default="")
    contact_name = db.Column(db.String(120), default="")
    contact_email = db.Column(db.String(160), default="")
    phone = db.Column(db.String(40), default="")
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    schools = db.relationship("School", back_populates="district",
                              cascade="all, delete-orphan")

    @property
    def student_count(self):
        return sum(s.student_count for s in self.schools)

    def __repr__(self):
        return f"<District {self.code}>"


class School(db.Model):
    __tablename__ = "schools"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)
    district_id = db.Column(db.Integer, db.ForeignKey("districts.id"), nullable=False)

    address = db.Column(db.String(200), default="")
    city = db.Column(db.String(80), default="")
    state = db.Column(db.String(40), default="")
    phone = db.Column(db.String(40), default="")
    principal_name = db.Column(db.String(120), default="")
    low_grade = db.Column(db.Integer, default=9)
    high_grade = db.Column(db.Integer, default=12)

    # Timetable shape — see ROTATION_MODES.
    rotation_mode = db.Column(db.String(20), nullable=False, default=ROTATION_DAILY)
    cycle_length = db.Column(db.Integer, nullable=False, default=6)

    # Schools call the flex block different things — Support, Advisory, FLEX, Homeroom.
    support_label = db.Column(db.String(40), nullable=False, default="Support")

    # Plenty of schools have no homeroom/form-group concept at all. When this is off,
    # the field disappears from every screen and import rather than sitting there empty.
    uses_homeroom = db.Column(db.Boolean, nullable=False, default=True)
    homeroom_label = db.Column(db.String(40), nullable=False, default="Homeroom")

    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    district = db.relationship("District", back_populates="schools")
    users = db.relationship("User", back_populates="school")
    courses = db.relationship("Course", back_populates="school",
                              cascade="all, delete-orphan")
    periods = db.relationship("Period", back_populates="school",
                              cascade="all, delete-orphan")
    bell_schedules = db.relationship("BellSchedule", back_populates="school",
                                     cascade="all, delete-orphan")

    @property
    def grade_range(self):
        return [g for g in range(self.low_grade or 9, (self.high_grade or 12) + 1)]

    @property
    def uses_rotation(self):
        """Whether day types mean anything here."""
        return self.rotation_mode in (ROTATION_AB, ROTATION_CYCLE)

    @property
    def rotation_label(self):
        return ROTATION_MODES.get(self.rotation_mode, {}).get("label", self.rotation_mode)

    @property
    def rotation_tokens(self):
        """The set of meeting-day tokens a course may be assigned at this school."""
        if self.rotation_mode == ROTATION_CYCLE:
            return [str(n) for n in range(1, (self.cycle_length or 6) + 1)]
        return ROTATION_MODES.get(self.rotation_mode, {}).get("tokens", [])

    def token_label(self, token):
        if self.rotation_mode == ROTATION_DAILY:
            return "Every day"
        if self.rotation_mode == ROTATION_AB:
            return f"{token} Day"
        if self.rotation_mode == ROTATION_WEEKDAY:
            return WEEKDAY_NAMES.get(token, token)
        return f"Day {token}"

    def day_label(self, day_type):
        """Human label for a scheduled day's type."""
        if day_type is None:
            return "No school"
        if self.rotation_mode == ROTATION_DAILY:
            return "School day"
        return self.token_label(day_type)

    @property
    def student_count(self):
        return sum(1 for u in self.users if u.role == ROLE_STUDENT and u.active)

    @property
    def staff_count(self):
        return sum(1 for u in self.users if u.role in (ROLE_TEACHER, ROLE_ADMIN) and u.active)

    def __repr__(self):
        return f"<School {self.code}>"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_STUDENT)

    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)

    # Tenancy. Students/teachers/school admins have a school; district admins have a
    # district; Homeroom staff have neither.
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), index=True)
    district_id = db.Column(db.Integer, db.ForeignKey("districts.id"), index=True)

    student_number = db.Column(db.String(20), index=True)
    grade_level = db.Column(db.Integer)
    homeroom = db.Column(db.String(20))
    department = db.Column(db.String(80))
    title = db.Column(db.String(80))

    # Profile / contact, editable from Account settings.
    preferred_name = db.Column(db.String(80), default="")
    pronouns = db.Column(db.String(40), default="")
    phone = db.Column(db.String(40), default="")
    address = db.Column(db.String(200), default="")
    birthdate = db.Column(db.Date)
    emergency_contact_name = db.Column(db.String(120), default="")
    emergency_contact_phone = db.Column(db.String(40), default="")
    emergency_contact_relation = db.Column(db.String(60), default="")

    # Demographics the office keeps on file. Staff-visible, not shown to other students.
    home_language = db.Column(db.String(60), default="")
    enrolled_on = db.Column(db.Date)
    counselor = db.Column(db.String(120), default="")
    locker = db.Column(db.String(20), default="")
    bus_route = db.Column(db.String(20), default="")
    has_iep = db.Column(db.Boolean, nullable=False, default=False)
    has_504 = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.Text, default="")

    # Notification preferences.
    notify_grades = db.Column(db.Boolean, nullable=False, default=True)
    notify_attendance = db.Column(db.Boolean, nullable=False, default=True)
    notify_assignments = db.Column(db.Boolean, nullable=False, default=True)
    notify_announcements = db.Column(db.Boolean, nullable=False, default=True)
    notify_digest = db.Column(db.String(20), nullable=False, default="daily")

    active = db.Column(db.Boolean, nullable=False, default=True)
    # Set on bulk-imported and admin-created accounts: the holder is forced to pick a
    # new password before they can use anything else.
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    last_login_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    school = db.relationship("School", back_populates="users", foreign_keys=[school_id])
    district = db.relationship("District", foreign_keys=[district_id])

    taught_courses = db.relationship(
        "Course", back_populates="teacher", foreign_keys="Course.teacher_id"
    )
    enrollments = db.relationship(
        "Enrollment", back_populates="student", cascade="all, delete-orphan"
    )
    course_requests = db.relationship(
        "CourseRequest", back_populates="student", cascade="all, delete-orphan"
    )
    # Links where this user is the parent.
    parent_links = db.relationship(
        "ParentLink", back_populates="parent", cascade="all, delete-orphan",
        foreign_keys="ParentLink.parent_id"
    )
    # Links where this user is the student.
    guardian_links = db.relationship(
        "ParentLink", back_populates="student", cascade="all, delete-orphan",
        foreign_keys="ParentLink.student_id"
    )

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def known_as(self):
        """Preferred name when set, otherwise the legal first name."""
        return self.preferred_name or self.first_name

    @property
    def display_name(self):
        return f"{self.last_name}, {self.first_name}"

    @property
    def initials(self):
        return (self.first_name[:1] + self.last_name[:1]).upper()

    @property
    def role_label(self):
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def is_admin(self):
        return self.role == ROLE_ADMIN

    @property
    def is_teacher(self):
        return self.role == ROLE_TEACHER

    @property
    def is_student(self):
        return self.role == ROLE_STUDENT

    @property
    def is_parent(self):
        return self.role == ROLE_PARENT

    @property
    def is_district_admin(self):
        return self.role == ROLE_DISTRICT

    @property
    def is_homeroom_staff(self):
        return self.role == ROLE_STAFF

    @property
    def is_school_user(self):
        """Belongs to exactly one school and lives inside its data."""
        return self.role in (ROLE_STUDENT, ROLE_TEACHER, ROLE_ADMIN, ROLE_PARENT)

    @property
    def children(self):
        """Students this parent is linked to."""
        return [link.student for link in self.parent_links]

    @property
    def can_switch_schools(self):
        return self.role in (ROLE_DISTRICT, ROLE_STAFF)

    @property
    def is_active(self):
        return self.active

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class ParentLink(db.Model):
    """Connects a parent/guardian account to a student account.

    A parent account is meaningless without at least one of these — the portal shows
    nothing but the children it is linked to.
    """

    __tablename__ = "parent_links"
    __table_args__ = (
        db.UniqueConstraint("parent_id", "student_id", name="uq_parent_link"),
    )

    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    relationship_label = db.Column(db.String(40), default="Parent")
    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    # Guardians can be limited to a subset of the record.
    can_view_grades = db.Column(db.Boolean, nullable=False, default=True)
    can_view_attendance = db.Column(db.Boolean, nullable=False, default=True)
    can_view_documents = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    parent = db.relationship("User", back_populates="parent_links", foreign_keys=[parent_id])
    student = db.relationship("User", back_populates="guardian_links",
                              foreign_keys=[student_id])


# ----------------------------------------------------------------------- assessments

ASSESSMENT_SUBJECTS = ("Mathematics", "English Language Arts", "Science", "Social Studies")

# Which assessment subject a department's teachers actually care about. A maths teacher
# has no use for an ELA score, so risk flags are filtered through this.
DEPARTMENT_SUBJECTS = {
    "Mathematics": "Mathematics",
    "Math": "Mathematics",
    "Science": "Science",
    "English": "English Language Arts",
    "English Language Arts": "English Language Arts",
    "Language Arts": "English Language Arts",
    "Social Studies": "Social Studies",
    "History": "Social Studies",
}


def subject_for_department(department):
    """The assessment subject matching a department, or None if it doesn't map."""
    if not department:
        return None
    return DEPARTMENT_SUBJECTS.get(department.strip())


class Assessment(db.Model):
    """A standardized test administration.

    `state_average` is the published statewide figure we compare against; school and
    district averages are computed from the results themselves.
    """

    __tablename__ = "assessments"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    subject = db.Column(db.String(80), nullable=False, default="Mathematics")
    term_label = db.Column(db.String(60), default="")
    administered_on = db.Column(db.Date, nullable=False, default=date.today)
    grade_level = db.Column(db.Integer)

    max_score = db.Column(db.Float, nullable=False, default=100.0)
    # Score at or above which a student counts as proficient.
    proficient_cutoff = db.Column(db.Float, nullable=False, default=70.0)
    state_average = db.Column(db.Float)

    # Optional tie to a course, so a teacher can see it on their roster.
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"))

    course = db.relationship("Course")
    results = db.relationship("AssessmentResult", back_populates="assessment",
                              cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Assessment {self.name}>"


class AssessmentResult(db.Model):
    __tablename__ = "assessment_results"
    __table_args__ = (
        db.UniqueConstraint("assessment_id", "student_id", name="uq_assessment_result"),
    )

    id = db.Column(db.Integer, primary_key=True)
    assessment_id = db.Column(db.Integer, db.ForeignKey("assessments.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    score = db.Column(db.Float, nullable=False)

    assessment = db.relationship("Assessment", back_populates="results")
    student = db.relationship("User", foreign_keys=[student_id])

    @property
    def percent(self):
        if not self.assessment.max_score:
            return None
        return round(self.score / self.assessment.max_score * 100, 1)

    @property
    def is_proficient(self):
        return self.score >= self.assessment.proficient_cutoff

    @property
    def vs_state(self):
        """Points above/below the statewide average, or None if there isn't one."""
        if self.assessment.state_average is None:
            return None
        return round(self.score - self.assessment.state_average, 1)


# ------------------------------------------------------- announcements & notices

ANNOUNCEMENT_AUDIENCES = {
    "all": "Everyone at the school",
    "students": "Students only",
    "parents": "Parents / guardians only",
    "families": "Students and parents",
    "staff": "Teachers and administrators",
}

NOTIFICATION_KINDS = {
    "announcement": "Announcement",
    "reminder": "Reminder",
    "grade": "Grades",
    "attendance": "Attendance",
    "assignment": "Assignments",
    "support": "Support",
    "absence": "Absence request",
}


class Announcement(db.Model):
    """Something the school wants people to see, optionally needing action."""

    __tablename__ = "announcements"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), index=True)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.Text, default="")

    audience = db.Column(db.String(20), nullable=False, default="all")
    grade_level = db.Column(db.Integer)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"))

    # Draws the eye and sorts to the top of the notification centre.
    urgent = db.Column(db.Boolean, nullable=False, default=False)
    # Something they must do — a form to return, a fee to pay.
    action_label = db.Column(db.String(80), default="")
    action_url = db.Column(db.String(300), default="")
    due_on = db.Column(db.Date)

    starts_on = db.Column(db.Date, nullable=False, default=date.today)
    expires_on = db.Column(db.Date)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    school = db.relationship("School")
    course = db.relationship("Course")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def is_live(self):
        today = date.today()
        if self.starts_on and self.starts_on > today:
            return False
        return not (self.expires_on and self.expires_on < today)

    @property
    def audience_label(self):
        return ANNOUNCEMENT_AUDIENCES.get(self.audience, self.audience)

    def reaches(self, user):
        if not self.is_live:
            return False
        if self.audience == "students" and not user.is_student:
            return False
        if self.audience == "parents" and not user.is_parent:
            return False
        if self.audience == "families" and not (user.is_student or user.is_parent):
            return False
        if self.audience == "staff" and not (user.is_teacher or user.is_admin):
            return False

        # Scoped to one class: only the people attached to that section see it.
        if self.course_id and not self._touches_course(user):
            return False

        if self.grade_level:
            if user.is_student and user.grade_level != self.grade_level:
                return False
            if user.is_parent and self.grade_level not in {
                c.grade_level for c in user.children
            }:
                return False
        return True

    def _touches_course(self, user):
        if user.is_student:
            return any(e.course_id == self.course_id for e in user.enrollments)
        if user.is_parent:
            return any(
                any(e.course_id == self.course_id for e in child.enrollments)
                for child in user.children
            )
        if user.is_teacher:
            return any(c.id == self.course_id for c in user.taught_courses)
        # School administrators see everything posted at their school.
        return user.is_admin


class AnnouncementRead(db.Model):
    """Marks one announcement as read by one person.

    Announcements are shared rows rather than per-person copies, so "read" has to live
    somewhere separate — without this the badge could never be cleared.
    """

    __tablename__ = "announcement_reads"
    __table_args__ = (
        db.UniqueConstraint("user_id", "announcement_id", name="uq_announcement_read"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    announcement_id = db.Column(db.Integer, db.ForeignKey("announcements.id"),
                                nullable=False, index=True)
    read_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", foreign_keys=[user_id])
    announcement = db.relationship("Announcement")


class Notification(db.Model):
    """A message delivered to one person's notification centre."""

    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False, default="reminder")
    title = db.Column(db.String(180), nullable=False)
    body = db.Column(db.Text, default="")
    link = db.Column(db.String(300), default="")
    urgent = db.Column(db.Boolean, nullable=False, default=False)

    announcement_id = db.Column(db.Integer, db.ForeignKey("announcements.id"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    read_at = db.Column(db.DateTime)

    user = db.relationship("User", foreign_keys=[user_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    announcement = db.relationship("Announcement")

    @property
    def is_read(self):
        return self.read_at is not None


# ------------------------------------------------------------- absence requests

# Two genuinely different things, which is why they're separated:
#
#   report  — it already happened or is happening now. You can't ask permission for a
#             fever after the fact; you're informing the office and asking them to excuse it.
#   request — it hasn't happened yet and is being planned. Here permission is meaningful,
#             because the school can say no before anyone commits to it.
ABSENCE_REPORT = "report"
ABSENCE_REQUEST = "request"

ABSENCE_KINDS = {
    ABSENCE_REPORT: "Absence report",
    ABSENCE_REQUEST: "Planned absence request",
}

# Reasons you can only know about afterwards or on the day.
ABSENCE_REPORT_REASONS = ("illness", "emergency", "bereavement", "other")
# Reasons you know about ahead of time.
ABSENCE_REQUEST_REASONS = ("appointment", "travel", "family", "religious",
                           "college_visit", "other")

ABSENCE_REASONS = tuple(sorted(set(ABSENCE_REPORT_REASONS + ABSENCE_REQUEST_REASONS)))

ABSENCE_REASON_LABELS = {
    "illness": "Illness",
    "emergency": "Family emergency",
    "bereavement": "Bereavement",
    "appointment": "Appointment",
    "travel": "Travel",
    "family": "Family commitment",
    "religious": "Religious observance",
    "college_visit": "College visit",
    "other": "Other",
}

ABSENCE_STATUSES = ("pending", "approved", "denied")


class AbsenceRequest(db.Model):
    """An absence a family has told the office about.

    Either a report of one that already happened, or a request for one that hasn't —
    see ABSENCE_KINDS. The distinction changes what the office is actually deciding.
    """

    __tablename__ = "absence_requests"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), index=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    submitted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    kind = db.Column(db.String(20), nullable=False, default=ABSENCE_REPORT)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.String(30), nullable=False, default="illness")
    detail = db.Column(db.Text, default="")

    status = db.Column(db.String(20), nullable=False, default="pending")
    reviewer_note = db.Column(db.String(255), default="")
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    submitted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    school = db.relationship("School")
    student = db.relationship("User", foreign_keys=[student_id])
    submitted_by = db.relationship("User", foreign_keys=[submitted_by_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])

    @property
    def day_count(self):
        return (self.end_date - self.start_date).days + 1

    @property
    def date_label(self):
        if self.start_date == self.end_date:
            return self.start_date.strftime("%b %d, %Y").replace(" 0", " ")
        return (f"{self.start_date.strftime('%b %d').replace(' 0', ' ')} – "
                f"{self.end_date.strftime('%b %d, %Y').replace(' 0', ' ')}")

    @property
    def is_report(self):
        return self.kind == ABSENCE_REPORT

    @property
    def kind_label(self):
        return ABSENCE_KINDS.get(self.kind, self.kind)

    @property
    def reason_label(self):
        return ABSENCE_REASON_LABELS.get(self.reason, self.reason.replace("_", " ").title())

    @property
    def verb(self):
        """What the family did — reported it, or asked for it."""
        return "reported" if self.is_report else "requested"

    @property
    def decision_label(self):
        """What the office's decision means for this kind."""
        if self.status == "pending":
            return "Awaiting review" if self.is_report else "Awaiting approval"
        if self.is_report:
            return "Excused" if self.status == "approved" else "Not excused"
        return "Approved" if self.status == "approved" else "Denied"


class HealthRecord(db.Model):
    """Nurse-office information. Visible to staff and the student's own guardians."""

    __tablename__ = "health_records"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                           unique=True, nullable=False, index=True)

    allergies = db.Column(db.Text, default="")
    medications = db.Column(db.Text, default="")
    conditions = db.Column(db.Text, default="")
    dietary_notes = db.Column(db.Text, default="")

    physician_name = db.Column(db.String(120), default="")
    physician_phone = db.Column(db.String(40), default="")
    insurance_provider = db.Column(db.String(120), default="")

    # Kept deliberately coarse — this is a demo, not a medical record system.
    has_action_plan = db.Column(db.Boolean, nullable=False, default=False)
    action_plan_note = db.Column(db.String(255), default="")

    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    student = db.relationship("User", foreign_keys=[student_id])
    updated_by = db.relationship("User", foreign_keys=[updated_by_id])

    @property
    def is_empty(self):
        return not any([self.allergies, self.medications, self.conditions,
                        self.dietary_notes, self.physician_name])


class Term(db.Model):
    __tablename__ = "terms"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), index=True)
    name = db.Column(db.String(80), nullable=False)
    school_year = db.Column(db.String(20), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    is_current = db.Column(db.Boolean, nullable=False, default=False)

    school = db.relationship("School")
    courses = db.relationship("Course", back_populates="term")


class Period(db.Model):
    """A named slot a course can occupy — "P1", "Support", "Lunch".

    A slot has no fixed time of its own. When it runs, and for how long, is decided by
    each BellSchedule that includes it, so P1 can start at 8:00 on a normal day and not
    run at all on a late-start day.
    """

    __tablename__ = "periods"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), index=True)
    name = db.Column(db.String(40), nullable=False)
    ordinal = db.Column(db.Integer, nullable=False)

    # Fallback times, used when building a new layout and as a last resort if a layout
    # somehow omits the slot.
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)

    # class / support / break / lunch
    kind = db.Column(db.String(20), nullable=False, default=PERIOD_CLASS)

    school = db.relationship("School", back_populates="periods")
    courses = db.relationship("Course", back_populates="period")
    support_sessions = db.relationship(
        "SupportSession", back_populates="period", cascade="all, delete-orphan"
    )
    bell_entries = db.relationship(
        "BellPeriod", back_populates="period", cascade="all, delete-orphan"
    )

    @property
    def is_support(self):
        return self.kind == PERIOD_SUPPORT

    @property
    def is_instructional(self):
        """Whether a course can be scheduled into this slot."""
        return self.kind == PERIOD_CLASS

    @property
    def label(self):
        """The slot's display name, honouring the school's own word for support."""
        if self.kind == PERIOD_SUPPORT and self.school:
            return self.school.support_label
        return self.name

    @property
    def time_range(self):
        return format_range(self.start_time, self.end_time)

    def contains(self, moment: time):
        return self.start_time <= moment <= self.end_time

    def __repr__(self):
        return f"<Period {self.name}>"


class BellSchedule(db.Model):
    """A named day layout — "Mon/Tue", "Wednesday", "Assembly Day".

    Which slots run on a given day, in what order, at what times. A school can have as
    many as it likes; each date resolves to exactly one.
    """

    __tablename__ = "bell_schedules"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), index=True)
    name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(200), default="")

    # Weekday tokens (M,T,W,R,F) this layout runs on by default. A per-date override on
    # SchoolDay beats this.
    default_weekdays = db.Column(db.String(20), default="")
    # Used for any day nothing else matches.
    is_default = db.Column(db.Boolean, nullable=False, default=False)

    school = db.relationship("School", back_populates="bell_schedules")
    entries = db.relationship(
        "BellPeriod", back_populates="bell_schedule",
        cascade="all, delete-orphan", order_by="BellPeriod.ordinal",
    )

    @property
    def weekday_tokens(self):
        return [t.strip().upper() for t in (self.default_weekdays or "").split(",") if t.strip()]

    @property
    def weekdays_label(self):
        tokens = self.weekday_tokens
        if not tokens:
            return "Not assigned"
        short = {"M": "Mon", "T": "Tue", "W": "Wed", "R": "Thu", "F": "Fri"}
        return "/".join(short.get(t, t) for t in tokens)

    @property
    def slot_ids(self):
        return {entry.period_id for entry in self.entries}

    @property
    def instructional_count(self):
        return sum(1 for e in self.entries if e.period and e.period.is_instructional)

    def runs_on_weekday(self, weekday):
        return WEEKDAY_TOKENS.get(weekday) in self.weekday_tokens

    def includes(self, period_id):
        return period_id in self.slot_ids

    @property
    def day_span(self):
        if not self.entries:
            return "—"
        return format_range(self.entries[0].start_time, self.entries[-1].end_time)

    def __repr__(self):
        return f"<BellSchedule {self.name}>"


class BellPeriod(db.Model):
    """One slot's placement inside one layout."""

    __tablename__ = "bell_periods"
    __table_args__ = (
        db.UniqueConstraint("bell_schedule_id", "period_id", name="uq_bell_slot"),
    )

    id = db.Column(db.Integer, primary_key=True)
    bell_schedule_id = db.Column(db.Integer, db.ForeignKey("bell_schedules.id"),
                                 nullable=False, index=True)
    period_id = db.Column(db.Integer, db.ForeignKey("periods.id"), nullable=False)
    ordinal = db.Column(db.Integer, nullable=False, default=1)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)

    bell_schedule = db.relationship("BellSchedule", back_populates="entries")
    period = db.relationship("Period", back_populates="bell_entries")

    @property
    def time_range(self):
        return format_range(self.start_time, self.end_time)

    @property
    def name(self):
        return self.period.label if self.period else "—"

    def contains(self, moment: time):
        return self.start_time <= moment <= self.end_time


class Course(db.Model):
    __tablename__ = "courses"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), index=True)
    code = db.Column(db.String(20), nullable=False, index=True)
    name = db.Column(db.String(140), nullable=False)
    description = db.Column(db.Text, default="")
    department = db.Column(db.String(80), nullable=False, default="General")
    credits = db.Column(db.Float, nullable=False, default=1.0)
    capacity = db.Column(db.Integer, nullable=False, default=30)
    room = db.Column(db.String(40), default="")
    # Comma-separated tokens interpreted against the school's rotation_mode:
    #   daily   -> "ALL"
    #   ab      -> "A", "B" or "A,B"
    #   weekday -> "M,W,F"
    #   cycle   -> "1,4"
    meeting_days = db.Column(db.String(40), nullable=False, default="ALL")

    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    period_id = db.Column(db.Integer, db.ForeignKey("periods.id"))
    term_id = db.Column(db.Integer, db.ForeignKey("terms.id"))

    selectable = db.Column(db.Boolean, nullable=False, default=True)
    prerequisite = db.Column(db.String(160), default="")

    # "points" totals everything; "weighted" uses GradeCategory weights.
    grading_mode = db.Column(db.String(20), nullable=False, default="points")

    # Extra grade points on the weighted GPA — 1.0 for AP, 0.5 for Honours, 0 otherwise.
    gpa_bonus = db.Column(db.Float, nullable=False, default=0.0)
    rigor = db.Column(db.String(20), nullable=False, default="regular")  # regular/honors/ap

    school = db.relationship("School", back_populates="courses")
    teacher = db.relationship("User", back_populates="taught_courses", foreign_keys=[teacher_id])
    period = db.relationship("Period", back_populates="courses")
    term = db.relationship("Term", back_populates="courses")
    enrollments = db.relationship(
        "Enrollment", back_populates="course", cascade="all, delete-orphan"
    )
    requests = db.relationship(
        "CourseRequest", back_populates="course", cascade="all, delete-orphan"
    )
    assignments = db.relationship(
        "Assignment", back_populates="course", cascade="all, delete-orphan"
    )
    categories = db.relationship(
        "GradeCategory", back_populates="course", cascade="all, delete-orphan"
    )

    @property
    def seats_taken(self):
        return len(self.enrollments)

    @property
    def seats_open(self):
        return max(self.capacity - self.seats_taken, 0)

    @property
    def is_full(self):
        return self.seats_open == 0

    @property
    def fill_percent(self):
        if not self.capacity:
            return 0
        return min(round(self.seats_taken / self.capacity * 100), 100)

    @property
    def students(self):
        return sorted((e.student for e in self.enrollments),
                      key=lambda s: (s.last_name, s.first_name))

    @property
    def day_tokens(self):
        return [t.strip().upper() for t in (self.meeting_days or "").split(",") if t.strip()]

    @property
    def days_label(self):
        """Readable meeting pattern, e.g. 'Mon/Wed/Fri' or 'A & B days'."""
        school = self.school
        tokens = self.day_tokens
        if not tokens or tokens == ["ALL"]:
            return "Every day"
        if school is None:
            return ", ".join(tokens)
        if school.rotation_mode == ROTATION_WEEKDAY:
            short = {"M": "Mon", "T": "Tue", "W": "Wed", "R": "Thu", "F": "Fri"}
            return "/".join(short.get(t, t) for t in tokens)
        if school.rotation_mode == ROTATION_AB:
            return " & ".join(f"{t}" for t in tokens) + " days"
        if school.rotation_mode == ROTATION_CYCLE:
            return "Days " + ", ".join(tokens)
        return "Every day"

    def meets_on(self, day_type, weekday=None):
        """Does this section meet on a day with this type?

        `day_type` is whatever SchoolDay recorded (A/B, a cycle number, or None for a
        non-session day). `weekday` is Python's weekday() and is only consulted for
        schools on the fixed-weekday model.
        """
        school = self.school
        mode = school.rotation_mode if school else ROTATION_AB
        tokens = self.day_tokens

        if mode == ROTATION_DAILY or not tokens or tokens == ["ALL"]:
            return True

        if mode == ROTATION_WEEKDAY:
            if weekday is None:
                return True
            return WEEKDAY_TOKENS.get(weekday) in tokens

        # A/B and numbered-cycle schools both key off the recorded day type.
        if day_type is None:
            return True
        return str(day_type).upper() in tokens

    def __repr__(self):
        return f"<Course {self.code}>"


class Enrollment(db.Model):
    __tablename__ = "enrollments"
    __table_args__ = (db.UniqueConstraint("student_id", "course_id", name="uq_enrollment"),)

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    enrolled_on = db.Column(db.Date, nullable=False, default=date.today)

    student = db.relationship("User", back_populates="enrollments")
    course = db.relationship("Course", back_populates="enrollments")


# --------------------------------------------------------------------- assignments


class GradeCategory(db.Model):
    """A weighted bucket within a course, e.g. Tests 40% / Homework 20%."""

    __tablename__ = "grade_categories"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    name = db.Column(db.String(80), nullable=False)
    weight = db.Column(db.Float, nullable=False, default=0.0)

    course = db.relationship("Course", back_populates="categories")
    assignments = db.relationship("Assignment", back_populates="category")


class Assignment(db.Model):
    __tablename__ = "assignments"

    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("grade_categories.id"))

    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, default="")
    points_possible = db.Column(db.Float, nullable=False, default=100.0)

    assigned_on = db.Column(db.Date, nullable=False, default=date.today)
    due_on = db.Column(db.Date, index=True)

    # Hidden from students until published.
    published = db.Column(db.Boolean, nullable=False, default=True)
    # Counts toward the course grade.
    graded = db.Column(db.Boolean, nullable=False, default=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    course = db.relationship("Course", back_populates="assignments")
    category = db.relationship("GradeCategory", back_populates="assignments")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    grades = db.relationship("Grade", back_populates="assignment",
                             cascade="all, delete-orphan")

    @property
    def is_overdue(self):
        return bool(self.due_on and self.due_on < date.today())

    @property
    def graded_count(self):
        return sum(1 for g in self.grades if g.status == "graded" and g.points_earned is not None)

    @property
    def average_percent(self):
        scored = [g.percent for g in self.grades if g.percent is not None]
        if not scored:
            return None
        return round(sum(scored) / len(scored), 1)


class Grade(db.Model):
    __tablename__ = "grades"
    __table_args__ = (
        db.UniqueConstraint("assignment_id", "student_id", name="uq_grade"),
    )

    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignments.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    points_earned = db.Column(db.Float)
    status = db.Column(db.String(20), nullable=False, default="ungraded")
    comment = db.Column(db.String(255), default="")

    graded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    graded_at = db.Column(db.DateTime)

    assignment = db.relationship("Assignment", back_populates="grades")
    student = db.relationship("User", foreign_keys=[student_id])
    graded_by = db.relationship("User", foreign_keys=[graded_by_id])

    @property
    def counts(self):
        """Excused work is left out of the average entirely."""
        return self.status != "excused" and self.assignment.graded

    @property
    def effective_points(self):
        if not self.counts:
            return None
        if self.status == "missing":
            return 0.0
        return self.points_earned

    @property
    def percent(self):
        points = self.effective_points
        if points is None or not self.assignment.points_possible:
            return None
        return round(points / self.assignment.points_possible * 100, 1)

    @property
    def letter(self):
        return letter_for(self.percent)


# ------------------------------------------------------------------------ documents

DOCUMENT_CATEGORIES = (
    "handbook", "form", "policy", "syllabus", "newsletter", "transcript", "other"
)


class Document(db.Model):
    """A file posted to the school, a course, or a single student."""

    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), index=True)

    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, default="")
    category = db.Column(db.String(40), nullable=False, default="other")

    stored_name = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(120), default="")
    size_bytes = db.Column(db.Integer, default=0)

    # Scope: school-wide by default; narrowed by course, grade level or one student.
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"))
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    grade_level = db.Column(db.Integer)
    staff_only = db.Column(db.Boolean, nullable=False, default=False)

    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    uploaded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    school = db.relationship("School")
    course = db.relationship("Course")
    student = db.relationship("User", foreign_keys=[student_id])
    uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_id])

    @property
    def size_label(self):
        size = self.size_bytes or 0
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    @property
    def extension(self):
        return self.original_name.rsplit(".", 1)[-1].lower() if "." in self.original_name else ""

    @property
    def scope_label(self):
        if self.student_id:
            return "Individual student"
        if self.course_id:
            return "Course"
        if self.grade_level:
            return f"Grade {self.grade_level}"
        if self.staff_only:
            return "Staff only"
        return "School-wide"


# -------------------------------------------------------------------------- support


class SupportSession(db.Model):
    """A teacher's offering for a support/flex block on a given date.

    kind: 'work' = open room to finish work, 'taught' = the teacher runs something.
    """

    __tablename__ = "support_sessions"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), index=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    period_id = db.Column(db.Integer, db.ForeignKey("periods.id"), nullable=False)
    session_date = db.Column(db.Date, nullable=False, index=True)

    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, default="")
    kind = db.Column(db.String(20), nullable=False, default="work")
    capacity = db.Column(db.Integer, nullable=False, default=25)
    location = db.Column(db.String(40), default="")

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    school = db.relationship("School")
    teacher = db.relationship("User", foreign_keys=[teacher_id])
    period = db.relationship("Period", back_populates="support_sessions")
    signups = db.relationship("SupportSignup", back_populates="session",
                              cascade="all, delete-orphan")

    @property
    def seats_taken(self):
        return len(self.signups)

    @property
    def seats_open(self):
        return max(self.capacity - self.seats_taken, 0)

    @property
    def is_full(self):
        return self.seats_open == 0

    @property
    def fill_percent(self):
        if not self.capacity:
            return 0
        return min(round(self.seats_taken / self.capacity * 100), 100)

    @property
    def kind_label(self):
        return "Taught session" if self.kind == "taught" else "Open work time"


class SupportSignup(db.Model):
    __tablename__ = "support_signups"
    __table_args__ = (
        db.UniqueConstraint("student_id", "session_id", name="uq_support_signup"),
    )

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("support_sessions.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    # An admin-placed student cannot move themselves out.
    locked = db.Column(db.Boolean, nullable=False, default=False)
    assigned_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    note = db.Column(db.String(255), default="")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    session = db.relationship("SupportSession", back_populates="signups")
    student = db.relationship("User", foreign_keys=[student_id])
    assigned_by = db.relationship("User", foreign_keys=[assigned_by_id])


# ------------------------------------------------------------- selection & calendar


class SelectionWindow(db.Model):
    __tablename__ = "selection_windows"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), index=True)
    name = db.Column(db.String(120), nullable=False)
    opens_on = db.Column(db.Date, nullable=False)
    closes_on = db.Column(db.Date, nullable=False)
    required_slots = db.Column(db.Integer, nullable=False, default=6)
    is_open = db.Column(db.Boolean, nullable=False, default=True)
    instructions = db.Column(db.Text, default="")

    school = db.relationship("School")
    requests = db.relationship("CourseRequest", back_populates="window",
                               cascade="all, delete-orphan")

    @property
    def accepting(self):
        return self.is_open and self.opens_on <= date.today() <= self.closes_on


class CourseRequest(db.Model):
    __tablename__ = "course_requests"
    __table_args__ = (
        db.UniqueConstraint("student_id", "window_id", "slot", "rank", name="uq_request_slot"),
    )

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    window_id = db.Column(db.Integer, db.ForeignKey("selection_windows.id"), nullable=False)

    slot = db.Column(db.Integer, nullable=False, default=1)
    rank = db.Column(db.Integer, nullable=False, default=1)

    status = db.Column(db.String(20), nullable=False, default="pending")
    reviewer_note = db.Column(db.String(255), default="")
    submitted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    student = db.relationship("User", back_populates="course_requests")
    course = db.relationship("Course", back_populates="requests")
    window = db.relationship("SelectionWindow", back_populates="requests")

    @property
    def rank_label(self):
        return "1st pick" if self.rank == 1 else "2nd pick"


class CalendarEvent(db.Model):
    __tablename__ = "calendar_events"

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), index=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text, default="")
    event_date = db.Column(db.Date, nullable=False, index=True)
    start_time = db.Column(db.Time)
    end_time = db.Column(db.Time)
    all_day = db.Column(db.Boolean, nullable=False, default=True)
    category = db.Column(db.String(40), nullable=False, default="activity")

    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"))
    grade_level = db.Column(db.Integer)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    school = db.relationship("School")
    course = db.relationship("Course")
    created_by = db.relationship("User")

    @property
    def time_label(self):
        if self.all_day or not self.start_time:
            return "All day"
        start = self.start_time.strftime("%I:%M %p").lstrip("0")
        if self.end_time:
            return f"{start} – {self.end_time.strftime('%I:%M %p').lstrip('0')}"
        return start


class SchoolDay(db.Model):
    __tablename__ = "school_days"
    __table_args__ = (
        db.UniqueConstraint("school_id", "day_date", name="uq_school_day"),
    )

    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey("schools.id"), index=True)
    day_date = db.Column(db.Date, nullable=False, index=True)
    day_type = db.Column(db.String(6), nullable=False, default="A")
    in_session = db.Column(db.Boolean, nullable=False, default=True)
    note = db.Column(db.String(160), default="")

    # Overrides the weekday default — an assembly schedule on one Tuesday, say.
    bell_schedule_id = db.Column(db.Integer, db.ForeignKey("bell_schedules.id"))

    school = db.relationship("School")
    bell_schedule = db.relationship("BellSchedule")


class AttendanceRecord(db.Model):
    __tablename__ = "attendance_records"
    __table_args__ = (
        db.UniqueConstraint("student_id", "course_id", "record_date", name="uq_attendance"),
    )

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    course_id = db.Column(db.Integer, db.ForeignKey("courses.id"), nullable=False)
    record_date = db.Column(db.Date, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="present")
    note = db.Column(db.String(255), default="")
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    recorded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    student = db.relationship("User", foreign_keys=[student_id])
    course = db.relationship("Course")
    recorded_by = db.relationship("User", foreign_keys=[recorded_by_id])


# -------------------------------------------------------------------- sales (staff)


class DemoRequest(db.Model):
    __tablename__ = "demo_requests"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), nullable=False)
    phone = db.Column(db.String(40), default="")
    organization = db.Column(db.String(160), nullable=False)
    job_title = db.Column(db.String(120), default="")
    student_count = db.Column(db.String(40), default="")
    preferred_date = db.Column(db.Date)
    interests = db.Column(db.String(255), default="")
    message = db.Column(db.Text, default="")
    status = db.Column(db.String(20), nullable=False, default="new")
    submitted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class PurchaseRequest(db.Model):
    __tablename__ = "purchase_requests"

    id = db.Column(db.Integer, primary_key=True)
    organization = db.Column(db.String(160), nullable=False)
    contact_name = db.Column(db.String(120), nullable=False)
    contact_email = db.Column(db.String(160), nullable=False)
    phone = db.Column(db.String(40), default="")
    billing_address = db.Column(db.Text, default="")
    plan = db.Column(db.String(40), nullable=False, default="core")
    seats = db.Column(db.Integer, nullable=False, default=500)
    term_length = db.Column(db.String(40), default="1 year")
    payment_method = db.Column(db.String(40), default="purchase_order")
    po_number = db.Column(db.String(60), default="")
    add_ons = db.Column(db.String(255), default="")
    notes = db.Column(db.Text, default="")
    quoted_total = db.Column(db.Float)
    status = db.Column(db.String(20), nullable=False, default="new")
    submitted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
