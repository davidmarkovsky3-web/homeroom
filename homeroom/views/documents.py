"""Documents: upload, scoped listing, and download."""

import os
import uuid

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request,
    send_from_directory, url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from ..extensions import db
from ..models import DOCUMENT_CATEGORIES, Course, Document, User
from ..security import active_school, may_view_student
from ..services import courses_for, student_courses

bp = Blueprint("documents", __name__, url_prefix="/app/documents")

# Deliberately excludes anything the browser would execute or render inline
# (.html, .svg, .js) — uploads are served as attachments regardless.
ALLOWED_EXTENSIONS = {
    "pdf", "doc", "docx", "odt", "rtf", "txt", "md",
    "xls", "xlsx", "ods", "csv",
    "ppt", "pptx", "odp",
    "png", "jpg", "jpeg", "gif", "webp", "heic",
    "zip",
}
MAX_BYTES = 15 * 1024 * 1024

# Types a browser can render safely in place. The mimetype is decided here from the
# extension — never from the uploader's Content-Type, which they control and could set
# to text/html on a file named .png.
#
# Deliberately absent: html, svg and anything Office. HTML and SVG execute script in the
# page's own origin, which would turn the document library into a way to run code as
# whoever opened it. Those stay downloads.
INLINE_TYPES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    # Rendered as plain text so markup inside them is shown, not interpreted.
    # Flask appends the charset itself, so don't put one here.
    "txt": "text/plain",
    "md": "text/plain",
    "csv": "text/plain",
}


def can_open_inline(document):
    return document.extension in INLINE_TYPES


def _upload_dir():
    path = os.path.join(current_app.instance_path, "uploads")
    os.makedirs(path, exist_ok=True)
    return path


def _allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def visible_documents(user):
    """Documents this user is allowed to see."""
    school = active_school()
    query = Document.query
    if school:
        query = query.filter(Document.school_id == school.id)
    docs = query.order_by(Document.uploaded_at.desc()).all()

    if user.is_admin or user.is_district_admin:
        return docs

    if user.is_teacher:
        my_ids = {c.id for c in courses_for(user)}
        return [d for d in docs
                if not d.student_id
                and (not d.course_id or d.course_id in my_ids)]

    if user.is_student:
        my_ids = {c.id for c in student_courses(user)}
        return [d for d in docs
                if not d.staff_only
                and (not d.course_id or d.course_id in my_ids)
                and (not d.student_id or d.student_id == user.id)
                and (not d.grade_level or d.grade_level == user.grade_level)]

    if user.is_parent:
        child_ids = {c.id for c in user.children}
        course_ids = set()
        grades = set()
        for child in user.children:
            course_ids |= {c.id for c in student_courses(child)}
            if child.grade_level:
                grades.add(child.grade_level)
        allowed = {
            link.student_id for link in user.parent_links if link.can_view_documents
        }
        return [d for d in docs
                if not d.staff_only
                and (not d.course_id or d.course_id in course_ids)
                and (not d.student_id or (d.student_id in child_ids
                                          and d.student_id in allowed))
                and (not d.grade_level or d.grade_level in grades)]

    return []


def _may_manage(document):
    if current_user.is_admin or current_user.is_district_admin:
        return True
    return document.uploaded_by_id == current_user.id


@bp.route("/")
@login_required
def index():
    category = request.args.get("category", "")
    search = request.args.get("q", "").strip().lower()

    docs = visible_documents(current_user)
    if category:
        docs = [d for d in docs if d.category == category]
    if search:
        docs = [d for d in docs
                if search in d.title.lower() or search in (d.description or "").lower()]

    can_upload = not (current_user.is_student or current_user.is_parent)
    return render_template(
        "app/documents.html",
        documents=docs,
        categories=DOCUMENT_CATEGORIES,
        category=category,
        search=search,
        can_upload=can_upload,
        can_open_inline=can_open_inline,
    )


