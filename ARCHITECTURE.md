# Architecture

How the system is put together and why it is shaped this way. For what it does and how to run it,
see [README.md](README.md); for the reasoning behind each choice, [DECISIONS.md](DECISIONS.md) by
entry number. Current measurements live in [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md).

---

## The trust model

> **Officialness is a property of who controls the domain, never of how a page reads.**

A page is evidence because it sits on a domain belonging to the destination's **own government**.
Convincing prose earns nothing, so an unofficial page is *unreachable* rather than merely
low-scoring.

"Own government" is **governmental** *and* **under the country's own top-level domain**. The first
half is a list of hostname markers (`gov`, `go.xx`, `gouv.xx`, `gob.xx`, `gv.xx`, `gub.xx`, `govt.xx`,
`gc.ca`, `canada.ca`, `admin.ch`, `europa.eu`), matched only at a label boundary anchored to the end,
so it cannot be spoofed by putting "gov" in a name. It is a sound *sufficient* test — it means "inside
a registry-controlled government namespace" — and a wrong *necessary* one: 16 of 51 governments
measured use no marker (entry 65). For those the fix is a **reviewed domain in committed data, never
a wider pattern** — adding `.de` would trust every commercial site in Germany, and for exactly those
countries the own-TLD test is the only other signal (entries 33, 34).

**Two lists must move together.** `bootstrap.GOVERNMENT_NAMESPACE_LABELS` asks *is this a government
namespace*; `trust.SUFFIX_MARKER_LABELS` asks *is it too broad to trust whole*. A label in the first
and missing from the second makes trusting one authority trust its whole government. A test asserts
the containment (entry 65).

### Where it is enforced

| When | What is checked | Code |
| --- | --- | --- |
| Configuration loads | Every configured source URL is on a trusted domain, or the registry fails to load | `DestinationConfig.validate_route` in `domain/models.py` |
| After an HTTP redirect | The **final** URL is still trusted | `LiveSourceFetcher._fetch_source` |
| After a meta-refresh forward | Both the forward target and where it lands are trusted | `LiveSourceFetcher._read_document` |

`domain/trust.py` is short and worth reading in full. `host_is_within` matches exactly or on a **dot
boundary** (`london.mfa.gov.sg` is within `mfa.gov.sg`; `notmfa.gov.sg` and `mfa.gov.sg.evil.example`
are not). `is_bare_public_suffix` rejects entries like `gov.sg` that would trust every site beneath
them; it is a heuristic, not a real public suffix list.

### Who to believe is committed data

`config/authority_domains.yaml` is generated offline by `visa-discover registry`, read once at
construction, and never re-derived per request (entries 34, 38) — a live bootstrap made "who is
Germany's government" vary between runs. A country missing from it is **refused**. `auto_trusted_domains`
still applies the rule; a person may add domains under `reviewed`, which survives regeneration and
records its evidence tier (entries 39, 111).

`bootstrap.py` proposes domains from search, with safeguards because this is the one place search is
not already bounded by an approved domain: a denylist of commercial agencies, corroboration by two
independent queries (relaxed to one only where "own government" is two independent signals, entry
22), bare public suffixes rejected, and the domain must belong to the destination. **At most five**
domains are used per country, ordered by authority hint (`emb`, `consul`, `immi`, `mofa`), then
corroboration, with `reviewed` first — each costs three searches. Everything left out is reported
with a reason, and each reason must be true:

| Reason | The problem it names |
| --- | --- |
| this destination's own government, not among the best evidenced | the cap bit |
| another country's government | it describes its own citizens' rules |
| under this destination's TLD, but **no marker this rule recognises** | the rule's own limit — it may be a real authority (entry 33) |
| neither governmental nor under the destination's TLD | an agency or an unrelated site |

### Delegated services

Some steps happen on a contractor's domain (VFS Global, TLScontact), which cannot pass domain trust.
Such a page may be **named, never read or cited** (entry 89): the crawler records the `href` off an
approved government page, its registrable domain must be in `config/service_providers.yaml`, and the
model may only pick a recorded `delegate_id`. It fills no role. (Hand-configured destinations use the
older `appointed_by` form of the same idea.)

### A supranational tier: the EU, for a Schengen member's visa decision

