"""Command-line run. Usage: python run.py [--mode live|replay|fixture] [--fresh]"""
import argparse
import sys

from erasmus_inbox.ai import FixtureAI, GeminiAI, ReplayAI, replay_ready
from erasmus_inbox.config import DEMO_QUESTIONS, OUTPUT, has_api_key
from erasmus_inbox.pipeline import Pipeline
from erasmus_inbox.tools import Store
from erasmus_inbox.validate import readiness


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["live", "replay", "fixture"], default="live")
    parser.add_argument("--fresh", action="store_true", help="delete earlier output first")
    args = parser.parse_args()

    # Checked before --fresh, so a missing key never wipes earlier results.
    if args.mode == "live" and not has_api_key():
        print("No Gemini key found. Copy .env.example to .env in this folder and paste your key into it.")
        raise SystemExit(2)
    if args.mode == "replay" and not replay_ready():
        print("No saved Gemini run yet. Run in live mode first.")
        raise SystemExit(2)

    ai = {"live": GeminiAI, "replay": ReplayAI, "fixture": FixtureAI}[args.mode]()
    store = Store(OUTPUT)
    if args.fresh:
        store.reset()
    pipeline = Pipeline(store, ai)
    for email_id in pipeline.run():
        items = [i for i in store.state["items"] if i["email_id"] == email_id]
        for item in items:
            print(f"{email_id:12} {item['status']:9} {item['original'][:28]:28} -> {item['new_name']}")
            for reason in item["reasons"]:
                print(f"{'':12} ! {reason}")
    if args.mode == "live":
        for question in DEMO_QUESTIONS:
            try:
                ai.answer(question)
            except Exception as error:
                print(f"WARNING: could not save the demo answer for '{question}': {type(error).__name__}")

    print("\nStudents")
    for student in store.state["students"].values():
        status = readiness(student)
        print(f"  {status['level']:6} {student['id']:20} {status['summary']}")
    for warning in store.state["warnings"]:
        print(f"WARNING: {warning}")
    store.save()
    print(f"\nFinished in {store.state['seconds']}s, mode: {args.mode}")


if __name__ == "__main__":
    main()
