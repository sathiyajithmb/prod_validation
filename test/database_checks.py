"""Read-only SQL checks for pending reports and report failures."""
# Allow direct execution from test/ while keeping configuration at project root.
import sys as _sys
from pathlib import Path as _Path
for _directory in (_Path(__file__).resolve().parent, _Path(__file__).resolve().parent.parent):
    if str(_directory) not in _sys.path:
        _sys.path.insert(0, str(_directory))

from datetime import date
import utill
from excel_report import record_result

REPORT_PENDING_SQL = """
SELECT
 (SELECT organisation_name FROM users
  WHERE user_id = cases.img_center_id AND enterprise_id = 1 LIMIT 1) AS `Img Center Name`,
 cases.case_number AS `Case`,
 studies.assession_number AS `Accession`,
 studies.patent_id AS `MRN`,
 cases.completed_date AS `Completed Date`,
 cases.img_center_id
FROM cases
LEFT JOIN case_studies ON case_studies.case_id = cases.case_id
LEFT JOIN studies ON case_studies.study_id = studies.study_id
WHERE hl7_status = 'Report Pending'
 AND cases.completed_date > CAST(DATE_SUB(NOW(), INTERVAL 7 DAY) AS DATE)
{excluded_centers}
ORDER BY img_center_id, case_number, cases.completed_date DESC
"""

REPORT_FAILURES_SQL = """
SELECT
 JSON_EXTRACT(oru.oru, '$.Ack') AS Ack,
 oru.AccessionNumber,
 (SELECT COUNT(*) FROM ORU AS oru2
  WHERE oru2.AccessionNumber = oru.AccessionNumber
    AND oru2.ImageCenterName = oru.ImageCenterName) AS times,
 oru.ImageCenterName,
 oru.ImageCenterId,
 oru.PatientMrn,
 oru.dateReceived
FROM ORU AS oru
WHERE LENGTH(JSON_EXTRACT(oru.oru, '$.Ack')) < 5
 AND LENGTH(oru.AccessionNumber) > 5
 AND oru.dateReceived > %s
 AND NOT EXISTS (
  SELECT 1 FROM ORU AS oru2
  WHERE oru2.AccessionNumber = oru.AccessionNumber
    AND oru2.ImageCenterName = oru.ImageCenterName
    AND LENGTH(JSON_EXTRACT(oru2.oru, '$.Ack')) > 5
 )
ORDER BY oru.dateReceived DESC
"""


def build_report_pending_query():
    ids = utill.REPORT_PENDING_EXCLUDED_IMG_CENTER_IDS
    if not isinstance(ids, (list, tuple)) or any(type(value) is not int or value <= 0 for value in ids):
        raise ValueError("REPORT_PENDING_EXCLUDED_IMG_CENTER_IDS must be a list of positive integers.")
    clause = " AND cases.img_center_id NOT IN (" + ", ".join(["%s"] * len(ids)) + ")" if ids else ""
    return REPORT_PENDING_SQL.replace("{excluded_centers}", clause), tuple(ids)


def read_query(config, database, sql, params):
    import pymysql
    connection = pymysql.connect(**config, database=database,
                                 cursorclass=pymysql.cursors.DictCursor)
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            cursor.execute(sql, params)
            columns = [item[0] for item in cursor.description]
            rows = list(cursor.fetchall())
        return columns, rows
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


def report_database_checks(results, only=None):
    if only not in (None, "Report Pending", "Report Failures"):
        raise ValueError("Unknown database report selection")
    base = dict(utill.DB_CONFIG)
    since = utill.DB_REPORT_FAILURES_FROM  # Fixed cutoff, independent of the run date.
    checks = [
        ("Report Pending", base, utill.DB_REPORT_PENDING_DATABASE,
         REPORT_PENDING_SQL, None, "After midnight 7 days ago (database server time)"),
        ("Report Failures", dict(utill.DB_FAILURES_CONFIG), utill.DB_REPORT_FAILURES_DATABASE,
         REPORT_FAILURES_SQL, (since,), f"After {since}"),
    ]
    completed = True
    for name, config, database, sql, params, period in checks:
        if only is not None and name != only:
            continue
        missing = [key for key in ("host", "user") if not config.get(key)]
        if missing:
            record_result(results, name, "Database query", result="SKIP", date_range=period,
                          details="DB connection not configured in utill.py: " + ", ".join(missing))
            print(f"{name}: SKIP - configure DB settings in utill.py.", flush=True)
            completed = False
            continue
        try:
            if name == "Report Pending":
                sql, params = build_report_pending_query()
            else:
                date.fromisoformat(params[0])
            columns, rows = read_query(config, database, sql, params)
            verdict = "FAIL" if len(rows) > 50 else "PASS"
            record_result(results, name, "Query result rows", count=len(rows),
                          result=verdict, date_range=period,
                          details="Query completed; count is returned rows, not distinct cases. "
                                  "PASS: 50 or fewer rows. FAIL: more than 50.")
            results.append({"_kind":"db_table", "Section":name,
                            "columns":columns, "rows":rows})
            print(f"{name}: {verdict} - {len(rows)} rows.", flush=True)
        except Exception as exc:
            # Do not expose credentials, server addresses, or patient rows.
            code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
            reason = f"{type(exc).__name__}" + (f" (MySQL code {code})" if code else "")
            if isinstance(exc, ModuleNotFoundError):
                reason = "Install dependency: python -m pip install PyMySQL"
            record_result(results, name, "Database query", result="FAIL", date_range=period,
                          details=f"Query did not complete: {reason}. No row count reported.")
            print(f"{name}: FAIL - {reason}", flush=True)
            completed = False
    return completed
