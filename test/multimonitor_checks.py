"""Verify the actual Electron desktop webview, not a Chrome popup."""
# Allow direct execution from test/ while keeping configuration at project root.
import sys as _sys
from pathlib import Path as _Path
for _directory in (_Path(__file__).resolve().parent, _Path(__file__).resolve().parent.parent):
    if str(_directory) not in _sys.path:
        _sys.path.insert(0, str(_directory))

import base64
from datetime import datetime
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from playwright.sync_api import expect
from excel_report import record_result

# Electron webviews are not exposed as normal Playwright Pages. Use their own
# local DevTools target through the app's already-installed ws module.
CDP = r"""async ({url, method, params}) => {
    const WS = require('ws');
    return await new Promise((resolve, reject) => {
        const ws = new WS(url);
        const timer = setTimeout(() => { ws.close(); reject(Error('Desktop command timeout')); }, 15000);
        const finish = (error, value) => {clearTimeout(timer); ws.close(); error ? reject(error) : resolve(value);};
        ws.on('error', error => finish(error));
        ws.on('open', () => ws.send(JSON.stringify({id:1, method, params})));
        ws.on('message', data => {
            const message = JSON.parse(data.toString());
            if (message.id === 1) finish(message.error ? Error(message.error.message) : null, message.result);
        });
    });
}"""


class DesktopStartupError(RuntimeError):
    """Safe diagnostic message for automatic desktop startup."""


def ensure_multimonitor_started(endpoint):
    import socket
    import subprocess
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise DesktopStartupError("Desktop debugging endpoint must be local HTTP.")
    port = parsed.port or 80
    def ready():
        try:
            with socket.create_connection((parsed.hostname, port), timeout=0.5):
                return True
        except OSError:
            return False
    if ready():
        print(f"Multi-Monitor: reusing local debugging port {port}.", flush=True)
        return
    script = Path(__file__).resolve().parent / "start_multimonitor_debug.ps1"
    try:
        launched = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(script), "-Port", str(port)],
            capture_output=True, text=True, timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DesktopStartupError(f"Automatic Multi-Monitor launcher failed ({type(exc).__name__}).") from exc
    if launched.returncode:
        if "Close Docpanel WebViewer normally" in (launched.stderr or ""):
            raise DesktopStartupError("Multi-Monitor is already running without debugging. Close the app normally and rerun; it will start automatically.")
        raise DesktopStartupError("Automatic Multi-Monitor startup failed. Check that Docpanel WebViewer is installed at the path in start_multimonitor_debug.ps1.")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if ready():
            print(f"Multi-Monitor: automatically started; debugging port {port} is ready.", flush=True)
            return
        time.sleep(0.25)
    raise DesktopStartupError(f"Multi-Monitor started but debugging port {port} did not become ready within 20 seconds.")


def connect_multimonitor(playwright, endpoint):
    parsed = urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Desktop debugging endpoint must be local HTTP.")
    app = playwright.chromium.connect_over_cdp(endpoint, timeout=10_000)
    shell = next((p for c in app.contexts for p in c.pages
                  if p.url.startswith("file:") and "electron-tabs.html" in p.url), None)
    if shell is None:
        raise RuntimeError("Endpoint is not the Docpanel desktop app.")
    return {"browser":app, "shell":shell, "endpoint":endpoint.rstrip("/")}


def targets(app):
    # The local endpoint supplies webSocketDebuggerUrl for each webview.
    return app["shell"].evaluate("""endpoint => new Promise((resolve,reject) => {
        require('http').get(endpoint + '/json/list', response => {
            let data=''; response.on('data', chunk => data+=chunk);
            response.on('end', () => {try {resolve(JSON.parse(data));} catch(e) {reject(e);}});
        }).on('error', reject);
    })""", app["endpoint"])


def snapshot_app(app):
    return {(t["id"], t["url"]) for t in targets(app)}


def command(app, target, method, params=None):
    return app["shell"].evaluate(CDP, {
        "url":target["webSocketDebuggerUrl"], "method":method, "params":params or {}})


def evaluate(app, target, expression, context_id=None):
    params = {"expression":expression, "returnByValue":True, "awaitPromise":True}
    if context_id is not None:
        params["contextId"] = context_id
    response = command(app, target, "Runtime.evaluate", params)
    if "exceptionDetails" in response:
        raise RuntimeError("Desktop DOM evaluation failed.")
    return response.get("result", {}).get("value")


