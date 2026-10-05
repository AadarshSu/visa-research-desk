# Visa Research Agent

Bounded, source-backed visa research. Every claim must be grounded in an official government source,
and the traveller must be told plainly when something could not be verified.

## Read these first

This file is loaded automatically; the documents below are not. **Read them before starting work.**

| File | The question it answers |
| --- | --- |
| [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md) | **Start here.** Where it stands, what to do next, what is known to be broken |
| [TODO.md](TODO.md) | The ordered queue of work, and why each item matters |
| [DECISIONS.md](DECISIONS.md) | Why it is built this way, what was tried and rejected — **start at its index** |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How it is built — trust model, pipeline, retrieval, discovery |
| [AGENTS.md](AGENTS.md) | How to contribute, and how to debug a corridor |
| [CORRECTIONS.md](CORRECTIONS.md) | Written-down diagnoses a run then contradicted — **read the rows for an area before changing it** |
| [OFSELF_FEEDBACK.md](OFSELF_FEEDBACK.md) | What integrating with Ofself showed about **their** platform — add to it whenever the platform surprises you |
| [docs/ofself/](docs/ofself/README.md) | **Before any Ofself work:** Ofself's own guides, as dated snapshots — where they disagree with the live platform, OFSELF_FEEDBACK.md wins |

**Each fact has one home.** When one of these files summarises another, the two drift, and the drift
is what has wasted the most time here.

## The goal

**The objective is providing the right information; latency and cost are the constraint it fits
inside** (the owner, entry 147). A country is built **offline** — its page corpus and page-text
index — and a corridor answers from that store. **The corpus holds what every traveller shares; live
search fetches what this traveller needs and stays the minority** (entry 148). The order is
correctness, then optimisation, then expansion. **Offline build time and one-time cost are
acceptable** for anything that makes each live request better (entry 184).

**This phase (the owner, entry 247): scale and speed are where the owner wants them.** Optimisation
and expansion are parked in [TODO.md](TODO.md) and come back only as required — do not propose
either unprompted. Next, **with the owner and not before**, is a travel-readiness app for one
traveller, built around the dates they enter (TODO item 80, entry 251); its open decisions are
the owner's. Where each earlier goal ended is the table at the top of TODO.

**Model calls go through Ofself Personas, from now on** (`model_route: personas`, entry 188); OpenAI
has been out of credit since 2026-09-16, so `model_route: openai` needs a top-up.

**Standing decisions** — each argued in the entry named; current numbers live only in
PROJECT_HANDOFF.md.
- Search stays on every corridor, and so does the purpose query. Do not re-propose conditional search
  without a new argument against both measured shapes (entries 159, 160).
- Do not build a truth set, a correctness grader or an accuracy metric without asking — correctness
  is checked by the owner outside this repository (entries 68, 147).
- `openai_reasoning_effort` is `low`; do not lower it without an accuracy measurement (entry 177).
- Grade a model-latency or cost change on several runs, never one, and price it in both seconds and
  dollars (entries 144, 145).
- Read stage timings as an aggregate over corridors; a stage is the span between two numbered steps of
  `_resolve`, not the act it is named after (entries 142, 143).
- Measure the selector pool's gate by the roles it hides at the margin, never by whether a relevant
  page exists outside it (entry 128).
- The selector experiment is closed: the model selector stays (entry 106).
- If a build comes back on one host, read `authority_domains.yaml` before touching crawl settings
  (entries 107, 110).
- Read entries 78–87 before touching discovery ranking; five of them correct the ones before them.
- The numeric stored-text lift in `combined` stays off (no country passes
  `DEFAULT_TEXT_COVERAGE_BAR`). Do not turn it on without a measurement that has no adjudicator in it
  — grade the shortlist, not the plan (entry 81).
- A fix that changes what a plan may conclude is the owner's decision (entries 206–209).

## Rules that must not be broken

Repeated here because each is a place where a plausible-looking "simplification" produces a serious
defect.

### Trust

- **Officialness is a property of who controls the domain, never of how a page reads.** Enforced when
  configuration loads, after every HTTP redirect, and after every meta-refresh forward. A change to
  retrieval must preserve all three.
- **Never disable TLS verification.** Incomplete chains are fixed by bundling the missing intermediate
  in `config/tls_intermediates/`, each verified to chain to an already-trusted root.
- **Web search belongs only in `discovery/`, and only as a candidate generator.** Nothing search
  returns becomes evidence until it passes the domain-trust rules.
- **A domain is trusted automatically only when it is the destination's *own* government** —
  governmental **and** under that country's own top-level domain. Both halves are load-bearing:
  without the first a commercial insurer under `.fr` gets in; without the second the US embassy's page
  about Vietnam does. Never relax it to "looks official". At most five domains are used, and the
  relaxed one-query evidence bar applies only where "own government" is two independent signals
  (entry 22). If no such domain is found, nothing is fetched and the destination is refused.
