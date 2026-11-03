"""Ranks destinations for an applicant using fixed, explainable rules. Staff approve the advice."""
import json

from .config import CONFIDENCE_THRESHOLD, PARTNERS_FILE
from .validate import find_injection, quote_found

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]
REACH_GAP = 0.5
STRONG_MARGIN = 0.5
MAX_OPTIONS = 5


def load_partners() -> list[dict]:
    return json.loads(PARTNERS_FILE.read_text(encoding="utf-8"))


def recommend(grade: float, field: str, level: str, countries: list[str], partners: list[dict] | None = None) -> dict:
    """Grades on a 0-10 scale. Returns the best options and why others were left out."""
    if not 0 <= grade <= 10:
        raise ValueError("Grade must be on a 0 to 10 scale.")
    options, ruled_out = [], []
    for partner in partners if partners is not None else load_partners():
        if field not in partner["fields"]:
            continue
        name = partner["university"]
        margin = round(grade - partner["min_grade"], 2)
        if LEVELS.index(level) < LEVELS.index(partner["min_level"]):
            ruled_out.append(f"{name}: needs {partner['language']} {partner['min_level']}, you have {level}")
        elif margin < -REACH_GAP:
            ruled_out.append(f"{name}: needs a grade of {partner['min_grade']:g}, you have {grade:g}")
        else:
            fit = "Strong match" if margin >= STRONG_MARGIN else "Good match" if margin >= 0 else "Reach"
            preferred = partner["country"] in countries
            reasons = [f"Minimum grade {partner['min_grade']:g}, your grade {grade:g}",
                       f"{partner['language']} {partner['min_level']} required, you have {level}",
                       f"{partner['places']} places (sample figure)"]
            if preferred:
                reasons.insert(0, f"In your preferred country: {partner['country']}")
            options.append({**partner, "fit": fit, "preferred": preferred, "margin": margin, "reasons": reasons})
    # Preferred countries come first, then the safest options.
    options.sort(key=lambda o: (not o["preferred"], o["fit"] == "Reach", -o["margin"], -o["places"], o["university"]))
    has_preferred = any(o["preferred"] for o in options)
    return {"options": options[:MAX_OPTIONS], "ruled_out": ruled_out,
            "no_preferred_match": bool(countries) and not has_preferred}


def read_grade(ai, attachment) -> dict:
    """Reads the grade. "grade" stays empty unless it passes every check, and staff still confirm it."""
    result = {"grade": None, "first_name": None, "quote": None, "problems": [], "warnings": []}
    if not attachment.mime_type:
        result["problems"].append("Please upload a PDF, JPG or PNG.")
        return result
    try:
        read = ai.read_transcript(attachment)
    except Exception as error:
        result["problems"].append(f"The AI could not read this transcript: {type(error).__name__}. Type the grade by hand.")
        return result
    source = attachment.text()
    if read.suspicious_content or find_injection(source):
        result["problems"].append("This document contains instructions aimed at the AI, so a person must check it.")
        return result
    result["first_name"], result["quote"] = read.first_name, read.quote
    if read.confidence < CONFIDENCE_THRESHOLD:
        result["problems"].append(f"Low confidence ({read.confidence:.0%}): the transcript may be hard to read.")
    if read.average_grade is None:
        result["problems"].append("No overall average is printed on this transcript.")
    elif not 0 <= read.average_grade <= 10:
        result["problems"].append(f"The grade {read.average_grade:g} doesn't fit the 0 to 10 scale.")
    if read.scale_max is not None and read.scale_max != 10:
        result["problems"].append(f"This transcript uses a scale up to {read.scale_max:g}, and only 0 to 10 is supported.")
    if not read.quote:
        result["problems"].append("The AI gave no quote showing where the grade came from.")
    elif source and not quote_found(read.quote, source):
        result["problems"].append("The quote for this grade wasn't found in the document.")
    if result["problems"]:
        return result
    result["grade"] = read.average_grade
    if read.scale_max is None:
        result["warnings"].append("No grading scale is printed, so check that this is a 0 to 10 grade.")
    if not source:
        result["warnings"].append("This is an image, so compare the grade with the document.")
    return result


def advice_body(name: str, result: dict) -> str:
    """The draft staff review. Nothing here promises a place."""
    first = name.strip() or "there"
    if not result["options"]:
        return (f"Hi {first},\n\nBased on your details, we couldn't find a suitable option in our current list. "
                "Please book a meeting with the Erasmus office so we can look at your choices together.\n\n"
                "Kind regards,\nErasmus Office")
    lines = [f"Hi {first},\n"]
    if result["no_preferred_match"]:
        lines.append("We couldn't find a suitable option in your preferred countries, so here are the closest alternatives.\n")
    lines.append("Based on your grades and preferences, these are your best options to consider:\n")
    for number, option in enumerate(result["options"], start=1):
        lines.append(f"{number}. {option['university']}, {option['city']}, {option['country']} ({option['fit']})")
    lines.append("\nA place isn't guaranteed, because selection depends on all applicants and the final decision "
                 "is made by the office. The choice is yours, so tell us which options you'd like to apply for.\n")
    lines.append("Kind regards,\nErasmus Office")
    return "\n".join(lines)
