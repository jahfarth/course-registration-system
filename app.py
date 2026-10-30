#!/usr/bin/env python3
"""
Online Course Registration System - Mini Project
=================================================
Students browse the course catalogue and register/drop courses each semester.
Admins set seat limits, approve registrations and manage the catalogue.

Modules (as per the assignment sheet):
  1. Course Catalogue - browse, search and filter courses by semester
  2. Registration     - students register, drop, or join a waitlist
  3. Seat Limits      - admin sets maximum seats per course
  4. Admin Approval   - admin approves or rejects pending registrations

How to run (only needs Python 3 - no packages to install):
    python app.py
Then open:  http://localhost:8000

Demo logins (seeded on first run):
    Student:  student / student123
    Admin:    admin   / admin123
New students can also sign up from the login page.

Data is stored in courses.db (SQLite) next to this file.
Delete courses.db to reset the demo data.
"""

import hashlib
import html
import os
import secrets
import sqlite3
import threading
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8000
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "courses.db")
db_lock = threading.Lock()

# Registration lifecycle: waitlisted -> pending -> approved
#                         (any active state) -> dropped, pending -> rejected
ACTIVE_STATUSES = ("pending", "approved", "waitlisted")


# ---------------------------------------------------------------- database

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def hash_pw(password):
    """Salted SHA-256 - simple hashing for a class demo (not production-grade)."""
    return hashlib.sha256(("crs-mini:" + password).encode()).hexdigest()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def init_db():
    """Create tables and seed demo data on first run."""
    conn = get_db()
    with db_lock:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            instructor TEXT NOT NULL,
            schedule TEXT NOT NULL,
            semester TEXT NOT NULL,
            description TEXT NOT NULL,
            seat_limit INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL REFERENCES students(id),
            course_id INTEGER NOT NULL REFERENCES courses(id),
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            role TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """)
        if conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0] == 0:
            courses = [
                ("CS101", "Introduction to Programming", "Dr. Rao", "Mon/Wed 9:00-10:30",
                 "Semester 1", "Python basics: variables, loops, functions and simple programs.", 40),
                ("CS201", "Data Structures", "Prof. Mehta", "Tue/Thu 11:00-12:30",
                 "Semester 2", "Arrays, linked lists, stacks, queues, trees and graphs.", 35),
                ("CS301", "Database Management Systems", "Dr. Iyer", "Mon/Thu 2:00-3:30",
                 "Semester 3", "SQL, ER diagrams, normalisation and transactions.", 30),
                ("CS302", "Computer Networks", "Prof. Nair", "Wed/Fri 10:00-11:30",
                 "Semester 4", "TCP/IP, routing, switching and network protocols.", 30),
                ("CS350", "Operating Systems", "Dr. Kulkarni", "Tue/Fri 1:00-2:30",
                 "Semester 5", "Processes, threads, scheduling, memory and file systems.", 25),
                ("CS401", "Cyber Security Fundamentals", "Dr. Thomas", "Mon/Wed 3:00-4:30",
                 "Semester 6", "Threats, cryptography basics, authentication and policies.", 3),
                ("MA201", "Discrete Mathematics", "Prof. Sharma", "Tue/Thu 9:00-10:30",
                 "Semester 3", "Logic, sets, relations and combinatorics for CS.", 45),
                ("CS420", "Web Technologies", "Dr. D'Souza", "Wed/Fri 2:00-3:30",
                 "Semester 6", "HTML, CSS, JavaScript and simple web applications.", 28),
                ("CS455", "Machine Learning", "Dr. Farida", "Tue/Fri 3:00-4:30",
                 "Semester 7", "Regression, classification, clustering and model evaluation.", 24),
                ("CS460", "Cloud Computing", "Prof. Kamath", "Mon/Thu 10:00-11:30",
                 "Semester 7", "Virtualisation, containers, and public cloud services.", 2),
            ]
            conn.executemany(
                "INSERT INTO courses (code,title,instructor,schedule,semester,description,seat_limit)"
                " VALUES (?,?,?,?,?,?,?)", courses)
        if conn.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO students (username,password_hash,full_name) VALUES (?,?,?)",
                [("student", hash_pw("student123"), "Demo Student"),
                 ("aisha", hash_pw("aisha123"), "Aisha Khan"),
                 ("rahul", hash_pw("rahul123"), "Rahul Verma")])
        if conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0:
            conn.execute("INSERT INTO admins (username,password_hash,full_name) VALUES (?,?,?)",
                         ("admin", hash_pw("admin123"), "Course Admin"))
        # Seed a little activity so the admin dashboard is alive in the demo
        if conn.execute("SELECT COUNT(*) FROM registrations").fetchone()[0] == 0:
            sid = {r["username"]: r["id"] for r in conn.execute("SELECT * FROM students")}
            cid = {r["code"]: r["id"] for r in conn.execute("SELECT * FROM courses")}
            seed_regs = [
                (sid["aisha"], cid["CS101"], "approved"),
                (sid["rahul"], cid["CS101"], "approved"),
                (sid["aisha"], cid["CS301"], "pending"),
                (sid["rahul"], cid["CS401"], "approved"),
                (sid["aisha"], cid["CS401"], "approved"),
                (sid["aisha"], cid["CS460"], "waitlisted"),
            ]
            conn.executemany(
                "INSERT INTO registrations (student_id,course_id,status,created_at)"
                " VALUES (?,?,?,?)",
                [(s, c, st, now()) for s, c, st in seed_regs])
        conn.commit()
    conn.close()


# ---------------------------------------------------------------- queries

def seats_taken(conn, course_id):
    """Seats held by pending or approved registrations (waitlist does not hold a seat)."""
    return conn.execute(
        "SELECT COUNT(*) FROM registrations WHERE course_id=? AND status IN ('pending','approved')",
        (course_id,)).fetchone()[0]


def active_reg(conn, student_id, course_id):
    """The student's live registration (pending/approved/waitlisted), if any."""
    return conn.execute(
        "SELECT * FROM registrations WHERE student_id=? AND course_id=? AND status IN (?,?,?)",
        (student_id, course_id, *ACTIVE_STATUSES)).fetchone()


