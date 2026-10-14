# Allow direct execution from test/ while keeping configuration at project root.
import sys as _sys
from pathlib import Path as _Path
for _directory in (_Path(__file__).resolve().parent, _Path(__file__).resolve().parent.parent):
    if str(_directory) not in _sys.path:
        _sys.path.insert(0, str(_directory))

import argparse
import os

# Resolve the CLI profile before importing configuration or dependent checks.
# Only the profile name is inherited by child processes, never the password.
if __name__ == "__main__":
    _user_parser = argparse.ArgumentParser(add_help=False)
    _user_parser.add_argument("--user", choices=("user1", "user2"))
    _user_args, _ = _user_parser.parse_known_args()
    if _user_args.user is not None:
        os.environ["DOCPANEL_ACTIVE_USER"] = _user_args.user
        os.environ["ZENDESK_ACTIVE_USER"] = _user_args.user

import json
import re
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import (
    Error as PlaywrightError, Page, Playwright, Request, Response, expect, sync_playwright,
)

from utill import BASE_URL, DOCPANEL_EMAIL, DOCPANEL_PASSWORD
from excel_report import record_result, record_job_sample, save_excel_report
from legacy_metrics import build_metrics, read_metric, expected_pattern


BASE_URL = BASE_URL.rstrip("/")

TOTAL_RECORDS = re.compile(r"Total\s*Records\s*:?\s*([\d,]+)", re.IGNORECASE)
def wait_for_results(page: Page) -> None:
    # Wait for the dashboard requests after navigation/filter changes.
    # Fail on timeout rather than reporting a potentially stale count.
    page.wait_for_load_state("networkidle", timeout=60_000)


def is_dashboard_response(response: Response) -> bool:
    return (
        "/api/v1/ost/admin_dashboard/" in response.url
        and response.request.method != "OPTIONS"
    )


def finish_dashboard_update(page: Page, response: Response) -> None:
    if not response.ok:
        raise RuntimeError(f"Dashboard request failed: HTTP {response.status}")
    # Reading the body waits for completion without leaving a finished-task
    # callback behind in this installed Playwright version.
    response.body()
    wait_for_results(page)


def read_total_records(page: Page, expected_count: int | None = None) -> int:
    counter = page.locator(".total-records:visible").filter(
        has_text=re.compile(r"Total\s*Records", re.IGNORECASE)
    )
    if counter.count() == 0:
        counter = page.get_by_text(TOTAL_RECORDS).filter(visible=True)
    expect(counter).to_have_count(1, timeout=60_000)
    expect(counter).to_contain_text(TOTAL_RECORDS, timeout=60_000)
    if expected_count is not None:
        expected_text = re.compile(
            rf"Total\s*Records\s*:?\s*(?:{expected_count}|{expected_count:,})(?![\d,])",
            re.IGNORECASE,
        )
        expect(counter).to_contain_text(expected_text, timeout=60_000)
    match = TOTAL_RECORDS.search(counter.inner_text())
    if match is None:
        raise RuntimeError("Could not read the dashboard Total Records count.")
    return int(match.group(1).replace(",", ""))


def login(page: Page) -> None:
    page.goto(f"{BASE_URL}/login/", wait_until="domcontentloaded")
    page.get_by_role("textbox", name="name@email.com").fill(DOCPANEL_EMAIL)
    page.get_by_role("button", name="Continue", exact=True).click()
    page.get_by_text("OS Supervisor", exact=True).click()
    page.get_by_role("textbox", name="Password", exact=True).fill(DOCPANEL_PASSWORD)
    page.get_by_role("button", name="Log In", exact=True).click()
    page.wait_for_url(re.compile(r"^" + re.escape(BASE_URL + "/admin/")), timeout=60_000)
    wait_for_results(page)


