"""Check up to two eligible In Progress Diagnostic cases from scanned grid rows."""
# Allow direct execution from test/ while keeping configuration at project root.
import sys as _sys
from pathlib import Path as _Path
for _directory in (_Path(__file__).resolve().parent, _Path(__file__).resolve().parent.parent):
    if str(_directory) not in _sys.path:
        _sys.path.insert(0, str(_directory))

import argparse
import re
import random
from datetime import datetime
import utill

from playwright.sync_api import expect, sync_playwright

from excel_report import record_result, save_excel_report
from open_docpanel_cases import (
    BASE_URL, capture_loaded_page, debug_checkpoint, finish_dashboard_update,
    is_dashboard_response, login, read_total_records,
)


class CaseDetailsCheckError(RuntimeError):
    """Safe stage description and screenshot evidence for a failed case check."""


def choose_case_sample(cases, case_number_filter=None):
    """Exclude configured centers before choosing up to two distinct cases."""
    from database_checks import read_query
    excluded = utill.CASE_DETAILS_EXCLUDED_IMG_CENTER_IDS
    if not isinstance(excluded, (list, tuple)) or any(type(value) is not int or value <= 0 for value in excluded):
        raise ValueError("CASE_DETAILS_EXCLUDED_IMG_CENTER_IDS must contain positive integer IDs.")
    candidates = [dict(item) for item in cases if not case_number_filter or item["number"] == case_number_filter]
    if excluded and candidates:
        centers = {}
        # Read only the IDs needed for exclusion; do not fetch patient information.
        numbers = list(dict.fromkeys(item["number"] for item in candidates))
        for start in range(0, len(numbers), 500):
            batch = numbers[start:start + 500]
            sql = ("SELECT case_number, img_center_id FROM cases WHERE case_number IN ("
                   + ", ".join(["%s"] * len(batch)) + ")")
            _, records = read_query(utill.DB_CONFIG, utill.DB_REPORT_PENDING_DATABASE, sql, tuple(batch))
            for record in records:
                number = str(record["case_number"])
                center = record["img_center_id"]
                if center is None:
                    raise RuntimeError("Case imaging-center ID is missing; exclusions cannot be verified.")
                center = int(center)
                if number in centers and centers[number] != center:
                    raise RuntimeError("Case imaging-center mapping is ambiguous; exclusions cannot be verified.")
                centers[number] = center
        if any(item["number"] not in centers for item in candidates):
            raise RuntimeError("Case imaging-center lookup is incomplete; exclusions cannot be verified.")
        candidates = [dict(item, imaging_center_id=centers[item["number"]])
                      for item in candidates if centers[item["number"]] not in excluded]
    return random.sample(candidates, min(2, len(candidates)))