- **Which domains are trusted is committed data** — `config/authority_domains.yaml`, generated offline
  by `visa-discover registry` (entry 38). A country missing from it is **refused**, never bootstrapped
  live.
- **Do not fix a government with no hostname marker by widening `GOVERNMENT_PATTERNS`.** Adding `.de`
  would trust every commercial site in Germany. The fix is a `reviewed` domain in committed data
  (entry 34). Adding a marker a government really uses (`gv`, `gub`, `canada.ca`) is a correction
  within the rule — and it must also go in `trust.SUFFIX_MARKER_LABELS` (entry 65).
- **`reviewed` means a person decided, not that a machine confirmed (entry 111).** Each entry says
  its tier: independent evidence (Wikidata `P856`/`P17`, or a TLS certificate naming the
  organisation), the page's own words, or the reviewer's judgement. Review a domain by asking Wikidata
  about the *organisation*; `unconfirmable` is what a search turned up, not a shortlist of real
  addresses (entry 110). `iom.sk` stays refused — the International Organization for Migration passes
  the own-TLD half and fails the governmental one.
- **Every reason in `withheld_domains` must be true.** A domain under the destination's TLD with no
  recognised marker "could not be confirmed as an authority, and may be a real one" — never "not a
  government domain" (entry 33).
- **The EU is a supranational tier for one role — the owner's decision, entry 201.** For a Schengen
  member the EU may answer **the visa decision and nothing else**. Its domains live in
  `config/supranational_authorities.yaml`, beside `trusted_domains` and never in it; a page from them
  chosen for any other role loses it (`confined_to_permitted_roles`). A plan cites it as the EU's.
  Ireland and Cyprus are not members until checked. EUR-Lex's render exception
  (`challenge_script_hosts`) extends to no other page. **Do not add a union, a role or a challenge
  host without a decision entry.**

### Refusal and blocks

- **Refusing is a correct output.** A plausible but wrong document checklist is worse than no answer.
  This includes **failing model calls**: a failed role adjudication refuses the corridor rather than
  falling back to the heuristic, which is the decider that produced Brazil's wrong checklist at full
  confidence (entries 15, 31) — and so does a failed candidate selection, and one naming no page
  (entry 258). A resolution built while a page it chose to read failed only "for now" is never
  stored.
- **Never work around an authority that blocks automated retrieval.** Do not spoof a user agent, do
  not retry to get around a rate limit, do not point the renderer at a page an authority refused. A
  block means *we cannot independently retrieve and verify it here* — mark the source inaccessible,
  say so, and let the role go unfilled.
- **First establish that it *is* a block, from what the page states (entries 41, 109).** A challenge
  (`cf-mitigated: challenge`, "enable JavaScript and cookies") is a capability test: the authority
  stated nothing, and our renderer may answer it **under our own user agent**. A challenge is
  `challenged`, never `blocked`, and **may never resolve a corridor**. A body saying "you have been
  blocked" or "attention required" is a refusal, checked **before** any challenge marker. A `401`, a
  bare `403` and a `429` are refusals. Challenges that need `challenges.cloudflare.com` stay
  `challenged` (entries 179, 202).
- **A blocked page may be named, never read (entry 27).** Its URL may be reported so the traveller can
  open it; it may never be read, inferred from, retried, or counted as a source. `UnreadableAuthority`
  is not a `ConfiguredSource`, and the packet carries its URL and no text.
- **Naming it stays narrow (entries 32, 57).** Only a `401`/`403` may qualify a corridor — not a `429`,
  and not a challenge — and only on a page judged a credible `visa_decision` candidate.
  `_decision_blocking` asks a model over the refused page's **address and label only**;
  `build_blocked_packet` has no parameter for page text, and must never grow one. It fails closed after
  two attempts. Every block is still *reported*.
- **Honest client, not anonymous client (entry 35).** `robots.txt` is read and obeyed (entry 36).
  Client-side retrieval through the traveller's browser is **not approved** — argue it in a decision
  entry before any code (TODO item 4).
- **Pacing is not the forbidden retry.** A build slows a host that stops answering and gives up after
  six transport failures in a row (entry 139), and a later build asks again for a page that failed
  transiently (entry 207). Neither gets past anything an authority *stated*; a `401`/`403`, a
  `Disallow` or a challenge stays final.
- **A `Disallow` is reported and may never resolve a corridor (entry 36).** `disallowed` sits outside
  `blocked_urls()` and `persistent_refusals()`. The reason must be true of what was seen: an unreadable
  policy "could not be read", never "does not permit".

### What a plan may say

