import argparse
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import pandas as pd
from simple_salesforce import Salesforce


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = BASE_DIR / "config.json"
FALLBACK_CONFIG = BASE_DIR / "config.example.json"
SOQL_FILES = {
    "salesforce_opportunities": BASE_DIR / "sql" / "salesforce_opportunities_2026.soql",
}
OPPORTUNITY_COLUMNS = [
    "Id",
    "Name",
    "Auth__c",
    "CloseDate",
    "StageName",
    "Motivo_de_rechazo__c",
    "Tipo_de_Rechazo__c",
    "Amount",
    "CurrencyIsoCode",
    "CreatedDate",
    "LastModifiedDate",
    "RecordTypeId",
    "CampaignId",
    "Medio_de_Pago__c",
    "ContactId",
    "npsp__Primary_Contact__c",
    "npe03__Recurring_Donation__c",
    "npsp__Recurring_Donation_Installment_Number__c",
    "OwnerId",
]
RD_COLUMNS = [
    "Id",
    "Name",
    "RecordTypeId",
    "npe03__Contact__c",
    "Transaction_ID_Unique__c",
    "npe03__Amount__c",
    "npe03__Installment_Period__c",
    "npe03__Open_Ended_Status__c",
    "npe03__Date_Established__c",
    "npe03__Next_Payment_Date__c",
    "npe03__Recurring_Donation_Campaign__c",
    "CreatedDate",
    "LastModifiedDate",
]
CONTACT_COLUMNS = [
    "Id",
    "Name",
    "Email",
    "Email_Unique__c",
    "RecordTypeId",
    "CreatedDate",
    "LastModifiedDate",
]
ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def load_config(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(FALLBACK_CONFIG.read_text(encoding="utf-8"))


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def parse_json_from_cli(stdout: str) -> dict:
    clean = strip_ansi(stdout).strip()
    start = clean.find("{")
    end = clean.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"Could not find JSON in Salesforce CLI output: {clean[:300]!r}")
    return json.loads(clean[start:end + 1])


