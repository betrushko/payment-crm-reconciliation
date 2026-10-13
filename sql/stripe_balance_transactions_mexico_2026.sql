-- Stripe balance transaction evidence. Type commonly identifies charge/refund/dispute flows.
DECLARE target_year INT64 DEFAULT @target_year;
DECLARE start_date DATE DEFAULT @start_date;

SELECT
  SourceId AS stripe_balance_source_id,
  Id AS stripe_balance_transaction_id,
  CreatedDate AS stripe_balance_created_at,
  DATE(CreatedDate) AS stripe_balance_created_date,
  Type AS stripe_balance_type,
  Status AS stripe_balance_status,
  Description AS stripe_balance_description,
  SAFE_DIVIDE(Amount, 100) AS stripe_balance_amount,
  SAFE_DIVIDE(Fee, 100) AS stripe_balance_fee,
  SAFE_DIVIDE(Net, 100) AS stripe_balance_net,
  Currency AS stripe_balance_currency
FROM `example-project.src_stripe_mexico.BalanceTransactions`
WHERE DATE(CreatedDate) IS NOT NULL
  AND EXTRACT(YEAR FROM DATE(CreatedDate)) = target_year
  AND DATE(CreatedDate) >= start_date
ORDER BY stripe_balance_created_at, stripe_balance_transaction_id;
