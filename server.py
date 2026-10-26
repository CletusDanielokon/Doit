"""Do It - a classic to-do app built with Flask."""
import calendar
import csv
import hashlib
import html as htmllib
import io
import json
import os
import re
import secrets
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from functools import wraps

from flask import (Flask, Response, abort, jsonify, redirect, render_template,
                   request, session, url_for)
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import ai
import mailer
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

basedir = os.path.abspath(os.path.dirname(__file__))
try:  # optional: read settings (DATABASE_URL, SECRET_KEY...) from a local .env file
    from dotenv import load_dotenv
    load_dotenv(os.path.join(basedir, ".env"))
except ImportError:
    pass


def _load_secret_key():
    """Use SECRET_KEY from the environment, else create one and keep it on disk."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    path = os.path.join(basedir, ".secret_key")
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        key = secrets.token_hex(32)
        try:
            with open(path, "w") as fh:
                fh.write(key)
        except OSError:
            pass  # read-only filesystem: sessions simply reset on restart
        return key


db_url = os.environ.get("DATABASE_URL") or "sqlite:///" + os.path.join(basedir, "todo.db")
if db_url.startswith("postgres://"):  # older hosting providers still emit this scheme
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app = Flask(__name__)
app.config.update(
    SQLALCHEMY_DATABASE_URI=db_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    # Managed PostgreSQL hosts close idle connections: test them before use and recycle often.
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True, "pool_recycle": 280},
    SECRET_KEY=_load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("COOKIE_SECURE")),
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    MAX_CONTENT_LENGTH=256 * 1024,
    SEND_FILE_MAX_AGE_DEFAULT=timedelta(days=30),  # assets are cache-busted with ?v=mtime
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # correct https URLs behind a proxy

db = SQLAlchemy(app)
migrate = Migrate(app, db)

ALARM_SOUNDS = {"classic", "digital", "phone", "rooster"}


def utcnow():
    """Naive UTC timestamp (used for created/completed times)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class TaskPriority(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RecurrenceEnum(Enum):
    none = "none"
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=True)
    email = db.Column(db.String(254), unique=True, nullable=True, index=True)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    email_reminders = db.Column(db.Boolean, default=True, nullable=False)
    email_digest = db.Column(db.Boolean, default=True, nullable=False)
    tz = db.Column(db.String(64))
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    tasks = db.relationship("Task", backref="user", lazy=True, cascade="all, delete-orphan")
    timetable = db.relationship("TimetableEntry", backref="user", lazy=True, cascade="all, delete-orphan")
    notes = db.relationship("Note", backref="user", lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.username}>"

    @property
    def display_name(self):
        return (self.name or "").strip().split(" ")[0] or self.username.strip()


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task = db.Column(db.String(255), nullable=False)
    notes = db.Column(db.Text)
    priority = db.Column(db.Enum(TaskPriority, name="taskpriority"),
                         default=TaskPriority.MEDIUM, nullable=False)
    tags = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_updated = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    # Due date and reminder are "floating" local times: stored exactly as the
    # person typed them, so they ring at the right moment in *their* timezone.
    due_date = db.Column(db.DateTime)
    due_has_time = db.Column(db.Boolean, default=False, nullable=False)
    reminder_datetime = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)  # UTC
    dismissed = db.Column(db.Boolean, default=False)
    acknowledged = db.Column(db.Boolean, default=False)
    alarm_sound = db.Column(db.String(50), default="classic")
    recurring = db.Column(db.Enum(RecurrenceEnum, name="recurrenceenum"),
                          default=RecurrenceEnum.none)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    subtasks = db.relationship("Subtask", backref="task", lazy=True,
                               cascade="all, delete-orphan", order_by="Subtask.position")

    def __repr__(self):
        return f"<Task {self.task}>"

    @property
    def is_completed(self):
        return self.completed_at is not None


class Subtask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("task.id"), nullable=False, index=True)
    title = db.Column(db.String(255), nullable=False)
    done = db.Column(db.Boolean, default=False, nullable=False)
    position = db.Column(db.Integer, default=0, nullable=False)


class TimetableEntry(db.Model):
    """One activity on one weekday (day 0 = Monday ... 6 = Sunday). Times are minutes after midnight."""
    __tablename__ = "timetable_entry"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title = db.Column(db.String(120), nullable=False)
    day = db.Column(db.Integer, nullable=False)
    start_min = db.Column(db.Integer, nullable=False)
    end_min = db.Column(db.Integer, nullable=False)
    location = db.Column(db.String(120))
    color = db.Column(db.String(12), default="blue")
    alerts = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)


