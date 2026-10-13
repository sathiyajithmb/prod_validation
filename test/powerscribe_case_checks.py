"""Independent PowerScribe check using its own imaging-center allowlist."""
# Allow direct execution from test/ while keeping configuration at project root.
import sys as _sys
from pathlib import Path as _Path
for _directory in (_Path(__file__).resolve().parent, _Path(__file__).resolve().parent.parent):
    if str(_directory) not in _sys.path:
        _sys.path.insert(0, str(_directory))

import argparse
import random
import re
import time
from pathlib import Path
from datetime import datetime
import utill
from playwright.sync_api import expect, sync_playwright
from case_details_checks import collect_inprogress_cases, check_inprogress_case
from database_checks import read_query
from excel_report import record_result, save_excel_report
from open_docpanel_cases import BASE_URL, login, is_dashboard_response, finish_dashboard_update
from powerscribe_checks import run_worker, record_case_check


POWERSCRIBE_CASE_DATE_RANGE = "Last two days"


def imaging_center_ids():
    ids = utill.POWERSCRIBE_IMG_CENTER_IDS
    if not isinstance(ids, (list, tuple)) or any(type(value) is not int or value <= 0 for value in ids):
        raise ValueError("POWERSCRIBE_IMG_CENTER_IDS must contain positive integer IDs.")
    return list(dict.fromkeys(ids))


def select_powerscribe_case(cases, case_number_filter=None):
    ids = imaging_center_ids()
    if not ids:
        raise ValueError("Set POWERSCRIBE_IMG_CENTER_IDS in utill.py.")
    candidates = {item["number"]: item for item in cases
                  if not case_number_filter or item["number"] == case_number_filter}
    centers = {}
    numbers = list(candidates)
    for start in range(0, len(numbers), 500):
        batch = numbers[start:start + 500]
        sql = ("SELECT case_number, img_center_id FROM cases WHERE case_number IN ("
               + ", ".join(["%s"] * len(batch)) + ") AND img_center_id IN ("
               + ", ".join(["%s"] * len(ids)) + ")")
        _, rows = read_query(utill.DB_CONFIG, utill.DB_REPORT_PENDING_DATABASE,
                             sql, tuple(batch + ids))
        for row in rows:
            number, center = str(row["case_number"]), int(row["img_center_id"])
            if number not in candidates or center not in ids:
                raise RuntimeError("PowerScribe eligibility query returned an unexpected case.")
            if number in centers and centers[number] != center:
                raise RuntimeError("Ambiguous PowerScribe imaging-center mapping.")
            centers[number] = center
    eligible = [dict(candidates[number], imaging_center_id=center) for number, center in centers.items()]
    return random.sample(eligible, min(1, len(eligible)))


class PowerScribeNavigationError(RuntimeError):
    pass


def open_powerscribe_case(page, item, debug=False):
    """Open and filter the radiologist's Active Cases before the report check."""
    detail = None
    stage = "starting case navigation"
    try:
        stage = "opening the admin preview"
        detail = check_inprogress_case(page, [], debug, POWERSCRIBE_CASE_DATE_RANGE, item["number"],
                                      verify_content=False, scroll_top=item.get("scroll_top"))
        stage = "returning to radiologist Active Cases using the back icon"
        back = detail.locator(".back-icon:visible")
        expect(back).to_have_count(1, timeout=60_000)
        back.click()
        stage = "filtering Active Cases by case number"
        search = detail.get_by_role("textbox", name=re.compile(r"Search for anything on this", re.I)).filter(visible=True)
        expect(search).to_have_count(1, timeout=60_000)
        search.fill(item["number"])
        expect(search).to_have_value(item["number"])
        print(f"PowerScribe: filtered radiologist cases by {item['number']}.", flush=True)
        stage = "locating the exact filtered case"
        grid = detail.get_by_role("treegrid").filter(visible=True)
        expect(grid).to_have_count(1, timeout=60_000)
        case = grid.get_by_text(item["number"], exact=True).filter(visible=True)
        expect(case).to_have_count(1, timeout=60_000)
        expect(case).to_be_visible(timeout=60_000)
        stage = "opening the filtered case report popup"
        with detail.expect_popup(timeout=60_000) as opened:
            case.click()
        report = opened.value
        report.wait_for_load_state("domcontentloaded")
        stage = "verifying the report popup case number"
        expect(report.locator("body")).to_contain_text(
            re.compile(r"Case\s*No\.?\s*:\s*" + re.escape(item["number"]) + r"\b", re.I), timeout=60_000)
        print(f"PowerScribe: report popup opened for case {item['number']}.", flush=True)
        return report
    except Exception as exc:
        message = f"Could not complete {stage} ({type(exc).__name__})."
        target = detail if detail is not None and not detail.is_closed() else page
        try:
            folder = Path(__file__).resolve().parent.parent / "screenshots"
            folder.mkdir(exist_ok=True)
            path = folder / f"powerscribe_navigation_fail_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
            target.screenshot(path=str(path), full_page=True, timeout=10_000)
            message += f" DocPanel navigation screenshot; PowerScribe load is unverified. Screenshot: {path}"
        except Exception:
            message += " Navigation screenshot unavailable."
        raise PowerScribeNavigationError(message) from exc


