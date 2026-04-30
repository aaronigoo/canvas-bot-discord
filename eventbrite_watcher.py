import requests
import os

EVENTBRITE_TOKEN = os.getenv("EVENTBRITE_TOKEN")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL2")

ORG_ID = "110021953071"

API_URL = f"https://www.eventbriteapi.com/v3/organizations/{ORG_ID}/events/"


def fetch_org_events():
    headers = {
        "Authorization": f"Bearer {EVENTBRITE_TOKEN}"
    }

    params = {
        "expand": "venue",
        "order_by": "start_asc"
    }

    res = requests.get(API_URL, headers=headers, params=params)

    if res.status_code != 200:
        print("Eventbrite error:", res.text)
        return []

    return res.json().get("events", [])


def format_event(event):
    name = event.get("name", {}).get("text", "No Title")
    url = event.get("url", "")
    start = event.get("start", {}).get("local", "Unknown time")

    venue = event.get("venue", {}) if event.get("venue") else {}
    location = venue.get("address", {}).get("localized_address_display", "Online / TBA")

    return {
        "content": f"📣 **{name}**\n🕒 {start}\n📍 {location}\n🔗 {url}"
    }


def send_to_discord(payload):
    if not DISCORD_WEBHOOK_URL:
        print("Missing webhook URL")
        return

    requests.post(DISCORD_WEBHOOK_URL, json=payload)


def run_event_watch(limit=5):
    events = fetch_org_events()

    if not events:
        print("No events found.")
        return

    for event in events[:limit]:
        payload = format_event(event)
        send_to_discord(payload)


# ===== allows both import AND direct run =====
if __name__ == "__main__":
    run_event_watch()
