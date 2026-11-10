"""Чтение и отправка почты по IMAP/SMTP. Адрес и пароль — из дашборда."""

from __future__ import annotations

import imaplib
import re
import smtplib
from email import message_from_bytes
from email.header import decode_header
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

from .. import config
from ..desk_settings import integer, text

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_LIST = 20
_MAX_BODY = 20_000
_MAX_ATTACHMENT = 20_000_000
_MAX_SEND = 50_000


def _cfg() -> dict:
    host = text("mail_imap_host", env=("MAIL_IMAP_HOST",))
    user = text("mail_username", env=("MAIL_USERNAME",))
    password = text("mail_password", env=("MAIL_PASSWORD",))
    if not host or not user or not password:
        raise ValueError("Почта не настроена. Укажите сервер, ящик и пароль во вкладке «Агент» дашборда.")
    security = text("mail_imap_security", "ssl", ("MAIL_IMAP_SECURITY",)).lower()
    if security not in {"ssl", "starttls", "plain"}:
        security = "ssl"
    smtp_security = text("mail_smtp_security", "starttls", ("MAIL_SMTP_SECURITY",)).lower()
    if smtp_security not in {"ssl", "starttls", "plain"}:
        smtp_security = "starttls"
    return {
        "imap_host": host,
        "imap_port": integer("mail_imap_port", 993, ("MAIL_IMAP_PORT",)),
        "imap_security": security,
        "smtp_host": text("mail_smtp_host", host, ("MAIL_SMTP_HOST",)),
        "smtp_port": integer("mail_smtp_port", 587, ("MAIL_SMTP_PORT",)),
        "smtp_security": smtp_security,
        "user": user,
        "password": password,
        "sender": text("mail_from", user, ("MAIL_FROM",)),
    }


def _imap(cfg: dict):
    if cfg["imap_security"] == "ssl":
        client = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"], timeout=30)
    else:
        client = imaplib.IMAP4(cfg["imap_host"], cfg["imap_port"], timeout=30)
        if cfg["imap_security"] == "starttls":
            client.starttls()
    client.login(cfg["user"], cfg["password"])
    return client


def check_mailbox() -> dict:
    cfg = _cfg()
    client = _imap(cfg)
    try:
        client.logout()
    except Exception:
        pass
    return {"ok": True, "detail": f"Ящик {cfg['user']} отвечает."}


def _folder(name: str) -> str:
    folder = (name or "INBOX").strip() or "INBOX"
    if any(ch in folder for ch in "\r\n\x00"):
        raise ValueError("Недопустимое имя папки.")
    return folder[:200]


def _uid(value: str) -> str:
    uid = (value or "").strip()
    if not uid.isdigit():
        raise ValueError("uid письма должен быть числом из списка писем.")
    return uid


