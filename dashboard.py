"""The demo screen. Run with: streamlit run dashboard.py"""
import csv
import html
import io
import json
import os
import time

import streamlit as st
from openpyxl import load_workbook

from erasmus_inbox import advisor, applications, mailbox
from erasmus_inbox.config import APPLICATIONS, CHAT_QUESTIONS
from erasmus_inbox.tools import list_email_files, parse_eml, read_eml

SAMPLE_APPLICATION = ("Hi, I'm Marta Greco and I'd like to apply for Erasmus. My average last semester was 8.3 and I have no failed subjects. "
                      "I study Business, my English is B2, and I'd love to go to Italy or Denmark.\nMarta Greco\nmarta.greco@student.example")
from erasmus_inbox.ai import FixtureAI, GeminiAI, ReplayAI, replay_ready
from erasmus_inbox.config import APPLICATIONS_SCORE, CHALLENGE, DEMO_QUESTIONS, INBOX, OUTPUT, SCORE_FILE, has_api_key
from erasmus_inbox.pipeline import Pipeline
from erasmus_inbox.tools import Attachment, Store, list_email_files, load_measured, read_eml, save_measured
from erasmus_inbox.validate import DOC_LABELS, insurance_problem, next_deadline, readiness

STATUS = {"filed": "✅ Filed", "review": "🔴 Needs a person", "answered": "💬 Answer drafted", "handoff": "🙋 Passed to staff"}
LEVEL = {"red": "🔴 Action needed", "amber": "🟡 Missing documents", "green": "🟢 Ready"}
MODES = {"Live Gemini": GeminiAI, "Replay of saved Gemini run": ReplayAI, "Test data (not AI)": FixtureAI}
FIELD_LABELS = {"host_university": "Host university", "stay_start": "Stay start", "stay_end": "Stay end",
                "insurance_start": "Insurance start", "insurance_end": "Insurance end"}

st.set_page_config(page_title="Erasmus Inbox Agent", page_icon="📬", layout="wide")
st.markdown("""<style>
:root {
  --ink:#14213d; --muted:#5b6780; --line:#e2e7f1; --surface:#ffffff; --page:#f4f6fb; --brand:#1a56db; --brand-dark:#10307a;
  --good:#047857; --good-bg:#d1fae5; --bad:#b91c1c; --bad-bg:#fee2e2; --warn:#92400e; --warn-bg:#fef3c7;
  --info:#1e40af; --info-bg:#dbeafe; --plain:#475569; --plain-bg:#e8ecf3;
  --shadow:0 1px 2px rgba(20,33,61,.06), 0 4px 14px rgba(20,33,61,.06);
}
.stApp {background:var(--page);}
[data-testid="stHeader"] {background:transparent;}
[data-testid="stToolbar"] {visibility:hidden;}
.block-container {padding-top:1.4rem; max-width:1280px;}
h2, h3 {color:var(--ink); letter-spacing:-.01em;}

.hero {background:linear-gradient(120deg, var(--brand-dark), var(--brand)); color:#fff; border-radius:18px; padding:1.5rem 1.8rem;
       display:flex; flex-wrap:wrap; gap:1rem; justify-content:space-between; align-items:center; box-shadow:var(--shadow); margin-bottom:1rem;}
.hero h1 {color:#fff; font-size:clamp(1.6rem,3vw,2.3rem); font-weight:800; margin:0; padding:0;}
.hero p {color:#dbe6ff; margin:.25rem 0 0; font-size:1rem;}
.steps {display:flex; flex-wrap:wrap; align-items:center; gap:.4rem;}
.steps span {background:rgba(255,255,255,.16); border:1px solid rgba(255,255,255,.28); color:#fff; border-radius:999px; padding:.3rem .8rem; font-size:.85rem; font-weight:600;}
.steps i {color:#dbe6ff; font-style:normal;}

.stTabs [role="tablist"] {gap:.3rem; background:var(--surface); border-radius:14px; padding:.35rem; box-shadow:var(--shadow); overflow-x:auto;}
.stTabs [data-baseweb="tab"] {border-radius:10px; padding:.45rem .9rem; height:auto; white-space:nowrap;}
.stTabs [data-baseweb="tab"] p {font-weight:600; color:var(--muted);}
.stTabs [aria-selected="true"] {background:var(--info-bg);}
.stTabs [aria-selected="true"] p {color:var(--brand);}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {display:none;}

.cards {display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:.8rem; margin:.6rem 0 1rem;}
.stat {background:var(--surface); border-radius:14px; padding:1rem 1.1rem; box-shadow:var(--shadow); border-top:4px solid var(--accent, var(--plain));}
.stat b {display:block; color:var(--accent, var(--ink)); font-size:2.3rem; line-height:1.1; font-weight:800;}
.stat span {color:var(--muted); font-size:.88rem; font-weight:600;}
[data-testid="stMetric"] {background:var(--surface); border-radius:14px; padding:.8rem 1rem; box-shadow:var(--shadow); border-top:4px solid var(--brand);}
[data-testid="stMetricValue"] {font-size:2rem; font-weight:800; color:var(--ink);}

.flow {display:flex; flex-wrap:wrap; gap:.6rem; align-items:stretch; margin:.5rem 0 1.5rem;}
.flow div {flex:1 1 170px; border-radius:14px; padding:.9rem 1rem; background:var(--surface); color:var(--ink); box-shadow:var(--shadow);}
.flow b {display:block; margin-bottom:.25rem; color:var(--brand);}

.grid-wrap {overflow:auto; max-height:460px; background:var(--surface); border-radius:14px; box-shadow:var(--shadow); margin-bottom:1rem;}
table.grid {border-collapse:collapse; width:100%; font-size:.92rem; margin:0;}
table.grid th {position:sticky; top:0; z-index:1; background:#eef2f9; color:var(--ink); text-align:left; padding:.6rem .8rem; border:0; white-space:nowrap;}
table.grid td {color:var(--ink); padding:.55rem .8rem; border:0; border-bottom:1px solid var(--line); overflow-wrap:anywhere;}
table.grid tr:last-child td {border-bottom:0;}
table.grid tbody tr:hover td {background:#f7f9fd;}
.pill {display:inline-block; border-radius:999px; padding:.14rem .65rem; font-size:.82rem; font-weight:700; white-space:nowrap;}
.pill.good {color:var(--good); background:var(--good-bg);} .pill.bad {color:var(--bad); background:var(--bad-bg);}
.pill.warn {color:var(--warn); background:var(--warn-bg);} .pill.info {color:var(--info); background:var(--info-bg);}
.pill.plain {color:var(--plain); background:var(--plain-bg);}

.reply {background:var(--surface); border:1px solid var(--line); border-left:4px solid var(--brand); border-radius:12px; padding:1rem 1.2rem;
        color:var(--ink); line-height:1.55; margin:.4rem 0 .8rem;}
.stButton button, .stDownloadButton button {border-radius:10px; font-weight:600;}
.stButton button[kind="primary"] {background:linear-gradient(120deg, var(--brand-dark), var(--brand)); border:0; box-shadow:var(--shadow);}
[data-testid="stExpander"] details {background:var(--surface); border:0; border-radius:14px; box-shadow:var(--shadow);}
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]) {border-radius:14px;}
[data-testid="stAlert"] > div {border-radius:12px;}
[data-testid="stFileUploaderDropzone"] {background:var(--surface);}
.stTextInput input, .stNumberInput input, .stTextArea textarea, [data-baseweb="select"] > div {background:var(--surface);}
</style>""", unsafe_allow_html=True)


