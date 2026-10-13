"""Check up to two random In Progress cases with Diagnostic study status."""
# Allow direct execution from test/ while keeping configuration at project root.
import sys as _sys
from pathlib import Path as _Path
for _directory in (_Path(__file__).resolve().parent, _Path(__file__).resolve().parent.parent):
    if str(_directory) not in _sys.path:
        _sys.path.insert(0, str(_directory))

import argparse
import re
import random
import utill

from playwright.sync_api import expect, sync_playwright

from excel_report import record_result, save_excel_report
from open_docpanel_cases import (
    BASE_URL, capture_loaded_page, debug_checkpoint, finish_dashboard_update,
    is_dashboard_response, login, read_total_records,
)


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
    page.locator('input[name="selectDate"]').click()
    with page.expect_response(is_dashboard_response, timeout=60_000) as dates_updated:
        page.get_by_text(date_range, exact=True).filter(visible=True).click()
        page.get_by_role("button", name="Apply", exact=True).click()
    finish_dashboard_update(page, dates_updated.value)
    status.click()
    menu = page.locator(".dropdown-menu.show:visible")
    with page.expect_response(is_dashboard_response, timeout=60_000) as updated:
        menu.get_by_text("In Progress", exact=True).click()
        page.locator("body").first.press("Enter")
    finish_dashboard_update(page, updated.value)

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
    all_cases = {}
    while True:
        page.wait_for_timeout(250)
        rendered = page.locator("#adminAgGrid .ag-center-cols-container .ag-row").evaluate_all(
            """rows => rows.map(row => ({
                number: row.querySelector('[col-id="Case #"]')?.textContent.trim(),
                status: row.querySelector('[col-id="Case Status"]')?.textContent.trim(),
                study_status: row.querySelector('[col-id="Study Status"]')?.textContent.trim(),
                radiologist: row.querySelector('[col-id="Radiologist"]')?.textContent.trim(),
                scroll_top: document.querySelector('#adminAgGrid .ag-body-viewport').scrollTop
            }))""")
        for item in rendered:
            number = item.get("number") or ""
            if (re.fullmatch(r"\d+", number)
                    and re.sub(r"\s+", " ", item.get("status") or "").lower() == "in progress"):
                all_cases[number] = {
                    "number": number,
                    "radiologist": item.get("radiologist") or "Unspecified",
                    "study_status": re.sub(r"\s+", " ", item.get("study_status") or "").strip(),
                    "scroll_top": item["scroll_top"],
                }
        moved = viewport.evaluate("""el => {
            const before = el.scrollTop;
            el.scrollTop += Math.max(1, el.clientHeight * 0.8);
            return el.scrollTop > before;
        }""")
        if not moved:
            break
    if len(all_cases) != total:
        raise RuntimeError(f"Could collect {len(all_cases)} of {total} filtered cases; complete coverage cannot be confirmed.")
    cases = {number: item for number, item in all_cases.items()
             if item["study_status"].lower() == "diagnostic"}
    if not cases:
        record_result(
            results, "Case Details", "Can you open up cases in DocPanel?",
            result="SKIP", details=f"No In Progress Diagnostic case available in {date_range}.",
        )
        print(f"In Progress Case Details: SKIP - no In Progress Diagnostic case available in {date_range}.", flush=True)
        return []
    selector = select_cases or choose_case_sample
    selected = selector(list(cases.values()), case_number_filter)
    if not selected:
        record_result(results, "Case Details", "Can you open up cases in DocPanel?", result="SKIP",
                      details=f"No eligible In Progress Diagnostic case after imaging-center exclusions; requested case: {case_number_filter or 'random sample'}.")
    print(f"Selected {len(selected)} eligible In Progress case(s).", flush=True)
    return selected


def check_inprogress_case(page, results, debug=False, date_range="Today", case_number_filter=None, *, verify_content=True, scroll_top=None):
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
    expect(icon).to_have_count(1)
    debug_checkpoint(page, debug, "Before opening In Progress Case Details")

    with page.expect_popup(timeout=60_000) as opened:
        icon.click()
    detail = opened.value
    detail.set_default_timeout(60_000)
    detail.wait_for_url(
        re.compile(re.escape(BASE_URL) + r"/web/#/icCaseDetail/"), timeout=60_000
    )
    detail.wait_for_load_state("domcontentloaded")
    expect(detail.get_by_text(
        re.compile(r"^\s*Case\s*No\.?\s*:?\s*" + re.escape(case_number) + r"\s*$")
    )).to_be_visible()
    if not verify_content:
        return detail
    expect(detail.get_by_text("Primary Study", exact=True)).to_be_visible()
    expect(detail.get_by_role("heading", name="Study Information", exact=True).filter(visible=True)).to_be_visible()
    thumbnail = detail.locator("#caseImageId0 > #thumImg0")
    expect(thumbnail).to_be_visible()
    # The application renders thumbnails as CSS background images on DIVs.
    detail.wait_for_function(
        r"""async () => {
            const el = document.querySelector('#caseImageId0 > #thumImg0');
            if (!el) return false;
            const bg = getComputedStyle(el).backgroundImage;
            const match = bg.match(/^url\(["']?(.*?)["']?\)$/);
            if (!match) return false;
            const image = new Image();
            image.src = match[1];
            try { await image.decode(); return image.naturalWidth > 0; }
            catch { return false; }
        }""", timeout=60_000,
    )
    screenshot = capture_loaded_page(detail, "In Progress Case Details")
    record_result(
        results, "Case Details", "Can you open up cases in DocPanel?", result="PASS",
        details=f"Radiologist {radiologist}; case {case_number}: matching case details, Primary Study, Study Information "
                f"and series thumbnail loaded. Screenshot: {screenshot}",
    )
    print(f"In Progress Case Details: PASS - case {case_number}; data and thumbnail loaded.")
    debug_checkpoint(detail, debug, "Case Details loaded")
    return detail


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
        with detail.expect_popup(timeout=30_000) as opened:
            detail.locator("#caseImageId0 > #thumImg0").click()
        viewer = opened.value
        viewer.set_default_timeout(60_000)
        viewer.wait_for_load_state("domcontentloaded")
        stage = "expanding the viewer controls"
        viewer.locator("#expandIcon").click()
        alerts = []
        def dismiss_alert(dialog):
            alerts.append(dialog.type)
            dialog.dismiss()
        viewer.on("dialog", dismiss_alert)
        try:
            stage = "opening Show Viewer"
            viewer.get_by_role("button", name="Show Viewer", exact=True).click()
            if alerts:
                raise RuntimeError("Show Viewer displayed a browser dialog; image load is unverified.")
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


