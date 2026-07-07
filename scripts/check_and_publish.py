# -*- coding: utf-8 -*-
"""
Runs in GitHub Actions every 15 minutes: finds reels approved via the Telegram
buttons (decisions live in Supabase, written by the flight-watch webhook) and
publishes them to Instagram through the official Graph API.

Required repo secrets: SUPABASE_URL, SUPABASE_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
Publishing additionally needs IG_ACCESS_TOKEN + IG_USER_ID (Meta app token with
instagram_content_publish); until they are set the script reports and exits 0.

Publish strategy: resumable upload (rupload.facebook.com) from the repo checkout,
so the repo can stay private. Falls back to raw.githubusercontent.com video_url
(requires the repo to be public) if the resumable path fails.
"""
import glob
import json
import os
import sys
import time

import httpx

GRAPH = "https://graph.facebook.com/v21.0"
PREFIX = "reel-approval-"


def sb_headers():
    key = os.environ["SUPABASE_KEY"]
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def latest_decisions(client: httpx.Client) -> dict:
    """reel_id -> latest note (approved / declined / published / resent ...)."""
    url = f"{os.environ['SUPABASE_URL']}/rest/v1/price_checks"
    r = client.get(url, headers=sb_headers(), params={
        "route_id": f"like.{PREFIX}*",
        "select": "route_id,note,checked_at",
        "order": "checked_at.desc",
        "limit": "200",
    })
    r.raise_for_status()
    decisions = {}
    for row in r.json():
        rid = row["route_id"][len(PREFIX):]
        decisions.setdefault(rid, row["note"] or "")
    return decisions


def mark_published(client: httpx.Client, reel_id: str):
    url = f"{os.environ['SUPABASE_URL']}/rest/v1/price_checks"
    client.post(url, headers=sb_headers(), json={
        "route_id": f"{PREFIX}{reel_id}", "price": None, "currency": "EUR",
        "previous_price": None, "changed": False, "direction": "same",
        "status": "ok", "note": "published",
    })


def notify(client: httpx.Client, text: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if token and chat:
        client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat, "text": text, "parse_mode": "HTML"})


def wait_container(client: httpx.Client, container_id: str, token: str, minutes: int = 10):
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        r = client.get(f"{GRAPH}/{container_id}",
                       params={"fields": "status_code", "access_token": token})
        status = r.json().get("status_code", "")
        print(f"  container status: {status}")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(f"container error: {r.json()}")
        time.sleep(15)
    raise RuntimeError("container processing timeout")


def publish_resumable(client: httpx.Client, ig_user: str, token: str,
                      video_path: str, caption: str) -> str:
    r = client.post(f"{GRAPH}/{ig_user}/media", data={
        "media_type": "REELS", "upload_type": "resumable",
        "caption": caption, "share_to_feed": "true", "access_token": token,
    })
    body = r.json()
    if "id" not in body or "uri" not in body:
        raise RuntimeError(f"resumable container failed: {body.get('error', body)}")
    container_id, upload_uri = body["id"], body["uri"]

    size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        r = client.post(upload_uri, content=f.read(), headers={
            "Authorization": f"OAuth {token}",
            "offset": "0", "file_size": str(size),
        }, timeout=600)
    if not r.json().get("success", False):
        raise RuntimeError(f"binary upload failed: {r.text[:300]}")

    wait_container(client, container_id, token)
    r = client.post(f"{GRAPH}/{ig_user}/media_publish",
                    data={"creation_id": container_id, "access_token": token})
    body = r.json()
    if "id" not in body:
        raise RuntimeError(f"media_publish failed: {body.get('error', body)}")
    return body["id"]


def publish_by_url(client: httpx.Client, ig_user: str, token: str,
                   video_url: str, caption: str) -> str:
    r = client.post(f"{GRAPH}/{ig_user}/media", data={
        "media_type": "REELS", "video_url": video_url,
        "caption": caption, "share_to_feed": "true", "access_token": token,
    })
    body = r.json()
    if "id" not in body:
        raise RuntimeError(f"url container failed: {body.get('error', body)}")
    wait_container(client, body["id"], token)
    r = client.post(f"{GRAPH}/{ig_user}/media_publish",
                    data={"creation_id": body["id"], "access_token": token})
    out = r.json()
    if "id" not in out:
        raise RuntimeError(f"media_publish failed: {out.get('error', out)}")
    return out["id"]


def main():
    with httpx.Client(timeout=60) as client:
        decisions = latest_decisions(client)
        approved = [rid for rid, note in decisions.items() if note == "approved"]
        print(f"decisions: {decisions} | to publish: {approved}")
        if not approved:
            return

        token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
        ig_user = os.environ.get("IG_USER_ID", "").strip()

        for reel_id in approved:
            videos = glob.glob(f"{reel_id}/*-post.mp4")
            caption_path = os.path.join(reel_id, "caption.txt")
            if not videos or not os.path.exists(caption_path):
                print(f"skip {reel_id}: files not found in repo")
                continue
            if not token or not ig_user:
                print(f"{reel_id} approved, but IG_ACCESS_TOKEN/IG_USER_ID secrets are not set — waiting")
                continue
            caption = open(caption_path, encoding="utf-8").read().strip()
            video = videos[0]
            print(f"publishing {reel_id} from {video}")
            try:
                try:
                    media_id = publish_resumable(client, ig_user, token, video, caption)
                except Exception as e:
                    print(f"resumable failed ({e}); trying raw URL fallback")
                    repo = os.environ.get("GITHUB_REPOSITORY", "tyrila66-maker/fw-reels")
                    raw = f"https://raw.githubusercontent.com/{repo}/main/{video.replace(os.sep, '/')}"
                    media_id = publish_by_url(client, ig_user, token, raw, caption)
            except Exception as e:
                notify(client, f"⚠️ Автопубликация <b>{reel_id}</b> не удалась: {str(e)[:200]}")
                print(f"FAILED {reel_id}: {e}", file=sys.stderr)
                continue
            mark_published(client, reel_id)
            notify(client, f"🎉 <b>{reel_id}</b> опубликован в Instagram автоматически (media {media_id})")
            print(f"PUBLISHED {reel_id}: media {media_id}")


if __name__ == "__main__":
    main()
