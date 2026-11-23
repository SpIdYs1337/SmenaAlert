"""Authenticated HTTP endpoints for the knowledge base, inventory and administration."""
from __future__ import annotations

import json
from functools import wraps
from io import BytesIO

from flask import Blueprint, abort, g, jsonify, request, send_file

from ..config import MSK
from .access import PERMISSIONS
from .acts import ACT_MIME, ACT_OPERATIONS
from .inventory import ACTIONS, SERVICE_WARNING_DAYS, STATES, Inventory
from .knowledge import DOCUMENT_LIMIT, MAX_MEDIA, MEDIA_ID, Knowledge
from .media import DOWNLOAD_MIME, article_media, photo_content
from .settings import InputError


def request_data():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise InputError("Не удалось прочитать данные формы.")
    return value


def uploaded_article():
    if request.mimetype != "multipart/form-data":
        return request_data(), {}
    try:
        data = json.loads(request.form.get("record", ""))
    except (ValueError, RecursionError):
        raise InputError("Не удалось прочитать статью.") from None
    if not isinstance(data, dict):
        raise InputError("Не удалось прочитать статью.")
    if len(request.files) > MAX_MEDIA:
        raise InputError("К статье можно прикрепить до 12 файлов.")
    uploads = {}
    for key, files in request.files.lists():
        prefix, _, item = key.partition(":")
        if prefix != "media" or not MEDIA_ID.fullmatch(item) or len(files) != 1 or not files[0].filename:
            raise InputError("Проверьте вложения статьи.")
        uploads[item] = article_media(files[0])
    return data, uploads


def requires(grant):
    def decorate(view):
        @wraps(view)
        def checked(*args, **kwargs):
            if grant not in g.user["grants"]:
                abort(403)
            return view(*args, **kwargs)
        return checked
    return decorate


def workspace_api(service, accounts):
    api = Blueprint("work", __name__, url_prefix="/api/work")
    knowledge, inventory = Knowledge(service.db), Inventory(service.db)

    def now():
        return service.now().astimezone(MSK)

    @api.get("/profile")
    def profile():
        person = g.user
        assigned = inventory.assets(now().date(), person["id"]) if "inventory.read" in person["grants"] else []
        return jsonify(person=person, permissions=PERMISSIONS, assigned=assigned)

    @api.get("/employees")
    @requires("admin.manage")
    def employees():
        return jsonify(items=accounts.all(), permissions=PERMISSIONS)

    @api.post("/employees")
    @api.put("/employees/<employee>")
    @requires("admin.manage")
    def employee_save(employee=None):
        saved = accounts.save(request_data(), g.user["id"], employee)
        return jsonify(id=saved), 200 if employee else 201

    @api.get("/knowledge")
    @requires("knowledge.read")
    def articles():
        return jsonify(items=knowledge.tree(archived=request.args.get("archived") == "1"))

    @api.get("/knowledge/<item>")
    @requires("knowledge.read")
    def article(item):
        return jsonify(knowledge.get(item) or abort(404))

    @api.post("/knowledge")
    @api.put("/knowledge/<item>")
    @requires("knowledge.write")
    def article_save(item=None):
        data, uploads = uploaded_article()
        saved = knowledge.save(data, now().isoformat(timespec="seconds"), g.user["name"], item, uploads)
        return jsonify(id=saved), 200 if item else 201

    @api.put("/knowledge/<item>/archive")
    @requires("knowledge.write")
    def article_archive(item):
        knowledge.set_archived(item, request_data())
        return jsonify(id=item)

    @api.delete("/knowledge/<item>")
    @requires("knowledge.write")
    def article_delete(item):
        knowledge.delete(item, request_data())
        return jsonify(id=item)

    @api.post("/knowledge/import")
    @requires("knowledge.write")
    def article_import():
        upload = request.files.get("file")
        if not upload:
            raise InputError("Выберите файл для импорта.")
        item, count = knowledge.import_file(upload.filename, upload.stream.read(DOCUMENT_LIMIT + 1),
                                           request.form.get("parent") or None,
                                           now().isoformat(timespec="seconds"), g.user["name"])
        return jsonify(id=item, count=count), 201

    @api.get("/knowledge/<item>/original")
    @requires("knowledge.read")
    def original(item):
        row = knowledge.original(item)
        if not row:
            abort(404)
        return send_file(BytesIO(row["content"]), download_name=row["name"], as_attachment=True,
                         mimetype="application/octet-stream")

    @api.get("/knowledge/<item>/media/<media>")
    @requires("knowledge.read")
    def read_media(item, media):
        file = knowledge.media(item, media)
        if not file:
            abort(404)
        if file["mime"] == DOWNLOAD_MIME:
            return send_file(BytesIO(file["content"]), mimetype=DOWNLOAD_MIME,
                             as_attachment=True, download_name=file["name"])
        return send_file(BytesIO(file["content"]), mimetype=file["mime"], conditional=True)

    @api.get("/inventory")
    @requires("inventory.read")
    def assets():
        today = now().date()
        items = inventory.assets(today, request.args.get("employee"), archived=request.args.get("archived") == "1")
        return jsonify(items=items, states=STATES, actions=ACTIONS,
                       today=today.isoformat(), service_warning_days=SERVICE_WARNING_DAYS, act_actions=ACT_OPERATIONS)

    @api.get("/inventory/assignees")
    @requires("inventory.write")
    def assignees():
        return jsonify(items=inventory.assignees())

    @api.get("/inventory/<item>")
    @requires("inventory.read")
    def asset(item):
        return jsonify(inventory.details(item, now().date()) or abort(404))

    @api.post("/inventory")
    @requires("inventory.write")
    def receive():
        photo = None
        if request.mimetype == "multipart/form-data":
            data = request.form
            upload = request.files.get("photo")
            if upload and upload.filename:
                photo = photo_content(upload)
        else:
            data = request_data()
        return jsonify(id=inventory.receive(data, now(), g.user, photo)), 201

    @api.post("/inventory/<item>/events")
    @requires("inventory.write")
    def move(item):
        event = inventory.move(item, request_data(), now(), g.user)
        return jsonify(id=item, event=event), 201

    @api.get("/inventory/<item>/events/<int:event>/act")
    @requires("inventory.read")
    def download_act(item, event):
        document = inventory.act(item, event)
        if not document:
            abort(404)
        return send_file(BytesIO(document), mimetype=ACT_MIME, as_attachment=True,
                         download_name=f"Акт-приёма-передачи-{event}.docx")

    @api.put("/inventory/<item>/service")
    @requires("inventory.write")
    def update_service(item):
        inventory.save_term(item, request_data())
        return jsonify(id=item)

    @api.get("/inventory/<item>/photo")
    @requires("inventory.read")
    def asset_photo(item):
        file = inventory.photo(item)
        if file is None:
            abort(404)
        return send_file(BytesIO(file), mimetype="image/webp")

    @api.put("/inventory/<item>/photo")
    @api.delete("/inventory/<item>/photo")
    @requires("inventory.write")
    def update_photo(item):
        photo = None
        if request.method == "PUT":
            upload = request.files.get("photo")
            if not upload or not upload.filename:
                raise InputError("Выберите фото устройства.")
            photo = photo_content(upload)
        if not inventory.save_photo(item, photo):
            abort(404)
        return jsonify(id=item)

    return api
