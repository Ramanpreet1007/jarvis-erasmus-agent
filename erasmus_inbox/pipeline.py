"""Processes emails: read, extract, check, route, file, record, draft."""
import hashlib
import time

from .ai import faq_ids, reply_matches_faq
from .config import INBOX
from .tools import ParsedEmail, Store, list_email_files, read_eml, save_measured, student_id
from .validate import (
    DOC_LABELS, academic_year, check_document, evidence_problems, find_injection, insurance_problem,
    missing_documents, next_deadline, parse_date, quote_found, semester_for, sender_problem, year_for,
)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
HANDOFF_REPLY = "Thank you for your message. I'll pass this to staff, and someone will get back to you."


def match_document(extraction, attachment, index: int, supported: int):
    """Finds Gemini's result for an attachment, even if it changed the name slightly."""
    squash = lambda name: "".join(c for c in name.casefold() if c.isalnum())
    for doc in extraction.documents:
        if squash(doc.attachment) == squash(attachment.filename):
            return doc
    if len(extraction.documents) == supported and index <= supported:
        return extraction.documents[index - 1]
    return None


class Pipeline:
    def __init__(self, store: Store, ai):
        self.store = store
        self.ai = ai

    def run(self, folder=INBOX):
        """Yields after each email so the dashboard can update live."""
        state = self.store.state
        state["mode"] = self.ai.mode
        for path in list_email_files(folder):
            email = read_eml(path)
            if email.id in state["emails"]:
                continue
            started = time.perf_counter()
            self.process(email)
            state["seconds"] = round((state["seconds"] or 0) + time.perf_counter() - started, 1)
            self.store.save()
            yield email.id
        if folder == INBOX and self.ai.mode == "live" and state["seconds"] is not None:
            save_measured(live_seconds=state["seconds"])

    def process_upload(self, data: bytes) -> str | None:
        """Processes an uploaded .eml file. Returns its id, or None if it was already done."""
        if len(data) > MAX_UPLOAD_BYTES:
            raise ValueError("This email is larger than 25 MB.")
        email_id = f"upload_{hashlib.sha256(data).hexdigest()[:12]}"  # Never uses the uploaded file's name.
        if email_id in self.store.state["emails"]:
            return None
        folder = self.store.output / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{email_id}.eml"
        path.write_bytes(data)
        self.process(read_eml(path))
        self.store.state["mode"] = self.store.state["mode"] or self.ai.mode
        self.store.save()
        return email_id

    def process(self, email: ParsedEmail) -> None:
        state = self.store.state
        state["items"] = [i for i in state["items"] if i["email_id"] != email.id]  # Replaces a failed attempt.
        try:
            extraction = self.ai.extract(email)
        except Exception as error:
            # Not marked as processed, so the next run tries this email again.
            self._add_item(email, status="review", reasons=[f"AI could not read this email: {type(error).__name__}"])
            for index, attachment in enumerate(email.attachments, start=1):
                self.store.quarantine(email.id, attachment, index)
            return
        state["emails"][email.id] = email.sha256

        injection = extraction.suspicious_quote if extraction.suspicious_content else None
        injection = injection or find_injection(email.body, *[a.text() for a in email.attachments])
        injected = injection is not None or extraction.suspicious_content
        injection_reason = f'Possible prompt injection, instructions ignored: "{injection or "see document"}"'
        student, identity_problem = self._student(extraction, email)

        supported = sum(1 for a in email.attachments if a.mime_type)
        position = 0
        for index, attachment in enumerate(email.attachments, start=1):
            position += 1 if attachment.mime_type else 0
            doc = match_document(extraction, attachment, position, supported) if attachment.mime_type else None
            self._handle_attachment(email, extraction, attachment, doc, index, student, identity_problem,
                                    injection_reason if injected else None)

        if extraction.student_question and not injected:
            self._handle_question(email, extraction.student_question, student)
        elif not email.attachments:
            reason = injection_reason if injected else "No document or clear question found"
            self._add_item(email, status="review", reasons=[reason], student=student)

        if student:
            self._refresh_student(student)

    def _student(self, extraction, email) -> tuple[dict | None, str | None]:
        """Returns the student record, or the reason this sender can't be trusted to update it."""
        if not (extraction.first_name and extraction.surname):
            return None, None
        sid = student_id(extraction.first_name, extraction.surname)
        if sid.startswith("_") or sid.endswith("_"):
            return None, None
        known = self.store.state["students"].get(sid)
        if not email.attachments:
            # A question alone does not open a student record.
            return known, None
        problem = sender_problem(email.sender, extraction.first_name, extraction.surname, known and known.get("email"))
        if problem:
            return None, problem
        if known:
            return known, None
        known = {
            "id": sid, "first_name": extraction.first_name, "surname": extraction.surname,
            "email": email.sender, "documents": {}, "open_reviews": [],
        }
        self.store.state["students"][sid] = known
        return known, None

    def _handle_attachment(self, email, extraction, attachment, doc, index, student, identity_problem, injection_reason):
        evidence = []
        if doc:
            source = attachment.text()
            for item in doc.evidence:
                checked = quote_found(item.quote, source) if source else None
                evidence.append({**item.model_dump(), "verified": checked})

        reasons = []
        if not attachment.mime_type:
            reasons.append(f"Unsupported file type: {attachment.suffix or 'none'}")
        elif doc is None:
            reasons.append("The AI returned no result for this attachment")
        else:
            reasons += check_document(doc, extraction.first_name, extraction.surname)
            reasons += evidence_problems(doc, evidence)
        if identity_problem:
            reasons.insert(0, identity_problem)
        if injection_reason:
            reasons.insert(0, injection_reason)
        year = year_for(doc, student) if doc else None
        if not reasons and not year:
            reasons.append("Academic year could not be worked out")

        common = dict(
            original=attachment.filename,
            document=DOC_LABELS.get(doc.doc_type, "Unknown") if doc else "Unknown",
            evidence=evidence,
            confidence=doc.confidence if doc else None,
            student=student,
        )
        if reasons or student is None:
            location = self.store.quarantine(email.id, attachment, index)
            self._add_item(email, status="review", reasons=reasons or ["Student could not be identified"], new_name=location, **common)
            if student is not None:
                student["open_reviews"].append({
                    "email_id": email.id, "original": attachment.filename, "reason": reasons[0],
                    "clearer_copy": any(r.startswith("Low confidence") for r in reasons),
                })
            return

        for field in ("host_university", "stay_start", "stay_end", "insurance_start", "insurance_end"):
            value = getattr(doc, field)
            if value:
                student[field] = value
                source_evidence = [e for e in evidence if e["field"] == field]
                if source_evidence:
                    student.setdefault("evidence", {})[field] = {**source_evidence[0], "file": attachment.filename}
        start = parse_date(student.get("stay_start"))
        student["academic_year"] = academic_year(start) if start else student.get("academic_year") or year
        student["semester"] = semester_for(start, parse_date(student.get("stay_end"))) if start else None
        name = self.store.save_attachment(attachment, student, doc.doc_type, year)
        self._add_item(email, status="filed", new_name=f"filed/{student['id']}/{name}", **common)
        self.store.align_years(student)
        self._clear_clearer_copy_reviews(student)

    def _clear_clearer_copy_reviews(self, student: dict) -> None:
        """A readable document has arrived, so the old "unreadable" review is finished."""
        for review in [r for r in student["open_reviews"] if r["clearer_copy"]]:
            self.resolve_review(review["email_id"], review["original"], refresh=False)

    def resolve_review(self, email_id: str, original: str, refresh: bool = True) -> None:
        """A person has dealt with this item. Held documents stay in the review folder."""
        for item in self.store.state["items"]:
            if item["email_id"] == email_id and item["original"] == original and item["status"] == "review":
                item["resolved"] = True
                student = self.store.state["students"].get(item["student"]) if item["student"] else None
                if student:
                    student["open_reviews"] = [
                        r for r in student["open_reviews"]
                        if not (r["email_id"] == email_id and r["original"] == original)
                    ]
                    if refresh:
                        self._refresh_student(student)
        if refresh:
            self.store.save()

    def _handle_question(self, email, question, student) -> None:
        answer = self.answer(question)
        status = "answered" if answer["can_answer"] else "handoff"
        draft_id = f"{email.id}_reply"
        self.store.save_draft(
            draft_id, kind="Answer" if answer["can_answer"] else "Handoff",
            to=email.sender, subject=f"Re: {email.subject or 'your message'}", question=question,
            body=answer["reply"], english=answer["english"], language=answer["language"], faq_id=answer["faq_id"],
        )
        reasons = [] if answer["can_answer"] else ["Question is not covered by the FAQ"]
        self._add_item(email, status=status, document="Question", new_name=answer["faq_id"] or "To staff",
                       original="(no attachment)" if not email.attachments else "(question in email)",
                       reasons=reasons, student=student)

    def answer(self, question: str) -> dict:
        """FAQ answer, with a code check that it really cites the FAQ."""
        try:
            result = self.ai.answer(question)
        except Exception:
            result = None
        valid = bool(result and result.can_answer and result.reply and result.faq_id in faq_ids()
                     and reply_matches_faq(result.faq_id, result.reply_english or result.reply))
        return {
            "can_answer": valid,
            "faq_id": result.faq_id if valid else None,
            "language": result.language if result else "Unknown",
            "reply": result.reply if valid else HANDOFF_REPLY,
            "english": result.reply_english if valid else HANDOFF_REPLY,
        }

    def _refresh_student(self, student: dict) -> None:
        self._draft_action(student)
        try:
            self.store.upsert_student(student)
            student.pop("excel_pending", None)
        except PermissionError:
            student["excel_pending"] = True
            warning = "students.xlsx is open in Excel, so it could not be updated. Close it and run again."
            if warning not in self.store.state["warnings"]:
                self.store.state["warnings"].append(warning)
        self.sync_pending_excel()

    def sync_pending_excel(self) -> None:
        pending = [s for s in self.store.state["students"].values() if s.get("excel_pending")]
        try:
            for student in pending:
                self.store.upsert_student(student)
                student.pop("excel_pending")
            self.store.state["warnings"] = []
        except PermissionError:
            pass

    def _draft_action(self, student: dict) -> None:
        """The drafted fix: every problem found comes with the next step ready to approve."""
        first = student["first_name"]
        problem = insurance_problem(student)
        missing = [DOC_LABELS[d] for d in missing_documents(student)]
        if problem:
            cover, stay = student.get("insurance_end"), student.get("stay_end")
            body = (f"Hi {first},\n\nYour insurance ends on {parse_date(cover):%d %B %Y}, but your stay runs until "
                    f"{parse_date(stay):%d %B %Y}. Please send proof of insurance that covers your whole stay.\n\n"
                    "Kind regards,\nErasmus Office")
            kind = "Fix: insurance"
        elif any(r["clearer_copy"] for r in student["open_reviews"]):
            body = (f"Hi {first},\n\nWe couldn't read the document you sent. Please send a clearer scan or photo.\n\n"
                    "Kind regards,\nErasmus Office")
            kind = "Fix: clearer copy"
        elif missing and not student["open_reviews"]:
            due = next_deadline(student)
            due_line = f" The next deadline is {parse_date(due['date']):%d %B %Y} for your {due['document']}." if due else ""
            body = (f"Hi {first},\n\nThanks for your documents. We still need: {', '.join(missing)}.{due_line}\n\n"
                    "Kind regards,\nErasmus Office")
            kind = "Request: missing documents"
        else:
            self.store.state["drafts"].pop(f"{student['id']}_action", None)
            return
        self.store.save_draft(f"{student['id']}_action", kind=kind, to=student["email"],
                              subject="Your Erasmus documents", body=body, student=student["id"])

    def _add_item(self, email, status, reasons=None, new_name="", original="(no attachment)", document="None",
                  evidence=None, confidence=None, student=None) -> None:
        self.store.state["items"].append({
            "email_id": email.id, "sender": email.sender, "subject": email.subject, "original": original,
            "document": document, "new_name": new_name, "status": status, "reasons": reasons or [],
            "evidence": evidence or [], "confidence": confidence, "student": student["id"] if student else None,
            "resolved": False,
        })