def collect_inprogress_cases(page, results, date_range="Today", case_number_filter=None, *, select_cases=None):
    status = page.locator("#CaseStatus")
    if not status.is_visible():
        page.locator(".filterUpArrow:visible").click()
    if date_range not in ("Today", "Last two days"):
        raise ValueError("Case checks support only Today or Last two days.")
    print(f"Case selection: applying date filter {date_range}.", flush=True)
    page.locator('input[name="selectDate"]').click()
    with page.expect_response(is_dashboard_response, timeout=60_000) as dates_updated:
        page.get_by_text(date_range, exact=True).filter(visible=True).click()
        page.get_by_role("button", name="Apply", exact=True).click()
    finish_dashboard_update(page, dates_updated.value)
    print("Case selection: applying In Progress status.", flush=True)
    status.click()
    menu = page.locator(".dropdown-menu.show:visible")
    with page.expect_response(is_dashboard_response, timeout=60_000) as updated:
        menu.get_by_text("In Progress", exact=True).click()
        page.locator("body").first.press("Enter")
    finish_dashboard_update(page, updated.value)

    print("Case selection: reading filtered grid count.", flush=True)
    total = read_total_records(page)
    if total == 0:
        record_result(
            results, "Case Details", "Can you open up cases in DocPanel?",
            result="SKIP", details=f"No In Progress Diagnostic case available in {date_range}.",
        )
        print(f"In Progress Case Details: SKIP - no In Progress Diagnostic case available in {date_range}.", flush=True)
        return []

    page.set_viewport_size({"width": 2600, "height": 1000})
    horizontal = page.locator("#adminAgGrid .ag-body-horizontal-scroll-viewport")
    if horizontal.count():
        horizontal.evaluate("el => { el.scrollLeft = 0; }")
    viewport = page.locator("#adminAgGrid .ag-body-viewport")
    viewport.evaluate("el => { el.scrollTop = 0; }")
    seen = set()
    selected = []
    selector = select_cases or choose_case_sample
    # Case Details needs two samples; PowerScribe/custom selection needs one.
    target = 1 if case_number_filter or select_cases is not None else 2
    while len(selected) < target:
        page.wait_for_timeout(250)
        rendered = page.locator("#adminAgGrid .ag-center-cols-container .ag-row").evaluate_all(
            """rows => rows.map(row => ({
                number: row.querySelector('[col-id="Case #"]')?.textContent.trim(),
                status: row.querySelector('[col-id="Case Status"]')?.textContent.trim(),
                study_status: row.querySelector('[col-id="Study Status"]')?.textContent.trim(),
                radiologist: row.querySelector('[col-id="Radiologist"]')?.textContent.trim(),
                scroll_top: document.querySelector('#adminAgGrid .ag-body-viewport').scrollTop
            }))""")
        batch = []
        for item in rendered:
            number = item.get("number") or ""
            if (number in seen or not re.fullmatch(r"\d+", number)
                    or re.sub(r"\s+", " ", item.get("status") or "").lower() != "in progress"
                    or re.sub(r"\s+", " ", item.get("study_status") or "").strip().lower() != "diagnostic"):
                continue
            seen.add(number)
            if case_number_filter and number != case_number_filter:
                continue
            batch.append({"number": number,
                          "radiologist": item.get("radiologist") or "Unspecified",
                          "study_status": "Diagnostic",
                          "scroll_top": item["scroll_top"]})
        if batch:
            # Apply the existing DB-backed IC exclusions/allowlist to each batch.
            print(f"Case selection: verifying IC IDs for {len(batch)} visible eligible cases.", flush=True)
            eligible = selector(batch, case_number_filter)
            selected.extend(eligible[:target - len(selected)])
            if len(selected) >= target:
                break
        moved = viewport.evaluate("""el => {
            const before = el.scrollTop;
            el.scrollTop += Math.max(1, el.clientHeight * 0.8);
            return el.scrollTop > before;
        }""")
        if not moved:
            break
    if not selected:
        record_result(results, "Case Details", "Can you open up cases in DocPanel?", result="SKIP",
                      details=f"No eligible In Progress Diagnostic case found in scanned rows after imaging-center filtering; requested case: {case_number_filter or 'any two cases'}.")
    print(f"Selected {len(selected)} eligible In Progress case(s); stopped after finding the requested sample or reaching the grid end.", flush=True)
    return selected


