"""An asset's state and holder are derived from its last recorded movement."""
from __future__ import annotations

from calendar import monthrange
from datetime import date

from .acts import ACT_OPERATIONS, act_fields, make_act
from .settings import InputError, identifier, number, text

ACTIONS = {"receive": "Приёмка", "commission": "Ввод в эксплуатацию", "assign": "Выдача сотруднику",
           "transfer": "Передача сотруднику", "return": "Возврат на склад", "relocate": "Перемещение",
           "write_off": "Списание"}
STATES = {"stock": "На складе", "in_use": "В эксплуатации", "written_off": "Списано"}

SERVICE_WARNING_DAYS = 30
SERVICE_MAX_MONTHS = 600
ASSET_FIELDS = """
    a.*,
    (SELECT day FROM asset_events WHERE asset=a.id AND state='in_use' ORDER BY id LIMIT 1) AS service_started_on,
    EXISTS(SELECT 1 FROM asset_photos WHERE asset=a.id) AS has_photo
"""


def service_months(data):
    return number(data.get("service_months"), "Срок эксплуатации, мес.", 1, SERVICE_MAX_MONTHS)


def service_term(row, today):
    result = dict(row)
    start = result["service_started_on"]
    months = result["service_months"]
    result.update(has_photo=bool(result["has_photo"]), service_expires_on=None,
                  service_status="unset" if start else "not_started")
    if start and months:
        day = date.fromisoformat(start)
        year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
        end = date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))
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
    return result


def event_day(data, now):
    try:
        day = date.fromisoformat(data["day"])
    except (TypeError, ValueError, KeyError):
        raise InputError("Укажите дату операции.") from None
    if day.isoformat() != data["day"] or day > now.date():
        raise InputError("Дата операции должна быть сегодняшней или более ранней.")
    return day.isoformat()


def record(conn, asset, action, state, employee, employee_name, day, now, actor, note, location):
    saved = conn.execute("""
        INSERT INTO asset_events(asset,action,state,employee,employee_name,day,recorded_at,actor,actor_name,note,location)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (asset, action, state, employee, employee_name, day, now.isoformat(timespec="seconds"),
          actor["id"], actor["name"], note, location))
    return saved.lastrowid


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
            return [service_term(row, today) for row in conn.execute(query + " ORDER BY a.tag_key", params)]

    def details(self, item, today):
        with self.db.connection() as conn:
            conn.execute("BEGIN")
            row = conn.execute(f"SELECT {ASSET_FIELDS} FROM assets a WHERE id=?", (item,)).fetchone()
            if not row:
                return None
            events = [dict(event) for event in conn.execute(
                "SELECT * FROM asset_events WHERE asset=? ORDER BY id DESC", (item,))]
            holder = conn.execute("SELECT name FROM employees WHERE id=?", (events[0]["employee"],)).fetchone()
        return {**service_term(row, today), "events": events, "version": events[0]["id"],
                "employee_name": holder["name"] if holder else events[0]["employee_name"]}

    def assignees(self):
        with self.db.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT id,name FROM employees WHERE active=1 ORDER BY name,id")]

    def receive(self, data, now, actor, photo=None):
        tag = text(data, "tag", "Инвентарный номер", limit=100)
        name = text(data, "name", "Наименование")
        serial = text(data, "serial", "Серийный номер", required=False, limit=120)
        location = text(data, "location", "Место приёмки", required=False, limit=160) or "Склад"
        note = text(data, "note", "Основание или комментарий", required=False, limit=500)
        day = event_day(data, now)
        fields = act_fields(data)
        item = identifier()
        with self.db.connection() as conn:
            conn.execute("INSERT INTO assets(id,tag,tag_key,name,serial) VALUES (?,?,?,?,?)",
                         (item, tag, tag.casefold(), name, serial))
            event = record(conn, item, "receive", "stock", None, "", day, now, actor, note, location)
            store_act(conn, item, event, fields)
            if photo is not None:
                conn.execute("INSERT INTO asset_photos VALUES (?,?)", (item, photo))
        return item

    def move(self, item, data, now, actor):
        version = number(data.get("version"), "Версия карточки", 1, 2**63-1)
        action = text(data, "action", "Операция")
        location = text(data, "location", "Куда перемещается техника", required=action == "relocate", limit=160)
        writing_off = action == "write_off"
        fields = act_fields(data) if action in ACT_OPERATIONS else None
        label = "Причина списания" if writing_off else "Основание или комментарий"
        note = text(data, "note", label, required=writing_off, limit=500)
        day = event_day(data, now)
        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            last = conn.execute("SELECT * FROM asset_events WHERE asset=? ORDER BY id DESC LIMIT 1", (item,)).fetchone()
            if not last or last["id"] != version:
                raise InputError("Состояние техники изменилось. Откройте карточку заново.")
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
            if action in {"assign", "transfer"}:
                employee_id = text(data, "employee", "Сотрудник")
                employee = conn.execute("SELECT name FROM employees WHERE id=? AND active=1", (employee_id,)).fetchone()
                if not employee or employee_id == last["employee"]:
                    raise InputError("Выберите другого действующего сотрудника.")
                employee_name = employee["name"]
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
