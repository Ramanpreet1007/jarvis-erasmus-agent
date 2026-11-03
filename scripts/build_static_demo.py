"""Builds a browser-only copy of the website for GitHub Pages. Usage: python scripts/build_static_demo.py <target folder>

It calls the real website code once with saved Gemini answers (no API calls), records every answer the page can ask
for, and puts them inside one index.html. A small script in the page stands in for the server, so anyone can try the
demo from a link. Live Gemini, typed chatbot questions and checking students other than the sample are not available.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["GOOGLE_API_KEY"] = ""  # Saved answers only: this build never calls Gemini.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from erasmus_inbox.ai import ChatAI, ReplayAI  # noqa: E402
from erasmus_inbox.config import CHAT_QUESTIONS, OUTPUT  # noqa: E402
from erasmus_inbox.tools import Store  # noqa: E402
from web_app import PAGE, create_app  # noqa: E402

SAMPLE_TEXT = ("Hi, I'm Marta Greco and I'd like to apply for Erasmus. My average last semester was 8.3 and I have no failed subjects. "
               "I study Business, my English is B2, and I'd love to go to Italy or Denmark.\nMarta Greco\nmarta.greco@student.example")
MOCK = (Path(__file__).parent / "static_demo_server.js").read_text(encoding="utf-8")


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def main() -> None:
    target = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        client = TestClient(create_app(Store(Path(tmp) / "web"), Store(OUTPUT), replay=ReplayAI, live=ReplayAI, chat=ChatAI, pace=0))
        empty = ok(client.post("/api/reset"))
        lines = [json.loads(line) for line in client.post("/api/process").text.splitlines()]
        assert lines[-1] == {"finished": True}

        details, detail_index, settings = [], {}, {}
        for allow in (False, True):
            for half in range(10, 19):  # 5.0 to 9.0 in steps of 0.5, to keep the page small
                minimum = half / 2
                state = ok(client.post("/api/rules", json={"min_average": minimum, "allow_failed_subjects": allow}))
                rows = {}
                for row in state["records"]:
                    detail = ok(client.get(f"/api/record/{row['id']}"))
                    detail.pop("approved", None)
                    key = json.dumps(detail, sort_keys=True)
                    if key not in detail_index:
                        detail_index[key] = len(details)
                        details.append(detail)
                    rows[row["id"]] = detail_index[key]
                settings[f"{minimum:.1f}|{int(allow)}"] = {"records": state["records"], "places": state["places"], "details": rows}
        ok(client.post("/api/rules", json={"min_average": 6, "allow_failed_subjects": False}))

        form = ok(client.post("/api/advice/read", json={"text": SAMPLE_TEXT}))
        check_request = {"first_name": form["first_name"], "grade": form["grade"], "failed_status": form["failed_status"],
                         "failed_subjects": form["failed_subjects"], "field": form["field"], "level": form["level"],
                         "countries": form["countries"], "language": form["language"]}
        data = {
            "empty": empty, "stream": lines[:-1], "settings": settings, "details": details,
            "chat": {q: ok(client.post("/api/chat", json={"question": q})) for q in CHAT_QUESTIONS},
            "sample_text": SAMPLE_TEXT, "sample_form": form, "sample_check": check_request,
            "sample_advice": ok(client.post("/api/advice/check", json=check_request)),
            "documents": ok(client.get("/api/documents")), "cheat": ok(client.get("/api/cheat")),
        }

    page = PAGE.read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    inject = f'<script id="demo-data" type="application/json">{payload}</script>\n<script>\n{MOCK}\n</script>\n<script>'
    assert page.count("<script>") == 1
    page = page.replace("<script>", inject, 1)
    slider = 'min="5" max="9" step="0.1"'
    assert page.count(slider) == 1
    page = page.replace(slider, 'min="5" max="9" step="0.5"')
    target.mkdir(parents=True, exist_ok=True)
    (target / "index.html").write_text(page, encoding="utf-8")
    (target / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Saved {target / 'index.html'} ({len(page) // 1024} KB, {len(details)} distinct records)")


if __name__ == "__main__":
    main()
