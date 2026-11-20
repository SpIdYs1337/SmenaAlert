"""An asset's state and holder are derived from its last recorded movement."""
from __future__ import annotations

from calendar import monthrange
from datetime import date

from .settings import InputError, identifier, number, text

ACTIONS = {"receive": "Приёмка", "commission": "Ввод в эксплуатацию", "assign": "Выдача сотруднику",
           "transfer": "Передача сотруднику", "return": "Возврат на склад", "relocate": "Перемещение"}
STATES = {"stock": "На складе", "in_use": "В эксплуатации"}

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
    conn.execute("""
        INSERT INTO asset_events(asset,action,state,employee,employee_name,day,recorded_at,actor,actor_name,note,location)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (asset, action, state, employee, employee_name, day, now.isoformat(timespec="seconds"),
          actor["id"], actor["name"], note, location))


class Inventory:
    def __init__(self, db):
        self.db = db

    def assets(self, today, employee=None):
        query = f"""
                SELECT {ASSET_FIELDS}, e.id AS version,e.day,e.state,e.employee,e.location,
                       COALESCE(holder.name,e.employee_name) AS employee_name,r.day AS received_on
                FROM assets a JOIN asset_events e ON e.id=(SELECT max(id) FROM asset_events WHERE asset=a.id)
                    JOIN asset_events r ON r.asset=a.id AND r.action='receive'
                    LEFT JOIN employees holder ON holder.id=e.employee
        """
        params = ()
        if employee is not None:
            query += " WHERE e.employee=?"
            params = (employee,)
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
        item = identifier()
        with self.db.connection() as conn:
            conn.execute("INSERT INTO assets(id,tag,tag_key,name,serial) VALUES (?,?,?,?,?)",
                         (item, tag, tag.casefold(), name, serial))
            record(conn, item, "receive", "stock", None, "", day, now, actor, note, location)
            if photo is not None:
                conn.execute("INSERT INTO asset_photos VALUES (?,?)", (item, photo))
        return item

    def move(self, item, data, now, actor):
        version = number(data.get("version"), "Версия карточки", 1, 2**63-1)
        action = text(data, "action", "Операция")
        location = text(data, "location", "Куда перемещается техника", required=action == "relocate", limit=160)
        note = text(data, "note", "Основание или комментарий", required=False, limit=500)
        day = event_day(data, now)
        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            last = conn.execute("SELECT * FROM asset_events WHERE asset=? ORDER BY id DESC LIMIT 1", (item,)).fetchone()
            if not last or last["id"] != version:
                raise InputError("Состояние техники изменилось. Откройте карточку заново.")
            if day < last["day"]:
                raise InputError("Дата должна быть не раньше предыдущей операции.")
            if action == "relocate":
                if location.casefold() == last["location"].casefold():
                    raise InputError("Укажите новое место устройства.")
                record(conn, item, action, last["state"], last["employee"], last["employee_name"],
                       day, now, actor, note, location)
                return
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
            record(conn, item, action, state, employee_id, employee_name, day, now, actor, note, destination)

    def save_term(self, item, data):
        months = service_months(data)
        version = number(data.get("service_revision"), "Версия срока", 0, 2**63-1)
        with self.db.connection() as conn:
            updated = conn.execute("""
                UPDATE assets SET service_months=?,service_revision=service_revision+1
                WHERE id=? AND service_revision=? AND EXISTS(
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
            if not conn.execute("SELECT 1 FROM assets WHERE id=?", (item,)).fetchone():
                return False
            if photo is None:
                conn.execute("DELETE FROM asset_photos WHERE asset=?", (item,))
            else:
                conn.execute("INSERT INTO asset_photos VALUES (?,?) ON CONFLICT(asset) DO UPDATE SET content=excluded.content",
                             (item, photo))
        return True
