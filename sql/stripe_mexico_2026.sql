-- Stripe support source for Mexico.
-- OrderNumber is normalized as orderid and used to validate PaymentHub coverage.
DECLARE target_year INT64 DEFAULT @target_year;
DECLARE start_date DATE DEFAULT @start_date;

SELECT
  TRIM(OrderNumber) AS orderid,
  Id AS stripe_payment_intent_id,
  OrderId AS stripe_order_id,
  OrderNumber AS stripe_order_number,
  InvoiceId AS stripe_invoice_id,
  CreatedDate AS stripe_created_at,
  DATE(CreatedDate) AS stripe_created_date,
  Amount AS stripe_amount_minor_units,
  AmountReceived AS stripe_amount_received_minor_units,
  SAFE_DIVIDE(AmountReceived, 100) AS stripe_amount_received,
  Currency AS stripe_currency,
  Status AS stripe_status,
  PaymentMethod AS stripe_payment_method,
  CustomerId AS stripe_customer_id,
  CountryCode AS stripe_country_code,
  DetectedCountry AS stripe_detected_country,
  CampaignId AS stripe_campaign_id
FROM `example-project.src_stripe_mexico.PaymentIntents`
WHERE OrderNumber IS NOT NULL
  AND DATE(CreatedDate) IS NOT NULL
  AND EXTRACT(YEAR FROM DATE(CreatedDate)) = target_year
  AND DATE(CreatedDate) >= start_date
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY TRIM(OrderNumber)
  ORDER BY CreatedDate DESC, Id DESC
) = 1
ORDER BY stripe_created_at, orderid;
