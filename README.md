# DocPanel automation reports

Windows automation for DocPanel dashboard checks, case details, Eunity images,
PowerScribe, database reports, and Zendesk unassigned tickets. Results are exported
to Excel and an offline HTML report with embedded screenshots when captured.

## Project layout

```text
Playwright/
  utill.py
  .env
  .env.example
  requirements.txt
  README.md
  test/
    open_docpanel_cases.py
    case_details_checks.py
    powerscribe_case_checks.py
    powerscribe_checks.py
    database_checks.py
    zendesk_checks.py
    multimonitor_checks.py
    excel_report.py
    html_report.py
    report_theme.py
    legacy_metrics.py
    start_multimonitor_debug.ps1
  reports/
  screenshots/
```

Tests remain separate files inside `test/`. Run commands from the project root.
Passwords, report outputs, screenshots and saved login sessions keep their
existing project-root locations.

## Requirements

- Windows with Python 3.13 (the source system uses Python 3.13).
- Access to the configured DocPanel environment and Zendesk account.
- Database access / local tunnels described below.
- PowerScribe One and the DocPanel Nuance proxy installed and configured for
  PowerScribe checks. Confirm manual login and case opening work first.
- An unlocked, interactive Windows desktop for native application automation.
- DocPanel WebViewer only when running Multi-Monitor checks.

Copy the entire `test/` folder plus root `utill.py`, `.env`, and `requirements.txt`. Old `reports`, `screenshots`,
`__pycache__`, `.pytest_cache`, and `.venv` folders are not needed.
Do not transfer `.auth` browser sessions; log in on the new machine.
Passwords are stored in `.env`, not in `utill.py`. Transfer `.env` securely or
create it from `.env.example` on the destination machine.

## Installation (PowerShell)

