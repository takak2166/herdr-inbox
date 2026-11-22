# 受信の X 欄を更新する。PIN と、この PC に既にある X の接続を使い、
# data/inbox/x.json を書く。PIN、接続情報、本文は出力しない。

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
PIN_PATH = HOME / ".config" / "CHAT_PIN"
OUT_PATH = HOME / ".config" / "herdr-app" / "data" / "inbox" / "x.json"
JUICEBOX_PATH = HOME / ".config" / "xchat-grokbot-helper" / "juicebox.json"
HELPER_ROOT = HOME / "xchat-lite"
API = "https://api.x.com/2"
MAX_CONVERSATIONS = 20
MAX_EVENTS = 8


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


def report(payload):
    print(json.dumps(payload, ensure_ascii=False))


def access_token():
    root = HOME / ".cursor/projects"
    paths = sorted(root.glob("*/mcp-auth.json"), key=lambda path: ("herdr-app" not in path.parent.name, -path.stat().st_mtime)) if root.is_dir() else []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        token = ((data.get("plugin-x-x") or {}).get("tokens") or {}).get("access_token") or ""
        if token:
            return token
    return ""


def api_get(token, path, query=None):
    url = f"{API}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            fail("X の接続が切れています。Cursor で X を開き直してください")
        fail(f"X の取得に失敗しました ({error.code})")
    except OSError:
        fail("X に接続できませんでした")


def conversations(token):
    rows = []
    users = {}
    page = None
    while len(rows) < MAX_CONVERSATIONS:
        query = {
            "max_results": min(50, MAX_CONVERSATIONS - len(rows)),
            "is_archived": "false",
            "chat_conversation.fields": "type,unread,group_name",
            "expansions": "participant_ids",
            "user.fields": "name,username,profile_image_url",
        }
        if page:
            query["pagination_token"] = page
        body = api_get(token, "/chat/conversations", query)
        for user in (body.get("includes") or {}).get("users") or []:
            if user.get("id"):
                users[str(user["id"])] = user
        rows.extend(body.get("data") or [])
        meta = body.get("meta") or {}
        page = meta.get("next_token") if meta.get("has_more") else None
        if not page:
            break
    return rows[:MAX_CONVERSATIONS], users


def public_keys(token, user_ids):
    found = []
    ids = [user_id for user_id in dict.fromkeys(user_ids) if user_id]
    for start in range(0, len(ids), 100):
        body = api_get(token, "/users/public_keys", {
            "ids": ",".join(ids[start:start + 100]),
            "public_key.fields": "public_key_version,public_key,signing_public_key,identity_public_key_signature,juicebox_config,user_id",
        })
        found.extend(body.get("data") or [])
    return found


def events(token, conversation_id, limit=MAX_EVENTS):
    quoted = urllib.parse.quote(conversation_id, safe="")
    body = api_get(token, f"/chat/conversations/{quoted}/events", {
        "max_results": limit,
        "chat_message_event.fields": "sender_id,created_at,encoded_event",
    })
    rows = body.get("data") or []
    meta = body.get("meta") or {}
    return rows, meta.get("conversation_key_events") or []


def api_get_optional(token, path, query=None):
    url = f"{API}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (urllib.error.HTTPError, OSError, json.JSONDecodeError):
        return None


def fill_profiles(token, users, user_ids):
    missing = [str(user_id) for user_id in dict.fromkeys(user_ids) if str(user_id).isdigit() and not (users.get(str(user_id)) or {}).get("profile_image_url")]
    for start in range(0, len(missing), 100):
        body = api_get_optional(token, "/users", {
            "ids": ",".join(missing[start:start + 100]),
            "user.fields": "name,username,profile_image_url",
        })
        if not body:
            return
        for user in body.get("data") or []:
            user_id = str(user.get("id") or "")
            if user_id:
                users[user_id] = {**(users.get(user_id) or {}), **user}


def partner_id(conversation, me):
    if conversation.get("type") == "group":
        return ""
    others = [str(user_id) for user_id in conversation.get("participant_ids") or [] if str(user_id) != me]
    return others[0] if others else me


def circle_png(src, dest):
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
             "-vf", "format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='lte(hypot(X-W/2,Y-H/2),min(W,H)/2)*255'",
             "-frames:v", "1", str(dest)],
            capture_output=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and dest.is_file() and dest.stat().st_size > 32


