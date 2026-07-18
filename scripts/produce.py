# -*- coding: utf-8 -*-
"""
Автономное производство рилсов в GitHub Actions.

Цикл: queue.json -> (переделки по decline / очередной queued с близкой датой)
 -> живые данные из Supabase -> сценарий+caption через Claude API (правила зашиты ниже)
 -> при необходимости запись страницы приложения (Playwright) -> рендер пайплайном
 -> пережатие -> коммит в репо -> видео Павлу в Telegram с кнопками ✅/❌.

Публикацией одобренного занимается publish-approved.yml. Env см. produce-reels.yml.
"""
import glob
import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPE = os.path.join(ROOT, "pipeline")
APP = os.environ.get("APP_URL", "https://flight-watch.onrender.com")
PREFIX = "reel-approval-"

PRODUCTION_RULES = """Ты пишешь сценарий короткого вертикального видео (reel) для Flight Price Watch
(https://flight-watch.onrender.com — бесплатный трекер цен на авиабилеты из Вильнюса и Варшавы) и подпись к посту.

ЖЕЛЕЗНЫЕ ПРАВИЛА СЦЕНАРИЯ:
1. ХУК В ПЕРВЫЕ 2 СЕКУНДЫ: первая реплика — самая сильная фраза/цифра/вопрос, без разгона и логотипов.
2. Ровно 8 реплик (index 0..7): hook -> развитие (scene/problem) -> solution (приложение) -> cta.
3. Каждая реплика 6-14 слов, разговорная, для озвучки. ВСЕ числа писать словами на языке ролика.
4. Язык ролика задан. Для литовского и польского: никаких коротких обрывочных фраз (TTS уходит в чужой акцент),
   каждая реплика — полное предложение с однозначным языковым контекстом.
5. Сцена solution ОБЯЗАНА использовать запись реального приложения: если указан клип tracker-live.mp4 —
   поставь "sourceClip": "tracker-live.mp4", "clipStartSec": 2.0; иначе "tracker-app.mp4" с clipStartSec 2.5.
6. visualPrompt каждой сцены — англ. запрос для стокового видео (Pexels), конкретный и живой.
7. Если даны живые данные цен — используй РЕАЛЬНЫЕ цифры, ничего не выдумывай. Видео не должно
   противоречить записи трекера (например, не говори «цена упала», если в данных рост).

ПРАВИЛА CAPTION (может отличаться от текста видео, играй словами):
- Первая строка: ключевик рынка + ссылка https://flight-watch.onrender.com
  (lt: 'pigūs skrydžiai...', pl: 'tanie loty z Warszawy...', ru: 'дешёвые билеты...', en: 'cheap flights...').
- История/крючок 2-4 строки, эмодзи умеренно, конкретные цифры если есть.
- CTA на пересылку другу (это главный сигнал алгоритма), НЕ на лайк.
- Хэштеги: ровно 3-5, среднего размера, в конце.

ОТВЕТ — СТРОГО JSON без пояснений:
{"script": {"topic": "...", "language": "<lithuanian|polish|russian|english>", "lines": [{"index": 0, "section": "hook", "text": "...", "visualPrompt": "..."}, ...]},
 "caption": "...",
 "tg_summary": "1-2 строки по-русски: что за ролик и какой хук"}"""


def sb_headers():
    k = os.environ["SUPABASE_KEY"]
    return {"apikey": k, "Authorization": f"Bearer {k}", "Content-Type": "application/json"}


def latest_decisions(client: httpx.Client) -> dict:
    r = client.get(f"{os.environ['SUPABASE_URL']}/rest/v1/price_checks", headers=sb_headers(), params={
        "route_id": f"like.{PREFIX}*", "select": "route_id,note,checked_at",
        "order": "checked_at.desc", "limit": "300"})
    r.raise_for_status()
    out = {}
    for row in r.json():
        out.setdefault(row["route_id"][len(PREFIX):], row["note"] or "")
    return out


def mark(client: httpx.Client, reel_id: str, note: str):
    client.post(f"{os.environ['SUPABASE_URL']}/rest/v1/price_checks", headers=sb_headers(), json={
        "route_id": f"{PREFIX}{reel_id}", "price": None, "currency": "EUR",
        "previous_price": None, "changed": False, "direction": "same", "status": "ok", "note": note})


