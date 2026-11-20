"""Authenticated HTTP endpoints for the knowledge base, inventory and administration."""
from __future__ import annotations

from functools import wraps
from io import BytesIO

from flask import Blueprint, abort, g, jsonify, request, send_file

from ..config import MSK
from .access import PERMISSIONS
from .inventory import ACTIONS, STATES, Inventory
from .knowledge import DOCUMENT_LIMIT, Knowledge
from .settings import InputError


def request_data():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise InputError("Не удалось прочитать данные формы.")
    return value


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
        return jsonify(items=knowledge.tree())

    @api.get("/knowledge/<item>")
    @requires("knowledge.read")
    def article(item):
        return jsonify(knowledge.get(item) or abort(404))

    @api.post("/knowledge")
    @api.put("/knowledge/<item>")
    @requires("knowledge.write")
    def article_save(item=None):
        saved = knowledge.save(request_data(), now().isoformat(timespec="seconds"), g.user["name"], item)
        return jsonify(id=saved), 200 if item else 201

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

    @api.get("/inventory")
    @requires("inventory.read")
    def assets():
        return jsonify(items=inventory.assets(), states=STATES, actions=ACTIONS, today=now().date().isoformat())

    @api.get("/inventory/assignees")
    @requires("inventory.write")
    def assignees():
        return jsonify(items=inventory.assignees())

    @api.get("/inventory/<item>")
    @requires("inventory.read")
    def asset(item):
        return jsonify(inventory.details(item) or abort(404))

    @api.post("/inventory")
    @requires("inventory.write")
    def receive():
        return jsonify(id=inventory.receive(request_data(), now(), g.user)), 201

    @api.post("/inventory/<item>/events")
    @requires("inventory.write")
    def move(item):
        inventory.move(item, request_data(), now(), g.user)
        return jsonify(id=item), 201

    return api
