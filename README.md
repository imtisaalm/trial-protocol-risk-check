# Clinical trial protocol risk check

This is a small learning project built from a clinical research perspective.
It turns selected registry fields into explicit prompts for protocol review.

The score is a checklist with weights. It is not a trained model, a probability
of termination, or a validated measure of protocol quality. I use the term
"risk" to describe operational questions that deserve human attention.

## Run in under five minutes

Use Python 3.10 or later. Place the files in this structure:

    trial-risk-demo/
        src/
            trial_risk_demo.py
        requirements.txt
        README.md
        NOTES.md

From the project directory:

    python -m venv .venv

Activate the environment on macOS or Linux:

    source .venv/bin/activate

On Windows PowerShell:

    .venv\Scripts\Activate.ps1

Install dependencies and run:

    python -m pip install -r requirements.txt
    python src/trial_risk_demo.py

Setup should take a few minutes on a typical connection. The initial download
depends on API availability, connection speed, and any retry delays. No API key
is required.

Later runs reuse the cache and do not call the API. To replace the cache:

    python src/trial_risk_demo.py --refresh

The script resolves file paths relative to its own project directory.

## Data and sampling

Source:

- API: https://clinicaltrials.gov/api/v2/studies
- API documentation: https://clinicaltrials.gov/data-api/api
- Registry field definitions: https://clinicaltrials.gov/policy/protocol-definitions

The script requests 300 unique interventional studies:

| Registry status | Target count |
| --- | ---: |
| COMPLETED | 150 |
| TERMINATED | 50 |
| WITHDRAWN | 50 |
| SUSPENDED | 50 |

It queries each status separately, sorts by NCT ID ascending, and follows page
tokens. This gives a reproducible selection rule within a changing registry.
It is a convenience sample with deliberate status quotas, not a random or
representative sample. Ascending NCT IDs also favor earlier registrations.

The script checks returned study types, statuses, and unique IDs. A failed
initial download does not save a partial cache. It expects all quotas on a
fresh download and checks that a reused cache contains at least 250 studies
with all four statuses represented.

`data/studies_raw.json` preserves each returned study object, along with the
fetch time and sampling settings. The cached snapshot is the reproducible
input for subsequent analysis. A refresh can change membership and values.

## Outputs

| File | Contents |
| --- | --- |
| data/studies_raw.json | Raw study objects and download metadata |
| results/trial_features.csv | Extracted features, scores, flags, and missing-signal indicators for all trials |
| results/top_risk_trials.csv | Ten highest scores, with flags and extracted fields |
| results/metrics.json | Group summaries, statistical comparisons, missingness, and sensitivity analysis |
| results/risk_by_status.png | High-resolution distribution chart |

Ties in the top ten are resolved by NCT ID. Being included does not establish
that a trial was poorly designed.

## Extracted features

The script extracts:

- NCT ID, brief title, overall status, enrollment, enrollment type, listed site
  count, and phases.
- Eligibility text length in characters and approximate inclusion and exclusion
  criterion counts.
- Minimum age, maximum age, and age-window width in years.
- Registry sex restriction and acceptance of healthy volunteers.
- Primary and secondary outcome counts.
- Enrollment divided by the number of listed sites.

Age conversion supports years, months, weeks, days, hours, and minutes. Months
are divided by 12; day-based conversions use 365.25 days per year. Missing,
unrecognized, or `N/A` values remain unknown. An age window requires two valid
bounds in the correct order.

The site count is the number of listed location entries. It is not a verified
count of unique institutions, activated sites, or sites that enrolled patients.
Missing or empty locations produce an unknown site count and ratio.

Absent outcome lists are treated as unknown rather than automatically zero.
An explicitly empty list is counted as zero. This conservative distinction
can leave outcome-related signals unassessable.

Sex restriction, healthy-volunteer status, phases, inclusion count, and text
length are descriptive features. They do not independently add risk points.

## Risk signals

These thresholds and weights are illustrative choices made before examining
the status comparison. They are not literature-derived cutoffs or coefficients
estimated from these data.