def top_drops(client: httpx.Client, days=7, n=3):
    since = (date.today() - timedelta(days=days)).isoformat()
    r = client.get(f"{os.environ['SUPABASE_URL']}/rest/v1/price_checks", headers=sb_headers(), params={
        "select": "route_id,price,previous_price,checked_at", "status": "eq.ok", "direction": "eq.down",
        "checked_at": f"gte.{since}", "order": "checked_at.desc", "limit": "300"})
    drops = []
    for row in r.json():
        p, pp = row.get("price"), row.get("previous_price")
        if p and pp and pp > 0 and not row["route_id"].startswith(PREFIX):
            pct = (pp - p) / pp * 100
            if pct >= 8:
                drops.append({"route_id": row["route_id"], "from": pp, "to": p,
                              "pct": round(pct, 1), "when": row["checked_at"][:10]})
    best, seen = [], set()
    for d in sorted(drops, key=lambda x: -x["pct"]):
        if d["route_id"] not in seen:
            seen.add(d["route_id"]); best.append(d)
        if len(best) >= n:
            break
    return best


def route_title(client: httpx.Client, route_id: str) -> str:
    try:
        html = client.get(f"{APP}/track/{route_id}", timeout=40).text
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
        return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else route_id
    except Exception:
        return route_id


def record_page(path: str, out_mp4: str):
    """Запись страницы приложения 540x960 -> mp4 (вертикальная, для sourceClip)."""
    from playwright.sync_api import sync_playwright
    rec_dir = os.path.join(PIPE, "tmp-rec")
    os.makedirs(rec_dir, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 540, "height": 960},
                                  record_video_dir=rec_dir, record_video_size={"width": 540, "height": 960})
        page = ctx.new_page()
        page.goto(f"{APP}{path}", wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(3000)
        for y in range(0, 560, 20):
            page.evaluate(f"window.scrollTo(0, {y})")
            page.wait_for_timeout(120)
        page.wait_for_timeout(1500)
        ctx.close(); browser.close()
    webm = max(glob.glob(os.path.join(rec_dir, "*.webm")), key=os.path.getmtime)
    ffmpeg = find_bin("ffmpeg")
    run([ffmpeg, "-y", "-v", "error", "-i", webm, "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "20", "-pix_fmt", "yuv420p", "-an", out_mp4])
    os.remove(webm)


def find_bin(name: str) -> str:
    hits = glob.glob(os.path.join(PIPE, "node_modules", f"{name}-static", "**", name), recursive=True) + \
           glob.glob(os.path.join(PIPE, "node_modules", f"{name}-static", "**", f"{name}.exe"), recursive=True)
    if not hits:
        return name  # системный
    return hits[0]


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd)[:200], flush=True)
    subprocess.run(cmd, check=True, **kw)


def gen_script(client: httpx.Client, job: dict, live: str, clips_note: str, feedback: str | None) -> dict:
    user = (f"Язык ролика: {job['lang']}\nБриф: {job.get('brief', job['id'])}\n"
            f"Доступные записи приложения: {clips_note}\n\nЖивые данные:\n{live}")
    if feedback:
        user += f"\n\nЭто ПЕРЕДЕЛКА. Правки от владельца (учесть обязательно): {feedback}"
    r = client.post("https://api.anthropic.com/v1/messages", timeout=120, headers={
        "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
        "content-type": "application/json"}, json={
        "model": "claude-sonnet-5", "max_tokens": 4000,
        "system": PRODUCTION_RULES,
        "messages": [{"role": "user", "content": user}]})
    r.raise_for_status()
    text = r.json()["content"][0]["text"]
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0))


def tg_send_video(client: httpx.Client, path: str, caption: str, reel_id: str):
    kb = {"inline_keyboard": [[
        {"text": "✅ Опубликовать", "callback_data": f"reel:{reel_id}:approve"},
        {"text": "❌ Переделать", "callback_data": f"reel:{reel_id}:decline"}]]}
    with open(path, "rb") as f:
        r = client.post(
            f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendVideo",
            data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "caption": caption[:1024],
                  "reply_markup": json.dumps(kb)},
            files={"video": (os.path.basename(path), f, "video/mp4")}, timeout=600)
    print("telegram:", r.json().get("ok"))