def select_study_status(page: Page, study_status: str) -> None:
    status = page.locator("#studyStatus")
    if not status.is_visible():
        toggle = page.locator(".filterUpArrow:visible")
        if not toggle.count():
            toggle = page.locator(".filterIcon:visible")
        expect(toggle).to_have_count(1)
        toggle.click()
    expect(status).to_be_visible()
    status.click()

    menu = page.locator(".dropdown-menu.dropdowncheckul:visible")
    expect(menu).to_have_count(1, timeout=30_000)
    expect(menu).to_be_visible(timeout=30_000)
    option = menu.get_by_text(re.compile(r"^\s*" + re.escape(study_status) + r"\s*$", re.I))
    try:
        expect(option).to_have_count(1, timeout=15_000)
        expect(option).to_be_visible()
    except AssertionError as exc:
        options = menu.inner_text().strip()
        raise RuntimeError(
            f"Could not identify one {study_status!r} option in Study Status. "
            f"Available options: {options!r}"
        ) from exc

    with page.expect_response(is_dashboard_response, timeout=60_000) as updated:
        option.click()
        page.locator("body").first.press("Enter")
    finish_dashboard_update(page, updated.value)


LOADING_CASE_STATUSES = ("Not Accepted", "In Progress", "Ready to Assign", "Needs Attention")


def select_loading_case_statuses(page: Page) -> None:
    status = page.locator("#CaseStatus")
    if not status.is_visible():
        toggle = page.locator(".filterUpArrow:visible")
        if not toggle.count():
            toggle = page.locator(".filterIcon:visible")
        expect(toggle).to_have_count(1)
        toggle.click()
    status.click()
    menu = page.locator(".dropdown-menu.show:visible")
    expect(menu).to_have_count(1)
    inputs = menu.locator('input[type="checkbox"]')
    expect(inputs.first).to_be_attached()
    # Read native checkbox state, including hidden inputs styled by their labels.
    names = inputs.evaluate_all(r"""inputs => inputs.map(input => {
        let row = input.parentElement;
        while (row && row.querySelectorAll('input[type="checkbox"]').length === 1) {
            const name = row.textContent.replace(/\s+/g, ' ').trim();
            if (name) return name;
            row = row.parentElement;
        }
        return '';
    })""")
    if any(not name for name in names) or len(set(names)) != len(names):
        raise RuntimeError("Cannot identify Case Status checkbox labels; Loading count not reported.")
    if not set(LOADING_CASE_STATUSES).issubset(names):
        raise RuntimeError("Required Loading Case Status options are missing.")
    # Clear unwanted options (including All Incomplete) before selecting the four.
    for wanted in (False, True):
        for index, name in enumerate(names):
            if (name in LOADING_CASE_STATUSES) != wanted:
                continue
            checkbox = inputs.nth(index)
            if checkbox.is_checked() != wanted:
                menu.get_by_text(name, exact=True).click()
            expect(checkbox).to_be_checked(checked=wanted)
    for index, name in enumerate(names):
        expect(inputs.nth(index)).to_be_checked(checked=name in LOADING_CASE_STATUSES)
    with page.expect_response(is_dashboard_response, timeout=60_000) as updated:
        page.locator("body").first.press("Enter")
    finish_dashboard_update(page, updated.value)


def select_loading(page: Page) -> None:
    select_loading_case_statuses(page)
    select_study_status(page, "Loading")


def is_jobs_response(
    response: Response, filters: dict[str, str] | None = None
) -> bool:
    if (
        urlsplit(response.url).path != "/ThreepalmMiddleware/api/v1/Admin/Filter"
        or response.request.method != "GET"
    ):
        return False
    params = parse_qs(urlsplit(response.url).query)
    return filters is None or all(
        params.get(key) == [value] for key, value in filters.items()
    )


def finish_jobs_update(page: Page, response: Response) -> int:
    finish_dashboard_update(page, response)
    expect(page.locator("#spinner:visible")).to_have_count(0, timeout=60_000)
    # This endpoint returns the complete filtered list in content.
    # Check the displayed total against that exact response, even when it is 0.
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        raise RuntimeError("Unexpected Jobs Dashboard response; count not reported.")
    return read_total_records(page, expected_count=len(payload["content"]))


JOB_TYPES = (
    ("SR Mach7", "SR_MACH7"),
    ("Study Routing Mach7", "STUDYROUTING_MACH7"),
    ("Case Dicom CMove", "CASE_DICOM_CMOVE"),
)



