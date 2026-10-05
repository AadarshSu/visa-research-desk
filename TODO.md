# TODO

Each item says why it matters, so it can be picked up cold.

**Now** is this phase's work, in the order written; **Parked** is real work the owner has set aside
until it is required, cut to what it is and what would reopen it; **Done** is a one-line index of
finished work, whose reasoning is in DECISIONS; **Smaller things** are one-paragraph defects that
can change what a traveller sees.

**Numbering is append-only**, because PROJECT_HANDOFF, DECISIONS and code comments cite item numbers.
The numbers are names, not an order: the section decides the order. Finished items move to *Done*.

## This phase — the owner, 2026-09-30 (entry 247)

**The owner is largely happy with where the project is on scale and performance**, and is still
running their own performance and accuracy checks. Optimising it further and adding countries are
**not this phase's work**: they come back when something requires them, and are under *Parked* until
then. **Do not propose one unprompted.** What the owner wants next is a travel-readiness app for one
traveller, built around the dates they enter (item 80, entry 251).

Where entry 182's goals ended:

| goal | where it ended | items |
| --- | --- | --- |
| Model calls paid through Ofself Personas | Done, and the route from now on (entry 188) | — |
| ~30s a corridor, with information on screen while it runs | **Parked at ~55s** a fresh request: ~25s research, ~29s plan (entry 171). Each step shows on screen as it starts (entry 226) | 58, 59, 61 — parked |
| Hosted at a URL | **Live since 2026-09-27** on one AWS EC2 instance (entry 231). No plan has been timed there | 20; 7's leftovers parked |
| Most corridors accurate and useful | **Ongoing.** Item 63's second round: decisions 7 of 10, checklists 5 of 10 (entry 214). Nothing in the repo measures *right*, only *answered* (known problem 26) | **63** |
| 55 → 100+ countries | **Parked at 55**, each with a registry row and a rebuilt store; the page offers only those (entry 228) | 64, 2 — parked |

**Offline cost is not the constraint — the owner, 2026-09-23 (entry 184).** Build time and a higher
one-time cost are acceptable for anything that makes each live request better. Prefer an offline fix
to a per-request one.

**Settled, do not re-propose without a new argument:** search stays on every corridor, purpose query
included (entries 159, 160); refusing on a corpus miss is dropped (entry 173); the corpus holds what
every traveller shares and live search fetches what this traveller needs (entry 148), which is why
items 35, 47 and 49 are parked; embeddings and the search boost were measured and not adopted
(entries 242, 246).

**One habit matters more than the list.** A constraint has repeatedly turned out not to be where the
documentation said — [CORRECTIONS.md](CORRECTIONS.md) has over two hundred and fifty rows. Prefer
running a corridor to reading a code path, and measure a proposed fix before implementing it.

| | | |
| --- | --- | --- |
| **Now** | 80. A travel-readiness app for one traveller, built around their dates | `explore`, with the owner |
|  | 63. Make most corridors return accurate and useful information | `ongoing` |
|  | 55. Take the traveller from Ofself's shared identity, through one adapter | `soon` |
|  | 79. Score pages fetched live with the post-aware stored-text fixes | `soon` |
|  | 4. Decide the client-side retrieval question | `soon` |
|  | 20. Make the stores substrate-swappable and durable | `soon` |
| **Parked** | Expansion: 64, 2 · Speed and cost: 58, 59, 61 · Hosting: 7's leftovers | until required |
|  | Retrieval and blocks: 46, 11, 27, 10, 69 · Corpus coverage: 49, 35, 47 · Deciders and drift: 12, 13, 14 | until required |

---

## Now — this phase's work

### 80. A travel-readiness app for one traveller, built around their dates — `explore`, **with the owner; the direction is set, nothing is built**

**The direction — the owner, 2026-10-05 (entry 251).** Someone planning a trip enters where and
when, and sees the visa answer with what else bears on going. **One traveller**; friends come much
later. **One date range**, entered by the traveller. Ofself's schemas are read to learn about the
person and, once allowed, written to record what happened for next time.

