# Online Course Registration System

A mini project where **students browse and register for courses each semester**,
and an **admin manages seat limits and approves registrations**.

Built as a single-file Python web app with **zero dependencies** - if the laptop
has Python 3, it runs. No pip install, no internet needed.

---

## How to run

```bash
git clone https://github.com/jahfarth/course-registration-system.git
cd course-registration-system
python app.py
```

Then open **http://localhost:8000** in a browser.

> If `python` does not work, try `python3 app.py`.
> To reset the demo data, stop the app and delete the `courses.db` file.

### Demo logins

| Role    | Username  | Password    |
|---------|-----------|-------------|
| Student | `student` | `student123` |
| Admin   | `admin`   | `admin123`   |

New students can also sign up from the login page.

---

## Features (mapped to the assignment modules)

### 1. Course Catalogue
- Browse all courses with code, instructor, schedule and semester
- Search by code / title / instructor
- Filter by semester
- Live seat availability with a capacity bar

### 2. Registration
- Register for a course - request goes to the admin as **pending**
- Drop (withdraw) a course at any time
- If a course is full, the student joins a **waitlist** with a queue position
- "My Registrations" page shows active requests and history
  (pending / approved / rejected / waitlisted / dropped)

### 3. Seat Limits
- Admin sets the maximum seats per course
- The system blocks new registrations once a course is full
- Seat limit can never be set below the current enrolment

### 4. Admin Approval
- Admin dashboard shows pending requests; **approve** or **reject** each one
- Approval is blocked if the course has filled up since the request
- Admin can move a waitlisted student to pending when a seat frees up

### Extra (intermediate) features
- Student sign-up (self registration of accounts)
- Add / edit / delete courses (delete blocked once a course has registrations)
- Per-course **roster** of students with statuses
- **CSV export** of all registrations
- Dashboard statistics (courses, students, pending, waitlisted, approved)

---

## Project structure

| File       | Purpose                                              |
|------------|------------------------------------------------------|
| `app.py`   | The whole application: routes, pages and database    |
| `courses.db` | SQLite database, created automatically on first run |
| `README.md` | This file                                           |

### Tech used
- **Backend:** Python 3 standard library (`http.server`, `sqlite3`)
- **Frontend:** HTML + CSS rendered by the server (responsive grid layout)
- **Database:** SQLite (file-based, no server to install)

---

## Typical demo flow

1. Login as **admin / admin123** - show the dashboard, stats and pending requests.
2. Login as **student / student123** in a private window - register for a course.
3. Back as admin: the request appears under *Pending approvals* - approve it.
4. As student: *My Registrations* now shows **APPROVED**.
5. Show a full course (e.g. CS460) - the student sees **Join waitlist** instead.
6. As admin: raise the seat limit, move the waitlisted student to pending, approve.
7. Register until a course fills to show the seat-limit rule blocking registrations.

---

## Likely viva questions

- **Why no frameworks?** The app uses only Python's standard library, so it runs
  anywhere Python 3 is installed. The same structure (routes, handlers, SQL)
  maps directly onto frameworks like Flask or Django.
- **How are passwords stored?** Salted SHA-256 hashes, never plain text.
- **What happens if a course is full?** New students are placed on a waitlist;
  the admin can promote them when a seat frees up.
- **How is double registration prevented?** The app checks for an existing
  live registration (pending/approved/waitlisted) before inserting.
- **Where is the data stored?** In `courses.db`, an SQLite database file with
  tables for students, admins, courses, registrations and sessions.
- **Do pending requests hold a seat?** Yes - a pending request reserves its seat
  until the admin decides, which prevents overbooking. Rejecting or withdrawing
  frees the seat again.
