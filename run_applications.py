"""Runs the 100 application emails. Usage: python run_applications.py [--mode live|replay|fixture] [--fresh]"""
import argparse
import json
import sys
import time

from erasmus_inbox.ai import FixtureAI, GeminiAI, ReplayAI
from erasmus_inbox.applications import run_batch, score
from erasmus_inbox.config import APPLICATIONS_EXPECTED, APPLICATIONS_SCORE, OUTPUT, has_api_key
from erasmus_inbox.tools import Store, save_measured


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["live", "replay", "fixture"], default="live")
    parser.add_argument("--fresh", action="store_true", help="forget earlier application results")
    args = parser.parse_args()
    if args.mode == "live" and not has_api_key():
        print("No Gemini key found. Add it to .env first.")
        raise SystemExit(2)

    store = Store(OUTPUT)
    if args.fresh:
        store.state["applications"] = {}
        store.state.pop("rules", None)
    ai = {"live": GeminiAI, "replay": ReplayAI, "fixture": FixtureAI}[args.mode]()
    started = time.perf_counter()
    for done, total, record in run_batch(store, ai):
        print(f"{done:3}/{total} {record['id']} {record['decision']:13} {record['reason'][:80]}", flush=True)
    seconds = round(time.perf_counter() - started, 1)

    expected = json.loads(APPLICATIONS_EXPECTED.read_text(encoding="utf-8"))
    result = score(store.state["applications"], expected)
    print(f"\nFinished in {seconds}s, mode: {args.mode}")
    if args.mode == "fixture":
        print("This run used the answer key, so no score was saved.")
        return
    print(f"Decisions correct: {result['decisions_right']}/{result['decisions_total']}")
    print(f"Grades correct:    {result['grades_right']}/{result['grades_total']}")
    for line in result["wrong"]:
        print(f"  - {line}")
    if args.mode == "live":
        APPLICATIONS_SCORE.write_text(json.dumps(result | {"seconds": seconds}, indent=2, ensure_ascii=False), encoding="utf-8")
        save_measured(applications_seconds=seconds)


if __name__ == "__main__":
    main()
