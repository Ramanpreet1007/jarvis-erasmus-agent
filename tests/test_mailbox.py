"""Tests the Gmail code with a fake mailbox, so no real account is touched."""
from email.message import EmailMessage

import pytest

from erasmus_inbox import mailbox
from erasmus_inbox.ai import FixtureAI
from erasmus_inbox.pipeline import Pipeline
from erasmus_inbox.schemas import EmailExtraction, FaqAnswer
from erasmus_inbox.tools import Store


def raw_email(sender="tom.w@student.example", subject="Deadline?"):
    message = EmailMessage()
    message["From"], message["Subject"] = sender, subject
    message.set_content("When is the deadline?")
    return bytes(message)


class FakeGmail:
    def __init__(self, emails=None, label_exists=True):
        self.emails = emails or {}
        self.label_exists = label_exists
        self.readonly = None
        self.selected = None
        self.fetch_commands = []
        self.appended = []

    def select(self, name, readonly=False):
        self.selected, self.readonly = name, readonly
        return ("OK" if self.label_exists else "NO"), [b""]

    def response(self, _name):
        return "UIDVALIDITY", [b"77"]

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b" ".join(self.emails)]
        self.fetch_commands.append(args[1])
        return "OK", [(b"header", self.emails[args[0]]), b")"]

    def list(self):
        return "OK", [b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Entw&APw-rfe"']

    def append(self, folder, flags, _date, message):
        self.appended.append((folder, flags, message))
        return "OK", [b""]

    def logout(self):
        pass


class Answers(FixtureAI):
    def extract(self, email):
        return EmailExtraction(first_name="Tom", surname="Wagner", student_question="When is the deadline for the insurance document?")

    def answer(self, question):
        return FaqAnswer(can_answer=True, faq_id="F1", language="English", reply="30 days before.", reply_english="30 days before.")


@pytest.fixture(autouse=True)
def account(monkeypatch):
    monkeypatch.setenv("ERASMUS_EMAIL", "office@gmail.com")
    monkeypatch.setenv("ERASMUS_APP_PASSWORD", "abcd efgh ijkl mnop")
    monkeypatch.setenv("ERASMUS_LABEL", "Erasmus-Demo")


def test_only_the_label_is_read_read_only_and_emails_stay_unread(tmp_path):
    gmail = FakeGmail({b"1": raw_email()})
    saved = mailbox.fetch_new(tmp_path, {}, lambda: gmail)
    assert gmail.selected == '"Erasmus-Demo"' and gmail.readonly is True
    assert gmail.fetch_commands == ["(BODY.PEEK[])"]
    assert saved[0].name == "gmail_77_1.eml"


def test_missing_label_stops_and_never_reads_the_inbox(tmp_path):
    gmail = FakeGmail({b"1": raw_email()}, label_exists=False)
    with pytest.raises(mailbox.MailboxError, match="Erasmus-Demo"):
        mailbox.fetch_new(tmp_path, {}, lambda: gmail)
    assert gmail.fetch_commands == []


def test_new_email_is_processed_once_with_a_draft(tmp_path):
    gmail = FakeGmail({b"1": raw_email()})
    store = Store(tmp_path / "output")
    pipeline = Pipeline(store, Answers())
    assert mailbox.check_mailbox(pipeline, store, lambda: gmail) == ["gmail_77_1"]
    assert mailbox.check_mailbox(pipeline, store, lambda: gmail) == []
    draft = store.state["drafts"]["gmail_77_1_reply"]
    assert mailbox.from_mailbox(draft, store)
    assert not mailbox.from_mailbox({"to": "anna@student.example"}, store)


def test_approved_reply_goes_to_gmail_drafts(monkeypatch):
    gmail = FakeGmail()
    draft = {"to": "Tom <tom.w@student.example>", "subject": "Re: Deadline?\r\nBcc: x@evil.example", "body": "30 days before."}
    mailbox.save_gmail_draft(draft, lambda: gmail)
    folder, flags, message = gmail.appended[0]
    assert folder == '"[Gmail]/Entw&APw-rfe"' and flags == "(\\Draft)"
    assert b"To: tom.w@student.example" in message
    assert b"\r\nBcc:" not in message and b"\nBcc:" not in message


def test_placeholder_values_do_not_count_as_configured(monkeypatch):
    assert mailbox.configured()
    monkeypatch.setenv("ERASMUS_EMAIL", "your-address@gmail.com")
    assert not mailbox.configured()


def test_applications_are_read_from_the_inbox_beyond_the_normal_limit_and_only_once(tmp_path):
    gmail = FakeGmail({str(n).encode(): raw_email() for n in range(1, 101)})
    store = Store(tmp_path / "output")
    folder = mailbox.fetch_applications(store, lambda: gmail)
    assert len(list(folder.glob("*.eml"))) == 100 and gmail.readonly is True
    gmail.fetch_commands.clear()
    mailbox.fetch_applications(store, lambda: gmail)
    assert gmail.fetch_commands == []


def test_loading_the_demo_inbox_only_adds_to_the_label(tmp_path):
    (tmp_path / "a.eml").write_bytes(raw_email())
    gmail = FakeGmail()
    gmail.create = lambda name: ("NO", [b"exists"])
    assert mailbox.load_emails(tmp_path, lambda: gmail) == 1
    assert gmail.appended[0][0] == '"Erasmus-Demo"'
