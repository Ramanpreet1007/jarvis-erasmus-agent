"""Exact rules, written in plain Python so results are repeatable."""
import re
import unicodedata
from datetime import date, timedelta
from email.utils import parseaddr

from .config import CONFIDENCE_THRESHOLD

REQUIRED_DOCS = ["LearningAgreement", "Transcript", "Insurance"]
DOC_LABELS = {
    "LearningAgreement": "Learning Agreement",
    "Transcript": "Transcript",
    "Insurance": "Insurance",
    "CertificateOfArrival": "Certificate of Arrival",
    "Other": "Other",
}
_SPECIAL_LETTERS = str.maketrans(
    {"ß": "ss", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "æ": "ae", "Æ": "Ae"}
)
_INJECTION = re.compile(
    r"ignore (all |any )?(your |the )?(previous |prior |above )?(instructions|rules)"
    r"|system prompt|you are now|mark (me|this|the student).{0,30}approved",
    re.IGNORECASE,
)


def parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def academic_year(start: date) -> str:
    """Academic years begin in August: Sep 2026 and Feb 2027 are both 2026-27."""
    first = start.year if start.month >= 8 else start.year - 1
    return f"{first}-{(first + 1) % 100:02d}"


def semester_for(start: date, end: date | None) -> str:
    if end and (end - start).days > 200:
        return "Full year"
    return "Autumn" if start.month >= 8 else "Spring"


def safe_name(name: str) -> str:
    """Kalniņa -> Kalnina. Also stops a name from changing the file path."""
    text = unicodedata.normalize("NFKD", name.translate(_SPECIAL_LETTERS))
    text = "".join(c for c in text if c.isascii() and (c.isalnum() or c in " -'"))
    return "-".join(text.replace("'", "").split())


def find_injection(*texts: str) -> str | None:
    for text in texts:
        match = _INJECTION.search(text or "")
        if match:
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            return text[start: end if end != -1 else None].strip()[:200]
    return None


def quote_found(quote: str, text: str) -> bool:
    squash = lambda s: re.sub(r"\s+", " ", s).strip().casefold()
    return bool(quote.strip()) and squash(quote) in squash(text)


def check_document(doc, first_name: str | None, surname: str | None) -> list[str]:
    """Reasons this document must go to a person. Empty means it can be filed."""
    reasons = []
    if not (first_name and surname and safe_name(first_name) and safe_name(surname)):
        reasons.append("Student name could not be identified")
    if doc.doc_type == "Other":
        reasons.append("Document type could not be identified")
    if doc.confidence < CONFIDENCE_THRESHOLD:
        reasons.append(f"Low confidence ({doc.confidence:.0%}): the document may be hard to read")
    for field in ("stay_start", "stay_end", "insurance_start", "insurance_end"):
        value = getattr(doc, field)
        if value and not parse_date(value):
            reasons.append(f"Unreadable date in {field}: {value}")
    start, end = parse_date(doc.stay_start), parse_date(doc.stay_end)
    if start and end and end < start:
        reasons.append(f"Stay ends ({end}) before it starts ({start})")
    cover_start, cover_end = parse_date(doc.insurance_start), parse_date(doc.insurance_end)
    if cover_start and cover_end and cover_end < cover_start:
        reasons.append(f"Insurance ends ({cover_end}) before it starts ({cover_start})")
    return reasons


def evidence_problems(doc, evidence: list[dict]) -> list[str]:
    """Every date must come with a quote, and a PDF quote must really be in the PDF."""
    problems = []
    for field in ("stay_start", "stay_end", "insurance_start", "insurance_end"):
        if not getattr(doc, field):
            continue
        quotes = [e for e in evidence if e["field"] == field]
        if not quotes:
            problems.append(f"No evidence given for {field}")
        elif all(e["verified"] is False for e in quotes):
            problems.append(f"Evidence for {field} was not found in the document")
    return problems


