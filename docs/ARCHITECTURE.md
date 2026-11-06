# Architecture

Bench Brief is a small Python app: a Starlette web server, a SQLite file, and a pluggable extractor. This document describes what the code does today.

## Pipeline

```
filing (PDF or text)
  -> ingest        pages and sentences with ids (ingest.py)
  -> extractor     draft Brief (extract.py)
  -> rule checks   deterministic flags merged in (checks.py)
  -> guardrails    drop uncited, unquoted, or non-neutral lines (guardrails.py)
  -> store         filing, brief, guard report, audit event (pipeline.py, store.py)
  -> render        brief page with links to source (app.py, templates/)
```

`pipeline.build_brief` runs extract, checks, and guardrails. `pipeline.file_and_brief` stores the filing and calls `generate` only if the case has briefs enabled.

### Ingest

- Text files split into pages on a form feed or a line like `--- Page 3 ---`.
- PDFs are read with pdfplumber. If no page has text, ingest raises an error asking for OCR.
- Sentences are split by regex after `.`, `!`, or `?`. Common abbreviations (Mr., St., Apt., No., v.) do not split. Blank lines and numbered paragraph starts are hard boundaries.
- Each sentence gets an id `p{page}.s{index}`, counted from 1 on each page.
- The filing is stored as JSON (title plus pages of sentence text). Ids are rebuilt from position, so they stay stable.

### Extractors

Both return a draft `Brief`.

- `ClaudeExtractor` calls the Anthropic SDK with structured output (`Brief` is the output format). The prompt gives the filing with ids in brackets. The system prompt sets neutrality rules, attribution to the filer, and the citation and quote requirements. A refusal, a `max_tokens` stop, or an empty parse raises an error. The pipeline logs it as a failed generation. The model defaults to `claude-opus-5-5` and can be set with `BENCHBRIEF_MODEL`.
- `RuleExtractor` is deterministic and offline. It uses regexes for requests, evidence, dates, places, and people. It quotes whole sentences, so it cannot invent text. It has per-section caps (for example, 8 lines for what happened). It returns no flags.

`default_extractor` follows `BENCHBRIEF_EXTRACTOR`: `rules`, `claude`, or `auto`. In `auto`, Claude is used only if a credential variable is set and the SDK is installed.

### Rule checks

`checks.run_all` returns flags from four checks. See the README for the list. `merge_flags` puts rule flags first and drops model flags that cite the same set of sentences as a rule flag.

### Guardrails

`guardrails.enforce` checks every claim, evidence item, and flag. It validates ids, checks the quote against the cited sentences, and applies the non-neutral word list with the filer's-own-words exception. It returns the cleaned brief and a `GuardReport` with `kept` (a count) and `dropped` (section, text, reason). Dropped lines are never shown to the judge. They are stored for the pilot team and counted on the metrics page.

### Render

- `/` lists cases.
- `/case/{id}` shows filings grouped into repeat threads, the on/off toggle, and an upload form.
- `/filing/{id}` shows the brief. If the case is off, it redirects to the source page.
- `/filing/{id}/source` shows the full text with sentence numbers. Citations link here with `?hl=` so cited sentences are highlighted.
- `/metrics` shows pilot numbers.

Brief sections appear in a fixed order: flags ("Questions of fact to clarify"), what happened, when, where, who, what the filer asks, evidence. The page carries a notice that it does not weigh evidence, assess credibility, or suggest a ruling. It prints cleanly.

## Data model (schema.py)

All models are Pydantic classes.

| Model | Fields |
| --- | --- |
| `Claim` | `text` (one neutral line, attributed to the filer), `cites` (sentence ids), `quote` (verbatim excerpt) |
| `EvidenceItem` | `label`, `filed_by`, `claimed_to_show`, `cites`, `quote` |
| `Flag` | `kind` (`conflict` or `gap`), `text`, `cites`, `source` (`rule` or `model`) |
| `Brief` | `filer`, `filing_type`, then lists: `what_happened`, `when`, `where`, `who`, `asking_for` (all `Claim`), `evidence` (`EvidenceItem`), `flags` (`Flag`) |

`SECTIONS` fixes the order and labels of the five claim sections.

The schema has no field for a recommendation, a score, or a prediction. The model cannot return one in structured output.

## Repeat-motion grouping (repeats.py)

Goal: tell the judge when a filer has made a similar request before. It does not say the request is improper.

For a new filing:

