# Xvoice entitlements — how access is decided, and how to test it

## The rule

There is deliberately **no account-wide "is premium"**. Dictation and Writing are
bought separately, so each is resolved on its own:

```
DICTATION_PREMIUM = active Dictation subscription  OR  active Platform subscription
WRITING_PREMIUM   = active Writing subscription    OR  active Platform subscription
```

Platform is an umbrella that grants both. It is **never** inferred from owning
both products separately — that is two subscriptions, and the billing page must
say so.

In code (`models.py`):

| Question | Property |
|---|---|
| Can they use Dictation premium? | `user.dictation_is_paid` |
| Can they use Writing premium? | `user.writing_is_paid` |
| Do they hold a Dictation sub specifically? | `user.dictation_subscription_active` |
| Do they hold a Writing sub specifically? | `user.writing_subscription_active` |
| Do they hold a real Platform sub? | `user.platform_subscription_active` |
| What do they actually own? | `user.owned_products` |

`user.tier` is kept as a thin alias for `dictation_is_paid` because the desktop
client and older code already expect the `"paid"` / `"trial"` strings.

## Why the schema changed

The old `users` table held **one** subscription: `plan_product` (a single value),
`subscription_status`, `current_period_end`. That made two of the requirements
impossible rather than merely wrong:

- "Dictation and Writing bought separately" — `plan_product` holds one value.
- "Dictation expires, Writing stays premium" — one `current_period_end` for the
  whole account, so one product's expiry took the other down with it.

Each product now has `<product>_sub_status`, `<product>_period_end` and
`<product>_sub_id`. `NULL` status means *never subscribed*, which is distinct
from `'expired'`.

**Old columns still work.** While *all three* statuses are `NULL` the account has
never been touched by the per-product system, and entitlement falls back to
`plan_product` + `subscription_status`. Accounts that predate the migration keep
their access whether or not the backfill has run, so deploy order does not
matter.

The fallback is deliberately account-wide rather than per-product. If each
product consulted the old columns independently, a `NULL` status would mean
"unknown, go ask `plan_product`" rather than "no subscription" — and since the
old columns are still mirrored for the billing page and the trial cron, clearing
or expiring one product would let those mirrored values re-grant it. Buying
Writing would light up Dictation, which is the exact bug this design exists to
prevent.

The consequence is that the **first** per-product write on a legacy account
switches the fallback off for the whole account, so that write must carry the
account's existing access across first. `User.materialize_legacy_products()`
does this — it copies a live legacy subscription onto the product `plan_product`
names before anything else is written, and is a no-op once migrated.
`apply_product_subscription()` and the dev simulator both call it, so every
write path is covered. The backfill does the same thing in SQL, ahead of time.

`plan_product` is free text and exists in the database with varying case and
stray whitespace, so both the fallback and the backfill compare it normalised
(`lower(trim(...))`). An exact match would drop those paying customers to trial.

## What premium actually unlocks

Verified per product against the real handlers, not just the flags:

| Gate | Resolved by |
|---|---|
| Custom push-to-talk hotkey (`PUT /auth/hotkey`) | `tier` → `dictation_is_paid` |
| Transcription language (`PUT /auth/language`) | `tier` → `dictation_is_paid` |
| Live translation (`PUT /auth/translation`) | `tier` → `dictation_is_paid` |
| Transcription queue priority | `tier` → `dictation_is_paid` |
| Unlimited writing actions | `writing_is_paid` |
| Writing status / monthly allowance | `writing_is_paid` |
| Desktop `dictation_enabled` / `writing_enabled` | the respective flag |

The Writing trial allows 30 actions per calendar month. Every writing endpoint
(`/text/transform`, `/writing/rewrite`, `/writing/record`) counts against that one
allowance through `transform._claim_writing_action`.

A paid **Dictation** customer on the Writing trial gets 100 writing actions a
month rather than the trial's 30 — a taste of the other product, deliberately
not unlimited and not an entitlement to it. It still needs the Writing trial:
without one (or once it ends) Writing is refused like for anyone else. If
Writing should have no extra allowance for Dictation customers, drop that branch
in `transform._writing_quota_for`.

There is no free tier after a trial: dictation stops when the 14-day Dictation
trial ends, and Writing needs its own trial or a subscription. The pricing cards
and landing pages say so.

## Billing lifecycle

How a Stripe subscription reaches these columns, and the rules that keep one
product's events from disturbing another's.

- **Purchase.** Checkout is created by the `create-checkout` edge function.
  Stripe's `customer.subscription.*` webhooks go to `payments-webhook`, which
  writes the website's `subscriptions` row and sends **that one subscription** to
  `/lovable-sync`. (It used to re-send every subscription the customer ever held,
  so a lapsed one replayed after a new one and overwrote it.)
- **Which product.** From the price's lookup key (`xvoice_intro_monthly`,
  `xvoice_writing_monthly`, `xvoice_platform_monthly`), the same in test and
  live mode. The `STRIPE_*_PRICE_ID` secrets are only a fallback. An unknown
  price unlocks nothing and fails loudly; it never defaults to Dictation.
- **Current state, not event state.** The webhook reads the subscription from
  Stripe before writing, so a late retry of an old event cannot roll it back.
