"""One-time Gmail login. Run on the Mac, not in Docker:
    .venv/bin/python auth.py
Opens a browser, saves secrets/token.json for the container to use."""
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from triage.gmail_client import SCOPES

SECRETS = Path(__file__).resolve().parent / "secrets"


def main() -> None:
    flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS / "credentials.json"), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    token_path = SECRETS / "token.json"
    token_path.write_text(creds.to_json())
    os.chmod(token_path, 0o600)
    profile = build("gmail", "v1", credentials=creds, cache_discovery=False).users().getProfile(
        userId="me"
    ).execute()
    print(f"Authorised {profile['emailAddress']} -> {token_path}")


if __name__ == "__main__":
    main()