PILL_COLUMNS = {"Decision", "Status", "Result", "Reply"}
PILL_COLORS = {"✅": "good", "🟢": "good", "☑": "good", "Approved": "good", "OK": "good", "❌": "bad", "🔴": "bad", "🔥": "bad",
               "📝": "warn", "🟡": "warn", "⚠": "warn", "🙋": "warn", "💬": "info", "Draft": "info", "📨": "plain", "None": "plain"}


def table_cell(column: str, value) -> str:
    text = html.escape("" if value is None else str(value))
    if column in PILL_COLUMNS:
        for start, color in PILL_COLORS.items():
            if text.startswith(start):
                return f'<span class="pill {color}">{text}</span>'
    return text


def show_reply(body: str) -> None:
    """A drafted reply, shown the way the student would read it. Email content is escaped."""
    st.markdown('<div class="reply">' + html.escape(body).replace("\n", "<br>") + "</div>", unsafe_allow_html=True)


def table_html(rows: list[dict]) -> str:
    """Email content is untrusted, so every value is escaped."""
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in rows[0])
    body = "".join(
        "<tr>" + "".join(f"<td>{table_cell(c, v)}</td>" for c, v in row.items()) + "</tr>"
        for row in rows
    )
    return f'<div class="grid-wrap"><table class="grid"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def default_mode() -> str:
    if has_api_key():
        return "Live Gemini"
    return "Replay of saved Gemini run" if replay_ready() else "Test data (not AI)"


def counts(state: dict) -> dict:
    items = state["items"]
    problems = sum(1 for s in state["students"].values() if insurance_problem(s))
    open_handoffs = sum(
        i["status"] == "handoff" and not state["drafts"].get(f"{i['email_id']}_reply", {}).get("approved")
        for i in items
    )
    return {
        "emails": len({i["email_id"] for i in items}),
        "filed": sum(i["status"] == "filed" for i in items),
        "person": sum(i["status"] == "review" and not i["resolved"] for i in items) + open_handoffs + problems,
        "drafts": sum(not d["approved"] for d in state["drafts"].values()),
    }


def run_time(state: dict) -> str:
    """Only real Gemini time, never the pauses or instant test runs."""
    if state["mode"] == "live" and state["seconds"] is not None:
        return f"{state['seconds']} s"
    measured = load_measured().get("live_seconds")
    return f"{measured} s" if state["mode"] == "replay" and measured else "–"


