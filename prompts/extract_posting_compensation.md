# Extract stated compensation from a job posting

You are extracting **only compensation that the posting itself states**. You
are not estimating, benchmarking, or inferring what a role "probably" pays.

Return a single JSON object of this shape:

```json
{
  "items": [
    {
      "amount_min": 105000,
      "amount_max": 125000,
      "currency": "GBP",
      "component": "base",
      "pay_period": "annual",
      "employment_basis": "permanent",
      "evidence_span": "£105,000 - £125,000 per annum",
      "note": "stated in the 'Package' section"
    }
  ],
  "no_compensation_stated": false,
  "notes": null
}
```

## Rules

1. **Every item must be supported by the source text.** `evidence_span` must
   be a span copied **character for character** from the posting, including
   its currency symbol, punctuation and spacing. Do not tidy it, translate
   it, re-case it, expand abbreviations, or join text from two different
   places. A server-side check rejects any span that is not found verbatim in
   the immutable source document, so an approximate quote is worse than no
   item at all.

2. **Never invent a missing field.** If the posting gives a single figure,
   set `amount_min` to that figure and leave `amount_max` null (or vice
   versa where only a ceiling is stated). If it gives no currency, leave
   `currency` null — do not infer one from the country, the employer, or the
   language of the advert. If it does not say whether the figure is annual,
   leave `pay_period` null.

3. **Never estimate.** If the posting says "competitive salary", "salary
   depending on experience", "market rate", or gives only a benefits list,
   that is *no stated compensation*: return `"items": []` and
   `"no_compensation_stated": true`. Producing a guessed number here is the
   single worst thing you can do in this task.

4. **`component`** is one of:
   - `base` — base salary or basic pay
   - `day_rate` — a contractor day rate
   - `total_package` — an explicitly stated total/OTE package
   - `bonus_pct` — a bonus expressed as a percentage of base
   Use exactly one per item. A posting stating both a base salary and a
   separate bonus produces **two** items, never one combined figure.

5. **`pay_period`** is `annual` or `daily`. A monthly or hourly figure that
   the posting does not itself annualise must not be converted — omit the
   item and mention it in `notes` instead.

6. **`employment_basis`** is `permanent`, `contract`, or `unknown`. Use
   `unknown` unless the posting is explicit.

7. **One item per distinct stated figure.** Do not repeat the same figure
   because it appears twice in the advert.

8. `notes` is for anything a human reviewer should know that does not fit an
   item — for example "an hourly rate is stated but was not extracted", or
   "the salary appears in a table that may not have captured cleanly". Leave
   it null when there is nothing to say.

Return only the JSON object, with no commentary around it.