def save_avatar(user):
    user_id = str((user or {}).get("id") or "")
    url = str((user or {}).get("profile_image_url") or "")
    if not user_id.isdigit() or not url.startswith("https://pbs.twimg.com/profile_images/"):
        return ""
    url = re.sub(r"_normal(\.[A-Za-z0-9]+)$", r"_200x200\1", url)
    folder = OUT_PATH.parent / "avatars"
    raw = folder / f"{user_id}.src"
    dest = folder / f"{user_id}.png"
    try:
        request = urllib.request.Request(url)
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read(2_000_000)
        if len(data) < 32:
            return ""
        folder.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(data)
        if not circle_png(raw, dest):
            return ""
        os.chmod(dest, 0o600)
    except (urllib.error.HTTPError, OSError):
        if not dest.is_file():
            return ""
    finally:
        raw.unlink(missing_ok=True)
    return f"data/inbox/avatars/{dest.name}"


def title_for(conversation, users, me):
    if conversation.get("type") == "group":
        return conversation.get("group_name") or "グループ"
    others = [user_id for user_id in conversation.get("participant_ids") or [] if user_id != me]
    if not others:
        return "自分"
    user = users.get(str(others[0])) or {}
    return user.get("name") or user.get("username") or "DM"


def one_line(text):
    collapsed = " ".join(str(text or "").split())
    return collapsed[:160]


def status_ids(text):
    found = []
    for match in re.finditer(r"https?://(?:x|twitter)\.com/(?:[^/\s]+/)?status/(\d+)", text or ""):
        found.append(match.group(1))
    for match in re.finditer(r"\[post:\s*(\d+)\]", text or ""):
        found.append(match.group(1))
    return list(dict.fromkeys(found))


def fetch_posts(token, ids):
    found = {}
    unique = [item for item in dict.fromkeys(ids) if item]
    for start in range(0, len(unique), 100):
        chunk = unique[start:start + 100]
        try:
            body = api_get(token, "/tweets", {
                "ids": ",".join(chunk),
                "tweet.fields": "text,author_id,created_at",
                "expansions": "author_id",
                "user.fields": "name,username",
            })
        except SystemExit:
            return found
        users = {user.get("id"): user for user in ((body.get("includes") or {}).get("users") or [])}
        for tweet in body.get("data") or []:
            author = users.get(tweet.get("author_id")) or {}
            found[str(tweet.get("id"))] = {
                "id": str(tweet.get("id") or ""),
                "name": author.get("name") or "",
                "username": author.get("username") or "",
                "text": str(tweet.get("text") or "")[:280],
            }
    return found


def posts_in(text, looked_up):
    return [looked_up[item] for item in status_ids(text) if item in looked_up]


def preview_line(text, looked_up):
    text = str(text or "").strip()
    if not text or text.startswith("[could not"):
        return ""
    leftover = re.sub(r"\[post:\s*[^\]]+\]", " ", text)
    leftover = " ".join(leftover.split())
    if leftover:
        return one_line(leftover)
    posts = posts_in(text, looked_up)
    if not posts:
        return one_line(text)
    post = posts[0]
    who = post.get("name") or (f"@{post['username']}" if post.get("username") else "投稿")
    return one_line(f"{who}: {post.get('text') or ''}")


def open_chat(pin, me, version, juicebox):
    if not (HELPER_ROOT / "xchat_lite.py").is_file():
        fail("X の復号ヘルパーがありません")
    sys.path.insert(0, str(HELPER_ROOT))
    os.environ["CHAT_PIN"] = pin
    import xchat_lite  # noqa: E402

    args = type("Args", (), {})()
    args.user_id = me
    args.key_version = version
    args.juicebox = str(juicebox)
    args.remember_key = False

    chat, _, _ = xchat_lite._open_session(args)
    return chat, xchat_lite._decrypt


def decrypt_messages(chat, decrypt, encoded, key_events, signing_keys):
    if not encoded:
        return []
    result = decrypt(chat, {
        "events": encoded,
        "conversation_key_events": key_events,
        "signing_keys": {"data": signing_keys},
    })
    return result.get("messages") or []


