DECLARE target_year INT64 DEFAULT @target_year;
DECLARE start_date DATE DEFAULT @start_date;

SELECT
  'paymenthub' AS source_name,
  COUNT(*) AS row_count,
  COUNT(DISTINCT donation_number) AS orderid_count,
  MIN(completed_date) AS min_date,
  MAX(completed_date) AS max_date
FROM `example-project.src_paymenthub_mexico.paymenthub_orders_mexico`
WHERE completed_date >= start_date
  AND EXTRACT(YEAR FROM completed_date) = target_year
UNION ALL
SELECT
  'stripe',
  COUNT(*),
  COUNT(DISTINCT OrderNumber),
  MIN(DATE(CreatedDate)),
  MAX(DATE(CreatedDate))
FROM `example-project.src_stripe_mexico.PaymentIntents`
WHERE DATE(CreatedDate) >= start_date
  AND EXTRACT(YEAR FROM DATE(CreatedDate)) = target_year
UNION ALL
SELECT
  'paypal',
  COUNT(*),
  COUNT(DISTINCT invoice_id),
  MIN(DATE(transaction_initiation_date)),
  MAX(DATE(transaction_initiation_date))
FROM `example-project.src_paypal_mexico.transactions`
WHERE DATE(transaction_initiation_date) >= start_date
  AND EXTRACT(YEAR FROM DATE(transaction_initiation_date)) = target_year;