def _header(value: str | None) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for text, encoding in decode_header(value):
        if isinstance(text, bytes):
            parts.append(text.decode(encoding or "utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts).strip()


def _body(payload: bytes | None, charset: str | None) -> str:
    if not payload:
        return ""
    return payload.decode(charset or "utf-8", errors="replace")


def _plain(message) -> tuple[str, list[dict]]:
    text = ""
    html = ""
    files: list[dict] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition = str(part.get("Content-Disposition") or "")
            filename = part.get_filename()
            if filename or "attachment" in disposition.lower():
                name = _header(filename) or "attachment"
                data = part.get_payload(decode=True) or b""
                files.append({"filename": name, "size": len(data), "data": data})
                continue
            content = _body(part.get_payload(decode=True), part.get_content_charset())
            if part.get_content_type() == "text/plain" and not text:
                text = content
            elif part.get_content_type() == "text/html" and not html:
                html = content
    else:
        content = _body(message.get_payload(decode=True), message.get_content_charset())
        if message.get_content_type() == "text/html":
            html = content
        else:
            text = content
    if not text and html:
        from bs4 import BeautifulSoup

        text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    return text.strip()[:_MAX_BODY], files


def _safe_name(name: str) -> str:
    base = Path(name).name.strip() or "attachment"
    cleaned = re.sub(r"[^\w.\- ()]+", "_", base, flags=re.UNICODE)[:120].strip("._")
    return cleaned or "attachment"


def _save_files(uid: str, files: list[dict]) -> list[dict]:
    saved = []
    root = config.DATA_ROOT / "uploads" / "mail" / uid
    for item in files:
        if item["size"] > _MAX_ATTACHMENT:
            saved.append({"filename": item["filename"], "size": item["size"], "skipped": "слишком большой"})
            continue
        root.mkdir(parents=True, exist_ok=True)
        path = root / _safe_name(item["filename"])
        path.write_bytes(item["data"])
        saved.append({
            "filename": item["filename"],
            "size": item["size"],
            "path": path.relative_to(config.DATA_ROOT).as_posix(),
        })
    return saved


def list_messages(folder: str = "INBOX", limit: int = 10, unread_only: bool = False) -> dict:
    folder = _folder(folder)
    limit = max(1, min(int(limit), _MAX_LIST))
    cfg = _cfg()
    client = _imap(cfg)
    try:
        status, _ = client.select(folder, readonly=True)
        if status != "OK":
            raise ValueError(f"Папка «{folder}» не открылась.")
        criterion = "UNSEEN" if unread_only else "ALL"
        status, data = client.uid("SEARCH", None, criterion)
        if status != "OK" or not data or not data[0]:
            return {"folder": folder, "messages": []}
        uids = data[0].split()[-limit:]
        messages = []
        for raw_uid in reversed(uids):
            uid = raw_uid.decode()
            status, fetched = client.uid("FETCH", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if status != "OK" or not fetched:
                continue
            header = b""
            flags = ""
            for piece in fetched:
                if isinstance(piece, tuple):
                    flags = piece[0].decode(errors="replace")
                    header = piece[1]
            parsed = message_from_bytes(header + b"\r\n\r\n")
            messages.append({
                "uid": uid,
                "from": _header(parsed.get("From")),
                "subject": _header(parsed.get("Subject")),
                "date": _header(parsed.get("Date")),
                "seen": "\\Seen" in flags,
            })
        return {"folder": folder, "messages": messages}
    finally:
        try:
            client.logout()
        except Exception:
            pass


def read_message(uid: str, folder: str = "INBOX", save_attachments: bool = True) -> dict:
    folder = _folder(folder)
    uid = _uid(uid)
    cfg = _cfg()
    client = _imap(cfg)
    try:
        status, _ = client.select(folder, readonly=True)
        if status != "OK":
            raise ValueError(f"Папка «{folder}» не открылась.")
        status, fetched = client.uid("FETCH", uid, "(RFC822)")
        if status != "OK" or not fetched:
            raise ValueError(f"Письмо {uid} не найдено.")
        raw = next((piece[1] for piece in fetched if isinstance(piece, tuple)), None)
        if not raw:
            raise ValueError(f"Письмо {uid} не найдено.")
        message = message_from_bytes(raw)
        body, files = _plain(message)
        attachments = _save_files(uid, files) if save_attachments else [
            {"filename": item["filename"], "size": item["size"]} for item in files
        ]
        return {
            "uid": uid,
            "folder": folder,
            "from": _header(message.get("From")),
            "to": _header(message.get("To")),
            "date": _header(message.get("Date")),
            "subject": _header(message.get("Subject")),
            "body": body,
            "attachments": attachments,
        }
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _addresses(values: list[str], label: str) -> list[str]:
    found = []
    for item in values:
        for part in str(item).replace(";", ",").split(","):
            _, address = parseaddr(part.strip())
            address = address or part.strip()
            if not _EMAIL.match(address):
                raise ValueError(f"Некорректный адрес в поле «{label}»: {part.strip()[:80]}")
            found.append(address)
    if not found and label == "кому":
        raise ValueError("Укажите хотя бы одного получателя.")
    if len(found) > 20:
        raise ValueError("Слишком много получателей (максимум 20).")
    return found


def send_message(to: list[str], subject: str, body: str, cc: list[str] | None = None) -> dict:
    recipients = _addresses(to, "кому")
    copies = _addresses(cc or [], "копия")
    subject = (subject or "").strip()
    if not subject:
        raise ValueError("Укажите тему письма.")
    text = body or ""
    if len(text) > _MAX_SEND:
        raise ValueError("Текст письма слишком длинный.")
    cfg = _cfg()
    message = EmailMessage()
    message["From"] = cfg["sender"]
    message["To"] = ", ".join(recipients)
    if copies:
        message["Cc"] = ", ".join(copies)
    message["Subject"] = subject
    message.set_content(text)
    if cfg["smtp_security"] == "ssl":
        client = smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], timeout=30)
    else:
        client = smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30)
    try:
        if cfg["smtp_security"] == "starttls":
            client.starttls()
        client.login(cfg["user"], cfg["password"])
        client.send_message(message)
    finally:
        try:
            client.quit()
        except Exception:
            pass
    return {"ok": True, "to": recipients, "cc": copies, "subject": subject}
