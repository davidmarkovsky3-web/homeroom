"""District administrator console — oversight across the schools in one district."""

from datetime import date

from flask import Blueprint, abort, render_template, request
from flask_login import current_user

from ..extensions import db
from ..models import Assessment, Course, District, School, User
from ..security import district_required, may_access_school
from ..services import assessment_breakdown, school_attendance_rate

bp = Blueprint("district", __name__, url_prefix="/app/district")


def _my_district():
    """The district this user runs. Only district admins reach these views."""
    district = db.session.get(District, current_user.district_id)
    if district is None:
        abort(404)
    return district


@bp.route("/")
@district_required
def index():
    district = _my_district()
    today = date.today()

    rows = []
    for school in sorted(district.schools, key=lambda s: s.name):
        rows.append({
            "school": school,
            "students": school.student_count,
            "staff": school.staff_count,
            "courses": Course.query.filter_by(school_id=school.id).count(),
            "attendance": school_attendance_rate(today, school),
        })

    totals = {
        "schools": len(district.schools),
        "students": sum(r["students"] for r in rows),
        "staff": sum(r["staff"] for r in rows),
    }
    rates = [r["attendance"] for r in rows if r["attendance"] is not None]
    totals["attendance"] = round(sum(rates) / len(rates), 1) if rates else None

    return render_template(
        "app/district_home.html",
        district=district,
        rows=rows,
        totals=totals,
        all_districts=[district],
    )


@bp.route("/testing")
@district_required
def testing():
    """Assessment results compared across every school in the district."""
    district = _my_district()
    schools = sorted(district.schools, key=lambda s: s.name)

    assessments = Assessment.query.order_by(Assessment.administered_on.desc()).all()
    table = []
    for assessment in assessments:
        per_school = []
        for school in schools:
            scores = [
                r.score for r in assessment.results
                if r.student.school_id == school.id
            ]
            per_school.append({
                "school": school,
                "average": round(sum(scores) / len(scores), 1) if scores else None,
                "count": len(scores),
            })
        district_scores = [
            r.score for r in assessment.results
            if r.student.school and r.student.school.district_id == district.id
        ]
        table.append({
            "assessment": assessment,
            "per_school": per_school,
            "district_average": (
                round(sum(district_scores) / len(district_scores), 1)
                if district_scores else None
            ),
            "state_average": assessment.state_average,
        })

    return render_template("app/district_testing.html", district=district,
                           schools=schools, table=table)


@bp.route("/staff")
@district_required
def staff_directory():
    district = _my_district()
    school_ids = [s.id for s in district.schools]
    people = (
        User.query.filter(User.school_id.in_(school_ids) if school_ids else False,
                          User.role.in_(("admin", "teacher")))
        .order_by(User.role, User.last_name).all()
    ) if school_ids else []
    return render_template("app/district_staff.html", district=district, people=people)
