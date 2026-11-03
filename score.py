"""Compares the last run with the answer key. Refuses to score the answer key against itself."""
import json
import sys

from erasmus_inbox.config import EXPECTED_FILE, OUTPUT, REPLAY_FILE, SCORE_FILE
from erasmus_inbox.tools import Store

FIELDS = ["doc_type", "host_university", "stay_start", "stay_end", "insurance_start", "insurance_end"]
DOC_NAMES = {"LearningAgreement": "Learning Agreement", "CertificateOfArrival": "Certificate of Arrival"}


def score(state: dict, expected: dict, replay: dict) -> dict:
    """Every demo email counts, so an email that failed to process counts as wrong."""
    routes_right = routes_total = fields_right = fields_total = 0
    failures = []
    demo = [e for e in expected["emails"] if e[0].isdigit() or e in state["emails"]]
    for email_id in demo:
        key = expected["emails"][email_id]
        got = sorted(i["status"] for i in state["items"] if i["email_id"] == email_id)
        routes_total += 1
        if got == sorted(key["statuses"]):
            routes_right += 1
        else:
            failures.append(f"{email_id}: routed {got}, expected {sorted(key['statuses'])}")
        answer = replay["emails"].get(state["emails"].get(email_id)) or {"documents": []}
        for field in ("first_name", "surname"):
            fields_total += 1
            if answer.get(field) == key["extraction"][field]:
                fields_right += 1
            else:
                failures.append(f"{email_id} {field}: got {answer.get(field)}, expected {key['extraction'][field]}")
        found = {d["attachment"]: d for d in answer["documents"]}
        for doc in key["extraction"]["documents"]:
            if doc["confidence"] < 0.5:
                continue  # Unreadable on purpose, so only its routing counts.
            for field in FIELDS:
                wanted, actual = doc[field], found.get(doc["attachment"], {}).get(field)
                if wanted is None and actual is None:
                    continue
                fields_total += 1
                if wanted == actual:
                    fields_right += 1
                else:
                    failures.append(f"{email_id} {doc['attachment']} {field}: got {actual}, expected {wanted}")
    return {"routes_right": routes_right, "routes_total": routes_total, "fields_right": fields_right,
            "fields_total": fields_total, "failures": failures}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    state = Store(OUTPUT).state
    if state["mode"] == "fixture":
        print("This run used the answer key, so a score would mean nothing. Run in live mode first.")
        return 1
    if not REPLAY_FILE.exists():
        print("No Gemini answers saved yet. Run in live mode first.")
        return 1
    expected = json.loads(EXPECTED_FILE.read_text(encoding="utf-8"))
    replay = json.loads(REPLAY_FILE.read_text(encoding="utf-8"))
    result = score(state, expected, replay)
    SCORE_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Routing correct: {result['routes_right']}/{result['routes_total']} emails")
    print(f"Fields correct:  {result['fields_right']}/{result['fields_total']}")
    for failure in result["failures"]:
        print(f"  - {failure}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
