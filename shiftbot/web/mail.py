from __future__ import annotations

import imaplib
import smtplib
import ssl
from contextlib import contextmanager, suppress
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid


class MailError(Exception):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class Mailer:
    @contextmanager
    def connect(self, settings: dict, password: str):
        connection = None
        context = ssl.create_default_context()
        try:
            if settings["security"] == "ssl":
                connection = smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=12, context=context)
            else:
                connection = smtplib.SMTP(settings["host"], settings["port"], timeout=12)
                connection.ehlo()
                connection.starttls(context=context)
            connection.ehlo()
            if settings["login"]:
                connection.login(settings["login"], password)
            yield connection
        except smtplib.SMTPAuthenticationError:
            raise MailError("SMTP отклонил логин или пароль приложения.") from None
        except smtplib.SMTPRecipientsRefused as error:
            temporary = any(type(reply[0]) is int and 400 <= reply[0] < 500 for reply in error.recipients.values())
            raise MailError("SMTP отклонил адрес получателя.", retryable=temporary) from None
        except smtplib.SMTPSenderRefused as error:
            raise MailError("У служебной почты нет права отправлять с этого адреса.",
                            retryable=400 <= error.smtp_code < 500) from None
        except ssl.SSLError:
            raise MailError("Проверьте тип шифрования, порт и сертификат SMTP.") from None
        except smtplib.SMTPNotSupportedError:
            raise MailError("Сервер SMTP не поддерживает выбранный режим TLS или авторизацию.") from None
        except smtplib.SMTPResponseException as error:
            raise MailError(f"Сервер SMTP вернул код {error.smtp_code}.",
                            retryable=400 <= error.smtp_code < 500) from None
        except (smtplib.SMTPException, OSError):
            raise MailError("Нет соединения с SMTP. Проверьте адрес, порт и сеть.", retryable=True) from None
        finally:
            if connection:
                # Once DATA has been accepted, a disconnected QUIT does not undo acceptance.
                try:
                    connection.quit()
                except (OSError, smtplib.SMTPException):
                    connection.close()

    def probe(self, settings: dict, password: str):
        with self.connect(settings, password):
            return

    def send(self, settings: dict, password: str, recipient: str, subject: str, body: str, *, key: int) -> str:
        message = EmailMessage()
        sender = Address(addr_spec=settings["sender"])
        message["From"] = Address(settings["sender_name"], sender.username, sender.domain)
        message["To"] = Address(addr_spec=recipient)
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = make_msgid(idstring=f"shift-{key}", domain=sender.domain)
        message.set_content(body)
        with self.connect(settings, password) as connection:
            connection.send_message(message)
        return str(message["Message-ID"])


def imap_quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Mailbox:
    @contextmanager
    def inbox(self, settings: dict, password: str):
        connection = None
        context = ssl.create_default_context()
        try:
            if settings["security"] == "ssl":
                connection = imaplib.IMAP4_SSL(settings["host"], settings["port"], ssl_context=context, timeout=12)
            else:
                connection = imaplib.IMAP4(settings["host"], settings["port"], timeout=12)
                try:
                    connection.starttls(ssl_context=context)
                except imaplib.IMAP4.error:
                    raise MailError("Не удалось включить STARTTLS на IMAP-сервере.") from None

            login = settings["login"]
            try:
                if "AUTH=PLAIN" in connection.capabilities:
                    connection.authenticate("PLAIN", lambda _: f"\0{login}\0{password}".encode())
                else:
                    if any(character in password for character in "\r\n\0"):
                        raise MailError("Укажите пароль приложения IMAP.")
                    connection.login(imap_quoted(login), password)
            except imaplib.IMAP4.abort:
                raise
            except (imaplib.IMAP4.error, UnicodeError):
                raise MailError("IMAP отклонил вход. Проверьте логин и пароль приложения.") from None

            status, _ = connection.select("INBOX", readonly=True)
            if status != "OK":
                raise MailError("Папка «Входящие» недоступна по IMAP.")
            yield connection
        except ssl.SSLError:
            raise MailError("Проверьте тип шифрования, порт и сертификат IMAP.") from None
        except (imaplib.IMAP4.abort, OSError):
            raise MailError("Нет соединения с IMAP. Проверьте адрес, порт и сеть.") from None
        except imaplib.IMAP4.error:
            raise MailError("IMAP отклонил запрос к папке «Входящие».") from None
        finally:
            if connection is not None:
                try:
                    connection.logout()
                except (imaplib.IMAP4.error, OSError):
                    with suppress(imaplib.IMAP4.error, OSError):
                        connection.shutdown()

    def probe(self, settings: dict, password: str):
        with self.inbox(settings, password):
            return

    def contains(self, settings: dict, password: str, message_id: str) -> bool:
        if any(character in message_id for character in "\r\n\0"):
            raise MailError("У тестового письма некорректный идентификатор.")
        with self.inbox(settings, password) as connection:
            status, matches = connection.search(None, "HEADER", "Message-ID", imap_quoted(message_id))
            if status != "OK":
                raise MailError("IMAP не смог выполнить поиск во входящих.")
            return bool(matches and matches[0] and matches[0].split())
