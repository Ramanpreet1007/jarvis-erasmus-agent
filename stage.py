"""The big-screen demo for an audience. It replays a saved real Gemini run, using the same rules as the dashboard."""
import html
import json
import time

import streamlit as st

from erasmus_inbox import applications
from erasmus_inbox.ai import ChatAI, ReplayAI
from erasmus_inbox.config import APPLICATIONS, APPLICATIONS_SCORE, CHAT_QUESTIONS, OUTPUT
from erasmus_inbox.pipeline import Pipeline
from erasmus_inbox.tools import Store, list_email_files, read_eml

PACE_SECONDS = 0.12
SCENES = ["Inbox", "Answer all", "Two students", "Ask the bot", "You set the rule", "The cheat", "Approve"]
COLORS = {"eligible": "var(--good)", "not_eligible": "var(--bad)", "needs_info": "var(--ask)", "review": "var(--person)", "other": "var(--person)"}
WORDS = {"eligible": "Eligible, options drafted", "not_eligible": "Not eligible, reason explained", "needs_info": "Asked for missing details",
         "review": "Sent to a person", "other": "Sent to a person"}

st.set_page_config(page_title="Erasmus Inbox Agent", page_icon="📬", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""<style>
:root { --bg:#0b1220; --panel:#16213a; --text:#f4f6fb; --muted:#a9b4cc; --good:#34d399; --bad:#f87171; --ask:#fbbf24; --person:#60a5fa; }
.stApp, [data-testid="stHeader"] { background:var(--bg); color:var(--text); }
[data-testid="stToolbar"], [data-testid="stDecoration"], footer { display:none; }
.block-container { padding:1.5rem 3rem; max-width:100%; }
.stApp p, .stApp label { color:var(--text); }
.headline { font-size:clamp(2.2rem,5.5vw,5rem); font-weight:800; line-height:1.1; margin:.5rem 0 1rem; }
.sub { font-size:clamp(1.1rem,2.2vw,2rem); color:var(--muted); margin-bottom:1rem; }
.counters { display:grid; grid-template-columns:repeat(4,1fr); gap:1rem; margin:1rem 0; }
.counter { background:var(--panel); border-radius:16px; padding:1rem; text-align:center; }
.counter b { display:block; font-size:clamp(3rem,8vw,7.5rem); line-height:1; }
.counter span { font-size:clamp(.9rem,1.6vw,1.5rem); color:var(--muted); }
.row { display:flex; justify-content:space-between; gap:1rem; background:var(--panel); border-left:8px solid var(--person);
       border-radius:8px; padding:.5rem 1rem; margin:.35rem 0; font-size:clamp(1rem,1.7vw,1.5rem); }
.card { background:var(--panel); border-radius:16px; padding:1.5rem; font-size:clamp(1rem,1.65vw,1.5rem); line-height:1.4; }
.label { font-size:clamp(1.1rem,2vw,1.8rem); font-weight:700; margin-bottom:.5rem; }
.hidden-text { background:var(--bad); color:#111; font-weight:700; padding:.5rem; border-radius:8px; display:block; margin-top:.5rem; }
[data-testid="stHeader"] { display:none; }
[data-testid="stBottom"], [data-testid="stBottom"] > div, [data-testid="stBottomBlockContainer"] { background:var(--bg); }
[data-testid="stChatInput"], [data-testid="stChatInput"] > div { background:var(--panel); }
[data-testid="stChatInput"] textarea { color:var(--text); font-size:1.3rem; }
[data-testid="stChatInput"] textarea::placeholder { color:var(--muted); opacity:1; }
.stButton button p { white-space:normal; overflow:visible; text-overflow:clip; }
.stButton button div { overflow:visible; }
.stButton button p { font-size:clamp(.8rem,1.3vw,1.4rem); }
.stButton button[kind="secondary"] { background:var(--panel); border:1px solid #33415f; color:var(--text); }
.foot { color:var(--muted); font-size:1rem; margin-top:1rem; }
</style>""", unsafe_allow_html=True)

store = Store(OUTPUT / "stage")  # Separate from the dashboard, so the two can't overwrite each other.
state = store.state
records = state.setdefault("applications", {})
scene = st.session_state.setdefault("scene", 0)


def go(number: int) -> None:
    st.session_state["scene"] = number


def counters_html(values: dict) -> str:
    def count(*keys):
        return sum(r["decision"] in keys for r in values.values())

    items = [("eligible", count("eligible"), "Eligible, options ready"), ("not_eligible", count("not_eligible"), "Not eligible, told why"),
             ("needs_info", count("needs_info"), "Asked for details"), ("review", count("review", "other"), "Sent to a person")]
    return '<div class="counters">' + "".join(
        f'<div class="counter"><b style="color:{COLORS[key]}">{number}</b><span>{label}</span></div>' for key, number, label in items) + "</div>"


def show_row(left: str, right: str, color: str) -> str:
    return f'<div class="row" style="border-left-color:{color}"><span>{html.escape(left)}</span><span>{html.escape(right)}</span></div>'


def card(text: str) -> str:
    return '<div class="card">' + html.escape(text).replace("\n", "<br>") + "</div>"


def first_record(test):
    return next((r for _, r in sorted(records.items()) if test(r)), None)


nav = st.columns(len(SCENES) + 1)
for number, name in enumerate(SCENES):
    nav[number].button(name, key=f"nav{number}", width="stretch", type="primary" if number == scene else "secondary", on_click=go, args=(number,))
if nav[-1].button("↺ Start over", width="stretch"):
    state["applications"] = {}
    state.pop("rules", None)
    store.save()
    st.session_state.clear()
    st.rerun()

if scene == 0:
    emails = [read_eml(path) for path in list_email_files(APPLICATIONS)]
    st.markdown(f'<div class="headline">{len(emails)} unread emails.<br>One Erasmus office. One morning.</div>'
                '<div class="sub">Every student is waiting to hear if their exchange is possible.</div>', unsafe_allow_html=True)
    st.markdown("".join(show_row(e.sender, e.subject, "var(--person)") for e in emails[:7]), unsafe_allow_html=True)
    st.markdown('<div class="foot">All students and rules here are made up for this demo.</div>', unsafe_allow_html=True)

elif scene == 1:
    title = st.empty()
    numbers = st.empty()
    feed = st.empty()
    title.markdown(f'<div class="headline">{len(records)} of 100 answered</div>', unsafe_allow_html=True)
    numbers.markdown(counters_html(records), unsafe_allow_html=True)
    if len(records) < 100 and st.button("▶ Answer them all", type="primary", width="stretch"):
        recent = []
        for done, total, record in applications.run_batch(store, ReplayAI(), workers=1):
            recent = [show_row(record["name"] or record["sender"], WORDS[record["decision"]], COLORS[record["decision"]]), *recent][:5]
            title.markdown(f'<div class="headline">{done} of {total} answered</div>', unsafe_allow_html=True)
            numbers.markdown(counters_html(records), unsafe_allow_html=True)
            feed.markdown("".join(recent), unsafe_allow_html=True)
            time.sleep(PACE_SECONDS)
        st.rerun()
    seconds = json.loads(APPLICATIONS_SCORE.read_text(encoding="utf-8"))["seconds"] if APPLICATIONS_SCORE.exists() else None
    took = f" That run took about {round(seconds / 60)} minutes." if seconds else ""
    st.markdown(f'<div class="foot">Playing back a saved real Gemini run, sped up for the stage.{took}</div>', unsafe_allow_html=True)

elif not records:
    st.markdown('<div class="headline">Answer the emails first.</div>', unsafe_allow_html=True)
    st.button("Go to Answer all", type="primary", on_click=go, args=(1,))

elif scene == 2:
    rejected = first_record(lambda r: r["decision"] == "not_eligible" and r["failed_subjects"] and (r["grade"] or 0) >= 8 and r["language"] == "en")
    foreign = first_record(lambda r: r["decision"] == "eligible" and r["language"] == "it")
    st.markdown('<div class="headline">Every student gets a real answer.</div>', unsafe_allow_html=True)
    left, right = st.columns(2)
    if rejected:
        left.markdown(f'<div class="label" style="color:var(--bad)">{html.escape(rejected["name"])}: average {rejected["grade"]:g}, failed {html.escape(", ".join(rejected["failed_subjects"]))}</div>'
                      + card(rejected["body"]), unsafe_allow_html=True)
    if foreign:
        right.markdown(f'<div class="label" style="color:var(--good)">{html.escape(foreign["name"])} wrote in Italian, so gets Italian</div>'
                       + card(foreign["body"]), unsafe_allow_html=True)

elif scene == 3:
    st.markdown('<div class="headline">Students ask the same questions every day.</div>'
                '<div class="sub">Answers come only from the office FAQ. Anything else goes to a person.</div>', unsafe_allow_html=True)
    chat = st.session_state.setdefault("chat", [])
    asked = None
    for column, example in zip(st.columns(len(CHAT_QUESTIONS)), CHAT_QUESTIONS):
        if column.button(example, width="stretch"):
            asked = example
    asked = st.chat_input("Ask anything about Erasmus, in any language") or asked
    if asked:
        with st.spinner("Checking the FAQ..."):
            chat.append((asked, Pipeline(store, ChatAI()).answer(asked)))
    for question, answer in chat[-2:]:
        source = f"From the FAQ, entry {answer['faq_id']}" if answer["can_answer"] else "Not in the FAQ, so it goes to a person instead of guessing"
        color = "var(--good)" if answer["can_answer"] else "var(--person)"
        st.markdown(f'<div class="label">🎓 {html.escape(question)}</div>' + card(answer["reply"])
                    + f'<div class="sub" style="color:{color};margin-top:.4rem">{source}</div>', unsafe_allow_html=True)

elif scene == 4:
    rules = applications.active_rules(state)
    st.markdown('<div class="headline">You run the office. What\'s the minimum grade?</div>', unsafe_allow_html=True)
    minimum = st.slider("Minimum average", 5.0, 9.0, float(rules["min_average"]), 0.5)
    if minimum != rules["min_average"]:
        applications.apply_rules(store, rules | {"min_average": minimum})
    st.markdown(f'<div class="sub">Minimum grade: <b>{minimum:g}</b>. Every decision and reply updated, with no new AI calls.</div>'
                + counters_html(records), unsafe_allow_html=True)

elif scene == 5:
    cheat = first_record(lambda r: r["decision"] == "review" and "instructions" in r["reason"])
    st.markdown('<div class="headline">One student tried to trick our AI.</div>', unsafe_allow_html=True)
    if cheat:
        transcript = read_eml(APPLICATIONS / f"{cheat['id']}.eml").attachments[0].text()
        lines = transcript.splitlines()
        hidden = [line for line in lines if "ignore" in line.casefold()]
        visible = "\n".join(line for line in lines if line not in hidden)
        left, right = st.columns(2)
        left.markdown('<div class="label">The transcript, as a person sees it</div>' + card(visible), unsafe_allow_html=True)
        if right.button("🔍 Reveal the hidden white text", type="primary", width="stretch"):
            st.session_state["revealed"] = True
        if st.session_state.get("revealed"):
            right.markdown('<div class="label" style="color:var(--bad)">Hidden in the PDF</div>'
                           + "".join(f'<span class="hidden-text">{html.escape(line)}</span>' for line in hidden)
                           + show_row("Caught.", "Sent to a person, no reply drafted", "var(--person)"), unsafe_allow_html=True)

else:
    def waiting(*keys):
        return sum(r["decision"] in keys and r["body"] and not r["approved"] for r in records.values())

    approved = sum(r["approved"] for r in records.values())
    st.markdown('<div class="headline">The agent never sends anything.<br>A person decides.</div>', unsafe_allow_html=True)
    if waiting("eligible", "needs_info") and st.button(f"✅ Approve {waiting('eligible', 'needs_info')} routine replies", type="primary", width="stretch"):
        applications.approve_group(store, "eligible")
        applications.approve_group(store, "needs_info")
        st.rerun()
    if approved:
        st.markdown(f'<div class="sub">{approved} replies approved. {waiting("not_eligible")} "not eligible" replies wait for staff to read them first, '
                    'because a wrong one could cost a student their exchange. Nothing has been sent.</div>'
                    '<div class="headline" style="color:var(--good)">Every student gets an answer today, not next month.</div>', unsafe_allow_html=True)

if scene < len(SCENES) - 1:
    st.button("Next →", key="next", width="stretch", on_click=go, args=(scene + 1,))