def report_case_details(playwright, results, headless=False, debug=False, multimonitor=True, app_cdp="http://127.0.0.1:9223", date_range="Today", case_number=None, *, page=None):
    from multimonitor_checks import (connect_multimonitor, snapshot_app, check_multimonitor,
                                     ensure_multimonitor_started, DesktopStartupError)
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
        app = None
        before = None
        startup_error = None
        if multimonitor:
            try:
                ensure_multimonitor_started(app_cdp)
            except DesktopStartupError as exc:
                startup_error = str(exc)
                print(f"Multi-Monitor: {startup_error}", flush=True)
            context.grant_permissions(["local-network-access"], origin=BASE_URL)
        with page.expect_response(is_dashboard_response, timeout=60_000) as initial:
            if owns_browser:
                login(page)
            else:
                # Reuse authentication; a full navigation clears the previous
                # dashboard component before selecting today's case.
                page.goto(f"{BASE_URL}/admin/#/Dashboard/1", wait_until="domcontentloaded")
        finish_dashboard_update(page, initial.value)
        if not owns_browser:
            # The earlier Loading-case check must not restrict this selection.
            with page.expect_response(is_dashboard_response, timeout=60_000) as reset:
                page.get_by_role("button", name="Reset", exact=True).click()
            finish_dashboard_update(page, reset.value)
        selected = collect_inprogress_cases(page, results, date_range, case_number)
        if not selected:
            for section, check in [("Eunity Viewer", "Images loading")] + ([("Multi-Monitor", "Case selection")] if multimonitor else []):
                record_result(results, section, check, result="SKIP",
                              details=f"No eligible In Progress Diagnostic case available in {date_range} after imaging-center exclusions; check was not run.")
            return True
        all_passed = True
        for selected_case in selected:
            number = selected_case["number"]
            identity = f"Case {number}; radiologist {selected_case['radiologist']}. "
            start = len(results)
            existing_pages = list(context.pages)
            app = None
            before = None
            try:
                # Snapshot before opening the case: the desktop app can open automatically.
                if multimonitor:
                    try:
                        if startup_error:
                            raise DesktopStartupError(startup_error)
                        app = connect_multimonitor(playwright, app_cdp)
                        before = snapshot_app(app)
                    except Exception as exc:
                        record_result(results, "Multi-Monitor", "Desktop automation connection", result="FAIL",
                                      details=(str(exc) if isinstance(exc, DesktopStartupError) else
                                               "Automatic startup completed, but desktop connection or snapshot failed (" + type(exc).__name__ + ")."))
                        app = None
                detail = check_inprogress_case(page, results, debug, date_range, number,
                                               scroll_top=selected_case.get("scroll_top"))
                unity_ok = check_unity_viewer(detail, results, debug)
                multi_ok = (check_multimonitor(app, before, number, results, debug=debug)
                            if multimonitor and app is not None else not multimonitor)
                all_passed = all_passed and unity_ok and multi_ok
            except Exception as exc:
                all_passed = False
                record_result(results, "Case Details", "Can you open up cases in DocPanel?", result="FAIL",
                              details=f"Case check did not complete: {type(exc).__name__}.")
                for section, check in [("Eunity Viewer", "Images loading")] + ([("Multi-Monitor", "Case selection")] if multimonitor else []):
                    if not any(row.get("Section") == section for row in results[start:]):
                        record_result(results, section, check, result="SKIP",
                                      details="Case check did not complete; dependent check was not run.")
                print(f"Case {number}: FAIL - {type(exc).__name__}; continuing with remaining cases.", flush=True)
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

def run(headless=False, debug=False, multimonitor=True, app_cdp="http://127.0.0.1:9223", date_range="Today", case_number=None):
    results = []
    with sync_playwright() as playwright:
        try:
            passed = report_case_details(playwright, results, headless, debug,
                                         multimonitor, app_cdp, date_range, case_number)
            if not passed:
                raise SystemExit(1)
        finally:
            save_excel_report(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--multimonitor", action="store_true", default=True, help="Verify the Docpanel desktop app (enabled by default)")
    parser.add_argument("--app-cdp", default="http://127.0.0.1:9223", help="Local desktop debugging endpoint")
    parser.add_argument("--case-date-range", choices=["Today"], default="Today", help="Only today's cases are checked")
    parser.add_argument("--case-number", help="Select a specific In Progress case (default: random 2 eligible cases across all radiologists; imaging-center exclusions always apply)")
    args = parser.parse_args()
    run(headless=args.headless and not args.debug, debug=args.debug,
        multimonitor=args.multimonitor, app_cdp=args.app_cdp, date_range=args.case_date_range, case_number=args.case_number)
