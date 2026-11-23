"""An asset's state and holder are derived from its last recorded movement."""
from __future__ import annotations

import json
from calendar import monthrange
from datetime import date
from itertools import takewhile

from .acts import ACT_OPERATIONS, act_fields, make_act
from .settings import InputError, identifier, number, text

ACTIONS = {"receive": "Приёмка", "commission": "Ввод в эксплуатацию", "assign": "Выдача сотруднику",
           "transfer": "Передача сотруднику", "return": "Возврат на склад", "relocate": "Перемещение",
           "write_off": "Списание"}
STATES = {"stock": "На складе", "in_use": "В эксплуатации", "written_off": "Списано"}

SERVICE_WARNING_DAYS = 30
SERVICE_MAX_MONTHS = 600
WARRANTY_MAX_MONTHS = 600
HOLDER_ACTIONS = {"assign", "transfer"}
EDIT_CONFLICT = "Данные техники изменились. Откройте карточку заново."
ASSET_FIELDS = """
    a.*,
    (SELECT day FROM asset_events WHERE asset=a.id AND state='in_use' ORDER BY id LIMIT 1) AS service_started_on,
    EXISTS(SELECT 1 FROM asset_photos WHERE asset=a.id) AS has_photo
"""


def service_months(data):
    return number(data.get("service_months"), "Срок эксплуатации, мес.", 1, SERVICE_MAX_MONTHS)


def term_end(start, months):
    day = date.fromisoformat(start)
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def asset_data(row, today):
    result = dict(row)
    start = result["service_started_on"]
    months = result["service_months"]
    result.update(has_photo=bool(result["has_photo"]), service_expires_on=None,
                  service_status="unset" if start else "not_started")
    if start and months:
        end = term_end(start, months)
        remaining = (end - today).days
        if remaining < 0:
            status = "expired"
        elif remaining == 0:
            status = "today"
        elif remaining <= SERVICE_WARNING_DAYS:
            status = "soon"
        else:
            status = "valid"
        result.update(service_expires_on=end.isoformat(), service_status=status)
    purchased, warranty = result["purchased_on"], result["warranty_months"]
    end = term_end(purchased, warranty) if purchased and warranty else None
    result.update(warranty_expires_on=end.isoformat() if end else None, warranty_expired=bool(end and end < today))
    return result


def asset_fields(data, today):
    purchased = data.get("purchased_on")
    if purchased not in (None, ""):
        try:
            day = date.fromisoformat(purchased)
        except (TypeError, ValueError):
            raise InputError("Укажите корректную дату покупки.") from None
        if day.isoformat() != purchased or day > today:
            raise InputError("Дата покупки должна быть сегодняшней или более ранней.")
    months = data.get("warranty_months")
    if months in (None, ""):
        months = None
    else:
        if isinstance(months, str) and len(months) <= 3 and months.isascii() and months.isdecimal():
            months = int(months)
        months = number(months, "Срок гарантии, мес.", 0, WARRANTY_MAX_MONTHS)
    tag = text(data, "tag", "Инвентарный номер", limit=100)
    name = text(data, "name", "Наименование")
    serial = text(data, "serial", "Серийный номер", required=False, limit=120)
    return tag, tag.casefold(), name, serial, purchased or None, months


def event_day(data, now):
    try:
        day = date.fromisoformat(data["day"])
    except (TypeError, ValueError, KeyError):
        raise InputError("Укажите дату операции.") from None
    if day.isoformat() != data["day"] or day > now.date():
        raise InputError("Дата операции должна быть сегодняшней или более ранней.")
    return day.isoformat()


def event_note(data, action):
    writing_off = action == "write_off"
    return text(data, "note", "Причина списания" if writing_off else "Основание или комментарий",
                required=writing_off, limit=500)


