# Data contracts (skeleton)

## Outcome states

The router and local pipelines share three outcome states: `success`,
`error`, and `unknown`. `success` means the request completed and its
usage was metered; `error` means the request failed (including
rate-limit and server errors, which stay distinguishable in the raw
counters); `unknown` means the outcome could not be determined from the
available record and must never be silently counted as either success
or error.

## Usage-known vs outcome separation

Usage-known tracks whether token/cost figures exist for a request,
independently of its outcome. A request can have a known outcome with
unknown usage (for example an error with no metered tokens), or known
usage with an ambiguous outcome bucket. Rollups, streaks, and cost
estimates must consult the two axes separately so unmetered or
error-only days do not inflate token totals and error-only activity
does not fabricate usage streaks.
