import json
import logging
from datetime import datetime, timedelta

import msal
import httpx

import config
from models import get_db, upsert_event

log = logging.getLogger("chronicle.outlook")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def get_msal_app() -> msal.ConfidentialClientApplication | None:
    if not config.AZURE_CLIENT_ID:
        return None
    return msal.ConfidentialClientApplication(
        config.AZURE_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{config.AZURE_TENANT_ID}",
        client_credential=config.AZURE_CLIENT_SECRET,
    )


def get_auth_url() -> str | None:
    app = get_msal_app()
    if not app:
        return None
    return app.get_authorization_request_url(
        scopes=["Calendars.ReadWrite"],
        redirect_uri=config.AZURE_REDIRECT_URI,
    )


def exchange_code(code: str) -> dict | None:
    app = get_msal_app()
    if not app:
        return None
    result = app.acquire_token_by_authorization_code(
        code,
        scopes=["Calendars.ReadWrite"],
        redirect_uri=config.AZURE_REDIRECT_URI,
    )
    if "access_token" in result:
        save_token(result)
        return result
    log.error(f"Outlook token exchange failed: {result.get('error_description', result)}")
    return None


def save_token(token_data: dict):
    from pathlib import Path
    Path(config.OUTLOOK_TOKEN_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(config.OUTLOOK_TOKEN_FILE, "w") as f:
        json.dump(token_data, f)


def load_token() -> dict | None:
    try:
        with open(config.OUTLOOK_TOKEN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def get_access_token() -> str | None:
    token = load_token()
    if not token:
        return None

    app = get_msal_app()
    if not app:
        return None

    # Try to use refresh token
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(["Calendars.ReadWrite"], account=accounts[0])
        if result and "access_token" in result:
            save_token(result)
            return result["access_token"]

    # Try refresh token directly
    if "refresh_token" in token:
        result = app.acquire_token_by_refresh_token(
            token["refresh_token"], scopes=["Calendars.ReadWrite"]
        )
        if result and "access_token" in result:
            save_token(result)
            return result["access_token"]

    return token.get("access_token")


def parse_event(event: dict) -> dict:
    start = event.get("start", {})
    end = event.get("end", {})
    all_day = event.get("isAllDay", False)

    return {
        "id": f"outlook_{event['id'][:64]}",
        "source": "outlook",
        "source_id": event["id"],
        "calendar_id": "default",
        "title": event.get("subject", "(No title)"),
        "description": event.get("bodyPreview", ""),
        "location": event.get("location", {}).get("displayName", ""),
        "start_time": start.get("dateTime", ""),
        "end_time": end.get("dateTime", ""),
        "all_day": 1 if all_day else 0,
        "status": "cancelled" if event.get("isCancelled") else "confirmed",
        "raw_json": json.dumps(event),
    }


def sync_calendar() -> int:
    access_token = get_access_token()
    if not access_token:
        log.warning("Outlook not authenticated")
        return 0

    conn = get_db()
    count = 0
    now = datetime.utcnow()

    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "$select": "id,subject,bodyPreview,start,end,location,isAllDay,isCancelled",
            "$orderby": "start/dateTime",
            "$top": 250,
            "$filter": f"start/dateTime ge '{(now - timedelta(days=30)).isoformat()}Z' and start/dateTime le '{(now + timedelta(days=90)).isoformat()}Z'",
        }

        url = f"{GRAPH_BASE}/me/calendar/events"
        while url:
            resp = httpx.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()

            for event in data.get("value", []):
                parsed = parse_event(event)
                upsert_event(conn, parsed)
                count += 1

            url = data.get("@odata.nextLink")
            params = {}  # nextLink includes params

        conn.execute("""
            INSERT INTO sync_state (source, calendar_id, last_sync)
            VALUES ('outlook', 'default', datetime('now'))
            ON CONFLICT(source, calendar_id) DO UPDATE SET last_sync=datetime('now')
        """)
        conn.commit()

    except Exception as e:
        log.error(f"Outlook sync error: {e}")
        conn.rollback()
    finally:
        conn.close()

    log.info(f"Outlook sync complete: {count} events processed")
    return count


def create_event(subject: str, start: str, end: str, description: str = "",
                 location: str = "") -> dict | None:
    access_token = get_access_token()
    if not access_token:
        return None

    body = {
        "subject": subject,
        "start": {"dateTime": start, "timeZone": "America/New_York"},
        "end": {"dateTime": end, "timeZone": "America/New_York"},
    }
    if description:
        body["body"] = {"contentType": "Text", "content": description}
    if location:
        body["location"] = {"displayName": location}

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    resp = httpx.post(f"{GRAPH_BASE}/me/calendar/events", headers=headers, json=body)

    if resp.status_code == 201:
        event = resp.json()
        conn = get_db()
        upsert_event(conn, parse_event(event))
        conn.commit()
        conn.close()
        log.info(f"Created Outlook event: {event['id'][:32]} - {subject}")
        return event

    log.error(f"Failed to create Outlook event: {resp.status_code} {resp.text}")
    return None


def setup_webhook() -> dict | None:
    """Register a Graph API subscription for calendar changes."""
    access_token = get_access_token()
    if not access_token:
        return None

    expiry = (datetime.utcnow() + timedelta(days=2)).isoformat() + "Z"
    body = {
        "changeType": "created,updated,deleted",
        "notificationUrl": f"{config.WEBHOOK_BASE_URL}/webhook/outlook",
        "resource": "me/events",
        "expirationDateTime": expiry,
        "clientState": "chronicle-outlook-webhook",
    }

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    resp = httpx.post(f"{GRAPH_BASE}/subscriptions", headers=headers, json=body)

    if resp.status_code == 201:
        sub = resp.json()
        log.info(f"Outlook webhook registered: {sub['id']}, expires {expiry}")
        return sub
    else:
        log.error(f"Outlook webhook failed: {resp.status_code} {resp.text}")
        return None
