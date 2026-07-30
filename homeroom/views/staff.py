"""Homeroom Staff console — the vendor's own view.

This is where the sales pipeline lives, along with the tools for standing up new
districts and schools. School administrators have no access here; they run a school,
they don't work for Homeroom.
"""

import secrets
import string
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user

from ..extensions import db
from ..models import (
    ROTATION_MODES,
    BellPeriod,
    BellSchedule,
    District,
    DemoRequest,
    Period,
    PurchaseRequest,
    School,
    Term,
    User,
)
from ..security import (
    SESSION_SCHOOL_KEY,
    homeroom_staff_required,
    provisioning_required,
    may_access_school,
    switchable_schools,
)

bp = Blueprint("staff", __name__, url_prefix="/app/hq")

DEMO_STATUSES = ("new", "contacted", "scheduled", "closed")
PURCHASE_STATUSES = ("new", "quoted", "invoiced", "won", "lost")

DEFAULT_PERIODS = [
    ("Period 1", 1, "08:00", "08:50", "class"),
    ("Period 2", 2, "08:57", "09:47", "class"),
    ("Period 3", 3, "09:54", "10:44", "class"),
    ("Period 4", 4, "10:51", "11:41", "class"),
    ("Lunch", 5, "11:41", "12:16", "lunch"),
    ("Support", 6, "12:23", "13:00", "support"),
    ("Period 5", 7, "13:07", "13:57", "class"),
    ("Period 6", 8, "14:04", "14:54", "class"),
]


def _temp_password(length=12):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _unique_code(model, base):
    code = base.upper()[:20] or "NEW"
    candidate, n = code, 2
    while model.query.filter_by(code=candidate).first():
        candidate = f"{code[:17]}{n}"
        n += 1
    return candidate


@bp.route("/")
@homeroom_staff_required
def index():
    return render_template(
        "app/staff_home.html",
        districts=District.query.order_by(District.name).all(),
        counts={
            "districts": District.query.count(),
            "schools": School.query.count(),
            "students": User.query.filter_by(role="student", active=True).count(),
            "staff": User.query.filter(User.role.in_(("teacher", "admin"))).count(),
            "demos": DemoRequest.query.filter_by(status="new").count(),
            "purchases": PurchaseRequest.query.filter_by(status="new").count(),
        },
        open_requests=PurchaseRequest.query.filter(
            PurchaseRequest.status.notin_(("won", "lost"))).count(),
        latest_demos=DemoRequest.query.order_by(DemoRequest.submitted_at.desc()).limit(5).all(),
        latest_purchases=PurchaseRequest.query.order_by(
            PurchaseRequest.submitted_at.desc()).limit(5).all(),
    )


# ------------------------------------------------------------------ sales pipeline


@bp.route("/demos")
@homeroom_staff_required
def demos():
    status = request.args.get("status", "")
    query = DemoRequest.query
    if status in DEMO_STATUSES:
        query = query.filter_by(status=status)
    return render_template(
        "app/staff_demos.html",
        requests=query.order_by(DemoRequest.submitted_at.desc()).all(),
        statuses=DEMO_STATUSES, status=status,
    )


@bp.route("/demos/<int:demo_id>/status", methods=["POST"])
@homeroom_staff_required
def update_demo(demo_id):
    lead = db.session.get(DemoRequest, demo_id)
    if lead is None:
        abort(404)
    if request.form.get("status") in DEMO_STATUSES:
        lead.status = request.form["status"]
        db.session.commit()
        flash(f"{lead.organization} marked {lead.status}.", "success")
    return redirect(url_for("staff.demos"))


@bp.route("/purchases")
@homeroom_staff_required
def purchases():
    status = request.args.get("status", "")
    query = PurchaseRequest.query
    if status in PURCHASE_STATUSES:
        query = query.filter_by(status=status)
    orders = query.order_by(PurchaseRequest.submitted_at.desc()).all()
    quoted = [o for o in orders if o.quoted_total is not None]
    return render_template(
        "app/staff_purchases.html",
        requests=orders, statuses=PURCHASE_STATUSES, status=status,
        # Only money a human actually entered — nothing is inferred or estimated.
        quoted_count=len(quoted),
        quoted_total=sum(o.quoted_total for o in quoted) if quoted else None,
    )