- **A questionnaire is an answer, and may be named, never driven (entries 59, 60).** A page read and
  judged to *ask* a question is named beside the question it settles. **A tool never fills its role**:
  no source is invented, `application_document_source_ids` stays empty, and a plan naming a checklist
  tool may not designate a checklist source. Only `visa_decision` changes whether a corridor resolves.
  Only the adjudicator names a tool, on a page whose text it was given; an invented id is discarded.
  A plan naming a decision tool cannot state `visa_required` and can never be `verified`. Driving the
  tool is out of scope — entry 59 argues it against the strongest case.
- **Guidance an authority contracts out may be named, never read, never believed (entry 89).**
  Trusting or crawling a contractor (`vfsglobal.com`) was declined; do not add one to
  `authority_domains.yaml` — `config/service_providers.yaml` is separate on purpose. A delegate needs
  both an approved page linking it **and** its registrable domain on the reviewed list. The model
  selects a recorded `delegate_id`; it never supplies a URL. Naming one fills nothing.
- **A visa decision that could not be confirmed must be `null`, and the application enforces that** —
  `decision_is_unverified` overrides the model, and such a plan can never be `verified` (entry 27).
- **A visa-free plan is an entry plan, built only on a *stated* decision (entries 95, 96).**
  `application_steps` carries entry steps, `requirements` is empty. Never from a tool, a blocked page
  or an unverified decision. **There is no minimum number of entry steps** — do not add one "so the
  panel is not empty". `where_to_apply` may be `None`, never forced to it (a visa-free American still
  needs a UK ETA).
- **The silences the owner allowed, and their bounds — all in `extract_visa_plan.txt`:**
  - **8e (entry 172):** absence from the authority's own complete visa-*required* list means no visa —
    only if the whole list was read, every name and footnote checked, it lists only who needs a visa,
    and no other source says a visa is needed.
  - **8f (entries 197, 206):** absence from the authority's complete *exemption* list means a visa is
    required — the whole list read, not a list of examples; an entry for a passport type the traveller
    does not hold does not count; it must be this trip's list; any exemption the traveller might meet
    keeps it null. **An exemption list never decides "no visa".**
  - **8g (entry 199):** a visa on arrival is a visa, where every route the sources describe issues one;
    any visa-free way in keeps it null.
  - **8h, 8i (entries 208, 209):** a "no visa" stated before and after an announced change is not held
    back by its start date; an announcement naming the country may be read with a later notice from
    the same government that it took effect. Both cite every page they rest on.
  - **8k (entry 250):** a decision may hold on a fact about the trip the profile lacks — layover,
    length of stay, leaving the transit area, onward country. One stated, uncontradicted side goes in
    `visa_required`, the fact in `decision_condition`, shown beside the decision; such a plan is
    never `verified`. **8l:** an exemption for another purpose says nothing about this trip, either
    way. **A list of who may enter or stay never decides transit** (8f, roles 7b).
  - **Do not widen any of them to another kind of silence without a decision entry.**
