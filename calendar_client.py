"""Thin wrapper around the Google Calendar API v3, one account at a time.

Mirrors gmail_client: every function takes account_name first and resolves its
own credentials via accounts.load_credentials. See specs/014.
"""

from googleapiclient.discovery import build

import accounts


def _service(account_name: str):
    creds = accounts.load_credentials(account_name)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _when(slot: dict) -> str:
    return slot.get("dateTime") or slot.get("date") or ""


def _event_summary(e: dict) -> dict:
    return {
        "id": e.get("id"),
        "summary": e.get("summary", "(no title)"),
        "start": _when(e.get("start", {})),
        "end": _when(e.get("end", {})),
        "all_day": "date" in e.get("start", {}),
        "location": e.get("location", ""),
        "status": e.get("status", ""),
        "organizer": e.get("organizer", {}).get("email", ""),
        "attendees": [a.get("email", "") for a in e.get("attendees", [])],
        "html_link": e.get("htmlLink", ""),
    }


def _time_field(value: str, time_zone: str | None) -> dict:
    """A bare YYYY-MM-DD is an all-day event; anything else is a timed one."""
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        return {"date": value}
    field = {"dateTime": value}
    if time_zone:
        field["timeZone"] = time_zone
    return field


# --- reads (calendar.readonly) -------------------------------------------


def list_calendars(account_name: str) -> list[dict]:
    service = _service(account_name)
    resp = service.calendarList().list().execute()
    return [
        {
            "id": c["id"],
            "summary": c.get("summary", ""),
            "primary": c.get("primary", False),
            "access_role": c.get("accessRole", ""),
        }
        for c in resp.get("items", [])
    ]


def list_events(account_name, calendar_id="primary", time_min=None, time_max=None,
                query=None, max_results=25) -> list[dict]:
    service = _service(account_name)
    resp = (
        service.events()
        .list(
            calendarId=calendar_id, timeMin=time_min or None, timeMax=time_max or None,
            q=query or None, maxResults=max_results, singleEvents=True, orderBy="startTime",
        )
        .execute()
    )
    return [_event_summary(e) for e in resp.get("items", [])]


def get_event(account_name, calendar_id, event_id) -> dict:
    service = _service(account_name)
    e = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    detail = _event_summary(e)
    detail["description"] = e.get("description", "")
    detail["attendees_detail"] = [
        {"email": a.get("email", ""), "response": a.get("responseStatus", ""), "self": a.get("self", False)}
        for a in e.get("attendees", [])
    ]
    return detail


def freebusy(account_name, time_min, time_max, calendar_ids=None) -> dict:
    service = _service(account_name)
    ids = calendar_ids or ["primary"]
    body = {"timeMin": time_min, "timeMax": time_max, "items": [{"id": i} for i in ids]}
    resp = service.freebusy().query(body=body).execute()
    return {cid: cal.get("busy", []) for cid, cal in resp.get("calendars", {}).items()}


def list_events_all(time_min, time_max, max_results_per_account=10) -> dict:
    """One time window across every configured account, grouped by account. An
    account whose auth expired is recorded as {"error": ...} (mirrors
    gmail_client.search_all) rather than sinking the whole sweep."""
    out: dict = {}
    for name in accounts.list_account_names():
        try:
            out[name] = list_events(name, time_min=time_min, time_max=time_max,
                                    max_results=max_results_per_account)
        except accounts.AccountError as e:
            out[name] = {"error": str(e)}
    return out


# --- writes (calendar.events) --------------------------------------------


def create_event(account_name, summary, start, end, calendar_id="primary", description=None,
                 location=None, attendees=None, time_zone=None, send_updates="none") -> dict:
    service = _service(account_name)
    body = {"summary": summary, "start": _time_field(start, time_zone), "end": _time_field(end, time_zone)}
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": a.strip()} for a in attendees if a.strip()]
    e = service.events().insert(calendarId=calendar_id, body=body, sendUpdates=send_updates).execute()
    return _event_summary(e)


def update_event(account_name, event_id, calendar_id="primary", summary=None, start=None, end=None,
                 description=None, location=None, attendees=None, time_zone=None, send_updates="none") -> dict:
    service = _service(account_name)
    patch: dict = {}
    if summary is not None:
        patch["summary"] = summary
    if description is not None:
        patch["description"] = description
    if location is not None:
        patch["location"] = location
    if start is not None:
        patch["start"] = _time_field(start, time_zone)
    if end is not None:
        patch["end"] = _time_field(end, time_zone)
    if attendees is not None:
        patch["attendees"] = [{"email": a.strip()} for a in attendees if a.strip()]
    e = service.events().patch(calendarId=calendar_id, eventId=event_id, body=patch,
                               sendUpdates=send_updates).execute()
    return _event_summary(e)


def delete_event(account_name, event_id, calendar_id="primary", send_updates="none") -> dict:
    service = _service(account_name)
    service.events().delete(calendarId=calendar_id, eventId=event_id, sendUpdates=send_updates).execute()
    return {"deleted": event_id}


def quick_add(account_name, text, calendar_id="primary") -> dict:
    service = _service(account_name)
    e = service.events().quickAdd(calendarId=calendar_id, text=text).execute()
    return _event_summary(e)


def respond_to_event(account_name, event_id, response, calendar_id="primary", send_updates="none") -> dict:
    """RSVP: patch the *self* attendee's responseStatus (Calendar has no dedicated
    RSVP endpoint). response is accepted / declined / tentative."""
    service = _service(account_name)
    e = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    attendees = e.get("attendees", [])
    changed = False
    for a in attendees:
        if a.get("self"):
            a["responseStatus"] = response
            changed = True
    if not changed:
        return {"error": "you are not listed as an attendee on this event"}
    patched = service.events().patch(calendarId=calendar_id, eventId=event_id,
                                     body={"attendees": attendees}, sendUpdates=send_updates).execute()
    return _event_summary(patched)
