"""PowerScribe desktop login and read-only same-case checks.

Windows UI automation runs in a separate process to isolate COM from Playwright.
No report edit, save, discard or sign controls are used.
"""
# Allow direct execution from test/ while keeping configuration at project root.
import sys as _sys
from pathlib import Path as _Path
for _directory in (_Path(__file__).resolve().parent, _Path(__file__).resolve().parent.parent):
    if str(_directory) not in _sys.path:
        _sys.path.insert(0, str(_directory))

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
import time


def header_matches(text, case_number):
    # Match only the active report header: (Draft) 2062581 - ...
    return bool(re.match(r"^\s*(?:\([^)]*\)|\[[^]]*\])?\s*" +
                         re.escape(str(case_number)) + r"\s*[-\u2013\u2014]", text))


class PowerScribeNotOpen(RuntimeError):
    pass


class PowerScribeStartupError(RuntimeError):
    pass


def _integrity_rid(pid):
    """Return a process integrity RID, or None when Windows denies inspection."""
    import ctypes
    import ctypes.wintypes as wintypes
    kernel = ctypes.windll.kernel32
    advapi = ctypes.windll.advapi32
    kernel.OpenProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.restype = wintypes.BOOL
    process = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return None
    token = wintypes.HANDLE()
    try:
        if not advapi.OpenProcessToken(process, 0x0008, ctypes.byref(token)):
            return None
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 25, None, 0, ctypes.byref(size))  # TokenIntegrityLevel
        if not size.value:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 25, buffer, size, ctypes.byref(size)):
            return None
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        subauthorities = ctypes.cast(sid, ctypes.POINTER(ctypes.c_ubyte))[1]
        return ctypes.cast(sid + 8 + (subauthorities - 1) * 4,
                           ctypes.POINTER(wintypes.DWORD)).contents.value
    finally:
        if token:
            kernel.CloseHandle(token)
        kernel.CloseHandle(process)


def powerscribe_window():
    import ctypes
    from pywinauto import Desktop
    windows = [w for w in Desktop(backend="uia").windows()
               if w.element_info.automation_id == "AutId_PowerScribeOne"
               or "powerscribe" in w.window_text().lower()]
    visible = [w for w in windows if w.is_visible()]
    if visible:
        main = [w for w in visible if w.element_info.automation_id == "AutId_PowerScribeOne"]
        return main[0] if main else visible[0]

    # Windows UI Automation hides controls of higher-integrity app windows.
    # Find the native HWND so we can distinguish that from a slow/missing launch.
    hwnd = ctypes.windll.user32.FindWindowW(None, "PowerScribe One")
    if hwnd and ctypes.windll.user32.IsWindowVisible(hwnd):
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        app_rid = _integrity_rid(pid.value)
        current_rid = _integrity_rid(ctypes.windll.kernel32.GetCurrentProcessId())
        if app_rid is not None and current_rid is not None and app_rid > current_rid:
            raise PowerScribeStartupError(
                "PowerScribe is open but runs with higher Windows privileges than this automation. "
                "Run the PowerShell window as Administrator, then start this check again."
            )
    raise PowerScribeNotOpen("PowerScribe window was not found.")


