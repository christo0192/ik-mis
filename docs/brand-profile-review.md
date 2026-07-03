# Brand Profile — Human Review Checklist

`brand-profile.json` was built manually (WebFetch + raw-HTML curl/grep) against
interviewkickstart.com, .com/courses, .com/reviews, .com/about-us, .com/in, and
.com/guide/interview-kickstart-cost, because the installed `ads-dna` skill's fixed
output schema didn't match the schema PLAN.md Step 5 requires. Before trusting this
file for ad generation, verify:

1. **Reconcile the learner-count stat.** Site shows four different numbers across
   pages: 25,000+ (home), 27,000+ (/in), 13,500+ (/about-us), 20,000 (/courses).
   Pick the current approved figure with the marketing team.
2. **Reconcile the salary-hike stat.** 66.5% (/courses), 60%+ (home), ₹23 LPA /
   60% (/about-us), ₹24 LPA (/in) — same conflict pattern; confirm the live figure.
3. **Confirm highest-offer framing** ($1.2M global vs ₹1.7Cr on /in) — same case
   or two different data points? Confirm currency to use for India creative.
4. **Colors are reverse-engineered, not official.** Primary blues (#005EFF,
   #009BEC) came from the logo SVG's fill attributes, not a stated brand
   guideline. Visually confirm against the live site/brand kit if one exists.
5. **Typography is unresolved.** No Google Font/custom @font-face found in the
   homepage HTML — only a browser system-font stack. Left `null` rather than
   guessed; someone should screenshot the site and identify the actual typeface.
6. **No pricing exists on the site** (cost page defers to a webinar/advisor call)
   — confirm no ad should ever state a specific price.
7. **"Guaranteed success" phrase** appears once on the homepage as marketing
   copy, with no formal guarantee/refund policy found elsewhere. Get legal/
   marketing sign-off before any ad implies a guarantee.
8. **Competitor list is empty by design** — none are named on the site. If a
   suppression list is needed, source it separately; don't let a future agent
   infer competitor names into this file without grounding.
9. **Proof points** each carry a source URL — spot-check 2-3 against the live
   pages in case content has changed since 2026-07-03.
10. **Personas (4) are inferred**, not stated by the site — grounded in course
    catalog + testimonial names/roles/companies; sanity-check against real
    funnel/lead data (India_Leads / segment performance) once available.
11. **Imagery style** description is inferred from page structure (icon style,
    photo/logo strip use), not from an explicit brand style guide.
12. **Tone-of-voice do/don't list** is inferred from copy patterns (headlines,
    CTA text) — reasonable starting point but not a substitute for an actual
    brand-voice doc if IK marketing has one internally.
13. **Region/currency context** assumes India-targeted generation should draw
    primarily from the /in page; confirm that's the right source of truth vs.
    the global .com site for India campaigns.
14. **Re-run/refresh cadence**: this is a one-time snapshot (2026-07-03); course
    catalog, stats, and press mentions will drift — re-extract periodically.
15. **Action before Step 5 is marked "Done"**: have a human (perf/marketing
    team) skim `brand-profile.json` end-to-end and confirm items 1-3 and 6-7
    above, since those are the highest-risk fields for compliance/accuracy.
