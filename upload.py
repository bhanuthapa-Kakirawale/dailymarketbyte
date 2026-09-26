"""Upload to YouTube with the Data API v3.

First time only (opens a browser to sign in):   python upload.py --auth
Upload a built video manually:                  python upload.py output/daily_byte_2026-09-21.mp4 \n                                                     output/publication/2026-09-21/publication_audit.json
(an upload without a PASS publication audit for that exact file is refused - publication.audit)
"""
import json
import os
import sys

from config import BASE_DIR, YT_PRIVACY, YT_CATEGORY

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
SECRET = os.getenv("YT_CLIENT_SECRET", os.path.join(BASE_DIR, "client_secret.json"))
TOKEN = os.getenv("YT_TOKEN", os.path.join(BASE_DIR, "token.json"))


def get_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(TOKEN, SCOPES) if os.path.exists(TOKEN) else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(SECRET, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload(video_path: str, meta: dict, audit) -> str:
    """Upload ONE video. `audit` (the publication_audit.json path or dict) is REQUIRED: the
    publication hard-block runs first - before any YouTube client or credential is touched - and
    raises `publication.PublicationBlocked` unless the audit says PASS, is PUBLIC_UNREGISTERED,
    is not synthetic, and was computed over this exact file (sha256). There is no bypass."""
    from publication.audit import require_publication_pass
    require_publication_pass(audit, video_path)
    from googleapiclient.http import MediaFileUpload
    yt = get_service()
    body = {"snippet": {"title": meta["title"], "description": meta["description"],
                        "tags": meta.get("tags", []), "categoryId": YT_CATEGORY,
                        "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": {"privacyStatus": YT_PRIVACY, "selfDeclaredMadeForKids": False}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(video_path, mimetype="video/mp4",
                                                        chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--auth":
        get_service()
        print(f"Authorised. Token saved to {TOKEN}")
    elif len(sys.argv) > 2:
        # manual upload: python upload.py <video.mp4> <publication_audit.json>
        path, audit_path = sys.argv[1], sys.argv[2]
        with open(path.replace(".mp4", ".json"), encoding="utf-8") as f:
            print("Uploaded:", upload(path, json.load(f), audit_path))
    elif len(sys.argv) > 1:
        print("Refused: a manual upload needs the video's publication audit - "
              "python upload.py <video.mp4> <publication_audit.json>")
        sys.exit(2)
    else:
        print(__doc__)