def waitlist_position(conn, reg):
    """1-based position of a waitlisted registration in its course queue."""
    return conn.execute(
        "SELECT COUNT(*) FROM registrations WHERE course_id=? AND status='waitlisted'"
        " AND created_at <= ?",
        (reg["course_id"], reg["created_at"])).fetchone()[0]


def esc(s):
    return html.escape(str(s), quote=True)


# ---------------------------------------------------------------- views

PAGE_CSS = """
:root { --navy:#1e3a8a; --navy2:#1e40af; --ink:#1e293b; --mut:#64748b; }
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:'Segoe UI',Arial,sans-serif; background:#eef2f7; color:var(--ink); }
nav { background:linear-gradient(90deg,#172554,#1e3a8a); color:#fff; padding:14px 28px;
      display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;
      box-shadow:0 2px 8px rgba(15,23,42,.25); }
nav .brand { font-size:1.15rem; font-weight:700; letter-spacing:.02em; }
nav a { color:#c7d7fe; text-decoration:none; margin-left:18px; font-size:.95rem; }
nav a:hover { color:#fff; text-decoration:underline; }
.container { max-width:1080px; margin:28px auto; padding:0 20px 40px; }
h1 { font-size:1.5rem; margin-bottom:6px; }
.sub { color:var(--mut); margin-bottom:20px; }
.msg { padding:12px 16px; border-radius:10px; margin-bottom:18px; font-size:.95rem; }
.msg.ok { background:#dcfce7; color:#166534; border:1px solid #86efac; }
.msg.err { background:#fee2e2; color:#991b1b; border:1px solid #fca5a5; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(310px,1fr)); gap:18px; }
.card { background:#fff; border-radius:14px; padding:18px 20px;
        box-shadow:0 1px 5px rgba(15,23,42,.09); display:flex; flex-direction:column; }
.card h3 { font-size:1.06rem; margin:4px 0; }
.card .code { color:var(--navy); font-weight:700; font-size:.82rem; letter-spacing:.06em; }
.card p { font-size:.88rem; color:#475569; margin:8px 0; flex:1; }
.meta { font-size:.83rem; color:var(--mut); margin-top:2px; }
.sem { display:inline-block; background:#e0e7ff; color:#3730a3; font-size:.74rem;
       font-weight:700; padding:2px 9px; border-radius:999px; margin-left:8px; }
.capacity { margin:10px 0 12px; }
.capacity .bar { height:8px; background:#e2e8f0; border-radius:999px; overflow:hidden; margin-top:5px; }
.capacity .fill { height:100%; background:#15803d; border-radius:999px; }
.capacity .fill.warn { background:#d97706; } .capacity .fill.full { background:#b91c1c; }
.capacity .lbl { font-size:.82rem; font-weight:600; color:#334155; }
.actions { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
button, .btn { background:var(--navy); color:#fff; border:none; padding:8px 16px;
        border-radius:8px; font-size:.9rem; cursor:pointer; text-decoration:none; display:inline-block; }
button:hover, .btn:hover { background:var(--navy2); }
button.grey { background:#64748b; } button.grey:hover { background:#475569; }
button.red { background:#b91c1c; } button.red:hover { background:#991b1b; }
button.green { background:#15803d; } button.green:hover { background:#166534; }
button.amber { background:#b45309; } button.amber:hover { background:#92400e; }
button:disabled { background:#cbd5e1; cursor:not-allowed; }
.badge { display:inline-block; padding:3px 10px; border-radius:999px; font-size:.76rem; font-weight:700; }
.badge.pending { background:#fef9c3; color:#854d0e; }
.badge.approved { background:#dcfce7; color:#166534; }
.badge.rejected { background:#fee2e2; color:#991b1b; }
.badge.dropped { background:#e2e8f0; color:#475569; }
.badge.waitlisted { background:#ffedd5; color:#9a3412; }
table { width:100%; border-collapse:collapse; background:#fff; border-radius:12px;
        overflow:hidden; box-shadow:0 1px 4px rgba(15,23,42,.10); margin-bottom:8px; }
th, td { text-align:left; padding:11px 14px; font-size:.89rem; vertical-align:middle; }
th { background:var(--navy); color:#fff; font-weight:600; }
tr:nth-child(even) td { background:#f8fafc; }
form.inline { display:inline; }
input[type=text], input[type=password], input[type=number], input[type=search], select {
    padding:9px 12px; border:1px solid #cbd5e1; border-radius:8px; font-size:.93rem; background:#fff; }
input[type=number] { width:88px; }
.login-box { max-width:400px; margin:52px auto; background:#fff; padding:32px;
             border-radius:16px; box-shadow:0 6px 24px rgba(15,23,42,.13); }
.login-box h1 { text-align:center; margin-bottom:4px; }
.login-box .sub { text-align:center; }
.login-box label { font-size:.86rem; font-weight:600; display:block; margin:14px 0 6px; }
.login-box input { width:100%; }
.login-box button { width:100%; margin-top:20px; padding:11px; }
.login-box .alt { text-align:center; margin-top:14px; font-size:.88rem; }
.login-box .alt a { color:var(--navy); }
.hint { margin-top:18px; font-size:.82rem; color:var(--mut); background:#f1f5f9;
        padding:12px; border-radius:10px; line-height:1.55; }
.toolbar { display:flex; gap:10px; margin-bottom:22px; flex-wrap:wrap; }
.toolbar input[type=search] { flex:1; min-width:220px; }
.section-title { margin:30px 0 14px; font-size:1.13rem; }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:14px; margin-bottom:6px; }
.stat { background:#fff; border-radius:12px; padding:16px 18px; box-shadow:0 1px 4px rgba(15,23,42,.09); }
.stat .num { font-size:1.6rem; font-weight:700; color:var(--navy); }
.stat .lbl { font-size:.82rem; color:var(--mut); font-weight:600; }
.panel { background:#fff; padding:18px 20px; border-radius:12px;
         box-shadow:0 1px 4px rgba(15,23,42,.10); }
form.addcourse { display:grid; grid-template-columns:1fr 2fr 1fr; gap:10px; align-items:end; }
form.addcourse label { font-size:.78rem; font-weight:700; color:#475569; display:block; margin-bottom:4px; }
form.addcourse input, form.addcourse select { width:100%; }
form.editcourse { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
form.editcourse label { font-size:.82rem; font-weight:700; color:#475569; display:block; margin-bottom:5px; }
form.editcourse input, form.editcourse select { width:100%; }
form.editcourse .full { grid-column:1 / -1; }
footer { text-align:center; color:#94a3b8; font-size:.78rem; padding:22px 0 6px; }
"""

