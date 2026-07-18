# -*- coding: utf-8 -*-
"""
Автономное производство рилсов в GitHub Actions — БЕЗ обязательного LLM.

Источники контента (в порядке приоритета):
  1. DROP_* env (бот-команда «дроп <город> <язык>» -> repository_dispatch):
     разовый live-price-drop по шаблону templates.build_price_drop.
  2. Переделка: элемент queue.json со status=rendered и свежим decline в Supabase.
  3. Очередной queued (post_date <= сегодня+2):
       - template == "price-drop": шаблон + живые/переданные цифры;
       - script_file задан: рендер готового сценария из репо (scripts/<file>);
       - brief + есть ANTHROPIC_API_KEY: сценарий пишет Claude (опционально);
       - иначе: уведомить в Telegram «нужен сценарий».
Публикацией одобренного занимается publish-approved.yml.
"""
import glob
import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import templates  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPE = os.path.join(ROOT, "pipeline")
APP = os.environ.get("APP_URL", "https://flight-watch.onrender.com")
PREFIX = "reel-approval-"


def sb_headers():
    k = os.environ["SUPABASE_KEY"]
    return {"apikey": k, "Authorization": f"Bearer {k}", "Content-Type": "application/json"}


def latest_decisions(client):
    r = client.get(f"{os.environ['SUPABASE_URL']}/rest/v1/price_checks", headers=sb_headers(), params={
        "route_id": f"like.{PREFIX}*", "select": "route_id,note,checked_at",
        "order": "checked_at.desc", "limit": "300"})
    r.raise_for_status()
    out = {}
    for row in r.json():
        out.setdefault(row["route_id"][len(PREFIX):], row["note"] or "")
    return out


def mark(client, reel_id, note):
    client.post(f"{os.environ['SUPABASE_URL']}/rest/v1/price_checks", headers=sb_headers(), json={
        "route_id": f"{PREFIX}{reel_id}", "price": None, "currency": "EUR",
        "previous_price": None, "changed": False, "direction": "same", "status": "ok", "note": note})


def top_drops(client, days=7, n=5):
    since = (date.today() - timedelta(days=days)).isoformat()
    r = client.get(f"{os.environ['SUPABASE_URL']}/rest/v1/price_checks", headers=sb_headers(), params={
        "select": "route_id,price,previous_price,checked_at", "status": "eq.ok", "direction": "eq.down",
        "checked_at": f"gte.{since}", "order": "checked_at.desc", "limit": "300"})
    drops, seen = [], set()
    for row in sorted(r.json(), key=lambda x: -(((x.get("previous_price") or 0) - (x.get("price") or 0)))):
        p, pp = row.get("price"), row.get("previous_price")
        if p and pp and pp > 0 and not row["route_id"].startswith(PREFIX) and row["route_id"] not in seen:
            pct = (pp - p) / pp * 100
            if pct >= 8:
                seen.add(row["route_id"])
                drops.append({"route_id": row["route_id"], "from": int(pp), "to": int(p), "pct": round(pct, 1)})
        if len(drops) >= n:
            break
    return drops


def route_meta(client, route_id):
    """(title, dest_city) со страницы трекера."""
    try:
        html = client.get(f"{APP}/track/{route_id}", timeout=40).text
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else route_id
    except Exception:
        title = route_id
    city = re.split(r"[→>\-]", title)[-1].strip() if title else route_id
    city = re.sub(r"\(.*?\)", "", city).strip() or title
    return title, city


def record_page(path, out_mp4):
    from playwright.sync_api import sync_playwright
    rec_dir = os.path.join(PIPE, "tmp-rec")
    os.makedirs(rec_dir, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(viewport={"width": 540, "height": 960},
                            record_video_dir=rec_dir, record_video_size={"width": 540, "height": 960})
        pg = ctx.new_page()
        pg.goto(f"{APP}{path}", wait_until="networkidle", timeout=90000)
        pg.wait_for_timeout(3000)
        for y in range(0, 560, 20):
            pg.evaluate(f"window.scrollTo(0, {y})"); pg.wait_for_timeout(120)
        pg.wait_for_timeout(1500)
        ctx.close(); b.close()
    webm = max(glob.glob(os.path.join(rec_dir, "*.webm")), key=os.path.getmtime)
    run([find_bin("ffmpeg"), "-y", "-v", "error", "-i", webm, "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an", out_mp4])
    os.remove(webm)


def find_bin(name):
    hits = glob.glob(os.path.join(PIPE, "node_modules", f"{name}-static", "**", name), recursive=True) + \
           glob.glob(os.path.join(PIPE, "node_modules", f"{name}-static", "**", f"{name}.exe"), recursive=True)
    return hits[0] if hits else name


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd)[:200], flush=True)
    subprocess.run(cmd, check=True, **kw)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:24] or "city"


