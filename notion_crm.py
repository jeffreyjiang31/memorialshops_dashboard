"""
Fetch CRM contacts from a Notion database.
Requires NOTION_TOKEN and NOTION_CRM_DB environment variables.
"""
import os
import requests


NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def get_config():
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_CRM_DB")
    if not token or not db_id:
        raise ValueError("Missing NOTION_TOKEN or NOTION_CRM_DB environment variables")
    return token, db_id


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _extract_text(prop):
    """Extract plain text from a Notion rich_text or title property."""
    items = prop.get("rich_text") or prop.get("title") or []
    return "".join(t.get("plain_text", "") for t in items)


def _extract_select(prop):
    """Extract select value."""
    sel = prop.get("select")
    return sel.get("name", "") if sel else ""


def _extract_multi_select(prop):
    """Extract multi_select values as list of strings."""
    items = prop.get("multi_select") or []
    return [item.get("name", "") for item in items]


def fetch_crm_contacts(config=None):
    """Query the Notion CRM database and return structured contact list."""
    if config:
        token, db_id = config
    else:
        token, db_id = get_config()

    url = f"{NOTION_API}/databases/{db_id}/query"
    headers = _headers(token)
    all_results = []
    has_more = True
    start_cursor = None

    while has_more:
        body = {}
        if start_cursor:
            body["start_cursor"] = start_cursor
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        all_results.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    contacts = []
    status_counts = {}

    for page in all_results:
        props = page.get("properties", {})
        name = _extract_text(props.get("Name", {}))
        statuses = _extract_multi_select(props.get("Status", {}))
        notes = _extract_text(props.get("Notes", {}))

        for s in statuses:
            status_counts[s] = status_counts.get(s, 0) + 1

        contacts.append({
            "name": name,
            "statuses": statuses,
            "notes": notes,
            "notion_id": page.get("id", ""),
        })

    contacts.sort(key=lambda c: (c["name"] or "").lower())

    return {
        "contacts": contacts,
        "total": len(contacts),
        "by_status": status_counts,
    }