def poll(app, callback, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = callback()
        if value:
            return value
        app["shell"].wait_for_timeout(300)
    raise RuntimeError("Desktop verification timed out.")


def screenshot(app, target, label):
    data = command(app, target, "Page.captureScreenshot", {"format":"png"})["data"]
    folder = Path(__file__).resolve().parent.parent / "screenshots"
    folder.mkdir(exist_ok=True)
    path = folder / f"multi_monitor_{label}_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
    path.write_bytes(base64.b64decode(data))
    return path


def failure_screenshot(app, target=None):
    """Capture failure evidence even when the requested case tab was not found."""
    if target is not None:
        try:
            path = screenshot(app, target, "fail")
            return f" Screenshot: {path}"
        except Exception:
            pass
    try:
        folder = Path(__file__).resolve().parent.parent / "screenshots"
        folder.mkdir(exist_ok=True)
        path = folder / f"multi_monitor_app_fail_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
        app["shell"].screenshot(path=str(path), full_page=True, animations="disabled", timeout=10_000)
        return (" Captured the Multi-Monitor app window for diagnosis; "
                "the requested case and image load are not verified. "
                f"Screenshot: {path}")
    except Exception as exc:
        return f" Screenshot unavailable: desktop app capture failed ({type(exc).__name__})."


THUMBNAIL = r"""(async () => {
    const visible = el => el && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0;
    if (![...document.querySelectorAll('*')].some(e => e.children.length===0 &&
        e.textContent.trim()==='Primary Study' && visible(e))) return false;
    const el = [...document.querySelectorAll('#primSeries img, #primSeries #thumImg0, #caseImageId0 > #thumImg0')]
        .find(visible);
    if (!el) return false;
    if (el instanceof HTMLImageElement) {
        try {await el.decode(); return el.naturalWidth>0;} catch {return false;}
    }
    const match=getComputedStyle(el).backgroundImage.match(/^url\(["']?(.*?)["']?\)$/);
    if (!match) return false;
    const image=new Image(); image.src=match[1];
    try {await image.decode(); return image.naturalWidth>0;} catch {return false;}
})()"""


def image_rendered(app, target):
    tree = command(app, target, "Page.getFrameTree")["frameTree"]
    frames = []
    def visit(node):
        frames.append(node["frame"])
        for child in node.get("childFrames", []):
            visit(child)
    visit(tree)
    frame = next((f for f in frames if "/e/viewer" in f.get("url","")), None)
    if frame is None:
        return False
    # Named isolated world lets the DOM inspect the cross-origin eUnity frame.
    context = command(app, target, "Page.createIsolatedWorld",
                      {"frameId":frame["id"], "worldName":"docpanel-image-check"})["executionContextId"]
    rect = evaluate(app,target, """(() => {
        const el=document.querySelector('canvas#mdiStage');
        if(!el) return null;
        const r=el.getBoundingClientRect();
        return {x:r.x,y:r.y,width:r.width,height:r.height};
    })()""",context)
    if not rect or rect["width"] < 100 or rect["height"] < 100:
        return False
    offset = evaluate(app,target,"""(() => {
        const el=document.querySelector('iframe#iframe');
        if(!el) return null;
        const r=el.getBoundingClientRect(); return {x:r.x+scrollX,y:r.y+scrollY};
    })()""")
    if not offset:
        return False
    rect.update(x=rect["x"]+offset["x"], y=rect["y"]+offset["y"], scale=1)
    data = command(app,target,"Page.captureScreenshot",{"format":"png","clip":rect})["data"]
    return app["shell"].evaluate("""async data => {
        const image=new Image(); image.src='data:image/png;base64,'+data; await image.decode();
        const canvas=document.createElement('canvas'); canvas.width=160;canvas.height=160;
        const ctx=canvas.getContext('2d');
        ctx.drawImage(image,image.width*.25,image.height*.25,image.width*.5,image.height*.5,0,0,160,160);
        const pixels=ctx.getImageData(0,0,160,160).data;
        const levels=new Set(); let mid=0,sum=0,squares=0;
        for(let i=0;i<pixels.length;i+=4) {
            const v=Math.round((pixels[i]+pixels[i+1]+pixels[i+2])/3);
            levels.add(v);sum+=v;squares+=v*v;if(v>20&&v<235)mid++;
        }
        const n=pixels.length/4;
        return levels.size>=32&&mid/n>=.15&&Math.sqrt(Math.max(0,squares/n-(sum/n)**2))>=12;
    }""",data)


def check_multimonitor(app, before, case_number, results, verify_images=None, debug=False):
    target = None
    stage = "Same case opened in desktop app"
    def matched_target():
        for candidate in targets(app):
            if (candidate["id"],candidate["url"]) in before or "reportTemplatePage.html" not in candidate["url"]:
                continue
            matched = evaluate(app,candidate,
                r"(() => {const m=document.body.innerText.match(/Case\s*No\s*:?\s*(\d+)/i);return m ? m[1] : null;})()")
            if matched == case_number:
                return candidate
        return None
    def passed(label):
        path=screenshot(app,target,label)
        record_result(results,"Multi-Monitor",stage,result="PASS",
                      details=f"Case {case_number}: {stage}. Screenshot: {path}")
        print(f"Multi-Monitor: PASS - {stage}",flush=True)
    try:
        target=poll(app,matched_target)
        passed("case")
        stage="Primary Study and thumbnails loaded"
        poll(app,lambda: evaluate(app,target,THUMBNAIL))
        passed("thumbnails")
        stage="Viewer image rendered"
        evaluate(app,target,r"""(() => {
            const candidates=[...document.querySelectorAll('#primSeries img, #primSeries #thumImg0, #caseImageId0 > #thumImg0')];
            const el=candidates.find(e=>e.getBoundingClientRect().width>0);
            if(el) el.click();
            const show=[...document.querySelectorAll('button')].find(e=>e.textContent.trim()==='Show Viewer' && e.getBoundingClientRect().width>0);
            if(show) show.click();
        })()""")
        poll(app,lambda:image_rendered(app,target))
        poll(app,lambda:evaluate(app,target,
            "![...document.querySelectorAll('#spinner')].some(e=>e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0)"))
        passed("viewer")
        return True
    except Exception as exc:
        details=f"Case {case_number}: {stage} was not verified ({type(exc).__name__})."
        details += failure_screenshot(app, target)
        record_result(results,"Multi-Monitor",stage,result="FAIL",details=details)
        print(f"Multi-Monitor: FAIL - {stage} ({type(exc).__name__})",flush=True)
        return False
