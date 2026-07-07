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
from datetime import date

import httpx

BUFFER_API = "https://api.buffer.com/graphql"
PREFIX = "reel-approval-"
REPO = os.environ.get("GITHUB_REPOSITORY", "tyrila66-maker/fw-reels")


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


def resolve_channel_id(client: httpx.Client) -> str:
    """Env override, else the first connected Instagram channel."""
    forced = os.environ.get("BUFFER_CHANNEL_ID", "").strip()
    if forced:
        return forced
    r = client.post(BUFFER_API, headers=buffer_headers(),
                    json={"query": "{ account { organizations { id } } }"})
    orgs = r.json().get("data", {}).get("account", {}).get("organizations", [])
    if not orgs:
        raise RuntimeError("Buffer: no organizations for this token")
    oid = orgs[0]["id"]
    q = '{ channels(input:{organizationId:"%s"}){ id service } }' % oid
    r = client.post(BUFFER_API, headers=buffer_headers(), json={"query": q})
    for c in r.json().get("data", {}).get("channels", []) or []:
        if c.get("service") == "instagram":
            return c["id"]
    raise RuntimeError("Buffer: no Instagram channel connected")


CREATE_POST = """mutation($input: CreatePostInput!){
  createPost(input: $input){ __typename
    ... on PostActionSuccess { post { id status } }
    ... on InvalidInputError { message }
    ... on UnexpectedError { message }
    ... on LimitReachedError { message }
    ... on UnauthorizedError { message }
    ... on NotFoundError { message }
    ... on RestProxyError { message } } }"""


def publish_reel(client: httpx.Client, channel_id: str, video_url: str, caption: str) -> str:
    variables = {"input": {
        "channelId": channel_id,
        "schedulingType": "automatic",
        "mode": "shareNow",
        "text": caption,
        "assets": [{"video": {"url": video_url}}],
        "metadata": {"instagram": {"type": "reel", "shouldShareToFeed": True}},
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

        channel_id = None
        today = date.today().isoformat()

        for reel_id in approved:
            videos = glob.glob(f"{reel_id}/*-post.mp4")
            caption_path = os.path.join(reel_id, "caption.txt")
            meta_path = os.path.join(reel_id, "meta.json")
            if not videos or not os.path.exists(caption_path):
                print(f"skip {reel_id}: files not found")
                continue

            post_date = "1970-01-01"
            if os.path.exists(meta_path):
                post_date = json.load(open(meta_path, encoding="utf-8")).get("post_date", post_date)
            if today < post_date:
                print(f"hold {reel_id}: post_date {post_date} is in the future")
                continue

            caption = open(caption_path, encoding="utf-8").read().strip()
            video = videos[0].replace(os.sep, "/")
            video_url = f"https://raw.githubusercontent.com/{REPO}/main/{video}"

            if channel_id is None:
                channel_id = resolve_channel_id(client)

            print(f"publishing {reel_id} -> {video_url}")
            try:
                post_id = publish_reel(client, channel_id, video_url, caption)
            except Exception as e:
                notify(client, f"⚠️ Автопубликация <b>{reel_id}</b> не удалась: {str(e)[:200]}")
                print(f"FAILED {reel_id}: {e}", file=sys.stderr)
                continue
            mark_published(client, reel_id)
            notify(client, f"🎉 <b>{reel_id}</b> отправлен в Instagram через Buffer (post {post_id})")
            print(f"PUBLISHED {reel_id}: {post_id}")


if __name__ == "__main__":
    main()
