-- PayPal support source for Mexico.
-- invoice_id is normalized as orderid and used to validate PaymentHub coverage.
DECLARE target_year INT64 DEFAULT @target_year;
DECLARE start_date DATE DEFAULT @start_date;

SELECT
  TRIM(invoice_id) AS orderid,
  uuid AS paypal_uuid,
  transaction_id AS paypal_transaction_id,
  paypal_reference_id,
  paypal_reference_id_type,
  transaction_event_code,
  transaction_initiation_date AS paypal_initiated_at,
  DATE(transaction_initiation_date) AS paypal_initiated_date,
  transaction_updated_date AS paypal_updated_at,
  transaction_amount_value AS paypal_amount,
  transaction_amount_currency_code AS paypal_currency,
  fee_amount_value AS paypal_fee_amount,
  transaction_status AS paypal_status,
  invoice_id AS paypal_invoice_id,
  invoice_number AS paypal_invoice_number,
  account_id AS paypal_account_id,
  email_address AS paypal_email,
  country_code AS paypal_country_code
FROM `example-project.src_paypal_mexico.transactions`
WHERE invoice_id IS NOT NULL
  AND DATE(transaction_initiation_date) IS NOT NULL
  AND EXTRACT(YEAR FROM DATE(transaction_initiation_date)) = target_year
  AND DATE(transaction_initiation_date) >= start_date
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY TRIM(invoice_id)
  ORDER BY transaction_initiation_date DESC, transaction_updated_date DESC, uuid DESC
) = 1
ORDER BY paypal_initiated_at, orderid;