def result_label(item: dict, state: dict) -> str:
    if item["resolved"]:
        return "☑️ Resolved by staff"
    student = state["students"].get(item["student"]) if item["student"] else None
    if item["status"] == "filed" and student and insurance_problem(student) and item["document"] in ("Insurance", "Learning Agreement"):
        return "⚠️ Filed: insurance gap"
    return STATUS[item["status"]]


def show_counts(slot, state: dict) -> None:
    numbers = counts(state)
    with slot.container():
        columns = st.columns(5)
        columns[0].metric("Emails processed", numbers["emails"])
        columns[1].metric("Documents filed", numbers["filed"])
        columns[2].metric("Need a person", numbers["person"])
        columns[3].metric("Drafts to approve", numbers["drafts"])
        columns[4].metric("AI run time", run_time(state))
    caught = [s for s in state["students"].values() if insurance_problem(s)]
    for student in caught:
        slot_text = f"**Caught:** {student['first_name']} {student['surname']}. {insurance_problem(student)}. Fix drafted for staff."
        catch_slot.error(slot_text, icon="🚨")


def show_results(slot, state: dict) -> None:
    rows = [{
        "Email": item["sender"].split("@")[0],
        "Subject": item["subject"] or "(no subject)",
        "Original file": item["original"],
        "Document found": item["document"],
        "New name": {"review": "Held for review", "answered": f"FAQ {item['new_name']}"}.get(item["status"], item["new_name"].split("/")[-1]),
        "Result": result_label(item, state),
    } for item in state["items"]]
    if rows:
        slot.markdown(table_html(rows), unsafe_allow_html=True)
    else:
        slot.info("Nothing processed yet. Press **Process inbox**.")


def open_path(path) -> None:
    if path.exists():
        os.startfile(path)
    else:
        st.toast("Nothing there yet. Process the inbox first.")


def evidence_lines(evidence: list[dict]) -> None:
    for item in evidence:
        badge = {True: "✅ found in document", False: "⚠️ not matched to text", None: "📷 read from image"}[item.get("verified")]
        label = FIELD_LABELS.get(item["field"], item["field"])
        file_note = f" · {item['file']}" if item.get("file") else ""
        st.markdown(f"- **{label}:** “{item['quote']}”  \n  :gray[{badge}{file_note}]")


def show_draft(draft: dict, store: Store) -> None:
    st.markdown(f"**{draft['kind']}** · to {draft['to']}")
    if draft.get("question"):
        st.caption(f"Student asked: {draft['question']}")
    show_reply(draft["body"])
    if draft.get("language") not in (None, "English", "Unknown") and draft.get("english"):
        st.caption(f"{draft['language']} reply. English for staff: {draft['english']}")
    if draft.get("faq_id"):
        st.caption(f"Source: FAQ {draft['faq_id']}")
    if draft["approved"]:
        note = "Saved to Gmail Drafts for staff to review and send." if draft.get("in_gmail") else "The agent cannot send email."
        st.success(f"Approved by staff. {note}")
    elif st.button("Approve draft", key=f"approve_{draft['id']}"):
        store.approve_draft(draft["id"])
        if mailbox.configured() and mailbox.from_mailbox(draft, store):
            try:
                mailbox.save_gmail_draft(draft)
                draft["in_gmail"] = True
                store.save()
            except mailbox.MailboxError as error:
                st.session_state["mail_error"] = str(error)
        st.rerun()


store = Store(OUTPUT)
state = store.state

st.markdown("""<div class="hero"><div><h1>📬 Erasmus Inbox Agent</h1>
<p>It does the routine work, catches the mistakes people miss, and never sends anything without a human.</p></div>
<div class="steps"><span>Gemini reads</span><i>→</i><span>Python checks</span><i>→</i><span>A person approves</span></div></div>""", unsafe_allow_html=True)

with st.expander("Demo controls"):
    left, right = st.columns([3, 1])
    options = list(MODES)
    if not has_api_key():
        options.remove("Live Gemini")
    if not replay_ready():
        options.remove("Replay of saved Gemini run")
    mode = left.selectbox("AI source", options, index=options.index(default_mode()))
    saved_manual = float(load_measured().get("manual_minutes", 0))
    manual = left.number_input("Measured time to do the 10 emails by hand (minutes, 0 = not measured)", min_value=0.0, value=saved_manual, step=1.0)
    if manual != saved_manual:
        save_measured(manual_minutes=manual)
    if right.button("Reset demo", width="stretch"):
        try:
            store.reset()
            st.session_state.pop("asked", None)
            st.rerun()
        except PermissionError:
            st.error("Close students.xlsx or any open document first, then reset.")

ai = MODES[mode]()
pipeline = Pipeline(store, ai)
pipeline.sync_pending_excel()
active_mode = state["mode"] or ai.mode
if active_mode == "fixture":
    st.warning("Test data mode: these answers come from the answer key, not from AI. Add a Gemini key for a real run.")
