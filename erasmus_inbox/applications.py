"""Handles Erasmus application emails in bulk: read, decide eligibility, draft the reply.

Gemini only reads the email. These fixed rules make the decision, and staff approve every reply.
"""
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parseaddr

from .advisor import LEVELS, load_partners, recommend
from .config import APPLICATIONS, RULES_FILE
from .schemas import ApplicationRead
from .tools import ParsedEmail, Store, excel_safe, list_email_files, read_eml
from .validate import find_injection, quote_found

ELIGIBLE, NOT_ELIGIBLE, NEEDS_INFO, REVIEW, OTHER = "eligible", "not_eligible", "needs_info", "review", "other"
CONFIDENCE_NEEDED = 0.7
NO_PARTNER = "Eligible, but no partner fits this profile"

# Replies come from fixed wording, so no AI writes what a student is told and the rule slider stays instant.
TEXT = {
    "en": {
        "hi": "Hi {first},", "there": "there", "sign": "Kind regards,\nErasmus Office",
        "no_preferred": "We couldn't find a suitable option in your preferred countries, so here are the closest alternatives.",
        "options": "Based on your grades and preferences, these are your best options to consider:",
        "promise": "A place isn't guaranteed, because selection depends on all applicants and the final decision is made by the office. The choice is yours, so tell us which options you'd like to apply for.",
        "fit": {"Strong match": "Strong match", "Good match": "Good match", "Reach": "Reach"},
        "sorry": "Thank you for your interest in Erasmus. Based on the details you sent, you don't currently meet the requirements.",
        "failed": "Failed subject: {names}", "low": "Average {grade:g} is below the minimum of {minimum:g}",
        "needs": "The requirements are an average of at least {minimum:g}", "needs_pass": " and no failed subjects",
        "again": "If these details are wrong, or your results change, please reply and we'll look again. You're also welcome to book a meeting with the office.",
        "ask": "Thanks for applying for Erasmus. To check your eligibility, we still need:",
        "missing": {"grade": "your average grade from last semester", "failed": "confirmation of whether you failed any subject, or your transcript",
                    "field": "your field of study", "level": "your English level (A1 to C2)"},
    },
    "it": {
        "hi": "Ciao {first},", "there": "", "sign": "Cordiali saluti,\nUfficio Erasmus",
        "no_preferred": "Non abbiamo trovato un'opzione adatta nei paesi che preferisci, quindi ecco le alternative più vicine.",
        "options": "In base ai tuoi voti e alle tue preferenze, queste sono le migliori opzioni da valutare:",
        "promise": "Il posto non è garantito, perché la selezione dipende da tutti i candidati e la decisione finale spetta all'ufficio. La scelta è tua: facci sapere per quali opzioni vuoi candidarti.",
        "fit": {"Strong match": "ottima corrispondenza", "Good match": "buona corrispondenza", "Reach": "più ambiziosa"},
        "sorry": "Grazie per il tuo interesse per l'Erasmus. In base ai dati che hai inviato, al momento non soddisfi i requisiti.",
        "failed": "Esame non superato: {names}", "low": "La media di {grade:g} è inferiore al minimo di {minimum:g}",
        "needs": "I requisiti sono una media di almeno {minimum:g}", "needs_pass": " e nessun esame non superato",
        "again": "Se questi dati sono errati, o se i tuoi risultati cambiano, rispondi a questa email e li ricontrolleremo. Puoi anche prenotare un incontro con l'ufficio.",
        "ask": "Grazie per la tua candidatura all'Erasmus. Per verificare i requisiti ci servono ancora:",
        "missing": {"grade": "la tua media dell'ultimo semestre", "failed": "la conferma di eventuali esami non superati, oppure il tuo transcript",
                    "field": "il tuo corso di studi", "level": "il tuo livello di inglese (da A1 a C2)"},
    },
    "es": {
        "hi": "Hola {first}:", "there": "", "sign": "Un saludo,\nOficina Erasmus",
        "no_preferred": "No hemos encontrado una opción adecuada en los países que prefieres, así que estas son las alternativas más cercanas.",
        "options": "Según tus notas y preferencias, estas son las mejores opciones que puedes considerar:",
        "promise": "La plaza no está garantizada, porque la selección depende de todos los solicitantes y la decisión final es de la oficina. La elección es tuya: dinos a qué opciones quieres presentarte.",
        "fit": {"Strong match": "muy adecuada", "Good match": "adecuada", "Reach": "más ambiciosa"},
        "sorry": "Gracias por tu interés en Erasmus. Según los datos que nos has enviado, por ahora no cumples los requisitos.",
        "failed": "Asignatura suspendida: {names}", "low": "La media de {grade:g} es inferior al mínimo de {minimum:g}",
        "needs": "Los requisitos son una media de al menos {minimum:g}", "needs_pass": " y ninguna asignatura suspendida",
        "again": "Si estos datos no son correctos, o si cambian tus resultados, responde a este correo y volveremos a revisarlo. También puedes pedir una reunión con la oficina.",
        "ask": "Gracias por solicitar Erasmus. Para comprobar los requisitos todavía necesitamos:",
        "missing": {"grade": "tu nota media del último semestre", "failed": "la confirmación de si has suspendido alguna asignatura, o tu expediente",
                    "field": "tus estudios", "level": "tu nivel de inglés (de A1 a C2)"},
    },
}