def salesforce_from_cli(config: dict) -> Salesforce:
    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    env["FORCE_COLOR"] = "0"
    env["SF_DISABLE_TELEMETRY"] = "true"
    env["SF_TEMP_SHOW_SECRETS"] = "true"

    cmd = [
        config.get("sf_cmd", "sf"),
        "org",
        "display",
        "--target-org",
        config["sf_alias"],
        "--json",
        "--verbose",
    ]
    result = subprocess.run(
        cmd,
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    payload = None
    try:
        payload = parse_json_from_cli(result.stdout)
    except Exception:
        payload = None

    if result.returncode != 0 and not (payload and payload.get("status") == 0):
        raise RuntimeError(
            "Salesforce CLI could not provide an access token. Re-authenticate with:\n"
            "sf org login web --alias DEMO_SANDBOX --instance-url https://example.my.salesforce.com"
            f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        )

    if payload is None:
        payload = parse_json_from_cli(result.stdout)
    org_info = payload["result"]
    access_token = org_info.get("accessToken")
    if not access_token or access_token.startswith("[REDACTED]"):
        raise RuntimeError(
            "Salesforce CLI did not return a usable access token. "
            "The CLI may have removed SF_TEMP_SHOW_SECRETS support."
        )

    return Salesforce(
        instance_url=org_info["instanceUrl"],
        session_id=access_token,
    )


def query_all_to_dataframe(sf: Salesforce, soql: str) -> pd.DataFrame:
    result = sf.query_all(soql)
    records = result.get("records", [])
    df = pd.DataFrame(records)
    if "attributes" in df.columns:
        df = df.drop(columns=["attributes"])
    return df


def read_latest_csv(raw_dir: Path, filename: str) -> pd.DataFrame:
    path = raw_dir / filename
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def clean_value(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def clean_email(value: object) -> str | None:
    text = clean_value(value)
    if not text:
        return None
    return text.lower()


def soql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def chunks(values: list[str], size: int):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def collect_gcp_scope(raw_dir: Path) -> tuple[list[str], list[str], list[str]]:
    paymenthub_orders = read_latest_csv(raw_dir, "paymenthub_orders_latest.csv")
    paymenthub_subscriptions = read_latest_csv(raw_dir, "paymenthub_subscriptions_latest.csv")
    paypal = read_latest_csv(raw_dir, "paypal_transactions_latest.csv")

    orderid_values = []
    for column in ["orderid", "transaction_id"]:
        if column in paymenthub_orders.columns:
            orderid_values.extend(paymenthub_orders[column].map(clean_value).dropna().tolist())

    agreement_values = []
    for df, column in [
        (paymenthub_orders, "agreement_id"),
        (paymenthub_subscriptions, "agreement_id"),
    ]:
        if column in df.columns:
            agreement_values.extend(df[column].map(clean_value).dropna().tolist())

    email_values = []
    for df, column in [
        (paymenthub_orders, "email"),
        (paymenthub_subscriptions, "paymenthub_subscription_email"),
        (paypal, "paypal_email"),
    ]:
        if column in df.columns:
            email_values.extend(df[column].map(clean_email).dropna().tolist())

    orderids = sorted(set(orderid_values))
    agreement_ids = sorted(set(agreement_values))
    emails = sorted(set(email_values))
    return orderids, agreement_ids, emails


def query_scoped_opportunities(sf: Salesforce, base_df: pd.DataFrame, orderids: list[str], batch_size: int) -> pd.DataFrame:
    frames = [base_df]
    select_clause = ", ".join(OPPORTUNITY_COLUMNS)
    for batch in chunks(orderids, batch_size):
        values = ", ".join(soql_literal(value) for value in batch)
        soql = f"""
SELECT {select_clause}
FROM Opportunity
WHERE Auth__c IN ({values})
ORDER BY CloseDate, CreatedDate
"""
        frames.append(query_all_to_dataframe(sf, soql))

    out = pd.concat(frames, ignore_index=True)
    if "Id" in out.columns:
        out = out.drop_duplicates("Id", keep="last")
    return out


def query_scoped_recurring_donations(sf: Salesforce, agreement_ids: list[str], batch_size: int) -> pd.DataFrame:
    if not agreement_ids:
        return pd.DataFrame(columns=RD_COLUMNS)

    frames = []
    select_clause = ", ".join(RD_COLUMNS)
    for batch in chunks(agreement_ids, batch_size):
        values = ", ".join(soql_literal(value) for value in batch)
        soql = f"""
SELECT {select_clause}
FROM npe03__Recurring_Donation__c
WHERE Transaction_ID_Unique__c IN ({values})
ORDER BY CreatedDate
"""
        frames.append(query_all_to_dataframe(sf, soql))

    if not frames:
        return pd.DataFrame(columns=RD_COLUMNS)
    return pd.concat(frames, ignore_index=True).drop_duplicates()


def query_scoped_contacts(sf: Salesforce, emails: list[str], batch_size: int) -> pd.DataFrame:
    if not emails:
        return pd.DataFrame(columns=CONTACT_COLUMNS)

    frames = []
    select_clause = ", ".join(CONTACT_COLUMNS)
    for batch in chunks(emails, batch_size):
        values = ", ".join(soql_literal(value) for value in batch)
        soql = f"""
SELECT {select_clause}
FROM Contact
WHERE Email_Unique__c IN ({values})
ORDER BY CreatedDate
"""
        frames.append(query_all_to_dataframe(sf, soql))

    if not frames:
        return pd.DataFrame(columns=CONTACT_COLUMNS)
    return pd.concat(frames, ignore_index=True).drop_duplicates()


def write_extract(raw_dir: Path, run_id: str, extract_name: str, df: pd.DataFrame) -> dict:
    timestamped_path = raw_dir / f"{extract_name}_{run_id}.csv"
    latest_path = raw_dir / f"{extract_name}_latest.csv"
    df.to_csv(timestamped_path, index=False, encoding="utf-8-sig")
    df.to_csv(latest_path, index=False, encoding="utf-8-sig")
    return {
        "name": extract_name,
        "rows": int(len(df)),
        "timestamped_path": str(timestamped_path.relative_to(BASE_DIR)),
        "latest_path": str(latest_path.relative_to(BASE_DIR)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    config = load_config(Path(args.config))
    raw_dir = BASE_DIR / config["raw_dir"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    sf = salesforce_from_cli(config)
    batch_size = int(config.get("salesforce_scope_batch_size", 400))
    orderids, agreement_ids, emails = collect_gcp_scope(raw_dir)

    manifest = {
        "run_id": run_id,
        "target_year": config["year"],
        "sf_alias": config["sf_alias"],
        "scoped_orderids": len(orderids),
        "scoped_agreement_ids": len(agreement_ids),
        "scoped_emails": len(emails),
        "extracts": [],
    }

    for extract_name, soql_path in SOQL_FILES.items():
        print(f"Running Salesforce extract: {extract_name}")
        df = query_all_to_dataframe(sf, soql_path.read_text(encoding="utf-8"))
        if extract_name == "salesforce_opportunities":
            print(f"Running scoped Salesforce supplement: {extract_name} ({len(orderids)} GCP orderids)")
            df = query_scoped_opportunities(sf, df, orderids, batch_size)
        manifest["extracts"].append(write_extract(raw_dir, run_id, extract_name, df))

    print(f"Running scoped Salesforce extract: salesforce_recurring_donations_regular ({len(agreement_ids)} Agreement IDs)")
    rd_df = query_scoped_recurring_donations(sf, agreement_ids, batch_size)
    manifest["extracts"].append(write_extract(raw_dir, run_id, "salesforce_recurring_donations_regular", rd_df))

    print(f"Running scoped Salesforce extract: salesforce_contacts_email_unique ({len(emails)} emails)")
    contacts_df = query_scoped_contacts(sf, emails, batch_size)
    manifest["extracts"].append(write_extract(raw_dir, run_id, "salesforce_contacts_email_unique", contacts_df))

    manifest_path = raw_dir / f"salesforce_manifest_{run_id}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Salesforce simple-salesforce extract complete: {manifest_path}")


if __name__ == "__main__":
    main()