elif active_mode == "replay":
    st.info("Replay mode: using saved answers from an earlier real Gemini run.")
for warning in state["warnings"]:
    st.warning(warning)

def check_gmail() -> None:
    try:
        if mailbox.check_mailbox(pipeline, store):
            st.rerun()
        st.session_state.pop("mail_error", None)
    except mailbox.MailboxError as error:
        st.session_state["mail_error"] = str(error)


@st.fragment(run_every=20)
def gmail_watcher() -> None:
    if st.session_state.get("watch_gmail"):
        check_gmail()


if mailbox.configured():
    label = mailbox.settings()["label"]
    if ai.mode == "live":
        watch_col, check_col = st.columns([3, 1])
        watch_col.toggle(f"Watch Gmail label “{label}” (checks every 20 seconds)", key="watch_gmail")
        if check_col.button("Check Gmail now", width="stretch"):
            check_gmail()
        gmail_watcher()
    else:
        st.caption(f"Gmail label “{label}” is connected, but it's only read in Live Gemini mode.")
if st.session_state.get("mail_error"):
    st.error(st.session_state["mail_error"])

apps_tab, inbox_tab, ready_tab, review_tab, ask_tab, advice_tab, how_tab = st.tabs(
    ["📥 Applications", "🗂️ Inbox to filed", "✅ Student readiness", "📝 Review and drafts", "💬 Ask the agent", "🎯 Applicant advice", "ℹ️ How it works"]
)

APP_LABELS = {"eligible": "✅ Eligible", "not_eligible": "❌ Not eligible", "needs_info": "📝 Needs details",
              "review": "🔴 Needs a person", "other": "📨 Not an application"}


def app_counts(records: dict) -> dict:
    values = list(records.values())
    return {key: sum(r["decision"] == key for r in values) for key in APP_LABELS} | {"total": len(values)}


def show_app_counts(slot, records: dict) -> None:
    numbers = app_counts(records)
    cards = [("Applications read", numbers["total"], "var(--brand)"), ("Eligible, options ready", numbers["eligible"], "var(--good)"),
             ("Not eligible", numbers["not_eligible"], "var(--bad)"), ("Needs details", numbers["needs_info"], "var(--warn)"),
             ("Needs a person", numbers["review"] + numbers["other"], "var(--info)")]
    slot.markdown('<div class="cards">' + "".join(
        f'<div class="stat" style="--accent:{color}"><b>{number}</b><span>{label}</span></div>' for label, number, color in cards
    ) + "</div>", unsafe_allow_html=True)


def app_rows(records: list[dict]) -> list[dict]:
    return [{
        "Student": r["name"] or r["sender"],
        "Average": "" if r["grade"] is None else f"{r['grade']:g}",
        "Failed": ", ".join(r["failed_subjects"]),
        "Decision": APP_LABELS[r["decision"]],
        "Reason": r["reason"],
        "Reply": "Approved" if r["approved"] else "Draft ready" if r["body"] else "None",
    } for r in sorted(records, key=lambda r: r["id"])]