**First piece — decided:** an optional date range on the form, and **three context panels beside the
visa plan: travel advice, news and weather.** Each is labelled with its source, linked, and never an
input to the visa answer; the visa form gets no harder. Mockup with example data:
`https://claude.ai/artifact/D7yXirsNX4jTBSA4LD71s4`.
- **Travel advice** from the traveller's own government (US State Department JSON API, GOV.UK
  content API — both checked; India publishes none found), in the government's words.
- **News** from Ofself's Assimilation news plugin (switched off; the owner is asking for it). Ask
  for its manifest first: can it take a country and a date window rather than the person's
  interests, must it save articles into the traveller's account, can an app trigger it, how fast it
  answers, which sources.
- **Weather** from an open weather service; climate averages beyond about two weeks, said so.

**Before building, the owner decides** (each set out in entry 251): (1) dates, fees and processing
times as cited values; (2) the travel-advice source tier and whose advice; (3) the news plugin
writing into the traveller's account; (4) research-first with a "have you booked?" question, or
not; (5) writing to Ofself — only what the person did, chose or confirmed; (6) reading their
calendar.

**To see the form fill from real documents,** a `travel-document` has to exist: no app writes one
yet. Create one in the owner's account with their developer token, or in the sandbox user, **with
the owner's yes** — only the fields the app reads, never a number, name or scan.

**Rules it meets — each moves only by a decision entry:** nothing written back to Ofself (item 55
rule 6, entry 44); no submission, booking or form filling, and no promise of approval; any field
added to `TravellerProfile` reaches the model on every plan (item 55 rule 1) — advice, news and
weather never do.

### 63. Make most corridors return accurate and useful information — `ongoing`

**The bound.** Correctness is checked by the owner, outside this repository (entry 68). Do not build a
truth set, grader or accuracy metric without asking. This item may measure what corridors *answer*,
reported with known problem 26's caveat.

**Where it stands.** Ten destinations — Australia, New Zealand, China, South Korea, Thailand,
Vietnam, Turkey, South Africa, Spain, Switzerland — for `IN/IN`, twice each. First round (entry 205)
found a wrong "no visa" from a flattened table, fixed. Second round (entry 214): decisions 7 of 10,
checklists 5 of 10. The owner's verdicts and what was fixed after are in PROJECT_HANDOFF's *Next
session*. What changed in the plan along the way, all on the owner's decisions: rules 8f–8j (entries
206–209, 218), checklists linked rather than copied (211, 213), renders ordered by promise (212).

**Open.**
- South Korea's rare wrong checklist — *Smaller things*.
- About 30 of the rebuilt countries have had no corridor run.
- **Purposes other than tourism (entries 248–250).** The owner's answers are built: transit,
  other-purpose exemptions and conditional answers (entry 250). Left: three conditions worded worse
  than the rule asks (New Zealand, Spain, South Africa transit), and every number here is one run.
- **Proposed, not measured:** de-duplicate near-identical series before the selector's cut —
  Australia once read seven quarterly reports while its step-by-step page was cut.

### 55. Take the traveller from Ofself's shared identity, through one adapter — `soon`

**Why — the owner, 2026-09-15.** This app is to be one app in Ofself, whose apps share one identity
structure per person. Integrating should be **one module mapping their schema onto ours**. The design
is in [CRUX.md](CRUX.md), argued in entries 180 and 181; platform surprises are in
[OFSELF_FEEDBACK.md](OFSELF_FEEDBACK.md).

**Built and seen working.**
- **The seam:** `create_visa_plan` asks an injected `TravellerSource` (`api/traveller.py`); the
  country check lives in `api/countries.py` and accepts alpha-3 codes. Discovery never sees the
  profile — only the `Corridor` codes.
