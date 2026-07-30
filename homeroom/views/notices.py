"""Notification centre and school announcements."""

from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import (
    ANNOUNCEMENT_AUDIENCES,
    Announcement,
    AnnouncementRead,
    Course,
    Notification,
    User,
)
from ..security import active_school, school_admin_required
from ..services import live_announcements, notification_feed, notify, unread_count

bp = Blueprint("notices", __name__, url_prefix="/app/notices")


def _parse_date(raw):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


@bp.route("/")
@login_required
def index():
    """The full notification centre."""
    kind = request.args.get("kind", "")
    feed = notification_feed(current_user)
    if kind:
        feed = [item for item in feed if item["kind"] == kind]

    kinds = sorted({item["kind"] for item in notification_feed(current_user)})
    return render_template(
        "app/notices.html",
        feed=feed, kinds=kinds, kind=kind,
        unread=unread_count(current_user),
    )


@bp.route("/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_read(notification_id):
    note = db.session.get(Notification, notification_id)
    if note is None or note.user_id != current_user.id:
        abort(404)
    note.read_at = datetime.utcnow()
    db.session.commit()
    return redirect(request.referrer or url_for("notices.index"))


@bp.route("/announcement/<int:announcement_id>/read", methods=["POST"])
@login_required
def mark_announcement_read(announcement_id):
    """Announcements are shared rows, so "read" is recorded per person."""
    announcement = db.session.get(Announcement, announcement_id)
    if announcement is None or not announcement.reaches(current_user):
        abort(404)
    if not AnnouncementRead.query.filter_by(user_id=current_user.id,
                                            announcement_id=announcement.id).first():
        db.session.add(AnnouncementRead(user_id=current_user.id,
                                        announcement_id=announcement.id))
        db.session.commit()
    return redirect(request.referrer or url_for("notices.index"))


@bp.route("/read-all", methods=["POST"])
@login_required
def mark_all_read():
    """Clears the badge completely — both stored notices and announcements."""
    now = datetime.utcnow()
    count = 0

    for note in Notification.query.filter_by(user_id=current_user.id, read_at=None).all():
        note.read_at = now
        count += 1

    already = {
        row.announcement_id
        for row in AnnouncementRead.query.filter_by(user_id=current_user.id).all()
    }
    for announcement in live_announcements(current_user):
        if announcement.id not in already:
            db.session.add(AnnouncementRead(user_id=current_user.id,
                                            announcement_id=announcement.id))
            count += 1

    db.session.commit()
    flash(f"Marked {count} item{'s' if count != 1 else ''} as read.", "success")
    return redirect(request.referrer or url_for("notices.index"))


# ------------------------------------------------------------------ announcements


@bp.route("/announcements", methods=["GET", "POST"])
@school_admin_required
def announcements():
    """Compose something that reaches a chosen audience."""
    school = active_school()
    if school is None:
        abort(403)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        audience = request.form.get("audience", "all")

        if not title:
            flash("Give the announcement a title.", "error")
            return redirect(url_for("notices.announcements"))
        if audience not in ANNOUNCEMENT_AUDIENCES:
            flash("Choose a valid audience.", "error")
            return redirect(url_for("notices.announcements"))

        announcement = Announcement(
            school_id=school.id,
            title=title,
            body=request.form.get("body", "").strip(),
            audience=audience,
            grade_level=request.form.get("grade_level", type=int) or None,
            urgent=request.form.get("urgent") == "on",
            action_label=request.form.get("action_label", "").strip()[:80],
            action_url=request.form.get("action_url", "").strip()[:300],
            due_on=_parse_date(request.form.get("due_on")),
            starts_on=_parse_date(request.form.get("starts_on")) or date.today(),
            expires_on=_parse_date(request.form.get("expires_on")),
            created_by_id=current_user.id,
        )
        db.session.add(announcement)
        db.session.flush()

        # Push a personal notification too, so it lands even if they don't
        # scroll the announcement list.
        sent = 0
        if request.form.get("push") == "on":
            for person in User.query.filter_by(school_id=school.id, active=True).all():
                if announcement.reaches(person):
                    notify(
                        person, announcement.title, announcement.body,
                        kind="announcement", urgent=announcement.urgent,
                        link=announcement.action_url or url_for("notices.index"),
                        created_by=current_user, announcement=announcement,
                    )
                    sent += 1

        db.session.commit()
        flash(
            f"Posted “{announcement.title}” to {announcement.audience_label.lower()}"
            + (f" and notified {sent} people." if sent else "."),
            "success",
        )
        return redirect(url_for("notices.announcements"))

    rows = (
        Announcement.query.filter_by(school_id=school.id)
        .order_by(Announcement.starts_on.desc()).all()
    )
    return render_template(
        "app/announcements.html",
        announcements=rows,
        audiences=ANNOUNCEMENT_AUDIENCES,
        school=school,
        courses=Course.query.filter_by(school_id=school.id).order_by(Course.name).all(),
    )


@bp.route("/announcements/<int:announcement_id>/delete", methods=["POST"])
@school_admin_required
def delete_announcement(announcement_id):
    school = active_school()
    announcement = db.session.get(Announcement, announcement_id)
    if announcement is None or announcement.school_id != school.id:
        abort(404)

    title = announcement.title
    Notification.query.filter_by(announcement_id=announcement.id).delete(
        synchronize_session=False)
    db.session.delete(announcement)
    db.session.commit()
    flash(f"Removed “{title}”.", "success")
    return redirect(url_for("notices.announcements"))


# ------------------------------------------------------- class announcements


def _owned_class(course_id):
    """A class the current user may post announcements to."""
    course = db.session.get(Course, course_id)
    if course is None:
        abort(404)
    if current_user.is_teacher and course.teacher_id == current_user.id:
        return course
    if current_user.is_admin and course.school_id == current_user.school_id:
        return course
    if current_user.is_district_admin and course.school and \
            course.school.district_id == current_user.district_id:
        return course
    abort(403)


@bp.route("/class/<int:course_id>", methods=["GET", "POST"])
@login_required
def class_announcements(course_id):
    """Announcements scoped to one class — its roster and their guardians only."""
    course = _owned_class(course_id)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        if not title:
            flash("Give the announcement a title.", "error")
            return redirect(url_for("notices.class_announcements", course_id=course.id))

        audience = request.form.get("audience", "families")
        if audience not in ("students", "families", "parents"):
            audience = "families"

        announcement = Announcement(
            school_id=course.school_id,
            course_id=course.id,
            title=title,
            body=request.form.get("body", "").strip(),
            audience=audience,
            urgent=request.form.get("urgent") == "on",
            action_label=request.form.get("action_label", "").strip()[:80],
            action_url=request.form.get("action_url", "").strip()[:300],
            due_on=_parse_date(request.form.get("due_on")),
            starts_on=_parse_date(request.form.get("starts_on")) or date.today(),
            expires_on=_parse_date(request.form.get("expires_on")),
            created_by_id=current_user.id,
        )
        db.session.add(announcement)
        db.session.flush()

        sent = 0
        if request.form.get("push") == "on":
            recipients = []
            for student in course.students:
                if audience in ("students", "families"):
                    recipients.append(student)
                if audience in ("parents", "families"):
                    recipients.extend(link.parent for link in student.guardian_links)
            for person in {p.id: p for p in recipients}.values():
                notify(
                    person, f"{course.code}: {announcement.title}", announcement.body,
                    kind="announcement", urgent=announcement.urgent,
                    link=announcement.action_url
                    or url_for("courses.detail", course_id=course.id),
                    created_by=current_user, announcement=announcement,
                )
                sent += 1

        db.session.commit()
        flash(
            f"Posted to {course.code}"
            + (f" and notified {sent} people." if sent else "."),
            "success",
        )
        return redirect(url_for("notices.class_announcements", course_id=course.id))

    rows = (
        Announcement.query.filter_by(course_id=course.id)
        .order_by(Announcement.starts_on.desc()).all()
    )
    return render_template("app/class_announcements.html", course=course,
                           announcements=rows)


# --------------------------------------------------------------- nudge a student


@bp.route("/nudge", methods=["POST"])
@login_required
def nudge():
    """Teacher or admin sends a student (and optionally guardians) a direct reminder."""
    if not (current_user.is_teacher or current_user.is_admin):
        abort(403)

    student = db.session.get(User, request.form.get("student_id", type=int))
    if student is None or not student.is_student:
        abort(404)

    from ..security import may_view_student
    if not may_view_student(student):
        abort(403)

    title = request.form.get("title", "").strip() or "A note from your teacher"
    body = request.form.get("body", "").strip()
    notify(student, title, body, kind="reminder", created_by=current_user,
           link=url_for("grades.report"))

    reached = [student.full_name]
    if request.form.get("include_guardians") == "on":
        for link in student.guardian_links:
            notify(link.parent, f"About {student.known_as}: {title}", body,
                   kind="reminder", created_by=current_user,
                   link=url_for("parents.child", student_id=student.id))
            reached.append(link.parent.full_name)

    db.session.commit()
    flash("Sent to " + ", ".join(reached) + ".", "success")
    return redirect(request.referrer or url_for("grades.gradebook"))
