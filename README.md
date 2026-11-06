# Bench Brief

Bench Brief turns a self-represented litigant's filing into a one-page brief for the judge.

The brief shows:

- what happened, when, where, and who was involved, in the filer's account
- what the filer is asking the court to do
- evidence the filing says is attached or referenced, and what the filer says it shows
- neutral flags for conflicts and gaps inside the filing
- a note when the filer has made a similar request before

Every line cites a sentence id such as `p2.s4`. The id links to that sentence in the full filing.

The brief does not recommend a ruling, score credibility, or predict an outcome. It can be turned off for any case. It is meant to run on court systems, not on a hosted service.

Why it exists: Indiana has fewer judges per capita than average. About 80% of litigants before the Allen County magistrates have no lawyer. Self-represented filings are often long and unstructured. The pilot idea is one docket, measuring time per case. See `docs/PILOT_PLAN.md`.

**Status: prototype. Not for production use without court IT and legal review.**

The sample filings in `samples/` and the cases in the seed data are fictional. The names, cause numbers, and facts are not real.

## Quickstart

```
pip install -r requirements.txt
python -m benchbrief seed --extractor rules
python -m benchbrief serve
```

Open http://127.0.0.1:8000.

`seed` loads three fictional cases into `benchbrief.db`. The `rules` extractor runs offline and needs no API key. `serve` binds to 127.0.0.1:8000 by default. Use `--host` and `--port` to change that.

## Using Claude

The `claude` extractor sends the filing text to the Anthropic API. The default extractor reads these environment variables:

| Variable | Meaning |
| --- | --- |
| `ANTHROPIC_API_KEY` | Credentials for the Anthropic SDK. `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_PROFILE` also count as credentials in `auto` mode. |
| `BENCHBRIEF_EXTRACTOR` | `auto` (default), `claude`, or `rules`. |
| `BENCHBRIEF_MODEL` | Model name for the Claude extractor. Default is `claude-opus-5-5`. |
| `BENCHBRIEF_DB` | SQLite file path. Default is `./benchbrief.db`. |

What the modes do:

- `rules`: always use the offline rule-based extractor.
- `claude`: always use Claude. Fails if the SDK or credentials are missing.
- `auto`: use Claude if a credential variable is set and the SDK imports. Otherwise use the rule-based extractor.

Example:

```
export ANTHROPIC_API_KEY=...
export BENCHBRIEF_EXTRACTOR=claude
python -m benchbrief seed
python -m benchbrief serve
```

The seed and web app also use `BENCHBRIEF_EXTRACTOR`. Filings uploaded through the web app use the default extractor.

Using Claude means filing text leaves the machine unless you route it through your own cloud or gateway. See "Data residency" in `docs/ARCHITECTURE.md`.

## CLI

```
python -m benchbrief serve [--host 127.0.0.1] [--port 8000]
python -m benchbrief seed [--extractor auto|claude|rules]
python -m benchbrief brief PATH [--extractor auto|claude|rules]
```

`brief` reads one `.txt` or `.pdf` file and prints JSON with two keys: `brief` (the guarded brief) and `withheld` (the lines the guardrails dropped, with reasons). It does not use the database.

Plain-text filings can mark page breaks with a form feed or a line like `--- Page 2 ---`. PDFs need a text layer. A scanned PDF is rejected with a message asking for OCR first. Bench Brief does not guess at unreadable pages.

## Tests

```
python -m unittest discover -s tests -v
```

The tests use fake model clients and the rule-based extractor. They need no network or API key. `httpx` (in `requirements.txt`) is needed for the web app tests.

## Project layout

| Path | Purpose |
| --- | --- |
| `benchbrief/__main__.py` | CLI: `serve`, `seed`, `brief`. Holds the fictional seed docket. |
| `benchbrief/ingest.py` | PDF or text to pages and sentences with ids like `p2.s4`. |
| `benchbrief/schema.py` | Pydantic data model: `Claim`, `EvidenceItem`, `Flag`, `Brief`. |
| `benchbrief/extract.py` | `ClaudeExtractor`, offline `RuleExtractor`, and the choice between them. |
| `benchbrief/checks.py` | Deterministic conflict and gap flags. |
| `benchbrief/guardrails.py` | Citation check, quote check, neutral-language filter. |
| `benchbrief/repeats.py` | Repeat-motion matching for the same filer. |
| `benchbrief/pipeline.py` | Ties the steps together. Stores filings and briefs. |
| `benchbrief/store.py` | SQLite schema, audit log, per-case toggle. |
| `benchbrief/app.py` | Starlette web app and routes. |
| `benchbrief/templates/` | HTML templates: docket, case, brief, source, metrics. |
| `samples/` | Fictional sample filings. |
| `tests/test_benchbrief.py` | Unit and app tests. |
| `docs/ARCHITECTURE.md` | Pipeline, data model, storage, metrics, data residency. |
| `docs/PILOT_PLAN.md` | One-docket pilot plan. |

## How the guardrails are enforced

Every draft brief goes through `guardrails.enforce` before it is stored or shown. This applies to both extractors. Lines that fail are dropped, not shown. Dropped lines are recorded in the filing's guard report and counted on the metrics page.

In `guardrails.py`:

- **Sentence-id validation.** Every claim, evidence item, and flag must cite at least one id. Each id must exist in the filing. A claim with no citation or an unknown id is dropped.
- **Verbatim quote check.** Claims and evidence items carry a quote. After normalizing case, quotes, and punctuation, the quote must appear in the cited sentences. A quote that cannot be found is dropped. This catches invented facts.
- **Non-neutral word list.** A list of whole-word patterns covers credibility, merit, outcome, and characterization terms, such as `credib*`, `lying`, `frivolous`, `likely`, `clearly`, `admits`, and `should (be) granted/denied`. A claim or evidence line containing one is dropped.
- **Filer's-own-words exception.** For claims and evidence, a listed word is allowed if it also appears in the claim's quote. So "Filer states he is clearly lying about the rent" survives when the filer wrote those words. The same word in Bench Brief's own wording does not survive. Flags have no quote, so any listed word drops a flag.

In `checks.py` (deterministic flags, run on every filing, independent of the model):

- different dates for the same event noun (for example, two dates for "the incident")
- different amounts for the same money noun (for example, rent)
- a second or later event, or a relative time like "last week", with no calendar date
- no specific request to the court found

Rule flags are marked `source: rule`. Model flags that cite exactly the same sentences as a rule flag are dropped as duplicates.

Other guardrail behavior in code:

- Failed or refused generations are logged as `brief_failed`. No partial brief is shown. The page tells the judge to read the filing directly and offers a retry button.
- The system prompt for Claude forbids recommendations and characterization, and requires attribution to the filer. The prompt is a request. The guardrails are the enforcement.
- The per-case toggle turns briefs off. When off, new filings get no brief and the case pages send the judge to the source text.

What the guardrails do not do: they check that quotes exist and that wording avoids a fixed word list. They cannot tell whether a line is a fair summary, or whether the choice and order of lines favors one side. A human still has to read.

## Limits

- No login, roles, or user identity. The audit log records events, not people.
- The word list is English and finite. Evaluative framing that avoids the list passes.
- Sentence splitting is regex-based. Unusual formatting can split or merge sentences.
- The rule-based extractor quotes whole sentences and is crude. It is for demos, tests, and offline installs.
- Only text and text-layer PDFs. No OCR.