Who needs a short-stay Schengen visa is EU law, so the EU may answer **that one role** for the 29
Schengen states (entry 201). Its reviewed domains are in `config/supranational_authorities.yaml` and
reach a member's `DestinationConfig` through `with_union` as `supranational_domains` — beside
`trusted_domains`, never in it — passing every fetch, redirect and render check a trusted domain does.
`confined_to_permitted_roles` strips any other role from an EU page; `derive_authority` names it the
EU's; `challenge_script_hosts` lets a render of an EUR-Lex page reach AWS's challenge-token host and
nothing else. The pages come from a shared store that `visa-discover eu-store` refreshes offline, and
a member corridor reads them on every run (`always_read`), live, before quoting.

---

## The plan pipeline

```
DestinationConfig
      │
      ▼
SourceFetcher.fetch(destination) ──▶ RetrievalReport { fetched[], failures[] }
      │                                     fixtures → snapshots on disk
      │                                     live     → HTTP, with cache and trust checks
      ▼
VisaPlanExtractor.extract(destination, traveller, report) ──▶ VisaPlan
                                     fixture → deterministic golden output
                                     openai  → one structured model call
```

**Two routes return the same plan.** `POST /visa-plans` answers with it; `POST /visa-plans/stream`
sends newline-delimited JSON — a `stage` event as each step starts (the resolver's phases through
`ResolutionTrace.on_phase`, then `retrieve` and `write` from the service), then one `plan`, `refusal`
or `error` event. Both call `research_plan`. **Only progress streams**: a stage carries its name and
nothing it found, and the plan is sent whole after every validator has passed (entry 226).

`research/service.py` is the whole orchestration. `api/dependencies.py` chooses implementations from
`runtime.yaml`, and the traveller comes from an injected `TravellerSource` (`api/traveller.py`).
Every model call — selection, role adjudication, the refused-page judgement, the plan — reaches the
model by one of two routes chosen by `model_route`: OpenAI directly, or Ofself Personas at
`capabilities: []`, one plain call on Ofself's account (`research/personas.py`, entry 188). Prompts,
packets, schemas and reasoning effort are the same on both.

### Evidence outcomes

Every source resolves to exactly one outcome. Two carry content; six do not.

| Outcome | Usable | Meaning |
| --- | --- | --- |
| `ok` | yes | Fetched fresh, or cache-fresh inside the TTL |
| `stale` | yes | Refresh failed; cached text served inside the stale ceiling |
| `untrusted` | no | Final URL left the approved domains |
| `unreachable` | no | Timeout, connection error, or error status |
| `unusable` | no | Retrieved but not evidence — client-rendered shell, unreadable PDF, JSON API, too little text |
| `blocked` | no | The authority refused automated retrieval (`401`, `403`, `429`) |
| `disallowed` | no | `robots.txt` excluded this client, or could not be read |
| `challenged` | no | A browser check was not answered — the render failed, or the run's renders or the host's three tries were spent |

**A `403` is a challenge or a refusal, decided from the response (entries 41, 73, 75, 109).** A body
saying "you have been blocked" or "attention required" is a refusal, checked first. A challenge —
Cloudflare's `cf-mitigated: challenge` or `_cf_chl_opt`, Azure's WAF JS challenge in the body — states
nothing, so the renderer answers it under our own user agent; one it cannot answer stays
`challenged`, never `blocked`, never a resolution. About half the challenges met need
`challenges.cloudflare.com`, which the render gate refuses (entries 179, 202).

**`blocked` is bounded twice, because a block can resolve a corridor** (entry 32). Only
`PERSISTENT_REFUSAL_STATUS_CODES` — `{401, 403}` — may qualify one; a `429` is a rate limit. And the
refused page must plausibly have held the decision: `ResolvedCorridor.decision_blocking_urls` holds
those, judged by `_decision_blocking` — a model call over the refused page's address and label only,
since nobody read it (entry 57). With no adjudicator configured a keyword test runs instead.
`inaccessible_domains` still carries every refusal.

**`disallowed` never resolves a corridor** (entry 36): `disallowed_urls()` sits outside
`blocked_urls()` and `persistent_refusals()`. A `403` was observed on the page; a `Disallow` covers a
path we chose not to request. A policy that could not be read (`5xx`, oversized, or a web page served
at `/robots.txt`, entry 119) is reported as exactly that.

