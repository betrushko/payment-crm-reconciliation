import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = BASE_DIR / "config.json"
FALLBACK_CONFIG = BASE_DIR / "config.example.json"


def load_config(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(FALLBACK_CONFIG.read_text(encoding="utf-8"))


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing required input: {path}")
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def normalize_orderid(series: pd.Series) -> pd.Series:
    cleaned = series.fillna("").astype(str).str.strip()
    return cleaned.mask(cleaned.eq(""))


def last_order_segment(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip().rsplit("-", 1)[-1]


def is_valid_paymenthub_transaction_alias(orderid: object, transaction_id: object) -> bool:
    if pd.isna(orderid) or pd.isna(transaction_id):
        return False
    order_text = str(orderid).strip()
    transaction_text = str(transaction_id).strip()
    if not order_text or not transaction_text:
        return False
    if not transaction_text.startswith("DEMO-FH"):
        return False
    return last_order_segment(order_text) == last_order_segment(transaction_text)


RETRY_SUFFIX_RE = re.compile(r"^(?P<base>.+)-(?P<retry>\d{1,4})$")


def split_retry_auth(value: object) -> tuple[object, int]:
    """
    Split Salesforce Auth__c into the PaymentHub base orderid and retry number.

    PaymentHub orderids already end in a long numeric id, for example:
      DEMO-FH-RD-20260101000000-1000001

    Salesforce retries append a short suffix:
      DEMO-FH-RD-20260101000000-1000001-2

    The trailing long PaymentHub id must stay part of the base orderid.
    """
    if pd.isna(value):
        return (pd.NA, 0)
    text = str(value).strip()
    if not text:
        return (pd.NA, 0)
    match = RETRY_SUFFIX_RE.match(text)
    if not match:
        return (text, 0)

    base = match.group("base")
    retry = int(match.group("retry"))
    previous_segment = base.rsplit("-", 1)[-1]
    if previous_segment.isdigit() and len(previous_segment) >= 5:
        return (base, retry)
    return (text, 0)


def to_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.date


def to_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def contains_test_marker(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.contains("test", case=False, na=False)


def load_reviewed_orderids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    reviewed = pd.read_csv(path, dtype=str, keep_default_na=False)
    if "orderid" not in reviewed.columns:
        return set()
    return set(normalize_orderid(reviewed["orderid"]).dropna())


def save_reviewed_orderids(path: Path, orderids: pd.Series) -> None:
    existing = load_reviewed_orderids(path)
    combined = sorted(existing.union(set(normalize_orderid(orderids).dropna())))
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"orderid": combined}).to_csv(path, index=False, encoding="utf-8-sig")


def prepare_paymenthub(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["orderid"] = normalize_orderid(out["orderid"])
    out["orderid_base"] = out["orderid"]
    if "transaction_id" in out.columns:
        out["transaction_id_can_match_salesforce"] = out.apply(
            lambda row: is_valid_paymenthub_transaction_alias(row["orderid"], row["transaction_id"]),
            axis=1,
        )
        out["transaction_id_base"] = normalize_orderid(out["transaction_id"]).map(lambda value: split_retry_auth(value)[0] if pd.notna(value) else pd.NA)
        out.loc[~out["transaction_id_can_match_salesforce"], "transaction_id_base"] = pd.NA
    else:
        out["transaction_id_can_match_salesforce"] = False
        out["transaction_id_base"] = pd.NA
    out["completed_date"] = to_date(out["completed_date"])
    if "activity_date" in out.columns:
        out["activity_date"] = to_date(out["activity_date"])
    else:
        out["activity_date"] = out["completed_date"]
    out["donation_amount"] = to_number(out["donation_amount"])
    out = out[out["orderid"].notna()].copy()
    out["is_paymenthub_collected"] = (
        out["order_status"].fillna("").eq("Completed")
        & out["payment_state"].fillna("").eq("Completed")
    )
    out["is_paymenthub_refunded"] = (
        out["order_status"].fillna("").eq("Completed")
        & out["payment_state"].fillna("").eq("Refunded")
    )
    out["paymenthub_charge_class"] = "not_collected"
    out.loc[out["is_paymenthub_refunded"], "paymenthub_charge_class"] = "refunded"
    out.loc[out["is_paymenthub_collected"], "paymenthub_charge_class"] = "collected"
    out = out.sort_values(["activity_date", "completed_date", "completed_time", "orderid"])
    out = out.drop_duplicates("orderid", keep="last")
    return out


def prepare_salesforce(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out = out.rename(columns={
        "Id": "sf_opportunity_id",
        "Name": "sf_opportunity_name",
        "Auth__c": "sf_auth",
        "CloseDate": "sf_close_date",
        "StageName": "sf_stage_name",
        "Motivo_de_rechazo__c": "sf_motivo_rechazo",
        "Tipo_de_Rechazo__c": "sf_tipo_rechazo",
        "Amount": "sf_amount",
        "CurrencyIsoCode": "sf_currency",
        "CreatedDate": "sf_created_date",
        "LastModifiedDate": "sf_last_modified_date",
        "RecordTypeId": "sf_record_type_id",
        "CampaignId": "sf_campaign_id",
        "Medio_de_Pago__c": "sf_medio_pago",
        "ContactId": "sf_contact_id",
        "npsp__Primary_Contact__c": "sf_primary_contact_id",
        "npe03__Recurring_Donation__c": "sf_recurring_donation_id",
        "npsp__Recurring_Donation_Installment_Number__c": "sf_installment_number",
        "OwnerId": "sf_owner_id",
    })
    out["sf_auth"] = normalize_orderid(out.get("sf_auth", pd.Series(dtype=str)))
    retry_parts = out["sf_auth"].map(split_retry_auth)
    out["sf_auth_base"] = retry_parts.map(lambda item: item[0])
    out["sf_retry_number"] = retry_parts.map(lambda item: item[1])
    out["sf_close_date"] = to_date(out.get("sf_close_date", pd.Series(dtype=str)))
    out["sf_amount"] = to_number(out.get("sf_amount", pd.Series(dtype=str)))
    out["sf_stage_normalized"] = out.get("sf_stage_name", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
    out["sf_motivo_rechazo_normalized"] = (
        out.get("sf_motivo_rechazo", pd.Series(dtype=str))
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )
    out["sf_is_collected"] = out["sf_stage_normalized"].eq("Closed Won")
    out["sf_is_rejected"] = out["sf_stage_normalized"].eq("Rechazada")
    out["sf_is_refund_or_chargeback"] = out["sf_stage_normalized"].isin(["Reembolso", "Contracargo"])
    out["sf_rejection_may_be_chargeback"] = (
        out["sf_stage_normalized"].eq("Rechazada")
        & out["sf_motivo_rechazo_normalized"].isin(["subscription_canceled", "fraudulent"])
    )
    out["sf_closed_won_with_rejection_reason"] = (
        out["sf_stage_normalized"].eq("Closed Won")
        & out["sf_motivo_rechazo_normalized"].ne("")
    )
    out = out[out["sf_auth"].notna()].copy()
    out = out.sort_values(["sf_close_date", "sf_created_date", "sf_auth", "sf_retry_number"])
    return out


def build_salesforce_groups(sf: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        sf.groupby("sf_auth_base", dropna=True)
        .agg(
            sf_group_opportunity_count=("sf_auth", "count"),
            sf_group_collected_count=("sf_is_collected", "sum"),
            sf_group_rejected_count=("sf_is_rejected", "sum"),
            sf_group_refund_chargeback_count=("sf_is_refund_or_chargeback", "sum"),
            sf_group_rejection_chargeback_count=("sf_rejection_may_be_chargeback", "sum"),
            sf_group_closed_won_with_rejection_reason_count=("sf_closed_won_with_rejection_reason", "sum"),
            sf_group_total_amount=("sf_amount", "sum"),
            sf_group_max_retry_number=("sf_retry_number", "max"),
            sf_group_latest_close_date=("sf_close_date", "max"),
        )
        .reset_index()
    )

    collected = (
        sf[sf["sf_is_collected"]]
        .sort_values(["sf_auth_base", "sf_retry_number", "sf_close_date", "sf_created_date"])
        .groupby("sf_auth_base", as_index=False)
        .tail(1)[["sf_auth_base", "sf_auth", "sf_opportunity_id", "sf_stage_name", "sf_amount"]]
        .rename(columns={
            "sf_auth": "sf_latest_collected_auth",
            "sf_opportunity_id": "sf_latest_collected_opportunity_id",
            "sf_stage_name": "sf_latest_collected_stage",
            "sf_amount": "sf_latest_collected_amount",
        })
    )

    latest = (
        sf.sort_values(["sf_auth_base", "sf_retry_number", "sf_close_date", "sf_created_date"])
        .groupby("sf_auth_base", as_index=False)
        .tail(1)[["sf_auth_base", "sf_auth", "sf_opportunity_id", "sf_stage_name", "sf_amount"]]
        .rename(columns={
            "sf_auth": "sf_latest_auth",
            "sf_opportunity_id": "sf_latest_opportunity_id",
            "sf_stage_name": "sf_latest_stage",
            "sf_amount": "sf_latest_amount",
        })
    )

    grouped = grouped.merge(collected, on="sf_auth_base", how="left")
    grouped = grouped.merge(latest, on="sf_auth_base", how="left")
    grouped["sf_group_has_collected"] = grouped["sf_group_collected_count"].gt(0)
    grouped["sf_group_has_only_rejections"] = (
        grouped["sf_group_opportunity_count"].gt(0)
        & grouped["sf_group_collected_count"].eq(0)
        & grouped["sf_group_rejected_count"].gt(0)
    )
    return grouped


def prepare_support(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    out = df.copy()
    out["orderid"] = normalize_orderid(out["orderid"])
    out["orderid_base"] = out["orderid"]
    out = out[out["orderid"].notna()].copy()
    out = out.drop_duplicates("orderid_base", keep="last")
    selected = ["orderid_base"] + [col for col in out.columns if col not in ["orderid", "orderid_base"] and col.startswith(prefix)]
    return out[selected]


def prepare_paymenthub_subscriptions(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["agreement_id"] = normalize_orderid(out["agreement_id"])
    out = out[out["agreement_id"].notna()].copy()
    out = out.drop_duplicates("agreement_id", keep="last")
    return out


def prepare_stripe_balance(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["stripe_balance_source_id"] = normalize_orderid(out["stripe_balance_source_id"])
    out["stripe_balance_amount"] = to_number(out.get("stripe_balance_amount", pd.Series(dtype=str)))
    out = out[out["stripe_balance_source_id"].notna()].copy()
    grouped = (
        out.groupby("stripe_balance_source_id", dropna=True)
        .agg(
            stripe_balance_type_list=("stripe_balance_type", lambda s: "; ".join(sorted(set(str(x) for x in s if str(x).strip())))),
            stripe_balance_status_list=("stripe_balance_status", lambda s: "; ".join(sorted(set(str(x) for x in s if str(x).strip())))),
            stripe_balance_total_amount=("stripe_balance_amount", "sum"),
            stripe_balance_transaction_count=("stripe_balance_transaction_id", "count"),
        )
        .reset_index()
    )
    grouped["stripe_balance_has_dispute"] = grouped["stripe_balance_type_list"].str.contains("dispute", case=False, na=False)
    grouped["stripe_balance_has_refund"] = grouped["stripe_balance_type_list"].str.contains("refund", case=False, na=False)
    return grouped


def prepare_recurring_donations(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[
            "sf_rd_id",
            "sf_rd_name",
            "sf_rd_record_type_id",
            "sf_rd_contact_id",
            "sf_rd_transaction_id_unique",
            "sf_rd_amount",
            "sf_rd_installment_period",
            "sf_rd_open_ended_status",
            "sf_rd_date_established",
            "sf_rd_next_payment_date",
            "sf_rd_campaign_id",
        ])
    out = df.copy().rename(columns={
        "Id": "sf_rd_id",
        "Name": "sf_rd_name",
        "RecordTypeId": "sf_rd_record_type_id",
        "npe03__Contact__c": "sf_rd_contact_id",
        "Transaction_ID_Unique__c": "sf_rd_transaction_id_unique",
        "npe03__Amount__c": "sf_rd_amount",
        "npe03__Installment_Period__c": "sf_rd_installment_period",
        "npe03__Open_Ended_Status__c": "sf_rd_open_ended_status",
        "npe03__Date_Established__c": "sf_rd_date_established",
        "npe03__Next_Payment_Date__c": "sf_rd_next_payment_date",
        "npe03__Recurring_Donation_Campaign__c": "sf_rd_campaign_id",
    })
    out["sf_rd_transaction_id_unique"] = normalize_orderid(out.get("sf_rd_transaction_id_unique", pd.Series(dtype=str)))
    out = out[out["sf_rd_transaction_id_unique"].notna()].copy()
    out = out.drop_duplicates("sf_rd_transaction_id_unique", keep="last")
    return out


def prepare_contacts(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=[
            "sf_email_contact_id",
            "sf_email_contact_name",
            "sf_contact_email",
            "email_unique",
            "sf_contact_record_type_id",
        ])
    out = df.copy().rename(columns={
        "Id": "sf_email_contact_id",
        "Name": "sf_email_contact_name",
        "Email": "sf_contact_email",
        "Email_Unique__c": "email_unique",
        "RecordTypeId": "sf_contact_record_type_id",
    })
    out["email_unique"] = (
        out.get("email_unique", pd.Series(dtype=str))
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
        .mask(lambda s: s.eq(""))
    )
    out = out[out["email_unique"].notna()].copy()
    out = out.drop_duplicates("email_unique", keep="last")
    return out


def build_summary(paymenthub: pd.DataFrame, sf: pd.DataFrame, reconciled: pd.DataFrame, new_critical: pd.DataFrame) -> pd.DataFrame:
    collected_missing_all = reconciled[reconciled["is_collected_missing_in_sf"]]
    collected_missing = reconciled[reconciled["is_load_relevant_collected_missing"]]
    excluded_test = reconciled[reconciled["is_excluded_test_record"]]
    no_sf_group = reconciled[reconciled["is_absent_from_salesforce_group"]]
    not_collected_no_sf = reconciled[
        reconciled["is_paymenthub_not_collected"]
        & reconciled["is_absent_from_salesforce_group"]
    ]
    collected_only_rejected = reconciled[
        reconciled["is_paymenthub_collected"]
        & reconciled["exists_in_salesforce_group"]
        & ~reconciled["sf_group_has_collected"]
        & reconciled["sf_group_has_only_rejections"]
    ]
    charged_missing = collected_missing
    rows = [
        ("paymenthub_orders_2026", len(paymenthub)),
        ("paymenthub_distinct_orderids_2026", paymenthub["orderid"].nunique()),
        ("salesforce_opportunities_with_auth_2026", len(sf)),
        ("salesforce_distinct_auth_2026", sf["sf_auth"].nunique()),
        ("salesforce_distinct_base_orderids_2026", sf["sf_auth_base"].nunique()),
        ("paymenthub_collected_orders", int(paymenthub["is_paymenthub_collected"].sum())),
        ("paymenthub_refunded_orders", int(paymenthub["is_paymenthub_refunded"].sum())),
        ("paymenthub_not_collected_orders", int((~paymenthub["is_paymenthub_collected"] & ~paymenthub["is_paymenthub_refunded"]).sum())),
        ("absent_from_salesforce_group_total", len(no_sf_group)),
        ("absent_from_salesforce_group_amount", no_sf_group["donation_amount"].sum(skipna=True)),
        ("collected_missing_in_salesforce_before_test_exclusion", len(collected_missing_all)),
        ("collected_missing_amount_before_test_exclusion", collected_missing_all["donation_amount"].sum(skipna=True)),
        ("excluded_test_records_from_load", len(excluded_test)),
        ("excluded_test_records_amount", excluded_test["donation_amount"].sum(skipna=True)),
        ("collected_missing_in_salesforce_as_collected", len(collected_missing)),
        ("collected_missing_amount", collected_missing["donation_amount"].sum(skipna=True)),
        ("new_collected_missing_incremental", len(new_critical)),
        ("new_collected_missing_amount", new_critical["donation_amount"].sum(skipna=True)),
        ("collected_with_only_rejected_salesforce_group", len(collected_only_rejected)),
        ("collected_with_only_rejected_amount", collected_only_rejected["donation_amount"].sum(skipna=True)),
        ("not_collected_absent_from_salesforce_group", len(not_collected_no_sf)),
        ("not_collected_absent_amount", not_collected_no_sf["donation_amount"].sum(skipna=True)),
        ("sf_rechazada_subscription_or_fraudulent", int(sf["sf_rejection_may_be_chargeback"].sum())),
        ("sf_closed_won_with_rejection_reason", int(sf["sf_closed_won_with_rejection_reason"].sum())),
        ("reconciled_with_stripe_dispute_signal", int(reconciled["stripe_indicates_dispute"].sum())),
        ("reconciled_with_refund_signal", int((reconciled["stripe_indicates_refund"] | reconciled["is_paymenthub_refunded"]).sum())),
        ("charged_missing_with_regular_rd_match", int(charged_missing["can_assign_regular_rd"].sum())),
        ("charged_missing_with_candidate_contact", int(charged_missing["candidate_contact_id"].notna().sum())),
        ("charged_missing_missing_email", int(charged_missing["gcp_email_normalized"].isna().sum())),
        ("charged_missing_email_without_contact", int((charged_missing["gcp_email_normalized"].notna() & charged_missing["candidate_contact_id"].isna()).sum())),
    ]
    return pd.DataFrame(rows, columns=["metric", "value"])


def select_existing_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    return df[[column for column in columns if column in df.columns]].copy()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--mark-reviewed", action="store_true")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    raw_dir = BASE_DIR / config["raw_dir"]
    outputs_dir = BASE_DIR / config["outputs_dir"]
    processed_dir = BASE_DIR / config["processed_dir"]
    state_path = BASE_DIR / config["state_path"]
    reviewed_path = BASE_DIR / "state" / f"reviewed_orderids_{config['year']}.csv"
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    paymenthub = prepare_paymenthub(read_csv(raw_dir / "paymenthub_orders_latest.csv"))
    paymenthub_subscriptions = prepare_paymenthub_subscriptions(read_csv(raw_dir / "paymenthub_subscriptions_latest.csv"))
    stripe = prepare_support(read_csv(raw_dir / "stripe_payment_intents_latest.csv"), "stripe_")
    stripe_charges = prepare_support(read_csv(raw_dir / "stripe_charges_latest.csv"), "stripe_charge_")
    stripe_balance = prepare_stripe_balance(read_csv(raw_dir / "stripe_balance_transactions_latest.csv"))
    paypal = prepare_support(read_csv(raw_dir / "paypal_transactions_latest.csv"), "paypal_")
    sf = prepare_salesforce(read_csv(raw_dir / "salesforce_opportunities_latest.csv"))
    sf_rds = prepare_recurring_donations(read_csv(raw_dir / "salesforce_recurring_donations_regular_latest.csv"))
    sf_contacts = prepare_contacts(read_csv(raw_dir / "salesforce_contacts_email_unique_latest.csv"))
    sf_groups = build_salesforce_groups(sf)

    reviewed_orderids = load_reviewed_orderids(reviewed_path)

    reconciled = paymenthub.merge(
        sf_groups,
        left_on="orderid_base",
        right_on="sf_auth_base",
        how="left",
        indicator="sf_group_match",
    )
    reconciled["salesforce_match_method"] = "none"
    reconciled.loc[reconciled["sf_group_match"].eq("both"), "salesforce_match_method"] = "auth_exact"

    transaction_sf_groups = sf_groups.add_prefix("transaction_")
    reconciled = reconciled.merge(
        transaction_sf_groups,
        left_on="transaction_id_base",
        right_on="transaction_sf_auth_base",
        how="left",
    )
    transaction_match = (
        reconciled["sf_group_match"].eq("left_only")
        & reconciled["transaction_id_can_match_salesforce"]
        & reconciled["transaction_sf_auth_base"].notna()
    )
    for column in sf_groups.columns:
        transaction_column = f"transaction_{column}"
        if transaction_column not in reconciled.columns:
            continue
        reconciled.loc[transaction_match, column] = reconciled.loc[transaction_match, transaction_column]
    reconciled.loc[transaction_match, "sf_group_match"] = "both"
    reconciled.loc[transaction_match, "salesforce_match_method"] = "paymenthub_transaction_id"
    reconciled = reconciled.drop(columns=[column for column in reconciled.columns if column.startswith("transaction_sf_")])

    reconciled = (
        reconciled
        .merge(paymenthub_subscriptions, on="agreement_id", how="left")
        .merge(sf_rds, left_on="agreement_id", right_on="sf_rd_transaction_id_unique", how="left")
        .merge(stripe, on="orderid_base", how="left")
        .merge(stripe_charges, on="orderid_base", how="left")
        .merge(paypal, on="orderid_base", how="left")
    )

    reconciled = reconciled.merge(
        stripe_balance.add_prefix("charge_"),
        left_on="stripe_charge_id",
        right_on="charge_stripe_balance_source_id",
        how="left",
    ).merge(
        stripe_balance.add_prefix("dispute_"),
        left_on="stripe_charge_dispute_id",
        right_on="dispute_stripe_balance_source_id",
        how="left",
    )

    reconciled["gcp_email_normalized"] = (
        reconciled["email"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )
    reconciled.loc[reconciled["gcp_email_normalized"].eq(""), "gcp_email_normalized"] = (
        reconciled.get("paymenthub_subscription_email", pd.Series(index=reconciled.index, dtype=str))
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )
    reconciled.loc[reconciled["gcp_email_normalized"].eq(""), "gcp_email_normalized"] = (
        reconciled.get("paypal_email", pd.Series(index=reconciled.index, dtype=str))
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )
    reconciled["gcp_email_normalized"] = reconciled["gcp_email_normalized"].mask(reconciled["gcp_email_normalized"].eq(""))
    reconciled = reconciled.merge(sf_contacts, left_on="gcp_email_normalized", right_on="email_unique", how="left")

    reconciled["exists_in_salesforce_group"] = reconciled["sf_group_match"].eq("both")
    reconciled["is_absent_from_salesforce_group"] = ~reconciled["exists_in_salesforce_group"]
    reconciled["sf_group_has_collected"] = reconciled["sf_group_has_collected"].fillna(False).astype(bool)
    reconciled["sf_group_has_only_rejections"] = reconciled["sf_group_has_only_rejections"].fillna(False).astype(bool)
    reconciled["sf_group_opportunity_count"] = reconciled["sf_group_opportunity_count"].fillna(0).astype(int)
    reconciled["sf_group_collected_count"] = reconciled["sf_group_collected_count"].fillna(0).astype(int)
    reconciled["sf_group_rejected_count"] = reconciled["sf_group_rejected_count"].fillna(0).astype(int)
    reconciled["sf_group_rejection_chargeback_count"] = reconciled["sf_group_rejection_chargeback_count"].fillna(0).astype(int)
    reconciled["sf_group_closed_won_with_rejection_reason_count"] = reconciled["sf_group_closed_won_with_rejection_reason_count"].fillna(0).astype(int)
    reconciled["sf_group_max_retry_number"] = reconciled["sf_group_max_retry_number"].fillna(0).astype(int)
    reconciled["agreement_matches_regular_rd"] = reconciled["sf_rd_id"].notna()
    reconciled["can_assign_regular_rd"] = reconciled["agreement_matches_regular_rd"]
    reconciled["candidate_opportunity_record_type_id"] = pd.NA
    reconciled.loc[reconciled["can_assign_regular_rd"], "candidate_opportunity_record_type_id"] = "0121N000001fs9UQAQ"
    reconciled["candidate_contact_id"] = reconciled["sf_rd_contact_id"].fillna(reconciled["sf_email_contact_id"])
    reconciled["candidate_contact_source"] = "none"
    reconciled.loc[reconciled["sf_email_contact_id"].notna(), "candidate_contact_source"] = "contact_email_unique"
    reconciled.loc[reconciled["sf_rd_contact_id"].notna(), "candidate_contact_source"] = "recurring_donation"
    reconciled["contact_assignment_status"] = "no_email_no_contact"
    reconciled.loc[reconciled["gcp_email_normalized"].notna() & reconciled["sf_email_contact_id"].isna(), "contact_assignment_status"] = "email_found_contact_not_found"
    reconciled.loc[reconciled["sf_email_contact_id"].notna(), "contact_assignment_status"] = "email_matched_contact"
    reconciled.loc[reconciled["sf_rd_contact_id"].notna(), "contact_assignment_status"] = "recurring_donation_contact"
    reconciled["stripe_indicates_dispute"] = (
        reconciled.get("stripe_charge_disputed", pd.Series(index=reconciled.index)).fillna("").astype(str).str.lower().isin(["true", "1"])
        | reconciled.get("charge_stripe_balance_has_dispute", pd.Series(index=reconciled.index)).fillna(False).astype(bool)
        | reconciled.get("dispute_stripe_balance_has_dispute", pd.Series(index=reconciled.index)).fillna(False).astype(bool)
        | reconciled.get("stripe_charge_dispute_id", pd.Series(index=reconciled.index)).fillna("").astype(str).str.strip().ne("")
    )
    reconciled["stripe_indicates_refund"] = (
        to_number(reconciled.get("stripe_charge_amount_refunded", pd.Series(index=reconciled.index))).fillna(0).gt(0)
        | reconciled.get("charge_stripe_balance_has_refund", pd.Series(index=reconciled.index)).fillna(False).astype(bool)
    )
    reconciled["gcp_chargeback_or_refund_signal"] = "none"
    reconciled.loc[reconciled["stripe_indicates_refund"], "gcp_chargeback_or_refund_signal"] = "stripe_refund"
    reconciled.loc[reconciled["stripe_indicates_dispute"], "gcp_chargeback_or_refund_signal"] = "stripe_dispute_chargeback"
    reconciled.loc[reconciled["is_paymenthub_refunded"], "gcp_chargeback_or_refund_signal"] = "paymenthub_refunded"
    reconciled["is_paymenthub_not_collected"] = ~reconciled["is_paymenthub_collected"] & ~reconciled["is_paymenthub_refunded"]
    reconciled["is_collected_missing_in_sf"] = (
        reconciled["is_paymenthub_collected"]
        & ~reconciled["sf_group_has_collected"]
    )
    test_fields = ["campaign_id", "adjusted_campaign_id", "market_original"]
    reconciled["has_test_marker"] = False
    for test_field in test_fields:
        if test_field in reconciled.columns:
            reconciled["has_test_marker"] = reconciled["has_test_marker"] | contains_test_marker(reconciled[test_field])
    reconciled["is_excluded_test_record"] = reconciled["is_collected_missing_in_sf"] & reconciled["has_test_marker"]
    reconciled["load_exclusion_reason"] = ""
    reconciled.loc[reconciled["is_excluded_test_record"], "load_exclusion_reason"] = "test campaign or market marker"
    reconciled["is_load_relevant_collected_missing"] = (
        reconciled["is_collected_missing_in_sf"]
        & ~reconciled["is_excluded_test_record"]
    )
    reconciled["reconciliation_category"] = "covered_by_salesforce_collected"
    reconciled.loc[reconciled["is_paymenthub_refunded"], "reconciliation_category"] = "paymenthub_refunded"
    reconciled.loc[
        reconciled["is_paymenthub_not_collected"] & reconciled["is_absent_from_salesforce_group"],
        "reconciliation_category",
    ] = "not_collected_absent_from_salesforce"
    reconciled.loc[
        reconciled["is_paymenthub_not_collected"] & reconciled["exists_in_salesforce_group"],
        "reconciliation_category",
    ] = "not_collected_has_salesforce_group"
    reconciled.loc[
        reconciled["is_paymenthub_collected"] & reconciled["is_absent_from_salesforce_group"],
        "reconciliation_category",
    ] = "collected_absent_from_salesforce"
    reconciled.loc[
        reconciled["is_paymenthub_collected"] & reconciled["exists_in_salesforce_group"] & ~reconciled["sf_group_has_collected"],
        "reconciliation_category",
    ] = "collected_not_collected_in_salesforce"
    reconciled.loc[
        reconciled["is_paymenthub_collected"] & reconciled["sf_group_has_only_rejections"],
        "reconciliation_category",
    ] = "collected_has_only_rejected_salesforce_group"

    reconciled["exists_in_stripe"] = reconciled.get("stripe_payment_intent_id", pd.Series(index=reconciled.index)).notna()
    reconciled["exists_in_paypal"] = reconciled.get("paypal_transaction_id", pd.Series(index=reconciled.index)).notna()
    reconciled["support_source"] = "paymenthub_only"
    reconciled.loc[reconciled["exists_in_stripe"], "support_source"] = "stripe"
    reconciled.loc[reconciled.get("stripe_charge_id", pd.Series(index=reconciled.index)).notna(), "support_source"] = "stripe_charge"
    reconciled.loc[reconciled["exists_in_paypal"], "support_source"] = "paypal"
    reconciled.loc[(reconciled["exists_in_stripe"] | reconciled.get("stripe_charge_id", pd.Series(index=reconciled.index)).notna()) & reconciled["exists_in_paypal"], "support_source"] = "stripe_and_paypal"

    missing_any = reconciled[reconciled["is_absent_from_salesforce_group"]].copy()
    excluded_test_records = reconciled[reconciled["is_excluded_test_record"]].copy()
    charged_missing = reconciled[reconciled["is_load_relevant_collected_missing"]].copy()
    charged_missing["already_reviewed_in_prior_run"] = charged_missing["orderid"].isin(reviewed_orderids)
    new_charged_missing = charged_missing[~charged_missing["already_reviewed_in_prior_run"]].copy()

    sf_without_paymenthub = sf_groups[~sf_groups["sf_auth_base"].isin(set(paymenthub["orderid_base"]))].copy()
    sf_typing_cases = (
        sf[
            sf["sf_motivo_rechazo_normalized"].isin(["subscription_canceled", "fraudulent"])
            | sf["sf_closed_won_with_rejection_reason"]
        ]
        .merge(paymenthub, left_on="sf_auth_base", right_on="orderid_base", how="left")
        .merge(paymenthub_subscriptions, on="agreement_id", how="left")
        .merge(stripe_charges, left_on="sf_auth_base", right_on="orderid_base", how="left", suffixes=("", "_stripe_case"))
        .merge(sf_rds, left_on="agreement_id", right_on="sf_rd_transaction_id_unique", how="left")
    )
    sf_typing_cases["typing_case"] = "other_rejection_reason"
    sf_typing_cases.loc[sf_typing_cases["sf_rejection_may_be_chargeback"], "typing_case"] = "rechazada_reason_maps_to_chargeback"
    sf_typing_cases.loc[sf_typing_cases["sf_closed_won_with_rejection_reason"], "typing_case"] = "closed_won_with_rejection_reason"
    sf_typing_cases["stripe_indicates_dispute"] = (
        sf_typing_cases.get("stripe_charge_disputed", pd.Series(index=sf_typing_cases.index)).fillna("").astype(str).str.lower().isin(["true", "1"])
        | sf_typing_cases.get("stripe_charge_dispute_id", pd.Series(index=sf_typing_cases.index)).fillna("").astype(str).str.strip().ne("")
    )
    sf_typing_cases["stripe_indicates_refund"] = (
        to_number(sf_typing_cases.get("stripe_charge_amount_refunded", pd.Series(index=sf_typing_cases.index))).fillna(0).gt(0)
    )
    sf_typing_cases["gcp_chargeback_or_refund_signal"] = "none"
    sf_typing_cases.loc[sf_typing_cases["stripe_indicates_refund"], "gcp_chargeback_or_refund_signal"] = "stripe_refund"
    sf_typing_cases.loc[sf_typing_cases["stripe_indicates_dispute"], "gcp_chargeback_or_refund_signal"] = "stripe_dispute_chargeback"
    sf_typing_cases.loc[sf_typing_cases.get("payment_state", pd.Series(index=sf_typing_cases.index)).fillna("").eq("Refunded"), "gcp_chargeback_or_refund_signal"] = "paymenthub_refunded"
    auto_load_candidates = charged_missing.copy()
    auto_load_candidates["recommended_action"] = "review_before_insert"
    auto_load_candidates.loc[
        auto_load_candidates["can_assign_regular_rd"] & auto_load_candidates["candidate_contact_id"].notna(),
        "recommended_action",
    ] = "ready_for_review_regular_rd_insert"
    auto_load_candidates.loc[
        auto_load_candidates["gcp_email_normalized"].isna() & auto_load_candidates["candidate_contact_id"].isna(),
        "recommended_action",
    ] = "manual_review_missing_email"
    auto_load_candidates.loc[auto_load_candidates["gcp_email_normalized"].notna() & auto_load_candidates["candidate_contact_id"].isna(), "recommended_action"] = "manual_review_email_without_contact"
    summary = build_summary(paymenthub, sf, reconciled, new_charged_missing)

    outputs_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    output_path = outputs_dir / f"missing_salesforce_mexico_{config['year']}_{run_id}.xlsx"
    latest_output_path = outputs_dir / f"missing_salesforce_mexico_{config['year']}_latest.xlsx"
    processed_path = processed_dir / f"reconciled_orders_{config['year']}_{run_id}.csv"

    reconciled.to_csv(processed_path, index=False, encoding="utf-8-sig")
    reconciled_review = select_existing_columns(reconciled, [
        "orderid",
        "orderid_base",
        "transaction_id",
        "transaction_id_base",
        "transaction_id_can_match_salesforce",
        "agreement_id",
        "activity_date",
        "completed_date",
        "completed_time",
        "donation_amount",
        "currency",
        "order_status",
        "payment_state",
        "agreement_status",
        "paymenthub_charge_class",
        "reconciliation_category",
        "has_test_marker",
        "is_excluded_test_record",
        "load_exclusion_reason",
        "salesforce_match_method",
        "support_source",
        "gcp_chargeback_or_refund_signal",
        "stripe_indicates_dispute",
        "stripe_indicates_refund",
        "stripe_charge_disputed",
        "stripe_charge_dispute_id",
        "stripe_charge_status",
        "stripe_charge_amount_refunded",
        "sf_group_opportunity_count",
        "sf_group_collected_count",
        "sf_group_rejected_count",
        "sf_group_rejection_chargeback_count",
        "sf_group_closed_won_with_rejection_reason_count",
        "sf_latest_auth",
        "sf_latest_opportunity_id",
        "sf_latest_stage",
        "sf_latest_amount",
        "sf_latest_collected_auth",
        "sf_latest_collected_opportunity_id",
        "sf_latest_collected_stage",
        "agreement_matches_regular_rd",
        "sf_rd_id",
        "sf_rd_name",
        "sf_rd_contact_id",
        "sf_rd_transaction_id_unique",
        "candidate_contact_id",
        "candidate_contact_source",
        "candidate_opportunity_record_type_id",
        "contact_assignment_status",
        "gcp_email_normalized",
        "sf_email_contact_id",
        "sf_email_contact_name",
    ])

    sheets = {
        "summary": summary,
        "new_charged_missing": new_charged_missing,
        "charged_missing_all": charged_missing,
        "auto_load_candidates": auto_load_candidates,
        "excluded_test_records": excluded_test_records,
        "sf_chargeback_typing_cases": sf_typing_cases,
        "absent_from_sf_group": missing_any,
        "reconciled_review": reconciled_review,
        "sf_without_paymenthub_window": sf_without_paymenthub,
    }

    if config.get("include_audit_source_sheets", False):
        sheets.update({
            "sf_orderid_groups": sf_groups,
        "source_paymenthub": paymenthub,
        "source_paymenthub_subscriptions": paymenthub_subscriptions,
        "source_salesforce": sf,
        "source_sf_regular_rds": sf_rds,
        "source_sf_contacts_email": sf_contacts,
        "source_stripe": stripe,
        "source_stripe_charges": stripe_charges,
        "stripe_balance_by_source": stripe_balance,
        "source_paypal": paypal,
        })

    written_outputs = []
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for sheet_name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    written_outputs.append(str(output_path.relative_to(BASE_DIR)))

    try:
        shutil.copyfile(output_path, latest_output_path)
        written_outputs.append(str(latest_output_path.relative_to(BASE_DIR)))
    except PermissionError:
        print(f"Skipped locked workbook: {latest_output_path}")

    if args.mark_reviewed:
        save_reviewed_orderids(reviewed_path, charged_missing["orderid"])

    max_completed_date = paymenthub["activity_date"].max()
    state = {
        "last_successful_run_at": datetime.now(timezone.utc).isoformat(),
        "last_paymenthub_completed_date": max_completed_date.isoformat() if pd.notna(max_completed_date) else None,
        "last_output": str(output_path.relative_to(BASE_DIR)),
        "written_outputs": written_outputs,
        "last_processed": str(processed_path.relative_to(BASE_DIR)),
        "last_missing_count": int(len(charged_missing)),
        "last_new_missing_count": int(len(new_charged_missing)),
        "reviewed_ledger": str(reviewed_path.relative_to(BASE_DIR)),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print(f"Reconciliation complete: {output_path}")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
