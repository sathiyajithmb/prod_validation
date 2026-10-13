"""Self-contained visual theme for offline DocPanel reports."""
# Allow direct execution from test/ while keeping configuration at project root.
import sys as _sys
from pathlib import Path as _Path
for _directory in (_Path(__file__).resolve().parent, _Path(__file__).resolve().parent.parent):
    if str(_directory) not in _sys.path:
        _sys.path.insert(0, str(_directory))

CSS = """
:root{--ink:#183b48;--muted:#607680;--line:#dfe9ec;--teal:#087d79}
body{background:#f0f5f6;color:var(--ink);font:14px/1.6 'Segoe UI',Arial,sans-serif}
header{position:relative;padding:38px max(28px,calc((100vw - 1400px)/2));background:linear-gradient(115deg,#103e4d,#126b72);border-bottom:4px solid #4dd7b2}
header:before{content:'DOCPANEL / QUALITY MONITORING';display:block;color:#96e5d8;letter-spacing:2px;font-size:11px;font-weight:700;margin-bottom:12px}
h1{font-size:30px;letter-spacing:-.7px;margin:0 0 8px;font-weight:650}header p{font-size:13px;color:#d1e6e9;max-width:750px}
.download{background:#ffffff18;border:1px solid #ffffff60;border-radius:9px;padding:9px 16px;text-decoration:none;font-weight:600}.download:hover{background:#ffffff30}
main{max-width:1456px;margin:auto;padding:28px}.cards{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px;margin-bottom:30px}
.card{min-width:0;padding:20px 22px;border:1px solid var(--line);border-radius:14px;box-shadow:0 3px 10px #173e4904;border-top:3px solid #51798a}
.card strong{font-size:32px;line-height:1.2;font-weight:650;letter-spacing:-1px}.card span{display:block;margin-top:8px;font-size:11px;font-weight:650;letter-spacing:.8px;color:var(--muted)}
.card:nth-child(2){border-top-color:#199365}.card:nth-child(2) strong{color:#14764f}.card:nth-child(3){border-top-color:#dc5353}.card:nth-child(3) strong{color:#bc3434}.card:nth-child(4){border-top-color:#4f83ce}.card:nth-child(5){border-top-color:#d6a139}
.report-tools{display:flex;align-items:center;justify-content:space-between;gap:18px;margin:0 0 16px}.report-tools h2{margin:0;font-size:20px}.report-tools p{margin:3px 0 0;color:var(--muted);font-size:12px}.report-tools nav{display:flex;gap:8px}.report-tools button{cursor:pointer;background:white;color:var(--ink);border:1px solid #c8dadd;border-radius:8px;padding:8px 12px;font:600 12px 'Segoe UI',sans-serif}.report-tools button:hover{background:#e6f3f1}
main>section{background:white;border:1px solid var(--line);border-left:4px solid #bdcfd4;border-radius:12px;margin:12px 0;padding:20px 22px;box-shadow:0 3px 10px #173e4903;min-width:0}
main>section:has(>h2>.fail){border-left-color:#d85858}main>section:has(>h2>.pass){border-left-color:#22946e}main>section:has(>h2>.pending){border-left-color:#d5a243}
main>section>h2{display:flex;align-items:center;flex-wrap:wrap;gap:12px;margin:0;font-size:16px;font-weight:650;line-height:1.5}
.pass-toggle{order:1;margin-left:auto;border:1px solid transparent;padding:7px 14px;font:650 12px 'Segoe UI',sans-serif;min-height:34px;transition:box-shadow .15s}.pass-toggle:hover{box-shadow:0 0 0 3px #173e4910}.pass-toggle[aria-expanded=true]{box-shadow:0 0 0 2px #163f4920}
.screenshot-link{order:0;margin-left:0;border:1px solid #d8e5e8;background:#f5f9fa;color:#246574;border-radius:7px;padding:5px 10px;text-decoration:none;font-size:12px}.screenshot-link:hover{background:#e5f3f1}
.jobs-indication,.loading-indication{order:2;flex-basis:100%;margin:0;font-size:13px;font-weight:400;line-height:1.7}
.section-details{margin-top:20px;padding-top:18px;border-top:1px solid #edf1f3}.section-details h3{font-size:14px;margin:22px 0 12px}.table-wrap{border-color:var(--line);border-radius:9px;max-width:100%}th{background:#f0f6f7;color:#40616f;font-size:11px;letter-spacing:.35px}td{font-size:13px;padding:13px 14px}tr:hover td{background:#f0f8f7}tr:last-child td{border-bottom:0}
.pass{background:#e4f5ec;color:#14764f}.fail{background:#fce9e7;color:#b73232}.pending{background:#fff3d8;color:#886013}.badge{font-size:12px}.neutral{background:#edf2fb;color:#426694}
dialog{border:1px solid #d6e5e7;box-shadow:0 24px 90px #082d4955;border-radius:16px}dialog::backdrop{background:#102b3ac2}.dialog-close-bar{padding-bottom:8px}.close{color:#42616d}.screenshot{border-radius:8px}figcaption{margin:12px 0 8px}footer{padding:18px 0;font-size:12px;color:#6c828a;border-top:1px solid var(--line);margin-top:26px}
button:focus-visible,a:focus-visible{outline:3px solid #178a9a;outline-offset:3px}
@media(max-width:760px){header{padding:28px 18px}h1{font-size:25px}main{padding:18px 14px}.cards{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.card:first-child{grid-column:1/-1}.card{padding:15px 18px}.card strong{font-size:28px}main>section{padding:16px}.report-tools{align-items:flex-start;flex-direction:column}main>section>h2{font-size:15px}.pass-toggle{margin-left:0}.screenshot-link{font-size:11px}.jobs-indication,.loading-indication{font-size:12px}dialog{width:96vw;padding:14px}pre{min-width:0}}
@media print{header{background:white;color:black;border-color:#aaa}header:before,header p{color:#444}.report-tools nav,.download{display:none}main>section{box-shadow:none;break-inside:avoid}.section-details[hidden]{display:block}.cards{grid-template-columns:repeat(5,1fr)}}
"""
TOOLBAR = """<div class="report-tools"><div><h2>Check overview</h2><p>Select a status to view details, or open the captured screenshots.</p></div><nav aria-label="Report details"><button type="button" data-report-expand="true">Expand all</button><button type="button" data-report-expand="false">Collapse all</button></nav></div>"""
SCRIPT = """document.querySelectorAll('[data-report-expand]').forEach(control => {
control.addEventListener('click', () => {
const expanded = control.dataset.reportExpand === 'true';
document.querySelectorAll('[data-pass-toggle]').forEach(button => {
const panel = document.getElementById(button.getAttribute('aria-controls'));
if(panel){panel.hidden = !expanded;button.setAttribute('aria-expanded', String(expanded));}
});
});
});"""


def apply_theme(document):
    if 'id="report-theme"' in document:
        return document
    document = document.replace('</head>', '<style id="report-theme">' + CSS + '</style></head>', 1)
    document = document.replace('</div><section>', '</div>' + TOOLBAR + '<section>', 1)
    document = document.replace(' ? Counts and check outcomes', ' &middot; Counts and check outcomes')
    return document.replace('</body>', '<script>' + SCRIPT + '</script></body>', 1)
