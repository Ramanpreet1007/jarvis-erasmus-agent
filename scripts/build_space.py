"""Builds a clean folder to upload to a Hugging Face Space. Usage: python scripts/build_space.py <target folder>

It copies only what the website needs, plus the files in deploy/huggingface. Never copies .env.
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOLDERS = ["erasmus_inbox", "web", "data"]
FILES = ["web_app.py", "run.py"]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".env", "*.env")


def main() -> None:
    target = Path(sys.argv[1]).resolve()
    if ROOT in target.parents or target == ROOT:
        raise SystemExit("Choose a folder outside the project.")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for folder in FOLDERS:
        shutil.copytree(ROOT / folder, target / folder, ignore=SKIP)
    for name in FILES:
        shutil.copy2(ROOT / name, target / name)
    for item in (ROOT / "deploy" / "huggingface").iterdir():
        shutil.copy2(item, target / item.name)
    leaked = [p for p in target.rglob("*") if p.name == ".env"]
    if leaked:
        raise SystemExit(f"Refusing: found {leaked}")
    print(f"Ready to upload: {target} ({sum(1 for p in target.rglob('*') if p.is_file())} files)")


if __name__ == "__main__":
    main()
