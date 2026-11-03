"""Copies the 100 fake applications into the mailbox label set in .env. Use a demo mailbox, not a personal one."""
from erasmus_inbox import mailbox
from erasmus_inbox.config import APPLICATIONS


def main() -> None:
    if not mailbox.configured():
        print("No mailbox found. Add ERASMUS_EMAIL and ERASMUS_APP_PASSWORD to .env first.")
        raise SystemExit(2)
    values = mailbox.settings()
    print(f"Copied {mailbox.load_emails(APPLICATIONS)} emails to {values['email']}, label {values['label']}.")


if __name__ == "__main__":
    main()