with apps_tab:
    records = state.setdefault("applications", {})
    st.write("Reads every Erasmus application, checks eligibility and drafts each reply. Staff approve what goes out.")
    rules = applications.active_rules(state)
    st.warning("The rules, universities and places are sample data made up for this demo.")
    rule_col, failed_col = st.columns([3, 1])
    minimum = rule_col.slider("Office rule: minimum average", 5.0, 9.0, float(rules["min_average"]), 0.1)
    allow_failed = failed_col.toggle("Allow failed subjects", rules["allow_failed_subjects"])
    if (minimum, allow_failed) != (rules["min_average"], rules["allow_failed_subjects"]):
        new_rules = {"min_average": minimum, "allow_failed_subjects": allow_failed}
        st.session_state["rule_changes"] = applications.apply_rules(store, new_rules)
        st.rerun()
    if "rule_changes" in st.session_state:
        st.success(f"{st.session_state.pop('rule_changes')} decisions changed instantly, with no new AI calls. Changed replies need approving again.")
    apps_slot = st.empty()
    show_app_counts(apps_slot, records)
    use_mailbox = mailbox.configured() and ai.mode == "live"
    if use_mailbox:
        st.success(f"Reading the connected mailbox: {mailbox.settings()['email']}, label “{mailbox.settings()['label']}”. Read-only.")
    else:
        st.info("Reading the sample inbox of 100 fake emails. No real mailbox is connected.")
        with st.expander("See the inbox"):
            st.markdown(table_html([{"From": e.sender, "Subject": e.subject, "Attachment": ", ".join(a.filename for a in e.attachments)}
                                    for e in map(read_eml, list_email_files(APPLICATIONS))]), unsafe_allow_html=True)
    if st.button("▶ Read inbox and process applications", type="primary"):
        bar = st.progress(0.0, "Starting...")
        table_slot = st.empty()
        try:
            folder = mailbox.fetch_applications(store) if use_mailbox else APPLICATIONS
            for done, total, record in applications.run_batch(store, ai, folder):
                bar.progress(done / total, f"{done} of {total} emails read")
                show_app_counts(apps_slot, records)
                table_slot.markdown(table_html(app_rows(records.values())[:8]), unsafe_allow_html=True)
            st.rerun()
        except mailbox.MailboxError as error:
            st.error(str(error))
    if records:
        groups = {label: key for key, label in APP_LABELS.items()}
        choice = st.selectbox("Show", ["All"] + list(groups))
        shown = [r for r in records.values() if choice == "All" or r["decision"] == groups[choice]]
        if shown:
            st.markdown(table_html(app_rows(shown)), unsafe_allow_html=True)

        st.subheader("Places at each university")
        st.caption("A preview: highest average first, using the options we ranked for each student. Students haven't chosen yet, and staff decide the real allocation.")
        plan = applications.allocate(records, advisor.load_partners())
        placed_col, waiting_col = st.columns(2)
        placed_col.metric("Could get a place", plan["placed"])
        waiting_col.metric("Eligible, but no place left", len(plan["waiting"]))
        st.markdown(table_html([{
            "University": u["university"], "Places": u["places"], "Top choice for": u["first_choice"], "Places filled": u["filled"],
            "Lowest average placed": "" if u["lowest"] is None else f"{u['lowest']:g}",
            "Status": "⚠ Tie at the cut-off, staff decide" if u["tie"] else "🔥 More demand than places" if u["first_choice"] > u["places"] else "OK",
        } for u in sorted(plan["universities"], key=lambda u: u["places"] - u["first_choice"])]), unsafe_allow_html=True)

        st.subheader("Approve replies")
        st.caption("Approving marks a reply as ready. The agent can't send email.")
        approve_eligible, approve_details, approve_rejected = st.columns(3)
        if approve_eligible.button(f"Approve all eligible ({sum(r['decision'] == 'eligible' and not r['approved'] for r in records.values())})", width="stretch"):
            applications.approve_group(store, "eligible")
            st.rerun()
        if approve_details.button(f"Approve all detail requests ({sum(r['decision'] == 'needs_info' and not r['approved'] for r in records.values())})", width="stretch"):
            applications.approve_group(store, "needs_info")
            st.rerun()
        checked = approve_rejected.checkbox("I've checked the not eligible replies")
        if approve_rejected.button(f"Approve not eligible ({sum(r['decision'] == 'not_eligible' and not r['approved'] for r in records.values())})", width="stretch", disabled=not checked):
            applications.approve_group(store, "not_eligible")
            st.rerun()

        waiting = [r for r in records.values() if r["approved"] and not r.get("in_drafts") and r["id"].startswith("gmail_")]
        if mailbox.configured() and waiting and st.button(f"Save {len(waiting)} approved replies to the mailbox Drafts"):
            try:
                for r in waiting:
                    mailbox.save_gmail_draft({"to": r["sender"], "subject": f"Re: {r['subject']}", "body": r["body"]})
                    r["in_drafts"] = True
                    store.save()
                st.rerun()
            except mailbox.MailboxError as error:
                st.error(str(error))

        st.subheader("Check one email")
        by_label = {f"{r['id']} · {r['name'] or r['sender']} · {APP_LABELS[r['decision']]}": r for r in sorted(records.values(), key=lambda r: r["id"])}
        record = by_label[st.selectbox("Email", list(by_label))]
        st.write(f"**Decision:** {APP_LABELS[record['decision']]}. {record['reason']}")
        if record["quote"]:
            st.caption(f"Grade from the email: “{record['quote']}”")
        if record["options"]:
            for number, option in enumerate(record["options"], start=1):
                st.markdown(f"{number}. **{option['university']}**, {option['country']} · {option['fit']}")
        if record["body"]:
            show_reply(record["body"])
            if record["body"] != record["body_english"]:
                with st.expander("English copy for staff"):
                    show_reply(record["body_english"])
            if record["approved"]:
                st.success("Approved by staff. Nothing has been sent.")
            elif st.button("Approve this reply"):
                record["approved"] = True
                store.save()
                st.rerun()
        else:
            st.info("No automatic reply. A person should handle this email.")

        lines = [["id", "student", "email", "average", "failed", "decision", "reason", "approved", "reply"]]
        lines += [[r["id"], r["name"], r["sender"], r["grade"], "; ".join(r["failed_subjects"]), r["decision"], r["reason"], r["approved"], r["body"] or ""]
                  for r in sorted(records.values(), key=lambda r: r["id"])]
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerows([[applications.excel_safe_cell(v) for v in line] for line in lines])
        st.download_button("Download all results (CSV)", output.getvalue().encode("utf-8-sig"), "erasmus_applications.csv", "text/csv")
    if APPLICATIONS_SCORE.exists() and state["mode"] != "fixture":
        result = json.loads(APPLICATIONS_SCORE.read_text(encoding="utf-8"))
        st.caption(f"Measured on 100 fake emails against our own answer key: {result['decisions_right']}/{result['decisions_total']} decisions correct, in {result['seconds']} seconds.")