def check_inprogress_case(page, results, debug=False, date_range="Today", case_number_filter=None, *, verify_content=True, scroll_top=None):
    stage = "locating the selected case row"
    # The caller has already applied the date/status filters and collected cases.
    # Keep Radiologist, Case Status, and Study Status rendered together. Scrolling to the far
    # right can remove Radiologist from AG Grid's virtual DOM.
    page.set_viewport_size({"width": 2600, "height": 1000})
    # Radiologist is a horizontally virtualized column; reveal it first.
    horizontal = page.locator("#adminAgGrid .ag-body-horizontal-scroll-viewport")
    if horizontal.count():
        horizontal.evaluate("el => { el.scrollLeft = 0; }")
    rows = page.locator("#adminAgGrid .ag-center-cols-container .ag-row")
    matching = rows
    if case_number_filter:
        matching = matching.filter(has=page.locator('[col-id="Case #"]').filter(has_text=re.compile(r"^\s*" + re.escape(case_number_filter) + r"\s*$")))
    # Search the rendered rows while scrolling the virtual grid.
    viewport = page.locator("#adminAgGrid .ag-body-viewport")
    viewport.evaluate("(el, top) => { el.scrollTop = top; }", scroll_top or 0)
    retry_from_top = scroll_top is not None
    while True:
        page.wait_for_timeout(250)
        if matching.count():
            row_id = matching.first.get_attribute("row-id")
            break
        moved = viewport.evaluate("""el => {
            const before = el.scrollTop;
            el.scrollTop += Math.max(1, el.clientHeight * 0.8);
            return el.scrollTop > before;
        }""")
        if not moved:
            if retry_from_top:
                viewport.evaluate("el => { el.scrollTop = 0; }")
                retry_from_top = False
                continue
            raise RuntimeError(f"Selected case {case_number_filter} is no longer available in the filtered grid.")
    if row_id is None:
        raise RuntimeError("Selected case row has no row-id.")
    stage = "verifying the selected case row"
    if horizontal.count():
        horizontal.evaluate("el => { el.scrollLeft = 0; }")
    # Keep the same row after horizontal virtualization recreates its cells.
    row = page.locator("#adminAgGrid .ag-center-cols-container .ag-row").locator(
        "xpath=self::*[@row-id=" + repr(row_id) + "]"
    )
    expect(row).to_be_visible(timeout=60_000)
    radiologist = row.locator('[col-id="Radiologist"]').inner_text().strip() or "Unspecified"
    print(f"Selected radiologist: {radiologist}", flush=True)
    expect(row.locator('[col-id="Case Status"]')).to_have_text(
        re.compile(r"^\s*In Progress\s*$", re.I)
    )
    expect(row.locator('[col-id="Study Status"]')).to_have_text(
        re.compile(r"^\s*Diagnostic\s*$", re.I)
    )
    case_number = row.locator('[col-id="Case #"]').inner_text().strip()
    if not re.fullmatch(r"\d+", case_number):
        raise RuntimeError("Could not identify the selected case number.")

    # Match the pinned action row to the selected center row.
    row_id = row.get_attribute("row-id")
    if row_id is None:
        raise RuntimeError("The selected grid row has no row-id.")
    matching_rows = page.locator("#adminAgGrid .ag-row").filter(
        has=page.locator(".aggrid-rad-icon")
    )
    icon = None
    for index in range(matching_rows.count()):
        candidate = matching_rows.nth(index)
        if candidate.get_attribute("row-id") == row_id:
            icon = candidate.locator(".aggrid-rad-icon:visible")
            break
    if icon is None:
        raise RuntimeError("Case Details icon was not found for the selected row.")
    stage = "locating the Case Details icon"
    expect(icon).to_have_count(1)
    debug_checkpoint(page, debug, "Before opening In Progress Case Details")

    detail = None
    try:
        stage = "waiting for the Case Details popup"
        with page.expect_popup(timeout=60_000) as opened:
            icon.click()
        detail = opened.value
        detail.set_default_timeout(60_000)
        stage = "waiting for the Case Details page URL"
        detail.wait_for_url(
            re.compile(r"^" + re.escape(BASE_URL.rstrip("/")) + r"/(?:web/)?#/icCaseDetail/"), timeout=60_000
        )
        detail.wait_for_load_state("domcontentloaded")
        stage = "verifying the case number on the detail page"
        expect(detail.get_by_text(
            re.compile(r"^\s*Case\s*No\.?\s*:?\s*" + re.escape(case_number) + r"\s*$")
        )).to_be_visible()
        if not verify_content:
            return detail
        screenshot = capture_loaded_page(detail, "In Progress Case Details")
        record_result(
            results, "Case Details", "Can you open up cases in DocPanel?", result="PASS",
            details=f"Radiologist {radiologist}; case {case_number}: Case Details page opened and case number verified. Screenshot: {screenshot}",
        )
        print(f"In Progress Case Details: PASS - case {case_number}; Case Details page opened.")
        debug_checkpoint(detail, debug, "Case Details loaded")
        return detail
    except Exception as exc:
        target = detail if detail is not None and not detail.is_closed() else page
        message = f"Case {case_number} failed while {stage} ({type(exc).__name__})."
        folder = _Path(__file__).resolve().parent.parent / "screenshots"
        folder.mkdir(exist_ok=True)
        path = folder / f"case_details_fail_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
        try:
            target.screenshot(path=str(path), full_page=True, animations="disabled", timeout=10_000)
            message += f" Screenshot: {path}"
        except Exception as screenshot_error:
            message += f" Screenshot unavailable ({type(screenshot_error).__name__})."
        raise CaseDetailsCheckError(message) from exc