| Signal | Exact trigger | Weight | Why a reviewer might look |
| --- | --- | ---: | --- |
| Very narrow age window | Known maximum minus minimum age is less than 10 years | 2 | Check whether the eligible population supports recruitment assumptions and whether the restriction is clinically necessary. |
| Long exclusion list | At least 15 parsed exclusion criteria | 2 | Review the cumulative screening burden, safety rationale, and potential redundant restrictions. |
| Many outcomes | At least 3 primary outcomes, or at least 10 primary plus secondary outcomes | 1 | Review endpoint hierarchy, multiplicity, assessment burden, and operational feasibility. |
| Very high enrollment per site | Enrollment divided by listed sites is greater than 200 | 2 | Compare the implied site contribution with recruitment duration, patient availability, and capacity. |
| Single site with large enrollment | Exactly 1 listed site and enrollment of at least 300 | 2 | Review concentration of delivery responsibility and contingency arrangements. |

The score is the sum of triggered weights, ranging from 0 to 9.
The illustrative high-risk review threshold is a score of at least 4.

The two enrollment signals can both fire. This intentionally emphasizes
concentrated enrollment burden but partly double-counts the same information.
Neither rule accounts for recruitment duration.

A missing value does not trigger a flag. The script records unassessable signals
and how many signals could be assessed. Consequently, a low score with missing
information must not be interpreted as reassuring. Scores are not normalized
by the number of available features.

For the outcome rule, three known primary outcomes are enough to trigger the
flag even if secondary outcomes are unknown. Otherwise, both counts are needed
to assess the combined threshold.

## Eligibility parsing

Registry eligibility text is not consistently structured. Counting only bullet
characters would miss criteria written as paragraphs.

The parser first finds case-insensitive "Inclusion Criteria" and "Exclusion
Criteria" headings, with or without a colon. It removes common Markdown heading
and emphasis markers. It splits at every recognized heading, including headings
embedded on the same line as the text. Repeated sections of the same type are
combined.

Within each section:

1. Each bullet or numbered line counts as one criterion. Recognized markers
   include hyphens, asterisks, common bullet symbols, `1.`, `1)`, `(1)`, and
   single-letter list markers.
2. Unmarked lines immediately following a list item are treated as wrapped
   continuation text.
3. Unmarked prose is joined across wrapped lines and split into sentence-like
   units at sentence-ending punctuation followed by whitespace, or semicolons.
4. Decimal points are not split because they are not followed by whitespace.
   A small set of common abbreviations is protected.
5. An explicit section containing only "None", "N/A", or "Not applicable" counts
   as zero. Missing headings, empty sections, and missing text remain unknown.

For example:

    Inclusion Criteria: Adults aged 18 to 65 years. Able to provide informed consent.
    Exclusion Criteria: Active infection. Pregnancy or breastfeeding; Prior treatment within 30 days.

Mental walkthrough:

- Split at the two headings.
- The inclusion section contains two sentence-like units.
- The exclusion section contains one period boundary and one semicolon boundary,
  giving three units.
- The resulting counts are 2 inclusion criteria and 3 exclusion criteria.

The script checks that example at startup, along with a numbered and bulleted
example containing a wrapped continuation line.

These checks establish expected behavior for the examples, not general parser
accuracy. Nested bullets are counted separately. A single bullet may contain
several requirements, and one clinical requirement may span several sentences.
Introductions can be counted as criteria. Paragraphs following bullets without
a blank line can be mistaken for continuation text. Unrecognized headings,
combined headings, abbreviations, tables, and non-English records require
manual review or a more capable parser.

## Results

No dataset analysis has been run for this README. The table below intentionally
contains no invented results. Replace each placeholder using
`results/metrics.json` after running the script.

Use `groups.COMPLETED` and `groups.TERMINATED_WITHDRAWN_SUSPENDED`.
Multiply `high_risk_share` by 100 to obtain a percentage.

| Measure | COMPLETED | TERMINATED / WITHDRAWN / SUSPENDED |
| --- | --- | --- |
| Actual group size | 150 | 150 |
| Median score | 0.0 | 0.5 |
| High-risk trial count | 11 | 9 |
| High-risk share (%) | 7.3 | 6.0 |
| All five signals assessable, count | 64 | 46 |

