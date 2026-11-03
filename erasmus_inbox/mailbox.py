"""Reads one Gmail label and saves approved replies to Gmail Drafts.

The label is opened read-only, so emails are never changed or marked as read.
Nothing here can send email: there is no SMTP, and drafts are only stored in Gmail.
"""
import imaplib
import os
import re
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

from .pipeline import Pipeline
from .tools import Store, read_eml

MAX_EMAILS = 25
APPLICATION_LIMIT = 200


class MailboxError(Exception):
    """A problem to show staff. It never contains the password."""


def settings() -> dict:
    return {
        "email": os.getenv("ERASMUS_EMAIL", "").strip(),
        "password": os.getenv("ERASMUS_APP_PASSWORD", "").replace(" ", ""),
        "label": os.getenv("ERASMUS_LABEL", "Erasmus-Demo").strip(),
    }


def configured() -> bool:
    values = settings()
    return bool(values["email"] and values["password"]) and "your-" not in values["email"] + values["password"]


def _connect() -> imaplib.IMAP4_SSL:
    values = settings()
    try:
        connection = imaplib.IMAP4_SSL(os.getenv("ERASMUS_IMAP_HOST", "imap.gmail.com"), timeout=30)
        connection.login(values["email"], values["password"])
        return connection
    except imaplib.IMAP4.error:
        raise MailboxError("Gmail rejected the login. Check ERASMUS_EMAIL and the app password in .env.") from None
    except OSError as error:
        raise MailboxError(f"Could not reach Gmail: {type(error).__name__}") from None


def _quoted(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def load_emails(folder: Path, connect=_connect) -> int:
    """Copies the sample .eml files into the label, to set up a demo mailbox. It doesn't send anything."""
    name = _quoted(settings()["label"])
    connection = connect()
    try:
        connection.create(name)  # Answers NO if the label already exists, which is fine.
        count = 0
        for path in sorted(folder.glob("*.eml")):
            status, _ = connection.append(name, None, None, path.read_bytes())
            if status != "OK":
                raise MailboxError(f"The mailbox did not accept {path.name}.")
            count += 1
        return count
    finally:
        try:
            connection.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def fetch_applications(store: Store, connect=_connect) -> Path:
    """Copies new application emails from the label to a local folder, ready for run_batch."""
    folder = store.output / "application_mail"
    fetch_new(folder, store.state.get("applications", {}), connect, APPLICATION_LIMIT)
    return folder


def fetch_new(destination: Path, known_ids, connect=_connect, limit: int = MAX_EMAILS) -> list[Path]:
    """Copies new emails from the label to local .eml files, newest ones first up to the limit."""
    label = settings()["label"]
    connection = connect()
    try:
        status, _ = connection.select(_quoted(label), readonly=True)
        if status != "OK":
            raise MailboxError(f'Gmail label "{label}" was not found. Create it and put the demo emails in it.')
        validity = (connection.response("UIDVALIDITY")[1] or [b"0"])[0].decode()
        status, data = connection.uid("SEARCH", None, "ALL")
        if status != "OK":
            raise MailboxError("Gmail could not list the label.")
        destination.mkdir(parents=True, exist_ok=True)
        saved = []
        for uid in (data[0] or b"").split()[-limit:]:
            email_id = f"gmail_{validity}_{uid.decode()}"
            path = destination / f"{email_id}.eml"
            if email_id in known_ids or path.exists():
                continue
            status, parts = connection.uid("FETCH", uid, "(BODY.PEEK[])")  # PEEK keeps it unread.
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple)), None)
            if status == "OK" and raw:
                path.write_bytes(raw)
                saved.append(path)
        return saved
    finally:
        try:
            connection.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def check_mailbox(pipeline: Pipeline, store: Store, connect=_connect) -> list[str]:
    """Fetches and processes new emails. Failed ones are retried on the next check."""
    folder = store.output / "mail"
    fetch_new(folder, store.state["emails"], connect)
    senders = store.state.setdefault("mailbox_senders", [])
    processed = []
    for email_id in pipeline.run(folder):
        address = parseaddr(read_eml(folder / f"{email_id}.eml").sender)[1].casefold()
        if address and address not in senders:
            senders.append(address)
        processed.append(email_id)
    store.save()
    return processed


def from_mailbox(draft: dict, store: Store) -> bool:
    """Only replies to people who emailed the connected label go to Gmail Drafts."""
    return parseaddr(draft["to"])[1].casefold() in store.state.get("mailbox_senders", [])


def _drafts_folder(connection) -> str:
    """Gmail's Drafts folder name changes with the account's language."""
    status, folders = connection.list()
    for line in folders if status == "OK" else []:
        text = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
        if "\\Drafts" in text:
            match = re.search(r'"([^"]+)"\s*$', text)
            if match:
                return match.group(1)
    return "[Gmail]/Drafts"


def save_gmail_draft(draft: dict, connect=_connect) -> None:
    """Stores a draft in Gmail. A person opens it there and decides whether to send it."""
    message = EmailMessage()
    message["From"] = settings()["email"]
    message["To"] = parseaddr(draft["to"])[1]
    message["Subject"] = " ".join(draft["subject"].split())
    message.set_content(draft["body"])
    connection = connect()
    try:
        status, _ = connection.append(_quoted(_drafts_folder(connection)), "(\\Draft)", None, bytes(message))
        if status != "OK":
            raise MailboxError("Gmail did not accept the draft.")
    except imaplib.IMAP4.error:
        raise MailboxError("Gmail did not accept the draft.") from None
    finally:
        try:
            connection.logout()
        except (imaplib.IMAP4.error, OSError):
            pass