def verify_unity_images(viewer, timeout_seconds=60):
    """Verify Eunity's image canvas, excluding its toolbar and thumbnail strip."""
    import base64
    import time

    expect(viewer.locator("iframe#iframe")).to_be_visible(timeout=60_000)
    stage = viewer.frame_locator("iframe#iframe").locator("canvas#mdiStage")
    expect(stage).to_be_visible(timeout=60_000)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        pixels = base64.b64encode(stage.screenshot(timeout=10_000)).decode("ascii")
        evidence = viewer.evaluate(
            """async data => {
                const image = new Image();
                image.src = 'data:image/png;base64,' + data;
                await image.decode();
                const canvas = document.createElement('canvas');
                canvas.width = 160; canvas.height = 160;
                const ctx = canvas.getContext('2d', {willReadFrequently:true});
                // Sample the middle of the image stage to exclude edge overlays.
                ctx.drawImage(image, image.width*.25, image.height*.25,
                    image.width*.5, image.height*.5, 0, 0, 160, 160);
                const p = ctx.getImageData(0,0,160,160).data;
                const levels = new Set();
                let mid = 0, sum = 0, squares = 0;
                for (let i=0;i<p.length;i+=4) {
                    const v = Math.round((p[i]+p[i+1]+p[i+2])/3);
                    levels.add(v); sum += v; squares += v*v;
                    if (v > 20 && v < 235) mid++;
                }
                const n = p.length/4;
                return {levels:levels.size, fraction:mid/n,
                    deviation:Math.sqrt(Math.max(0,squares/n-(sum/n)**2))};
            }""", pixels,
        )
        if (evidence["levels"] >= 32 and evidence["fraction"] >= 0.15
                and evidence["deviation"] >= 12):
            return evidence
        viewer.wait_for_timeout(500)
    raise RuntimeError("Eunity image canvas stayed blank or lacked sufficient rendered image pixels.")