- **Statuses.** `incomplete` / `incomplete_expired` (never paid) change nothing.
  `past_due` keeps access while Stripe retries. `unpaid` / `paused` end it.
  `canceled` grants access until the paid period ends — unless the subscription
  was ended outright (refund, cancel-now, or Stripe giving up after failed
  payments), when access ends at that moment.
- **Superseded subscriptions.** `/lovable-sync` ignores a sync for a different
  subscription to a product whose current subscription is still active, unless
  the new one is itself live and runs at least as long.
- **Cancelling.** The website calls the `cancel-subscription` edge function with
  the product; it sets `cancel_at_period_end` on that product's subscription, so
  access continues to the end of the paid period and nothing renews. Railway's
  `/billing/cancel` remains for older website builds; it needs `product` when the
  customer holds more than one subscription and never guesses.
- **Upgrading to Platform.** When a Platform subscription starts, any Dictation or
  Writing subscription the customer still holds is cancelled immediately and the
  unused part of its current period refunded. Idempotent under webhook retries.
- **Desktop app.** Every applied sync pushes `entitlements_updated`, so a running
  app picks up a purchase or lapse without a restart.

## Stale rows

A `paid` product grants access until its `period_end`, and forever if no period
end was ever recorded. Sync reports each subscription's status, so a product is
normally revoked by being told it was cancelled — but nothing arrives to say a
subscription simply stopped being reported (a row deleted upstream, a sync call
that failed and was never retried). Honouring the expiry means such a row lapses
on its own instead of granting access indefinitely.

NULL is treated as "no expiry recorded", not "expired", so a paying customer
whose period end was never stored keeps access. The backfill copies
`current_period_end`, which can be NULL, and a sync payload may omit it — so
that case is real, and locking those users out would be the worse failure.

## Test matrix

Run `POST /api/billing/dev/simulate` (development builds only) then check
`GET /api/billing/products`.

| # | Set up | Dictation | Writing | Platform |
|---|---|---|---|---|
| 1 | `{"dictation":"trial","writing":"trial"}` | TRIAL | TRIAL | inactive |
| 2 | `{"dictation":"premium","writing":"trial"}` | **PREMIUM** | TRIAL | inactive |
| 3 | `{"dictation":"trial","writing":"premium"}` | TRIAL | **PREMIUM** | inactive |
| 4 | `{"dictation":"premium","writing":"premium"}` | **PREMIUM** | **PREMIUM** | inactive |
| 5 | `{"platform":"active"}` | **PREMIUM** | **PREMIUM** | **ACTIVE** |
| 6 | `{"dictation":"premium","platform":"inactive"}` | **PREMIUM** | free/trial | inactive |
| 7 | `{"writing":"premium","platform":"inactive"}` | free/trial | **PREMIUM** | inactive |
| 8 | `{"dictation":"premium","writing":"premium","platform":"inactive"}` | **PREMIUM** | **PREMIUM** | **inactive** |
| 9 | `{"dictation":"expired","writing":"premium"}` | expired | **PREMIUM** | inactive |
| 10 | `{"writing":"expired","dictation":"premium"}` | **PREMIUM** | expired | inactive |
| 11 | `{"platform":"expired","dictation":"free","writing":"free"}` | free | free | expired |
| 12 | `{"platform":"expired","dictation":"premium"}` | **PREMIUM** | free/trial | expired |
| 13 | `{"platform":"expired","writing":"premium"}` | free/trial | **PREMIUM** | expired |
| 14 | `{"platform":"expired","dictation":"premium","writing":"premium"}` | **PREMIUM** | **PREMIUM** | expired |

Row 8 is the one to watch: `owned_products` must be `["dictation","writing"]`
and must **not** contain `platform`.

Also covered by the automated suite:

- Cancelled but inside the paid period → access continues to the period end.
- Cancelled and past it → access ends.
- Platform cancelled but inside the period → both products still premium.
- Buying one product never consumes or shortens the other's trial.
- Legacy accounts (per-product columns still `NULL`) keep exactly the access
  their old `plan_product` implied.

For each row, check:

- `GET /api/billing/products` — per-product state, `owned_products`
- `GET /api/auth/validate` — `dictation_premium`, `writing_premium`,
  `platform_active`, `writing_enabled`, `dictation_enabled`
- Dictation premium features: custom hotkey, transcription language,
  translation (`PATCH /api/auth/hotkey|language|translation` → 403 unless
  Dictation premium)
- Writing quota: `writing_is_paid` → unlimited; Dictation-only → 100/month;
  otherwise the free allowance

## The dev simulator

```
POST /api/billing/dev/simulate
{"dictation": "free|trial|premium|expired|canceling",
 "writing":   "free|trial|premium|expired|canceling",
 "platform":  "inactive|active|expired|canceling"}
```

Only the products named are changed, so you can move one and confirm the others
did not shift.

It is registered **only** when `ENVIRONMENT` is one of `development`, `dev`,
`local`, `test`, `testing`. In production the route does not exist at all — a
404, not a permission check that has to be correct.

## Applying the schema change

Runs automatically at startup. To apply it by hand:

```
psql "$DATABASE_URL" -f backend/migrations/001_per_product_subscriptions.sql
```

Idempotent and safe to re-run. The file ends with verification queries,
including one that lists anyone who lost access — it should return no rows.