Open a terminal in the project folder:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pip check
```

No virtual-environment activation or PowerShell execution-policy change is needed
for these Python commands. Requirements pin the versions installed on the source
machine; a fresh installation must still be verified on the destination machine.
Internet access or an approved package mirror is needed to download dependencies
and the Playwright browser.

## Configuration: utill.py and .env

Only passwords are read from `.env` beside `utill.py`. URLs, usernames, ports,
IC lists, and other settings remain in `utill.py`. Existing Python variable names
and database configuration dictionaries are unchanged.

On a new machine, copy `.env.example` to `.env` and fill all six entries:
`DOCPANEL_PASSWORD`, `PROD_DOCPANEL_PASSWORD`, `DB_PASSWORD`,
`DB_FAILURES_PASSWORD`, `POWERSCRIBE_PASSWORD`, and `ZENDESK_PASSWORD`.
The current machine's passwords have already been migrated to `.env`.

Use single-quoted values, for example `DB_PASSWORD='your-password'`.
For a literal single quote inside a quoted password, escape it with a backslash.
Password interpolation is disabled. The file is resolved relative to `utill.py`,
independently of the terminal working directory. Missing or empty values produce
an error naming the setting, without displaying its password. The selected DocPanel profile password and the other service passwords
are required when importing the configuration. Shell environment variables do
not override this file. `.env` files are ignored by Git; `.env.example` contains
only empty placeholders.


Keep the existing variable names and update values for the destination system.
Do not put credentials in this README or report output.

| Setting | Purpose |
| --- | --- |
| `BASE_URL`, `DOCPANEL_EMAIL`, `DOCPANEL_PASSWORD` | DocPanel URL and login |
| `DB_CONFIG`, `DB_REPORT_PENDING_DATABASE` | Report Pending and case imaging-center lookup connection |
| `REPORT_PENDING_EXCLUDED_IMG_CENTER_IDS` | Imaging centers excluded from Report Pending |
| `CASE_DETAILS_EXCLUDED_IMG_CENTER_IDS` | Imaging centers excluded from case-detail sampling; supports multiple IDs |
| `DB_FAILURES_CONFIG`, `DB_REPORT_FAILURES_DATABASE` | Separate Report Failures connection |
| `DB_REPORT_FAILURES_FROM` | Fixed cutoff `"2026-09-28"`; do not change to the run date |
| `POWERSCRIBE_USERNAME`, `POWERSCRIBE_PASSWORD` | PowerScribe login |
| `POWERSCRIBE_IMG_CENTER_IDS` | PowerScribe imaging-center allowlist; currently includes 4110 |
| `POWERSCRIBE_APP_PATH` | Actual PowerScribe executable path on this machine |
| `ZENDESK_URL`, `ZENDESK_USERNAME`, `ZENDESK_PASSWORD` | Zendesk view URL and Google sign-in credentials |

PowerScribe's ClickOnce executable path is machine/user specific and can change
after an application update. Replace the old path with the installed path.
Complete Google verification manually if requested during the first Zendesk login.

### Database connections

| Use | Configured local endpoint | Database |
| --- | --- | --- |
| Report Pending and case IC lookup | `localhost:33061` | `r4r_marketplace` |
| Report Failures | `localhost:33069` | `messageStorage` |

`localhost` means the machine running Python. Copying the project does not create
these connections. Recreate the existing approved database tunnels/VPN setup on
the destination machine, or update the connection configuration to its valid
endpoint. Tunnel credentials and remote forwarding configuration are not supplied
by this project.

```powershell
Test-NetConnection localhost -Port 33061
Test-NetConnection localhost -Port 33069
```

A successful TCP check confirms only that the port is reachable, not database
credentials or query permissions. The queries require read access. Report Pending
uses the database server's midnight seven days ago; Report Failures always uses the configured fixed cutoff `2026-09-28`.

## Run all checks

```powershell
.\.venv\Scripts\python.exe test/open_docpanel_cases.py
```

The main run includes dashboards, Case Assignment, Legacy Operations, case details,
Eunity, database reports, and Zendesk. DocPanel browser checks share
the authenticated session where supported. Zendesk is the last report section.

**Multi-Monitor and PowerScribe are excluded from the full run.** Run PowerScribe
separately with `--check powerscribe` or `python test/powerscribe_case_checks.py`. Case-detail sampling uses up to two random
eligible In Progress cases with Study Status Diagnostic, applying IC exclusions.
Eunity uses those same selected Diagnostic cases. PowerScribe independently
selects a Diagnostic case from its configured IC list within the last two days.
No eligible case is reported as not checked rather than a successful image load.

## Run one group

Use the following command, replacing `jobs` with a value from the table:

```powershell
.\.venv\Scripts\python.exe test/open_docpanel_cases.py --check jobs
```

| Value | Checks |
| --- | --- |
| `cases` | Admin total and Loading counts |
| `jobs` | Jobs Dashboard counts and job details |
| `case_assignment` | Case Assignment tabs |
| `legacy_operations` | Legacy Operations |
| `case_details` | Case Details and Eunity; Multi-Monitor disabled in this runner |
| `powerscribe` | PowerScribe same-case check |
| `report_pending` | Only Report Pending database query |
| `report_failures` | Only Report Failures database query |
| `database` | Both database reports |
| `zendesk` | Zendesk unassigned tickets |
| `all` | Default full run |

Database-only modes do not launch the DocPanel browser.

### Standalone checks and debugging

```powershell
# PowerScribe only
.\.venv\Scripts\python.exe test/powerscribe_case_checks.py

# Optional exact case: replace the example with an eligible current case.
.\.venv\Scripts\python.exe test/powerscribe_case_checks.py --case-number 2062921

# Case Details + Eunity + Multi-Monitor (enabled in this standalone runner)
.\.venv\Scripts\python.exe test/case_details_checks.py

# Zendesk only
.\.venv\Scripts\python.exe test/zendesk_checks.py

# Playwright Inspector / checkpoints
.\.venv\Scripts\python.exe test/open_docpanel_cases.py --check jobs --debug
```

An explicit PowerScribe case must still be In Progress, have Study Status
Diagnostic, fall within the last two days, and belong to the configured
imaging-center allowlist. Selecting a case does not guarantee that
PowerScribe opens it: the result records what was actually verified.

## Multi-Monitor setup (optional)

Install DocPanel WebViewer. Check `$viewerExe` in
`test/start_multimonitor_debug.ps1` matches its installed executable path.
The standalone check attempts automatic startup. If the app is already open
without debugging, close it normally first, then rerun. Existing case tabs are
not forcibly closed by the launcher.

Manual startup when needed:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\test\start_multimonitor_debug.ps1
```