def sender_problem(sender: str, first_name: str, surname: str, known_email: str | None) -> str | None:
    """Stops one person's email from changing another student's record."""
    address = parseaddr(sender)[1].casefold()
    if known_email:
        known = parseaddr(known_email)[1].casefold()
        return None if address == known else f"Sent from {address}, but this student's address on file is {known}"
    local = re.sub(r"[^a-z0-9]", "", address.split("@")[0])
    names = [safe_name(first_name), *safe_name(surname).split("-")]
    if any(len(n) > 1 and n.casefold() in local for n in names):
        return None
    return f"Sender {address} doesn't appear to match the student named in the document"


def year_for(doc, student: dict | None) -> str | None:
    for value in (doc.stay_start, doc.insurance_start, (student or {}).get("stay_start")):
        parsed = parse_date(value)
        if parsed:
            return academic_year(parsed)
    if doc.academic_year and re.fullmatch(r"\d{4}-\d{2}", doc.academic_year):
        return doc.academic_year
    return (student or {}).get("academic_year")


def insurance_problem(student: dict) -> str | None:
    """The cross-document check: does the insurance cover the whole stay?"""
    stay_start, stay_end = parse_date(student.get("stay_start")), parse_date(student.get("stay_end"))
    cover_start, cover_end = (
        parse_date(student.get("insurance_start")),
        parse_date(student.get("insurance_end")),
    )
    if stay_end and cover_end and cover_end < stay_end:
        gap = (stay_end - cover_end).days
        return f"Insurance ends {cover_end:%d %b %Y}, {gap} days before the stay ends on {stay_end:%d %b %Y}"
    if stay_start and cover_start and cover_start > stay_start:
        gap = (cover_start - stay_start).days
        return f"Insurance starts {cover_start:%d %b %Y}, {gap} days after the stay starts on {stay_start:%d %b %Y}"
    return None


def deadlines(student: dict) -> dict[str, date]:
    """Deadlines from the office FAQ, based on the student's stay."""
    start = parse_date(student.get("stay_start"))
    if not start:
        return {}
    due = {"Insurance": start - timedelta(days=30), "CertificateOfArrival": start + timedelta(days=7)}
    if semester_for(start, parse_date(student.get("stay_end"))) == "Spring":
        december = date(start.year - 1, 12, 15)
        due |= {"LearningAgreement": december, "Transcript": december}
    return due


def next_deadline(student: dict, today: date | None = None) -> dict | None:
    """The earliest deadline among the documents still missing."""
    today = today or date.today()
    due = deadlines(student)
    open_items = [(due[d], d) for d in missing_documents(student, today) if d in due]
    if not open_items:
        return None
    when, document = min(open_items)
    return {"date": when.isoformat(), "document": DOC_LABELS[document], "overdue": when < today}


def missing_documents(student: dict, today: date | None = None) -> list[str]:
    today = today or date.today()
    required = list(REQUIRED_DOCS)
    start = parse_date(student.get("stay_start"))
    if start and start <= today:
        required.append("CertificateOfArrival")
    return [d for d in required if d not in student.get("documents", {})]


def readiness(student: dict, today: date | None = None) -> dict:
    """Overall status for the readiness board: green, amber or red."""
    missing = missing_documents(student, today)
    problem = insurance_problem(student)
    reviews = student.get("open_reviews", [])
    if problem:
        return {"level": "red", "summary": problem, "next": "Staff review: updated cover requested in draft", "missing": missing}
    if reviews:
        return {"level": "red", "summary": reviews[0]["reason"], "next": "A person needs to check this", "missing": missing}
    if missing:
        names = ", ".join(DOC_LABELS[d] for d in missing)
        return {"level": "amber", "summary": f"Missing: {names}", "next": "Request drafted for staff", "missing": missing}
    return {"level": "green", "summary": "All documents received and consistent", "next": "None", "missing": []}
