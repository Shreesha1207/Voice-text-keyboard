-- ============================================================================
--  Xvoice — per-product subscriptions
--
--  Dictation, Writing and Platform are sold separately and a user may hold any
--  combination of them. The users table could not express that:
--
--    plan_product        holds ONE value, so "bought Dictation AND Writing
--                        separately" was unrepresentable
--    subscription_status one status for the whole account
--    current_period_end  one expiry, so one product lapsing silently took the
--                        others down with it
--
--  This adds a status, period end and Stripe subscription id per product, then
--  backfills them from the old columns.
--
--  SAFE TO RE-RUN. Every statement is idempotent and the backfill only touches
--  rows that have never been written to.
--
--  The application also runs this automatically at startup (backend/database.py
--  init_db). Running it by hand is only needed if you want it applied before a
--  deploy, or to verify it.
--
--  Postgres. Run against the Railway database.
-- ============================================================================

BEGIN;

-- ── 1. Columns ──────────────────────────────────────────────────────────────
-- NULL status means "never subscribed to this product". That is deliberately
-- distinct from 'expired'. While ALL THREE are NULL the account is untouched by
-- the per-product system and the application falls back to the old columns, so
-- access stays correct whether or not this has run yet. As soon as any one of
-- them is set the per-product columns are authoritative for the whole account.
ALTER TABLE users ADD COLUMN IF NOT EXISTS dictation_sub_status VARCHAR(20);
ALTER TABLE users ADD COLUMN IF NOT EXISTS dictation_period_end TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS dictation_sub_id     VARCHAR(255);

ALTER TABLE users ADD COLUMN IF NOT EXISTS writing_sub_status   VARCHAR(20);
ALTER TABLE users ADD COLUMN IF NOT EXISTS writing_period_end   TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS writing_sub_id       VARCHAR(255);

ALTER TABLE users ADD COLUMN IF NOT EXISTS platform_sub_status  VARCHAR(20);
ALTER TABLE users ADD COLUMN IF NOT EXISTS platform_period_end  TIMESTAMP;
ALTER TABLE users ADD COLUMN IF NOT EXISTS platform_sub_id      VARCHAR(255);

-- ── 2. Backfill ─────────────────────────────────────────────────────────────
-- Seed each existing live subscription into the product it was actually for.
-- Only rows with all three statuses still NULL are touched, so re-running can
-- never overwrite anything set since.

UPDATE users
   SET dictation_sub_status = lower(subscription_status::text),
       dictation_period_end = current_period_end,
       dictation_sub_id     = stripe_subscription_id
 WHERE lower(trim(plan_product)) = 'dictation'
   AND dictation_sub_status IS NULL
   AND writing_sub_status   IS NULL
   AND platform_sub_status  IS NULL
   AND lower(subscription_status::text) IN ('paid', 'canceled');

UPDATE users
   SET writing_sub_status = lower(subscription_status::text),
       writing_period_end = current_period_end,
       writing_sub_id     = stripe_subscription_id
 WHERE lower(trim(plan_product)) = 'writing'
   AND dictation_sub_status IS NULL
   AND writing_sub_status   IS NULL
   AND platform_sub_status  IS NULL
   AND lower(subscription_status::text) IN ('paid', 'canceled');

UPDATE users
   SET platform_sub_status = lower(subscription_status::text),
       platform_period_end = current_period_end,
       platform_sub_id     = stripe_subscription_id
 WHERE lower(trim(plan_product)) = 'platform'
   AND dictation_sub_status IS NULL
   AND writing_sub_status   IS NULL
   AND platform_sub_status  IS NULL
   AND lower(subscription_status::text) IN ('paid', 'canceled');

COMMIT;

-- ============================================================================
--  Verification — run these afterwards.
-- ============================================================================

-- How many accounts hold each product?
-- SELECT
--   count(*) FILTER (WHERE dictation_sub_status = 'paid') AS dictation_paid,
--   count(*) FILTER (WHERE writing_sub_status   = 'paid') AS writing_paid,
--   count(*) FILTER (WHERE platform_sub_status  = 'paid') AS platform_paid,
--   count(*) FILTER (WHERE dictation_sub_status IS NULL
--                      AND writing_sub_status   IS NULL
--                      AND platform_sub_status  IS NULL) AS not_yet_backfilled
-- FROM users;

-- Nobody should have lost access: every previously-paying account must now hold
-- at least one product. This should return ZERO rows.
-- SELECT id, email, plan_product, subscription_status
--   FROM users
--  WHERE lower(subscription_status::text) IN ('paid', 'canceled')
--    AND dictation_sub_status IS NULL
--    AND writing_sub_status   IS NULL
--    AND platform_sub_status  IS NULL;

-- ============================================================================
--  Manually setting a test account (no Stripe payment needed).
--  In a development deploy prefer POST /api/billing/dev/simulate.
-- ============================================================================

-- Platform (both products premium):
-- UPDATE users SET platform_sub_status = 'paid',
--                  platform_period_end = now() + interval '30 days'
--  WHERE email = 'you@example.com';

-- Dictation only:
-- UPDATE users SET dictation_sub_status = 'paid',
--                  dictation_period_end = now() + interval '30 days',
--                  writing_sub_status = NULL, platform_sub_status = NULL
--  WHERE email = 'you@example.com';

-- Writing only:
-- UPDATE users SET writing_sub_status = 'paid',
--                  writing_period_end = now() + interval '30 days',
--                  dictation_sub_status = NULL, platform_sub_status = NULL
--  WHERE email = 'you@example.com';

-- Both, bought separately (must NOT display as Platform):
-- UPDATE users SET dictation_sub_status = 'paid',
--                  dictation_period_end = now() + interval '30 days',
--                  writing_sub_status = 'paid',
--                  writing_period_end = now() + interval '30 days',
--                  platform_sub_status = NULL
--  WHERE email = 'you@example.com';

-- Back to a clean slate. Clearing all three makes the row look untouched by the
-- per-product system again, so the old columns have to be cleared with them or
-- they will re-grant exactly what was just removed.
-- UPDATE users SET dictation_sub_status = NULL, dictation_period_end = NULL,
--                  writing_sub_status   = NULL, writing_period_end   = NULL,
--                  platform_sub_status  = NULL, platform_period_end  = NULL,
--                  subscription_status  = 'TRIAL', current_period_end = NULL
--  WHERE email = 'you@example.com';
