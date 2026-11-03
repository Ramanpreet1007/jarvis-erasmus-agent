"""Email reading, file saving, Excel updates and drafts.

There is deliberately no function here, or anywhere in this project, that sends email.
"""
import hashlib
import json
import shutil
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook, load_workbook
from pypdf import PdfReader

from .config import INBOX, MEASURED_FILE
from .validate import DOC_LABELS, next_deadline, readiness, safe_name

ALLOWED_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
EXCEL_COLUMNS = [
    "Student ID", "Surname", "First name", "Email", "Academic year", "Host university",
    "Stay start", "Stay end", "Insurance until", "Documents received", "Status", "Issue",
    "Semester", "Next deadline",
]


def load_measured() -> dict:
    if MEASURED_FILE.exists():
        return json.loads(MEASURED_FILE.read_text(encoding="utf-8"))
    return {}


def save_measured(**values) -> None:
    MEASURED_FILE.write_text(json.dumps(load_measured() | values, indent=2), encoding="utf-8")


def excel_safe(value):
    """Stops text from an email being treated as an Excel formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@dataclass
class Attachment:
    filename: str
    data: bytes

    @property
    def suffix(self) -> str:
        return Path(self.filename).suffix.lower()

    @property
    def mime_type(self) -> str | None:
        return ALLOWED_TYPES.get(self.suffix)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def text(self) -> str:
        """Text layer of a PDF, including text hidden from human readers."""
        if self.suffix != ".pdf":
            return ""
        try:
            reader = PdfReader(BytesIO(self.data))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            return ""


@dataclass
class ParsedEmail:
    id: str
    sender: str
    subject: str
    body: str
    sha256: str
    attachments: list[Attachment] = field(default_factory=list)


def read_eml(path: Path) -> ParsedEmail:
    return parse_eml(path.read_bytes(), path.stem)


def parse_eml(raw: bytes, email_id: str) -> ParsedEmail:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    body = message.get_body(preferencelist=("plain", "html"))
    return ParsedEmail(
        id=email_id,
        sender=str(message.get("From", "")),
        subject=str(message.get("Subject", "")),
        body=body.get_content().strip() if body else "",
        sha256=hashlib.sha256(raw).hexdigest(),
        attachments=[
            Attachment(a.get_filename() or "unnamed", a.get_payload(decode=True) or b"")
            for a in message.iter_attachments()
        ],
    )


def list_email_files(folder: Path = INBOX) -> list[Path]:
    return sorted(folder.glob("*.eml"))


class Store:
    """Everything the agent produces, kept under one output folder."""

    def __init__(self, output: Path):
        self.output = Path(output)
        self.filed = self.output / "filed"
        self.review = self.output / "review"
        self.excel = self.output / "students.xlsx"
        self.state_file = self.output / "state.json"
        self.state = self._load()

    def _load(self) -> dict:
        if self.state_file.exists():
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        return {"mode": None, "seconds": None, "emails": {}, "items": [], "students": {}, "drafts": {}, "warnings": []}

    def save(self) -> None:
        self.output.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.state_file)

    def reset(self) -> None:
        if self.output.exists():
            # Checked first, so a file open in Excel can't leave a half-deleted folder.
            for path in self.output.rglob("*"):
                if path.is_file():
                    with open(path, "r+b"):
                        pass
            shutil.rmtree(self.output)
        self.state = self._load()

    def save_attachment(self, attachment: Attachment, student: dict, doc_type: str, year: str) -> str:
        """Saves as Surname_Name_DocType_YYYY-YY. A changed resubmission becomes _v2."""
        documents = student.setdefault("documents", {})
        existing = documents.get(doc_type)
        if existing and attachment.sha256 in existing["hashes"]:
            return existing["file"]
        version = len(existing["hashes"]) + 1 if existing else 1
        tag = f"_v{version}" if version > 1 else ""
        folder = self.filed / student["id"]
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{student['id']}_{doc_type}_{year}{tag}{attachment.suffix}"
        (folder / name).write_bytes(attachment.data)
        documents[doc_type] = {
            "file": name,
            "hashes": (existing["hashes"] if existing else []) + [attachment.sha256],
            "year": year,
        }
        return name

    def align_years(self, student: dict) -> None:
        """If the stay dates arrive later, earlier files move to the correct academic year."""
        year = student.get("academic_year")
        folder = self.filed / student["id"]
        for doc_type, record in student.get("documents", {}).items():
            old = record.get("year")
            if not year or old == year:
                continue
            for path in folder.glob(f"{student['id']}_{doc_type}_{old}*"):
                renamed = path.name.replace(f"_{old}", f"_{year}", 1)
                path.replace(path.with_name(renamed))
                for item in self.state["items"]:
                    if item["new_name"].endswith("/" + path.name):
                        item["new_name"] = f"filed/{student['id']}/{renamed}"
            record["file"] = record["file"].replace(f"_{old}", f"_{year}", 1)
            record["year"] = year

    def quarantine(self, email_id: str, attachment: Attachment, index: int) -> str:
        """Keeps a document for a person to check, outside the student folders."""
        folder = self.review / email_id
        folder.mkdir(parents=True, exist_ok=True)
        suffix = attachment.suffix if attachment.mime_type else ".bin"
        name = f"attachment_{index}{suffix}"
        (folder / name).write_bytes(attachment.data)
        return f"review/{email_id}/{name}"

    def upsert_student(self, student: dict) -> None:
        """Updates the student's row, or adds it. Other columns staff added are kept."""
        if self.excel.exists():
            workbook = load_workbook(self.excel)
            sheet = workbook.active
        else:
            self.output.mkdir(parents=True, exist_ok=True)
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Students"
            sheet.append(EXCEL_COLUMNS)
            sheet.freeze_panes = "A2"
        row = next(
            (r for r in range(2, sheet.max_row + 1) if sheet.cell(r, 1).value == student["id"]),
            sheet.max_row + 1,
        )
        status = readiness(student)
        due = next_deadline(student)
        values = [
            student["id"], student["surname"], student["first_name"], student.get("email"),
            student.get("academic_year"), student.get("host_university"),
            student.get("stay_start"), student.get("stay_end"), student.get("insurance_end"),
            ", ".join(DOC_LABELS[d] for d in student.get("documents", {})),
            status["level"].capitalize(), "" if status["level"] == "green" else status["summary"],
            student.get("semester"), f"{due['date']} ({due['document']})" if due else "",
        ]
        for column, value in enumerate(values, start=1):
            sheet.cell(row, column).value = excel_safe(value)
        workbook.save(self.excel)

    def save_draft(self, draft_id: str, **fields) -> None:
        """Stores a reply for staff. Editing the text resets any earlier approval."""
        old = self.state["drafts"].get(draft_id)
        approved = bool(old and old["approved"] and old["body"] == fields["body"])
        self.state["drafts"][draft_id] = {"id": draft_id, "approved": approved, **fields}

    def approve_draft(self, draft_id: str) -> None:
        """Marks a draft as approved by staff. It does not send anything."""
        self.state["drafts"][draft_id]["approved"] = True
        self.save()


def student_id(first_name: str, surname: str) -> str:
    return f"{safe_name(surname)}_{safe_name(first_name)}"
