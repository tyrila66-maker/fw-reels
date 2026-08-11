# -*- coding: utf-8 -*-
"""
Runs in GitHub Actions every 15 minutes: finds reels approved via the Telegram
buttons (decisions live in Supabase, written by the flight-watch webhook) and
publishes them to Instagram through Buffer's GraphQL API.

Each reel folder has meta.json with a post_date — a reel is only published on or
after that date, so approving early is safe (RU can wait while EN goes today).

Required repo secrets: SUPABASE_URL, SUPABASE_KEY, TELEGRAM_BOT_TOKEN,
TELEGRAM_CHAT_ID, BUFFER_ACCESS_TOKEN. Optional: BUFFER_CHANNEL_ID (otherwise the
first connected Instagram channel is used). Video is fetched by Buffer from the
public raw.githubusercontent.com URL, so this repo must stay public.
"""
import glob
import json
import os
import sys
import time
from datetime import date

import httpx

BUFFER_API = "https://api.buffer.com/graphql"
PREFIX = "reel-approval-"
REPO = os.environ.get("GITHUB_REPOSITORY", "tyrila66-maker/fw-reels")

# Buffer fetches the video from raw.githubusercontent.com, which can lag a few
# minutes behind a fresh push. These messages mean "try the exact same request
# again in a moment", not "this reel is broken".
RETRYABLE = ("could not be read", "could not be downloaded", "not be found",
             "restproxyerror", "unexpectederror", "try again", "timeout",
             "temporarily", "processing")


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


def buffer_headers():
    return {"Authorization": f"Bearer {os.environ['BUFFER_ACCESS_TOKEN']}",
            "Content-Type": "application/json"}


def resolve_channels(client: httpx.Client) -> dict:
    """{service: channel_id} for all connected Buffer channels."""
    r = client.post(BUFFER_API, headers=buffer_headers(),
                    json={"query": "{ account { organizations { id } } }"})
    orgs = r.json().get("data", {}).get("account", {}).get("organizations", [])
    if not orgs:
        raise RuntimeError("Buffer: no organizations for this token")
    oid = orgs[0]["id"]
    q = '{ channels(input:{organizationId:"%s"}){ id service } }' % oid
    r = client.post(BUFFER_API, headers=buffer_headers(), json={"query": q})
    return {c["service"]: c["id"] for c in (r.json().get("data", {}).get("channels", []) or [])}


CREATE_POST = """mutation($input: CreatePostInput!){
  createPost(input: $input){ __typename
    ... on PostActionSuccess { post { id status } }
    ... on InvalidInputError { message }
    ... on UnexpectedError { message }
    ... on LimitReachedError { message }
    ... on UnauthorizedError { message }
    ... on NotFoundError { message }
    ... on RestProxyError { message } } }"""


def wait_for_raw(client: httpx.Client, url: str, tries: int = 6, delay: int = 8) -> bool:
    """Poll the public raw URL until the CDN serves it (200), so Buffer's own
    fetcher is likely to see it too. Returns False if it never became ready."""
    for i in range(tries):
        try:
            r = client.get(url, headers={"Range": "bytes=0-1023"}, timeout=20)
            if r.status_code in (200, 206):
                return True
        except httpx.HTTPError:
            pass
        if i < tries - 1:
            time.sleep(delay)
    return False


def publish_reel(client: httpx.Client, channel_id: str, video_url: str, caption: str, channel: str = "instagram") -> str:
    meta = {"instagram": {"type": "reel", "shouldShareToFeed": True}} if channel == "instagram" else {channel: {}}
    variables = {"input": {
        "channelId": channel_id,
        "schedulingType": "automatic",
        "mode": "shareNow",
        "text": caption,
        "assets": [{"video": {"url": video_url}}],
        "metadata": meta,
    }}
    r = client.post(BUFFER_API, headers=buffer_headers(),
                    json={"query": CREATE_POST, "variables": variables}, timeout=120)
    body = r.json()
    if "errors" in body:
        raise RuntimeError(f"Buffer GraphQL: {body['errors'][:1]}")
    result = body["data"]["createPost"]
    if result["__typename"] != "PostActionSuccess":
        raise RuntimeError(f"Buffer: {result['__typename']} — {result.get('message')}")
    return result["post"]["id"]


def main():
    with httpx.Client(timeout=60) as client:
        decisions = latest_decisions(client)
        approved = [rid for rid, note in decisions.items() if note == "approved"]
        print(f"decisions: {decisions} | approved: {approved}")
        if not approved:
            return

        token = os.environ.get("BUFFER_ACCESS_TOKEN", "").strip()
        if not token:
            print("BUFFER_ACCESS_TOKEN not set — waiting")
            return

        channels = None
        today = date.today().isoformat()

        for reel_id in approved:
            videos = glob.glob(f"{reel_id}/*-post.mp4")
            caption_path = os.path.join(reel_id, "caption.txt")
            meta_path = os.path.join(reel_id, "meta.json")
            if not videos or not os.path.exists(caption_path):
                print(f"skip {reel_id}: files not found")
                continue

            post_date, channel = "1970-01-01", "instagram"
            if os.path.exists(meta_path):
                m = json.load(open(meta_path, encoding="utf-8"))
                post_date = m.get("post_date", post_date)
                channel = m.get("channel", "instagram")
            if today < post_date:
                print(f"hold {reel_id}: post_date {post_date} is in the future")
                continue

            caption = open(caption_path, encoding="utf-8").read().strip()
            video = videos[0].replace(os.sep, "/")
            video_url = f"https://raw.githubusercontent.com/{REPO}/main/{video}"

            if channels is None:
                channels = resolve_channels(client)
            channel_id = channels.get(channel)
            if not channel_id:
                notify(client, f"⚠️ <b>{reel_id}</b>: канал {channel} не подключён в Buffer")
                print(f"skip {reel_id}: no {channel} channel")
                continue

            print(f"publishing {reel_id} -> [{channel}] {video_url}")
            if not wait_for_raw(client, video_url):
                print(f"hold {reel_id}: raw URL not yet served by CDN, retry next run")
                continue

            post_id, last_err = None, None
            for attempt in range(3):
                try:
                    post_id = publish_reel(client, channel_id, video_url, caption, channel)
                    break
                except Exception as e:
                    last_err = e
                    if any(p in str(e).lower() for p in RETRYABLE) and attempt < 2:
                        print(f"retry {reel_id} (attempt {attempt+1}): {e}")
                        time.sleep(20)
                        continue
                    break
            if post_id is None:
                # Transient CDN errors: stay 'approved' so the next cron run retries.
                if any(p in str(last_err).lower() for p in RETRYABLE):
                    print(f"hold {reel_id}: transient error, retry next run: {last_err}")
                    continue
                notify(client, f"⚠️ Автопубликация <b>{reel_id}</b> не удалась: {str(last_err)[:200]}")
                print(f"FAILED {reel_id}: {last_err}", file=sys.stderr)
                continue
            mark_published(client, reel_id)
            notify(client, f"🎉 <b>{reel_id}</b> отправлен в {channel} через Buffer (post {post_id})")
            print(f"PUBLISHED {reel_id}: {post_id}")


if __name__ == "__main__":
    main()