def prepare_shared_dashboard(page):
    """Refresh the existing dashboard without replacing its authenticated session."""
    dashboard_url = f"{BASE_URL}/admin/#/Dashboard/1"
    with page.expect_response(is_dashboard_response, timeout=60_000) as initial:
        # Navigating to the same SPA URL may not issue a dashboard request.
        if page.url.rstrip("/") == dashboard_url.rstrip("/"):
            page.reload(wait_until="domcontentloaded")
        else:
            page.goto(dashboard_url, wait_until="domcontentloaded")
    finish_dashboard_update(page, initial.value)
    with page.expect_response(is_dashboard_response, timeout=60_000) as reset:
        page.get_by_role("button", name="Reset", exact=True).click()
    finish_dashboard_update(page, reset.value)


def report_powerscribe(playwright, results, headless=False, debug=False, case_number=None, *, page=None):
    browser = None
    owns_browser = page is None
    original_viewport = page.viewport_size if page is not None else None
    original_pages = list(page.context.pages) if page is not None else []
    identity = ""
    stage = "starting"
    stage_started = time.perf_counter()
    def stage_done(label):
        nonlocal stage_started
        now = time.perf_counter()
        print(f"PowerScribe timing - {label}: {now - stage_started:.1f}s", flush=True)
        stage_started = now
    try:
        if not imaging_center_ids():
            record_result(results, "PowerScribe", "Same case opens in PowerScribe", result="SKIP",
                          details="Set POWERSCRIBE_IMG_CENTER_IDS in utill.py; no case was selected.")
            print("PowerScribe: SKIP - configure POWERSCRIBE_IMG_CENTER_IDS.", flush=True)
            return False
        if owns_browser:
            stage = "logging in to DocPanel"
            print("PowerScribe: logging in to DocPanel...", flush=True)
            browser = playwright.chromium.launch(headless=headless)
            context = browser.new_context(viewport={"width":1440,"height":1000})
            page = context.new_page()
            page.set_default_timeout(60_000)
            with page.expect_response(is_dashboard_response, timeout=60_000) as initial:
                login(page)
            finish_dashboard_update(page, initial.value)
        else:
            stage = "reusing DocPanel login: dashboard navigation response"
            print("PowerScribe: reusing DocPanel login...", flush=True)
            prepare_shared_dashboard(page)
        stage = "preparing the DocPanel session"
        page.context.grant_permissions(["local-network-access"], origin=BASE_URL)
        stage_done("DocPanel session ready")
        stage = "selecting an eligible IC case"
        print("PowerScribe: selecting an eligible IC case...", flush=True)
        selected = collect_inprogress_cases(page, [], POWERSCRIBE_CASE_DATE_RANGE, case_number,
                                            select_cases=select_powerscribe_case)
        stage_done("case selection and IC lookup")
        if not selected:
            record_result(results, "PowerScribe", "Same case opens in PowerScribe", result="SKIP",
                          details=f"No eligible In Progress Diagnostic case in {POWERSCRIBE_CASE_DATE_RANGE} in POWERSCRIBE_IMG_CENTER_IDS; nothing was opened.")
            print(f"PowerScribe: SKIP - no eligible In Progress Diagnostic case in {POWERSCRIBE_CASE_DATE_RANGE} in the configured imaging centers.", flush=True)
            return True
        item = selected[0]
        identity = f"Case {item['number']}; IC {item['imaging_center_id']}; radiologist {item['radiologist']}. "
        stage = "starting PowerScribe and checking login"
        print(f"PowerScribe: selected case {item['number']} from IC {item['imaging_center_id']}.", flush=True)
        print("PowerScribe: starting app / checking login...", flush=True)
        logged_in = run_worker("login")
        stage_done("app startup / login")
        start = len(results)
        if logged_in["Result"] == "PASS":
            # Open only this separately selected case; no Unity or Multi-Monitor checks.
            stage = "opening the selected case in DocPanel"
            open_powerscribe_case(page, item, debug)
            stage_done("open selected case")
        stage = "verifying the active PowerScribe report"
        print("PowerScribe: verifying the active report case...", flush=True)
        passed = record_case_check(results, item["number"], logged_in)
        stage_done("PowerScribe verification and screenshot")
        for row in results[start:]:
            row["Details"] = identity + row["Details"]
        return passed
    except Exception as exc:
        failure = (str(exc) if isinstance(exc, PowerScribeNavigationError)
                   else f"PowerScribe {stage} failed ({type(exc).__name__}).")
        record_result(results, "PowerScribe", "Same case opens in PowerScribe", result="FAIL",
                      details=identity + failure)
        print("PowerScribe: FAIL - " + failure, flush=True)
        return False
    finally:
        if owns_browser and browser is not None:
            browser.close()
        elif page is not None and not page.is_closed():
            for opened in list(page.context.pages):
                if opened not in original_pages and not opened.is_closed():
                    opened.close()
            if original_viewport is not None:
                page.set_viewport_size(original_viewport)


def run(headless=False, debug=False, case_number=None):
    results = []
    try:
        with sync_playwright() as playwright:
            passed = report_powerscribe(playwright, results, headless, debug, case_number)
    finally:
        save_excel_report(results)
    return passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--case-number", help="Optional case number; must be In Progress today and in the configured IC list")
    args = parser.parse_args()
    raise SystemExit(0 if run(args.headless and not args.debug, args.debug, args.case_number) else 1)
