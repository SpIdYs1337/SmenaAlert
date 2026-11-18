from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import tomllib
from datetime import datetime
from zoneinfo import ZoneInfoNotFoundError

from .commands import Commands, incoming_command, whoami_text
from .config import MSK, ConfigError, Settings, load_env, read_config
from .domain import Shift, evaluate
from .http import Http
from .iiko import Iiko, IikoError
from .messenger import Messenger, MessengerError
from .monitor import Monitor
from .state import Store

log = logging.getLogger(__name__)


async def periodic(monitor: Monitor, stop: asyncio.Event, interval: int):
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        started = loop.time()
        try:
            await monitor.tick()
        except Exception as error:
            log.error("Ошибка цикла мониторинга: %s", type(error).__name__)
        delay = max(1, interval - (loop.time() - started))
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass


async def polling(messenger: Messenger, handler, stop: asyncio.Event, store: Store | None = None,
                  offset_key: str = "", *, interval: float = 5):
    offset = int(store.get_meta(offset_key, "0")) if store is not None else 0
    while not stop.is_set():
        try:
            for update in await messenger.updates(offset):
                if stop.is_set():
                    break
                if update["update_id"] < offset:
                    continue
                try:
                    await handler(update)
                except MessengerError as error:
                    log.warning("Ответ на команду не доставлен: %s", error)
                except Exception as error:
                    log.error("Ошибка команды: %s", type(error).__name__)
                offset = int(update["update_id"]) + 1
                if store is not None:
                    store.set_meta(offset_key, str(offset))
        except MessengerError as error:
            log.warning("Получение команд: %s", error)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass


def stop_event() -> asyncio.Event:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    return stop


async def messenger_identity(messenger: Messenger) -> tuple[str, str]:
    me = await messenger.call("GET", "self/get")
    if me.get("webhook_url"):
        raise ConfigError("У бота включён webhook. Для опроса сообщений отключите webhook или создайте отдельного бота.")
    bot_id, login = me.get("id"), me.get("login")
    if not isinstance(bot_id, str) or not bot_id or not isinstance(login, str) or not login:
        raise MessengerError("Яндекс Мессенджер вернул неполные данные о боте")
    return bot_id, login.lower()


async def setup(settings: Settings):
    messenger = Messenger(settings.token(), settings.chat_id, Http())
    bot_id, bot_login = await messenger_identity(messenger)
    stop = stop_event()

    async def whoami(update):
        command = incoming_command(update, bot_login)
        if command and command[0] == "/whoami":
            _, _, login, chat_id = command
            log.info("login=%s chat_id=%s", login, chat_id)
            await messenger.send(whoami_text(login, chat_id), chat_id)

    log.info("Отправьте /whoami в целевом чате, чтобы получить логин и ID чата. Выход: Ctrl+C.")
    store = Store(settings.state_path)
    try:
        await polling(messenger, whoami, stop, store, f"yandex_offset:{bot_id}")
    finally:
        store.close()


async def run(settings: Settings, mode: str, discover: str | None):
    http = Http()
    clients = {sid: Iiko(server, http) for sid, server in settings.servers.items()}
    # Validate all required credentials before starting either network loop.
    for server in settings.servers.values():
        server.credentials()
    try:
        if discover:
            if discover not in clients:
                raise ConfigError("Неизвестный код сервера")
            for kind in ("departments", "groups"):
                print(kind + ":")
                for row in await clients[discover].discover(kind):
                    print(json.dumps(row, ensure_ascii=False))
            return
        if mode == "probe":
            now = datetime.now(MSK)
            failed = False
            store = Store(":memory:")
            try:
                for outlet in settings.outlets:
                    try:
                        shifts = await clients[outlet.server].shifts(outlet, now)
                        result = evaluate(outlet, shifts, now, store, settings.at_deadline_is_on_time)
                        print(f"{outlet.id}: status={result.status}, shifts={len(shifts)}, opened_at={result.opened_at}")
                    except IikoError as error:
                        print(f"{outlet.id}: {error}")
                        failed = True
            finally:
                store.close()
            if failed:
                raise ConfigError("Проверка iiko завершилась с ошибками")
            return
        messenger = Messenger(settings.token(), settings.chat_id, http, settings.thread_id)
        store = Store(settings.state_path)
        try:
            bot_id, bot_login = await messenger_identity(messenger)
            chat = await messenger.call("GET", "chats/getChat/", params={"chat_id": settings.chat_id})
            data = chat.get("data")
            if not isinstance(data, dict) or data.get("type") != "group":
                raise ConfigError("Для оповещений укажите групповой чат Яндекс Мессенджера")
            monitor = Monitor(settings, store, clients, messenger)
            commands = Commands(settings, store, monitor, messenger, bot_login)
            stop = stop_event()
            log.info("Бот запущен; точек: %s; время: Europe/Moscow", len(settings.outlets))
            await asyncio.gather(periodic(monitor, stop, settings.interval),
                                 polling(messenger, commands.handle, stop, store, f"yandex_offset:{bot_id}"))
        finally:
            store.close()
    finally:
        await asyncio.gather(*(client.close() for client in clients.values()))


def demo(settings):
    store = Store(":memory:")
    try:
        outlet = settings.outlets[0]
        day = datetime(2026, 11, 18, 9, 5, tzinfo=MSK)
        store.set_schedule(outlet.id, ["wed"], "09:00")
        register = outlet.registers[0] if outlet.registers else 1
        examples = [("Нет смен", []),
                    ("Вчерашняя смена", [Shift(register, day.replace(day=17, hour=8))]),
                    ("Открыта вовремя", [Shift(r, day.replace(hour=8, minute=55)) for r in (outlet.registers or (register,))]),
                    ("Открыта с опозданием", [Shift(r, day.replace(minute=3)) for r in (outlet.registers or (register,))])]
        for label, shifts in examples:
            result = evaluate(outlet, shifts, day, store, settings.at_deadline_is_on_time)
            print(f"{label}: {result.status}")
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description="Контроль открытия iiko в Яндекс Мессенджере по МСК")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--env-file", default=".env")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--validate", action="store_true", help="Проверить конфигурацию без API")
    modes.add_argument("--demo", action="store_true", help="Показать расчёт на вымышленных данных без API")
    modes.add_argument("--probe", action="store_true", help="Проверить чтение смен из iiko без уведомлений")
    modes.add_argument("--discover", metavar="SERVER", help="Показать ID предприятий и групп из iiko")
    modes.add_argument("--setup", action="store_true", help="Получить логин и ID чата Яндекс Мессенджера")
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        load_env(args.env_file)
        settings = read_config(args.config)
        if args.validate:
            print(f"Конфигурация корректна: {len(settings.outlets)} точек, {len(settings.servers)} серверов. API не вызывался.")
        elif args.demo:
            demo(settings)
        elif args.setup:
            asyncio.run(setup(settings))
        else:
            asyncio.run(run(settings, "probe" if args.probe else "run", args.discover))
    except (ConfigError, IikoError, MessengerError) as error:
        log.error("%s", error)
        return 1
    except (OSError, tomllib.TOMLDecodeError, ZoneInfoNotFoundError, KeyError, TypeError, ValueError) as error:
        # ConfigError messages are authored locally; never dump upstream exceptions or env values.
        log.error("Ошибка конфигурации/файла (%s)", type(error).__name__)
        return 1
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        log.error("Ошибка запуска: %s", type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
