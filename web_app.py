"""The modern website for the agent. Run with: python web_app.py, then open http://localhost:8000

It only calls the agent's existing code. It has no way to send email, and it's bound to this computer.
"""
import json
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from erasmus_inbox import applications
from erasmus_inbox.advisor import LEVELS, load_partners
from erasmus_inbox.ai import ChatAI, GeminiAI, ReplayAI
from erasmus_inbox.config import APPLICATIONS, APPLICATIONS_SCORE, CHAT_QUESTIONS, OUTPUT, has_api_key
from erasmus_inbox.pipeline import Pipeline
from erasmus_inbox.tools import Store, read_eml
from erasmus_inbox.validate import DOC_LABELS, insurance_problem, next_deadline, readiness

PAGE = Path(__file__).parent / "web" / "index.html"
REPLAY_PACE_SECONDS = 0.08
LIST_FIELDS = ("id", "sender", "subject", "name", "grade", "failed_subjects", "decision", "reason", "approved", "language", "failed")


class Rules(BaseModel):
    min_average: float = Field(ge=0, le=10)
    allow_failed_subjects: bool


class Approval(BaseModel):
    decision: str | None = None
    id: str | None = None
    checked: bool = False


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class EmailText(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class Advice(BaseModel):
    first_name: str = Field(default="", max_length=100)
    grade: float = Field(ge=0, le=10)
    failed_status: str = Field(pattern="^(No|Yes|Not known)$")
    failed_subjects: str = Field(default="", max_length=500)
    field: str = Field(max_length=100)
    level: str
    countries: list[str] = Field(default_factory=list, max_length=20)
    language: str = Field(default="en", max_length=10)


def counts(records: dict) -> dict:
    values = list(records.values())
    numbers = {key: sum(r["decision"] == key for r in values) for key in ("eligible", "not_eligible", "needs_info")}
    return numbers | {"person": sum(r["decision"] in ("review", "other") for r in values), "total": len(values),
                      "unread": sum(r["failed"] for r in values)}


def create_app(store: Store, documents_store: Store, replay=ReplayAI, live=GeminiAI, chat=ChatAI, pace=REPLAY_PACE_SECONDS) -> FastAPI:
    app = FastAPI(title="Erasmus Inbox Agent", docs_url=None, redoc_url=None, openapi_url=None)
    busy = threading.Lock()  # One change at a time, so a run and a rule change can't overwrite each other.

    def records() -> dict:
        return store.state.setdefault("applications", {})

    def change(action):
        if not busy.acquire(blocking=False):
            raise HTTPException(409, "The inbox is being processed. Try again when it finishes.")
        try:
            return action()
        finally:
            busy.release()

    def summary() -> dict:
        score = json.loads(APPLICATIONS_SCORE.read_text(encoding="utf-8")) if APPLICATIONS_SCORE.exists() else None
        partners = load_partners()
        return {
            "counts": counts(records()), "rules": applications.active_rules(store.state), "live_available": has_api_key(),
            "records": [{key: r.get(key) for key in LIST_FIELDS} for r in sorted(records().values(), key=lambda r: r["id"])],
            "places": applications_places(records(), partners), "score": score, "questions": CHAT_QUESTIONS,
            "levels": LEVELS, "fields": sorted({f for p in partners for f in p["fields"]}),
            "countries": sorted({p["country"] for p in partners}),
        }

    def applications_places(values: dict, partners: list[dict]) -> dict:
        plan = applications.allocate(values, partners)
        return {"universities": plan["universities"], "placed": plan["placed"], "waiting": len(plan["waiting"])}

    @app.get("/")
    def page():
        return FileResponse(PAGE, media_type="text/html")

    @app.get("/api/state")
    def state():
        return summary()

    @app.post("/api/process")
    def process(mode: str = "replay"):
        if mode == "live" and not has_api_key():
            raise HTTPException(400, "Live Gemini needs a key in .env.")
        if not busy.acquire(blocking=False):
            raise HTTPException(409, "The inbox is already being processed.")
        ai, workers, wait = (live(), 4, 0) if mode == "live" else (replay(), 1, pace)

        def lines():
            try:
                for done, total, record in applications.run_batch(store, ai, APPLICATIONS, workers=workers):
                    row = {key: record.get(key) for key in LIST_FIELDS}
                    yield json.dumps({"done": done, "total": total, "record": row, "counts": counts(records())}) + "\n"
                    time.sleep(wait)
                yield json.dumps({"finished": True}) + "\n"
            except Exception as error:
                yield json.dumps({"error": f"Processing stopped: {type(error).__name__}"}) + "\n"
            finally:
                busy.release()

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @app.post("/api/rules")
    def rules(new: Rules):
        changed = change(lambda: applications.apply_rules(store, new.model_dump()))
        return summary() | {"changed": changed}

    @app.post("/api/approve")
    def approve(request: Approval):
        def action():
            if request.id:
                record = records().get(request.id)
                if not record or not record["body"]:
                    raise HTTPException(404, "There's no reply to approve for this email.")
                decision = record["decision"]
            else:
                decision = request.decision
            if decision == "not_eligible" and not request.checked:
                raise HTTPException(400, "Please confirm you've checked the not eligible replies.")
            if request.id:
                records()[request.id]["approved"] = True
                store.save()
                return 1
            if decision not in ("eligible", "needs_info", "not_eligible"):
                raise HTTPException(400, "This group can't be approved in bulk.")
            return applications.approve_group(store, decision)

        approved_now = change(action)
        return summary() | {"approved_now": approved_now}

    @app.post("/api/reset")
    def reset():
        def action():
            store.state["applications"] = {}
            store.state.pop("rules", None)
            store.save()

        change(action)
        return summary()

    @app.get("/api/record/{email_id}")
    def record(email_id: str):
        found = records().get(email_id)
        if not found:
            raise HTTPException(404, "Email not found.")
        return {key: value for key, value in found.items() if key not in ("source", "read")}

    @app.post("/api/chat")
    def ask(request: Question):
        return Pipeline(store, chat()).answer(request.question)

    @app.post("/api/advice/read")
    def advice_read(request: EmailText):
        rules_now = applications.active_rules(store.state)
        return applications.form_from_email(applications.email_from_text(request.text), chat(), rules_now, load_partners())

    @app.post("/api/advice/check")
    def advice_check(request: Advice):
        if request.level not in LEVELS:
            raise HTTPException(400, "Please choose an English level.")
        return applications.advise(request.first_name, request.grade, request.failed_status, request.failed_subjects, request.field,
                                   request.level, request.countries, applications.active_rules(store.state), load_partners(), request.language)

    @app.get("/api/documents")
    def documents():
        saved = Store(documents_store.output).state
        students = sorted(saved["students"].values(), key=lambda s: ["red", "amber", "green"].index(readiness(s)["level"]))
        return {
            "items": [{"sender": i["sender"], "subject": i["subject"], "original": i["original"], "document": i["document"],
                       "new_name": (i["new_name"] or "").split("/")[-1], "status": i["status"], "resolved": i["resolved"]} for i in saved["items"]],
            "students": [{
                "name": f"{s['first_name']} {s['surname']}", "university": s.get("host_university"), **{k: readiness(s)[k] for k in ("level", "summary", "next")},
                "documents": [DOC_LABELS[d] for d in s["documents"]], "deadline": next_deadline(s), "insurance_problem": insurance_problem(s),
            } for s in students],
        }

    @app.get("/api/cheat")
    def cheat():
        found = next((r for _, r in sorted(records().items()) if r["decision"] == "review" and "instructions" in r["reason"]), None)
        path = APPLICATIONS / f"{found['id']}.eml" if found else None
        if not path or not path.exists():
            return {"found": False}
        lines = read_eml(path).attachments[0].text().splitlines()
        hidden = [line for line in lines if "ignore" in line.casefold()]
        return {"found": True, "id": found["id"], "name": found["name"], "visible": [line for line in lines if line not in hidden], "hidden": hidden}

    return app


app = create_app(Store(OUTPUT / "web"), Store(OUTPUT))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