- **The adapter** `api/ofself.py`: `passport_nationalities` reads `work-authorization.citizenships`;
  `traveller_defaults` reads `travel-document`, `travel-plan` and (only to resolve a plan's
  candidate) `place`. Errors distinguish a lost grant (`OfselfAuthorizationLost`) from an
  unavailable service (`OfselfUnavailable`), so a broken answer is never read as an empty one. Run
  live against sandbox user `66a3241b-5130-4ca9-9ce7-baaa69f84745`.
- **Sign-in** `api/signin.py`: `/oauth/login` → Ofself's authorize page → `/oauth/callback`, which
  trusts only the `sid_code` exchange (`POST /api/v1/auth/session/exchange`) for the user id. The
  session is one HMAC-signed cookie (`SESSION_SECRET`), 12 hours. The owner signed in for real on
  2026-09-17 as `43b82f83-66c4-449b-a9ce-eb1f690c433b`; the grant expires **2026-10-17**. A lost
  grant now ends the session. `sid_code` is redacted from uvicorn's access log.
- **The page:** signed in, passport, residence, destination and purpose start empty and are filled
  from Ofself as defaults the traveller confirms; one statement above the form says whether Ofself
  filled anything. `.claude/launch.json` has `visa-research-agent-signin` on port 8000.
- **Registration:** `.paradigm/secrets.toml` holds the CLI's binding (gitignored); the app's key is
  `PARADIGM_API_KEY` in `.env`. The DLR is published (spec version 5) and the owner re-authorised;
  `crux review` passes all five criteria, 13 of 16 checks. Readiness is capped at 2 of 5 because level
  3 needs a graph write, which rule 6 below forbids — accepted deliberately (feedback 5.6).

**Left to do.**
- **See the form fill from live data:** put real `travel-document` and `travel-plan` records in the
  owner's account.
- **Check authorisation twice per request** — before reading, and before returning a plan, since a
  plan takes ~55s. Handle `EP_NOT_FOUND`, `EP_REVOKED`, `EP_PAUSED`, `EP_EXPIRED` and legacy
  `NO_AUTHORIZATION`; none may fall back to `DEFAULT_TRAVELLER_PROFILE`.
- **Revocation:** our session is our own cookie, so revoking on Ofself logs no one out here. Ofself
  fires `session.revoked` to a webhook, which needs a public address — the EC2 host has one (entry
  231); the webhook is not built. Record what the exchange returns on the next real sign-in.
- **Stop keeping plan drafts past their reuse window.** `FilePlanStore` never deletes, and a draft
  holds the whole `TravellerProfile`.
- The authorize page echoes no `state`, so a callback cannot be tied to its login; the pending cookie
  refuses a browser that never started. Cannot be closed from this side.

**Six rules an adapter must not lose.**
1. **Read only fields that select guidance; drop the rest.** `build_research_packet` sends the whole
   profile to the model, so any field added to `TravellerProfile` goes to a third party on every
   plan. Keep `StrictModel`'s `extra="forbid"`.
2. **A passport type the program cannot research is refused, never coerced** to `ordinary`.
3. **A traveller with more than one passport chooses; the adapter never picks.** Residence likewise —
   another app's record is a default to confirm, never the corridor.
4. **A missing deciding field is asked, never defaulted** — the anonymous form's default traveller
   must not answer someone else's corridor.
5. **After the adapter a country is an ISO alpha-2 code.**
6. **Nothing is written back without a decision entry.** A plan is a rendering, never a stored fact
   (entry 44); `travel-requirement` rows are never read as evidence. `fact` nodes are never evidence.

### 79. Score pages fetched live with the post-aware stored-text fixes — `soon`

**The owner, 2026-09-30.** Entry 245's fixes to `score_body` — nationality from the title and path
and never the host, the traveller's own post credited and another post penalised, no purpose bonus
on the decision — apply only when the caller passes `residence`, which stored-text scoring does and
the fetched-body call in `CorridorResolver` (`fetched_candidate.body_scores = score_body(...)`)
does not. So a page is scored one way before it is fetched and another after.

