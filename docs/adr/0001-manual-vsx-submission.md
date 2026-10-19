# Submission to VSX is manual by design

VSX has no submission API, and its revision forms go to volunteer moderators who review
one submission per person per day. We decided that no code in this repo will ever post
to, drive or scrape those forms: the pipeline stops at paste-ready field values, reference
text and a phase plot, and Tom enters them by hand. The alternative, a browser automation
step, would save a few minutes a day while putting Tom's standing with the moderators at
risk and violating the spirit of the AAVSO's guidance; the rate limit means throughput was
never the constraint anyway.

## Consequences

- The review app has no "submit" button, only "mark as submitted" with a date.
- Reading VSX programmatically (VizieR copy, the undocumented `api.object` endpoints) is
  fine; only the write path is off limits.