def capture_job_details(page: Page, api_job_type: str, api_status: str) -> tuple[str, str]:
    grid = page.locator(".ag-root-wrapper:visible").filter(
        has=page.locator(".ag-header-cell-text").filter(
            has_text=re.compile(r"^Job ID$")
        )
    )
    expect(grid).to_have_count(1, timeout=30_000)
    cell = grid.locator(".ag-center-cols-container .ag-row").first.locator(
        '[col-id="jobid"]'
    )
    expect(cell).to_be_visible(timeout=30_000)
    job_id = cell.inner_text().strip()
    if not job_id:
        raise RuntimeError("The first matching row has no Job ID")
    dialog = page.get_by_role("dialog", name="Job Details", exact=True)
    try:
        cell.click()
        expect(dialog).to_be_visible(timeout=30_000)
        # This modal has delayed Bootstrap opening callbacks beyond its CSS
        # fade. The observed UI needs a short settling interval before OK works.
        page.wait_for_timeout(1000)
        payload_view = dialog.locator("pre")
        expect(payload_view).to_have_count(1, timeout=30_000)
        expect(payload_view).to_contain_text(re.compile(r"\{"), timeout=30_000)
        payload = json.loads(payload_view.inner_text())
        if not isinstance(payload, dict) or str(payload.get("_id")) != job_id:
            raise RuntimeError("Job Details does not match the selected Job ID")
        normalize = lambda value: re.sub(r"[^a-z0-9]", "", str(value).lower())
        if normalize(payload.get("jobType")) != normalize(api_job_type):
            raise RuntimeError("Job Details has a different job type")
        if normalize(payload.get("jobStatus")) != normalize(api_status):
            raise RuntimeError("Job Details status changed or does not match the filter")
        # Keep the complete object; do not print case/patient JSON to the terminal.
        return job_id, json.dumps(payload, indent=2, ensure_ascii=True, allow_nan=False)
    finally:
        if dialog.is_visible():
            # Bootstrap ignores hide requests while the opening animation is
            # still running. Finish the transition before clicking OK.
            dialog.evaluate(
                """async element => {
                    await Promise.all(element.getAnimations({subtree: true})
                        .map(animation => animation.finished.catch(() => {})));
                }"""
            )
            dialog.get_by_role("button", name="OK", exact=True).click()
            expect(dialog).not_to_be_visible(timeout=30_000)


def open_jobs_dashboard(page: Page) -> None:
    page.goto(f"{BASE_URL}/admin/#/jobdashboard", wait_until="domcontentloaded")
    filters = page.locator("mat-select:visible")
    # Deployments can show the filters directly or behind a navigation tab.
    tab = page.get_by_role("button", name="Jobs Dashboard", exact=True).or_(
        page.get_by_role("tab", name="Jobs Dashboard", exact=True)
    ).or_(page.locator("a").filter(has_text=re.compile(r"^\s*Jobs Dashboard\s*$"))).filter(visible=True)
    expect(filters.first.or_(tab.first).first).to_be_visible(timeout=60_000)
    if filters.count() != 2:
        expect(tab).to_have_count(1, timeout=30_000)
        tab.click()
    expect(filters).to_have_count(2, timeout=60_000)
    # Apply explicitly so a response emitted during navigation cannot be missed.
    with page.expect_response(is_jobs_response, timeout=90_000) as initial:
        page.get_by_role("button", name="Apply", exact=True).click()
    finish_jobs_update(page, initial.value)