excel_safe_cell = excel_safe  # CSV files open in Excel, so email text must not run as a formula.


def load_rules() -> dict:
    return json.loads(RULES_FILE.read_text(encoding="utf-8"))


def active_rules(state: dict) -> dict:
    """The rules staff chose in the dashboard, or the saved defaults."""
    return state.get("rules") or load_rules()


def rule_failure(grade: float | None, failed_subjects: list[str], rules: dict) -> dict | None:
    """Why the office rules make a student not eligible, or None. A clear failure decides it, even if details are missing."""
    english = TEXT["en"]
    if failed_subjects and not rules["allow_failed_subjects"]:
        return {"decision": NOT_ELIGIBLE, "failed": failed_subjects, "reason": english["failed"].format(names=", ".join(failed_subjects))}
    if grade is not None and grade < rules["min_average"]:
        return {"decision": NOT_ELIGIBLE, "grade": grade, "reason": english["low"].format(grade=grade, minimum=rules["min_average"])}
    return None


def failed_status_shown(read, source_text: str, has_image: bool) -> bool:
    """The AI must show its evidence, because a wrong "no failures" would give a student false options."""
    # It may list several transcript lines, so each part has to be there.
    return bool(read.failed_status_known and read.failed_status_quote and (
        has_image or all(quote_found(part, source_text) for part in re.split("[,;]", read.failed_status_quote) if part.strip())))


def decide(read, source_text: str, has_image: bool, rules: dict, partners: list[dict]) -> dict:
    """The decision for one application, with its reason."""
    decision = {"decision": REVIEW, "reason": "", "options": [], "ruled_out": [], "no_preferred_match": False}
    if read.suspicious_content or find_injection(source_text):
        return decision | {"reason": "Contains instructions aimed at the AI, so a person must check it"}
    if not read.is_application:
        return decision | {"decision": OTHER, "reason": "Not an Erasmus application"}
    if read.confidence < CONFIDENCE_NEEDED:
        return decision | {"reason": f"Low confidence ({read.confidence:.0%})"}

    grade = read.last_semester_average
    if read.scale_max not in (None, 10):
        return decision | {"reason": f"Grade uses a scale up to {read.scale_max:g}, and only 0 to 10 is supported"}
    if grade is not None and not 0 <= grade <= 10:
        return decision | {"reason": f"The grade {grade:g} doesn't fit the 0 to 10 scale"}
    if grade is not None:
        if not read.grade_quote:
            return decision | {"reason": "No quote shows where the grade came from"}
        if not has_image and not quote_found(read.grade_quote, source_text):
            return decision | {"reason": "The quote for the grade wasn't found in the email or PDF"}

    failure = rule_failure(grade, read.failed_subjects, rules)
    if failure:
        return decision | failure

    english = TEXT["en"]
    missing = []
    if grade is None:
        missing.append("grade")
    if not failed_status_shown(read, source_text, has_image):
        missing.append("failed")
    field = read.field_of_study
    fields = {f for p in partners for f in p["fields"]}
    if field not in fields:
        missing.append("field")
    if read.english_level not in LEVELS:
        missing.append("level")
    if missing:
        return decision | {"decision": NEEDS_INFO, "missing": missing,
                           "reason": "Missing: " + "; ".join(english["missing"][key] for key in missing)}

    result = recommend(grade, field, read.english_level, read.preferred_countries, partners)
    if not result["options"]:
        return decision | {"reason": NO_PARTNER, "ruled_out": result["ruled_out"]}
    top = result["options"][0]["university"]
    return decision | result | {"decision": ELIGIBLE, "reason": f"Eligible. Top option: {top}"}


