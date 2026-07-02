#!/usr/bin/env python3
"""Minimal email IMAP reader server. Serves inbox as JSON on :19876."""
import http.server
import imaplib
import json
import os
import email as email_lib
from email.header import decode_header
from dotenv import load_dotenv
from pathlib import Path

# Load profile env
profile_env = Path(os.environ.get("HERMES_HOME", "~/.hermes/profiles/dimitri")).expanduser() / ".env"
load_dotenv(profile_env)

HOST = os.getenv("EMAIL_IMAP_HOST", "imap.gmail.com")
PORT = 993
ADDR = os.getenv("EMAIL_ADDRESS", "")
PWD = os.getenv("EMAIL_PASSWORD") or os.getenv("GMAIL_APP_PASSWORD_DIMITRI") or os.getenv("GMAIL_APP_PASSWORD", "")


def decode(s):
    parts = decode_header(s or "")
    return "".join(
        p.decode(ch or "utf-8", errors="replace") if isinstance(p, bytes) else str(p)
        for p, ch in parts
    )


def fetch_inbox(limit=20):
    """Return list of {id, subject, from, date, snippet} from INBOX."""
    if not all([ADDR, PWD]):
        return {"error": "Email not configured"}
    try:
        imap = imaplib.IMAP4_SSL(HOST, PORT, timeout=15)
        imap.login(ADDR, PWD)
        imap.select("INBOX")
        _, data = imap.search(None, "ALL")
        ids = (data[0] or b"").split()[-limit:]
        mails = []
        for uid in ids:
            _, msg_data = imap.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            raw = msg_data[0][1] if msg_data and msg_data[0] else b""
            msg = email_lib.message_from_bytes(raw)
            subj = decode(msg.get("Subject", ""))
            frm = decode(msg.get("From", ""))
            dt = msg.get("Date", "")
            # Fetch body snippet
            _, body_data = imap.fetch(uid, "(BODY.PEEK[TEXT])")
            body_raw = body_data[0][1] if body_data and body_data[0] else b""
            snippet = body_raw.decode("utf-8", errors="replace")[:200].replace("\n", " ").strip()
            mails.append({
                "id": uid.decode() if isinstance(uid, bytes) else str(uid),
                "subject": subj,
                "from": frm,
                "date": dt,
                "snippet": snippet,
            })
        imap.logout()
        return mails
    except Exception as e:
        return {"error": str(e)}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/email/inbox":
            data = json.dumps(fetch_inbox()).encode()
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif self.path.startswith("/api/email/read/"):
            uid = self.path.split("/")[-1]
            # ponytail: full body read TBD
            self.send_response(501)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *a): pass


if __name__ == "__main__":
    srv = http.server.HTTPServer(("127.0.0.1", 19876), Handler)
    srv.allow_reuse_address = True
    print(f"Email API on :{19876} — {ADDR}")
    srv.serve_forever()