### An official tool that answers by asking

A page served willingly and read can still not *state* an answer, because the authority publishes it
as a questionnaire (`gov.uk/check-uk-visa`). `ResolvedCorridor.interactive_tools` carries these into
`VisaPlan.official_tools`, each naming the `GuidanceTopic` it settles, and the plan offers each beside
its question — the decision panel, the documents panel, or the caveats (entries 59, 60).

- **Only `visa_decision` changes whether a corridor resolves**; such a plan is `partial` with
  `visa_required` null.
- **A tool never fills its role**: nothing is cited, and for `document_checklist`
  `application_document_source_ids` stays empty.
- **Only the adjudicator names one**, on a page whose text it was given; an invented id is discarded;
  a tool is dropped for a role a source already answers; the URL is checked against approved domains.
- **Driving the tool is out of scope** (entry 59).

### Grading and refusal

`research/outcomes.py`, shared by both extractors.

- A destination's `required_source_ids` plus the checklist are load-bearing; one missing **refuses
  before the model is called**, and the API answers `503` naming the missing evidence.
- **A blocked decision page can carry a plan.** The corridor resolves with `decision_is_unverified`,
  the decision is forced to unknown in the extractor, and the page is named under
  `unavailable_sources` (entry 27). A decision merely *not found* refuses.
- **A missing checklist does not refuse** (entry 14). `VisaPlan` rejects a plan listing requirements
  with no designated checklist source, and one silent about the gap. The plan never says none exists —
  only that none was found among the pages read — and names a likely unread checklist with its link
  (entries 153, 154, 213). **A checklist is linked, never copied** (entry 211).
- **A visa-free plan is an entry plan** (entries 95, 96, 98): on a decision a source *stated*,
  `application_steps` holds entry duties with no minimum, `where_to_apply` may be `None`, and
  `requirements` is empty.
- Any failure or stale source → `partial`, said in one line above the guidance. Everything current
  and a stated decision → `verified`. A null decision is never `verified` (entry 150).
- A claim carries citations, never a quote: quoting was removed because a checked quote could still
  be a useless fragment (entry 227). Each cited source carries its content hash and why discovery
  chose it (entry 157).

### The trust boundary at the model

The model never originates a trusted field. Page text enters the prompt under `untrusted_content`;
afterwards the application rebuilds `sources`, `last_checked`, `status` and
`application_document_source_ids` from its own records, and `VisaPlan`'s validators reject any
citation of a source that was not fetched. The prompts are `prompts/*.txt`.

---

## Live retrieval

`research/live_sources.py` fetches only URLs that passed domain trust.

**Caching** — one JSON file per URL under `var/cache/`, written atomically. Below
`source_cache_ttl_hours` it is served without a request; above it, revalidated with `If-None-Match` /
`If-Modified-Since`. A failed refresh serves cached text **flagged stale and keeping its original
retrieval time**; past `source_maximum_stale_hours` it is refused. The cache keeps extracted text, so
a change to extraction needs `var/cache/` cleared.

**Documents, not just pages.** PDFs are read (recognised by content type or a `.pdf` path), and one meta-refresh forward is followed, up to two hops, each trust-checked. Tables are read with
each value under its heading (entry 205). Provenance records the document actually read.

**Rendering** (`research/rendering.py`, behind `PageRenderer`). `render_mode: on_demand` renders a
page only after an ordinary fetch came back below the readable floor, as translation placeholders
(`looks_untranslated`), or as a challenge. **It widens no trust**: every request the page makes is
aborted unless its host is approved for the destination, and where it lands is re-checked like a
redirect. It needs the optional `[render]` extra; selecting it without the extra raises. Budgets are
per caller: 400 for an offline build, 12 for a corridor's crawl, **5** shared by the pages that
become evidence, most promising first (entry 212); a host that fails three times in a row is offered
no more (entry 135).

**TLS.** Missing intermediates are bundled in `config/tls_intermediates/`, each chaining to a trusted
root. Verification is never disabled; tests assert expired, self-signed, mismatched and unknown-CA
certificates are rejected.

**Politeness** (`research/robots.py`). Each origin's `robots.txt` is fetched once, re-read after 24
hours, and consulted before every request by crawl and retrieval alike, matching RFC 9309 (the stdlib
parser supports neither `*` nor `$`). A skipped page is `disallowed`, never an absence.

