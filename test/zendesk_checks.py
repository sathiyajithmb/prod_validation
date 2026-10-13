"""Read all tickets from the configured Zendesk view without changing tickets."""
# Allow direct execution from test/ while keeping configuration at project root.
import sys as _sys
from pathlib import Path as _Path
for _directory in (_Path(__file__).resolve().parent, _Path(__file__).resolve().parent.parent):
    if str(_directory) not in _sys.path:
        _sys.path.insert(0, str(_directory))

import argparse
from datetime import datetime
from pathlib import Path
import re
import time
from urllib.parse import urlsplit, urljoin
import utill
from playwright.sync_api import sync_playwright
from excel_report import record_result, save_excel_report

SECTION = "Zendesk Unassigned Tickets"
ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".auth" / "zendesk.json"
COLUMNS = ["Ticket ID", "Status", "Subject", "Updated", "Requester", "Priority", "Created", "Assignee ID", "Ticket URL"]


class ZendeskCheckError(RuntimeError):
    pass


def view_config():
    parsed = urlsplit(utill.ZENDESK_URL)
    match = re.fullmatch(r"/agent/filters/(\d+)/?", parsed.path)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".zendesk.com") or not match:
        raise ZendeskCheckError("ZENDESK_URL must be the HTTPS Zendesk view URL.")
    return f"https://{parsed.netloc}", match[1]


def login(page, origin, timeout=120):
    page.goto(utill.ZENDESK_URL, wait_until="domcontentloaded")
    deadline = time.monotonic() + timeout
    entered_email = entered_password = clicked_google = False
    notified = False
    while time.monotonic() < deadline:
        pages = [p for p in page.context.pages if not p.is_closed()]
        for current in pages:
            url = urlsplit(current.url)
            if current.url.startswith(origin + "/agent/"):
                heading = current.get_by_role("heading", name="Unassigned tickets", exact=True)
                if heading.is_visible():
                    return current
            if url.hostname == "accounts.google.com":
                email = current.get_by_role("textbox", name="Email or phone", exact=True)
                password = current.locator('input[type="password"]:visible')
                if not entered_email and email.is_visible():
                    email.fill(utill.ZENDESK_USERNAME)
                    current.get_by_role("button", name="Next", exact=True).click()
                    entered_email = True
                elif not entered_password and password.count() == 1:
                    password.fill(utill.ZENDESK_PASSWORD)
                    current.get_by_role("button", name="Next", exact=True).click()
                    entered_password = True
                elif not entered_email:
                    account = current.get_by_text(utill.ZENDESK_USERNAME, exact=True)
                    if account.count() == 1 and account.is_visible():
                        account.click()
                        entered_email = True
            elif url.hostname and url.hostname.endswith(".zendesk.com") and not clicked_google:
                google = current.locator('a,button').filter(has_text=re.compile(r"^(?:Sign in|Continue|Log in) with Google$", re.I)).filter(visible=True)
                if google.count() == 1:
                    google.click()
                    clicked_google = True
        if not notified and time.monotonic() > deadline - timeout + 20:
            print("Zendesk: waiting for login. If Google requests verification, complete it in the browser.", flush=True)
            notified = True
        page.wait_for_timeout(500)
    raise ZendeskCheckError("Zendesk login did not complete. Complete Google verification in the browser and retry; no ticket count was reported.")


def get_json(request, url, origin):
    target = urlsplit(url)
    base = urlsplit(origin)
    if (target.scheme, target.netloc) != (base.scheme, base.netloc) or not target.path.startswith('/api/v2/'):
        raise ZendeskCheckError("Unexpected pagination URL; collection stopped.")
    response = request.get(url, timeout=60_000, max_redirects=0)
    try:
        if not response.ok:
            raise ZendeskCheckError(f"Zendesk read request failed (HTTP {response.status}); no complete count reported.")
        try:
            payload = response.json()
        except Exception:
            raise ZendeskCheckError("Zendesk returned a non-JSON response; check the login session.") from None
        if not isinstance(payload, dict):
            raise ZendeskCheckError("Unexpected Zendesk response format.")
        return payload
    finally:
        response.dispose()