@bp.route("/purchases/<int:order_id>/status", methods=["POST"])
@homeroom_staff_required
def update_purchase(order_id):
    order = db.session.get(PurchaseRequest, order_id)
    if order is None:
        abort(404)
    if request.form.get("status") in PURCHASE_STATUSES:
        order.status = request.form["status"]
    quoted = request.form.get("quoted_total", "")
    if quoted:
        try:
            order.quoted_total = float(quoted)
        except ValueError:
            pass
    db.session.commit()
    flash(f"{order.organization} updated.", "success")
    return redirect(url_for("staff.purchases"))


# ------------------------------------------------------------------ district tool


@bp.route("/districts", methods=["GET", "POST"])
@homeroom_staff_required
def districts():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("A district name is required.", "error")
            return redirect(url_for("staff.districts"))

        district = District(
            name=name,
            code=_unique_code(District, request.form.get("code", "").strip()
                              or "".join(w[0] for w in name.split())[:6]),
            state=request.form.get("state", "").strip(),
            contact_name=request.form.get("contact_name", "").strip(),
            contact_email=request.form.get("contact_email", "").strip(),
            phone=request.form.get("phone", "").strip(),
        )
        db.session.add(district)
        db.session.flush()

        # Optionally provision the district's first admin account.
        admin_email = request.form.get("admin_email", "").strip().lower()
        message = f"Created district {district.name} ({district.code})."
        if admin_email:
            if User.query.filter(db.func.lower(User.email) == admin_email).first():
                flash("That district-admin email is already in use.", "warning")
            else:
                temp = _temp_password()
                admin = User(
                    email=admin_email,
                    role="district_admin",
                    first_name=request.form.get("admin_first", "District").strip() or "District",
                    last_name=request.form.get("admin_last", "Admin").strip() or "Admin",
                    district_id=district.id,
                    title="District Administrator",
                )
                admin.set_password(temp)
                db.session.add(admin)
                message += f" District admin {admin_email} — temporary password: {temp}"

        db.session.commit()
        flash(message, "success")
        return redirect(url_for("staff.districts"))

    return render_template("app/staff_districts.html",
                           districts=District.query.order_by(District.name).all())


@bp.route("/districts/<int:district_id>/toggle", methods=["POST"])
@homeroom_staff_required
def toggle_district(district_id):
    district = db.session.get(District, district_id)
    if district is None:
        abort(404)
    district.active = not district.active
    db.session.commit()
    flash(f"{district.name} is now {'active' if district.active else 'inactive'}.",
          "success")
    return redirect(url_for("staff.districts"))


# -------------------------------------------------------------------- school tool


