from __future__ import annotations

import argparse
import fcntl
import logging
import os
import signal
from pathlib import Path
from tempfile import TemporaryDirectory

from waitress import serve

from ..config import ConfigError, load_env
from .app import create_app
from .knowledge import DOCUMENT_LIMIT
from .demo import DemoIiko, DemoMail, demo_now, seed
from .service import Service
from .settings import Vault
from .storage import Database


def interrupt_server(_signum, _frame):
    raise KeyboardInterrupt


def start(directory: Path, args, admin: str, password: str):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "panel.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConfigError("Панель уже запущена с этим каталогом данных.") from None
        db, vault = Database(directory / "panel.sqlite3"), Vault(directory / ".panel-key")
        if args.demo:
            mail = DemoMail()
            service = Service(db, vault, mailer=mail, mailbox=mail, iiko_factory=DemoIiko, now=demo_now)
            seed(service)
        else:
            service = Service(db, vault)
        app = create_app(service, admin=admin, password=password, demo=args.demo,
                         secure_cookie=not args.demo and os.environ.get("PANEL_SECURE_COOKIE") == "1")
        service.start()
        previous_handler = signal.signal(signal.SIGTERM, interrupt_server)
        try:
            serve(app, host=args.host, port=args.port, threads=8, channel_timeout=60,
                  max_request_body_size=DOCUMENT_LIMIT + 128 * 1024, ident="shift-panel")
        except KeyboardInterrupt:
            pass
        finally:
            signal.signal(signal.SIGTERM, previous_handler)
            service.close()


def main():
    parser = argparse.ArgumentParser(description="Веб-панель iiko с SMTP и IMAP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--env-file", default=".env.web")
    parser.add_argument("--demo", action="store_true", help="Временные данные и имитация iiko, SMTP и IMAP")
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.demo:
            with TemporaryDirectory(prefix="shift-panel-demo-") as directory:
                start(Path(directory), args, "Администратор", "demo-preview")
        else:
            load_env(args.env_file)
            admin = os.environ.get("PANEL_ADMIN_LOGIN", "admin")
            password = os.environ.get("PANEL_ADMIN_PASSWORD", "")
            if len(password) < 12 or password == "replace-with-a-strong-password":
                raise ConfigError("Задайте PANEL_ADMIN_PASSWORD длиной от 12 символов в .env.web.")
            start(Path(args.data_dir), args, admin, password)
    except ConfigError as error:
        logging.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