def collect_tickets(request, origin, view_id):
    url = f"{origin}/api/v2/views/{view_id}/tickets.json?per_page=100"
    visited, seen = set(), set()
    tickets = []
    while url:
        url = urljoin(origin, url)
        if url in visited:
            raise ZendeskCheckError("Zendesk pagination repeated a page; complete count unavailable.")
        visited.add(url)
        data = get_json(request, url, origin)
        batch = data.get('tickets')
        if not isinstance(batch, list):
            raise ZendeskCheckError("Zendesk response has no ticket list.")
        for ticket in batch:
            number = ticket.get('id')
            if type(number) is not int or number in seen:
                raise ZendeskCheckError("Ticket list changed or has duplicate/missing IDs; rerun to collect a complete list.")
            seen.add(number)
            tickets.append(ticket)
        if 'next_page' in data:
            url = data['next_page']
        elif isinstance(data.get('meta'), dict) and 'has_more' in data['meta']:
            url = data.get('links', {}).get('next') if data['meta']['has_more'] else None
            if data['meta']['has_more'] and not url:
                raise ZendeskCheckError("Zendesk next page is missing; collection is incomplete.")
        else:
            raise ZendeskCheckError("Zendesk pagination metadata missing; complete collection cannot be confirmed.")
        if url is not None and not isinstance(url, str):
            raise ZendeskCheckError("Unexpected next-page value.")
    names = {}
    requester_ids = sorted({t['requester_id'] for t in tickets if type(t.get('requester_id')) is int})
    for offset in range(0, len(requester_ids), 100):
        ids = ','.join(str(n) for n in requester_ids[offset:offset + 100])
        users = get_json(request, f"{origin}/api/v2/users/show_many.json?ids={ids}", origin)
        if not isinstance(users.get('users'), list):
            raise ZendeskCheckError("Could not read ticket requester names.")
        names.update({u['id']:u.get('name', '') for u in users['users']})
    rows = [{"Ticket ID":t['id'], "Status":t.get('status'), "Subject":t.get('subject'),
             "Updated":t.get('updated_at'), "Requester":names.get(t.get('requester_id'), t.get('requester_id')),
             "Priority":t.get('priority'), "Created":t.get('created_at'), "Assignee ID":t.get('assignee_id'),
             "Ticket URL":f"{origin}/agent/tickets/{t['id']}"} for t in tickets]
    return rows


def report_zendesk(playwright, results, headless=False, debug=False):
    browser = None
    try:
        origin, view_id = view_config()
        browser = playwright.chromium.launch(headless=headless)
        kwargs = {"viewport":{"width":1600,"height":1000}}
        if STATE.is_file():
            kwargs['storage_state'] = str(STATE)
        context = browser.new_context(**kwargs)
        page = context.new_page()
        page.set_default_timeout(15_000)
        page = login(page, origin)
        STATE.parent.mkdir(exist_ok=True)
        context.storage_state(path=str(STATE))
        if debug:
            page.pause()
        rows = collect_tickets(context.request, origin, view_id)
        details = "All pages collected from the configured Unassigned tickets view; tickets were not modified. Dates are UTC."
        folder = ROOT / 'screenshots'
        folder.mkdir(exist_ok=True)
        path = folder / f"zendesk_unassigned_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
        try:
            page.screenshot(path=str(path), full_page=True, animations='disabled')
            details += f" Screenshot: {path}"
        except Exception:
            details += " Screenshot unavailable."
        record_result(results, SECTION, "Unassigned tickets", count=len(rows), result="COUNT", details=details)
        results.append({"_kind":"db_table", "Section":SECTION, "columns":COLUMNS, "rows":rows})
        print(f"Zendesk: collected all {len(rows)} unassigned-view tickets.", flush=True)
        return True
    except Exception as exc:
        details = str(exc) if isinstance(exc, ZendeskCheckError) else f"Zendesk check did not complete ({type(exc).__name__}); no complete count reported."
        record_result(results, SECTION, "Unassigned tickets", result="FAIL", details=details)
        print('Zendesk: FAIL - ' + details, flush=True)
        return False
    finally:
        if browser is not None:
            browser.close()


def run(headless=False, debug=False):
    results = []
    try:
        with sync_playwright() as playwright:
            passed = report_zendesk(playwright, results, headless, debug)
    finally:
        save_excel_report(results)
    return passed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()
    raise SystemExit(0 if run(args.headless and not args.debug, args.debug) else 1)