def thread_main(conversation_id):
    if not PIN_PATH.is_file():
        fail("チャット PIN がありません")
    pin = PIN_PATH.read_text(encoding="utf-8").strip()
    if not pin:
        fail("チャット PIN がありません")
    token = access_token()
    if not token:
        fail("X の接続がありません。Cursor で X を接続してください")
    me = str((api_get(token, "/users/me").get("data") or {}).get("id") or "")
    if not me:
        fail("X のアカウントを確認できませんでした")
    event_rows, key_events = events(token, conversation_id, 20)
    sender_ids = [row.get("sender_id") for row in event_rows if row.get("sender_id")]
    keys = public_keys(token, [me, *sender_ids])
    mine = next((key for key in keys if str(key.get("user_id") or "") == me), None)
    if not mine or not mine.get("juicebox_config") or not mine.get("public_key_version"):
        fail("X のチャット鍵を読めませんでした")
    JUICEBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
    JUICEBOX_PATH.write_text(json.dumps(mine["juicebox_config"]), encoding="utf-8")
    os.chmod(JUICEBOX_PATH, 0o600)
    chat, decrypt = open_chat(pin, me, str(mine["public_key_version"]), JUICEBOX_PATH)
    encoded_rows = [row for row in event_rows if row.get("encoded_event")]
    decoded = decrypt_messages(
        chat, decrypt,
        [row["encoded_event"] for row in encoded_rows],
        key_events, keys,
    )
    texts = [str((message or {}).get("text") or "") for message in decoded]
    looked_up = fetch_posts(token, [item for text in texts for item in status_ids(text)])
    turns = []
    for row, message in zip(encoded_rows, decoded):
        text = str((message or {}).get("text") or "").strip()
        if not text or text.startswith("[could not"):
            continue
        turns.append({
            "mine": str(row.get("sender_id") or "") == me,
            "at": row.get("created_at") or "",
            "text": text,
            "posts": posts_in(text, looked_up),
        })
    turns.reverse()
    report({"ok": True, "turns": turns})


def main():
    if not PIN_PATH.is_file():
        report({"ok": True, "skipped": "pin"})
        return
    pin = PIN_PATH.read_text(encoding="utf-8").strip()
    if not pin:
        report({"ok": True, "skipped": "pin"})
        return
    token = access_token()
    if not token:
        fail("X の接続がありません。Cursor で X を接続してください")

    me_body = api_get(token, "/users/me")
    me = str((me_body.get("data") or {}).get("id") or "")
    if not me:
        fail("X のアカウントを確認できませんでした")

    rows, users = conversations(token)
    fill_profiles(token, users, [me, *(user_id for row in rows for user_id in (row.get("participant_ids") or []))])
    bundles = []
    sender_ids = []
    for conversation in rows:
        try:
            event_rows, key_events = events(token, conversation.get("id") or "")
        except SystemExit:
            raise
        except Exception:
            event_rows, key_events = [], []
        sender_ids.extend(row.get("sender_id") for row in event_rows if row.get("sender_id"))
        bundles.append((conversation, event_rows, key_events))

    keys = public_keys(token, [me, *sender_ids, *(users.keys())])
    mine = next((key for key in keys if str(key.get("user_id") or "") == me), None)
    if not mine or not mine.get("juicebox_config") or not mine.get("public_key_version"):
        fail("X のチャット鍵を読めませんでした")
    JUICEBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
    JUICEBOX_PATH.write_text(json.dumps(mine["juicebox_config"]), encoding="utf-8")
    os.chmod(JUICEBOX_PATH, 0o600)

    chat, decrypt = open_chat(pin, me, str(mine["public_key_version"]), JUICEBOX_PATH)
    decoded_bundles = []
    post_ids = []
    for conversation, event_rows, key_events in bundles:
        encoded = [row.get("encoded_event") for row in event_rows if row.get("encoded_event")]
        messages = decrypt_messages(chat, decrypt, encoded, key_events, keys)
        decoded_bundles.append((conversation, event_rows, messages))
        for message in messages:
            post_ids.extend(status_ids(message.get("text")))
    looked_up = fetch_posts(token, post_ids)
    conversations_out = []
    for conversation, event_rows, messages in decoded_bundles:
        preview = ""
        for message in messages:
            preview = preview_line(message.get("text"), looked_up)
            if preview:
                break
        when = event_rows[0].get("created_at") if event_rows else ""
        conversations_out.append({
            "id": str(conversation.get("id") or ""),
            "title": title_for(conversation, users, me),
            "preview": preview,
            "at": when or "",
            "unread": bool(conversation.get("unread")),
            "icon": save_avatar(users.get(partner_id(conversation, me))),
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({
        "updatedAt": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "conversations": conversations_out,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(OUT_PATH)
    report({
        "ok": True,
        "n": len(conversations_out),
        "with_preview": sum(1 for row in conversations_out if row["preview"]),
        "with_icon": sum(1 for row in conversations_out if row["icon"]),
    })


if __name__ == "__main__":
    try:
        if len(sys.argv) >= 3 and sys.argv[1] == "--conversation":
            thread_main(sys.argv[2])
        else:
            main()
    except SystemExit:
        raise
    except Exception as error:
        fail(f"X の更新に失敗しました ({type(error).__name__})")