def ensure_powerscribe_started(timeout=90):
    import utill
    try:
        return powerscribe_window()
    except PowerScribeNotOpen:
        pass
    exe = Path(utill.POWERSCRIBE_APP_PATH)
    if exe.is_file():
        command = [str(exe)]
        working_directory = str(exe.parent)
    else:
        deployment_url = getattr(utill, "POWERSCRIBE_DEPLOYMENT_URL", "")
        if not deployment_url:
            raise PowerScribeStartupError("PowerScribe executable was not found. Update POWERSCRIBE_APP_PATH or configure POWERSCRIBE_DEPLOYMENT_URL in utill.py.")
        command = ["rundll32.exe", "dfshim.dll,ShOpenVerbApplication", deployment_url]
        working_directory = None
    try:
        # Credentials are filled through the UI, never passed as process arguments.
        subprocess.Popen(command, cwd=working_directory, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    except OSError as exc:
        raise PowerScribeStartupError("PowerScribe automatic startup failed; check POWERSCRIBE_APP_PATH in utill.py.") from exc
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return powerscribe_window()
        except PowerScribeNotOpen:
            time.sleep(0.5)
    raise PowerScribeStartupError(f"PowerScribe was launched but its window did not appear within {timeout} seconds.")


def login_controls(window):
    edits = [c for c in window.descendants(control_type="Edit") if c.is_visible()]
    passwords = [c for c in edits if c.element_info.element.CurrentIsPassword
                 or c.element_info.automation_id == "AutId_LoginWindow_PwdBox"]
    users = [c for c in edits if c not in passwords and
             any(label in re.sub(r"[^a-z]", "", (c.element_info.automation_id + " " + c.window_text()).lower())
                 for label in ("username", "userid", "loginname"))]
    buttons = [c for c in window.descendants(control_type="Button") if c.is_visible() and
               re.sub(r"[^a-z]", "", c.window_text().lower()) in ("login", "signin", "logon")]
    return (users[0], passwords[0], buttons[0]) if len(users) == len(passwords) == len(buttons) == 1 else None


def authenticated(window):
    return any(c.is_visible() and c.element_info.automation_id == "AutId_WindowTitleBar_UserDropDownButton"
               for c in window.descendants(control_type="Button"))


def screenshot(window, label):
    if window is None:
        return " Screenshot unavailable: PowerScribe window was not found."
    try:
        # Only the application window is captured, not the whole desktop.
        window.set_focus()
        folder = Path(__file__).resolve().parent.parent / "screenshots"
        folder.mkdir(exist_ok=True)
        path = folder / f"powerscribe_{label}_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
        window.capture_as_image().save(str(path))
        return f" Screenshot: {path}"
    except Exception as exc:
        return f" Screenshot unavailable: PowerScribe capture failed ({type(exc).__name__})."


def ensure_login():
    import utill
    window = None
    try:
        window = ensure_powerscribe_started()
        controls = None
        ready_deadline = time.monotonic() + 20
        while time.monotonic() < ready_deadline:
            if authenticated(window):
                return {"Result": "PASS", "Details": "Existing PowerScribe login session reused."}
            controls = login_controls(window)
            if controls:
                break
            time.sleep(0.5)
            try:
                window = powerscribe_window()
            except PowerScribeNotOpen:
                continue
        if controls is None:
            return {"Result":"FAIL", "Details":"PowerScribe opened but login controls could not be identified safely; login was not submitted." + screenshot(window, "login_fail")}
        username, password, submit = controls
        username.set_edit_text(utill.POWERSCRIBE_USERNAME)
        # PowerScribe's WPF password control does not reliably accept UIA Value writes.
        password.set_focus()
        literal_password = "".join("{" + char + "}" if char in "+^%~(){}" else char
                                   for char in utill.POWERSCRIBE_PASSWORD)
        password.type_keys("^a{BACKSPACE}" + literal_password + "{TAB}",
                           with_spaces=True, pause=0.01, set_foreground=False)
        submit.invoke()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                window = powerscribe_window()
            except PowerScribeNotOpen:
                time.sleep(0.5)
                continue
            if authenticated(window):
                return {"Result":"PASS", "Details":"PowerScribe login completed."}
            time.sleep(0.5)
        return {"Result":"FAIL", "Details":"PowerScribe login did not complete within 45 seconds." + screenshot(window, "login_fail")}
    except PowerScribeStartupError as exc:
        return {"Result":"FAIL", "Details":str(exc) + screenshot(window, "login_fail")}
    except Exception as exc:
        return {"Result":"FAIL", "Details":f"PowerScribe startup/login could not be completed ({type(exc).__name__})." + screenshot(window, "login_fail")}


def matching_report_editors(window, case_number, selected_only=False):
    matches = []
    for editor in window.descendants(control_type="TabItem"):
        if editor.element_info.automation_id != "reportEditorScreen" or not editor.is_visible():
            continue
        if selected_only and not editor.is_selected():
            continue
        if any(header_matches(c.window_text(), case_number)
               for c in editor.children(control_type="Text") if c.is_visible()):
            matches.append(editor)
    return matches


def report_close_button(button):
    # The application title-bar Close button must never be used.
    if button.window_text().strip().lower() != "close" or not button.is_visible() or not button.is_enabled():
        return False
    current = button
    for _ in range(20):
        info = current.element_info
        if info.control_type == "TitleBar":
            return False
        current = current.parent()
        if current is None:
            break
    return True


def has_powerscribe_dialog(window):
    from pywinauto import Desktop
    # A confirmation may be an owned top-level window or a child dialog.
    if any(c.is_visible() for c in window.descendants(control_type="Window")):
        return True
    return any(w.is_visible() and w.handle != window.handle
               for w in Desktop(backend="uia").windows(process=window.process_id()))


def close_verified_case(window, case_number):
    try:
        if len(matching_report_editors(window, case_number, selected_only=True)) != 1:
            return {"Clicked": False, "Details": "Close not clicked: the verified case is no longer the active report."}
        if has_powerscribe_dialog(window):
            return {"Clicked": False, "Details": "Close not clicked: an application dialog needs manual attention."}
        buttons = [c for c in window.descendants(control_type="Button") if report_close_button(c)]
        if len(buttons) != 1:
            return {"Clicked": False, "Details": "Close not clicked: one report Close control could not be identified."}
        # Recheck identity immediately before invoking a report action.
        if len(matching_report_editors(window, case_number, selected_only=True)) != 1:
            return {"Clicked": False, "Details": "Close not clicked: the active report changed."}
        buttons[0].invoke()
        return {"Clicked": True, "Details": "Clicked the PowerScribe report Close button."}
    except Exception as exc:
        return {"Clicked": False, "Details": f"PowerScribe report Close button could not be clicked ({type(exc).__name__})."}


def verify_case(case_number, timeout=60):
    window = None
    try:
        if not re.fullmatch(r"\d+", str(case_number)):
            raise ValueError("Invalid case number")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            window = powerscribe_window()
            editors = [c for c in window.descendants(control_type="TabItem")
                       if c.element_info.automation_id == "reportEditorScreen" and c.is_visible() and c.is_selected()]
            for editor in editors:
                # Direct Text children exclude case numbers in prior reports or report body.
                headers = [c.window_text() for c in editor.children(control_type="Text") if c.is_visible()]
                if authenticated(window) and any(header_matches(value, case_number) for value in headers):
                    evidence = screenshot(window, "case_loaded")
                    closed = close_verified_case(window, case_number)
                    return {"Result":"PASS", "Details":f"PowerScribe active report opened for the same DocPanel case {case_number}. Report content was not edited or signed." + evidence,
                            "CloseClicked": closed["Clicked"], "CloseDetails": closed["Details"]}
            time.sleep(0.5)
        return {"Result":"FAIL", "Details":f"PowerScribe active report did not show DocPanel case {case_number} within {timeout} seconds; same-case opening was not verified." + screenshot(window, "case_fail")}
    except Exception as exc:
        return {"Result":"FAIL", "Details":f"PowerScribe same-case verification could not complete ({type(exc).__name__})." + screenshot(window, "case_fail")}


def run_worker(action, case_number=None):
    args = [sys.executable, str(Path(__file__).resolve()), "--worker", action]
    if case_number is not None:
        args += ["--case-number", str(case_number)]
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=140 if action == "login" else 100,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if completed.returncode:
            raise RuntimeError("Desktop worker failed")
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        if result.get("Result") not in ("PASS", "FAIL") or not isinstance(result.get("Details"), str):
            raise ValueError("Invalid worker result")
        return result
    except Exception as exc:
        return {"Result":"FAIL", "Details":f"PowerScribe automation process failed ({type(exc).__name__}); screenshot unavailable. Ensure pywinauto and Pillow are installed and the app is open."}


def record_case_check(results, case_number, login_result):
    from excel_report import record_result
    outcome = run_worker("verify", case_number) if login_result["Result"] == "PASS" else login_result
    record_result(results, "PowerScribe", "Same case opens in PowerScribe",
                  result=outcome["Result"], details=outcome["Details"])
    print(f"PowerScribe: {outcome['Result']} - same-case check for {case_number}.", flush=True)
    if "CloseClicked" in outcome:
        status = "clicked" if outcome["CloseClicked"] else "not clicked"
        print(f"PowerScribe report Close: {status} - {outcome['CloseDetails']}", flush=True)
    return outcome["Result"] == "PASS"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=("login", "verify"), required=True)
    parser.add_argument("--case-number")
    args = parser.parse_args()
    result = ensure_login() if args.worker == "login" else verify_case(args.case_number)
    print(json.dumps(result))
