# JARVIS: AI Inbox Agent for Erasmus Coordinators

A hackathon prototype (Google Developer Group, Challenge 03) built with Python, Google ADK and Gemini.

An Erasmus coordinator receives around 100 applicant emails. The agent reads each application, checks it against the office rules, suggests partner universities and drafts a reply in the student's language. The coordinator approves every reply. **The agent has no way to send email.**

**Try the online demo:** https://ramanpreet1007.github.io/jarvis-erasmus-demo/ (plays back a saved real Gemini run in your browser)

All names, grades and emails in this repository are invented sample data.

## How it works

1. **Gemini reads** each email and attachment and returns fixed fields (grade, failed subjects, field, English level, wishes, language) with a quote from the email as evidence.
2. **Python rules decide** eligibility (by default: average of at least 6, no failed subjects) and rank partner universities. The AI never makes the decision.
3. **A person approves** the drafted replies. Doubtful emails, and emails with hidden instructions aimed at the AI, go to a person.

## Features

- Reads the whole inbox, from `.eml` files or an IMAP mailbox
- Eligibility with a reason and evidence; change a rule and every decision updates with no new AI calls
- University matching by grade, field, English level and country, plus a places-versus-demand view
- Replies in English, Italian and Spanish
- An FAQ chatbot that answers only from `data/faq.md`
- Document tracking: renames attachments, flags missing documents and insurance problems
- Prompt-injection detection

## Setup (Windows)

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Open `.env` and paste your key from [AI Studio](https://aistudio.google.com/apikey). Never share it or commit it. Without a key, the website still runs on the saved Gemini answers.

## Run

| Command | What it does |
|---|---|
| `.venv\Scripts\python.exe web_app.py` | The website, at http://localhost:8000 |
| `.venv\Scripts\python.exe run_applications.py` | Processes the 100 sample applications from the command line |
| `.venv\Scripts\python.exe run.py --mode replay --fresh` | Document tracking on the sample inbox, using saved answers |
| `.venv\Scripts\python.exe -m streamlit run dashboard.py` | The Streamlit dashboard |
| `.venv\Scripts\adk.exe web` | The ADK chat UI. Pick `erasmus_inbox` |
| `.venv\Scripts\python.exe -m pytest` | The tests |

| Folder | Contents |
|---|---|
| `erasmus_inbox/` | The agent: AI reading, rules, replies, mailbox, chatbot |
| `web/` | The website (one HTML file) |
| `data/` | Sample emails, answer key, FAQ, partner universities, rules and saved Gemini answers |
| `deploy/huggingface/` | Files to host the website on Hugging Face Spaces |
| `scripts/` | Data generators and the online-demo builder |

## Results and limits

On the 100 sample emails, the final run's decisions matched our own answer key. We tuned the agent on these same emails, so this is a prototype result, not a measured accuracy. The universities, places and rules are sample data, and the agent has not been used at a real university.