def report_jobs(page: Page, results=None, job_types=JOB_TYPES) -> None:
    open_jobs_dashboard(page)

    # These are the two visible job filters; generated mat-select IDs change.
    filters = page.locator("mat-select:visible")
    expect(filters).to_have_count(2, timeout=30_000)
    job_type = filters.nth(0)
    job_status = filters.nth(1)

    # Use the dashboard's current date, and include today plus two prior days.
    # Specify input because the datepicker wrapper has the same ID.
    from_date = page.locator("input#FromDatejobdash")
    to_date = page.locator("input#ToDateJobDash")
    end_date = datetime.strptime(to_date.input_value(), "%m/%d/%Y").date()
    start_date = end_date - timedelta(days=2)
    from_date.fill(start_date.strftime("%m/%d/%Y"))
    from_date.press("Tab")
    to_date.fill(end_date.strftime("%m/%d/%Y"))
    to_date.press("Tab")
    expect(from_date).to_have_value(start_date.strftime("%m/%d/%Y"))
    expect(to_date).to_have_value(end_date.strftime("%m/%d/%Y"))

    # The UI sends local date boundaries as UTC query parameters.
    date_filters = page.evaluate(
        """([start, end]) => {
            const boundary = (text, last) => {
                const [year, month, day] = text.split("-").map(Number);
                return new Date(year, month - 1, day,
                    last ? 23 : 0, last ? 59 : 0, last ? 59 : 0)
                    .toISOString().slice(0, 19).replace("T", " ");
            };
            return {FromDate: boundary(start, false), ToDate: boundary(end, true)};
        }""",
        [start_date.isoformat(), end_date.isoformat()],
    )
    sample_failures = []
    for job_name, api_job_type in job_types:
        failure_status = (
            ("FAILED", "failed") if api_job_type == "SR_MACH7"
            else ("VIEWER FAILED", "viewerfailed")
        )
        statuses = {
            "NEW": "new",
            "INPROGRESS": "inprogress",
            failure_status[0]: failure_status[1],
            "VIEWER SUCCESS": "viewersuccess",
        }
        job_type.click()
        page.get_by_role("option", name=job_name, exact=True).click()
        expect(job_type).to_have_text(job_name)
        print(
            f"{job_name} jobs ({start_date:%Y-%m-%d} to {end_date:%Y-%m-%d}, inclusive):",
            flush=True,
        )
        for status, api_status in statuses.items():
            job_status.click()
            page.get_by_role("option", name=status, exact=True).click()
            expect(job_status).to_have_text(status)
            expected_filters = {
                "DestinationGuid": "all",
                "JobType": api_job_type,
                "JobStatus": api_status,
                **date_filters,
            }
            with page.expect_response(
                lambda response: is_jobs_response(response, expected_filters),
                timeout=90_000,
            ) as updated:
                page.get_by_role("button", name="Apply", exact=True).click()
            count = finish_jobs_update(page, updated.value)
            print(f"  {status}: {count}", flush=True)
            record_result(results, f"{job_name} jobs", status, count,
                          date_range=f"{start_date:%Y-%m-%d} to {end_date:%Y-%m-%d}",
                          details="Inclusive dates; status count, not a stuck-duration assessment.")
            if api_status in ("new", "inprogress", "failed", "viewerfailed"):
                period = f"{start_date:%Y-%m-%d} to {end_date:%Y-%m-%d}"
                if count == 0:
                    record_job_sample(
                        results, job_name, status, count, period, "NO MATCHING JOB",
                        details="Count is zero; no Job Details dialog opened.",
                    )
                else:
                    try:
                        job_id, job_json = capture_job_details(page, api_job_type, api_status)
                        record_job_sample(
                            results, job_name, status, count, period, "CAPTURED",
                            job_id=job_id, job_json=job_json,
                            details="One sample: first displayed job. ID, type and status verified.",
                        )
                        print(f"    {status}: one Job Details JSON captured", flush=True)
                    except (AssertionError, PlaywrightError, RuntimeError, ValueError) as exc:
                        sample_failures.append(f"{job_name} / {status}")
                        record_job_sample(
                            results, job_name, status, count, period, "ERROR",
                            details=f"Could not capture a verified sample ({type(exc).__name__}).",
                        )
                        print(f"    {status}: Job Details capture failed ({type(exc).__name__})", flush=True)
    if sample_failures:
        raise RuntimeError("Job Details sample capture failed: " + ", ".join(sample_failures))



def report_sr_mach7_jobs(page: Page, results=None) -> None:
    """Keep the existing single-job function available for direct callers."""
    report_jobs(page, results, job_types=(JOB_TYPES[0],))


ASSIGNMENT_TABS = {
    "Fulfilled": {"case_assignment_status": "autocase", "isfullfill": 1, "roomcheck": 0},
    "Un-Fulfilled": {"case_assignment_status": "autocase", "isfullfill": 2, "roomcheck": 0},
    "Same Room": {"case_assignment_status": "sameday", "isfullfill": 2, "roomcheck": 1},
    "Different Room": {"case_assignment_status": "sameday", "isfullfill": 2, "roomcheck": 2},
    "Exceptions": {"case_assignment_status": "exception", "isfullfill": 2, "roomcheck": 0},
}


def is_assignment_request(request: Request, filters: dict) -> bool:
    if (
        urlsplit(request.url).path != "/roomsexceptionapi/analytics/assignment/caseslist"
        or request.method != "POST"
    ):
        return False
    payload = request.post_data_json
    return isinstance(payload, dict) and all(
        payload.get(key) == value for key, value in filters.items()
    )