with inbox_tab:
    counter_slot = st.empty()
    catch_slot = st.empty()
    show_counts(counter_slot, state)
    live_seconds = load_measured().get("live_seconds")
    if manual and live_seconds:
        st.caption(f"Measured on our 10 fake emails. By hand: {manual:g} min. Agent: {live_seconds} s.")
    run_col, folder_col, excel_col, _ = st.columns([2, 1, 1, 2])
    run_clicked = run_col.button("▶ Process inbox", type="primary", width="stretch")
    if folder_col.button("Open folders", width="stretch"):
        open_path(store.filed)
    if excel_col.button("Open Excel", width="stretch"):
        open_path(store.excel)

    st.subheader("Results")
    results_slot = st.empty()
    show_results(results_slot, state)

    if run_clicked:
        delay = 0 if ai.mode == "live" else 0.7
        for _ in pipeline.run():
            show_counts(counter_slot, state)
            show_results(results_slot, state)
            time.sleep(delay)
        st.rerun()

    with st.expander("Upload emails from Gmail", expanded=True):
        st.caption("In Gmail, open an email and choose ⋮ → Download message. Only the emails you upload go to Gemini.")
        if ai.mode == "live":
            uploads = st.file_uploader("Drop .eml files here", type=["eml"], accept_multiple_files=True)
            if uploads and st.button("Process uploaded emails", type="primary"):
                done = 0
                for upload in uploads:
                    try:
                        done += pipeline.process_upload(upload.getvalue()) is not None
                    except ValueError as error:
                        st.error(f"{upload.name}: {error}")
                if done:
                    st.rerun()
                st.toast("Those emails were already processed.")
        else:
            st.info("Uploads need Live Gemini. Choose it in Demo controls.")

    with st.expander("The messy inbox", expanded=not state["items"]):
        emails = [read_eml(path) for path in list_email_files(INBOX)]
        st.markdown(table_html([{
            "From": e.sender, "Subject": e.subject or "(no subject)",
            "Attachments": ", ".join(a.filename for a in e.attachments) or "none",
        } for e in emails]), unsafe_allow_html=True)

    with st.expander("Judge challenge: pick an email and run it"):
        challenges = {read_eml(p).subject + " · " + ", ".join(a.filename for a in read_eml(p).attachments): p
                      for p in list_email_files(CHALLENGE)}
        picked = st.selectbox("Email", list(challenges))
        if st.button("Run this email"):
            email = read_eml(challenges[picked])
            if email.id in state["emails"]:
                st.toast("Already processed. See the results table.")
            else:
                pipeline.process(email)
                state["mode"] = state["mode"] or ai.mode
                store.save()
                st.rerun()

    if store.excel.exists():
        with st.expander("students.xlsx"):
            try:
                sheet = load_workbook(store.excel, read_only=True).active
                rows = list(sheet.iter_rows(values_only=True))
                st.markdown(table_html([dict(zip(rows[0], row)) for row in rows[1:]]), unsafe_allow_html=True)
            except PermissionError:
                st.info("Close the spreadsheet in Excel to preview it here.")

with ready_tab:
    students = sorted(state["students"].values(), key=lambda s: ["red", "amber", "green"].index(readiness(s)["level"]))
    if not students:
        st.info("Student status appears here after the inbox is processed.")
    for start in range(0, len(students), 3):
        for column, student in zip(st.columns(3), students[start:start + 3]):
            status = readiness(student)
            with column.container(border=True):
                st.markdown(f"#### {student['first_name']} {student['surname']}")
                st.markdown(f"**{LEVEL[status['level']]}**")
                st.write(status["summary"])
                if student.get("host_university"):
                    semester = f"{student['semester']} · " if student.get("semester") else ""
                    st.caption(f"{student['host_university']} · {semester}{student.get('stay_start', '?')} to {student.get('stay_end', '?')}")
                docs = ", ".join(DOC_LABELS[d] for d in student["documents"]) or "None filed"
                st.caption(f"Received: {docs}")
                st.caption(f"Next step: {status['next']}")
                due = next_deadline(student)
                if due:
                    late = " (overdue)" if due["overdue"] else ""
                    st.caption(f"Next deadline: {due['date']}, {due['document']}{late}")