def main():
    force_id = os.environ.get("FORCE_REEL_ID", "").strip()
    queue_path = os.path.join(ROOT, "queue.json")
    queue = json.load(open(queue_path, encoding="utf-8"))
    items = {i["id"]: i for i in queue["items"]}

    with httpx.Client(timeout=90) as client:
        decisions = latest_decisions(client)

        job, feedback = None, None
        # 1) переделки: rendered + свежий decline
        for it in queue["items"]:
            note = decisions.get(it["id"], "")
            if it["status"] == "rendered" and note.startswith("declined"):
                job = it
                feedback = note[len("declined"):].lstrip(": ").strip() or "без комментария — сделай сильнее хук и динамику"
                break
        # 2) очередное производство
        if job is None:
            horizon = (date.today() + timedelta(days=2)).isoformat()
            for it in queue["items"]:
                if force_id and it["id"] == force_id:
                    job = it; break
                if not force_id and it["status"] == "queued" and it["post_date"] <= horizon:
                    job = it; break
        if job is None:
            print("nothing to produce"); return

        print(f"JOB: {job['id']} (remake={bool(feedback)})")

        # живые данные
        drops = top_drops(client)
        for d in drops:
            d["title"] = route_title(client, d["route_id"])
        live = "Топ падений цен за 7 дней (EUR):\n" + "\n".join(
            f"- {d['title']}: {d['from']} -> {d['to']} (-{d['pct']}%)" for d in drops) if drops else "нет заметных падений"

        # запись страницы
        clips_note = "tracker-app.mp4 (общий трекер), tracker-georgia.mp4, tracker-weekend.mp4"
        rec = job.get("record", "")
        if rec:
            path = f"/track/{drops[0]['route_id']}" if rec == "auto-top-drop" and drops else (rec if rec.startswith("/") else "")
            if path:
                out = os.path.join(PIPE, "input", "tracker-live.mp4")
                record_page(path, out)
                clips_note = "tracker-live.mp4 (СВЕЖАЯ запись нужного трекера — используй её)" \
                             ", tracker-app.mp4, tracker-georgia.mp4, tracker-weekend.mp4"

        # сценарий + caption
        gen = gen_script(client, job, live, clips_note, feedback)
        reel_id = job["id"]
        reel_dir = os.path.join(ROOT, reel_id)
        os.makedirs(reel_dir, exist_ok=True)
        script_path = os.path.join(PIPE, "job-script.json")
        json.dump(gen["script"], open(script_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        open(os.path.join(reel_dir, "caption.txt"), "w", encoding="utf-8").write(gen["caption"].strip() + "\n")
        json.dump(gen["script"], open(os.path.join(reel_dir, "script.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        json.dump({"post_date": job["post_date"], "slot": job.get("slot", "12:00"),
                   "lang": job["lang"], "channel": job.get("channel", "instagram")},
                  open(os.path.join(reel_dir, "meta.json"), "w", encoding="utf-8"))

        # рендер
        env = dict(os.environ); env["ANTHROPIC_API_KEY"] = ""  # сценарий уже готов
        raw = os.path.join(reel_dir, f"{reel_id}-raw.mp4")
        run(["npx", "ts-node", "src/index.ts", reel_id, "--script", script_path,
             "--music", os.path.join("assets", "_ambient_music.wav"), "--out", raw],
            cwd=PIPE, env=env)

        post = os.path.join(reel_dir, f"{reel_id}-post.mp4")
        run([find_bin("ffmpeg"), "-y", "-v", "error", "-i", raw, "-c:v", "libx264", "-preset", "medium",
             "-crf", "26", "-maxrate", "1400k", "-bufsize", "2800k", "-c:a", "aac", "-b:a", "128k",
             "-movflags", "+faststart", post])
        os.remove(raw)

        probe = subprocess.run([find_bin("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", post], capture_output=True, text=True, check=True)
        dur = float(probe.stdout.strip())
        assert 15 <= dur <= 95, f"duration {dur}s out of range"
        print(f"rendered: {post} ({dur:.1f}s)")

        # статус + git
        job["status"] = "rendered"
        json.dump(queue, open(queue_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        run(["git", "config", "user.name", "reels-bot"], cwd=ROOT)
        run(["git", "config", "user.email", "reels-bot@users.noreply.github.com"], cwd=ROOT)
        run(["git", "add", reel_id, "queue.json"], cwd=ROOT)
        run(["git", "commit", "-m", f"{reel_id}: rendered in CI ({'remake' if feedback else 'scheduled'})"], cwd=ROOT)
        run(["git", "push"], cwd=ROOT)

        if feedback:
            mark(client, reel_id, "resent")

        head = "🔁 ПЕРЕДЕЛКА" if feedback else "🎬 Новый рилс (собран автоматически в облаке)"
        msg = (f"{head} — {reel_id} ({job['lang'].upper()})\n"
               f"⏰ Слот: {job['post_date']} {job.get('slot','')} — после ✅ опубликуется сам.\n"
               f"{gen.get('tg_summary','')}\n"
               f"⚠️ Качество в Telegram сжато — оценивай контент, не резкость.\n\n"
               f"📝 Подпись поста:\n————\n{gen['caption'].strip()}")
        tg_send_video(client, post, msg, reel_id)


if __name__ == "__main__":
    main()
