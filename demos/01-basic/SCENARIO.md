# Demo 01 — Basic rotation triage

A small org keeps an `inventory.json` of secret **metadata** (no secret values
ever live in this file — just name, provider, when it was last rotated, and how
sensitive it is). TOKENROTATE turns that inventory into an actionable rotation
plan and a CI gate.

## Input

`inventory.json` lists six secrets across AWS, GitHub, Stripe, Datadog, a
database, and an SMTP relay. It overrides the rotation cadence for `stripe`
(365d) and `github` (180d), and one secret (`legacy-db-readonly`) carries a
per-secret 30-day override. The SMTP relay has no `last_rotated` on record.

Defaults applied for the rest: `aws=90`, `datadog=180`, `generic=90`.

## Run it

```sh
# Ordered plan, highest priority first (table or json)
python -m tokenrotate plan demos/01-basic/inventory.json
python -m tokenrotate plan demos/01-basic/inventory.json --format json

# Roll-up report
python -m tokenrotate report demos/01-basic/inventory.json

# CI gate: non-zero exit if anything is overdue or has an unknown age
python -m tokenrotate check demos/01-basic/inventory.json --format json
echo "exit code: $?"
```

## What you should see

Relative to its compute date the engine buckets each secret into
`ok | due_soon | overdue | unknown`:

- **unknown** — `smtp-relay-password` (never rotated / no date) → floats to top,
  exit non-zero.
- **overdue** — `prod-aws-deploy-key` (90d cadence, rotated 2025-12-01) and
  `github-actions-pat` (180d cadence, rotated 2025-08-15) are past due; the
  `critical` severity on the AWS key weights its priority above the GitHub PAT.
- **due_soon / ok** — the Stripe, Datadog, and short-interval DB keys land in the
  near-term or healthy buckets depending on the run date.

`check` exits **1** because actionable findings exist — wire it into CI to fail
a pipeline when credentials drift past their rotation window. `plan` and
`report` also return non-zero when findings exist so any of them can gate.