@bp.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if current_user.is_student or current_user.is_parent:
        abort(403)

    school = active_school()
    if school is None:
        abort(403)

    if current_user.is_teacher:
        courses = courses_for(current_user)
        roster = []
        for course in courses:
            roster.extend(course.students)
        students = sorted({s.id: s for s in roster}.values(),
                          key=lambda s: (s.last_name, s.first_name))
    else:
        courses = Course.query.filter_by(school_id=school.id).order_by(Course.name).all()
        students = (
            User.query.filter_by(role="student", school_id=school.id)
            .order_by(User.last_name, User.first_name).all()
        )

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        upload_file = request.files.get("file")

        errors = []
        if not title:
            errors.append("A title is required.")
        if upload_file is None or not upload_file.filename:
            errors.append("Choose a file to upload.")
        elif not _allowed(upload_file.filename):
            errors.append(
                "That file type isn't allowed. Accepted: "
                + ", ".join(sorted(ALLOWED_EXTENSIONS))
            )

        blob = b""
        if upload_file and upload_file.filename and not errors:
            blob = upload_file.read()
            if len(blob) > MAX_BYTES:
                errors.append("Files must be 15 MB or smaller.")
            elif not blob:
                errors.append("That file is empty.")

        if errors:
            for error in errors:
                flash(error, "error")
            return render_template(
                "app/document_form.html", categories=DOCUMENT_CATEGORIES,
                courses=courses, students=students, school=school, form=request.form
            ), 400

        original = secure_filename(upload_file.filename)
        stored = f"{uuid.uuid4().hex}_{original}"
        with open(os.path.join(_upload_dir(), stored), "wb") as handle:
            handle.write(blob)

        document = Document(
            school_id=school.id,
            title=title,
            description=request.form.get("description", "").strip(),
            category=request.form.get("category", "other"),
            stored_name=stored,
            original_name=original,
            content_type=upload_file.mimetype or "",
            size_bytes=len(blob),
            course_id=request.form.get("course_id", type=int) or None,
            student_id=request.form.get("student_id", type=int) or None,
            grade_level=request.form.get("grade_level", type=int) or None,
            staff_only=request.form.get("staff_only") == "on",
            uploaded_by_id=current_user.id,
        )
        db.session.add(document)
        db.session.commit()
        flash(f"Uploaded “{document.title}”.", "success")
        return redirect(url_for("documents.index"))

    return render_template(
        "app/document_form.html", categories=DOCUMENT_CATEGORIES,
        courses=courses, students=students, school=school,
        form={"category": "other"},
    )


def _fetch_visible(document_id):
    document = db.session.get(Document, document_id)
    if document is None:
        abort(404)
    if document.id not in {d.id for d in visible_documents(current_user)}:
        abort(403)
    return document


@bp.route("/<int:document_id>/view")
@login_required
def view(document_id):
    """Open a document in the browser instead of downloading it.

    Only for types that can't execute anything. Everything else falls through to a
    download, because rendering an uploaded HTML or SVG file in-origin would let one
    user run script as another.
    """
    document = _fetch_visible(document_id)
    if not can_open_inline(document):
        return redirect(url_for("documents.download", document_id=document.id))

    response = send_from_directory(
        _upload_dir(),
        document.stored_name,
        mimetype=INLINE_TYPES[document.extension],
        as_attachment=False,
        download_name=document.original_name,
    )
    # Belt and braces: don't let the browser sniff a different type, and give the
    # response no ability to load or run anything of its own.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; img-src 'self' data:; object-src 'none'; "
        "script-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'"
    )
    return response


@bp.route("/<int:document_id>/download")
@login_required
def download(document_id):
    document = _fetch_visible(document_id)
    return send_from_directory(
        _upload_dir(),
        document.stored_name,
        as_attachment=True,
        download_name=document.original_name,
    )


@bp.route("/<int:document_id>/delete", methods=["POST"])
@login_required
def delete(document_id):
    document = db.session.get(Document, document_id)
    if document is None:
        abort(404)
    if not _may_manage(document):
        abort(403)

    title = document.title
    path = os.path.join(_upload_dir(), document.stored_name)
    db.session.delete(document)
    db.session.commit()
    try:
        os.remove(path)
    except OSError:
        pass  # row is gone; a stray file is harmless
    flash(f"Deleted “{title}”.", "success")
    return redirect(url_for("documents.index"))
