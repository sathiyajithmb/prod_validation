# DocPanel Automation - Amazon WorkSpaces Setup

This guide is for Windows Amazon WorkSpaces. Run all commands in Command Prompt (CMD).

## 1. Project location

Use this folder:

```text
D:\Docpanel\daily-prod-validation
```

Open CMD and run:

```cmd
cd /d D:\Docpanel\daily-prod-validation
git checkout automation/docpanel-validation
```

If the project is already on this branch, no checkout is needed.

## 2. Install Python packages (first setup only)

Install Python 3.13 if it is not available. Then run:

```cmd
py -3.13 --version
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe -m pip check
```

If the environment is already installed and pip reports `No broken requirements found`, continue to the next step.

## 3. Add passwords in .env

```cmd
if not exist .env copy .env.example .env
notepad .env
```

Fill all six entries with the correct passwords and save the file:

```dotenv
DOCPANEL_PASSWORD='your UAT password'
PROD_DOCPANEL_PASSWORD='your production password'
DB_PASSWORD='your Report Pending database password'
POWERSCRIBE_PASSWORD='your PowerScribe password'
DB_FAILURES_PASSWORD='your Report Failures database password'
ZENDESK_PASSWORD='your Zendesk sign-in password'
```

The text above is an example, not working passwords. The selected DocPanel profile password and other service passwords are required by the configuration loader, even when running only one check. The unused DocPanel profile password may be empty.

Use `DB_PASSWORD`, not `DB_PASS`. Do not upload `.env` to Bitbucket or send passwords in reports.

## 4. Update utill.py

```cmd
notepad utill.py
```

| Setting | What to check or change |
| --- | --- |
| `BASE_URL` | Keep `https://uat.docpanel.com` for UAT testing. |
| `DOCPANEL_EMAIL` | Set the correct UAT login email. |
| `DB_CONFIG` | Set the Report Pending DB host, port and username. Password is read from `.env`. |
| `DB_REPORT_PENDING_DATABASE` | Use `r4r_marketplace`. |
| `DB_FAILURES_CONFIG` | Set the separate Report Failures DB host, port and username. |
| `DB_REPORT_FAILURES_DATABASE` | Use `messageStorage`. |
| `POWERSCRIBE_USERNAME` | Set the PowerScribe login username. |
| `POWERSCRIBE_APP_PATH` | If using the executable, set the path installed on this WorkSpace. Do not copy another PC's user-specific path. |
| `POWERSCRIBE_DEPLOYMENT_URL` | The script can use this ClickOnce URL when no valid executable path is set. Confirm that the application can launch manually. |
| `ZENDESK_URL`, `ZENDESK_USERNAME` | Check the tickets view URL and sign-in email. |

The repository name does not select Production. `BASE_URL` and the active `DOCPANEL_EMAIL` / `DOCPANEL_PASSWORD` settings control the login. Adding `PROD_BASE_URL` alone does not switch the script to Production.

### Database connection example

If your approved DB tunnel is running on this WorkSpace at port 33061, use:

```python
DB_CONFIG = {
    'host': 'localhost',
    'port': 33061,
    'user': 'd0cpanel',
    'password': _password("DB_PASSWORD"),
    'charset': 'utf8mb4',
    'connect_timeout': 10,
    'read_timeout': 120,
    'write_timeout': 30,
}
```

`d0cpanel` contains the digit zero after `d`. Confirm the username and password with the DB administrator. These settings still require working database access; copying them does not create a tunnel.

For approved direct RDS access, use the RDS hostname and port `3306` instead. Direct access must be allowed by the AWS network configuration.

Report Failures uses its own `DB_FAILURES_CONFIG`. Configure and test that connection separately. Its local tunnel port has been `33069`; confirm the actual setup on this WorkSpace.

Connection tests for local tunnels:

```cmd
powershell -NoProfile -Command "Test-NetConnection localhost -Port 33061"
powershell -NoProfile -Command "Test-NetConnection localhost -Port 33069"
```

`TcpTestSucceeded: True` confirms the port is reachable. It does not confirm the DB username or password. The DB checks use SELECT queries; a read-only account needs SELECT access to the required tables. Case selection also needs the Report Pending database connection to look up imaging-center IDs.

Report Failures uses the supplied query with the fixed cutoff `dateReceived > '2026-09-28'`. Keep `DB_REPORT_FAILURES_FROM = "2026-09-28"` in `utill.py`; do not change it to today.

## 5. Run the script

Always run from the project folder using the virtual environment Python.

Start with the Admin Dashboard check:

```cmd
.venv\Scripts\python.exe test\open_docpanel_cases.py --check cases
```

Full run:

```cmd
.venv\Scripts\python.exe test\open_docpanel_cases.py
```

Run one check by adding `--check` and a value from this table:

| Value | What it runs |
| --- | --- |
| `jobs` | Jobs Dashboard |
| `case_assignment` | Case Assignment tabs |
| `legacy_operations` | Legacy Operations |
| `case_details` | Case Details and Eunity |
| `powerscribe` | PowerScribe only |
| `report_pending` | Report Pending only |
| `report_failures` | Report Failures only |
| `database` | Both database reports |
| `zendesk` | Zendesk unassigned tickets |

Example:

```cmd
.venv\Scripts\python.exe test\open_docpanel_cases.py --check report_pending
```

PowerScribe and Multi-Monitor are currently excluded from the default full run. PowerScribe requires PowerScribe One and the DocPanel Nuance proxy on the WorkSpace. Run it explicitly with `--check powerscribe`.

Keep the WorkSpaces session open and unlocked during desktop checks. Complete DocPanel MFA or Google verification when prompted.

## 6. Report and screenshot locations

```text
D:\Docpanel\daily-prod-validation\reports
D:\Docpanel\daily-prod-validation\screenshots
```

Excel and HTML reports are saved automatically. Open the HTML file in a browser to view details and embedded screenshots.

No report-folder path change is needed in the code. The script finds `utill.py` and `.env` at the project root. If you install the project in another folder, change the CMD `cd` path; keep the same folder structure.

## 7. Common errors

| Error | Action |
| --- | --- |
| `No module named playwright` | Use `.venv\Scripts\python.exe`, not a different system Python. |
| Missing password setting | Fill the named entry in the root `.env` file. |
| MySQL error `2003` | Check the DB endpoint, tunnel and network access. |
| MySQL error `1045` | DB login was rejected. Check username, password and the account's allowed source host with the DBA. |
| `Report Failures: FAIL - 42 rows` | The query completed and found 42 failure records. This is not a connection error. |
| Login waits at MFA | Complete the verification in the browser or authentication app. |
| PowerScribe login fields not found | Open the captured screenshot and check the app screen. Try manual login, keep the app open, then rerun. |

Saving a report does not mean every check passed. Read the PASS, FAIL and Not checked results inside the report.

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