The script starts local debugging on `127.0.0.1:9223`, which lets the automation
inspect the desktop viewer. This is separate from database ports 33061/33069.
It does not enable Multi-Monitor in `open_docpanel_cases.py`.

## Reports and evidence

- `reports/`: timestamped `.xlsx` and `.html` reports.
- `screenshots/`: captured evidence PNGs.
- `.auth/`: locally saved Zendesk session state; treat as credentials.

Open the HTML file in a browser. Section buttons reveal details; screenshot links
open embedded evidence. Keep the matching Excel file beside the HTML for the
Excel download/open link. Failed checks can include diagnostic screenshots;
a screenshot alone is not proof that loading succeeded.

Loading counts above five are marked FAIL. Nonzero Report Pending and Report
Failures query results are marked FAIL with their counts. Jobs use a neutral
View job details control; status counts alone do not measure how long a job has
been stuck. Review the report details rather than treating successful report-file
creation as a successful test run.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Missing Python module | Install requirements using the same `.venv` Python used to run the project |
| Playwright browser executable missing | Run the Chromium installation command above |
| MySQL error 2003 | Check the configured host/port and database tunnel/VPN |
| PowerScribe executable not found | Update `POWERSCRIBE_APP_PATH` |
| PowerScribe opens but case does not | Check Nuance proxy, browser local-network permission, the filtered Active Cases route, and same-case evidence |
| Multi-Monitor connection error | Check app installation/path and local debug port 9223 |
| Google verification prompt | Complete verification in the opened browser |
| No eligible case | Check Today date, In Progress status, IC exclusions/allowlist |
| Excel save fails | Close the output file if Excel has locked it and verify folder write access |

Native app automation requires the desktop to remain available and unlocked.
This project checks case opening and evidence; do not use report editing/signing
as a workaround for a failed check.

## Choose a DocPanel login user

In `utill.py`, set `DOCPANEL_ACTIVE_USER = "user1"` or `"user2"`.
Set each email under `DOCPANEL_USERS`. User1 keeps the existing email and
`DOCPANEL_PASSWORD` entry in `.env`. Fill `DOCPANEL_USER2_PASSWORD` in `.env`
before selecting user2. The unused user's password is not required.
This selects one user per run, not two users in the same run. The run command
and `BASE_URL` stay unchanged. Both accounts must support the existing
OS Supervisor login flow and have access to the checks being run.

```python
DOCPANEL_ACTIVE_USER = "user2"
```

```cmd
.venv\Scripts\python.exe test\open_docpanel_cases.py
```

### Select a user in the run command

```cmd
.venv\Scripts\python.exe test\open_docpanel_cases.py --user user1
.venv\Scripts\python.exe test\open_docpanel_cases.py --user user2
```

The `--user` option overrides the default for this run without editing any file.
It also works with `--check`, for example `--user user2 --check cases`.
Fill the selected profile email in `DOCPANEL_USERS` and its password in `.env`
before running. Without `--user`, `DOCPANEL_ACTIVE_USER` from the environment is
used if set, otherwise the default is user1. This selects a login account, not
an environment: `BASE_URL` still controls UAT or Production.

### Zendesk user1 and user2

Set both Zendesk emails under `ZENDESK_USERS` in `utill.py`.
User1 uses `.env` entry `ZENDESK_PASSWORD`; user2 uses `ZENDESK_USER2_PASSWORD`.
A full run with `--user user2` selects user2 for both DocPanel and Zendesk.
For Zendesk alone, these commands select only the Zendesk profile:

```cmd
.venv\Scripts\python.exe test\zendesk_checks.py --user user1
.venv\Scripts\python.exe test\zendesk_checks.py --user user2
```

Saved Zendesk sessions are separated by profile, email and Zendesk origin.
The old shared `.auth/zendesk.json` is not reused. Each profile may need Google
verification on its first login. The configured Zendesk view URL remains shared.