| Comparison | Result |
| --- | --- |
| Mann-Whitney U, interrupted group first | 11608.0 |
| Two-sided p-value | 0.607 |
| Rank-biserial effect size, positive means interrupted scores are higher | 0.032 |
| Interpretation copied from metrics.json | The observed separation is weak or absent by the demo's descriptive effect-size threshold, regardless of the p-value. |
| Complete-feature sensitivity comparison | n=110 (64 completed, 46 interrupted); U 1560.5, p 0.576, rank-biserial 0.060; same interpretation: weak or absent separation. |

The JSON also reports each of the four statuses separately.

## How to interpret the comparison

The primary comparison pools terminated, withdrawn, and suspended studies into
one interrupted-status group and compares their scores with completed studies.
These statuses have different meanings; suspension need not be permanent.

The Mann-Whitney U test is two-sided and uses the asymptotic method with tie
and continuity corrections because the score has many ties. When all scores
are identical, the script reports U equal to half the cross-group pairs and
p equal to 1.

This is a distributional comparison. It is not necessarily a test of medians
alone when group distribution shapes differ.

The rank-biserial effect size is:

    2 * U / (n_interrupted * n_completed) - 1

A positive value indicates that interrupted studies tend to have higher scores.
Ties contribute half a pair to U.

For this demo, an absolute effect size below 0.10 is described as a weak or
absent separation, regardless of statistical significance. This is a declared
descriptive convention, not an established clinical threshold. If scores are
higher among completed trials, the script explicitly says the intended signal
is not supported. If the effect points in the intended direction but p is at
least 0.05, it describes the evidence as inconclusive.

If the actual result is weak or absent, say so plainly when completing this
README. Do not change the thresholds to manufacture a positive result.

A sensitivity comparison uses only studies for which all five signals are
assessable. It helps expose missing-data dependence but can introduce its own
selection bias. If either group is empty, the comparison is reported as
unavailable.

## Important limitations

- Status is a crude proxy. Stopping can reflect funding, business decisions,
  emerging external evidence, safety, or other factors unrelated to the selected
  protocol features. Completion does not prove an efficient or strong design.
- Correlation is not causation. Disease area, phase, sponsor, calendar period,
  geography, and recruitment duration may explain observed differences.
- These are current registry snapshots, not protocols frozen before enrollment.
  Eligibility, outcomes, sites, and enrollment can change after initiation.
  Actual enrollment may be a consequence of stopping. This creates temporal
  leakage and reverse-causation concerns.
- Enrollment is recorded with its type, but both actual and estimated values
  enter the illustrative score. This is a major limitation, especially for
  terminated or withdrawn trials with low realized enrollment.
- Parsing is approximate. Criterion counts do not measure clinical necessity,
  complexity, or the fraction of patients excluded.
- The fixed age threshold is particularly crude across pediatric and adult
  populations. Narrow eligibility can be scientifically essential.
- Outcome counts do not capture visit burden, assay complexity, endpoint
  importance, or participant experience.
- Missing registry fields can suppress scores. A zero is not a clearance.
- Status quotas prevent estimation of population interruption rates. The sample
  cannot establish calibration, sensitivity, specificity, or clinical utility.
- Related trials may share sponsors, protocols, or sites. The U test assumes
  independent observations, which may not fully hold.
- The p-value is exploratory. It does not establish generalizability or replace
  assessment of effect size, missingness, and confounding.
- This is a screening aid for human review, not a prediction, clinical decision
  tool, regulatory recommendation, or basis for ranking investigators.

## Next steps

1. Manually annotate a varied eligibility sample and measure parser error,
   including paragraph formats, nested lists, and absent headings.
2. Use historical registry versions to freeze predictors before recruitment.
   Separate planned enrollment and planned sites from realized values.
3. Study amendment history as an outcome. Define substantive amendments,
   distinguish protocol changes from administrative updates, and establish a
   clear time origin and follow-up window.
4. Develop per-area baselines by therapeutic area, phase, age population,
   intervention type, and recruitment duration.
5. Review thresholds with trial designers and operations staff before any
   prospective use. Preserve rule versions and reasons for changes.
6. Evaluate on a separate sample after rules are fixed. Assess whether flagged
   reviews identify useful actions, not merely whether a p-value is small.
7. Lock exact dependency versions and retain the input snapshot when sharing
   a completed analysis.

The useful deliverable is an auditable review conversation, not a claim that
these five rules can predict whether a trial will succeed.