def assignment_button(page: Page, name: str):
    return page.get_by_role(
        "button",
        name=re.compile(r"^\s*" + re.escape(name) + r"\s*\(\s*[\d,]+\s*\)\s*$"),
    )


def finish_assignment_request(page: Page, request: Request) -> None:
    # Called only after requestfinished: large case lists need not be copied
    # out of Chromium's response cache just to verify that their table loaded.
    response = request.response()
    if response is None or not response.ok:
        status = response.status if response else "no response"
        raise RuntimeError(f"Case Assignment data request failed: {status}")
    wait_for_results(page)
    expect(page.locator("#spinner:visible")).to_have_count(0, timeout=90_000)


def assignment_grid(page: Page, name: str):
    # Assignment groups cases by Location; room views group them by Client.
    # Exceptions shows case records directly. Radiologist grids do not prove
    # that the selected assignment category has finished loading.
    if name in ("Fulfilled", "Un-Fulfilled"):
        heading = r"^Location(?:\s*\([\d,]+\))?$"
    elif name in ("Same Room", "Different Room"):
        heading = r"^Client(?:\s*\([\d,]+\))?$"
    else:
        heading = r"^Case\s*#$"
    return page.locator(".ag-root-wrapper:visible").filter(
        has=page.locator(".ag-header-cell-text").filter(
            has_text=re.compile(heading)
        )
    )


def check_assignment_table(page: Page, name: str) -> tuple[int, str]:
    button = assignment_button(page, name)
    expect(button).to_be_visible()
    expect(button).to_have_class(re.compile(r"\blightgreenstyle\b"))
    badge = re.search(r"\(([\d,]+)\)", button.inner_text())
    if badge is None:
        raise RuntimeError(f"{name}: could not read tab count")
    count = int(badge.group(1).replace(",", ""))
    # Verify the data grid for this tab rather than an unrelated radiologist list.
    grid = assignment_grid(page, name)
    expect(grid).to_have_count(1, timeout=90_000)
    expect(page.locator("#spinner:visible")).to_have_count(0, timeout=90_000)
    expect(grid.locator(".ag-overlay-loading-center:visible")).to_have_count(
        0, timeout=90_000
    )
    rows = grid.locator(".ag-center-cols-container .ag-row")
    if count:
        expect(rows.first).to_be_visible(timeout=90_000)
        expect(rows.first).not_to_be_empty(timeout=90_000)
    else:
        expect(rows).to_have_count(0, timeout=90_000)
        expect(grid.locator(".ag-overlay-no-rows-center")).to_be_visible(timeout=90_000)
    row_count = grid.locator("[role=treegrid]").get_attribute("aria-rowcount")
    if row_count is None:
        raise RuntimeError(f"{name}: data grid has no loaded row count")
    return count, row_count



def capture_loaded_page(page: Page, name: str) -> Path:
    folder = Path(__file__).resolve().parent.parent / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    label = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    path = folder / f"{label}_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
    expect(page.locator("#spinner:visible")).to_have_count(0, timeout=60_000)
    original_viewport = page.viewport_size
    try:
        if name == "Legacy Operations":
            # The dashboard uses an internal scroll panel; full_page alone
            # does not expose its lower cards at a small viewport height.
            page.set_viewport_size({"width": 1920, "height": 1600})
        page.screenshot(path=str(path), full_page=True, animations="disabled")
    finally:
        if name == "Legacy Operations" and original_viewport is not None:
            page.set_viewport_size(original_viewport)
    print(f"Screenshot saved: {path}", flush=True)
    return path


