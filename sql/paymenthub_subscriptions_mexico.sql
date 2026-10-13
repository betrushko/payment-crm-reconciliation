-- PaymentHub subscription status by Agreement_ID.
SELECT
  TRIM(agreement_id) AS agreement_id,
  uuid AS paymenthub_subscription_uuid,
  import_date AS paymenthub_subscription_import_date,
  first_name AS paymenthub_subscription_first_name,
  last_name AS paymenthub_subscription_last_name,
  email AS paymenthub_subscription_email,
  donation_type AS paymenthub_subscription_donation_type,
  amount AS paymenthub_subscription_amount,
  currency AS paymenthub_subscription_currency,
  frequency AS paymenthub_subscription_frequency,
  monthly_debit_date AS paymenthub_subscription_monthly_debit_date,
  next_debit_date AS paymenthub_subscription_next_debit_date,
  agreement_status AS paymenthub_subscription_agreement_status,
  date_created AS paymenthub_subscription_date_created,
  date_updated AS paymenthub_subscription_date_updated,
  payment_method AS paymenthub_subscription_payment_method,
  payment_gateway AS paymenthub_subscription_payment_gateway,
  payment_id AS paymenthub_subscription_payment_id,
  customer_remote_id AS paymenthub_subscription_customer_remote_id,
  market AS paymenthub_subscription_market,
  market_original AS paymenthub_subscription_market_original,
  pm_original AS paymenthub_subscription_pm_original,
  pm_brand AS paymenthub_subscription_pm_brand
FROM `example-project.src_paymenthub_mexico.paymenthub_subscriptions_mexico`
WHERE agreement_id IS NOT NULL
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY TRIM(agreement_id)
  ORDER BY import_date DESC, date_updated DESC, uuid DESC
) = 1
ORDER BY agreement_id;