**Do:** pass `residence` and `other_posts` there too. **Measure first:** what reads `body_scores`
after the fetch (`combined`, `rank_for_role`, the shortlist's heuristic path) and whether any
recorded corridor's roles or refusals move — the recall logs replay it without a network. The
adjudicator, not this score, fills roles on the model route, so the effect may be nil; say so if it
is.

### 4. Decide the client-side retrieval question — `soon`

DECISIONS entry 35 raises it and deliberately does **not** approve it. The traveller's own browser can
open a page this program may not read; whether the agent may then read what their session received is
near entry 18's boundary. **Write the decision either way before any code** — content arriving through
a browser has not passed the checkpoints `LiveSourceFetcher` applies, and the domain rule must still
hold. Nothing here licenses spoofing or retrying.

### 20. Make the stores substrate-swappable and durable — `soon`

**Where it stands on the EC2 host (entry 231):** durable for now. Every store is under `var/` on the
instance's EBS root volume, which survives a service restart, a reboot and a stop and start, and `git
pull` never touches it (`var/*` is gitignored). It is lost only if the instance is **terminated**
(delete-on-termination is the default). The corpus and page text were copied up by hand from the
owner's Mac, so they drift from it. Moving to a container platform brings the problem back.

**Do:** put the corpus, the source snapshots and the corridor resolutions behind their existing
`load`/`store` protocols with a networked implementation. Add a weekly refresh job — a conditional
`GET` over every stored URL, cheap because most answer `304`, and where a `404` or an off-domain
redirect flags the country.

**Three things right today and easy to lose in a migration:**
1. **A row records when the evidence was retrieved, never when it was written** (entry 4).
2. **Past `source_maximum_stale_hours` a stored page is refused**, whatever the store.
3. **A hash change marks a source and never auto-swaps a role-bearing one** — item 14, parked.

`content_hash` is already over the *cleaned* text; do not add a second hash over raw bytes.

---

## Parked — not this phase

**The owner, 2026-09-30 (entry 247):** set aside until something requires them. Each is cut to what
it is, what is known, and what to do first; the entries named hold the rest, and the full text of
each item as it last stood is in this file's history before that date.

### Expansion

- **64. Expand from 55 countries to 100+.** 143 of 198 have no registry row. **Ask the owner which
  countries and how many before a batch.** Three stages (entry 68): a row from `visa-discover
  registry`, reviewed by asking Wikidata about the *organisation* (entries 67, 110, 111); a corpus
  and page-text index, then corridors, reading every refusal's reason (entry 70); answers from the
  store. Arithmetic, not measured: ~$3 of search for all 143 rows, ~$12 and ~10 hours per fifty
  corpora. For a destination that states its visa rules only in its own language, check its
  `visa_decision` page is pooled; if not, embeddings reopen as a pool admission (entries 241, 242).
- **2. Reviewed authority domains for governments with no hostname marker.** All 55 rows are done;
  each new country may need one. Never widen `GOVERNMENT_PATTERNS`. A TLS certificate's organisation
  names the authority for 9 of 16, Wikidata's `P856`/`P17` for others, RDAP is dropped, and a person
  confirms each (entries 65, 66, 110, 111). A government's own published domain list is unmeasured;
  cross-vouching from a trusted domain would be a decision, not a patch.

### Speed and cost

- **58. What is left of model-call cost and research latency.** ~55s a fresh request (entry 171).
  Untried: a cheaper model for selection only, graded with entry 170's A/B; trimming the roles
  call's packet; overlapping search with the fetches; finding out what `fetch` is made of; entry
  146's country-stable packet. Grade on several runs, in seconds and dollars (entries 144, 145).
  Carried over: whether `visa-discover corridor` should write back to the corpus; corpus eviction,
  designed and unbuilt; a dead pinned page must refuse the corridor.
- **59. Guard the 272K-token price threshold.** OpenAI bills a request over it at 2× input and 1.5×
  output (entry 167). Nothing caps a selection packet as a whole; Canada's was ~92K before entry
  195's cut. Measure how close any country comes first; silently dropping candidates is not an
  option.
- **61. Decide whether a corridor's five renders should grow.** France left 14 of 17 challenged
  pages unrendered (entry 179); nothing yet shows the extra pages change an answer. Before shipping,
  run France `BD/AE` and a Liechtenstein corridor several times in each arm, cache warm in both
  (entry 136), and count roles. Cloudflare's script host stays refused (entry 202).

### Hosting

- **7. What was never run on the server (entry 231).** A few corridors from the instance, compared
  with local runs — a cloud address may be challenged or blocked more — and a cold request timed
  there. **The owner's open decision (entry 151):** a refusal is never stored and a resolution is
  kept three weeks, so a refused request is retried by the next traveller until one run resolves;
  keep it, store refusals briefly, or require two agreeing runs. There is no per-user rate limit.

### Retrieval and blocks

- **46. A refusal served as `HTTP 200`.** Morocco's F5 block answers "Request Rejected" and is
  reported `unusable` (entry 121). Reclassifying it as `blocked` changes what resolves a corridor
  (entries 32, 57, 109). Count such bodies across the corpora first.
- **11. Skip a host that has refused every request?** A `403` on one path is not evidence about
  another. Count per-host refusals first; the block must still be reported, and nothing here may
  become a retry.
- **27. A hosted scraping service, for corpus discovery only.** Retrieval through one is already
  refused by entries 4, 12, 35 and 36; only offline URL enumeration (`/map`) is open. Try item 10
  first.
- **10. Try sitemaps before crawling.** Check first whether pages that filled roles appear in their
  domains' sitemaps.
- **69. Read scanned PDFs.** 196 PDFs score on their link alone and cannot be read; none holds an
  oracle answer. For ranking it is low risk; as evidence it needs a decision entry, because nothing
  downstream can catch a misread digit. First count, over `var/recall`, how often a selection
  includes one.

### Corpus coverage — parked by entry 148, which hands per-traveller pages to search

- **49. Walk the mission family to the traveller's post.** Australia's family has 169 members and a
  build walks ~25 (entries 132–134, 137, 139). **Do not add `{residence}` to `corpus_queries`.**
  Reopen only if a sweep shows search failing to supply a post.
- **35. Finish the Netherlands' family reservation.** `airport-transit-visa/apply-{}` is 52% read; a
  rebuild cannot open an address an earlier build recorded and skipped (entries 88, 101).
- **47. How much of the world the family detector cannot see.** `country_family_keys` matches the
  URL only; the blind spot is mostly English aliases and dependent territories (entries 124, 126).

### Deciders and drift

- **12. Watch where the two deciders disagree.** `decided_by` and the heuristic's score sit beside
  the model's choice. Do not tune the lexicon to agree with the model.
- **13. Conflict detection, with claim scope.** Entry 30 deleted `conflicts`. If it returns: record
  the population each claim applies to, compare same-scope claims only, leave the visa decision out.
  Read entry 6 first.
- **14. Detect drift in configured sources.** On a content-hash change, **mark** the source; never
  auto-rediscover and swap a role-bearing one. Item 20 makes this the corpus-rot check.

### Small defects in builds, crawls and tools

None changes what a traveller is told today.

- `coverage` reports the EU store as a country nobody has built (entry 204), and counts a delegated
  checklist as `open` — give it its own column, never added into `held` (entry 93).
- `canonicalise_url` drops a trailing slash before a query string, and some servers care (entry
  201). It changes stored addresses: measure first, ship with a rebuild.
- A per-host fair share treats unequal hosts equally — Thailand's 31 provincial sites each took the
  national immigration service's share. Needs its own rebuild.
- `CorpusEntry` holds one `link_text`/`heading` per URL, so one section's context can attach to a
  page every traveller needs (entry 55); a footer link inherits the heading above it (entry 26).
- A reserved shortlist place guarantees a domain, not a page; the fix is in mission scoring.
- The corpus page budget is fixed at 1,200 rather than derived from the seed count; the corridor
  crawl's 40-page budget is spent at depth 0 for a country with no usable corpus.
- Nothing validates a `tlds` entry in `countries.yaml` that widens trust, and
  `is_bare_public_suffix` is a heuristic — review both as countries are added.
- Sweden's ranking is unexplained; trace it as the Netherlands was before changing anything.
- Cyprus names `mip.gov.cy`, which does not resolve (`www.mip.gov.cy` does); Ireland reports the
  dead `inis.gov.ie` two ways; `www.ph.emb-japan.go.jp` answered 404 twice.
- 25 recall logs predate `RecallRecord.cause`; only a re-run fills them.
- A sweep cannot notice every corridor failing for the same non-country reason. Stop after N
  consecutive `adjudication_failed` and say which provider said what.

---

## Done

The reasoning is in the DECISIONS entry; this is the index. Code comments cite some of these numbers.

| Was | Done | Entry | What building it found |
| --- | --- | --- | --- |
| 71. Take where the traveller lives, and name the one post that serves them | 10-05 | 225, 252 | A state or region from a committed GeoNames list, not a city; the plan names the page's post for it in 12 of 12 replays and asks where the split is finer (Japan England named London once in 2). `in_person` shown only where a page says. Discovery does not use the region; Ofself supplies none yet |
| 78. Decide whether to show the selector 60 + 40 pages with the search boost | 09-30 | 246 | Closed, not adopted: live over ten corridors the boosted 60 + 40 gave the same decisions and checklists on 44% less selector input and lost France `PH/PH`'s processing time in every run; a vocabulary fix and embeddings were both checked and neither keeps the page. The owner kept 120 + 40; the boost stays in the code, off |
| 77. Make the stored-text score credit the traveller's own post | 09-30 | 244, 245 | Shipped: nationality from title and path only, a residence credit and the own/other-post signals on post roles, no purpose bonus on the decision. At today's cut every decision and checklist in every run (127.0 against 126.6); with the search boost 60 + 40 becomes level (item 78) |
| 76. Test the search boost with the selector | 09-30 | 243, 244 | Offline 129 of 131 at a 40 cut once `CandidatePage.searched` records every page search returned; the selector scores 124.2–124.4 against 126.6 on 45–60% less input — corpus-only answers are displaced. Not adopted; the cause is item 77 |
| 75. Decide whether to adopt the embedding-ordered shortlist | 09-29 | 239–242 | **Not adopted.** Level on accuracy across 27 corridors in four languages at half the selector's input, but no answer changed; the saving (cents, 0.6–2s) does not pay for a vendor and a second index. Reopen at expansion to a destination that publishes only in its own language, if selector input cost binds, or if answers fall past the 120 cut |
| 67. Test hybrid ranking with embeddings of stored page text | 09-29 | 235–239 | No gain at today's cut (it already keeps every answer); an embedding-ordered 40 + 40 packet is level within the bar, re-graded on the refreshed oracle — adoption is item 75. Other-language pages stay untested |
| 74. Let a traveller report a corridor that did not work | 09-27 | 233 | A copied log can predate what the traveller saw: a stored corridor writes none. Nothing notifies the owner of a new report |
| 73. Tell the traveller exactly why their corridor was refused | 09-27 | 232 | A search outage with no corpus was a bare 500, and every plan-stage fault one sentence; messages read from the code, not collected from live runs |
| 7. Put it somewhere others can open it | 09-27 | 231 | Hosted on one AWS EC2 instance behind Caddy, stores on its disk; HTTPS and Ofself sign-in seen working. What was never run there is under *Parked* |
| 57. Show each step of a plan request as it starts | 09-26 | 226 | `POST /visa-plans/stream` names each step and then sends the whole validated plan; eight steps over about a minute, seen live |
| 72. Redesign the form and landing page | 09-26 | 229, 230 | A dark hero, the form as a plan-request ticket, a how-it-works strip; seen at 1280px and 390px with the fixture plan |
| — The full rebuild of every store | 09-25 | 193, 203, 204 | All 55 rebuilt and graded; stores hold every oracle answer. The archived-year veto was dropping filed guidance, widened |
| 68. Read more of what a build records | 09-25 | 185–187, 189, 192, 200 | The cause was the per-host split: a scored link may now read past its share, and is scored with its surrounding text |
| 70. Why a corridor that read its authority has no decision | 09-25 | 198–202 | The roles call was right every time it refused. Eight measured fixes took 11 of 14 to an answer; the EU tier answers Schengen decisions |
| 66. Keep the selector's picks as good as its pools grow | 09-24 | 194, 195 | A shorter list, not more text: the fusion top 120 plus 40 with no text, at 42% less input, shipped without the live A/B |
| 62. Pay for model calls through Ofself Personas | 09-24 | 188 | One plain call at `capabilities: []`; graded equal to the direct route. The route from now on |
| 5. Answer the challenge, honour every `robots.txt`, get France's checklist | 09-16 | 75, 92, 93, 109, 179 | Half the challenges need Cloudflare's script host; France's checklist is behind its wizard — named, never driven |
| 48. Test root seeding; separate discovery from allocation | 09-15 | 161–163, 176 | Root seeding rejected; the gap was a build discarding its own search seeds. All 53 then rebuilt |
| — Reuse a plan written for the same inputs | 09-16 | 178 | The draft is kept 24h, never the plan; every request still validates and grades. Not timed live |
| 56. Make the written plan shorter | 09-15 | 174, 175 | Short source ids declined (wrong answers); one short quote per claim shipped, ~1.5s saved |
| 19. Get a corridor under ten seconds | 09-15 | 140–146, 159–173 | Closed, not reached. Search pace 19.0s → 2.6s; every call's cost recorded; $0.394 → $0.251 a corridor |
| 51. Search only for what is specific to this traveller | 09-15 | 159, 160 | Nothing built: neither conditional shape pays, and the purpose query is not traveller-neutral |
| 31. The anchor scorer gates 94% of the corpus | 09-14 | 123, 125–128, 158 | Five best per role on stored text added to the pool, nothing removed, +16% selection input |
| 21. Fill the three provenance gaps | 09-14 | 156, 157 | Sources carry content hash and why chosen; the checked quotes were later removed (entry 227) |
| 54. Say which refused page mattered, once per authority | 09-14 | 155 | One sentence per authority, decision pages first |
| 9. "No checklist exists" vs "we failed to find it" | 09-14 | 153, 154 | Re-scoped: say what was found; name likely unread checklists with links |
| 8. Confirm a blocked authority reads usefully | 09-14 | 152 | Reads as "we could not check"; found items 54 and 7's storing question |
| 17. What a corridor that flips between runs should do | 09-14 | 43, 44, 118, 151 | A refusal is retried until one run resolves — deferred to item 7 |
| 53. A null-decision plan graded `verified` | 09-14 | 150 | Now refused by `VisaPlan` |
| 52. Hand-configured destinations answering from one traveller's pages | 09-14 | 149 | Singapore and Japan now researched like every other country |
| 1. Score a page for where the traveller applies from | 09-02 | 126 | The scorer's order is consumed by nothing — it reaches a corridor as a boolean |
| 50. Cap renders per host on the request path | 09-05 | 135, 136 | The shortlist shares 5 renders, not 12; one host may no longer take all five |
| 45. Re-run five countries measured before their corpus | 09-01 | 121, 122 | Romania fills 5 of 6; found items 46 and 47 |
| 43. Give the new 43 something the coverage gate can grade | 09-01 | 120 | An `ungraded` verdict; the oracle is not growing to 53 |
| 44. Re-measure countries whose ranking text was a bot-check page | 09-01 | 118, 119 | NO and ID fill 6 of 6; the US corridor flips |
| 42. Why Liechtenstein's 7,456 pages yield two candidates | 08-30 | 117 | `is_challenge` read only 20,000 characters; 414 stored bodies were bot-check pages |
| 41. Build corpora for the 43 remaining countries | 08-30 | 116 | 10 → 53; three defects only breadth found (113–115) |
| 30. Perfect batch 1 before adding a country | 08-30 | 116 | All three stages met |
| 18. Build the offline corpus job | 08-30 | 44, 116 | A build records far more than it reads (entry 88) |
| 40. Let curation fetch one page the index lacks | 08-28 | 99 | Dropped; the premise was wrong |
| 39, 39a. The visa-free plan as an entry plan | 08-28 | 96, 98 | No step floor; `where_to_apply` may be null, never forced |
| 38. Re-run the twenty oracle corridors | 08-28 | 97, 98 | `selector` recorded the configured selector, not the one that ran |
| 37. The gate that says whether a corpus is good enough | 08-28 | 90 | `visa-discover coverage` |
| 36. Guidance on a commercial contractor | 08-28 | 89 | Named, never read, never believed |
| 34. An oracle neither selector helped make | 08-27 | 87 | Entry 86's +41 is +30 |
| 33. Measure the model candidate selector | 08-27 | 85 | Turned on |
| 32. Raise the corpus page budget | 08-27 | 82 | No change; the UK's fee host published a form |
| 26. The nationality bonus rewards naming a country | 08-24 | 62 | Closed with no code change |
| 25. Get the answering page into the shortlist | 08-24 | 61 | Five per role at 35 places; the UK went 0/8 → 4/4 |
| 24. "The answer is behind a tool we cannot drive" | 08-24 | 59, 60 | A questionnaire is an answer, for every role |
| 3. Measure the top 20 corridors against a bar set in advance | 08-24 | 58 | A marginal pass; the wizard, not blocks, is the largest limit |
| 23. Give `visa_decision` its floor back | 08-24 | 56 | The vocabulary could not recognise an answer |
| 15. Re-run the six verified corridors | 08-23 | 55 | Qualification broke when the crawl left |
| 22. Route the request path through the corpus | 08-23 | 49–53 | 2–5× faster |
| — Earlier (08-18 to 08-24) | — | 29–43, 63 | robots.txt, the registry, block narrowing, failed adjudication refuses, the recall log, typed refusal causes |

## Smaller things

Defects that can change what a traveller sees. The rest are under *Parked*.

**The roles call can credit a generic sentence as the checklist (entries 223, 224).** South Korea
`IN/IN` once credited the Chennai consulate's exemption page ("passport, application forms, a recent
photograph, and other relevant documents") and was graded `verified`. About 1 run in 18; a stricter
rule 4 did no better on replay. Untried: a deterministic floor on how many distinct documents a
credited checklist names (`names_documents` in `scoring.py`); refusing a checklist credited from the
page the same call credited as an exemption list. Measure on the saved packets first.

**A place to apply with no location reads "Online" (entry 252).** The where-to-apply card has
always shown a null `location` as "Online", which is false when the page names a post but no
address — South Korea's plans often do. Entry 252 shows "Not stated" only beside `in_person:
required`; the general case is untouched.

**Egypt `BD/SA` credits its decision 1 run in 3 on a byte-identical packet (entry 204).** A prompt
question — measure with `replay_roles.py` before changing anything.

**Germany fills `document_checklist` for `IN/GB` and `PH/PH` but not `NG/NG`**, though
`nigeria.diplo.de` is in the corpus (entry 112). Not held, or not selected — nobody has looked.

**A PDF served as `application/octet-stream` from a path without `.pdf` is read as HTML (entry
216).** Checking the `%PDF-` signature would catch it. Waits for a corridor it would help.

**A `TypeError` from the model call reports itself as bad model output.** Separate the invoke from the
parse, in its own change.

**A plan can leak an internal field name** (`application_document_source_ids`) into traveller-facing
text. A prompt matter.

**The Japan eVisa "Go here" link downloads a PDF shell** rather than opening the portal. The plan
flags it.