with review_tab:
    flagged = [s for s in state["students"].values() if insurance_problem(s)]
    reviews = [i for i in state["items"] if i["status"] == "review" and not i["resolved"]]
    drafts = state["drafts"]
    if not (flagged or reviews or drafts):
        st.info("Flags and reply drafts appear here after the inbox is processed.")

    if flagged or reviews:
        st.subheader("Needs a person")
    for student in flagged:
        with st.container(border=True):
            st.markdown(f"### 🔴 {student['first_name']} {student['surname']}: insurance doesn't cover the stay")
            st.write(readiness(student)["summary"])
            evidence = [student["evidence"][f] | {"field": f} for f in ("insurance_end", "stay_end") if f in student.get("evidence", {})]
            evidence_lines(evidence)
            draft = drafts.get(f"{student['id']}_action")
            if draft:
                st.divider()
                st.markdown("**Fix already drafted**")
                show_draft(draft, store)
    for item in reviews:
        with st.container(border=True):
            st.markdown(f"**{item['sender']}** · `{item['original']}`")
            for reason in item["reasons"]:
                st.write(f"🔴 {reason}")
            st.caption(f"Held for review, not filed: {item['new_name'] or 'no document'}")
            if item["student"]:
                draft = drafts.get(f"{item['student']}_action")
                if draft and draft["kind"] == "Fix: clearer copy":
                    show_draft(draft, store)
            if st.button("Mark as resolved", key=f"resolve_{item['email_id']}_{item['original']}"):
                pipeline.resolve_review(item["email_id"], item["original"])
                st.rerun()

    shown = {f"{s['id']}_action" for s in flagged}
    shown |= {f"{i['student']}_action" for i in reviews if i["student"] and drafts.get(f"{i['student']}_action", {}).get("kind") == "Fix: clearer copy"}
    others = [d for key, d in drafts.items() if key not in shown]
    if others:
        st.subheader("Reply drafts")
        for draft in others:
            with st.container(border=True):
                show_draft(draft, store)

with ask_tab:
    st.write("Answers come only from the office FAQ. Anything else goes to staff.")
    chat = st.session_state.setdefault("chat", [])
    example_columns = st.columns(len(CHAT_QUESTIONS))
    for column, example in zip(example_columns, CHAT_QUESTIONS):
        if column.button(example, width="stretch"):
            chat.append((example, pipeline.answer(example)))
    question = st.chat_input("Type a student's question, in any language")
    if question:
        chat.append((question, pipeline.answer(question)))
    for asked, answer in chat:
        with st.chat_message("user"):
            st.write(asked)
        with st.chat_message("assistant"):
            st.write(answer["reply"])
            if answer["can_answer"]:
                note = f"Source: FAQ {answer['faq_id']}"
                if answer["language"] not in ("English", "Unknown"):
                    note += f" · {answer['language']} · English: {answer['english']}"
                st.caption(note)
            else:
                st.caption("Not in the FAQ, so it's handed to staff instead of guessed.")

