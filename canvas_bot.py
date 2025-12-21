import requests, time, json, os, re, html
from datetime import datetime
from flask import Flask
import threading

# ------------------ CONFIG ------------------
SEEN_FILE = "seen_announcements.json"
INITIAL_RUN_SEND = True                # False = mark existing announcements as seen on first run (recommended)
CANVAS_DOMAIN = "feu.instructure.com"   # your Canvas domain
API_TOKEN = os.getenv("CANVAS_TOKEN")   # loaded from Render env vars
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK")
COURSE_IDS = [106566, 106734, 107500, 107253, 107072, 107047, 108084, 107246, 107342, 108172]
COURSE_ROLES = ["CCS0005", "CCS0005", "CCS0007", "CCS0007", "GED0085", "GED0001", "GED", "GED", "IT0003", "NSTP1"]
POLL_INTERVAL = 60  # seconds
# --------------------------------------------

app = Flask('')

@app.route('/')
def home():
    return "Canvas bot running!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = threading.Thread(target=run)
    t.start()

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

def extract_named_links(html_text):
    """
    Extract <a href="url">text</a> links and return them as
    Markdown links usable by Discord.
    """
    if not html_text:
        return []

    links = []
    for match in re.findall(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html_text, re.IGNORECASE | re.DOTALL):
        url, text = match
        text = strip_html(text)
        url = html.unescape(url)
        if text and url:
            links.append(f"[{text}]({url})")
    return links

def strip_html(html_text):
    if not html_text:
        return ""
    # quick-and-dirty html -> plaintext
    text = re.sub(r'<[^>]+>', '', html_text)
    return html.unescape(text).strip()

def course_roles(cid):
    # save course roles
    role = COURSE_ROLES[COURSE_IDS.index(cid)]
    return role

def send_to_discord(cid, title, body, course_name=None, url=None, author=None, attachments=None, posted_at=None):
    discord_role = course_roles(cid)
    embed = {
        "title": title or "(no title)",
        "description": (body[:1800] + "...") if len(body) > 1900 else body,  # limit for safety
        "url": url,
        "color": 0x3498db,
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
        "content": "@" + discord_role + " 🚨 New Canvas Announcement!",
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
            raw_html = t.get("message", "")
            body = strip_html(raw_html)

            named_links = extract_named_links(raw_html)
            if named_links:
                body += "\n\n🔗 **Links:**\n" + "\n".join(named_links)

            url = t.get("html_url") or f"https://{CANVAS_DOMAIN}/courses/{cid}/discussion_topics/{t.get('id')}"
            try:
                send_to_discord(cid, title, body, course_map.get(cid), url)
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
                    raw_html = t.get("message", "")
                    body = strip_html(raw_html)

                    named_links = extract_named_links(raw_html)
                    if named_links:
                        body += "\n\n🔗 **Links:**\n" + "\n".join(named_links)

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
    keep_alive()

    main()
