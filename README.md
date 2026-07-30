# Homeroom

A multi-tenant student information system — a public marketing site with demo and purchase
request capture, plus the signed-in SIS itself.

Local: **http://localhost:9997**

## Quick start

```bash
pip install -r requirements.txt
python run.py            # dev server (debugger ON — loopback only)
python serve.py          # waitress, debugger OFF — use this whenever it's reachable by others
python serve.py --lan    # same, but bound to 0.0.0.0
```

Seeding happens automatically on first run. `python run.py --reset` wipes and reseeds.

## Demo accounts

| Role | Email | Password |
| --- | --- | --- |
| Student | `student@homeroom.edu` | `study1234` |
| Parent | `parent@homeroom.edu` | `parent1234` |
| Teacher | `mdelacroix@homeroom.edu` | `teach1234` |
| School Administrator | `principal@homeroom.edu` | `admin1234` |
| District Administrator | `district@riversideusd.org` | `district1234` |
| Homeroom Staff | `hq@homeroom.example` | `homeroom1234` |

The sign-in page lists all six and fills them in on click.

## Tenancy

```
District  →  School  →  users, courses, periods, terms, calendar, documents…
```

- **Students, parents, teachers and school admins** are pinned to one school.
- **District admins** manage every school in their district and switch between them.
- **Homeroom Staff** are the vendor. They see every tenant, own the sales pipeline, and
  create districts and schools.

District admins and Homeroom Staff pick an active school from the top-bar selector; every
other screen then operates on that school.

## Roles

| | Student | Parent | Teacher | School Admin | District Admin | Homeroom Staff |
| --- | --- | --- | --- | --- | --- | --- |
| Home, calendar, schedule, today | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Own grades / assignments | ✓ | linked kids | — | — | — | — |
| Gradebook, post assignments | — | — | ✓ | ✓ | — | — |
| Take attendance | — | — | ✓ | ✓ | — | — |
| Support: pick a session | ✓ | — | — | — | — | — |
| Support: publish a session | — | — | ✓ | — | — | — |
| Support: assign & lock students | — | — | — | ✓ | ✓ | ✓ |
| Course requests (submit) | ✓ | — | — | — | — | — |
| Course requests (approve) | — | — | — | ✓ | ✓ | ✓ |
| Test scores vs district & state | — | — | own classes | ✓ | ✓ | ✓ |
| Accounts, courses, bell schedule | — | — | — | ✓ | ✓ | ✓ |
| Create districts & schools | — | — | — | — | schools only | ✓ |
| Sales pipeline | — | — | — | — | — | ✓ |

Enforced server-side in [`homeroom/security.py`](homeroom/security.py). Admins are **not**
implicitly allowed everywhere — a school admin is blocked from the sales console, and a
teacher is blocked from course-request approval, because neither is their job.

## Modules

- **Home** — a different dashboard per role. Admins get school-wide period activity rather
  than an empty personal timetable.
- **Calendar** — month grid with day types, closed days, and events scopeable to a course or
  grade level.
- **Schedule / Today** — week grid and a phone-first single-day view with a live bell
  countdown. Support blocks resolve to whichever session you're in.
- **Grades** — student report card with letter grades and GPA; teacher gradebook with inline
  editing (type a score, `M` for missing, `E` for excused); weighted categories or straight
  points, per course.
- **Assignments** — teacher authoring with publish/draft and graded/practice flags; student
  views filtered by open / missing / graded.
- **Documents** — uploads scoped school-wide, to a course, a grade level, one student, or
  staff-only. Served as attachments; scriptable formats rejected.
- **Support** — the flex block. Teachers publish what they're running each day (open work
  time or a taught session) with a seat limit; students pick one; **an administrator can
  place a student and lock it so they can't move themselves out**.
- **Attendance** — present / absent / tardy / excused per section per day, with roster
  defaults, term rates, outstanding-section tracking and chronic-absence flags.
- **Course Requests** — ranked 1st and 2nd picks inside an admin-controlled window. The
  course catalog lives inside this section rather than as its own tab.
- **Test scores** — standardized results with school vs. district vs. state comparison, plus
  per-class averages for teachers.

## Timetables

Schools don't all run the same week, so the rotation model is per-school configuration:

| Mode | Meaning |
| --- | --- |
| `daily` | Every section meets every school day |
| `ab` | Alternating A/B block days |
| `weekday` | Fixed weekdays — Mon/Wed/Fri, Tue/Thu, etc. |
| `cycle` | A numbered cycle, Day 1…N, skipping weekends |

A section's meeting days are tokens interpreted against its school's mode. Changing the mode
is a school-admin setting; there's a generator that fills a date range with the pattern.

## At-risk flagging

`student_risk()` in [`homeroom/services.py`](homeroom/services.py) combines standardized test
proficiency, course grade, missing work and attendance rate into `none` / `watch` / `high`
with the reasons attached. Teachers see flagged students on their dashboard and in each
gradebook; parents see it for their own children. It pairs with Support — flag a student,
then place them in a session they can't drop.

## Notable behavior

- **Deleting a bell period** unschedules its sections rather than destroying them, and says
  how many were affected. Deleting a course does cascade, and the confirmation says so.
- **Parent accounts must be linked to at least one student** — creation is rejected otherwise,
  since an unlinked parent account shows nothing.
- **Enrollment conflict detection** blocks two courses sharing a period and overlapping days.
- Admin-generated passwords are random and shown once.
- `?next=` on login only follows relative paths.

## Layout

```
run.py / serve.py           dev entry (debug) / waitress entry (no debug)
homeroom/
  models.py                 District, School, User, ParentLink, Course, Period, Term,
                            Assignment, GradeCategory, Grade, Document, SupportSession,
                            SupportSignup, Assessment, AssessmentResult, attendance, sales
  security.py               role decorators + active-school resolution
  services.py               schedules, grade math, assessment rollups, risk scoring
  seed.py                   2 districts, 3 schools, 8 teachers, 81 students, 13 parents,
                            20 sections, a term of school days, assignments and grades,
                            20 days of attendance, 4 assessments, a week of support sessions
  views/                    public, auth, main, calendar, courses, attendance, grades,
                            assignments, documents, support, parents, admin, district, staff
  templates/ static/
instance/homeroom.sqlite    created on first run
instance/uploads/           document uploads
```

## Notes

Demonstration application. Before real use it needs CSRF protection, rate limiting on login
and the public forms, real email delivery, a production WSGI deployment, and a migration tool
(schema changes currently rely on `create_all()`, so model edits mean a reseed). Marketing
copy, pricing, contact details and all student data are fictional.