with advice_tab:
    st.write("Find an applicant's best destinations from their grades and preferences. Staff approve the advice, and the student chooses.")
    st.warning("Sample partner list: the universities' requirements and places are made up for this demo. Use the office's real agreements in practice.")
    partners = advisor.load_partners()
    rules = applications.active_rules(state)
    for key, value in {"advice_name": "Nora", "advice_email": "nora.lind@student.example", "advice_grade": 8.1, "advice_field": "Business",
                       "advice_level": "B2", "advice_countries": ["Netherlands", "Italy"], "advice_failed": "No", "advice_failed_names": "",
                       "advice_language": "en"}.items():
        st.session_state.setdefault(key, value)
    if ai.mode != "fixture":
        st.subheader("Fill the form from an email")
        pasted_col, file_col = st.columns(2)
        pasted = pasted_col.text_area("Paste the student's email", key="advice_pasted", height=130)
        pasted_col.button("Use a sample email", on_click=lambda: st.session_state.update(advice_pasted=SAMPLE_APPLICATION))
        uploaded = file_col.file_uploader("Or upload it (.eml, with attachments)", type=["eml"])
        if (pasted.strip() or uploaded) and st.button("Read email and fill the form", type="primary"):
            if uploaded and uploaded.size > 25_000_000:
                filled = {"error": "This email is larger than 25 MB."}
            else:
                email = parse_eml(uploaded.getvalue(), "uploaded") if uploaded else applications.email_from_text(pasted)
                with st.spinner("Reading the email..."):
                    filled = applications.form_from_email(email, ai, rules, partners)
            st.session_state["email_reading"] = filled
            st.session_state.pop("advice", None)
            if "error" not in filled:
                st.session_state.update(
                    advice_name=filled["first_name"], advice_grade=filled["grade"], advice_field=filled["field"], advice_level=filled["level"],
                    advice_countries=filled["countries"], advice_failed=filled["failed_status"], advice_failed_names=filled["failed_subjects"],
                    advice_language=filled["language"], advice_email=filled["email"])
        reading = st.session_state.get("email_reading")
        if reading and "error" in reading:
            st.error(reading["error"])
        elif reading:
            st.success("Form filled from the email. Please check the details below.")
            if reading["quote"]:
                st.caption(f"Grade from the email: “{reading['quote']}”")
    if ai.mode == "fixture":
        st.caption("Reading transcripts needs Live Gemini or a saved replay. Type the grade below.")
    else:
        transcript = st.file_uploader("Read the grade from a transcript (optional)", type=["pdf", "jpg", "jpeg", "png"])
        if transcript and st.button("Read transcript"):
            attachment = Attachment(transcript.name, transcript.getvalue())
            with st.spinner("Reading the transcript..."):
                reading = advisor.read_grade(ai, attachment)
            st.session_state["transcript_reading"] = reading
            st.session_state.pop("advice", None)
            if reading["grade"] is not None:
                st.session_state["advice_grade"] = reading["grade"]
                if reading["first_name"]:
                    st.session_state["advice_name"] = reading["first_name"]
        reading = st.session_state.get("transcript_reading")
        if reading:
            if reading["grade"] is not None:
                st.success(f"Grade read: {reading['grade']:g}. Please confirm it below before finding options.")
                if reading["quote"]:
                    st.caption(f"From the document: “{reading['quote']}”")
            for problem in reading["problems"]:
                st.error(f"{problem} The grade wasn't filled in.")
            for warning in reading["warnings"]:
                st.warning(warning)
    left, right = st.columns(2)
    applicant = left.text_input("Applicant's first name", key="advice_name")
    address = right.text_input("Applicant's email", key="advice_email")
    grade = left.number_input("Average grade (0 to 10)", min_value=0.0, max_value=10.0, step=0.1, value=None, key="advice_grade")
    field = right.selectbox("Field of study", sorted({f for p in partners for f in p["fields"]}), index=None, key="advice_field")
    level = left.selectbox("English level", advisor.LEVELS, index=None, key="advice_level")
    wanted = right.multiselect("Where they want to go", sorted({p["country"] for p in partners}), key="advice_countries")
    failed_status = left.selectbox("Failed any subject last semester?", ["No", "Yes", "Not known"], key="advice_failed")
    failed_names = right.text_input("Failed subjects, if any", key="advice_failed_names", disabled=failed_status != "Yes")
    blanks = [label for label, value in [("average grade", grade), ("field of study", field), ("English level", level)] if value is None]
    if blanks:
        st.warning(f"Not found in the email: {', '.join(blanks)}. Please fill it in or ask the student.")
    if st.button("Check eligibility and find options", type="primary", disabled=bool(blanks)):
        st.session_state["advice"] = (applicant, address, applications.advise(
            applicant, grade, failed_status, failed_names, field, level, wanted, rules, partners, st.session_state["advice_language"]))
    if "advice" in st.session_state:
        applicant, address, result = st.session_state["advice"]
        if result["decision"] == "not_eligible":
            st.error(f"Not eligible. {result['reason']}. These are the office's sample rules.")
        elif result["decision"] == "needs_info":
            st.warning("We can't confirm eligibility until we know whether the student failed any subject.")
        elif result["decision"] == "review":
            st.warning("Eligible, but no partner in this list fits. This student should talk to a person.")
        else:
            st.success(f"Eligible: average of at least {rules['min_average']:g}" + ("." if rules["allow_failed_subjects"] else " and no failed subjects."))
        if result.get("no_preferred_match") and result["decision"] == "eligible":
            st.info("Nothing suitable in the preferred countries, so these are the closest alternatives.")
        for number, option in enumerate(result.get("options", []), start=1):
            with st.container(border=True):
                st.markdown(f"**{number}. {option['university']}**, {option['city']}, {option['country']} · **{option['fit']}**")
                for reason in option["reasons"]:
                    st.caption(reason)
        if result.get("ruled_out"):
            with st.expander(f"Ruled out ({len(result['ruled_out'])})"):
                for reason in result["ruled_out"]:
                    st.write(reason)
        st.markdown("**Advice draft for staff**")
        body = result["body"] or advisor.advice_body(applicant, {"options": []})
        show_reply(body)
        if result["body"] and result["body"] != result["body_english"]:
            with st.expander("English copy for staff"):
                show_reply(result["body_english"])
        if st.button("Add to drafts", disabled=not address.strip()):
            store.save_draft(f"advice_{address}", kind="Applicant advice", to=address, subject="Your Erasmus application", body=body)
            store.save()
            st.success("Added to Review and drafts. Nothing has been sent.")

with how_tab:
    st.subheader("Gemini reads. Python checks. A person approves.")
    st.markdown("""<div class="flow">
<div><b>1 · Read</b>Python opens the email and attachments</div>
<div><b>2 · Understand</b>Gemini returns fixed fields with evidence, through Google ADK</div>
<div><b>3 · Check</b>Python tests dates, cross-checks documents and scans for injection</div>
<div><b>4 · Route</b>Clear items are filed. Doubtful ones go to a person, with the reason</div>
<div><b>5 · Record</b>Clear file names and one Excel row per student</div>
<div><b>6 · Approve</b>Replies stay drafts. The agent has no send tool</div>
</div>""", unsafe_allow_html=True)
    if SCORE_FILE.exists() and state["mode"] != "fixture":
        result = json.loads(SCORE_FILE.read_text(encoding="utf-8"))
        first, second = st.columns(2)
        first.metric("Emails routed correctly", f"{result['routes_right']}/{result['routes_total']}")
        second.metric("Fields extracted correctly", f"{result['fields_right']}/{result['fields_total']}")
        st.caption("Measured on our fake test emails against a hand-written answer key.")
    st.caption("Demo uses fake data only.")