---

## Source discovery

`discovery/` runs two ways on the same code: **`visa-discover`**, a command with a person reading the
result; and **in the request path** under `destination_mode: automatic`, where a destination nobody
configured is researched when a plan is asked for (`discovery/automatic.py`, entry 19).

### The corridor, and what varies with it

The right pages depend on the **corridor** — destination, passport, country applied from, purpose —
because authorities publish per-nationality pages and route applicants to the post serving where they
live.

| | Who to believe (domains) | Which pages exist (corpus) | Which page answers *this* traveller |
| --- | --- | --- | --- |
| Corridor-dependent? | No | No | Yes |
| Decided by | **A rule, once per country, committed** | **An offline build, refreshed** | **The machine, every corridor** |

The corridor store (`discovery/corridor_store.py`) keeps a resolution for a week, keyed on the
whole corridor; a refusal is never stored (entry 151), and neither is a resolution that ran without
search (entry 74) or while a page it chose to read failed for a reason that says only "not now" (entry
258). The corridor is keyed on the country slug, so
"USA" and "United States" are one corridor (entry 168). `visa-discover corridor` bypasses the store
both ways.

### The stages of `_resolve`

```
Corridor ─▶ search ─▶ corpus ─▶ crawl ─▶ select ─▶ fetch ─▶ adjudicate ─▶ ResolvedCorridor
            (Brave)  (var/corpus) (skipped when  (model,   (live,   (one model   or a refusal
                                   the corpus     reads     trust-    call)
                                   out-covers it) stored    checked)
                                                  text)
```

1. **Search** (`search.py`) — templated queries with `site:` for each trusted domain, never
   model-written or derived from page content, filtered again by `trusts_host`. It runs on **every**
   corridor, the purpose query included (entries 159, 160). Paced from one lock at 0.05s (entry 141).
2. **Corpus** — the country's stored candidates. The candidate set is `corpus ∪ search`, and what a
   web request discovers is folded back in (`visa-discover corridor` folds nothing back).
3. **Crawl** (`crawl.py`) — best-first, two hops, inside approved domains, **skipped when the corpus
   already offers more pages than a crawl could visit** (entry 51). Budget is shared between hosts
   (a `www.` host and its bare host count as one, entry 163), walked in waves of one page per host,
   results handled in frontier order so the answer never depends on which site answered first.
   3b. **Stored text is scored** for every candidate the index holds (`text_scores`), crediting the
   traveller's own post and penalising another's on post-specific roles (entry 245).
4. **Select** (`discovery/selection.py`, `discovery_selector: model`). The pool is every candidate
   whose link scores above zero for some role, plus the five best per role the link scored zero and
   stored text puts back (`admitted_on_text`, entry 158). It is then cut: `fusion_order` — per role,
   reciprocal-rank fusion of link rank and stored-text rank — shows the model the top 120 plus the 40
   best-linked candidates with no stored text (entries 194, 195). A third ranking, of the pages live
   search returned, is in the code and off (`selection_boost_searched`, entry 246). The notes and the
   recall log record what was withheld. The model reads stored excerpts and picks up to 20 pages; its `Selection` type
   holds ids and no prose. With no stored text the heuristic shortlist is used instead, and the notes
   say so. Selection rule 11 keeps an announcement together with the later page saying it took
   effect (entry 222).
5. **Fetch** — through a throwaway `DestinationConfig` and the ordinary `LiveSourceFetcher`, so
   redirect trust, PDFs, forwarding, rendering and caching come for free and an off-domain candidate
   cannot even be constructed. Refusals met here are reported like the crawl's (entry 49). A
   checklist a read page links to is read too, one hop down (entry 210).
6. **Adjudicate** (`discovery/adjudication.py`, `discovery_decider: model`) — one call assigns pages to
   roles, from pages fetched this run. Only `visa_decision` is load-bearing.

**Stage timings** are recorded per corridor in the recall log's `phase_seconds`; a stage is the span
between two numbered steps, not the act it is named after (entries 142, 143).

### What the adjudicator may do

Keyword ranking got Brazil wrong *confidently* (entry 15), so the final choice asks a model, bounded
hard:
- it chooses from an explicit candidate list; **an invented id is discarded** and the role left
  unfilled;
