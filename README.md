# Homeroom

A student information system in the spirit of Infinite Campus / ClassLink — a public
marketing site with demo and purchase request capture, plus the signed-in SIS itself.

Running at **http://localhost:9997**

## Quick start

```bash
pip install -r requirements.txt
python run.py            # seeds a demo school on first run, then serves on :9997
python run.py --reset    # wipe and reseed
```

## Demo accounts

| Role | Email | Password |
| --- | --- | --- |
| Administrator | `principal@homeroom.edu` | `admin1234` |
| Administrator | `registrar@homeroom.edu` | `admin1234` |
| Teacher | `mdelacroix@homeroom.edu` | `teach1234` |
| Student | `student@homeroom.edu` | `study1234` |

Other teachers follow `{first initial}{lastname}@homeroom.edu` (all `teach1234`); the 80 seeded
students use `first.last@students.homeroom.edu` (all `study1234`). The sign-in page lists the
three primary accounts and fills them in on click.

## The public site

| Route | Purpose |
| --- | --- |
| `/` | Landing page — positioning, module overview, pricing summary |
| `/product` | Feature detail for each module |
| `/pricing` | Three plans plus a full feature comparison matrix |
| `/demo` | **Demo request** — captures contact, enrollment size, focus areas, preferred date |
| `/purchase` | **Purchase request** — plan, seats, billing contact, PO number, add-ons, live price estimate |

Both forms validate server-side, persist to the database, and return a confirmation with a
reference number (`DEMO-00001`, `QUOTE-00001`). Submissions land in the admin console under
Administration → Demo requests / Purchase requests, where they can be moved through a status
pipeline (`new → contacted → scheduled → closed`, and `new → quoted → invoiced → won/lost`).

## The application

All six requested screens, role-aware:

- **Home** — a different dashboard per role. Students see their current period, today's classes,
  recent attendance and course-selection status. Teachers see the section they're teaching now,
  today's roster list and attendance progress. Administrators see enrollment counts, the
  school-wide attendance rate, pending course requests and the incoming sales pipeline.
- **Calendar** — month grid carrying A/B rotation days, closed days, and events by category
  (exam, holiday, assembly, deadline, sports, activity). Events can be scoped to a single course
  or grade level, so students only see what applies to them. Day detail view; staff can add and
  remove events.
- **Schedule** — the Monday–Friday grid. Each column shows its rotation type, and sections that
  meet only on A or only on B days appear only in those columns. Prints cleanly.
- **Responsive Schedule** (`/app/today`) — phone-first single-day view: a large "right now" card,
  a live countdown to the bell, and the day's timeline with past periods dimmed and the current
  one highlighted. Polls `/app/api/now` every 30s (and on tab focus) so it stays current without
  a reload.
- **Course Selection** — ranked requests with a **1st pick and a 2nd pick** per slot, inside an
  admin-controlled selection window. Validates that every required slot has a first pick, that no
  course is a first pick twice, and that a slot's two picks differ. Counselors and admins approve
  or deny request by request; approving seats the student automatically unless the section is full.
- **Attendance** — present / absent / tardy / excused, per section per day. Teachers get a
  roster defaulted to present with bulk-mark buttons and per-student notes. Students see their
  own term rate, per-course breakdown and history. Admins get a daily overview: rate, absent and
  tardy list, sections that haven't submitted yet, and chronic absences for the term.

### The three account types

| | Student | Teacher | Administrator |
| --- | --- | --- | --- |
| Home, calendar, schedule, today view | ✓ | ✓ | ✓ |
| Own attendance record | ✓ | — | — |
| Course selection (1st / 2nd picks) | ✓ | — | — |
| Take attendance, section rosters | — | ✓ | ✓ |
| Create calendar events | — | own sections | anything |
| Review / approve course requests | — | ✓ | ✓ |
| Accounts, courses, bell schedule, rotation | — | — | ✓ |
| Demo and purchase pipeline | — | — | ✓ |

Access is enforced server-side by decorators in [`homeroom/security.py`](homeroom/security.py) —
hiding a nav link is never the only thing stopping a request. Teachers are additionally scoped to
their own sections: they can't take attendance for a course they don't teach, or open the record
of a student they don't share a section with.

## Notable behavior

- **A/B rotation** is modeled throughout. Every date is an A day, a B day, or closed. A section
  declares whether it meets on `A`, `B` or `AB`, and schedules, the week grid, and the
  outstanding-attendance list all follow it.
- **Enrollment conflict detection** — enrolling a student into a course that collides with an
  existing one in the same period on overlapping rotation days is rejected.
- **Capacity** — sections track seats taken against capacity; approving a request into a full
  section warns rather than silently overfilling.
- Admin-generated passwords are random and shown once, on screen, right after creation.
- `?next=` on login only follows relative paths, so it can't be used to bounce users off-site.

## Layout

```
run.py                      entry point (port 9997)
homeroom/
  __init__.py               app factory, template filters, error handlers, `flask seed`
  extensions.py             db + login manager
  models.py                 User, Term, Period, Course, Enrollment, SelectionWindow,
                            CourseRequest, CalendarEvent, SchoolDay, AttendanceRecord,
                            DemoRequest, PurchaseRequest
  security.py               role decorators
  services.py               schedule derivation, rotation lookup, attendance rollups
  seed.py                   demo school: 8 teachers, 81 students, 24 sections, a term of
                            rotation days, 20 days of attendance, events, requests, leads
  views/                    public, auth, main, calendar, courses, attendance, admin
  templates/                base_public.html + base_app.html and their pages
  static/css/style.css      one stylesheet, responsive + print styles
instance/homeroom.sqlite    created on first run
```

## Notes

This is a demonstration application. Before real use it would need CSRF protection on forms,
rate limiting on the public forms and login, real email delivery for the request confirmations,
a production WSGI server, and a migration tool (it currently calls `create_all()` at startup).
The marketing copy, pricing and contact details are fictional.
