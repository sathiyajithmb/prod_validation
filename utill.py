from pathlib import Path
from dotenv import dotenv_values

# Resolve beside this file so running from another directory also works.
# Disable interpolation: passwords containing ${...} must remain literal.
_password_values = dotenv_values(Path(__file__).resolve().with_name(".env"), interpolate=False)


def _password(name):
    value = _password_values.get(name)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Missing password {name} in .env beside utill.py. See .env.example.")
    return value


BASE_URL = "https://uat.docpanel.com"

DOCPANEL_EMAIL = "shripalanivel@gmail.com"
DOCPANEL_PASSWORD = _password("DOCPANEL_PASSWORD")

# Production settings; the active BASE_URL/DOCPANEL_* above remain UAT.
PROD_BASE_URL = "https://app.docpanel.com"
PROD_DOCPANEL_EMAIL = "shripalanivel@gmail.com"
PROD_DOCPANEL_PASSWORD = _password("PROD_DOCPANEL_PASSWORD")


# Database report connection. Fill these locally; no credentials are printed.
DB_CONFIG = {
    'host': 'localhost',
    'port': 33061,
    'user': 'root',
    'password': _password("DB_PASSWORD"),
    'charset': 'utf8mb4',
    'connect_timeout': 10,
    'read_timeout': 120,
    'write_timeout': 30,
}
DB_REPORT_PENDING_DATABASE = "r4r_marketplace"

# Imaging centers excluded from Report Pending. Empty list excludes none.
REPORT_PENDING_EXCLUDED_IMG_CENTER_IDS = [
    2751, 2564, 2611, 107, 2720, 2273, 585, 597, 603, 3645, 2795
]

# Imaging centers excluded from Case Details, Unity and Multi-Monitor sampling.
# Add multiple integer IDs, for example [4215, 1234].
CASE_DETAILS_EXCLUDED_IMG_CENTER_IDS = [4215]  # Peer Review Test IC

# PowerScribe login credentials.
POWERSCRIBE_USERNAME = "mohana.dineshkumar"
POWERSCRIBE_PASSWORD = _password("POWERSCRIBE_PASSWORD")

# Only these imaging centers are eligible for the independent PowerScribe check.
# Add the desired integer IC IDs. Empty means not configured, never all centers.
POWERSCRIBE_IMG_CENTER_IDS = [4110]

# Installed PowerScribe executable used for automatic startup.
# POWERSCRIBE_APP_PATH = 'C:\\Users\\docpanel\\AppData\\Local\\Apps\\2.0\\LETKHCRL.B6C\\XT6VK55C.C15\\nuan...app_fabea4ec5e17cf93_07e7.0003_637fb4e2408ccac6\\Nuance.PSOne.exe'
POWERSCRIBE_APP_PATH = ""
POWERSCRIBE_DEPLOYMENT_URL = "https://pstest.docpanel.com/PSOneReportingClient/Client/Nuance.PSOne.application"
# Separate connection for Report Failures (messageStorage).
DB_FAILURES_CONFIG = {
    "host": "localhost",
    "port": 33069,
    "user": "mohana",
    "password": _password("DB_FAILURES_PASSWORD"),
    "charset": "utf8mb4",
    "connect_timeout": 10,
    "read_timeout": 120,
    "write_timeout": 30,
}
DB_REPORT_FAILURES_DATABASE = "messageStorage"
DB_REPORT_FAILURES_FROM = "2026-09-28"  # Fixed query cutoff; do not replace with the run date.

# Zendesk Unassigned tickets (Google SSO).
ZENDESK_URL = "https://docpanel.zendesk.com/agent/filters/360071009493"
ZENDESK_USERNAME = "mohana@docpanel.com"
ZENDESK_PASSWORD = _password("ZENDESK_PASSWORD")