SEMESTERS = [f"Semester {i}" for i in range(1, 9)]


def layout(title, body, session=None, msg=None, msg_kind="ok"):
    """Shared page frame: navbar + optional message banner + content."""
    if session:
        if session["role"] == "student":
            links = ('<a href="/catalogue">Course Catalogue</a>'
                     '<a href="/my-courses">My Registrations</a>')
        else:
            links = ('<a href="/admin">Dashboard</a>'
                     '<a href="/admin/export">Export CSV</a>')
        nav = (f'<div class="brand">Course Registration System</div>'
               f'<div>{links}<a href="/logout">Logout ({esc(session["name"])})</a></div>')
    else:
        nav = '<div class="brand">Course Registration System</div>'
    banner = f'<div class="msg {msg_kind}">{esc(msg)}</div>' if msg else ""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} - Course Registration System</title><style>{PAGE_CSS}</style></head>
<body><nav>{nav}</nav><div class="container">{banner}{body}</div>
<footer>Mini Project &middot; Online Course Registration System</footer></body></html>"""


def capacity_html(taken, limit):
    """Small progress bar showing how full a course is."""
    pct = min(100, round(100 * taken / limit)) if limit else 100
    cls = "full" if taken >= limit else ("warn" if pct >= 70 else "")
    left = limit - taken
    lbl = (f"{left} of {limit} seats left" if left > 0 else f"Full - {limit} seats taken")
    return (f'<div class="capacity"><div class="lbl">{lbl}</div>'
            f'<div class="bar"><div class="fill {cls}" style="width:{pct}%"></div></div></div>')


# ---------------------------------------------------------------- HTTP app

class App(BaseHTTPRequestHandler):

    # ---------- plumbing ----------
    def log_message(self, fmt, *args):  # keep the console tidy during the demo
        pass

    def send_html(self, text, status=200):
        data = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_csv(self, filename, text):
        data = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, url):
        self.send_response(303)
        self.send_header("Location", url)
        self.end_headers()

    def read_form(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        return {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}

    def current_session(self):
        cookie = self.headers.get("Cookie", "")
        token = None
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("crs_session="):
                token = part.split("=", 1)[1]
        if not token:
            return None
        conn = get_db()
        row = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
        if not row:
            conn.close()
            return None
        table = "students" if row["role"] == "student" else "admins"
        user = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row["user_id"],)).fetchone()
        conn.close()
        if not user:
            return None
        return {"role": row["role"], "id": row["user_id"], "name": user["full_name"]}

    def require_role(self, role):
        session = self.current_session()
        if not session or session["role"] != role:
            self.redirect("/login")
            return None
        return session

    # ---------- routing ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        msg, kind = query.get("msg", [None])[0], query.get("kind", ["ok"])[0]
        routes = {
            "/": self.page_home,
            "/login": self.page_login,
            "/signup": self.page_signup,
            "/logout": self.page_logout,
            "/catalogue": self.page_catalogue,
            "/my-courses": self.page_my_courses,
            "/admin": self.page_admin,
            "/admin/edit-course": self.page_edit_course,
            "/admin/roster": self.page_roster,
            "/admin/export": self.page_export,
        }
        handler = routes.get(parsed.path)
        if handler:
            handler(msg, kind, query)
        else:
            self.send_html("<div class='container'><h1>404 - page not found</h1></div>", 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        form = self.read_form()
        routes = {
            "/login": self.action_login,
            "/signup": self.action_signup,
            "/register": self.action_register,
            "/drop": self.action_drop,
            "/admin/set-seats": self.action_set_seats,
            "/admin/decide": self.action_decide,
            "/admin/promote": self.action_promote,
            "/admin/add-course": self.action_add_course,
            "/admin/edit-course": self.action_edit_course,
            "/admin/delete-course": self.action_delete_course,
        }
        handler = routes.get(path)
        if handler:
            handler(form)
        else:
            self.send_html("<div class='container'><h1>404 - page not found</h1></div>", 404)

    # ---------- auth ----------
    def page_home(self, msg=None, kind="ok", query=None):
        session = self.current_session()
        if session and session["role"] == "student":
            self.redirect("/catalogue")
        elif session:
            self.redirect("/admin")
        else:
            self.redirect("/login")

    def page_login(self, msg=None, kind="ok", query=None):
        body = """
        <div class="login-box">
          <h1>Welcome back</h1>
          <p class="sub">Sign in to register for courses</p>
          <form method="post" action="/login">
            <label>Username</label>
            <input type="text" name="username" required autocomplete="off">
            <label>Password</label>
            <input type="password" name="password" required>
            <button type="submit">Login</button>
          </form>
          <div class="alt">New student? <a href="/signup">Create an account</a></div>
          <div class="hint"><b>Demo logins</b><br>
            Student &rarr; <b>student / student123</b><br>
            Admin &rarr; <b>admin / admin123</b></div>
        </div>"""
        self.send_html(layout("Login", body, msg=msg, msg_kind=kind))

    def page_signup(self, msg=None, kind="ok", query=None):
        body = """
        <div class="login-box">
          <h1>Create account</h1>
          <p class="sub">Sign up as a student</p>
          <form method="post" action="/signup">
            <label>Full name</label>
            <input type="text" name="full_name" required autocomplete="off">
            <label>Username</label>
            <input type="text" name="username" required autocomplete="off">
            <label>Password</label>
            <input type="password" name="password" required minlength="4">
            <button type="submit">Sign up</button>
          </form>
          <div class="alt">Already registered? <a href="/login">Login</a></div>
        </div>"""
        self.send_html(layout("Sign up", body, msg=msg, msg_kind=kind))

    def _create_session(self, role, user_id):
        token = secrets.token_hex(24)
        conn = get_db()
        with db_lock:
            conn.execute("INSERT INTO sessions VALUES (?,?,?,?)", (token, role, user_id, now()))
            conn.commit()
        conn.close()
        self.send_response(303)
        self.send_header("Set-Cookie", f"crs_session={token}; Path=/; HttpOnly")
        self.send_header("Location", "/catalogue" if role == "student" else "/admin")
        self.end_headers()

    def action_login(self, form):
        username, password = form.get("username", "").strip(), form.get("password", "")
        conn = get_db()
        row = conn.execute("SELECT * FROM students WHERE username=?", (username,)).fetchone()
        role = "student"
        if not row:
            row = conn.execute("SELECT * FROM admins WHERE username=?", (username,)).fetchone()
            role = "admin"
        conn.close()
        if not row or row["password_hash"] != hash_pw(password):
            self.redirect("/login?kind=err&msg=Invalid username or password")
            return
        self._create_session(role, row["id"])

    def action_signup(self, form):
        full_name = form.get("full_name", "").strip()
        username = form.get("username", "").strip().lower()
        password = form.get("password", "")
        if not full_name or not username or len(password) < 4:
            self.redirect("/signup?kind=err&msg=Fill all fields (password min 4 characters)")
            return
        conn = get_db()
        clash = conn.execute("SELECT 1 FROM students WHERE username=?", (username,)).fetchone() or \
            conn.execute("SELECT 1 FROM admins WHERE username=?", (username,)).fetchone()
        if clash:
            conn.close()
            self.redirect("/signup?kind=err&msg=Username already taken")
            return
        with db_lock:
            cur = conn.execute("INSERT INTO students (username,password_hash,full_name) VALUES (?,?,?)",
                               (username, hash_pw(password), full_name))
            conn.commit()
            new_id = cur.lastrowid
        conn.close()
        self._create_session("student", new_id)

    def page_logout(self, msg=None, kind="ok", query=None):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("crs_session="):
                conn = get_db()
                with db_lock:
                    conn.execute("DELETE FROM sessions WHERE token=?", (part.split("=", 1)[1],))
                    conn.commit()
                conn.close()
        self.send_response(303)
        self.send_header("Set-Cookie", "crs_session=; Path=/; Max-Age=0")
        self.send_header("Location", "/login")
        self.end_headers()

    # ---------- student: catalogue ----------
    def page_catalogue(self, msg=None, kind="ok", query=None):
        session = self.require_role("student")
        if not session:
            return
        q = (query or {}).get("q", [""])[0].strip()
        sem = (query or {}).get("semester", [""])[0].strip()
        conn = get_db()
        sql, params = "SELECT * FROM courses WHERE 1=1", []
        if q:
            sql += " AND (code LIKE ? OR title LIKE ? OR instructor LIKE ?)"
            params += [f"%{q}%"] * 3
        if sem:
            sql += " AND semester=?"
            params.append(sem)
        courses = conn.execute(sql + " ORDER BY code", params).fetchall()

        cards = []
        for c in courses:
            taken = seats_taken(conn, c["id"])
            reg = active_reg(conn, session["id"], c["id"])
            if reg:
                extra = ""
                if reg["status"] == "waitlisted":
                    extra = f" (position {waitlist_position(conn, reg)})"
                action = (f'<span class="badge {reg["status"]}">{reg["status"].upper()}{extra}</span>'
                          f'<form class="inline" method="post" action="/drop">'
                          f'<input type="hidden" name="reg_id" value="{reg["id"]}">'
                          f'<button class="grey" type="submit">Withdraw</button></form>')
            elif taken >= c["seat_limit"]:
                action = (f'<form class="inline" method="post" action="/register">'
                          f'<input type="hidden" name="course_id" value="{c["id"]}">'
                          f'<button class="amber" type="submit">Join waitlist</button></form>')
            else:
                action = (f'<form class="inline" method="post" action="/register">'
                          f'<input type="hidden" name="course_id" value="{c["id"]}">'
                          f'<button type="submit">Register</button></form>')
            cards.append(f"""<div class="card">
              <div><span class="code">{esc(c['code'])}</span><span class="sem">{esc(c['semester'])}</span></div>
              <h3>{esc(c['title'])}</h3>
              <div class="meta">{esc(c['instructor'])} &bull; {esc(c['schedule'])}</div>
              <p>{esc(c['description'])}</p>
              {capacity_html(taken, c['seat_limit'])}
              <div class="actions">{action}</div></div>""")
        conn.close()
        listing = "".join(cards) if cards else "<p>No courses match your search.</p>"
        sem_opts = '<option value="">All semesters</option>' + "".join(
            f'<option {"selected" if s == sem else ""}>{s}</option>' for s in SEMESTERS)
        body = f"""<h1>Course Catalogue</h1>
        <p class="sub">Browse and register for courses</p>
        <form class="toolbar" method="get" action="/catalogue">
          <input type="search" name="q" placeholder="Search by code, title or instructor..." value="{esc(q)}">
          <select name="semester">{sem_opts}</select>
          <button type="submit">Search</button></form>
        <div class="grid">{listing}</div>"""
        self.send_html(layout("Course Catalogue", body, session, msg, kind))

    def action_register(self, form):
        session = self.require_role("student")
        if not session:
            return
        course_id = form.get("course_id", "")
        conn = get_db()
        course = conn.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        if not course:
            conn.close()
            self.redirect("/catalogue?kind=err&msg=Course not found")
            return
        if active_reg(conn, session["id"], course["id"]):
            conn.close()
            self.redirect("/catalogue?kind=err&msg=You already have a live registration for "
                          + course["code"])
            return
        full = seats_taken(conn, course["id"]) >= course["seat_limit"]
        status = "waitlisted" if full else "pending"
        with db_lock:
            conn.execute("INSERT INTO registrations (student_id,course_id,status,created_at)"
                         " VALUES (?,?,?,?)", (session["id"], course["id"], status, now()))
            conn.commit()
        conn.close()
        if status == "waitlisted":
            self.redirect("/my-courses?msg=" + course["code"]
                          + " is full - you are on the waitlist")
        else:
            self.redirect("/my-courses?msg=Registration sent for admin approval: " + course["code"])

    # ---------- student: my registrations ----------
    def page_my_courses(self, msg=None, kind="ok", query=None):
        session = self.require_role("student")
        if not session:
            return
        conn = get_db()
        rows = conn.execute(
            """SELECT r.id AS reg_id, r.status, r.created_at, r.course_id,
                      c.code, c.title, c.instructor, c.schedule, c.semester
               FROM registrations r JOIN courses c ON c.id = r.course_id
               WHERE r.student_id=? ORDER BY r.created_at DESC""",
            (session["id"],)).fetchall()
        active = [r for r in rows if r["status"] in ACTIVE_STATUSES]
        history = [r for r in rows if r["status"] not in ACTIVE_STATUSES]

        def table(lines, empty):
            if not lines:
                return f"<p>{empty}</p>"
            return ("<table><tr><th>Code</th><th>Course</th><th>Semester</th><th>Schedule</th>"
                    "<th>Status</th><th></th></tr>" + "".join(lines) + "</table>")

        alines = []
        for r in active:
            extra = ""
            if r["status"] == "waitlisted":
                extra = f" (position {waitlist_position(conn, r)})"
            alines.append(f"""<tr><td>{esc(r['code'])}</td><td>{esc(r['title'])}</td>
              <td>{esc(r['semester'])}</td><td>{esc(r['schedule'])}</td>
              <td><span class="badge {r['status']}">{r['status'].upper()}{extra}</span></td>
              <td><form class="inline" method="post" action="/drop">
                <input type="hidden" name="reg_id" value="{r['reg_id']}">
                <button class="grey" type="submit">Withdraw</button></form></td></tr>""")
        hlines = []
        for r in history:
            hlines.append(f"""<tr><td>{esc(r['code'])}</td><td>{esc(r['title'])}</td>
              <td>{esc(r['semester'])}</td><td>{esc(r['schedule'])}</td>
              <td><span class="badge {r['status']}">{r['status'].upper()}</span></td><td></td></tr>""")
        conn.close()
        body = (f"<h1>My Registrations</h1><p class='sub'>Your course requests and their status</p>"
                f"<h2 class='section-title'>Active</h2>"
                + table(alines, "Nothing active - visit the catalogue to register.")
                + f"<h2 class='section-title'>History</h2>"
                + table(hlines, "No past registrations."))
        self.send_html(layout("My Registrations", body, session, msg, kind))

    def action_drop(self, form):
        session = self.require_role("student")
        if not session:
            return
        conn = get_db()
        with db_lock:
            cur = conn.execute(
                "UPDATE registrations SET status='dropped' WHERE id=? AND student_id=?"
                " AND status IN (?,?,?)",
                (form.get("reg_id", ""), session["id"], *ACTIVE_STATUSES))
            conn.commit()
        conn.close()
        if cur.rowcount:
            self.redirect("/my-courses?msg=Registration withdrawn")
        else:
            self.redirect("/my-courses?kind=err&msg=Could not withdraw that registration")

    # ---------- admin: dashboard ----------
    def page_admin(self, msg=None, kind="ok", query=None):
        session = self.require_role("admin")
        if not session:
            return
        conn = get_db()
        n_courses = conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
        n_students = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        n_pending = conn.execute(
            "SELECT COUNT(*) FROM registrations WHERE status='pending'").fetchone()[0]
        n_wait = conn.execute(
            "SELECT COUNT(*) FROM registrations WHERE status='waitlisted'").fetchone()[0]
        n_approved = conn.execute(
            "SELECT COUNT(*) FROM registrations WHERE status='approved'").fetchone()[0]

        stats = f"""<div class="stats">
          <div class="stat"><div class="num">{n_courses}</div><div class="lbl">Courses</div></div>
          <div class="stat"><div class="num">{n_students}</div><div class="lbl">Students</div></div>
          <div class="stat"><div class="num">{n_pending}</div><div class="lbl">Pending approvals</div></div>
          <div class="stat"><div class="num">{n_wait}</div><div class="lbl">Waitlisted</div></div>
          <div class="stat"><div class="num">{n_approved}</div><div class="lbl">Approved</div></div>
        </div>"""

        pending = conn.execute(
            """SELECT r.id AS reg_id, r.created_at, s.full_name AS student,
                      c.code, c.title, c.seat_limit, c.id AS course_id
               FROM registrations r JOIN students s ON s.id=r.student_id
               JOIN courses c ON c.id=r.course_id
               WHERE r.status='pending' ORDER BY r.created_at""").fetchall()
        if pending:
            plines = []
            for p in pending:
                taken = seats_taken(conn, p["course_id"])
                full_note = ' <span class="badge rejected">FULL</span>' if taken >= p["seat_limit"] else ""
                plines.append(f"""<tr><td>{esc(p['student'])}</td>
                  <td>{esc(p['code'])} - {esc(p['title'])}</td>
                  <td>{taken} / {p['seat_limit']}{full_note}</td><td>{esc(p['created_at'])}</td>
                  <td><form class="inline" method="post" action="/admin/decide">
                    <input type="hidden" name="reg_id" value="{p['reg_id']}">
                    <button class="green" name="decision" value="approve">Approve</button>
                    <button class="red" name="decision" value="reject">Reject</button>
                  </form></td></tr>""")
            ptable = ("<table><tr><th>Student</th><th>Course</th><th>Seats</th><th>Requested</th>"
                      "<th>Decision</th></tr>" + "".join(plines) + "</table>")
        else:
            ptable = "<p>No pending registrations right now.</p>"

        waitlisted = conn.execute(
            """SELECT r.id AS reg_id, r.created_at, r.course_id, s.full_name AS student,
                      c.code, c.title, c.seat_limit
               FROM registrations r JOIN students s ON s.id=r.student_id
               JOIN courses c ON c.id=r.course_id
               WHERE r.status='waitlisted' ORDER BY r.course_id, r.created_at""").fetchall()
        if waitlisted:
            wlines = []
            for w in waitlisted:
                pos = waitlist_position(conn, {"course_id": w["course_id"],
                                               "created_at": w["created_at"]})
                free = w["seat_limit"] - seats_taken(conn, w["course_id"])
                promote = ""
                if free > 0:
                    promote = (f'<form class="inline" method="post" action="/admin/promote">'
                               f'<input type="hidden" name="reg_id" value="{w["reg_id"]}">'
                               f'<button class="green" type="submit">Move to pending</button></form>')
                else:
                    promote = '<span class="badge pending">NO FREE SEAT</span>'
                wlines.append(f"""<tr><td>{esc(w['code'])} - {esc(w['title'])}</td>
                  <td>{esc(w['student'])}</td><td>#{pos}</td><td>{free} free</td>
                  <td>{promote}</td></tr>""")
            wtable = ("<table><tr><th>Course</th><th>Student</th><th>Position</th>"
                      "<th>Seats</th><th></th></tr>" + "".join(wlines) + "</table>")
        else:
            wtable = "<p>Waitlists are empty.</p>"

        courses = conn.execute("SELECT * FROM courses ORDER BY code").fetchall()
        clines = []
        for c in courses:
            taken = seats_taken(conn, c["id"])
            n_reg = conn.execute("SELECT COUNT(*) FROM registrations WHERE course_id=?",
                                 (c["id"],)).fetchone()[0]
            delete = ('<button class="red" type="submit" '
                      'onclick="return confirm(\'Delete this course?\')">Delete</button>') \
                if n_reg == 0 else '<button class="red" disabled title="Has registrations">Delete</button>'
            clines.append(f"""<tr><td>{esc(c['code'])}</td><td>{esc(c['title'])}</td>
              <td>{esc(c['semester'])}</td><td>{taken} / {c['seat_limit']}</td>
              <td><form class="inline" method="post" action="/admin/set-seats">
                <input type="hidden" name="course_id" value="{c['id']}">
                <input type="number" name="seat_limit" min="{taken}" value="{c['seat_limit']}">
                <button type="submit">Update</button></form></td>
              <td><a class="btn grey" style="padding:7px 12px" href="/admin/edit-course?id={c['id']}">Edit</a>
                  <a class="btn" style="padding:7px 12px" href="/admin/roster?id={c['id']}">Roster</a>
                  <form class="inline" method="post" action="/admin/delete-course">
                    <input type="hidden" name="course_id" value="{c['id']}">{delete}</form></td></tr>""")
        ctable = ("<table><tr><th>Code</th><th>Course</th><th>Semester</th><th>Enrolled/Limit</th>"
                  "<th>Set seat limit</th><th>Actions</th></tr>" + "".join(clines) + "</table>")
        conn.close()

        sem_opts = "".join(f"<option>{s}</option>" for s in SEMESTERS)
        body = f"""<h1>Admin Dashboard</h1>
        <p class="sub">Approve registrations, manage seat limits and the catalogue</p>
        {stats}
        <h2 class="section-title">Pending approvals</h2>{ptable}
        <h2 class="section-title">Waitlists</h2>{wtable}
        <h2 class="section-title">Courses and seat limits</h2>{ctable}
        <h2 class="section-title">Add a new course</h2>
        <div class="panel"><form class="addcourse" method="post" action="/admin/add-course">
          <div><label>Code</label><input type="text" name="code" required placeholder="CS499"></div>
          <div><label>Title</label><input type="text" name="title" required placeholder="Course title"></div>
          <div><label>Instructor</label><input type="text" name="instructor" required placeholder="Dr. ..."></div>
          <div><label>Schedule</label><input type="text" name="schedule" required placeholder="Mon/Wed 9:00"></div>
          <div><label>Semester</label><select name="semester">{sem_opts}</select></div>
          <div><label>Seats</label><input type="number" name="seat_limit" min="1" value="30" required></div>
          <div class="full" style="grid-column:1 / -1"><label>Description</label>
            <input type="text" name="description" required placeholder="Short description"></div>
          <div><button type="submit">Add course</button></div>
        </form></div>"""
        self.send_html(layout("Admin Dashboard", body, session, msg, kind))

    def action_set_seats(self, form):
        session = self.require_role("admin")
        if not session:
            return
        course_id = form.get("course_id", "")
        conn = get_db()
        course = conn.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        try:
            limit = int(form.get("seat_limit", ""))
        except ValueError:
            limit = -1
        if not course or limit < 0:
            conn.close()
            self.redirect("/admin?kind=err&msg=Invalid seat limit")
            return
        taken = seats_taken(conn, course_id)
        if limit < taken:
            conn.close()
            self.redirect("/admin?kind=err&msg=Seat limit cannot be below current enrolment ("
                          + str(taken) + ")")
            return
        with db_lock:
            conn.execute("UPDATE courses SET seat_limit=? WHERE id=?", (limit, course_id))
            conn.commit()
        conn.close()
        self.redirect("/admin?msg=Seat limit updated for " + course["code"])

    def action_decide(self, form):
        session = self.require_role("admin")
        if not session:
            return
        reg_id, decision = form.get("reg_id", ""), form.get("decision", "")
        if decision not in ("approve", "reject"):
            self.redirect("/admin?kind=err&msg=Unknown decision")
            return
        conn = get_db()
        reg = conn.execute(
            "SELECT r.*, c.code, c.seat_limit FROM registrations r"
            " JOIN courses c ON c.id=r.course_id WHERE r.id=? AND r.status='pending'",
            (reg_id,)).fetchone()
        if not reg:
            conn.close()
            self.redirect("/admin?kind=err&msg=Registration not found or already decided")
            return
        if decision == "approve" and seats_taken(conn, reg["course_id"]) >= reg["seat_limit"]:
            conn.close()
            self.redirect("/admin?kind=err&msg=Cannot approve - " + reg["code"] + " is full")
            return
        new_status = "approved" if decision == "approve" else "rejected"
        with db_lock:
            conn.execute("UPDATE registrations SET status=? WHERE id=?", (new_status, reg_id))
            conn.commit()
        conn.close()
        self.redirect("/admin?msg=" + reg["code"] + " registration " + new_status)

    def action_promote(self, form):
        """Move a waitlisted student to pending (only when a seat is free)."""
        session = self.require_role("admin")
        if not session:
            return
        conn = get_db()
        reg = conn.execute(
            "SELECT r.*, c.code, c.seat_limit FROM registrations r"
            " JOIN courses c ON c.id=r.course_id WHERE r.id=? AND r.status='waitlisted'",
            (form.get("reg_id", ""),)).fetchone()
        if not reg:
            conn.close()
            self.redirect("/admin?kind=err&msg=Waitlist entry not found")
            return
        if seats_taken(conn, reg["course_id"]) >= reg["seat_limit"]:
            conn.close()
            self.redirect("/admin?kind=err&msg=No free seat in " + reg["code"] + " yet")
            return
        with db_lock:
            conn.execute("UPDATE registrations SET status='pending' WHERE id=?", (reg["id"],))
            conn.commit()
        conn.close()
        self.redirect("/admin?msg=" + reg["code"] + ": moved from waitlist to pending approval")

    # ---------- admin: course management ----------
    def action_add_course(self, form):
        session = self.require_role("admin")
        if not session:
            return
        code = form.get("code", "").strip().upper()
        title = form.get("title", "").strip()
        instructor = form.get("instructor", "").strip()
        schedule = form.get("schedule", "").strip()
        semester = form.get("semester", "").strip() or "Semester 1"
        description = form.get("description", "").strip()
        try:
            seat_limit = int(form.get("seat_limit", "0"))
        except ValueError:
            seat_limit = 0
        if not all([code, title, instructor, schedule, description]) or seat_limit < 1:
            self.redirect("/admin?kind=err&msg=Please fill every field correctly")
            return
        conn = get_db()
        try:
            with db_lock:
                conn.execute(
                    "INSERT INTO courses (code,title,instructor,schedule,semester,description,seat_limit)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (code, title, instructor, schedule, semester, description, seat_limit))
                conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            self.redirect("/admin?kind=err&msg=A course with code " + code + " already exists")
            return
        conn.close()
        self.redirect("/admin?msg=Course " + code + " added")

    def page_edit_course(self, msg=None, kind="ok", query=None):
        session = self.require_role("admin")
        if not session:
            return
        conn = get_db()
        c = conn.execute("SELECT * FROM courses WHERE id=?",
                         ((query or {}).get("id", [""])[0],)).fetchone()
        conn.close()
        if not c:
            self.redirect("/admin?kind=err&msg=Course not found")
            return
        sem_opts = "".join(f'<option {"selected" if s == c["semester"] else ""}>{s}</option>'
                           for s in SEMESTERS)
        body = f"""<h1>Edit course</h1><p class="sub">{esc(c['code'])} - {esc(c['title'])}</p>
        <div class="panel"><form class="editcourse" method="post" action="/admin/edit-course">
          <input type="hidden" name="course_id" value="{c['id']}">
          <div><label>Code</label><input type="text" name="code" value="{esc(c['code'])}" required></div>
          <div><label>Title</label><input type="text" name="title" value="{esc(c['title'])}" required></div>
          <div><label>Instructor</label><input type="text" name="instructor" value="{esc(c['instructor'])}" required></div>
          <div><label>Schedule</label><input type="text" name="schedule" value="{esc(c['schedule'])}" required></div>
          <div><label>Semester</label><select name="semester">{sem_opts}</select></div>
          <div><label>Seat limit</label><input type="number" name="seat_limit" min="0" value="{c['seat_limit']}" required></div>
          <div class="full"><label>Description</label>
            <input type="text" name="description" value="{esc(c['description'])}" required></div>
          <div class="full"><button type="submit">Save changes</button>
            <a class="btn grey" href="/admin">Cancel</a></div>
        </form></div>"""
        self.send_html(layout("Edit course", body, session, msg, kind))

    def action_edit_course(self, form):
        session = self.require_role("admin")
        if not session:
            return
        course_id = form.get("course_id", "")
        code = form.get("code", "").strip().upper()
        title = form.get("title", "").strip()
        instructor = form.get("instructor", "").strip()
        schedule = form.get("schedule", "").strip()
        semester = form.get("semester", "").strip() or "Semester 1"
        description = form.get("description", "").strip()
        try:
            seat_limit = int(form.get("seat_limit", "0"))
        except ValueError:
            seat_limit = -1
        conn = get_db()
        course = conn.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        taken = seats_taken(conn, course_id) if course else 0
        if not course or not all([code, title, instructor, schedule, description]) \
                or seat_limit < taken:
            conn.close()
            self.redirect("/admin?kind=err&msg=Invalid values (seat limit cannot be below "
                          "current enrolment)")
            return
        try:
            with db_lock:
                conn.execute(
                    "UPDATE courses SET code=?, title=?, instructor=?, schedule=?, semester=?,"
                    " description=?, seat_limit=? WHERE id=?",
                    (code, title, instructor, schedule, semester, description,
                     seat_limit, course_id))
                conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            self.redirect("/admin?kind=err&msg=Another course already uses code " + code)
            return
        conn.close()
        self.redirect("/admin?msg=Course " + code + " updated")

    def action_delete_course(self, form):
        session = self.require_role("admin")
        if not session:
            return
        course_id = form.get("course_id", "")
        conn = get_db()
        course = conn.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        n_reg = conn.execute("SELECT COUNT(*) FROM registrations WHERE course_id=?",
                             (course_id,)).fetchone()[0]
        if not course:
            conn.close()
            self.redirect("/admin?kind=err&msg=Course not found")
            return
        if n_reg:
            conn.close()
            self.redirect("/admin?kind=err&msg=Cannot delete " + course["code"]
                          + " - it has registrations")
            return
        with db_lock:
            conn.execute("DELETE FROM courses WHERE id=?", (course_id,))
            conn.commit()
        conn.close()
        self.redirect("/admin?msg=Course " + course["code"] + " deleted")

    # ---------- admin: roster + export ----------
    def page_roster(self, msg=None, kind="ok", query=None):
        session = self.require_role("admin")
        if not session:
            return
        conn = get_db()
        c = conn.execute("SELECT * FROM courses WHERE id=?",
                         ((query or {}).get("id", [""])[0],)).fetchone()
        if not c:
            conn.close()
            self.redirect("/admin?kind=err&msg=Course not found")
            return
        rows = conn.execute(
            """SELECT s.full_name, s.username, r.status, r.created_at
               FROM registrations r JOIN students s ON s.id=r.student_id
               WHERE r.course_id=? ORDER BY r.status, r.created_at""",
            (c["id"],)).fetchall()
        conn.close()
        if rows:
            lines = "".join(f"""<tr><td>{esc(r['full_name'])}</td><td>{esc(r['username'])}</td>
              <td><span class="badge {r['status']}">{r['status'].upper()}</span></td>
              <td>{esc(r['created_at'])}</td></tr>""" for r in rows)
            table = ("<table><tr><th>Student</th><th>Username</th><th>Status</th>"
                     "<th>Since</th></tr>" + lines + "</table>")
        else:
            table = "<p>No registrations for this course yet.</p>"
        body = (f"<h1>Roster: {esc(c['code'])}</h1>"
                f"<p class='sub'>{esc(c['title'])} &bull; {esc(c['semester'])}</p>{table}"
                f"<p style='margin-top:16px'><a class='btn grey' href='/admin'>Back</a></p>")
        self.send_html(layout("Roster", body, session, msg, kind))

    def page_export(self, msg=None, kind="ok", query=None):
        session = self.require_role("admin")
        if not session:
            return
        conn = get_db()
        rows = conn.execute(
            """SELECT s.full_name, s.username, c.code, c.title, c.semester, r.status, r.created_at
               FROM registrations r JOIN students s ON s.id=r.student_id
               JOIN courses c ON c.id=r.course_id ORDER BY c.code, r.created_at""").fetchall()
        conn.close()
        out = ["student,username,course_code,course_title,semester,status,requested_at"]
        for r in rows:
            out.append(",".join('"' + str(v).replace('"', '""') + '"' for v in (
                r["full_name"], r["username"], r["code"], r["title"],
                r["semester"], r["status"], r["created_at"])))
        self.send_csv("registrations.csv", "\n".join(out) + "\n")


def main():
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), App)
    print("=" * 56)
    print("  Online Course Registration System")
    print(f"  Running at:  http://localhost:{PORT}")
    print("  Student login:  student / student123")
    print("  Admin login:    admin   / admin123")
    print("  Press Ctrl+C to stop")
    print("=" * 56)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
