import argparse
import json
import os
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from google.cloud import bigquery
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = BASE_DIR / "config.json"
FALLBACK_CONFIG = BASE_DIR / "config.example.json"


QUERIES = {
    "paymenthub_orders": BASE_DIR / "sql" / "paymenthub_mexico_2026.sql",
    "paymenthub_subscriptions": BASE_DIR / "sql" / "paymenthub_subscriptions_mexico.sql",
    "stripe_payment_intents": BASE_DIR / "sql" / "stripe_mexico_2026.sql",
    "stripe_charges": BASE_DIR / "sql" / "stripe_charges_mexico_2026.sql",
    "stripe_balance_transactions": BASE_DIR / "sql" / "stripe_balance_transactions_mexico_2026.sql",
    "paypal_transactions": BASE_DIR / "sql" / "paypal_mexico_2026.sql",
    "source_summary": BASE_DIR / "sql" / "bigquery_source_summary.sql",
}


def load_config(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(FALLBACK_CONFIG.read_text(encoding="utf-8"))


def load_state(config: dict) -> dict:
    state_path = BASE_DIR / config["state_path"]
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text(encoding="utf-8"))


def calculate_start_date(config: dict, full_refresh: bool) -> date:
    year_start = date(int(config["year"]), 1, 1)
    if full_refresh:
        return year_start

    state = load_state(config)
    last_date = state.get("last_paymenthub_completed_date")
    if not last_date:
        return year_start

    lookback_days = int(config.get("lookback_days", 14))
    return max(year_start, date.fromisoformat(last_date) - timedelta(days=lookback_days))


def resolve_gcp_key(config: dict) -> Path | None:
    configured = config.get("gcp_key_path") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if configured:
        path = Path(configured).expanduser()
        if path.exists():
            return path

    return None


def build_client(config: dict) -> bigquery.Client:
    if config.get("gcp_auth_mode") == "gcloud_access_token":
        result = subprocess.run(
            ["gcloud.cmd", "auth", "print-access-token"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Could not retrieve gcloud access token.\n"
                f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
            )
        credentials = Credentials(token=result.stdout.strip())
        return bigquery.Client(
            credentials=credentials,
            project=config.get("gcp_job_project") or config["gcp_project"],
        )

    key_path = resolve_gcp_key(config)
    if key_path:
        credentials = service_account.Credentials.from_service_account_file(key_path)
        job_project = config.get("gcp_job_project") or credentials.project_id
        return bigquery.Client(credentials=credentials, project=job_project)
    return bigquery.Client(project=config.get("gcp_job_project") or config["gcp_project"])


def prepare_sql(sql_path: Path) -> str:
    lines = []
    for line in sql_path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("--"):
            continue
        if stripped.startswith("DECLARE target_year") or stripped.startswith("DECLARE start_date"):
            continue
        lines.append(line)
    sql = "\n".join(lines).strip()
    sql = sql.replace("target_year", "@target_year")
    sql = sql.replace("start_date", "@start_date")
    return sql


def run_query(client: bigquery.Client, config: dict, sql_path: Path, start_date: date):
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("target_year", "INT64", int(config["year"])),
            bigquery.ScalarQueryParameter("start_date", "DATE", start_date),
        ]
    )
    query_job = client.query(prepare_sql(sql_path), job_config=job_config)
    return query_job.to_dataframe(create_bqstorage_client=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--full-refresh", action="store_true")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    raw_dir = BASE_DIR / config["raw_dir"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    start_date = calculate_start_date(config, args.full_refresh)
    client = build_client(config)

    manifest = {
        "run_id": run_id,
        "target_year": config["year"],
        "start_date": start_date.isoformat(),
        "full_refresh": bool(args.full_refresh),
        "extracts": [],
    }

    for extract_name, sql_path in QUERIES.items():
        print(f"Running BigQuery SDK extract: {extract_name}")
        df = run_query(client, config, sql_path, start_date)
        timestamped_path = raw_dir / f"{extract_name}_{run_id}.csv"
        latest_path = raw_dir / f"{extract_name}_latest.csv"
        df.to_csv(timestamped_path, index=False, encoding="utf-8-sig")
        df.to_csv(latest_path, index=False, encoding="utf-8-sig")
        manifest["extracts"].append({
            "name": extract_name,
            "rows": int(len(df)),
            "timestamped_path": str(timestamped_path.relative_to(BASE_DIR)),
            "latest_path": str(latest_path.relative_to(BASE_DIR)),
        })

    manifest_path = raw_dir / f"bigquery_manifest_{run_id}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"BigQuery SDK extracts complete: {manifest_path}")


if __name__ == "__main__":
    main()