def report_case_assignment(page: Page, results=None) -> None:
    print("Case Assignment loading checks:", flush=True)
    # Initial data is Un-Fulfilled; returning to it uses the page's cached data.
    with page.expect_event(
        "requestfinished",
        predicate=lambda request: is_assignment_request(
            request, ASSIGNMENT_TABS["Un-Fulfilled"]
        ),
        timeout=120_000,
    ) as initial:
        page.goto(
            f"{BASE_URL}/admin/#/automatedrad",
            wait_until="domcontentloaded",
        )
    finish_assignment_request(page, initial.value)
    try:
        _, cached_unfulfilled_rows = check_assignment_table(page, "Un-Fulfilled")
    except (AssertionError, PlaywrightError, RuntimeError):
        raise RuntimeError("Initial Un-Fulfilled assignment data did not load.") from None

    failures = []
    for name, filters in ASSIGNMENT_TABS.items():
        try:
            button = assignment_button(page, name)
            if name == "Un-Fulfilled":
                button.click()
                wait_for_results(page)
                # Verify the cached grid was restored, rather than reading the
                # previous Fulfilled table while the view is switching.
                expect(assignment_grid(page, name).locator("[role=treegrid]")).to_have_attribute(
                    "aria-rowcount", cached_unfulfilled_rows, timeout=90_000
                )
            else:
                with page.expect_event(
                    "requestfinished",
                    predicate=lambda request: is_assignment_request(request, filters),
                    timeout=120_000,
                ) as updated:
                    button.click()
                finish_assignment_request(page, updated.value)
            count, _ = check_assignment_table(page, name)
            screenshot = capture_loaded_page(page, f"Case Assignment {name}")
            result = "data loaded" if count else "empty result loaded"
            print(f"  {name}: PASS - {result} (tab count: {count})", flush=True)
            record_result(results, "Case Assignment", name, count, "PASS",
                          details=f"{result}; count is the tab badge. Screenshot: {screenshot}")
        except (AssertionError, PlaywrightError, RuntimeError) as exc:
            failures.append(name)
            record_result(results, "Case Assignment", name, result="FAIL",
                          details=f"Loading check failed ({type(exc).__name__}).")
            # Keep case data and full DOM assertion dumps out of the report.
            print(f"  {name}: FAIL - loading check failed ({type(exc).__name__})", flush=True)
    if failures:
        raise RuntimeError("Case Assignment checks failed: " + ", ".join(failures))


LEGACY_ENDPOINTS = (
    "/analytics/dashboard/caseinflight/",
    "/analytics/dashboard/casescompletedcount/",
    "/analytics/dashboard/caseover/",
    "/analytics/dashboard/exceptionpending/",
    "/analytics/dashboard/addendumandcriticalcount/",
    "/analytics/dashboard/averagetat/",
    "/analytics/roomexception/casescount",
    "/docpanel-platformworkspace-api/api/v2/radCapacitySchedularDetails",
    "/analytics/v1/GetChart",
)

LEGACY_CARDS = {
    "Cases Pending": ("RTA", "Needs Attention", "Posted", "In Progress", "Overflow", "On Hold"),
    "Cases Completed": ("Yesterday", "Today", "Last Week", "This Week", "Last Month", "This Month"),
    "Cases Past 24 Hrs": ("Pending Weekday", "Pending Weekend", "Completed Weekday", "Completed Weekend"),
    "Exceptions Pending": ("STAT", "Expedited", "Needs More Info", "Time Out", "Decline"),
    "Addendums & Critical": ("Addendums Pending", "CF Pending", "Addendums Completed", "CF Completed"),
    "Average TAT": ("Weekday", "Weekend", "Overall"),
    "Rooms": (),
    "Out Of Office": ("Salaried", "Full Time", "Steady Eddy", "Part Time", "Episodic"),
    "Load Balancing": ("Under", "Over"),
}


def is_legacy_request(request: Request, endpoint: str) -> bool:
    path = urlsplit(request.url).path
    return request.method != "OPTIONS" and (
        path.startswith(endpoint) if endpoint.endswith("/") else path == endpoint
    )


