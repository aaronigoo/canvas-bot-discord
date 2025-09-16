import requests, time, json, os, re, html
from datetime import datetime

# ------------------ CONFIG ------------------
SEEN_FILE = "seen_announcements.json"
INITIAL_RUN_SEND = False       # False = mark existing announcements as seen on first run (recommended)
CANVAS_DOMAIN = "feu.instructure.com"   # your Canvas domain
API_TOKEN = os.getenv("CANVAS_TOKEN")   # loaded from Render env vars
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
COURSE_IDS = [104308, 104276, 104233, 104169, 104108, 105756, 103949, 105147]
POLL_INTERVAL = 60  # seconds
# --------------------------------------------

HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}

def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2)

def load_seen():
    if os.path.exists(SEEN_FILE):
        return json.load(open(SEEN_FILE, encoding="utf-8"))
    return {}

def canvas_get(path, params=None):
    url = f"https://{CANVAS_DOMAIN}{path}"
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_user_courses():
    # returns list of courses (id,name)
    return canvas_get("/api/v1/courses", params={"enrollment_state":"active","per_page":100})

def fetch_announcements_for_course(course_id):
    path = f"/api/v1/courses/{course_id}/discussion_topics"
    params = {"only_announcements": True, "per_page": 100}
    return canvas_get(path, params=params)

def strip_html(html_text):
    if not html_text:
        return ""
    # quick-and-dirty html -> plaintext
    text = re.sub(r'<[^>]+>', '', html_text)
    return html.unescape(text).strip()

def send_to_discord(title, body, course_name=None, url=None, author=None, attachments=None, posted_at=None):
    embed = {
        "title": title or "(no title)",
        "description": (body[:1800] + "...") if len(body) > 1900 else body,  # limit for safety
        "url": url,
        "color": 0x3498db,  # nice blue
        "footer": {"text": f"{course_name}" if course_name else "Canvas Announcement"},
    }
    if posted_at:
        embed["timestamp"] = posted_at  # ISO8601 UTC time
    if author:
        embed["author"] = {"name": author}

    # Add attachments (as extra fields)
    if attachments:
        fields = []
        for a in attachments:
            name = a.get("display_name", "file")
            link = a.get("url")
            if link:
                fields.append({"name": "Attachment", "value": f"[{name}]({link})", "inline": False})
        if fields:
            embed["fields"] = fields

    payload = {
        "content": "@everyone 🚨 New Canvas Announcement!",
        "embeds": [embed]
    }

    r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
    r.raise_for_status()
    return r



def main():
    seen = load_seen()   # dict of {announcement_id: posted_at}
    # get course list
    if not COURSE_IDS:
        print("Fetching your active courses...")
        courses = fetch_user_courses()
        course_ids = [c['id'] for c in courses]
        course_map = {c['id']: c.get('name') for c in courses}
    else:
        course_ids = COURSE_IDS
        course_map = {}
        for cid in course_ids:
            try:
                info = canvas_get(f"/api/v1/courses/{cid}")
                course_map[cid] = info.get("name")
            except Exception:
                course_map[cid] = f"course_{cid}"

    # initial pass: collect existing announcements
    initial_found = []
    for cid in course_ids:
        try:
            topics = fetch_announcements_for_course(cid)
            for t in topics:
                # ensure announcement flag if present
                tid = str(t.get("id"))
                posted = t.get("posted_at") or t.get("created_at") or ""
                initial_found.append((cid, t, tid, posted))
        except Exception as e:
            print(f"Error fetching course {cid}: {e}")

    # sort ascending by posted time to maintain order
    initial_found.sort(key=lambda x: x[3] or "")

    if not INITIAL_RUN_SEND:
        # mark existing as seen without sending
        for cid, t, tid, posted in initial_found:
            seen[tid] = posted
        save_seen(seen)
        print(f"Marked {len(initial_found)} existing announcements as seen (INITIAL_RUN_SEND=False).")
    else:
        # send all existing announcements
        for cid, t, tid, posted in initial_found:
            if tid in seen:
                continue
            title = t.get("title", "(no title)")
            body = strip_html(t.get("message", ""))
            url = t.get("html_url") or f"https://{CANVAS_DOMAIN}/courses/{cid}/discussion_topics/{t.get('id')}"
            try:
                send_to_discord(title, body, course_map.get(cid), url)
                print("Sent:", title)
                seen[tid] = posted
                time.sleep(1)
                save_seen(seen)
            except Exception as e:
                print("Failed to send:", e)

    print("Entering poll loop. Poll interval:", POLL_INTERVAL, "seconds.")
    while True:
        try:
            for cid in course_ids:
                topics = fetch_announcements_for_course(cid)
                # ensure oldest-first
                topics.sort(key=lambda t: t.get("posted_at") or "")
                for t in topics:
                    tid = str(t.get("id"))
                    if tid in seen:
                        continue
                    title = t.get("title", "(no title)")
                    body = strip_html(t.get("message", ""))
                    url = t.get("html_url") or f"https://{CANVAS_DOMAIN}/courses/{cid}/discussion_topics/{t.get('id')}"
                    try:
                        send_to_discord(title, body, course_map.get(cid), url)
                        print(f"[{datetime.utcnow().isoformat()}] Posted announcement {tid}: {title}")
                        seen[tid] = t.get("posted_at") or ""
                        save_seen(seen)
                        time.sleep(1)  # tiny delay between posts
                    except Exception as e:
                        print("Error sending to Discord:", e)
        except Exception as e:
            print("Polling error:", e)
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
