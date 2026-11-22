# 選択したメールのスレッドを、返信案用の引用として出す。本文以外は出力しない。

import base64
import html
import json
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
MAX_MESSAGES = 8


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def parse_json(stdout):
    text = str(stdout or "").strip()
    start = min([index for index in (text.find("{"), text.find("[")) if index >= 0], default=-1)
    if start < 0:
        return {}
    return json.loads(text[start:])


def gws(args):
    bins = [str(HOME / ".local/bin/gws"), "gws"]
    missing = None
    for binary in bins:
        try:
            return subprocess.run([binary, *args], capture_output=True, text=True, timeout=60)
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            missing = error
    fail(getattr(missing, "message", None) or "gws が見つかりません")


def header(message, name):
    headers = ((message.get("payload") or {}).get("headers") or [])
    for item in headers:
        if str(item.get("name") or "").lower() == name.lower():
            return str(item.get("value") or "")
    return ""


def decode_data(data):
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return ""


def plain_text(source):
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", source)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|blockquote|table)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def collect(part, plain, html):
    if not isinstance(part, dict):
        return
    mime = str(part.get("mimeType") or "")
    body = decode_data((part.get("body") or {}).get("data")).strip()
    if mime == "text/plain" and body:
        plain.append(body)
    elif mime == "text/html" and body:
        html.append(plain_text(body))
    for child in part.get("parts") or []:
        collect(child, plain, html)


def message_text(message):
    plain, html = [], []
    collect(message.get("payload") or {}, plain, html)
    text = "\n".join(plain) if plain else "\n".join(item for item in html if item)
    if not text:
        text = str(message.get("snippet") or "").strip()
    return text


def main():
    if len(sys.argv) < 2 or not sys.argv[1]:
        fail("メールの id がありません")
    message_id = sys.argv[1]
    account = (sys.argv[2] if len(sys.argv) > 2 else "").strip().lower()
    looked = gws([
        "gmail", "users", "messages", "get",
        "--params", json.dumps({"userId": "me", "id": message_id, "format": "minimal"}),
        "--format", "json",
    ])
    if looked.returncode != 0:
        fail("メールを読めませんでした")
    thread_id = str(parse_json(looked.stdout).get("threadId") or message_id)
    fetched = gws([
        "gmail", "users", "threads", "get",
        "--params", json.dumps({"userId": "me", "id": thread_id, "format": "full"}),
        "--format", "json",
    ])
    if fetched.returncode != 0:
        fail("メールのやり取りを読めませんでした")
    messages = list((parse_json(fetched.stdout).get("messages") or []))
    messages.sort(key=lambda item: int(item.get("internalDate") or 0))
    messages = messages[-MAX_MESSAGES:]
    turns = []
    for message in messages:
        sender = header(message, "From")
        text = message_text(message)
        if not text:
            continue
        turns.append({
            "mine": bool(account) and account in sender.lower(),
            "at": header(message, "Date"),
            "text": text,
        })
    print(json.dumps({"ok": True, "turns": turns}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as error:
        fail(f"メールのやり取りを読めませんでした ({type(error).__name__})")