def check_unity_viewer(detail, results, debug=False):
    """Open Eunity through the recorded controls; never infer image load from a click."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from datetime import datetime
    from pathlib import Path

    viewer = detail
    stage = "opening the viewer popup from the primary study"
    result = "FAIL"
    message = ""
    try:
        # Admin previews open a report; report pages already have viewer controls.
        if "reportTemplatePage" not in detail.url:
            with detail.expect_popup(timeout=30_000) as opened:
                detail.locator("#caseImageId0 > #thumImg0").click()
            viewer = opened.value
        viewer.set_default_timeout(60_000)
        viewer.wait_for_load_state("domcontentloaded")
        show_viewer = viewer.get_by_role("button", name="Show Viewer", exact=True)
        frame = viewer.locator("iframe#iframe")
        expand = viewer.locator("#expandIcon")
        stage = "waiting for Eunity viewer controls"
        expect(show_viewer.or_(frame).or_(expand).filter(visible=True).first).to_be_visible(timeout=60_000)
        if not show_viewer.is_visible() and not frame.is_visible():
            stage = "expanding the viewer controls"
            expand.click()
        alerts = []
        def dismiss_alert(dialog):
            alerts.append(dialog.type)
            dialog.dismiss()
        viewer.on("dialog", dismiss_alert)
        try:
            stage = "opening Show Viewer"
            if not frame.is_visible():
                show_viewer.click()
            if alerts:
                raise RuntimeError("Show Viewer displayed a browser dialog; image load is unverified.")
            stage = "verifying rendered Eunity images"
            evidence = verify_unity_images(viewer)
            result = "PASS"
            message = ("Eunity image canvas rendered; central image pixel check passed "
                       f"({evidence['levels']} intensity levels). "
                       "This checks rendering, not clinical image correctness.")
            debug_checkpoint(viewer, debug, "Inspect Eunity image and loading indicators")
        finally:
            viewer.remove_listener("dialog", dismiss_alert)
    except PlaywrightTimeout:
        message = f"Timed out while {stage}; Eunity image load was not confirmed."
    except Exception as exc:
        message = f"Could not complete {stage}: {type(exc).__name__}."
    finally:
        folder = Path(__file__).resolve().parent.parent / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        screenshot = folder / f"unity_viewer_{result.lower()}_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
        try:
            viewer.screenshot(path=str(screenshot), full_page=True, animations="disabled")
            message += f" Screenshot: {screenshot}"
        except Exception:
            message += " Screenshot unavailable."
        record_result(results, "Eunity Viewer", "Images loading",
                      result=result, details=message)
        print(f"Eunity Viewer: {result} - {message}", flush=True)
    return result == "PASS"


def report_case_details(playwright, results, headless=False, debug=False, date_range="Today", case_number=None, *, page=None):
    expect.set_options(timeout=60_000)
    owns_browser = page is None
    browser = None
    if owns_browser:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.set_default_timeout(30_000)
    else:
        context = page.context
    original_viewport = page.viewport_size
    try:
        if owns_browser:
            # MFA/login must not consume the dashboard request timeout.
            login(page)
        dashboard_url = f"{BASE_URL}/admin/#/Dashboard/1"
        with page.expect_response(is_dashboard_response, timeout=60_000) as initial:
            # A same-URL SPA navigation may emit no new dashboard request.
            if page.url.rstrip("/") == dashboard_url.rstrip("/"):
                page.reload(wait_until="domcontentloaded")
            else:
                page.goto(dashboard_url, wait_until="domcontentloaded")
        finish_dashboard_update(page, initial.value)
        if not owns_browser:
            # The earlier Loading-case check must not restrict this selection.
            with page.expect_response(is_dashboard_response, timeout=60_000) as reset:
                page.get_by_role("button", name="Reset", exact=True).click()
            finish_dashboard_update(page, reset.value)
        selected = collect_inprogress_cases(page, results, date_range, case_number)
        if not selected:
            for section, check in [("Eunity Viewer", "Images loading")]:
                record_result(results, section, check, result="SKIP",
                              details=f"No eligible In Progress Diagnostic case available in {date_range} after imaging-center exclusions; check was not run.")
            return True
        all_passed = True
        for selected_case in selected:
            number = selected_case["number"]
            identity = f"Case {number}; radiologist {selected_case['radiologist']}. "
            start = len(results)
            existing_pages = list(context.pages)
            try:
                detail = check_inprogress_case(page, results, debug, date_range, number,
                                               scroll_top=selected_case.get("scroll_top"))
                unity_ok = check_unity_viewer(detail, results, debug)
                all_passed = all_passed and unity_ok
            except Exception as exc:
                all_passed = False
                failure = str(exc) if isinstance(exc, CaseDetailsCheckError) else f"Case check did not complete: {type(exc).__name__}."
                record_result(results, "Case Details", "Can you open up cases in DocPanel?", result="FAIL",
                              details=failure)
                for section, check in [("Eunity Viewer", "Images loading")]:
                    if not any(row.get("Section") == section for row in results[start:]):
                        record_result(results, section, check, result="SKIP",
                                      details="Case check did not complete; dependent check was not run.")
                print(f"Case {number}: FAIL - {failure} Continuing with remaining cases.", flush=True)
                debug_checkpoint(page, debug, "Inspect failed case")
            finally:
                for row in results[start:]:
                    row["Details"] = identity + row.get("Details", "")
                # Close only browser tabs created for this case; keep the login session.
                for opened_page in list(context.pages):
                    if opened_page not in existing_pages and not opened_page.is_closed():
                        opened_page.close()
        return all_passed
    except Exception as exc:
        record_result(
            results, "Case Details", "Can you open up cases in DocPanel?", result="FAIL",
            details=f"Check did not complete: {type(exc).__name__}. See console traceback.",
        )
        print(f"In Progress Case Details: FAIL - {type(exc).__name__}", flush=True)
        debug_checkpoint(context.pages[-1], debug, "Inspect failed check")
        raise
    finally:
        if owns_browser:
            context.close()
            browser.close()
        elif original_viewport is not None and not page.is_closed():
            page.set_viewport_size(original_viewport)
    return True

def run(headless=False, debug=False, date_range="Today", case_number=None):
    results = []
    with sync_playwright() as playwright:
        try:
            passed = report_case_details(playwright, results, headless, debug,
                                         date_range=date_range, case_number=case_number)
            if not passed:
                raise SystemExit(1)
        finally:
            save_excel_report(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--case-date-range", choices=["Today"], default="Today", help="Only today's cases are checked")
    parser.add_argument("--case-number", help="Select a specific In Progress case (default: random 2 eligible cases across all radiologists; imaging-center exclusions always apply)")
    args = parser.parse_args()
    run(headless=args.headless and not args.debug, debug=args.debug,
        date_range=args.case_date_range, case_number=args.case_number)
