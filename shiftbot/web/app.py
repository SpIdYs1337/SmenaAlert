from __future__ import annotations

import hmac
import secrets
import sqlite3
import threading
from datetime import date, timedelta
from time import monotonic

from flask import Flask, abort, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash

from .. import __version__
from ..config import ConfigError, MSK
from ..iiko import IikoError
from .access import Accounts
from .knowledge import DOCUMENT_LIMIT
from .workspace import request_data, workspace_api
from .mail import MailError
from .service import Service, can_check_receipt
from .settings import InputError, email_address, flag, iiko_settings, list_ids, number, point_settings, smtp_settings, text
from .statistics import opening_statistics, openings_on_day


def same(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def create_app(service: Service, *, admin: str, password: str, secure_cookie=False, demo=False) -> Flask:
    app = Flask(__name__)
    app.jinja_env.globals.update(panel_name="iiko-Контроль смены", panel_version=__version__)
    app.secret_key = service.vault.session_key(admin, password)
    app.config.update(SESSION_COOKIE_NAME="shift_monitor", SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_SECURE=secure_cookie,
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=12), MAX_CONTENT_LENGTH=128 * 1024)
    db = service.db
    accounts = Accounts(db, admin)
    app.register_blueprint(workspace_api(service, accounts))
    dummy_hash = generate_password_hash(secrets.token_urlsafe(32))
    failures: dict[str, list[float]] = {}
    login_lock = threading.Lock()

    def token():
        return session.setdefault("csrf", secrets.token_urlsafe(32))

    def identity():
        if demo or session.get("admin") == admin:
            return accounts.owner
        user = accounts.get(session["employee"]) if session.get("employee") else None
        if user and user["active"] and session.get("version") == user["version"]:
            return user
        return None

    @app.before_request
    def require_access():
        if request.endpoint == "work.article_import":
            request.max_content_length = DOCUMENT_LIMIT + 128 * 1024
        if request.endpoint in {"login", "static", "health"}:
            return
        g.user = identity()
        if not g.user:
            if request.path.startswith("/api/"):
                return jsonify(error="Сессия завершена. Войдите в панель заново."), 401
            return redirect(url_for("login"))
        if g.user["must_change"] and request.endpoint not in {"change_password", "logout"}:
            if request.path.startswith("/api/"):
                return jsonify(error="Задайте собственный пароль при первом входе."), 403
            return redirect(url_for("change_password"))
        shared = {"index", "logout", "change_password", "dashboard"}
        if request.blueprint != "work" and request.endpoint not in shared and "monitor.manage" not in g.user["grants"]:
            abort(403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            supplied = request.headers.get("X-CSRF-Token", "") or request.form.get("csrf", "")
            if not session.get("csrf") or not same(session["csrf"], supplied):
                return jsonify(error="Обновите страницу и повторите действие."), 403

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; img-src 'self' data:"
        )
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(InputError)
    @app.errorhandler(ConfigError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(IikoError)
    @app.errorhandler(MailError)
    def unavailable(error):
        return jsonify(error=str(error)), 502

    @app.errorhandler(sqlite3.IntegrityError)
    def conflict(error):
        messages = {"people.email": "Такой адрес уже есть в списке получателей.",
                    "employees.login_key": "Этот логин уже занят.",
                    "assets.tag_key": "Техника с таким инвентарным номером уже принята."}
        message = next((value for key, value in messages.items() if key in str(error)),
                       "Связанная запись недоступна. Обновите страницу и проверьте настройки.")
        return jsonify(error=message), 409

    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith("/api/"):
            messages = {403: "Недостаточно прав.", 404: "Запись не найдена.", 413: "Слишком большой запрос или файл."}
            return jsonify(error=messages.get(error.code, "Запрос не удалось выполнить.")), error.code
        return error

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if identity():
            return redirect(url_for("index"))
        error, status = None, 200
        if request.method == "POST":
            if not same(token(), request.form.get("csrf", "")):
                return render_template("login.html", csrf=token(), error="Обновите страницу и повторите вход."), 403
            addr, now = request.remote_addr or "local", monotonic()
            login_name = request.form.get("login", "")
            entered = request.form.get("password", "")
            with login_lock:
                for source in list(failures):
                    attempts = [attempt for attempt in failures[source] if now - attempt < 300]
                    if attempts:
                        failures[source] = attempts
                    else:
                        del failures[source]
                if len(failures.get(addr, [])) >= 8:
                    error, status = "Слишком много попыток. Попробуйте через пять минут.", 429
                    return render_template("login.html", csrf=token(), error=error), status
                failures.setdefault(addr, []).append(now)
            owner = same(login_name, admin) and same(entered, password)
            employee = None
            if not owner and len(login_name) <= 120 and len(entered) <= 256:
                employee = accounts.authenticate(login_name, entered, dummy_hash)
            if owner or employee:
                with login_lock:
                    failures.pop(addr, None)
                session.clear()
                if owner:
                    session["admin"] = admin
                else:
                    session["employee"], session["version"] = employee
                session.permanent = True
                return redirect(url_for("index"))
            error, status = "Проверьте логин и пароль.", 401
        return render_template("login.html", csrf=token(), error=error), status

    @app.route("/account/password", methods=["GET", "POST"])
    def change_password():
        if g.user["id"] == accounts.owner["id"]:
            return redirect(url_for("index"))
        error = None
        if request.method == "POST":
            try:
                new = request.form.get("new_password", "")
                if new != request.form.get("confirm_password", ""):
                    raise InputError("Новый пароль и подтверждение должны совпадать.")
                version = accounts.change_password(g.user["id"], g.user["version"], request.form.get("password", ""), new)
            except InputError as exc:
                error = str(exc)
            else:
                session.clear()
                session["employee"], session["version"] = g.user["id"], version
                session.permanent = True
                return redirect(url_for("index"))
        return render_template("password.html", csrf=token(), error=error, first=g.user["must_change"]), 400 if error else 200

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/healthz")
    def health():
        return "ok\n"

    @app.get("/")
    def index():
        return render_template("index.html", csrf=token(), admin=g.user["name"], user=g.user, demo=demo)

    @app.get("/api/dashboard")
    def dashboard():
        if "monitor.manage" not in g.user["grants"]:
            return jsonify(now=service.now().isoformat())
        return jsonify(service.snapshot())

    @app.get("/api/statistics")
    def statistics():
        try:
            period = int(request.args.get("days", "7"))
        except ValueError:
            raise InputError("Укажите количество дней для отчёта.") from None
        period = number(period, "Период отчёта", 1, 90)
        today = service.now().astimezone(MSK).date()
        return jsonify(opening_statistics(db, today, period, request.args.get("point") or None))

    @app.get("/api/statistics/day/<day>")
    def statistics_day(day):
        try:
            selected = date.fromisoformat(day)
        except ValueError:
            raise InputError("Проверьте дату отчёта.") from None
        if selected.isoformat() != day or selected > service.now().astimezone(MSK).date():
            raise InputError("Проверьте дату отчёта.")
        return jsonify(openings_on_day(db, selected, request.args.get("point") or None))

    @app.put("/api/smtp")
    def save_smtp():
        db.save_setting("smtp", smtp_settings(request_data(), db.get_setting("smtp", {}), service.vault))
        db.save_setting("health:smtp", None)
        return jsonify(ok=True)

    @app.post("/api/smtp/probe")
    def probe_smtp():
        service.call(service.probe_smtp)
        return jsonify(ok=True)

    @app.post("/api/smtp/test")
    def test_smtp():
        job_id = service.queue_test(email_address(request_data().get("email")))
        return jsonify(id=job_id), 202

    @app.put("/api/imap")
    def save_imap():
        service.call(service.save_imap, request_data())
        return jsonify(ok=True)

    @app.post("/api/imap/probe")
    def probe_imap():
        service.call(service.probe_imap)
        return jsonify(ok=True)

    @app.get("/api/history/<int:job_id>")
    def delivery(job_id):
        job = db.delivery(job_id)
        if not job:
            abort(404)
        visible = {key: value for key, value in job.items() if key not in {"event_key", "revision", "message_id"}}
        return jsonify(**visible, can_check_imap=can_check_receipt(job, db.get_setting("imap", {})))

    @app.post("/api/history/<int:job_id>/imap")
    def check_receipt(job_id):
        job = db.delivery(job_id)
        if not job:
            abort(404)
        return jsonify(found=service.call(service.check_receipt, job))

    @app.post("/api/history/<int:job_id>/retry")
    def retry(job_id):
        if not db.retry(job_id):
            raise InputError("Повтор доступен для письма с ошибкой отправки.")
        return jsonify(ok=True), 202

    @app.post("/api/servers/<server_id>/probe")
    def probe_iiko(server_id):
        service.call(service.probe_iiko, server_id, False)
        return jsonify(ok=True)

    @app.post("/api/servers/<server_id>/departments")
    def departments(server_id):
        return jsonify(items=service.call(service.probe_iiko, server_id, True))

    @app.post("/api/check")
    def check():
        point_id = request_data().get("point")
        if point_id is not None and (not isinstance(point_id, str) or not db.get("points", point_id)):
            abort(404)
        return jsonify(checked=service.call(service.check, True, point_id))

    @app.put("/api/monitor")
    def preferences():
        payload = request_data()
        previous = db.preferences()
        settings = {"enabled": flag(payload, "enabled", previous["enabled"]),
                    "inclusive": flag(payload, "inclusive", previous["inclusive"]),
                    "poll_seconds": number(payload.get("poll_seconds", previous["poll_seconds"]), "Интервал проверки", 5, 300)}
        if settings["enabled"] and not previous["enabled"]:
            if not db.get_setting("smtp"):
                raise InputError("Сохраните настройки SMTP, чтобы включить авторассылку.")
            if not any(point["active"] and db.targets(point["id"]) for point in db.list("points")):
                raise InputError("Добавьте активную точку и закрепите за ней получателя.")
        db.save_setting("monitor", settings)
        return jsonify(ok=True)

    @app.post("/api/points/<point_id>/preview")
    @app.post("/api/points/<point_id>/send")
    def manual_letter(point_id):
        payload = request_data()
        note = payload.get("note", "")
        if not isinstance(note, str) or len(note) > 2000 or "\0" in note:
            raise InputError("Комментарий должен содержать до 2000 символов.")
        point, subject, body = service.manual_preview(point_id, note.strip())
        if request.path.endswith("/preview"):
            return jsonify(subject=subject, body=body)
        ids = service.queue_manual(point, list_ids(payload, "people"), subject, body)
        return jsonify(ids=ids), 202

    @app.post("/api/<entity>")
    @app.route("/api/<entity>/<item_id>", methods=["PUT", "DELETE"])
    def edit(entity, item_id=None):
        if entity not in {"servers", "people", "points"}:
            abort(404)
        before = db.get(entity, item_id) if item_id else {}
        if before is None:
            abort(404)
        if request.method == "DELETE":
            db.delete(entity, item_id)
            return jsonify(ok=True)
        payload, links = request_data(), None
        if entity == "servers":
            value = iiko_settings(payload, before, service.vault)
        elif entity == "points":
            value = point_settings(payload)
            links = list_ids(payload, "people")
        else:
            email = email_address(payload.get("email"))
            value = {"name": text(payload, "name", "Имя", required=False, limit=120) or email,
                     "email": email, "active": flag(payload, "active", True)}
            links = list_ids(payload, "points")
        saved_id = db.save(entity, value, item_id, links)
        if entity == "servers":
            db.save_setting(f"health:{saved_id}", None)
        return jsonify(id=saved_id), 200 if item_id else 201

    return app