def gen_with_claude(client, job, live, clips_note, feedback):
    from claude_rules import PRODUCTION_RULES  # optional; only used if key present
    user = (f"Язык: {job['lang']}\nБриф: {job.get('brief', job['id'])}\n"
            f"Записи приложения: {clips_note}\n\nЖивые данные:\n{live}")
    if feedback:
        user += f"\n\nПЕРЕДЕЛКА. Правки (учесть): {feedback}"
    r = client.post("https://api.anthropic.com/v1/messages", timeout=120, headers={
        "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
        "content-type": "application/json"}, json={
        "model": "claude-sonnet-5", "max_tokens": 4000, "system": PRODUCTION_RULES,
        "messages": [{"role": "user", "content": user}]})
    r.raise_for_status()
    m = re.search(r"\{.*\}", r.json()["content"][0]["text"], re.S)
    d = json.loads(m.group(0))
    return d["script"], d["caption"], d.get("tg_summary", "")


def tg_text(client, text):
    httpx.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
               json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text}, timeout=20)


def tg_video(client, path, caption, reel_id):
    kb = {"inline_keyboard": [[
        {"text": "✅ Опубликовать", "callback_data": f"reel:{reel_id}:approve"},
        {"text": "❌ Переделать", "callback_data": f"reel:{reel_id}:decline"}]]}
    with open(path, "rb") as f:
        client.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendVideo",
                    data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "caption": caption[:1024],
                          "reply_markup": json.dumps(kb)},
                    files={"video": (os.path.basename(path), f, "video/mp4")}, timeout=600)


