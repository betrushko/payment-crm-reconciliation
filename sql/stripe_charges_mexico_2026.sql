-- Stripe charge-level evidence for paid/refunded/disputed orderids.
DECLARE target_year INT64 DEFAULT @target_year;
DECLARE start_date DATE DEFAULT @start_date;

SELECT
  TRIM(COALESCE(OrderNumber, DonationNumber)) AS orderid,
  Id AS stripe_charge_id,
  PaymentIntentId AS stripe_charge_payment_intent_id,
  BalanceTransactionId AS stripe_charge_balance_transaction_id,
  OrderNumber AS stripe_charge_order_number,
  DonationNumber AS stripe_charge_donation_number,
  Created AS stripe_charge_created_at,
  DATE(Created) AS stripe_charge_created_date,
  SAFE_DIVIDE(Amount, 100) AS stripe_charge_amount,
  SAFE_DIVIDE(AmountCaptured, 100) AS stripe_charge_amount_captured,
  SAFE_DIVIDE(AmountRefunded, 100) AS stripe_charge_amount_refunded,
  Currency AS stripe_charge_currency,
  Paid AS stripe_charge_paid,
  Captured AS stripe_charge_captured,
  Disputed AS stripe_charge_disputed,
  Dispute AS stripe_charge_dispute_id,
  Status AS stripe_charge_status,
  FailureCode AS stripe_charge_failure_code,
  FailureMessage AS stripe_charge_failure_message,
  OutcomeType AS stripe_charge_outcome_type,
  OutcomeReason AS stripe_charge_outcome_reason,
  RiskLevel AS stripe_charge_risk_level,
  RiskScore AS stripe_charge_risk_score,
  SellerMessage AS stripe_charge_seller_message,
  CardBrand AS stripe_charge_card_brand,
  PaymentType AS stripe_charge_payment_type
FROM `example-project.src_stripe_mexico.Charges`
WHERE COALESCE(OrderNumber, DonationNumber) IS NOT NULL
  AND DATE(Created) IS NOT NULL
  AND EXTRACT(YEAR FROM DATE(Created)) = target_year
  AND DATE(Created) >= start_date
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY TRIM(COALESCE(OrderNumber, DonationNumber))
  ORDER BY Created DESC, Id DESC
) = 1
ORDER BY stripe_charge_created_at, orderid;
