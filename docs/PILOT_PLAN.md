# Pilot plan: one docket

Status: draft. Nothing here has been approved. Items marked **Proposal** do not exist in the code yet.

## Question

Does a Bench Brief shorten the time a judge or magistrate needs to review a self-represented filing, without making the review worse?

Context: Indiana has fewer judges per capita than average. About 80% of litigants before the Allen County magistrates have no lawyer.

## Scope

- One docket. One court. Self-represented filings only.
- Fictional data is used for training and dry runs. Real filings need court IT and legal review first. See the status note in the README.
- Briefs are for the judicial officer. They are not filed, not served, and not part of the record. **Proposal:** confirm this with court counsel before the pilot starts.

## Design

The unit is the case. Each case is either brief-on or brief-off, using the existing per-case toggle (`/case/{id}`, "Turn on/off for this case"). The code stores the setting in `cases.brief_enabled` and logs each change in the audit table.

Assignment:

- Preferred: **alternate by case number.** Sort new cases as they arrive. Odd position is brief-on, even position is brief-off. Staff set the toggle when the case is opened.
- Alternative: randomize with a coin flip or a printed random list made before the pilot starts.
- **Proposal:** the app does not assign cases or record how assignment was made. Staff keep a log outside the app.

The toggle affects briefs generated on later filings. A case switched on after filings exist has no briefs for those filings until someone runs generation. Set the toggle before the first filing is entered.

Why by case, not by filing: all filings in a case share one filer's style and one set of facts. Mixing modes inside a case would blur the comparison. It also lets the repeat-motion note work across a whole case.

Brief-off cases still go through the app. The judge opens the full text in the source view and presses "Done reviewing". This gives a comparable timer. Without that, the two groups would be measured differently.

## Metrics

Collected by the app (see `/metrics`):

- **Review time per filing.** Seconds from page load to "Done reviewing". Reported as median and average, per mode. Use the median as the main number. A few long reviews skew the average.
- **Usefulness rating.** Yes, somewhat, no. Asked on brief pages only.
- **Guardrail withholds.** Count of lines dropped, grouped by reason (no citation, quote not found, non-neutral wording, unknown sentence).
- **Failed generations.** Count of `brief_failed` events. Each one means the judge reviewed the filing without a brief.
- Generation time per brief, by extractor.

Collected by hand (not in the app):

- **Flag accuracy spot-checks.** A staff member reads a sample of briefs next to their filings. For each flag, mark: correct, wrong, or misleading. For each line, mark: faithful or not. Suggested sample: 5 filings a week, or every filing in the first two weeks. Rule flags (`source: rule`) and model flags (`source: model`) are scored separately.
- **Missed items.** For the same sample, note any conflict, gap, or request that the brief missed.
- **Repeat-motion check.** For every "repeats" note, record whether the earlier filing is truly similar. Also note repeats the app missed.
- **Judge comments.** A short weekly check-in.

Known measurement problems in the current code:

- The timer includes idle time. Ask judges to press "Done reviewing" when they finish and to reload the page if they step away for long.
- The audit log has no judge id. **Proposal:** add a judge or session id to `review_done`, so results can be split by person and repeated reviews of one filing can be found.
- Metrics mix all data in the database. Use a fresh `BENCHBRIEF_DB` for the pilot.
- Failed-generation counts are not split by case mode. They can only come from brief-on cases, so this is a labeling issue, not a bias.
- The median for an even count is the upper middle value. It is close enough here. Recompute from the audit table for the final report.

Final analysis should export the `audit` table and compute numbers outside the app.

## Duration and sample size

Plain version: the number that matters is how many filings each group reviews, not how many weeks pass.

- Review times vary a lot. A short continuance motion takes a minute. A multi-page petition takes ten. The spread between filings is bigger than the effect we hope to see.
- A rough rule: to see a 20% drop in median time with this much variation, plan for at least 40 to 60 filings reviewed in each group. With fewer, a real difference can be hidden by noise, and a fake one can appear by chance.
- Compare like with like. Report results by filing type (for example, continuances separately from petitions), or at least check that both groups have a similar mix.
- The number of weeks follows from the docket's volume. If the docket gets about 15 self-represented filings a week, 100 filings take 7 weeks. Add 2 weeks at the start for training and a dry run with fictional filings, and 1 week at the end for analysis.
- **Proposal:** before starting, ask the court clerk for the actual weekly count. If it is under 10, extend the pilot rather than lowering the sample.
- These numbers are planning estimates, not a power calculation. Have someone with statistics training check them.

With a small pilot, treat the result as a signal about whether a larger test is worth running. Do not treat it as proof.

## Judge onboarding

Before the pilot, 45 minutes per judicial officer:

1. Show a brief and its source side by side, using the fictional seed cases (`python -m benchbrief seed`).
2. Click citations. Show that every line leads to a sentence, and that the judge can read the full filing at any time.
3. Explain what the brief does not do: no recommendation, no credibility view, no prediction. Explain the guardrails in plain words.
4. Show the flags. Explain they are questions of fact to clarify, not findings. Show that rule flags come from fixed checks.
5. Show the toggle. Any judge can ask to turn the brief off for any case at any time. Log the request and honor it.
6. Show "Done reviewing" and the usefulness rating. Explain what time is being measured and that it is about the tool, not the judge.
7. Explain failed briefs: the page says so, and the judge reads the filing directly.
8. Tell judges how to report a bad brief: write down the filing, the line, and what is wrong. **Proposal:** add a "report a problem with this line" button that writes an audit event.