def render(reel_id, script, caption, meta, reel_dir, extra_note=""):
    os.makedirs(reel_dir, exist_ok=True)
    sp = os.path.join(PIPE, "job-script.json")
    json.dump(script, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    open(os.path.join(reel_dir, "caption.txt"), "w", encoding="utf-8").write(caption.strip() + "\n")
    json.dump(script, open(os.path.join(reel_dir, "script.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(meta, open(os.path.join(reel_dir, "meta.json"), "w", encoding="utf-8"))
    env = dict(os.environ); env["ANTHROPIC_API_KEY"] = ""
    raw = os.path.join(reel_dir, f"{reel_id}-raw.mp4")
    run(["npx", "ts-node", "src/index.ts", reel_id, "--script", sp,
         "--music", os.path.join("assets", "_ambient_music.wav"), "--out", raw], cwd=PIPE, env=env)
    post = os.path.join(reel_dir, f"{reel_id}-post.mp4")
    run([find_bin("ffmpeg"), "-y", "-v", "error", "-i", raw, "-c:v", "libx264", "-preset", "medium",
         "-crf", "26", "-maxrate", "1400k", "-bufsize", "2800k", "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", post])
    os.remove(raw)
    dur = float(subprocess.run([find_bin("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", post], capture_output=True, text=True, check=True).stdout.strip())
    assert 15 <= dur <= 95, f"duration {dur}s out of range"
    return post, dur


def git_push(reel_id, msg, also=()):
    run(["git", "config", "user.name", "reels-bot"], cwd=ROOT)
    run(["git", "config", "user.email", "reels-bot@users.noreply.github.com"], cwd=ROOT)
    run(["git", "add", reel_id, *also], cwd=ROOT)
    run(["git", "commit", "-m", msg], cwd=ROOT)
    run(["git", "push"], cwd=ROOT)


def main():
    with httpx.Client(timeout=90) as client:
        # -------- режим 1: команда-дроп от бота (repository_dispatch) --------
        drop_city = os.environ.get("DROP_CITY", "").strip()
        if drop_city:
            lang = os.environ.get("DROP_LANG", "lt").strip() or "lt"
            route = os.environ.get("DROP_ROUTE", "").strip()
            frm = os.environ.get("DROP_FROM", "").strip()
            to = os.environ.get("DROP_TO", "").strip()
            drops = top_drops(client)
            if route and (not frm or not to):
                for d in drops:
                    if d["route_id"] == route:
                        frm, to = str(d["from"]), str(d["to"]); break
            if not route and not (frm and to) and drops:
                d = drops[0]; route, frm, to = d["route_id"], str(d["from"]), str(d["to"])
            if not (frm and to):
                tg_text(client, f"⚠️ Дроп {drop_city}: не нашёл цифры (было/стало). Пришли так: дроп {drop_city} 200 140 {lang}")
                return
            src = "tracker-app.mp4"
            if route:
                try:
                    record_page(f"/track/{route}", os.path.join(PIPE, "input", "tracker-live.mp4"))
                    src = "tracker-live.mp4"
                except Exception as e:
                    print("record failed:", e)
            script, caption = templates.build_price_drop(drop_city, frm, to, lang, src)
            reel_id = f"drop-{slug(drop_city)}-{lang}"
            meta = {"post_date": date.today().isoformat(), "slot": "now", "lang": lang, "channel": "instagram"}
            post, dur = render(reel_id, script, caption, meta, os.path.join(ROOT, reel_id))
            git_push(reel_id, f"{reel_id}: live drop from bot command")
            tg_video(client, post,
                     f"🎬 Дроп-рилс по твоему сигналу — {drop_city} {frm}→{to}€ ({lang.upper()}, {dur:.0f}s)\n"
                     f"⚠️ качество в TG сжато, оценивай контент.\n\n📝 Подпись:\n————\n{caption}", reel_id)
            print("done: bot drop", reel_id)
            return

        # -------- очередь --------
        queue_path = os.path.join(ROOT, "queue.json")
        queue = json.load(open(queue_path, encoding="utf-8"))
        decisions = latest_decisions(client)

        job, feedback = None, None
        for it in queue["items"]:
            if it["status"] == "rendered" and decisions.get(it["id"], "").startswith("declined"):
                job = it
                feedback = decisions[it["id"]][len("declined"):].lstrip(": ").strip() or "сделай хук острее и динамику быстрее"
                break
        if job is None:
            horizon = (date.today() + timedelta(days=2)).isoformat()
            force = os.environ.get("FORCE_REEL_ID", "").strip()
            for it in queue["items"]:
                if force and it["id"] == force:
                    job = it; break
                if not force and it["status"] == "queued" and it["post_date"] <= horizon:
                    job = it; break
        if job is None:
            print("nothing to produce"); return
        print(f"JOB {job['id']} remake={bool(feedback)}")

        drops = top_drops(client)
        for d in drops:
            d["title"], d["city"] = route_meta(client, d["route_id"])
        live = "Топ падений (EUR):\n" + "\n".join(f"- {d['city']}: {d['from']}->{d['to']} (-{d['pct']}%)" for d in drops) if drops else "нет падений"

        # запись страницы, если нужна
        clips_note = "tracker-app.mp4, tracker-georgia.mp4, tracker-weekend.mp4"
        rec = job.get("record", "")
        chosen = None
        if rec:
            if rec == "auto-top-drop" and drops:
                chosen = drops[0]; path = f"/track/{chosen['route_id']}"
            elif rec.startswith("/"):
                path = rec
            else:
                path = ""
            if path:
                try:
                    record_page(path, os.path.join(PIPE, "input", "tracker-live.mp4"))
                    clips_note = "tracker-live.mp4 (СВЕЖАЯ, используй её), tracker-app.mp4"
                except Exception as e:
                    print("record failed:", e)

        # выбор способа генерации (без Claude по умолчанию)
        script = caption = tg_summary = None
        if job.get("template") == "price-drop":
            city = job.get("city") or (chosen["city"] if chosen else (drops[0]["city"] if drops else "Europa"))
            frm = job.get("from") or (chosen["from"] if chosen else (drops[0]["from"] if drops else 200))
            to = job.get("to") or (chosen["to"] if chosen else (drops[0]["to"] if drops else 140))
            src = "tracker-live.mp4" if clips_note.startswith("tracker-live") else "tracker-app.mp4"
            script, caption = templates.build_price_drop(str(city), frm, to, job["lang"], src)
            tg_summary = f"price-drop по шаблону: {city} {frm}->{to}"
        elif job.get("script_file"):
            sf = os.path.join(ROOT, "scripts", job["script_file"])
            script = json.load(open(sf, encoding="utf-8"))
            cf = os.path.join(ROOT, "scripts", job.get("caption_file", ""))
            caption = open(cf, encoding="utf-8").read() if os.path.exists(cf) else job.get("caption", "https://flight-watch.onrender.com")
            tg_summary = "готовый сценарий из репо"
        elif job.get("brief") and os.environ.get("ANTHROPIC_API_KEY"):
            script, caption, tg_summary = gen_with_claude(client, job, live, clips_note, feedback)
        else:
            tg_text(client, f"✍️ Для «{job['id']}» ({job['lang']}) нужен сценарий, а Claude-ключа нет.\n"
                            f"Вариант: пришли как price-drop-команду, либо я заготовлю сценарий-файл.\nБриф: {job.get('brief','')[:300]}")
            return

        reel_id = job["id"]
        meta = {"post_date": job["post_date"], "slot": job.get("slot", "12:00"),
                "lang": job["lang"], "channel": job.get("channel", "instagram")}
        post, dur = render(reel_id, script, caption, meta, os.path.join(ROOT, reel_id))

        job["status"] = "rendered"
        json.dump(queue, open(queue_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        git_push(reel_id, f"{reel_id}: rendered in CI ({'remake' if feedback else 'scheduled'})", also=("queue.json",))
        if feedback:
            mark(client, reel_id, "resent")

        head = "🔁 ПЕРЕДЕЛКА" if feedback else "🎬 Новый рилс (собран автоматически)"
        tg_video(client, post,
                 f"{head} — {reel_id} ({job['lang'].upper()}, {dur:.0f}s)\n"
                 f"⏰ Слот: {job['post_date']} {job.get('slot','')} — после ✅ опубликуется сам.\n{tg_summary}\n"
                 f"⚠️ качество в TG сжато, оценивай контент.\n\n📝 Подпись:\n————\n{caption.strip()}", reel_id)
        print("done:", reel_id)


if __name__ == "__main__":
    main()
