"""Public marketing site: product pages, demo requests, purchase requests."""

from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..extensions import db
from ..models import DemoRequest, PurchaseRequest

bp = Blueprint("public", __name__)

PLANS = {
    "core": {
        "name": "Core",
        "price_per_student": 4.50,
        "blurb": "Scheduling, attendance and calendar for a single school.",
        "features": [
            "Student, teacher and admin portals",
            "Daily and rotating bell schedules",
            "Period-by-period attendance",
            "School calendar with A/B day rotation",
            "Email support, next business day",
        ],
    },
    "campus": {
        "name": "Campus",
        "price_per_student": 7.25,
        "blurb": "Everything in Core plus course selection and analytics.",
        "popular": True,
        "features": [
            "Everything in Core",
            "Course selection with 1st and 2nd picks",
            "Request approval workflow for counselors",
            "Attendance analytics and daily rate tracking",
            "Roster imports (CSV / SIS export)",
            "Priority support with 4-hour response",
        ],
    },
    "district": {
        "name": "District",
        "price_per_student": 11.00,
        "blurb": "Multi-school deployments with SSO and integrations.",
        "features": [
            "Everything in Campus",
            "Unlimited schools under one district tenant",
            "SAML / OAuth single sign-on",
            "API access and nightly data exports",
            "Dedicated implementation manager",
            "99.9% uptime SLA",
        ],
    },
}

ADD_ONS = [
    ("sso", "Single sign-on (SAML/OAuth)"),
    ("import", "Historical data migration"),
    ("training", "On-site staff training"),
    ("api", "API + webhook access"),
]


def _parse_date(raw):
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


@bp.route("/")
def home():
    return render_template("public/home.html", plans=PLANS)


@bp.route("/product")
def product():
    return render_template("public/product.html")


@bp.route("/pricing")
def pricing():
    return render_template("public/pricing.html", plans=PLANS)


@bp.route("/demo", methods=["GET", "POST"])
def demo():
    if request.method == "POST":
        form = request.form
        errors = []
        name = form.get("name", "").strip()
        email = form.get("email", "").strip()
        organization = form.get("organization", "").strip()

        if not name:
            errors.append("Your name is required.")
        if "@" not in email:
            errors.append("A valid work email is required.")
        if not organization:
            errors.append("School or district name is required.")

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("public/demo.html", form=form), 400

        lead = DemoRequest(
            name=name,
            email=email,
            phone=form.get("phone", "").strip(),
            organization=organization,
            job_title=form.get("job_title", "").strip(),
            student_count=form.get("student_count", ""),
            preferred_date=_parse_date(form.get("preferred_date")),
            interests=", ".join(form.getlist("interests")),
            message=form.get("message", "").strip(),
        )
        db.session.add(lead)
        db.session.commit()
        return render_template("public/thanks.html", kind="demo", record=lead)

    return render_template("public/demo.html", form={})


@bp.route("/purchase", methods=["GET", "POST"])
def purchase():
    preselected = request.args.get("plan", "campus")
    if preselected not in PLANS:
        preselected = "campus"

    if request.method == "POST":
        form = request.form
        errors = []
        organization = form.get("organization", "").strip()
        contact_name = form.get("contact_name", "").strip()
        contact_email = form.get("contact_email", "").strip()
        plan = form.get("plan", "campus")

        try:
            seats = int(form.get("seats", 0))
        except ValueError:
            seats = 0

        if not organization:
            errors.append("School or district name is required.")
        if not contact_name:
            errors.append("A billing contact name is required.")
        if "@" not in contact_email:
            errors.append("A valid contact email is required.")
        if plan not in PLANS:
            errors.append("Choose one of the available plans.")
        if seats < 25:
            errors.append("Seat count must be at least 25.")

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template(
                "public/purchase.html", plans=PLANS, add_ons=ADD_ONS,
                selected=plan, form=form
            ), 400

        add_ons = form.getlist("add_ons")
        order = PurchaseRequest(
            organization=organization,
            contact_name=contact_name,
            contact_email=contact_email,
            phone=form.get("phone", "").strip(),
            billing_address=form.get("billing_address", "").strip(),
            plan=plan,
            seats=seats,
            term_length=form.get("term_length", "1 year"),
            payment_method=form.get("payment_method", "purchase_order"),
            po_number=form.get("po_number", "").strip(),
            add_ons=", ".join(add_ons),
            notes=form.get("notes", "").strip(),
            # No auto-generated quote. A quote is a number a person commits to, and
            # list-price × seats isn't one — staff enter it in the HQ console.
            quoted_total=None,
        )
        db.session.add(order)
        db.session.commit()
        return render_template("public/thanks.html", kind="purchase", record=order)

    return render_template(
        "public/purchase.html", plans=PLANS, add_ons=ADD_ONS,
        selected=preselected, form={"seats": 500}
    )


@bp.route("/contact")
def contact():
    return redirect(url_for("public.demo"))