- **A checklist is linked, never copied (entry 211).** A plan lists no documents; it links the
  authority's checklist, and names an unread one with its link only where the government page's own
  words say it is this trip's (entry 213). With no designated checklist, nothing may be listed
  (`validate_absent_checklist`'s first clause).
- **Never show a traveller an unverified claim that would alarm them if wrong (entry 6).** The
  `conflicts` field was deleted (entry 30) — **do not add it back.** A disagreement between pages is
  an unresolved question.
- **Never** add application submission, appointment booking, form filling, or any claim that approval
  is guaranteed.

### Storage

- **What may be stored is a *page*, never an *answer* (entry 44).** A country's page corpus is stored;
  which page answers a traveller stays live. A plan is a rendering; only the model's *draft* may be
  reused, for byte-identical inputs inside the page TTL, and every request still
  validates and grades (entry 178). A `visa_rule` decision table is deliberately not built.
- **Stored page text ranks; it never speaks (entries 78, 83).** `PageTextStore.rank` returns URLs and
  scores; `TextMatch` has no body field. The one accessor returning bodies, `text_for_selection`,
  feeds `discovery/selection.py`, whose `Selection` type holds ids and no prose. **Do not add a
  `snippet()`, a body field, or a "just for debugging" accessor**; a second caller wanting bodies must
  argue for itself. A page it ranks is fetched live before a word reaches a plan.
- **A corpus miss must never be answered by quietly falling back.** The candidate set is `corpus ∪
  live search`, with search on every corridor, so nothing is conditional (entries 47, 173).
- **A corpus may order work on what it lacks, never on a traveller (entries 44, 139).** Do not add
  `{residence}` to `corpus_queries` — a traveller dimension enters the offline job only where it can be
  covered exhaustively.
- **A stored row records when the evidence was retrieved, never when the row was written (entry 4).**
  A failed refresh serves cached text flagged `stale` and keeps its original `fetched_at`; only a `304`
  moves it. Past `source_maximum_stale_hours` a page is **refused**. A content-hash change **marks** a
  source and never auto-swaps a role-bearing one.

### Code

- **LangGraph is declined, not deferred (entry 29).** The pipeline is linear and the trust checks are
  Pydantic validators that cannot be skipped. LangChain stays for structured output only.
- **Tests must not touch the network or an LLM.** Use the `transport=` and `now=` seams and the fake
  generators; see `tests/discovery_site.py`.

## Before finishing a session

Run `ruff check .`, `ruff format --check .`, `mypy`, `pytest`, then update the handoff: current state
and known problems in `PROJECT_HANDOFF.md`, any decision and its reasoning in `DECISIONS.md`, what is
now next in `TODO.md`. Add a row to `CORRECTIONS.md` whenever a run contradicts what a file said.

Do not record a problem as fixed unless it is fixed, or a result as verified unless it was run. These
files are read by someone with no other context. **Check a documented claim against the code before
carrying it forward**, and **measure a proposed fix before implementing it** — prefer a run, a test
or a printed result over a careful reading.

**Commits:** one subject line in conventional-commit form — **`type: lowercase description`**, where
`type` is `feat`, `fix`, `docs`, `refactor`, `test`, `perf`, `build`, `ci` or `chore` — no body, no
attribution trailers, on `main`. **Every commit takes a type, not only docs-only ones**: a
code change with its docs is `feat:` or `fix:`. One concern per commit, **with the documentation for
that concern in the same commit** — `docs:` is for when documentation is the only thing changing.
**Ask the owner whether a commit is done before pushing it, and wait for the answer.** Feedback
after a push costs a second commit for one concern; before it, the change folds into the first.
`.githooks/commit-msg` rejects a subject that does not fit; enable it once per clone with
`git config core.hooksPath .githooks`.

## Running it

```bash
.venv/bin/uvicorn visa_research_agent.api.app:create_app --factory   # the app
.venv/bin/visa-discover corridor --destination japan --nationality IN --from GB
.venv/bin/visa-discover corpus --country CA     # build a country's offline page corpus
.venv/bin/visa-discover eu-store               # before a rebuild, and after the EU regulation changes
.venv/bin/visa-discover pagetext --backfill    # index the text the retrieval cache already holds
.venv/bin/visa-discover pagetext --purge-interstitials  # drop stored bodies that are a bot-check page
.venv/bin/visa-discover coverage --country NL  # does the store hold the answer? no network, no model
.venv/bin/visa-discover reports                # travellers' problem reports, each with its re-run line
.venv/bin/visa-discover regions --geonames admin1CodesASCII.txt  # regenerate the region list (entry 252)
.venv/bin/visa-discover advisories --only GB,IN  # survey who publishes travel advice; a draft to review (entry 260)
.venv/bin/visa-discover advisory-links --only GB  # build and check each government's advice links (entry 260)
.venv/bin/visa-discover country-names          # regenerate country names in publisher languages, from Wikidata
.venv/bin/visa-discover cities --geonames cities15000.txt  # regenerate the weather panel's cities (entry 261)
.venv/bin/visa-discover climate                # build destination cities' monthly averages from NOAA (entry 261)
.venv/bin/visa-discover selection-recall       # does the corridor find it? no network, no model
.venv/bin/visa-discover contention --destination czechia --nationality IN --from GB --outside-pool \
    --role document_checklist                  # curate an oracle row, including outside the pool
```

Secrets live only in `.env`. Reviewable policy — source mode, extraction mode, model route, cache TTL,
stale ceiling — is committed in `config/runtime.yaml`. **Start builds and anything that renders from
the owner's Terminal panel**: Chromium cannot start in the sandboxed shell.

**`coverage` and `selection-recall` answer different questions and must not be merged**: whether the
store holds the answer, and whether the corridor then finds it.

**Clearing state when testing.** Clear `var/cache/` for a retrieval change, `var/corridors/` for a
discovery change, `var/plans/` for a change to how the plan call is sent — or a pre-change result is
served. **Clear it for both arms of a comparison or for neither** (entry 136). Copy `var/recall/`
aside before re-running a baseline; a re-run overwrites it (entry 118). **`var/corpus/` and
`var/pagetext/` are stores, not caches** — never cleared between runs; rebuilding costs search quota.

**If a country ranks strangely, check for stored bot-check pages before blaming the vocabulary**
(entry 117).

**Both paid providers fail in their own way.** OpenAI's `429 credit_balance_exhausted` raises
`AdjudicationQuotaExhausted` and is not retried (entry 79). Brave answers `HTTP 402` both when out of
credit and when queried too fast; the program tells them apart (`SearchQuotaExhausted` /
`SearchThrottled`, entry 74). A search outage falls back to a stored corpus and says so; with no
corpus it refuses, because *we could not look* must never become *there is nothing to find*.