1. Text to compare is the quotes of its `asking_for` claims joined together.
2. Candidate priors are filings in the same case, by the same filer (case-insensitive name match), with a brief, filed earlier (earlier date, or same date and lower id).
3. `content_words` lowercases, keeps words of three or more letters, removes a stop list (articles, pronouns, auxiliaries, and court words such as "please", "court", "order", "motion"), and strips a trailing `s`.
4. `similarity` is Jaccard overlap: shared content words divided by all content words in either text.
5. If both filings state the same filing type (case-insensitive), add 0.15.
6. A prior is a match if the score is at least 0.4. Scores are capped at 1.0.

Output: one match gives "This repeats the March 3, 2026 request." Two or more give "This is the 3rd similar request from this filer since March 3, 2026." The brief page links to each earlier filing. The case page groups each filing into the thread of its earliest match.

Limits: matching depends on the filer name being the same text on each filing. A filing with no extracted request (empty `asking_for`) is never matched. The 0.4 threshold and 0.15 bonus are untuned guesses. The pilot should spot-check them.

## Storage and audit

SQLite, one file, set by `BENCHBRIEF_DB` (default `./benchbrief.db`). Nothing is written elsewhere.

| Table | Columns |
| --- | --- |
| `cases` | `id`, `number` (unique), `caption`, `brief_enabled` (default 1) |
| `filings` | `id`, `case_id`, `filer`, `title`, `filed_on`, `doc_json`, `brief_json`, `guard_json`, `extractor`, `seconds` |
| `audit` | `id`, `at` (UTC), `event`, `case_id`, `filing_id`, `detail` (JSON) |

`seconds` on a filing is generation time, not review time.

Audit events written by the code:

| Event | When | Detail |
| --- | --- | --- |
| `filed` | filing stored | none |
| `brief_generated` | generation succeeded | extractor, kept count, dropped count |
| `brief_failed` | generation raised an error | error text |
| `brief_enabled`, `brief_disabled` | toggle changed | none |
| `brief_viewed` | brief page opened | none |
| `source_viewed` | source page opened | none |
| `review_done` | "Done reviewing" pressed | seconds, mode, useful |

A failed generation leaves the filing with no brief. The judge sees a notice and a retry button (`/filing/{id}/regenerate`).

## Pilot metrics

The brief page and the source page (when the case is off) each have a "Done reviewing" button. Page-side script starts a timer on load. The button posts the elapsed seconds.

- `review_done` stores `seconds` (clamped to 0 to 4 hours), `mode`, and `useful`.
- `mode` is `brief` when pressed on the brief page, and `no-brief` when pressed on the source page of a case with briefs off.
- `useful` is `yes`, `somewhat`, `no`, or empty. Only the brief page has the rating.
- `/metrics` shows review count, average, and median seconds per mode; usefulness counts; generation count and average generation seconds per extractor; the count of failed generations; and guardrail withholds grouped by reason.

Limits of these numbers:

- Time runs from page load to button press. It includes any time the judge spends away from the screen.
- There is no judge or session id in the audit log. The same filing can be timed more than once.
- The median for an even count is the upper middle value, not the average of the two middle values.
- Failed generations are counted across all time, not per case or per mode.
- Metrics do not separate pilot docket from other data. Use a fresh database for the pilot.

## Data residency

Filing text is sensitive. Options, from most to least contained:

1. **Fully offline.** `BENCHBRIEF_EXTRACTOR=rules`. No network calls at all. Briefs are crude (whole-sentence quotes) but cannot invent text. The guardrails and rule checks still run.
2. **Model in your own cloud tenancy.** The `ClaudeExtractor` docstring says to swap the client for `AnthropicBedrockMantle`, `AnthropicVertex`, or `AnthropicFoundry`, so data stays in your cloud tenancy. The constructor accepts a `client` argument for this. The code does not wire these clients up for you. This needs a small code change and a review of the provider's terms.
3. **Approved gateway.** The docstring also says to point `base_url` at your approved gateway. Same note: the constructor currently builds a default `anthropic.Anthropic()` client, so you pass your own client.
4. **Direct Anthropic API.** Works by setting `ANTHROPIC_API_KEY`. Filing text goes to the provider. Do not use this for real filings without legal and IT approval.

Other residency facts:

- The web app and database are local. There is no telemetry or outside call besides the extractor.
- The app has no authentication. Put it behind court network controls and add login before real use.
- `benchbrief.db` holds full filing text. Treat it as case records. Encryption at rest and retention are not handled by the app.
- Seeding with `--extractor claude` sends the fictional samples to the API. Real filings follow the same path.