@bp.route("/schools", methods=["GET", "POST"])
@provisioning_required
def schools():
    """Create and list schools. District admins are limited to their own district."""
    if current_user.is_district_admin:
        districts_available = [db.session.get(District, current_user.district_id)]
        school_query = School.query.filter_by(district_id=current_user.district_id)
    else:
        districts_available = District.query.order_by(District.name).all()
        school_query = School.query

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        district_id = request.form.get("district_id", type=int)
        if current_user.is_district_admin:
            district_id = current_user.district_id

        district = db.session.get(District, district_id) if district_id else None
        rotation = request.form.get("rotation_mode", "daily")

        errors = []
        if not name:
            errors.append("A school name is required.")
        if district is None:
            errors.append("Choose a district.")
        if rotation not in ROTATION_MODES:
            errors.append("Choose a valid timetable type.")

        if errors:
            for error in errors:
                flash(error, "error")
            return redirect(url_for("staff.schools"))

        try:
            low = int(request.form.get("low_grade") or 9)
            high = int(request.form.get("high_grade") or 12)
        except ValueError:
            low, high = 9, 12
        if high < low:
            low, high = high, low

        school = School(
            name=name,
            code=_unique_code(School, request.form.get("code", "").strip()
                              or "".join(w[0] for w in name.split())[:6]),
            district_id=district.id,
            city=request.form.get("city", "").strip(),
            state=request.form.get("state", "").strip() or district.state,
            address=request.form.get("address", "").strip(),
            phone=request.form.get("phone", "").strip(),
            principal_name=request.form.get("principal_name", "").strip(),
            low_grade=low,
            high_grade=high,
            rotation_mode=rotation,
            cycle_length=int(request.form.get("cycle_length") or 6),
        )
        db.session.add(school)
        db.session.flush()

        message = f"Created {school.name} ({school.code})."

        # A school with no bell schedule and no term is unusable, so seed both —
        # slots, one day layout covering the whole week, and a current term.
        if request.form.get("seed_periods") == "on":
            slots = []
            for label, ordinal, start, end, kind in DEFAULT_PERIODS:
                period = Period(
                    school_id=school.id, name=label, ordinal=ordinal,
                    start_time=datetime.strptime(start, "%H:%M").time(),
                    end_time=datetime.strptime(end, "%H:%M").time(),
                    kind=kind,
                )
                slots.append(period)
                db.session.add(period)
            db.session.flush()

            layout = BellSchedule(
                school_id=school.id, name="Regular Day",
                description="The standard bell schedule.",
                default_weekdays="M,T,W,R,F", is_default=True,
            )
            db.session.add(layout)
            db.session.flush()
            for index, period in enumerate(slots, start=1):
                db.session.add(BellPeriod(
                    bell_schedule_id=layout.id, period_id=period.id, ordinal=index,
                    start_time=period.start_time, end_time=period.end_time,
                ))

            today = date.today()
            db.session.add(Term(
                school_id=school.id, name="Semester 1",
                school_year=f"{today.year}–{today.year + 1}",
                start_date=today - timedelta(days=30),
                end_date=today + timedelta(days=120),
                is_current=True,
            ))
            message += " Seeded slots, a “Regular Day” layout and a current term."

        admin_email = request.form.get("admin_email", "").strip().lower()
        if admin_email:
            if User.query.filter(db.func.lower(User.email) == admin_email).first():
                flash("That principal email is already in use.", "warning")
            else:
                temp = _temp_password()
                admin = User(
                    email=admin_email, role="admin",
                    first_name=request.form.get("admin_first", "School").strip() or "School",
                    last_name=request.form.get("admin_last", "Admin").strip() or "Admin",
                    school_id=school.id, title="Principal",
                )
                admin.set_password(temp)
                db.session.add(admin)
                message += f" Admin {admin_email} — temporary password: {temp}"

        db.session.commit()
        flash(message, "success")
        return redirect(url_for("staff.schools"))

    return render_template(
        "app/staff_schools.html",
        schools=school_query.order_by(School.name).all(),
        districts=[d for d in districts_available if d],
        rotation_modes=ROTATION_MODES,
    )


def _may_provision(school):
    """Provisioning reach — which is not the same as data access.

    Homeroom staff can activate or suspend any tenant; a district admin only their own.
    Neither implies permission to look inside the school.
    """
    if school is None:
        return False
    if current_user.is_homeroom_staff:
        return True
    return current_user.is_district_admin and school.district_id == current_user.district_id


@bp.route("/schools/<int:school_id>/toggle", methods=["POST"])
@provisioning_required
def toggle_school(school_id):
    school = db.session.get(School, school_id)
    if not _may_provision(school):
        abort(404)
    school.active = not school.active
    db.session.commit()
    flash(f"{school.name} is now {'active' if school.active else 'inactive'}.", "success")
    return redirect(url_for("staff.schools"))


@bp.route("/switch", methods=["POST"])
def switch_school():
    """Change which school a district admin is looking at.

    Homeroom staff can't switch, because there's nowhere for them to switch into.
    """
    if not current_user.is_authenticated or not current_user.can_switch_schools:
        abort(403)

    school = db.session.get(School, request.form.get("school_id", type=int))
    if school is None or not may_access_school(school):
        abort(403)

    session[SESSION_SCHOOL_KEY] = school.id
    flash(f"Now viewing {school.name}.", "success")
    return redirect(request.referrer or url_for("main.home"))
