-- Master source for Mexico PaymentHub orders.
-- donation_number is normalized as orderid because it should match Salesforce Opportunity.Auth__c.
DECLARE target_year INT64 DEFAULT @target_year;
DECLARE start_date DATE DEFAULT @start_date;

SELECT
  TRIM(donation_number) AS orderid,
  donation_number,
  Agreement_ID AS agreement_id,
  Transaction_ID AS transaction_id,
  completed_date,
  completed_time,
  DATETIME(completed_date, IFNULL(completed_time, TIME '00:00:00')) AS completed_at,
  COALESCE(completed_date, created_date, updated_date) AS activity_date,
  created_date,
  created_time,
  updated_ts,
  donation_amount,
  Currency AS currency,
  Payment_Method AS payment_method,
  Payment_Gateway AS payment_gateway,
  Payment_Gateway_Integration AS payment_gateway_integration,
  Merchant_Account AS merchant_account,
  Order_Status AS order_status,
  Payment_State AS payment_state,
  Agreement_Status AS agreement_status,
  Donation_Type AS donation_type,
  Campaign_ID AS campaign_id,
  Adjusted_Campaign_ID AS adjusted_campaign_id,
  Email AS email,
  First_Name AS first_name,
  Last_Name AS last_name,
  source,
  market_original,
  pm_original,
  pm_brand
FROM `example-project.src_paymenthub_mexico.paymenthub_orders_mexico`
WHERE donation_number IS NOT NULL
  AND COALESCE(completed_date, created_date, updated_date) IS NOT NULL
  AND EXTRACT(YEAR FROM COALESCE(completed_date, created_date, updated_date)) = target_year
  AND COALESCE(completed_date, created_date, updated_date) >= start_date
ORDER BY activity_date, completed_date, completed_time, orderid;