def report_legacy_operations(page: Page, results=None) -> None:
    print("Legacy Operations loading checks:", flush=True)
    phase = "dashboard requests"
    try:
        # All listeners start before navigation. The page initially displays
        # zero placeholders, so networkidle alone cannot establish readiness.
        with ExitStack() as stack:
            pending = {
                endpoint: stack.enter_context(page.expect_event(
                    "requestfinished",
                    predicate=lambda request, endpoint=endpoint: is_legacy_request(request, endpoint),
                    timeout=120_000,
                ))
                for endpoint in LEGACY_ENDPOINTS
            }
            link = page.get_by_role("link", name="Legacy Operations", exact=True)
            if link.is_visible():
                link.click()
            else:
                page.goto(
                    f"{BASE_URL}/admin/#/operationsreports",
                    wait_until="domcontentloaded",
                )
            page.wait_for_url(f"{BASE_URL}/admin/#/operationsreports", timeout=60_000)

        payloads = {}
        for endpoint, event in pending.items():
            phase = endpoint
            response = event.value.response()
            if response is None or not response.ok:
                raise RuntimeError("Dashboard data request failed")
            payload = response.json()
            if not isinstance(payload, (dict, list)) or not payload:
                raise RuntimeError("Dashboard returned no structured data")
            payloads[endpoint] = payload
        wait_for_results(page)
        expect(page.locator("#spinner:visible")).to_have_count(0, timeout=60_000)

        office = page.locator(".card").filter(
            has=page.locator(".card-header h4").filter(has_text="Out Of Office")
        )
        date_text = re.search(r"\d{2}/\d{2}/\d{4}", office.inner_text())
        if date_text is None:
            raise RuntimeError("Could not read the dashboard availability date")
        today = datetime.strptime(date_text[0], "%m/%d/%Y").date().isoformat()
        metrics = build_metrics(payloads, LEGACY_ENDPOINTS, today)
        failures = []
        for title in LEGACY_CARDS:
            phase = title
            heading = page.locator(".card-header h4").filter(
                has_text=re.compile(r"^" + re.escape(title) + r"(?:\s*\(|\s*$)")
            )
            card = page.locator(".card").filter(has=heading)
            expect(card).to_have_count(1, timeout=60_000)
            expect(card).to_be_visible(timeout=60_000)
            body = card.locator(".card-body")
            dates = re.findall(r"\d{2}/\d{2}/\d{4}", card.inner_text())
            date_range = " to ".join(dates) if dates else "Dashboard current periods"
            for item in (metric for metric in metrics if metric["card"] == title):
                phase = title + " / " + item["label"]
                try:
                    expect(body).to_contain_text(
                        expected_pattern(item), timeout=10_000, use_inner_text=True
                    )
                except AssertionError:
                    pass
                actual = read_metric(body.inner_text(), item)
                passed = actual is not None and actual == item["value"]
                result = "PASS" if passed else "FAIL"
                if not passed:
                    failures.append(phase)
                details = "data loaded; count is the tab badge." if passed else "Screen value missing or different from API."
                if not passed and item["note"]:
                    details += " " + item["note"]
                record_result(
                    results, "Legacy Operations", phase, actual, result,
                    date_range=date_range, details=details,
                    api_count=item["value"], unit=item["unit"],
                )
                print(f"  {phase}: {actual} {item['unit']} | API: {item['value']} | {result}", flush=True)
        if failures:
            raise RuntimeError(f"{len(failures)} Legacy Operations metrics did not match")
        screenshot = capture_loaded_page(page, "Legacy Operations")
        if results:
            results[-1]["Details"] += f" Screenshot: {screenshot}"
        print(f"Legacy Operations: PASS - all {len(metrics)} values match API data", flush=True)
    except (AssertionError, PlaywrightError, RuntimeError, ValueError, KeyError, TypeError):
        print(f"Legacy Operations: FAIL - {phase}", flush=True)
        record_result(results, "Legacy Operations", phase, result="FAIL",
                      details="Dashboard loading check failed.")
        raise RuntimeError(f"Legacy Operations loading check failed: {phase}") from None


def debug_checkpoint(page: Page, enabled: bool, label: str) -> None:
    if enabled:
        print(f"[DEBUG] {label} - click Resume in Playwright Inspector to continue.", flush=True)
        page.pause()


