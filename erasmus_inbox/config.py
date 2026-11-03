"""Project paths and settings."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
INBOX = DATA / "inbox"
CHALLENGE = DATA / "challenge"
FAQ_FILE = DATA / "faq.md"
PARTNERS_FILE = DATA / "partners.json"
RULES_FILE = DATA / "eligibility_rules.json"
APPLICATIONS = DATA / "applications"
APPLICATIONS_EXPECTED = DATA / "applications_expected.json"
APPLICATIONS_SCORE = DATA / "applications_score.json"
EXPECTED_FILE = DATA / "expected.json"
REPLAY_FILE = DATA / "replay_cache.json"
SCORE_FILE = DATA / "score.json"
MEASURED_FILE = DATA / "measured.json"
OUTPUT = ROOT / os.getenv("ERASMUS_OUTPUT", "output")

MODEL = os.getenv("ERASMUS_MODEL", "gemini-flash-lite-latest")
CONFIDENCE_THRESHOLD = 0.8
CHAT_QUESTIONS = ["Can I apply if I failed a subject?", "Quale voto serve per l'Erasmus?", "How do I apply?", "My visa was refused. What should I do?"]
DEMO_QUESTIONS = ["When is the deadline for the insurance document?", "My visa was refused. What should I do?"]


def has_api_key() -> bool:
    key = os.getenv("GOOGLE_API_KEY", "")
    return bool(key) and key != "paste-your-key-here"