def reply_body(first_name: str | None, outcome: dict, rules: dict, language: str = "en") -> str | None:
    """The reply staff review, in the student's language if we have it. Review and other emails get no automatic reply."""
    text = TEXT.get(language, TEXT["en"])
    hello = " ".join(text["hi"].format(first=first_name or text["there"]).split()).replace(" ,", ",").replace(" :", ":")
    kind = outcome["decision"]
    if kind == ELIGIBLE:
        lines = [hello, ""]
        if outcome["no_preferred_match"]:
            lines += [text["no_preferred"], ""]
        lines += [text["options"], ""]
        for number, option in enumerate(outcome["options"], start=1):
            lines.append(f"{number}. {option['university']}, {option['city']}, {option['country']} ({text['fit'][option['fit']]})")
        return "\n".join([*lines, "", text["promise"], "", text["sign"]])
    if kind == NOT_ELIGIBLE:
        if outcome.get("failed"):
            why = text["failed"].format(names=", ".join(outcome["failed"]))
        else:
            why = text["low"].format(grade=outcome["grade"], minimum=rules["min_average"])
        needs = text["needs"].format(minimum=rules["min_average"]) + ("" if rules["allow_failed_subjects"] else text["needs_pass"])
        return f"{hello}\n\n{text['sorry']} {why}.\n\n{needs}. {text['again']}\n\n{text['sign']}"
    if kind == NEEDS_INFO:
        needed = "\n".join(f"- {text['missing'][key]}" for key in outcome["missing"])
        return f"{hello}\n\n{text['ask']}\n{needed}\n\n{text['sign']}"
    return None


def _result(read, source: str, has_image: bool, rules: dict, partners: list[dict]) -> dict:
    """The fields of a record that depend on the rules."""
    outcome = decide(read, source, has_image, rules, partners)
    return {
        "decision": outcome["decision"], "reason": outcome["reason"], "options": outcome["options"],
        "body": reply_body(read.first_name, outcome, rules, read.language or "en"),
        "body_english": reply_body(read.first_name, outcome, rules),
    }


def apply_rules(store: Store, rules: dict) -> int:
    """Decides every application again without calling the AI. Replies that changed need approving again."""
    state = store.state
    state["rules"] = rules
    partners = load_partners()
    changed = 0
    for record in state.get("applications", {}).values():
        if record["failed"]:
            continue
        result = _result(ApplicationRead.model_validate(record["read"]), record["source"], record["has_image"], rules, partners)
        changed += result["decision"] != record["decision"]
        if result["body"] != record["body"]:
            record.update(approved=False, in_drafts=False)
        record.update(result)
    store.save()
    return changed


def allocate(records: dict, partners: list[dict]) -> dict:
    """A preview of who could get which place: highest average first, using each student's ranked options.

    Reach options are skipped, and students haven't chosen yet, so staff decide the real allocation.
    """
    free = {p["university"]: p["places"] for p in partners}
    placed = {p["university"]: [] for p in partners}
    wanted = {p["university"]: 0 for p in partners}
    left_out = {p["university"]: [] for p in partners}
    waiting = []
    eligible = sorted((r for r in records.values() if r["decision"] == ELIGIBLE), key=lambda r: (-r["grade"], r["id"]))
    for record in eligible:
        wanted[record["options"][0]["university"]] += 1
        for option in record["options"]:
            name = option["university"]
            if option["fit"] == "Reach":
                continue
            if free[name]:
                free[name] -= 1
                placed[name].append(record)
                break
            left_out[name].append(record)
        else:
            waiting.append(record)
    universities = [{
        "university": p["university"], "places": p["places"], "first_choice": wanted[p["university"]],
        "filled": len(placed[p["university"]]), "lowest": placed[p["university"]][-1]["grade"] if placed[p["university"]] else None,
        # Same grade on both sides of the cut-off, so the order alone can't be fair.
        "tie": bool(placed[p["university"]] and left_out[p["university"]]) and placed[p["university"]][-1]["grade"] == left_out[p["university"]][0]["grade"],
    } for p in partners]
    return {"universities": universities, "waiting": waiting, "placed": sum(len(v) for v in placed.values())}


def _process_one(email, ai, rules, partners) -> dict:
    record = {"id": email.id, "sender": email.sender, "subject": email.subject, "approved": False}
    source = "\n".join([email.body, *[a.text() for a in email.attachments]])
    has_image = any(a.suffix != ".pdf" for a in email.attachments)
    try:
        read = ai.read_application(email)
    except Exception as error:
        return record | {"failed": True, "decision": REVIEW, "reason": f"AI could not read this email: {type(error).__name__}",
                         "name": "", "grade": None, "failed_subjects": [], "options": [], "quote": None, "body": None}
    return record | _result(read, source, has_image, rules, partners) | {
        "failed": False, "name": " ".join(filter(None, [read.first_name, read.surname])), "grade": read.last_semester_average,
        "failed_subjects": read.failed_subjects, "quote": read.grade_quote, "language": read.language or "en",
        "read": read.model_dump(), "source": source, "has_image": has_image,
    }


