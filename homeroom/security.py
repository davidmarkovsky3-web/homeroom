"""Role-based access control and tenant (school) resolution."""

from functools import wraps

from flask import abort, g, session
from flask_login import current_user, login_required

from .extensions import db
from .models import (
    ROLE_ADMIN,
    ROLE_DISTRICT,
    ROLE_PARENT,
    ROLE_STAFF,
    ROLE_STUDENT,
    ROLE_TEACHER,
    ParentLink,
    School,
)

SESSION_SCHOOL_KEY = "active_school_id"


def active_school():
    """The school whose data the current request operates on.

    School users are pinned to their own school. District admins and Homeroom staff
    choose one, which is remembered in the session.
    """
    if "active_school" in g:
        return g.active_school

    school = None
    if not current_user.is_authenticated:
        g.active_school = None
        return None

    if current_user.is_school_user:
        school = current_user.school
    elif current_user.can_switch_schools:
        chosen = session.get(SESSION_SCHOOL_KEY)
        if chosen:
            school = db.session.get(School, chosen)
        if school is None or not _may_access_school(current_user, school):
            school = _default_school(current_user)

    g.active_school = school
    return school


def _default_school(user):
    query = School.query.filter_by(active=True)
    if user.role == ROLE_DISTRICT:
        query = query.filter_by(district_id=user.district_id)
    return query.order_by(School.name).first()


def _may_access_school(user, school):
    """Whether a user may operate inside a school's data.

    Homeroom's own staff are deliberately excluded. They provision tenants and run the
    business; they have no business reading a customer's student records.
    """
    if school is None:
        return False
    if user.role == ROLE_STAFF:
        return False
    if user.role == ROLE_DISTRICT:
        return school.district_id == user.district_id
    return user.school_id == school.id


def may_access_school(school):
    return _may_access_school(current_user, school)


def switchable_schools():
    """Schools the current user is allowed to switch between."""
    if not current_user.is_authenticated:
        return []
    if current_user.role == ROLE_DISTRICT:
        return (
            School.query.filter_by(district_id=current_user.district_id)
            .order_by(School.name)
            .all()
        )
    return []


def scoped(query, model):
    """Constrain a query to the active school when the model is school-scoped."""
    school = active_school()
    if school is not None and hasattr(model, "school_id"):
        return query.filter(model.school_id == school.id)
    return query


# ------------------------------------------------------------------------ decorators


def roles_required(*roles):
    """Allow only the listed roles.

    Unlike the earlier version, admins are NOT implicitly allowed everywhere — a school
    admin has no business in the Homeroom sales console, so each route lists its roles.
    """

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def school_required(view):
    """Routes that need an active school to operate on."""

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if active_school() is None:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


admin_required = roles_required(ROLE_ADMIN)
student_required = roles_required(ROLE_STUDENT)
teacher_required = roles_required(ROLE_TEACHER)
parent_required = roles_required(ROLE_PARENT)
staff_required = roles_required(ROLE_TEACHER, ROLE_ADMIN)
# Homeroom's own staff: sales and provisioning only.
homeroom_staff_required = roles_required(ROLE_STAFF)
# The district console — a district's own people.
district_required = roles_required(ROLE_DISTRICT)
# Anyone who administers a school's configuration and data. Deliberately excludes
# Homeroom staff: provisioning a tenant is not the same as being inside it.
school_admin_required = roles_required(ROLE_ADMIN, ROLE_DISTRICT)
# The tenant-provisioning tool, which Homeroom staff and district admins share.
provisioning_required = roles_required(ROLE_STAFF, ROLE_DISTRICT)


def may_view_student(student):
    """Whether the current user is allowed to see one student's record.

    Homeroom staff never qualify — no vendor employee reads a student's record.
    """
    user = current_user
    if not user.is_authenticated or student is None:
        return False
    if user.role == ROLE_STAFF:
        return False
    if user.role == ROLE_DISTRICT:
        return student.school and student.school.district_id == user.district_id
    if user.role == ROLE_ADMIN:
        return student.school_id == user.school_id
    if user.role == ROLE_TEACHER:
        mine = {c.id for c in user.taught_courses}
        theirs = {e.course_id for e in student.enrollments}
        return bool(mine & theirs)
    if user.role == ROLE_PARENT:
        return any(link.student_id == student.id for link in user.parent_links)
    if user.role == ROLE_STUDENT:
        return student.id == user.id
    return False


def require_student_access(student):
    if not may_view_student(student):
        abort(403)
    return student


def parent_link_for(student):
    """The ParentLink joining the current parent to this student, if any."""
    if not current_user.is_authenticated or current_user.role != ROLE_PARENT:
        return None
    return ParentLink.query.filter_by(
        parent_id=current_user.id, student_id=student.id
    ).first()