- it never widens trust;
- candidate text arrives under `untrusted_content`, as `anchored_excerpt` — the page head plus windows
  around every mention of the traveller's nationality or residence, to `DEFAULT_EXCERPT_CHARACTERS`
  (entry 42), because a flat head slice made whether a traveller got an answer depend on where their
  country fell in an alphabetical list;
- it may return null, and a null is honoured;
- heuristic scores are withheld from the packet;
- a failed call is retried once and then **refuses the corridor** — never falls back to the heuristic
  (entry 31).

Its reasons are kept in the recall log (`role_verdicts`, with `adjudicated_ids` to map ids to URLs).

### The heuristic: a recall gate, not a decider

`scoring.py` is deterministic, with vocabulary and weights in `config/discovery_lexicon.yaml`. Link
text outweighs the URL; wrong-audience pages and site furniture are **vetoes**, not penalties; country
identity is read from a page's own words, not its host (a host label names the *post*, entry 26).
Posts are recognised by host label and by `mission_labels`, and a page about another post loses points
on post-specific roles (entries 72, 134). A page about where the traveller applies from gains
`residence_weight`; nothing is subtracted from a page about their passport country (entry 126).

On the shipped path the link score decides **admission to the pool**, and its ordering feeds only the
fusion cut and the heuristic fallback. The lesson measured twice (entries 40, 61): a page ranked in
wrongly costs one excerpt; a page ranked out is lost for good — **widen the gate rather than improve
the ranking**. The link test also admits search results far more readily than corpus pages (entry
125).

### The offline corpus and page-text index

A **corpus** (`var/corpus/<CC>.json`, `discovery/corpus.py`) records which pages exist and what linked
to them — `url`, `title`, `link_text`, `heading`, depth, status. It is built by `visa-discover corpus`
(`corpus_build.py`) from search seeds and a crawl with an offline budget, and it is additive. A build
records far more than it opens (entry 88).
- Search seeds are kept as entries even when nothing links to them (entry 161).
- The authority's own index of its missions is promoted to a seed (`mission_index_seeds`, entry 137).
- A per-traveller family (`…/apply-{country}`) gets a reserved share, ordered by what the store lacks —
  never on a traveller (entries 88, 139).
- A host that stops answering is slowed, then dropped after six consecutive transport failures (entry
  139); a later build asks again for up to 100 transiently-failed entries (entry 207).
- Each build prints the links it rejected by rule (entry 200) — read it.

The **page-text index** (`var/pagetext/`, one SQLite/FTS5 file per country, `discovery/page_text.py`) keeps the
body of pages read, filled by builds and by `pagetext --backfill`. It is separate from the corpus JSON,
which is read whole on every request. **It ranks; it never speaks** (entries 78, 83): `rank` returns
URLs and scores; `text_for_selection` feeds only the selector, whose output type holds no prose; a
page it ranks is still fetched live before a word reaches a plan. The numeric text lift in `combined`
stays off — no country passes `DEFAULT_TEXT_COVERAGE_BAR` (entries 80, 81).

### Diagnostics

- **Recall log** (`discovery/recall_log.py`, `var/recall/<corridor>.json`, entry 43) — every
  candidate with its scores, what was pooled, withheld, fetched and read, the queries, each unreadable
  URL with its typed `FailureOutcome`, a typed `RefusalCause`, the selector that ran, the
  adjudicator's verdicts, stage seconds and model-call usage. Written in a `finally` and swallows its
  own `OSError`: a diagnostic may never cost an answer. A re-run overwrites it.
- **`visa-discover audit`** — why travellers go unanswered: reachability from the registry, and causes
  from runs, never added together.
- **`visa-discover coverage`** — whether a country's *store* holds the answer. The verdict comes from
  per-traveller families alone (*no per-traveller dimension*, *covered*, *bounded by the authority*,
  *incomplete*, or *ungraded*); the oracle half never votes (entries 90, 120).
- **`visa-discover selection-recall`** — whether a selector's picks match
  `oracle/selection_oracle.yaml`, a hand-curated fixture neither selector helped build (entry 87).
- **`visa-discover contention`** — rebuilds a corridor's candidate set from the store, offline, to
  curate an oracle row; `--outside-pool` looks where the pool never does (entry 127).