def email_from_text(text: str) -> ParsedEmail:
    """A pasted email, treated the same as one from the inbox."""
    return ParsedEmail("pasted", "", "", text.strip(), hashlib.sha256(text.strip().encode("utf-8")).hexdigest())


def _address(email) -> str:
    """The sender, or for pasted text the first address in it. Empty if there's none, so staff never reply to a stale one."""
    found = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", email.body)
    return parseaddr(email.sender)[1] or (found.group() if found else "")


def form_from_email(email, ai, rules: dict, partners: list[dict]) -> dict:
    """Reads one email into the advice form. Only details that pass the checks are filled in, and staff confirm them."""
    record = _process_one(email, ai, rules, partners)
    if record["failed"]:
        return {"error": "The AI couldn't read this email, so nothing was filled in. Try again or type the details."}
    if record["decision"] == OTHER:
        return {"error": "This doesn't look like an Erasmus application, so nothing was filled in."}
    if record["decision"] == REVIEW and record["reason"] != NO_PARTNER:
        return {"error": f"A person should check this email, so nothing was filled in. {record['reason']}."}
    read = ApplicationRead.model_validate(record["read"])
    fields = {f for p in partners for f in p["fields"]}
    countries = {p["country"] for p in partners}
    known = failed_status_shown(read, record["source"], record["has_image"])
    return {
        "first_name": read.first_name or "", "email": _address(email), "grade": read.last_semester_average,
        "field": read.field_of_study if read.field_of_study in fields else None,
        "level": read.english_level if read.english_level in LEVELS else None,
        "countries": [c for c in read.preferred_countries if c in countries],
        "failed_status": "Yes" if read.failed_subjects else "No" if known else "Not known",
        "failed_subjects": ", ".join(read.failed_subjects), "language": read.language or "en", "quote": read.grade_quote,
    }


def advise(first_name: str, grade: float, failed_status: str, failed_subjects: str, field: str, level: str,
           countries: list[str], rules: dict, partners: list[dict], language: str = "en") -> dict:
    """Eligibility comes first, then options, using the same rules as the bulk flow."""
    failed = [name.strip() for name in failed_subjects.split(",") if name.strip()] or ["not named"] if failed_status == "Yes" else []
    outcome = rule_failure(grade, failed, rules)
    if not outcome and failed_status == "Not known":
        outcome = {"decision": NEEDS_INFO, "missing": ["failed"], "reason": "Missing: " + TEXT["en"]["missing"]["failed"]}
    if not outcome:
        result = recommend(grade, field, level, countries, partners)
        outcome = result | {"decision": ELIGIBLE if result["options"] else REVIEW,
                            "reason": "Eligible" if result["options"] else NO_PARTNER}
    return outcome | {"body": reply_body(first_name, outcome, rules, language), "body_english": reply_body(first_name, outcome, rules)}


def run_batch(store: Store, ai, folder=APPLICATIONS, workers: int = 4):
    """Yields (done, total, record) as emails finish. Emails that failed are tried again next run."""
    state = store.state
    records = state.setdefault("applications", {})
    state["mode"] = state["mode"] or ai.mode
    rules, partners = active_rules(state), load_partners()
    emails = [read_eml(path) for path in list_email_files(folder)]
    pending = [e for e in emails if e.id not in records or records[e.id]["failed"]]
    done = len(emails) - len(pending)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_process_one, email, ai, rules, partners) for email in pending]
        for future in as_completed(futures):
            record = future.result()
            records[record["id"]] = record
            done += 1
            store.save()
            yield done, len(emails), record


def approve_group(store: Store, decision: str) -> int:
    """Marks a whole group as approved by staff. It doesn't send anything."""
    count = 0
    for record in store.state.get("applications", {}).values():
        if record["decision"] == decision and record["body"] and not record["approved"]:
            record["approved"] = True
            count += 1
    store.save()
    return count


def score(records: dict, expected: dict) -> dict:
    """Compares decisions with the answer key. A missing email counts as wrong."""
    right, grades_right, grades_total, wrong = 0, 0, 0, []
    for email_id, key in expected["emails"].items():
        record = records.get(email_id)
        got = record["decision"] if record else None
        if got == key["decision"]:
            right += 1
        else:
            wrong.append(f"{email_id}: got {got}, expected {key['decision']}" + (f" ({record['reason']})" if record else ""))
        wanted = key["extraction"]["last_semester_average"]
        if wanted is not None and key["extraction"]["scale_max"] in (None, 10):
            grades_total += 1
            if record and record["grade"] == wanted:
                grades_right += 1
    return {"decisions_right": right, "decisions_total": len(expected["emails"]),
            "grades_right": grades_right, "grades_total": grades_total, "wrong": wrong}
