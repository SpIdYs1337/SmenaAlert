"""Employee accounts and module access. The deployment administrator remains the recovery account."""
from __future__ import annotations

import json

from werkzeug.security import check_password_hash, generate_password_hash

from .settings import InputError, flag, identifier, number, text

PERMISSIONS = {
    "knowledge.read": "Читать базу знаний",
    "knowledge.write": "Редактировать и импортировать статьи",
    "inventory.read": "Просматривать IT-инвентарь",
    "inventory.write": "Принимать и выдавать технику",
    "monitor.manage": "Управлять мониторингом, подключениями и рассылкой",
    "admin.manage": "Администрировать панель и права сотрудников",
}
PUBLIC_COLUMNS = "id, name, login, grants, active, must_change, version"


def grants_for(value):
    if not isinstance(value, list) or any(not isinstance(p, str) or p not in PERMISSIONS for p in value):
        raise InputError("Проверьте список прав доступа.")
    grants = set(value)
    if "admin.manage" in grants:
        return sorted(PERMISSIONS)
    for module in ("knowledge", "inventory"):
        if f"{module}.write" in grants:
            grants.add(f"{module}.read")
    return sorted(grants)


def password_value(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 256 or not value.strip() or "\0" in value:
        raise InputError("Введите пароль длиной от 12 до 256 символов.")
    return value


def employee_record(row):
    return {**dict(row), "active": bool(row["active"]), "must_change": bool(row["must_change"]),
            "grants": json.loads(row["grants"])} if row else None


class Accounts:
    def __init__(self, db, owner_login):
        self.db = db
        self.owner = {"id": "@owner", "name": owner_login, "login": owner_login, "grants": list(PERMISSIONS),
                      "active": True, "must_change": False, "version": 1}

    def get(self, employee_id):
        if employee_id == self.owner["id"]:
            return self.owner
        with self.db.connection() as conn:
            return employee_record(conn.execute(
                f"SELECT {PUBLIC_COLUMNS} FROM employees WHERE id=?", (employee_id,)).fetchone())

    def all(self):
        with self.db.connection() as conn:
            return [employee_record(row) for row in conn.execute(f"SELECT {PUBLIC_COLUMNS} FROM employees ORDER BY name, id")]

    def authenticate(self, login, password, dummy_hash):
        with self.db.connection() as conn:
            row = conn.execute("SELECT id,password_hash,active,version FROM employees WHERE login_key=?",
                               (login.strip().casefold(),)).fetchone()
        valid = check_password_hash(row["password_hash"] if row else dummy_hash, password)
        if row and valid and row["active"]:
            return row["id"], row["version"]
        return None

    def save(self, data, actor, employee_id=None):
        name = text(data, "name", "Имя сотрудника")
        login = text(data, "login", "Логин", limit=120)
        if login.casefold() == self.owner["login"].casefold() or any(c.isspace() for c in login):
            raise InputError("Выберите другой логин без пробелов.")
        grants = grants_for(data.get("grants", []))
        active = flag(data, "active", True)
        if actor == employee_id and (not active or "admin.manage" not in grants):
            raise InputError("Для своей учётной записи сохраните доступ администратора.")
        version = number(data.get("version"), "Версия карточки", 1, 2**63-1) if employee_id else None
        password_hash = None
        if not employee_id or data.get("password"):
            password_hash = generate_password_hash(password_value(data.get("password")))
        fields = (name, login, login.casefold(), json.dumps(grants), active)
        with self.db.connection() as conn:
            if not employee_id:
                employee_id = identifier()
                conn.execute("INSERT INTO employees(id,name,login,login_key,grants,active,password_hash) "
                             "VALUES (?,?,?,?,?,?,?)", (employee_id, *fields, password_hash))
            else:
                cursor = conn.execute("""
                    UPDATE employees SET name=?,login=?,login_key=?,grants=?,active=?,
                        password_hash=COALESCE(?,password_hash),
                        must_change=CASE WHEN ? IS NULL THEN must_change ELSE 1 END, version=version+1
                    WHERE id=? AND version=?
                """, (*fields, password_hash, password_hash, employee_id, version))
                if not cursor.rowcount:
                    raise InputError("Карточка изменилась. Обновите её и повторите сохранение.")
        return employee_id

    def change_password(self, employee_id, version, old, new):
        password_value(new)
        if old == new:
            raise InputError("Новый пароль должен отличаться от прежнего.")
        with self.db.connection() as conn:
            row = conn.execute("SELECT password_hash FROM employees WHERE id=? AND active=1 AND version=?",
                               (employee_id, version)).fetchone()
            if not row or not check_password_hash(row[0], old):
                raise InputError("Проверьте текущий пароль.")
            updated = conn.execute("UPDATE employees SET password_hash=?,must_change=0,version=version+1 "
                                   "WHERE id=? AND version=? AND active=1",
                                   (generate_password_hash(new), employee_id, version))
            if not updated.rowcount:
                raise InputError("Учётная запись изменилась. Войдите заново.")
        return version + 1