class Note(db.Model):
    """A diary entry."""
    __tablename__ = "note"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    entry_date = db.Column(db.Date, nullable=False, default=date.today)
    title = db.Column(db.String(150))
    body = db.Column(db.Text)
    mood = db.Column(db.String(12))
    pinned = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class PendingSignup(db.Model):
    """A sign-up waiting for its email to be confirmed. The real account is only created after the link is used."""
    __tablename__ = "pending_signup"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    name = db.Column(db.String(80))
    username = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    token_hash = db.Column(db.String(64), index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    sent_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class EmailLog(db.Model):
    """Remembers which reminder emails were already sent, so nobody is emailed twice."""
    __tablename__ = "email_log"
    __table_args__ = (db.UniqueConstraint("user_id", "key", name="uq_email_log_user_key"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    key = db.Column(db.String(160), nullable=False)
    sent_at = db.Column(db.DateTime, default=utcnow)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def get_user():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None


def api_login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = get_user()
        if not user:
            return jsonify(error="Please sign in again."), 401
        return fn(user, *args, **kwargs)
    return wrapper


def find_user(username):
    """Exact match first, then a forgiving (trimmed, case-insensitive) one."""
    user = User.query.filter_by(username=username).first()
    if user:
        return user
    return User.query.filter(
        db.func.lower(db.func.trim(User.username)) == username.strip().lower()).first()


def own_task_or_404(user, task_id):
    task = Task.query.filter_by(id=task_id, user_id=user.id).first()
    if not task:
        abort(404)
    return task


def iso(dt):
    return dt.isoformat(timespec="minutes") if dt else None


def utc_iso(dt):
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def split_tags(raw):
    return [t.strip() for t in (raw or "").split(",") if t.strip()]


def task_json(t):
    return {
        "id": t.id,
        "title": t.task,
        "notes": t.notes or "",
        "priority": (t.priority or TaskPriority.MEDIUM).value,
        "tags": split_tags(t.tags),
        "due": iso(t.due_date),
        "due_has_time": bool(t.due_has_time),
        "reminder": iso(t.reminder_datetime),
        "recurring": (t.recurring or RecurrenceEnum.none).value,
        "alarm_sound": t.alarm_sound or "classic",
        "dismissed": bool(t.dismissed),
        "created_at": utc_iso(t.created_at),
        "completed_at": utc_iso(t.completed_at),
        "subtasks": [{"id": s.id, "title": s.title, "done": s.done} for s in t.subtasks],
    }


def add_months(dt, n):
    year_off, month0 = divmod(dt.month - 1 + n, 12)
    year, month = dt.year + year_off, month0 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def step(dt, recurrence):
    if recurrence == RecurrenceEnum.daily:
        return dt + timedelta(days=1)
    if recurrence == RecurrenceEnum.weekly:
        return dt + timedelta(weeks=1)
    return add_months(dt, 1)


def parse_dt(value, fmt):
    try:
        return datetime.strptime(value, fmt) if value else None
    except (ValueError, TypeError):
        return None


class ValidationError(Exception):
    pass

TT_COLORS = {"blue", "navy", "sky", "teal", "green", "amber", "red", "purple"}
MOODS = {"happy", "good", "okay", "sad", "angry"}


def tt_json(e):
    return {"id": e.id, "title": e.title, "day": e.day, "start": e.start_min, "end": e.end_min,
            "location": e.location or "", "color": e.color or "blue", "alerts": bool(e.alerts)}


def note_json(n):
    return {"id": n.id, "date": n.entry_date.isoformat(), "title": n.title or "", "body": n.body or "",
            "mood": n.mood or "", "pinned": bool(n.pinned), "updated_at": utc_iso(n.updated_at)}


def minutes_of(value):
    """'HH:MM' -> minutes after midnight."""
    try:
        hh, mm = str(value).split(":")[:2]
        total = int(hh) * 60 + int(mm)
    except (ValueError, TypeError):
        raise ValidationError("Please enter a valid time.")
    if not 0 <= total <= 1439:
        raise ValidationError("Please enter a valid time.")
    return total


def apply_timetable(entry, data):
    title = str(data.get("title", "")).strip()
    if not title:
        raise ValidationError("Please give the activity a name.")
    entry.title = title[:120]
    entry.start_min, entry.end_min = minutes_of(data.get("start")), minutes_of(data.get("end"))
    if entry.end_min <= entry.start_min:
        raise ValidationError("The end time must be after the start time.")
    entry.location = str(data.get("location", "") or "").strip()[:120] or None
    entry.color = data.get("color") if data.get("color") in TT_COLORS else "blue"
    entry.alerts = bool(data.get("alerts", True))


def apply_note(note, data):
    note.title = str(data.get("title", "") or "").strip()[:150] or None
    note.body = str(data.get("body", "") or "")[:20000]
    note.mood = data.get("mood") if data.get("mood") in MOODS else None
    note.pinned = bool(data.get("pinned"))
    parsed = parse_dt(data.get("date"), "%Y-%m-%d")
    if parsed:
        note.entry_date = parsed.date()
    elif not note.entry_date:
        note.entry_date = date.today()



def apply_payload(task, data):
    """Validate JSON from the editor / quick-add and copy it onto the task."""
    title = str(data.get("title", "")).strip()
    if not title:
        raise ValidationError("Please give the task a title.")
    task.task = title[:255]
    task.notes = str(data.get("notes", "") or "").strip()[:2000] or None

    try:
        task.priority = TaskPriority(data.get("priority") or "medium")
    except ValueError:
        task.priority = TaskPriority.MEDIUM

    tags = data.get("tags") or []
    if isinstance(tags, str):
        tags = tags.split(",")
    clean, seen = [], set()
    for tag in tags:
        tag = re.sub(r"\s+", " ", str(tag).strip().lstrip("#"))[:24]
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            clean.append(tag)
    task.tags = ", ".join(clean[:8]) or None

    due_date, due_time = data.get("due"), data.get("due_time")
    due = parse_dt(f"{due_date} {due_time}", "%Y-%m-%d %H:%M") if due_date and due_time \
        else parse_dt(due_date, "%Y-%m-%d")
    task.due_date = due
    task.due_has_time = bool(due and due_time)

    reminder = parse_dt(data.get("reminder"), "%Y-%m-%dT%H:%M")
    if reminder != task.reminder_datetime:
        task.dismissed = reminder is None
        task.acknowledged = False
    task.reminder_datetime = reminder
    if reminder is None:
        task.dismissed = True

    try:
        task.recurring = RecurrenceEnum(data.get("recurring") or "none")
    except ValueError:
        task.recurring = RecurrenceEnum.none
    if not due and not reminder:  # nothing to repeat from
        task.recurring = RecurrenceEnum.none

    sound = data.get("alarm_sound")
    task.alarm_sound = sound if sound in ALARM_SOUNDS else "classic"


def sync_subtasks(task, items):
    existing = {s.id: s for s in task.subtasks}
    kept = []
    for pos, item in enumerate((items or [])[:30]):
        title = str(item.get("title", "")).strip()[:255]
        if not title:
            continue
        sub = existing.pop(item.get("id"), None) or Subtask(title=title)
        sub.title, sub.done, sub.position = title, bool(item.get("done")), pos
        kept.append(sub)
    for orphan in existing.values():
        db.session.delete(orphan)
    task.subtasks[:] = kept


def spawn_next_occurrence(task, local_now):
    """When a repeating task is completed, create the next one."""
    rec = task.recurring
    if not rec or rec == RecurrenceEnum.none:
        return None
    due, reminder = task.due_date, task.reminder_datetime
    for _ in range(400):  # skip cycles that are already in the past
        due = step(due, rec) if due else None
        reminder = step(reminder, rec) if reminder else None
        if (due or reminder) > local_now:
            break
    nxt = Task(task=task.task, notes=task.notes, priority=task.priority, tags=task.tags,
               due_date=due, due_has_time=task.due_has_time, reminder_datetime=reminder,
               dismissed=reminder is None, acknowledged=False, recurring=rec,
               alarm_sound=task.alarm_sound, user_id=task.user_id)
    nxt.subtasks = [Subtask(title=s.title, position=s.position) for s in task.subtasks]
    db.session.add(nxt)
    return nxt


@app.template_global()
def asset(filename):
    """static URL with a version stamp so browsers refresh files when they change."""
    path = os.path.join(app.static_folder, filename)
    version = int(os.path.getmtime(path)) if os.path.exists(path) else 0
    return url_for("static", filename=filename, v=version)


def site_url():
    return (os.environ.get("SITE_URL") or request.url_root).rstrip("/")


@app.context_processor
def inject_site():
    return {"site_url": site_url()}


# --------------------------------------------------------------------------
# CSRF protection (token lives in the session; forms and fetch both send it)
# --------------------------------------------------------------------------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def check_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            if request.path.startswith("/api/"):
                return jsonify(error="Your session expired. Please refresh the page."), 400
            abort(400)


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


# --------------------------------------------------------------------------
# Account, e-mail and security helpers
# --------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
_hits = defaultdict(list)


def rate_ok(key, limit, seconds):
    """Very small in-memory rate limiter (per server process)."""
    now = time.time()
    _hits[key] = [t for t in _hits[key] if now - t < seconds]
    if len(_hits[key]) >= limit:
        return False
    _hits[key].append(now)
    return True


def clean_email(value):
    value = str(value or "").strip().lower()
    return value if len(value) <= 254 and EMAIL_RE.match(value) else ""


def find_by_email(email):
    return User.query.filter(db.func.lower(User.email) == email.lower()).first() if email else None


def serializer(salt):
    return URLSafeTimedSerializer(app.config["SECRET_KEY"], salt=salt)


def pw_stamp(user):
    return hashlib.sha256(user.password.encode()).hexdigest()[:12]


def user_zone(user):
    try:
        return ZoneInfo(user.tz or os.environ.get("DEFAULT_TZ", "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def email_html(heading, intro, lines=(), button=None, footer=""):
    """A simple blue-and-white e-mail body. Everything user-supplied is escaped."""
    esc = htmllib.escape
    items = "".join(f'<li style="margin:6px 0">{esc(l)}</li>' for l in lines)
    btn = (f'<p style="margin:22px 0"><a href="{esc(button[1])}" style="background:#0741dc;color:#fff;'
           f'padding:12px 22px;border-radius:8px;text-decoration:none;font-weight:bold">{esc(button[0])}</a></p>') if button else ""
    return (f'<div style="background:#f3f6fc;padding:24px;font-family:Georgia,serif;color:#0f1b3d"><div style="max-width:520px;margin:auto;'
            f'background:#fff;border:1px solid #d8e0f2;border-radius:10px;overflow:hidden"><div style="background:#0a1f5c;color:#fff;padding:16px 22px;'
            f'font-size:20px;font-weight:bold">Do.iT</div><div style="padding:22px"><h2 style="margin:0 0 10px;color:#0a1f5c">{esc(heading)}</h2>'
            f'<p style="margin:0 0 10px">{esc(intro)}</p>{"<ul style=padding-left:18px>" + items + "</ul>" if items else ""}{btn}'
            f'<p style="color:#5f6b8a;font-size:12px;margin-top:22px">{esc(footer)}</p></div></div></div>')


def try_send(to, subject, heading, intro, lines=(), button=None, footer=""):
    """Send an e-mail; returns an error message or '' when it worked."""
    text = "\n".join([heading, intro, *lines, button[1] if button else ""])
    try:
        mailer.send_email(to, subject, email_html(heading, intro, lines, button, footer), text)
        return ""
    except mailer.MailError as err:
        app.logger.warning("E-mail failed: %s", err)
        return str(err)


def send_verification(user):
    token = serializer("verify-email").dumps({"u": user.id, "e": user.email})
    link = f"{site_url()}{url_for('verify_email', token=token)}"
    return try_send(user.email, "Confirm your email for Do.iT", "Confirm your email",
                    f"Hi {user.display_name}, please confirm this address so Do.iT can email you task reminders and help you recover your account.",
                    button=("Confirm my email", link), footer="This link works for 7 days.")


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------
def safe_next(target):
    return target if target and target.startswith("/") and not target.startswith("//") else None


@app.route("/")
def landing():
    return render_template("landing.html", signed_in=bool(get_user()))


@app.route("/app")
def homepage():
    user = get_user()
    if not user:
        return redirect(url_for("login", next="/app"))
    tasks = (Task.query.filter_by(user_id=user.id)
             .order_by(Task.created_at.desc()).all())
    boot = {"user": {"name": user.display_name, "username": user.username.strip(),
                     "full_name": (user.name or "").strip()},
            "account": {"email": user.email or "", "verified": bool(user.email_verified),
                        "email_reminders": bool(user.email_reminders), "email_digest": bool(user.email_digest),
                        "tz": user.tz or "", "mail_enabled": mailer.mail_enabled(), "ai_enabled": ai.ai_enabled()},
            "tasks": [task_json(t) for t in tasks],
            "timetable": [tt_json(e) for e in TimetableEntry.query.filter_by(user_id=user.id)
                          .order_by(TimetableEntry.day, TimetableEntry.start_min).all()],
            "notes": [note_json(n) for n in Note.query.filter_by(user_id=user.id)
                      .order_by(Note.entry_date.desc(), Note.updated_at.desc()).limit(500).all()]}
    return render_template("home.html", boot=boot)


@app.route("/add")
def add_page():  # old bookmark: the editor is now a dialog on the home page
    return redirect(url_for("homepage", new=1))


@app.route("/login", methods=["GET", "POST"])
def login():
    if get_user():
        return redirect(url_for("homepage"))
    error = ""
    if request.method == "POST":
        identifier = (request.form.get("identifier") or request.form.get("username") or "").strip()
        password = request.form.get("password", "")
        if not rate_ok(f"login:{request.remote_addr}:{identifier.lower()}", 10, 900):
            error = "Too many attempts. Please wait a few minutes and try again."
        else:
            user = None
            if identifier:  # an email address or a username both work
                user = find_by_email(clean_email(identifier)) if "@" in identifier else find_user(identifier)
            if user and check_password_hash(user.password, password):
                session.clear()
                session["user_id"] = user.id
                session["username"] = user.username
                session.permanent = bool(request.form.get("remember"))
                return redirect(safe_next(request.args.get("next")) or url_for("homepage"))
            error = "That username/email and password don't match."
    return render_template("index.html", error=error, notice=request.args.get("notice", ""))


SIGNUP_LINK_SECONDS = 3600  # the confirmation link works for 1 hour


def send_signup_email(pending):
    """Make a fresh one-hour link for this pending sign-up and email it. Returns '' or an error."""
    token = secrets.token_urlsafe(32)
    pending.token_hash = hashlib.sha256(token.encode()).hexdigest()
    pending.sent_at = utcnow()
    db.session.commit()
    link = f"{site_url()}{url_for('confirm_signup', token=token)}"
    first = (pending.name or pending.username).split(" ")[0]
    return try_send(pending.email, "Confirm your email to finish creating your Do.iT account", "Confirm your email",
                    f"Hi {first}, one last step: confirm your email address to create your Do.iT account.",
                    button=("Confirm and create my account", link),
                    footer="This link works for 1 hour. If you did not sign up, you can ignore this email and nothing will be created.")


@app.route("/create-account", methods=["GET", "POST"])
def create_account():
    if get_user():
        return redirect(url_for("homepage"))
    error, form = "", {}
    if request.method == "POST":
        form = {"fullname": request.form.get("fullname", "").strip()[:80],
                "username": request.form.get("username", "").strip(),
                "email": request.form.get("email", "").strip()}
        password = request.form.get("password", "")
        email = clean_email(form["email"])
        if not email:
            error = "Please enter a valid email address. We use it to confirm your account and recover it."
        elif find_by_email(email):
            error = "An account with that email already exists. Try signing in or resetting your password."
        elif not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", form["username"]):
            error = "Usernames are 3-40 characters: letters, numbers, dots, dashes or underscores."
        elif len(password) < 8:
            error = "Please choose a password of at least 8 characters."
        elif find_user(form["username"]):
            error = "That username is already taken. Please choose another."
        elif mailer.mail_enabled():
            # the account is created only after the emailed link is confirmed
            if not rate_ok(f"signup:{request.remote_addr}", 10, 3600) or not rate_ok(f"signup-mail:{email}", 3, 3600):
                error = "Too many sign-up attempts. Please wait a while and try again."
            else:
                PendingSignup.query.filter(PendingSignup.created_at < utcnow() - timedelta(hours=24)).delete()
                pending = PendingSignup.query.filter_by(email=email).first() or PendingSignup(email=email)
                pending.name, pending.username = form["fullname"] or None, form["username"]
                pending.password_hash, pending.created_at = generate_password_hash(password), utcnow()
                db.session.add(pending)
                db.session.commit()
                if send_signup_email(pending):
                    error = "We could not send the confirmation email right now. Please check the address and try again in a few minutes."
                else:
                    return render_template("check_email.html", email=email)
        else:  # email is not set up on this server (for example during local testing): create the account straight away
            user = User(name=form["fullname"] or None, username=form["username"], email=email,
                        password=generate_password_hash(password))
            db.session.add(user)
            db.session.commit()
            session.clear()
            session["user_id"] = user.id
            session["username"] = user.username
            return redirect(url_for("homepage"))
    return render_template("create_account.html", error=error, form=form, confirm_by_email=mailer.mail_enabled())


@app.route("/signup/resend", methods=["POST"])
def resend_signup():
    email = clean_email(request.form.get("email"))
    pending = PendingSignup.query.filter_by(email=email).first() if email else None
    if not rate_ok(f"signup-mail:{email}", 3, 3600):
        return render_template("check_email.html", email=email, error="Too many emails requested. Please wait a while before asking again."), 429
    if not pending or pending.created_at < utcnow() - timedelta(hours=24):
        return render_template("message.html", title="Please sign up again", text="We no longer have your sign-up details. Please fill in the sign-up form again.",
                               button=("Sign up", url_for("create_account"))), 400
    err = send_signup_email(pending)
    return render_template("check_email.html", email=email, resent=not err,
                           error="We could not send the email right now. Please try again in a few minutes." if err else "")


@app.route("/confirm/<token>", methods=["GET", "POST"])
def confirm_signup(token):
    pending = PendingSignup.query.filter_by(token_hash=hashlib.sha256(token.encode()).hexdigest()).first()
    if not pending:
        return render_template("message.html", title="Link not valid", text="This confirmation link is not valid or was already used. If you already confirmed, just sign in. Otherwise, sign up again.",
                               button=("Sign in", url_for("login"))), 400
    if utcnow() - pending.sent_at > timedelta(seconds=SIGNUP_LINK_SECONDS):
        return render_template("check_email.html", email=pending.email, expired=True), 400
    if request.method == "GET":  # a button (not the link itself) creates the account, so email scanners cannot use it up
        return render_template("confirm_signup.html", token=token, name=(pending.name or pending.username).split(" ")[0])
    if find_user(pending.username) or find_by_email(pending.email):
        db.session.delete(pending)
        db.session.commit()
        return render_template("message.html", title="Already registered", text="That username or email is already in use. Please sign in, or sign up again with different details.",
                               button=("Sign in", url_for("login"))), 400
    user = User(name=pending.name, username=pending.username, email=pending.email, password=pending.password_hash, email_verified=True)
    db.session.add(user)
    db.session.delete(pending)
    db.session.commit()
    session.clear()
    session["user_id"] = user.id
    session["username"] = user.username
    return redirect(url_for("homepage"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.errorhandler(404)
def not_found(err):
    if request.path.startswith("/api/"):
        return jsonify(error="Not found."), 404
    return render_template("404.html"), 404


# --------------------------------------------------------------------------
# JSON API
# --------------------------------------------------------------------------
@app.errorhandler(ValidationError)
def handle_validation(err):
    return jsonify(error=str(err)), 400


@app.route("/api/tasks")
@api_login_required
def api_list(user):
    tasks = Task.query.filter_by(user_id=user.id).order_by(Task.created_at.desc()).all()
    return jsonify(tasks=[task_json(t) for t in tasks])


@app.route("/api/tasks", methods=["POST"])
@api_login_required
def api_create(user):
    data = request.get_json(silent=True) or {}
    task = Task(user_id=user.id)
    apply_payload(task, data)
    sync_subtasks(task, data.get("subtasks"))
    db.session.add(task)
    db.session.commit()
    return jsonify(task=task_json(task)), 201


@app.route("/api/tasks/<int:task_id>", methods=["PATCH"])
@api_login_required
def api_update(user, task_id):
    task = own_task_or_404(user, task_id)
    data = request.get_json(silent=True) or {}
    apply_payload(task, data)
    if "subtasks" in data:
        sync_subtasks(task, data["subtasks"])
    db.session.commit()
    return jsonify(task=task_json(task))


@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
@api_login_required
def api_delete(user, task_id):
    db.session.delete(own_task_or_404(user, task_id))
    db.session.commit()
    return jsonify(ok=True)


@app.route("/api/tasks/<int:task_id>/complete", methods=["POST"])
@api_login_required
def api_complete(user, task_id):
    task = own_task_or_404(user, task_id)
    data = request.get_json(silent=True) or {}
    spawned = None
    if not task.is_completed:
        task.completed_at = utcnow()
        task.dismissed = True
        local_now = parse_dt(data.get("now"), "%Y-%m-%dT%H:%M") or utcnow()
        spawned = spawn_next_occurrence(task, local_now)
    db.session.commit()
    return jsonify(task=task_json(task), spawned=task_json(spawned) if spawned else None)


@app.route("/api/tasks/<int:task_id>/reopen", methods=["POST"])
@api_login_required
def api_reopen(user, task_id):
    task = own_task_or_404(user, task_id)
    task.completed_at = None
    db.session.commit()
    return jsonify(task=task_json(task))


@app.route("/api/tasks/<int:task_id>/stop", methods=["POST"])
@api_login_required
def api_stop(user, task_id):
    task = own_task_or_404(user, task_id)
    task.dismissed = task.acknowledged = True
    db.session.commit()
    return jsonify(task=task_json(task))


@app.route("/api/tasks/<int:task_id>/snooze", methods=["POST"])
@api_login_required
def api_snooze(user, task_id):
    task = own_task_or_404(user, task_id)
    data = request.get_json(silent=True) or {}
    minutes = data.get("minutes") if data.get("minutes") in (5, 10, 15, 30, 60) else 5
    local_now = parse_dt(data.get("now"), "%Y-%m-%dT%H:%M") or utcnow()
    task.reminder_datetime = local_now + timedelta(minutes=minutes)
    task.dismissed = task.acknowledged = False
    db.session.commit()
    return jsonify(task=task_json(task))


@app.route("/api/subtasks/<int:sub_id>/toggle", methods=["POST"])
@api_login_required
def api_toggle_subtask(user, sub_id):
    sub = db.session.get(Subtask, sub_id)
    if not sub or sub.task.user_id != user.id:
        abort(404)
    sub.done = not sub.done
    db.session.commit()
    return jsonify(task=task_json(sub.task))


@app.route("/api/tasks/clear-completed", methods=["POST"])
@api_login_required
def api_clear_completed(user):
    for task in Task.query.filter(Task.user_id == user.id, Task.completed_at.isnot(None)).all():
        db.session.delete(task)
    db.session.commit()
    return jsonify(ok=True)


# --------------------------------------------------------------------------
# Timetable API
# --------------------------------------------------------------------------
@app.route("/api/timetable", methods=["POST"])
@api_login_required
def api_tt_create(user):
    data = request.get_json(silent=True) or {}
    days = sorted({d for d in (data.get("days") or []) if isinstance(d, int) and 0 <= d <= 6})
    if not days:
        raise ValidationError("Choose at least one day.")
    if TimetableEntry.query.filter_by(user_id=user.id).count() + len(days) > 300:
        raise ValidationError("Your timetable is full (300 activities).")
    created = []
    for day in days:
        entry = TimetableEntry(user_id=user.id, day=day)
        apply_timetable(entry, data)
        db.session.add(entry)
        created.append(entry)
    db.session.commit()
    return jsonify(entries=[tt_json(e) for e in created]), 201


@app.route("/api/timetable/<int:entry_id>", methods=["PATCH"])
@api_login_required
def api_tt_update(user, entry_id):
    entry = TimetableEntry.query.filter_by(id=entry_id, user_id=user.id).first_or_404()
    data = request.get_json(silent=True) or {}
    if isinstance(data.get("day"), int) and 0 <= data["day"] <= 6:
        entry.day = data["day"]
    apply_timetable(entry, data)
    db.session.commit()
    return jsonify(entry=tt_json(entry))


@app.route("/api/timetable/<int:entry_id>", methods=["DELETE"])
@api_login_required
def api_tt_delete(user, entry_id):
    db.session.delete(TimetableEntry.query.filter_by(id=entry_id, user_id=user.id).first_or_404())
    db.session.commit()
    return jsonify(ok=True)


@app.route("/api/timetable/clear", methods=["POST"])
@api_login_required
def api_tt_clear(user):
    TimetableEntry.query.filter_by(user_id=user.id).delete()
    db.session.commit()
    return jsonify(ok=True)


# --------------------------------------------------------------------------
# Notes (diary) API
# --------------------------------------------------------------------------
@app.route("/api/notes", methods=["POST"])
@api_login_required
def api_note_create(user):
    note = Note(user_id=user.id)
    apply_note(note, request.get_json(silent=True) or {})
    db.session.add(note)
    db.session.commit()
    return jsonify(note=note_json(note)), 201


@app.route("/api/notes/<int:note_id>", methods=["PATCH"])
@api_login_required
def api_note_update(user, note_id):
    note = Note.query.filter_by(id=note_id, user_id=user.id).first_or_404()
    apply_note(note, request.get_json(silent=True) or {})
    db.session.commit()
    return jsonify(note=note_json(note))


@app.route("/api/notes/<int:note_id>", methods=["DELETE"])
@api_login_required
def api_note_delete(user, note_id):
    db.session.delete(Note.query.filter_by(id=note_id, user_id=user.id).first_or_404())
    db.session.commit()
    return jsonify(ok=True)


@app.route("/healthz")
def healthz():
    db.session.execute(db.text("SELECT 1"))
    return Response("ok", mimetype="text/plain")


# --------------------------------------------------------------------------
# Forgot username / password, e-mail verification
# --------------------------------------------------------------------------
@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    sent, error = False, ""
    if request.method == "POST":
        email = clean_email(request.form.get("email"))
        if not email:
            error = "Please enter the email address you signed up with."
        elif not mailer.mail_enabled():
            error = "Email is not set up on this server yet, so we cannot send a recovery message."
        elif not rate_ok(f"forgot:{request.remote_addr}:{email}", 3, 900):
            error = "Too many requests. Please wait a few minutes and try again."
        else:
            user = find_by_email(email)
            if user:  # the page looks the same whether or not the address exists
                token = serializer("pw-reset").dumps({"u": user.id, "p": pw_stamp(user)})
                link = f"{site_url()}{url_for('reset_password', token=token)}"
                try_send(user.email, "Recover your Do.iT account", "Recover your account",
                         f"Your username is {user.username.strip()}. If you also forgot your password, use the button below to choose a new one.",
                         button=("Choose a new password", link),
                         footer="This link works for 1 hour. If you did not ask for this, you can ignore this email.")
            sent = True
    return render_template("forgot.html", sent=sent, error=error)


@app.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        data = serializer("pw-reset").loads(token, max_age=3600)
    except (BadSignature, SignatureExpired):
        return render_template("message.html", title="Link expired", text="This reset link is invalid or has expired. Please request a new one.",
                               button=("Request a new link", url_for("forgot"))), 400
    user = db.session.get(User, data.get("u"))
    if not user or pw_stamp(user) != data.get("p"):
        return render_template("message.html", title="Link already used", text="This reset link has already been used. Please request a new one.",
                               button=("Request a new link", url_for("forgot"))), 400
    error = ""
    if request.method == "POST":
        new, again = request.form.get("password", ""), request.form.get("confirm", "")
        if len(new) < 8:
            error = "Please choose a password of at least 8 characters."
        elif new != again:
            error = "The two passwords do not match."
        else:
            user.password = generate_password_hash(new)
            user.email_verified = True  # they proved they own the inbox
            db.session.commit()
            return redirect(url_for("login", notice="Password updated. You can sign in now."))
    return render_template("reset.html", error=error, username=user.username.strip())


@app.route("/verify/<token>")
def verify_email(token):
    try:
        data = serializer("verify-email").loads(token, max_age=7 * 86400)
    except (BadSignature, SignatureExpired):
        return render_template("message.html", title="Link expired", text="This confirmation link is invalid or expired. Sign in and request a new one from your profile.",
                               button=("Sign in", url_for("login"))), 400
    user = db.session.get(User, data.get("u"))
    if user and user.email == data.get("e"):
        user.email_verified = True
        db.session.commit()
        return render_template("message.html", title="Email confirmed", text="Thank you! Task and timetable email reminders are now active.",
                               button=("Open Do.iT", url_for("homepage")))
    return render_template("message.html", title="Link not valid", text="This link no longer matches your account.",
                           button=("Sign in", url_for("login"))), 400


# --------------------------------------------------------------------------
# Account settings API
# --------------------------------------------------------------------------
@app.route("/api/account/email", methods=["POST"])
@api_login_required
def api_set_email(user):
    email = clean_email((request.get_json(silent=True) or {}).get("email"))
    if not email:
        raise ValidationError("Please enter a valid email address.")
    other = find_by_email(email)
    if other and other.id != user.id:
        raise ValidationError("That email is already used by another account.")
    changed = email != (user.email or "")
    user.email = email
    if changed:
        user.email_verified = False
    db.session.commit()
    warn = ""
    if changed and mailer.mail_enabled() and rate_ok(f"verify:{user.id}", 5, 3600):
        warn = send_verification(user)
    return jsonify(ok=True, verified=user.email_verified, warning=warn)


@app.route("/api/account/resend", methods=["POST"])
@api_login_required
def api_resend_verification(user):
    if not user.email:
        raise ValidationError("Add your email address first.")
    if not mailer.mail_enabled():
        raise ValidationError("Email is not set up on this server yet.")
    if not rate_ok(f"verify:{user.id}", 5, 3600):
        raise ValidationError("Please wait a little before asking again.")
    err = send_verification(user)
    if err:
        raise ValidationError("The email could not be sent. " + err)
    return jsonify(ok=True)


@app.route("/api/account/name", methods=["POST"])
@api_login_required
def api_set_name(user):
    name = re.sub(r"\s+", " ", str((request.get_json(silent=True) or {}).get("name", "")).strip())[:80]
    user.name = name or None
    db.session.commit()
    return jsonify(ok=True, name=user.display_name, full_name=name)


@app.route("/api/account/delete", methods=["POST"])
@api_login_required
def api_delete_account(user):
    if not rate_ok(f"delete:{user.id}", 5, 900):
        raise ValidationError("Too many attempts. Please wait a few minutes.")
    if not check_password_hash(user.password, str((request.get_json(silent=True) or {}).get("password", ""))):
        raise ValidationError("That password is not correct.")
    if user.email:
        PendingSignup.query.filter(db.func.lower(PendingSignup.email) == user.email.lower()).delete(synchronize_session=False)
    EmailLog.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)  # tasks, checklists, timetable and diary entries are deleted with it
    db.session.commit()
    session.clear()
    return jsonify(ok=True)


@app.route("/export.json")
def export_json():
    user = get_user()
    if not user:
        return redirect(url_for("login"))
    data = {"exported_at": utc_iso(utcnow()),
            "account": {"name": user.name, "username": user.username, "email": user.email},
            "tasks": [task_json(t) for t in Task.query.filter_by(user_id=user.id).order_by(Task.created_at).all()],
            "timetable": [tt_json(e) for e in TimetableEntry.query.filter_by(user_id=user.id).order_by(TimetableEntry.day, TimetableEntry.start_min).all()],
            "diary": [note_json(n) for n in Note.query.filter_by(user_id=user.id).order_by(Note.entry_date).all()]}
    return Response(json.dumps(data, indent=2, ensure_ascii=False), mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=do-it-my-data.json"})


@app.route("/api/ai/status", methods=["POST"])
@api_login_required
def api_ai_status(user):
    if not rate_ok(f"aistatus:{user.id}", 10, 3600):
        raise ValidationError("Please wait a little before testing again.")
    return jsonify(ai.diagnose())


@app.route("/api/account/prefs", methods=["POST"])
@api_login_required
def api_prefs(user):
    data = request.get_json(silent=True) or {}
    if "email_reminders" in data:
        user.email_reminders = bool(data["email_reminders"])
    if "email_digest" in data:
        user.email_digest = bool(data["email_digest"])
    db.session.commit()
    return jsonify(ok=True)


@app.route("/api/account/timezone", methods=["POST"])
@api_login_required
def api_timezone(user):
    tz = str((request.get_json(silent=True) or {}).get("tz", ""))[:64]
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationError("Unknown timezone.")
    user.tz = tz
    db.session.commit()
    return jsonify(ok=True)


@app.route("/api/account/test-email", methods=["POST"])
@api_login_required
def api_test_email(user):
    if not user.email:
        raise ValidationError("Add your email address first.")
    if not mailer.mail_enabled():
        raise ValidationError("Email is not set up on this server yet.")
    if not rate_ok(f"testmail:{user.id}", 5, 3600):
        raise ValidationError("Too many test emails. Try again later.")
    err = try_send(user.email, "Test email from Do.iT", "It works!", f"Hi {user.display_name}, this is a test email from Do.iT. Your reminders can reach you.",
                   button=("Open Do.iT", f"{site_url()}{url_for('homepage')}"))
    if err:
        raise ValidationError("The email could not be sent. " + err)
    return jsonify(ok=True)


# --------------------------------------------------------------------------
# Automatic e-mail reminders (called every few minutes by a free cron service)
# --------------------------------------------------------------------------
def _fmt(dt):
    return dt.strftime("%I:%M %p").lstrip("0")


def _already(user, key):
    return EmailLog.query.filter_by(user_id=user.id, key=key).first() is not None


def run_email_reminders():
    now_utc = datetime.now(timezone.utc)
    digest_hour = int(os.environ.get("DIGEST_HOUR", "7"))
    sent = 0
    EmailLog.query.filter(EmailLog.sent_at < utcnow() - timedelta(days=14)).delete()
    users = User.query.filter(User.email.isnot(None), User.email_verified.is_(True)).all()
    for user in users:
        local = now_utc.astimezone(user_zone(user)).replace(tzinfo=None)
        app_link = f"{site_url()}{url_for('homepage')}"
        keys, lines = [], []
        if user.email_reminders:
            for t in Task.query.filter(Task.user_id == user.id, Task.completed_at.is_(None),
                                       Task.reminder_datetime.isnot(None), Task.reminder_datetime <= local,
                                       Task.reminder_datetime > local - timedelta(minutes=30)).all():
                key = f"task-{t.id}-{t.reminder_datetime:%Y%m%d%H%M}"
                if not _already(user, key):
                    keys.append(key)
                    lines.append(f"Task: {t.task}" + (f" (due {_fmt(t.due_date)})" if t.due_date and t.due_has_time else ""))
            now_min = local.hour * 60 + local.minute
            for e in TimetableEntry.query.filter_by(user_id=user.id, day=local.weekday(), alerts=True).all():
                if 0 <= e.start_min - now_min <= 30:
                    key = f"tt-{e.id}-{local:%Y%m%d}"
                    if not _already(user, key):
                        keys.append(key)
                        start = local.replace(hour=e.start_min // 60, minute=e.start_min % 60)
                        lines.append(f"Timetable: {e.title} at {_fmt(start)}" + (f" ({e.location})" if e.location else ""))
        if lines:
            subject = "Reminder: " + lines[0].split(": ", 1)[1].split(" (")[0] + (f" (+{len(lines) - 1} more)" if len(lines) > 1 else "")
            if not try_send(user.email, subject, "Coming up", f"Hi {user.display_name}, here is what needs your attention:", lines,
                            ("Open Do.iT", app_link), "You can turn these emails off in Profile."):
                db.session.add_all(EmailLog(user_id=user.id, key=k) for k in keys)
                db.session.commit()
                sent += 1
        if user.email_digest and digest_hour <= local.hour < 12:
            dkey = f"digest-{local:%Y%m%d}"
            if not _already(user, dkey):
                items = [f"{_fmt(local.replace(hour=e.start_min // 60, minute=e.start_min % 60))}  {e.title}" + (f" ({e.location})" if e.location else "")
                         for e in TimetableEntry.query.filter_by(user_id=user.id, day=local.weekday()).order_by(TimetableEntry.start_min).all()]
                tasks = Task.query.filter(Task.user_id == user.id, Task.completed_at.is_(None), Task.due_date.isnot(None),
                                          Task.due_date < datetime.combine(local.date() + timedelta(days=1), datetime.min.time())).all()
                items += [("Overdue: " if t.due_date.date() < local.date() else "Task: ") + t.task for t in tasks]
                if items and not try_send(user.email, "Your day on Do.iT", f"Good morning, {user.display_name}", "Here is your plan for today:", items,
                                          ("Open my day", app_link), "Turn off the morning summary in Profile."):
                    db.session.add(EmailLog(user_id=user.id, key=dkey))
                    db.session.commit()
                    sent += 1
    db.session.commit()
    return sent


@app.route("/api/cron/reminders")
def cron_reminders():
    secret = os.environ.get("CRON_SECRET", "")
    given = request.args.get("key") or request.headers.get("X-Cron-Key", "")
    if not secret or not secrets.compare_digest(given, secret):
        return jsonify(error="Not allowed."), 403
    if not mailer.mail_enabled():
        return jsonify(error="Email is not set up."), 503
    return jsonify(ok=True, emails_sent=run_email_reminders())


# --------------------------------------------------------------------------
# AI assistant
# --------------------------------------------------------------------------
def ai_guard(user):
    if not ai.ai_enabled():
        raise ValidationError("AI is not set up on this server yet.")
    if not rate_ok(f"ai:{user.id}", int(os.environ.get("AI_HOURLY_LIMIT", "40")), 3600):
        raise ValidationError("You have reached the hourly AI limit. Please try again later.")


def ai_call(system, messages, max_tokens=700, json_mode=False):
    try:
        return ai.complete(system, messages, max_tokens, json_mode)
    except ai.AIError as err:
        raise ValidationError(str(err))


DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _hm(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def free_windows(entries, now_min, day_end=22 * 60, min_len=30):
    """Gaps (at least min_len minutes) left in today's timetable, from now until the evening."""
    cur, out = max(now_min, 6 * 60), []
    for start, end in sorted((e.start_min, e.end_min) for e in entries if e.end_min > now_min):
        if start - cur >= min_len:
            out.append(f"{_hm(cur)}-{_hm(start)}")
        cur = max(cur, end)
    if day_end - cur >= min_len:
        out.append(f"{_hm(cur)}-{_hm(day_end)}")
    return out


def clean_actions(raw):
    """Check the actions the AI proposed. The user still has to tap to add each one."""
    out = []
    for a in (raw or [])[:6]:
        if not isinstance(a, dict):
            continue
        title = re.sub(r"\s+", " ", str(a.get("title", "")).strip())
        if a.get("type") == "add_task" and title:
            due = a.get("due") if parse_dt(a.get("due"), "%Y-%m-%d") else ""
            tm = a.get("time") if due and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(a.get("time") or "")) else ""
            out.append({"type": "add_task", "title": title[:255], "due": due, "time": tm,
                        "priority": a.get("priority") if a.get("priority") in ("low", "medium", "high") else "medium",
                        "tags": [str(t).strip()[:24] for t in (a.get("tags") or []) if str(t).strip()][:5]})
        elif a.get("type") == "add_timetable" and title:
            days = sorted({d for d in (a.get("days") or []) if isinstance(d, int) and 0 <= d <= 6})
            try:
                start, end = minutes_of(a.get("start")), minutes_of(a.get("end"))
            except ValidationError:
                continue
            if days and end > start:
                out.append({"type": "add_timetable", "title": title[:120], "days": days, "start": _hm(start), "end": _hm(end)})
    return out


def try_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text[text.index("{"): text.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return None


@app.route("/api/ai/chat", methods=["POST"])
@api_login_required
def api_ai_chat(user):
    ai_guard(user)
    data = request.get_json(silent=True) or {}
    msgs = [{"role": m["role"], "content": str(m.get("content", ""))[:2000]} for m in (data.get("messages") or [])[-12:]
            if isinstance(m, dict) and m.get("role") in ("user", "assistant") and str(m.get("content", "")).strip()]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    if not msgs or msgs[-1]["role"] != "user":
        raise ValidationError("Please type a message.")
    now = parse_dt(data.get("now"), "%Y-%m-%dT%H:%M") or utcnow()
    now_min = now.hour * 60 + now.minute
    tasks = (Task.query.filter(Task.user_id == user.id, Task.completed_at.is_(None))
             .order_by(Task.due_date.is_(None), Task.due_date).limit(60).all())
    week = TimetableEntry.query.filter_by(user_id=user.id).order_by(TimetableEntry.day, TimetableEntry.start_min).limit(100).all()
    today_entries = [e for e in week if e.day == now.weekday()]
    context = {
        "now": now.strftime("%A %d %B %Y, %H:%M"), "today_is_day_number": now.weekday(),
        "pending_tasks": [{"title": t.task, "due": iso(t.due_date) if t.due_date else None,
                           "overdue": bool(t.due_date and (t.due_date < now if t.due_has_time else t.due_date.date() < now.date())),
                           "priority": (t.priority or TaskPriority.MEDIUM).value, "tags": split_tags(t.tags),
                           "steps_left": sum(1 for s in t.subtasks if not s.done)} for t in tasks],
        "weekly_timetable": [{"day": DAY_NAMES[e.day], "from": _hm(e.start_min), "to": _hm(e.end_min), "title": e.title} for e in week],
        "free_time_left_today": free_windows(today_entries, now_min)}
    system = (
        f"You are Do.iT Assistant, a sharp, warm productivity coach inside a to-do, timetable and diary app. You are talking to {user.display_name}.\n"
        "RULES:\n"
        "- Use ONLY the live data below. Refer to real task and class names. Never invent tasks, classes or times that are not in the data.\n"
        "- When asked to plan, give a short time-blocked schedule that respects the timetable and the free windows. Put urgent and overdue work first.\n"
        "- Be concrete and brief (under 170 words unless asked for more). If the request is unclear, ask ONE short question.\n"
        "- Write in the same language the user writes in. Plain text only: no markdown symbols, use short lines starting with a dash.\n"
        "- You cannot change anything yourself. When the user asks you to add, schedule or plan something concrete, you may PROPOSE actions; the user taps to confirm.\n"
        'REPLY FORMAT: return ONLY a JSON object: {"reply": "your message", "actions": []}.\n'
        'Action shapes: {"type":"add_task","title":"...","due":"YYYY-MM-DD or null","time":"HH:MM or null","priority":"low|medium|high","tags":["..."]} and '
        '{"type":"add_timetable","title":"...","days":[0=Monday ... 6=Sunday],"start":"HH:MM","end":"HH:MM"}. Use at most 6 actions, and an empty list when none are needed.\n'
        "LIVE DATA:\n" + json.dumps(context, ensure_ascii=False))
    raw = ai_call(system, msgs, 700, json_mode=True)
    parsed = try_json(raw)
    if parsed and isinstance(parsed.get("reply"), str) and parsed["reply"].strip():
        return jsonify(reply=parsed["reply"].strip(), actions=clean_actions(parsed.get("actions")))
    return jsonify(reply=raw, actions=[])


def _parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text[text.index("{"): text.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        raise ValidationError("The AI answer could not be read. Please try again.")


@app.route("/api/ai/breakdown", methods=["POST"])
@api_login_required
def api_ai_breakdown(user):
    ai_guard(user)
    goal = str((request.get_json(silent=True) or {}).get("goal", "")).strip()[:300]
    if not goal:
        raise ValidationError("Describe the goal you want to break down.")
    system = ('Break the user\'s goal into a short, ordered checklist. Reply with ONLY JSON like '
              '{"title": "short task title", "steps": ["step 1", "step 2"]}. Use 3 to 8 steps, each under 80 characters, '
              "starting with a verb. Write in the same language as the goal.")
    out = _parse_json(ai_call(system, [{"role": "user", "content": goal}], 500))
    steps = [str(x).strip()[:255] for x in out.get("steps", []) if str(x).strip()][:8]
    if not steps:
        raise ValidationError("The AI did not return any steps. Please try again.")
    return jsonify(title=str(out.get("title") or goal)[:255], steps=steps)


@app.route("/api/ai/note", methods=["POST"])
@api_login_required
def api_ai_note(user):
    ai_guard(user)
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action == "week":  # weekly review: the server gathers the last 7 days of entries, only when the user asks
        today = (parse_dt(data.get("now"), "%Y-%m-%dT%H:%M") or utcnow()).date()
        entries = (Note.query.filter(Note.user_id == user.id, Note.entry_date >= today - timedelta(days=6), Note.entry_date <= today)
                   .order_by(Note.entry_date).all())
        text = "\n\n".join(f"{n.entry_date:%A %d %B}" + (f" (mood: {n.mood})" if n.mood else "") + (f" - {n.title}" if n.title else "") + "\n" + (n.body or "")
                            for n in entries if (n.body or n.title))[:7000]
        if not text.strip():
            raise ValidationError("There are no diary entries from the last 7 days yet.")
    else:
        text = str(data.get("text", "")).strip()[:8000]
    if not text:
        raise ValidationError("Write something first.")
    prompts = {
        "week": "You are a kind journaling coach. Read this week of diary entries and reply, in the same language, with three short parts: 'Highlights' (2 bullets), 'Patterns I notice' (2 bullets about mood, energy or habits) and 'One thing to try next week' (1 sentence). Be warm and specific, under 150 words, plain text with dashes.",
        "polish": "The text was probably typed by voice, so fix spelling, grammar and punctuation, split run-on speech into sentences and paragraphs, and remove words or whole sentences that are accidentally repeated by the speech recognition (the same phrase twice in a row). Keep the author's words, voice, meaning and language. Return ONLY the corrected text.",
        "summarize": "Summarize this diary entry in 2 to 3 warm, plain sentences, in the same language. Return ONLY the summary.",
        "reflect": "Read this diary entry and offer three gentle reflection questions that could help the writer think about their day. Use a short numbered list, in the same language. Return ONLY the questions."}
    if action not in prompts:
        raise ValidationError("Unknown action.")
    return jsonify(text=ai_call(prompts[action], [{"role": "user", "content": text}], 1500))


# --------------------------------------------------------------------------
# SEO / PWA files
# --------------------------------------------------------------------------
@app.route("/robots.txt")
def robots():
    body = ("User-agent: *\nAllow: /\nDisallow: /app\nDisallow: /api/\nDisallow: /export.csv\n"
            f"Sitemap: {site_url()}/sitemap.xml\n")
    return Response(body, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap():
    pages = [("landing", "1.0"), ("login", "0.6"), ("create_account", "0.8")]
    urls = "".join(f"<url><loc>{site_url()}{url_for(ep)}</loc><priority>{pr}</priority></url>"
                   for ep, pr in pages)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>')
    return Response(xml, mimetype="application/xml")


@app.route("/manifest.webmanifest")
def manifest():
    data = {"name": "Do.iT - Digital To-do List", "short_name": "Do.iT", "start_url": "/app",
            "scope": "/", "display": "standalone", "background_color": "#f4f7fe",
            "theme_color": "#0741dc", "description": "Plan your day, set alarms and get things done.",
            "icons": [{"src": asset("images/icon-192.png"), "sizes": "192x192", "type": "image/png"},
                      {"src": asset("images/icon-512.png"), "sizes": "512x512", "type": "image/png",
                       "purpose": "any"}]}
    return Response(json.dumps(data), mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    resp = app.send_static_file("sw.js")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.route("/api/account/password", methods=["POST"])
@api_login_required
def api_change_password(user):
    data = request.get_json(silent=True) or {}
    if not check_password_hash(user.password, str(data.get("current", ""))):
        return jsonify(error="Your current password is not correct."), 400
    new = str(data.get("new", ""))
    if len(new) < 8:
        return jsonify(error="Choose a new password of at least 8 characters."), 400
    user.password = generate_password_hash(new)
    db.session.commit()
    return jsonify(ok=True)


@app.route("/export.csv")
def export_csv():
    user = get_user()
    if not user:
        return redirect(url_for("login"))

    def safe(value):  # stop spreadsheet formula injection
        value = "" if value is None else str(value)
        return "'" + value if value[:1] in "=+-@" else value

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Task", "Notes", "Priority", "Tags", "Due", "Reminder", "Repeat",
                     "Status", "Completed (UTC)", "Subtasks"])
    for t in Task.query.filter_by(user_id=user.id).order_by(Task.created_at).all():
        writer.writerow([safe(t.task), safe(t.notes), t.priority.value, safe(t.tags),
                         iso(t.due_date) or "", iso(t.reminder_datetime) or "",
                         (t.recurring or RecurrenceEnum.none).value,
                         "done" if t.is_completed else "open",
                         utc_iso(t.completed_at) or "",
                         safe("; ".join(("[x] " if s.done else "[ ] ") + s.title
                                        for s in t.subtasks))])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=do-it-tasks.csv"})


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