def latest_event(conn, item, data, *, editing=False):
    version = number(data.get("version"), "Версия карточки", 1, 2**63-1)
    revision = None
    if editing or "edit_revision" in data:
        revision = number(data.get("edit_revision"), "Версия исправлений", 0, 2**63-1)
    last = conn.execute("""
        SELECT e.*,a.edit_revision FROM asset_events e JOIN assets a ON a.id=e.asset
        WHERE e.asset=? ORDER BY e.id DESC LIMIT 1
    """, (item,)).fetchone()
    if not last or last["id"] != version or (revision is not None and last["edit_revision"] != revision):
        raise InputError(EDIT_CONFLICT)
    return last


def active_holder(conn, employee_id, previous):
    employee = conn.execute("SELECT name FROM employees WHERE id=? AND active=1", (employee_id,)).fetchone()
    if not employee or employee_id == previous:
        raise InputError("Выберите другого действующего сотрудника.")
    return employee["name"]


def record(conn, asset, action, state, employee, employee_name, day, now, actor, note, location):
    saved = conn.execute("""
        INSERT INTO asset_events(asset,action,state,employee,employee_name,day,recorded_at,actor,actor_name,note,location)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (asset, action, state, employee, employee_name, day, now.isoformat(timespec="seconds"),
          actor["id"], actor["name"], note, location))
    return saved.lastrowid


def correct_event(conn, event, changes, now, actor):
    previous = {key: event[key] for key, value in changes.items() if event[key] != value}
    if not previous:
        return False
    conn.execute("""
        INSERT INTO asset_event_edits(event,previous,edited_at,editor,editor_name) VALUES (?,?,?,?,?)
    """, (event["id"], json.dumps(previous, ensure_ascii=False), now.isoformat(timespec="seconds"),
          actor["id"], actor["name"]))
    conn.execute("""
        UPDATE asset_events SET day=:day,location=:location,note=:note,employee=:employee,employee_name=:employee_name
        WHERE id=:id
    """, dict(event) | changes)
    return True


def act_operation(conn, item, event):
    return conn.execute("""
        SELECT e.id,e.day,e.action,e.actor_name,e.employee_name,e.location,a.name,a.tag,a.serial,act.content,
               (SELECT employee_name FROM asset_events
                WHERE asset=e.asset AND id<e.id ORDER BY id DESC LIMIT 1) AS previous_holder
        FROM asset_events e JOIN assets a ON a.id=e.asset
        LEFT JOIN asset_acts act ON act.event=e.id
        WHERE e.asset=? AND e.id=?
    """, (item, event)).fetchone()


def store_act(conn, item, event, fields):
    data = act_operation(conn, item, event)
    conn.execute("INSERT INTO asset_acts(event,content) VALUES (?,?)", (event, make_act(data, fields)))


class Inventory:
    def __init__(self, db):
        self.db = db

    def assets(self, today, employee=None, *, archived=False):
        query = f"""
                SELECT {ASSET_FIELDS}, e.id AS version,e.day,e.state,e.employee,e.location,e.note,
                       COALESCE(holder.name,e.employee_name) AS employee_name,r.day AS received_on
                FROM assets a JOIN asset_events e ON e.id=(SELECT max(id) FROM asset_events WHERE asset=a.id)
                    JOIN asset_events r ON r.asset=a.id AND r.action='receive'
                    LEFT JOIN employees holder ON holder.id=e.employee
                WHERE (e.state='written_off')=?
        """
        params = (archived,)
        if employee is not None:
            query += " AND e.employee=?"
            params += (employee,)
        with self.db.connection() as conn:
            return [asset_data(row, today) for row in conn.execute(query + " ORDER BY a.tag_key", params)]

    def details(self, item, today):
        with self.db.connection() as conn:
            conn.execute("BEGIN")
            row = conn.execute(f"SELECT {ASSET_FIELDS} FROM assets a WHERE id=?", (item,)).fetchone()
            if not row:
                return None
            events = [dict(event) for event in conn.execute("""
                SELECT e.*,edit.edited_at,edit.editor_name,
                       EXISTS(SELECT 1 FROM asset_acts WHERE event=e.id) AS has_saved_act
                FROM asset_events e LEFT JOIN asset_event_edits edit ON edit.id=(
                    SELECT max(id) FROM asset_event_edits WHERE event=e.id
                ) WHERE e.asset=? ORDER BY e.id DESC
            """, (item,))]
            holder = conn.execute("SELECT name FROM employees WHERE id=?", (events[0]["employee"],)).fetchone()
        return {**asset_data(row, today), "events": events, "version": events[0]["id"],
                "employee_name": holder["name"] if holder else events[0]["employee_name"]}

    def assignees(self):
        with self.db.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT id,name FROM employees WHERE active=1 ORDER BY name,id")]

    def receive(self, data, now, actor, photo=None):
        details = asset_fields(data, now.date())
        location = text(data, "location", "Место приёмки", required=False, limit=160) or "Склад"
        note = event_note(data, "receive")
        day = event_day(data, now)
        fields = act_fields(data)
        item = identifier()
        with self.db.connection() as conn:
            conn.execute("INSERT INTO assets(id,tag,tag_key,name,serial,purchased_on,warranty_months) VALUES (?,?,?,?,?,?,?)",
                         (item, *details))
            event = record(conn, item, "receive", "stock", None, "", day, now, actor, note, location)
            store_act(conn, item, event, fields)
            if photo is not None:
                conn.execute("INSERT INTO asset_photos VALUES (?,?)", (item, photo))
        return item

    def edit_asset(self, item, data, today):
        details = asset_fields(data, today)
        revision = number(data.get("edit_revision"), "Версия исправлений", 0, 2**63-1)
        with self.db.connection() as conn:
            updated = conn.execute("""
                UPDATE assets SET tag=?,tag_key=?,name=?,serial=?,purchased_on=?,warranty_months=?,
                                  edit_revision=edit_revision+1
                WHERE id=? AND edit_revision=?
            """, (*details, item, revision))
            if not updated.rowcount:
                if not conn.execute("SELECT 1 FROM assets WHERE id=?", (item,)).fetchone():
                    return False
                raise InputError(EDIT_CONFLICT)
        return True

    def move(self, item, data, now, actor):
        action = text(data, "action", "Операция")
        location = text(data, "location", "Куда перемещается техника", required=action == "relocate", limit=160)
        writing_off = action == "write_off"
        fields = act_fields(data) if action in ACT_OPERATIONS else None
        note = event_note(data, action)
        day = event_day(data, now)
        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            last = latest_event(conn, item, data)
            if last["state"] == "written_off":
                raise InputError("Устройство уже списано.")
            if day < last["day"]:
                raise InputError("Дата должна быть не раньше предыдущей операции.")
            if writing_off:
                return record(conn, item, action, "written_off", None, "", day, now, actor, note, last["location"])
            if action == "relocate":
                if location.casefold() == last["location"].casefold():
                    raise InputError("Укажите новое место устройства.")
                return record(conn, item, action, last["state"], last["employee"], last["employee_name"],
                              day, now, actor, note, location)
            if last["state"] == "stock":
                allowed = {"assign", "commission"}
            elif last["employee"] is None:
                allowed = {"assign", "return"}
            else:
                allowed = {"transfer", "return"}
            if action not in allowed:
                raise InputError("Такая операция недоступна в текущем состоянии.")
            employee_id, employee_name = None, ""
            if action in HOLDER_ACTIONS:
                employee_id = text(data, "employee", "Сотрудник")
                employee_name = active_holder(conn, employee_id, last["employee"])
            state = "stock" if action == "return" else "in_use"
            if state == "in_use" and not conn.execute(
                "SELECT 1 FROM asset_events WHERE asset=? AND state='in_use' LIMIT 1", (item,),
            ).fetchone():
                conn.execute("UPDATE assets SET service_months=?, service_revision=service_revision+1 WHERE id=?",
                             (service_months(data), item))
            destination = location or ("Склад" if action == "return" else last["location"])
            event = record(conn, item, action, state, employee_id, employee_name, day, now, actor, note, destination)
            if fields is not None:
                store_act(conn, item, event, fields)
            return event

    def edit_event(self, item, event_id, data, now, actor):
        if not 1 <= event_id <= 2**63 - 1:
            return False
        day = event_day(data, now)
        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            events = conn.execute("SELECT * FROM asset_events WHERE asset=? ORDER BY id", (item,)).fetchall()
            index = next((i for i, row in enumerate(events) if row["id"] == event_id), None)
            if index is None:
                return False
            latest_event(conn, item, data, editing=True)
            event = events[index]
            previous = events[index - 1] if index else None
            following = events[index + 1:]
            if (previous and day < previous["day"]) or (following and day > following[0]["day"]):
                raise InputError("Дата должна попадать между соседними операциями.")

            action = event["action"]
            changes = {"day": day}
            if "note" in data:
                changes["note"] = event_note(data, action)
            if action != "write_off" and "location" in data:
                location = text(data, "location", "Место устройства", required=False)
                if action == "relocate" and location != event["location"]:
                    if not location or location.casefold() == previous["location"].casefold():
                        raise InputError("Укажите новое место устройства.")
                changes["location"] = location

            # Relocations inherit the holder, and a write-off inherits the last location.
            derived = []
            if action in HOLDER_ACTIONS and "employee" in data:
                employee = text(data, "employee", "Сотрудник")
                if employee != event["employee"]:
                    holder = active_holder(conn, employee, previous["employee"])
                    relocations = list(takewhile(lambda row: row["action"] == "relocate", following))
                    next_movement = following[len(relocations):]
                    if next_movement and next_movement[0]["action"] == "transfer" and next_movement[0]["employee"] == employee:
                        raise InputError("В следующей передаче должен быть другой получатель. Сначала уточните её получателя.")
                    changes.update(employee=employee, employee_name=holder)
                    derived.extend((row, {"employee": employee, "employee_name": holder}) for row in relocations)
            if "location" in changes and following and following[0]["action"] == "write_off":
                derived.append((following[0], {"location": changes["location"]}))

            if correct_event(conn, event, changes, now, actor):
                for row, updates in derived:
                    correct_event(conn, row, updates, now, actor)
                conn.execute("UPDATE assets SET edit_revision=edit_revision+1 WHERE id=?", (item,))
        return True

    def act(self, item, event):
        if not 1 <= event <= 2**63 - 1:
            return None
        with self.db.connection() as conn:
            operation = act_operation(conn, item, event)
        if operation is None or operation["action"] not in ACT_OPERATIONS:
            return None
        return operation["content"] or make_act(operation)

    def save_term(self, item, data):
        months = service_months(data)
        version = number(data.get("service_revision"), "Версия срока", 0, 2**63-1)
        with self.db.connection() as conn:
            updated = conn.execute("""
                UPDATE assets SET service_months=?,service_revision=service_revision+1
                WHERE id=? AND service_revision=?
                    AND (SELECT state FROM asset_events WHERE asset=assets.id ORDER BY id DESC LIMIT 1)<>'written_off'
                    AND EXISTS(
                    SELECT 1 FROM asset_events WHERE asset=assets.id AND state='in_use')
            """, (months, item, version))
            if not updated.rowcount:
                raise InputError("Срок можно изменить после ввода в эксплуатацию. Обновите карточку.")

    def photo(self, item):
        with self.db.connection() as conn:
            row = conn.execute("SELECT content FROM asset_photos WHERE asset=?", (item,)).fetchone()
        return row["content"] if row else None

    def save_photo(self, item, photo):
        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT state FROM asset_events WHERE asset=? ORDER BY id DESC LIMIT 1",
                                   (item,)).fetchone()
            if not current:
                return False
            if current["state"] == "written_off":
                raise InputError("Устройство уже списано.")
            if photo is None:
                conn.execute("DELETE FROM asset_photos WHERE asset=?", (item,))
            else:
                conn.execute("INSERT INTO asset_photos VALUES (?,?) ON CONFLICT(asset) DO UPDATE SET content=excluded.content",
                             (item, photo))
        return True