def run(playwright: Playwright, debug: bool = False, check: str = "all") -> None:
    results = []
    if check in ("database", "report_pending", "report_failures"):
        from database_checks import report_database_checks
        try:
            if not report_database_checks(results, only={"report_pending":"Report Pending", "report_failures":"Report Failures"}.get(check)):
                raise SystemExit(1)
        finally:
            save_excel_report(results)
        return
    if check == "zendesk":
        from zendesk_checks import report_zendesk
        try:
            if not report_zendesk(playwright, results, debug=debug):
                raise SystemExit(1)
        finally:
            save_excel_report(results)
        return
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(30_000)
    try:
        debug_checkpoint(page, debug, "Before login")
        if check in ("all", "cases"):
            # Login has its own waits; do not spend the response timeout on login.
            login(page)
            dashboard_url = f"{BASE_URL}/admin/#/Dashboard/1"
            with page.expect_response(is_dashboard_response, timeout=60_000) as loaded:
                # Force a new request even when login already landed on this SPA route.
                if page.url.rstrip("/") == dashboard_url.rstrip("/"):
                    page.reload(wait_until="domcontentloaded")
                else:
                    page.goto(dashboard_url, wait_until="domcontentloaded")
                page.wait_for_url(dashboard_url, timeout=60_000)
            finish_dashboard_update(page, loaded.value)
            expect(page.locator("#adminAgGrid")).to_be_visible(timeout=60_000)
            debug_checkpoint(page, debug, "1. Admin Dashboard total cases")
            total_cases = read_total_records(page)
            print(f"Total cases: {total_cases}", flush=True)
            record_result(results, "Admin Dashboard", "Total cases", total_cases)
            debug_checkpoint(page, debug, "2. Loading cases filter")
            select_loading(page)
            loading_cases = read_total_records(page)
            if loading_cases > total_cases:
                raise RuntimeError(
                    "Loading count exceeds total count; check dashboard filters "
                    "or rerun if cases changed during the request."
                )
            loading_status = "FAIL" if loading_cases > 5 else "PASS"
            print(f"Loading cases: {loading_cases} - {loading_status} (allowed: 5 or fewer)", flush=True)
            record_result(results, "Admin Dashboard", "Loading cases", loading_cases,
                          result=loading_status, details="Case Status: " + ", ".join(LOADING_CASE_STATUSES) + "; Study Status: Loading. PASS: 5 or fewer loading cases. FAIL: more than 5.")
        else:
            login(page)
        if check in ("all", "jobs"):
            debug_checkpoint(page, debug, "3. Job status counts")
            report_jobs(page, results)
        if check in ("all", "case_assignment"):
            debug_checkpoint(page, debug, "4. Case Assignment")
            report_case_assignment(page, results)
        if check in ("all", "legacy_operations"):
            debug_checkpoint(page, debug, "5. Legacy Operations")
            report_legacy_operations(page, results)
        if check in ("all", "case_details"):
            from case_details_checks import report_case_details
            debug_checkpoint(page, debug, "6. Case Details and Eunity")
            # Multi-Monitor temporarily disabled. Change False to True below to re-enable.
            if not report_case_details(playwright, results, debug=debug, page=page, multimonitor=False):
                raise SystemExit(1)
        debug_checkpoint(page, debug, "Checks complete; resume to save Excel and close browser")
    except Exception as exc:
        if debug and not page.is_closed():
            try:
                debug_checkpoint(page, True, f"Check failed ({type(exc).__name__}); inspect the page")
            except PlaywrightError:
                pass
        record_result(results, "Run", "Execution", result="FAIL",
                      details=f"Run stopped ({type(exc).__name__}); see terminal for the error.")
        raise
    finally:
        try:
            try:
                # PowerScribe is opt-in; skip it during the full run.
                # if check == "powerscribe":
                if check in ("all", "powerscribe"):
                    from powerscribe_case_checks import report_powerscribe
                    # Keep the independent IC selection but reuse this DocPanel login.
                    # Run only when explicitly selected with --check powerscribe.
                    report_powerscribe(playwright, results, debug=debug,
                                      page=page if not page.is_closed() else None)
            finally:
                context.close()
                browser.close()
        finally:
            try:
                if check == "all":
                    from database_checks import report_database_checks
                    report_database_checks(results)
            finally:
                try:
                    if check == "all":
                        from zendesk_checks import report_zendesk
                        report_zendesk(playwright, results, debug=debug)
                finally:
                    save_excel_report(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run DocPanel checks and export Excel results.")
    parser.add_argument(
        "--debug", action="store_true",
        help="Open Playwright Inspector and pause before each use case and on errors.",
    )
    parser.add_argument(
        "--check",
        choices=("all", "cases", "jobs", "case_assignment", "legacy_operations", "case_details", "database", "report_pending", "report_failures", "zendesk", "powerscribe"),
        default="all",
        help="Run only the selected check; default: all.",
    )
    parser.add_argument(
        "--user", choices=("user1", "user2"),
        help="DocPanel login profile; overrides the configured default for this run.",
    )
    args = parser.parse_args()
    if args.check in ("database", "report_pending", "report_failures"):
        run(None, debug=args.debug, check=args.check)
    else:
        with sync_playwright() as playwright:
            run(playwright, debug=args.debug, check=args.check)