Ground rules to state at onboarding:

- Read the source for anything that matters to a decision.
- The brief is not a substitute for the filing.
- Do not rely on the absence of a flag. No flag does not mean no conflict.

## Stop conditions

Stop the pilot, or turn the brief off for a case or the docket, if any of these happen:

- A brief contains a recommendation, credibility judgment, or outcome prediction that got past the guardrails. One confirmed case is enough to pause and review.
- A brief states a fact that is not in the filing, or misattributes who said something.
- A judge reports that the brief changed how they read a case in a way they think was unfair.
- Flag spot-checks show more than 1 in 10 flags wrong or misleading after the first two weeks. (Proposed threshold. The court should set its own.)
- Review time with briefs is longer than without by a clear margin after the sample reaches 20 filings per group.
- Failed generations exceed 1 in 10 filings.
- Filing text appears outside approved systems.
- Court IT, counsel, or the presiding judge asks to stop.

When stopping: turn the toggle off for affected cases, keep the audit table, and write down what happened.

## Open questions

### 1. Will judges trust a summary?

Risk: judges may over-trust, skim the brief, and skip the filing. Or they may distrust it and read everything, which saves no time.

Mitigations that exist:

- Every line cites a sentence id. One click opens the source with the cited sentence highlighted.
- Lines whose quote is not in the cited sentences are dropped, so the judge never sees an invented quote.
- The brief says "Filer states..." and uses the filer's words. Hover on a citation shows the quote.
- "Read full filing" link is at the top of every brief.
- Failed generations say plainly that no brief exists.
- The usefulness rating and the `source_viewed` and `brief_viewed` audit events show how often judges open the source after reading the brief. A low rate with a high rate of flag errors would be a warning sign.
- The toggle lets judges opt out.

Proposals:

- Report source-open rate per brief in `/metrics`, from the existing audit events.
- Ask in the weekly check-in: "Did you read the filing after the brief?" Record answers.
- Show spot-check error rates to judges during the pilot, so trust follows evidence.

### 2. How do we prevent subtle framing of one side's story?

Risk: choosing which sentences to include, their order, and their wording can tilt a brief. The guardrails block explicit evaluation, not selection.

Mitigations that exist:

- Fixed section order and fixed section labels. The model does not choose headings.
- Everything is attributed to the filer, and the brief page says it does not weigh evidence.
- A word list blocks credibility, merit, and outcome terms. Filer's own words are allowed only when they are in the cited quote.
- The schema has no fields for scores or recommendations.
- Flags come first from deterministic rules, so the most common conflicts do not depend on model judgment.
- The `rules` extractor quotes whole sentences and does not paraphrase. It can serve as a control: compare its output to the Claude brief for the same filing.
- Dropped lines are stored with reasons, so the pilot team can see what the model tried to say.

Proposals:

- In spot-checks, score each sampled brief for framing: does it leave out something material that the filer said, or give more space to one part? Use two reviewers and compare.
- Run a paired test on a sample: generate briefs with both extractors, and have a reviewer who has not seen the filing mark differences in emphasis.
- When opposing parties both file, show both briefs with the same layout. The current code groups repeat requests only for one filer, and has no cross-party view. Any such view would be new work.
- Add a fixed instruction test set of fictional filings with known tilts, run on each model or prompt change. **Proposal:** not in the repo today.
- Extend the word list as the spot-checks find new loaded terms. This is a code change in `guardrails.py` with a test.

### 3. Should litigants have a right to see the same brief?

Risk: a brief seen by only one side creates an information gap. A brief shown to litigants could invite disputes about wording, and an error would carry weight.

Position for the pilot: this is a policy question for the court, not for the tool. The prototype has no litigant-facing view and no login of any kind. Do not expose it to litigants.

Mitigations that exist:

- The brief only restates the filer's own filing and cites it. A litigant can check every line against their own document.
- The brief page prints cleanly (print styles hide controls), so a copy could be produced by staff.
- The per-case toggle allows the court to exclude any case.

Proposals:

- Decide before the pilot whether briefs are part of the court's file, work product of the judicial officer, or something else. Get counsel's answer in writing.
- If the answer is "litigants may see it": add a read-only, authenticated view for the parties of a case. Show the brief for each party's own filing. This does not exist.
- If the answer is "no": document that, and tell self-represented litigants that a tool may be used to organize filings. Notice text is for the court to write.
- Add a way for a litigant to flag a mistake in a brief of their own filing, routed to staff. This does not exist.
- In either case, keep the brief out of the record unless the court decides otherwise.

## Roles

- Pilot lead: runs the plan, keeps the assignment log, reviews metrics.
- Court IT: hosting, access control, data residency, backups.
- Counsel: confirms the legal status of briefs and the notice question.
- Judicial officers: use the tool, give feedback, can opt out at any time.
- Spot-check reviewer: staff member who is not a judge, scores briefs against filings.

## Before day one

- [ ] Counsel review of the status of briefs and litigant access.
- [ ] Court IT review of hosting, authentication, and data residency (see `docs/ARCHITECTURE.md`).
- [ ] Decision on which extractor and where the model runs. If a hosted model is not approved, use `rules`.
- [ ] Fresh database. Dry run with fictional filings.
- [ ] Assignment method chosen and logged.
- [ ] Judges onboarded and told how to opt out.
- [ ] Stop conditions agreed.