---

## Persistence and freshness

| Store | Keyed by | Lifetime | Depended on? | Where |
| --- | --- | --- | --- | --- |
| Authority domains | country | committed, reviewed | **Yes** — a missing country refuses | `config/authority_domains.yaml` |
| Page corpus | country | additive, never pruned | **Yes** — the candidate source | `var/corpus/`, `discovery/corpus.py` |
| Page text | country | additive, replaced per URL | **Yes** — the selector reads it | `var/pagetext/`, `discovery/page_text.py` |
| EU store | union | refreshed by `eu-store` | **Yes** — Schengen visa decisions | `var/corpus/EU.json` |
| Source snapshot | URL | TTL 24h, refused past 168h | **Yes** — it is the evidence | `var/cache/`, `research/source_cache.py` |
| Corridor resolution | full corridor | 3 weeks; refusals never stored | **Yes** — what a warm request serves | `var/corridors/`, `discovery/corridor_store.py` |
| Plan draft | everything the model is shown | `plan_reuse_hours` (24), never past the page TTL | No — a reused draft is still validated and graded (entry 178) | `var/plans/`, `research/plan_store.py` |
| Recall log | corridor | overwritten each run | No | `var/recall/` |
| Model usage log | UTC day | appended | No | `var/usage/`, `research/model_usage.py` |
| Selection oracle | corridor | committed, hand-edited | No — it grades, never serves | `oracle/selection_oracle.yaml` |

**The lifetimes differ because the things do.** A government page can change any day, so evidence is
measured in hours; which pages answer a corridor changes when a site is redesigned, so weeks; whose
domains a government controls changes on the timescale of governments, so it is committed.

**What may be stored is a page, never an answer** (entry 44). The corridor is the wrong unit to
precompute at any width, and a plan is a rendering — only the model's draft is reused, for
byte-identical inputs.

**Three rules hold across all of them:** a row records when the evidence was retrieved, never when it
was written (a `304` moves it; a failed refresh does not); the stale ceiling refuses; and only a
diagnostic may swallow its own failure.

**Not done yet:** nothing detects that a stored page changed (`content_hash` is recorded and unread,
TODO item 14); the cache is not re-validated against changed rules (known problem 17); every store is
a local directory, so a disposable host makes every request cold (item 20).

---

## Configuration

| Where | Holds | Committed? |
| --- | --- | --- |
| `config/runtime.yaml` | source, extraction, render and destination modes; decider, selector, model route; cache TTL, stale ceiling, plan reuse | **Yes** — reviewable policy |
| `config/authority_domains.yaml`, `supranational_authorities.yaml`, `service_providers.yaml` | who may be believed, and which contractors may be named | **Yes** — the trust anchor |
| `config/destinations.yaml` | seven hand-configured destinations, used only under `destination_mode: configured` | **Yes** |
| `config/discovery_*.yaml`, `countries.yaml` | scoring vocabulary, denylist, country reference data and mission labels | **Yes** |
| `.env` | `OPENAI_API_KEY`, `OPENAI_MODEL`, `SEARCH_API_KEY`, `PERSONAS_*`, `PARADIGM_*`, `SESSION_SECRET`, timeouts, limits | **Never** |

---

## Testing

Tests never touch the network or an LLM; `tests/conftest.py` blocks the network. The seams are
`transport=` (an `httpx.MockTransport`) and `now=` on both fetchers, `renderer=` for the browser, and
fake generators for the model.

- `tests/discovery_site.py` is a fake two-host government site with the shapes met in the wild: an
  opaque URL identifiable only by anchor text, a per-nationality page two hops deep, a checklist behind
  a forward to a PDF, a wrong-audience sibling, a client-rendered shell, and an off-domain link that
  must never be requested.
- `tests/test_rendering.py` injects a fake renderer; its one real Chromium check is skipped unless
  `VISA_RENDER_MANUAL=1`, and renders a `data:` URL.
- `tests/test_pdf_sources.py` builds real PDFs by hand.
- `tests/test_trust_coverage.py` freezes the trust rule's measured coverage and its traps.
- The load-bearing assertions are the safety ones: no off-domain host is ever requested, a spam result
  is dropped before any fetch, and a failed model call refuses.
