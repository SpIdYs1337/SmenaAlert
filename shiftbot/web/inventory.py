"""An asset's state and holder are derived from its last recorded movement."""
from __future__ import annotations

from datetime import date

from .settings import InputError, identifier, number, text

ACTIONS = {"receive": "Приёмка", "commission": "Ввод в эксплуатацию", "assign": "Выдача сотруднику",
           "transfer": "Передача сотруднику", "return": "Возврат на склад"}
STATES = {"stock": "На складе", "in_use": "В эксплуатации"}


def event_day(data, now):
    try:
        day = date.fromisoformat(data["day"])
    except (TypeError, ValueError, KeyError):
        raise InputError("Укажите дату операции.") from None
    if day.isoformat() != data["day"] or day > now.date():
        raise InputError("Дата операции должна быть сегодняшней или более ранней.")
    return day.isoformat()


def record(conn, asset, action, state, employee, employee_name, day, now, actor, note):
    conn.execute("""
        INSERT INTO asset_events(asset,action,state,employee,employee_name,day,recorded_at,actor,actor_name,note)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (asset, action, state, employee, employee_name, day, now.isoformat(timespec="seconds"),
          actor["id"], actor["name"], note))


class Inventory:
    def __init__(self, db):
        self.db = db

    def assets(self, employee=None):
        query = """
                SELECT a.*,e.id AS version,e.day,e.state,e.employee,e.employee_name,r.day AS received_on
                FROM assets a JOIN asset_events e ON e.id=(SELECT max(id) FROM asset_events WHERE asset=a.id)
                    JOIN asset_events r ON r.asset=a.id AND r.action='receive'
        """
        params = ()
        if employee is not None:
            query += " WHERE e.employee=?"
            params = (employee,)
        with self.db.connection() as conn:
            return [dict(row) for row in conn.execute(query + " ORDER BY a.tag_key", params)]

    def details(self, item):
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM assets WHERE id=?", (item,)).fetchone()
            if not row:
                return None
            events = [dict(event) for event in conn.execute(
                "SELECT * FROM asset_events WHERE asset=? ORDER BY id DESC", (item,))]
        return {**dict(row), "events": events, "version": events[0]["id"]}

    def assignees(self):
        with self.db.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT id,name FROM employees WHERE active=1 ORDER BY name,id")]

    def receive(self, data, now, actor):
        tag = text(data, "tag", "Инвентарный номер", limit=100)
        name = text(data, "name", "Наименование")
        serial = text(data, "serial", "Серийный номер", required=False, limit=120)
        note = text(data, "note", "Основание или комментарий", required=False, limit=500)
        day = event_day(data, now)
        item = identifier()
        with self.db.connection() as conn:
            conn.execute("INSERT INTO assets VALUES (?,?,?,?,?)", (item, tag, tag.casefold(), name, serial))
            record(conn, item, "receive", "stock", None, "", day, now, actor, note)
        return item

    def move(self, item, data, now, actor):
        version = number(data.get("version"), "Версия карточки", 1, 2**63-1)
        action = text(data, "action", "Операция")
        note = text(data, "note", "Основание или комментарий", required=False, limit=500)
        day = event_day(data, now)
        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            last = conn.execute("SELECT * FROM asset_events WHERE asset=? ORDER BY id DESC LIMIT 1", (item,)).fetchone()
            if not last or last["id"] != version:
                raise InputError("Состояние техники изменилось. Откройте карточку заново.")
            if day < last["day"]:
                raise InputError("Дата должна быть не раньше предыдущей операции.")
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
            record(conn, item, action, state, employee_id, employee_name, day, now, actor, note)
