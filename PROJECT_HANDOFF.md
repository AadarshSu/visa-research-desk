# Visa Research Agent — Project Handoff

**Read this first when picking the project up.** It answers three questions: where the project
stands, what to do next, and what is known to be broken. The history of how it got here is in
[DECISIONS.md](DECISIONS.md), by entry number; it is not repeated here.

| | |
| --- | --- |
| **Repository** | `github.com/AadarshSu/visa-research-agent` |
| **Last updated** | 2026-10-07 — update this line when you touch the handoff |
| **Tests** | 1,100 on the owner's machine, with `var/` present: 1,099 passing and 1 skipped (the opt-in browser test), run 2026-10-07; two more skip in a checkout without the corpora. `ruff` and `mypy --strict` clean. The suite is blocked from the network (`tests/conftest.py`, entry 45) |

| Question | File |
| --- | --- |
| Where are we, what is next, what is broken | **this file** |
| The ordered queue of work, and why each item matters | [TODO.md](TODO.md) |
| Why it is built this way, what was tried and rejected | [DECISIONS.md](DECISIONS.md) — **start at its index** |
| How it is built | [ARCHITECTURE.md](ARCHITECTURE.md) |
| The rules that must not be broken | [CLAUDE.md](CLAUDE.md) — loaded automatically |
| How to contribute and debug a corridor | [AGENTS.md](AGENTS.md) |
| What a run has contradicted before | [CORRECTIONS.md](CORRECTIONS.md) — read the rows for an area before changing it |

**Link, do not copy.** Every time this file summarised another, the two drifted.

---

## Next session — start here

**The owner, 2026-09-30 (entry 247): scale and speed are where they want them.** The owner is still
running their own performance and accuracy checks. Optimising further and adding countries are not
this phase's work; both are under *Parked* in [TODO.md](TODO.md) and come back as required. **Do not
propose either unprompted.**

**Next, with the owner: a travel-readiness app for one traveller, built around the dates they enter
(TODO item 80, entry 251).** Built so far: optional dates on the form (entry 256) and the
at-a-glance box (entry 253). **Built next:** a travel-advice panel below the at-a-glance box,
from the passport's government, **links only** — nothing read or quoted (entry 260). Built on
2026-10-05: 52 reviewed publishers, 682 checked destination links, `GET /travel-advice` and the
panel under the at-a-glance box — seen on the fixture plan, not yet deployed. **Weather** (entry 261)
followed on 2026-10-06: MET Norway's forecast within about nine days, NOAA station averages beyond,
for a city picked in the panel — also not deployed. Neither panel is ever an input to the visa
answer.

**Design exploration has its own item (TODO 81) and belongs in a session of its own:** different
layouts and UI workflows for the site, starting from the current design (entry 270) and the parked
`design-bold` branch.

**What 2026-10-06 changed** (entries 262–270), all on `main` and pushed to GitHub, **none of it on
the deployed server yet**:
- **Plans no longer need sign-in** (entry 262). A visitor gets ten free plans per address, counted
  under a keyed hash in `var/allowance/`, and signing in with Ofself imports their details and lifts
  the limit.
- **A checker is never where to apply** (entry 272). United Kingdom `US/US` sometimes filed GOV.UK's
  visa checker as the route. The plan now drops a route whose address is a named questionnaire for
  another question. In five real runs it was never filed there.
- **United Kingdom `US/US` is settled** (entries 272, 274). It reads `gov.uk/eta` and the visa
  national list on every visitor corridor. Roles rule 7e puts a pre-travel authorisation under
  where to apply, not the decision. "No visa" on 5 runs of 5, with the ETA on the band on 4.
  **Known problem:** the fifth keeps the ETA only as a step, and is still `verified`.
- **An open decision leads with where to settle it** (entry 271). The band offers the authority's
  checker, an unread official page or its contractor as a button, and says "Could not be confirmed"
  only with none of them. Where to apply and documents read "if you need a visa" or fold into one
  line, and a documents cell shows only over a documents section.
- **The result page was redesigned** (entry 270): a verdict band, the pass as the result's header,
  and advice and weather in a sidebar. A bolder makeover is parked on the local branch `design-bold`.
- **A travel authorisation is never called a visa** (entry 263). New Zealand on a US passport reads
  "No visa required" with "Travel authorisation required — not a visa: NZeTA" beside it. A document
  the authority itself calls a visa (Australia's ETA, subclass 601) stays "visa required".
- **Rule 8k was tightened twice.** It states the side an ordinary trip of the purpose falls on
  (entry 264). It names a condition only where it changes *whether* a visa is needed, never which
  visa or a disqualification such as a conviction (entry 268).
- **Australia's ETA page could not win the ranking** (entries 265, 266). The nationality check ran
  on substrings ("us" matched "Australia"), and an eligibility list did not read as a decision;
  both are fixed. The page still ranks 55th, held down by the breadth penalty (known problem 46). So
  a country may now name pages its corridors read on every run, in `config/always_read.yaml` (entry
  267). Australia `US/US` then answered "visa required, ETA (601)" 3 of 3.
- **`visa-discover corpus-add`** stores named pages with no crawl (entry 265), and a source id the
  model writes into a plan's prose is now removed (entry 269).
- **Every prompt change was measured** by replaying the plan call on captured packets, the current
  prompt against the candidate, with controls. The tooling is under *Where things are*.

**This phase's queue** is TODO's *Now*: 80, then **63** (accurate answers, ongoing), **55** (what is
left of the Ofself adapter), **79** (a small scoring consistency fix), **4** (the client-side
retrieval decision) and **20** (durable stores).

**Item 71 shipped 2026-10-05 (entry 252):** the form asks for an optional state or region after the
country, from a committed GeoNames list (`config/regions.yaml`, regenerated with `visa-discover
regions`); a plan names the post the page assigns to that region, and says "In person" only where a
page does. Measured by replaying the plan call on saved packets, 1–2 runs a case; no corridor was run
end to end, and the deployed server has not been updated.

**Where item 63 was left (entries 214–224).**
- The second round of ten destinations, `IN/IN`, twice each: decisions 5 → 7 of 10, checklists
  3 → 5 of 10 (entry 214). Review page:
  [Ten Corridors, Second Round](https://claude.ai/artifact/XGJy6wjofg6tDcWc9jZu7K).
- The owner's verdicts (entries 216, 224): **Australia and Spain now right**, both `verified`
  (entries 215, 217–219). **South Korea wrong** for a rare wrong checklist, about 1 run in 18 — TODO
  *Smaller things*, with two untried fixes.
- Thailand's refusal and Japan's open decision were both a decision resting on two pages judged one
  at a time; fixed by selection rule 11 and roles rule 7b (entries 220–222). The regression set was
  `verified` in all ten runs after.
- The SharePoint countries and Korea were rebuilt on 2026-09-26 (entry 223).

**Purposes other than tourism, measured 2026-10-03 (entry 248).** 51 corridors, one run each, with a
same-day tourism control: business answers as tourism does (9 decisions, 8 checklists each), study 7
and 6, transit 3 and 1. Review page:
[Corridors Beyond Tourism](https://claude.ai/artifact/6P4pYoYzkh2nHvVvCEX12z). **The owner's answers to entry 248 are built and measured (entry 250):** a short-stay list never
decides transit, an exemption for another purpose says nothing, and a plan may state an answer that
holds on a trip fact (`decision_condition`, shown as "Only if …", never `verified`). Transit went from
3 to 8 decisions, 8 of them conditional; Japan transit's wrong "visa required" now refuses. **Open
for the owner:** three conditions are worded worse than the rule asks (New Zealand, Spain, South
Africa transit), worded before entries 264 and 268 changed 8k — re-read them on a fresh run before
acting. **The stale ceiling is now 90 days** (entry 249, the owner's decision): stale pages are
served, flagged, rather than refused.

**A fix that changes what a plan may conclude is the owner's decision** (entries 206–209 are the
pattern). Record each in DECISIONS and CORRECTIONS.

**Any change to the plan call's prompt or packet re-runs Japan `IN/GB` and Singapore `PH/PH`
several times first.** Rule 8e's bounds live only in the prompt, and a packet change once broke them
(entry 175).

## Waiting, or shipped and not measured

- **Decisions waiting on the owner:** item 4, whether the traveller's own browser may fetch what the
  agent was refused. Parked with their items: whether a corridor's five
  renders should grow (item 61), and whether to store a refusal (entry 151).
- **Questions for Ofself:** does it host apps, and does any of its apps record a trip before it
  happens (TODO item 55).
- **Not measured live:** entry 178's plan reuse has never been timed; of the 2026-09-25 rebuild,
  only entry 204's corridors and item 63's ten have been run; entries 134 and 135 were never priced
  (entry 136). On the server, no plan and no corridor has been timed.
- **Ofself sign-in** works for real, but the owner's account holds no travel records, so the form
  has only been seen filling from the sandbox user and a fake Ofself. **The grant expires
  2026-10-17.**
- **OpenAI has been out of credit since 2026-09-16.** Nothing waits on it — model calls go through
  Personas (entry 188) — except `model_route: openai`.
- **Measured and not adopted, 2026-09-27 to 09-30:** embeddings for ranking or for the shortlist
  (entries 235–242), and a boost for pages live search returned (entries 243, 244, 246). The boost
  is in the code and off (`selection_boost_searched`); the shortlist stays 120 + 40. What did ship
  is entry 245: the stored-text score credits the traveller's own post. What would reopen each is in
  its entry. The Voyage account has a card on it, for the rate limit, and about 171M of its free
  200M tokens left by this repository's count.

## Where things are

- **The deployed app (entry 231):** one AWS EC2 instance at `https://<dashed-ip>.sslip.io`, a systemd
  service `visa` behind Caddy. Update: `cd ~/visa-research-agent && git pull && sudo systemctl restart
  visa`. **It has not pulled since before 2026-10-05**, so it lacks the advice and weather panels,
  the redesign, free plans without sign-in and entries 263–269. Pulling brings all of them in at
  once, including ten free plans per address for anyone who finds it. The pages added with
  `corpus-add` (Australia's ETA and eVisitor) are in this machine's stores only; the always-read
  list fetches the ETA page live, so that fix needs no store. Its `.env`, `var/corpus` and `var/pagetext` were copied by hand, so they drift from this
  machine's. Its stores are on the instance's disk, lost only on terminate. Not yet run there: a plan,
  a corridor's timing, and the block/challenge rate from an AWS address. Travellers' problem reports land
  in its `var/reports/`; read them there with `visa-discover reports` (entry 233).
- **Raw outputs of item 63's rounds:** `var/item70-2026-09-24/`
  - `item63-round2/<slug>_IN_IN/<1|2>/`
  - `item63-round3-forms/` (entry 215), `item63-round3-217/` and `-217b/` (entries 217–219),
    `item63-round3-220/` and `-220b/` (entries 220–222), `item63-rebuilt-2026-09-26/` (entry 223)
  - Replays: `japan-roles/`, `thailand-select/`, `korea-checklist/`
- **Tooling, all in `var/item70-2026-09-24/`:**
  - `run.py N slug/NAT/RES …` re-runs corridors; `ITEM70_OUT=<folder>`, optionally
    `ITEM70_CORPUS=<copy>`.
  - `replay_plan.py` replays the plan call; `replay_roles.py` takes `ROLE=`; `replay_select.py`
    takes `SELECT_PROMPT=`.
  - `probe_render.py` renders pages with the project's renderer and prints what came back.
- **Measuring a plan-prompt change (2026-10-06), in `var/nzeta-2026-10-06/`:**
  - `var/purposes-2026-10-03/run.py N slug/NAT/RES/purpose` runs corridors live and captures each
    plan packet; `PURPOSE_OUT=<folder>` names the output.
  - `replay_plan.py OUT.jsonl RUNS baseline|PROMPT PACKET…` replays the plan call alone. Unlike
    item 63's copy, it records `where_to_apply`, the steps and `decision_condition`. The
    `replay_*.sh` scripts and `*.jsonl` files are entries 263, 264 and 268's measurements.
  - `oracle_text_rank.py before|after` ranks every oracle answer by stored text under the committed
    scorer and the working one. **`selection-recall` cannot measure a scoring change**: it replays
    logged rankings and printed the same output before and after entry 266.
  - `var/selection-replay-2026-09-24/` replays only the selection call over fixed pools.
- **Keep the Mac awake and the lid open for a long batch.** A run in progress freezes in clamshell
  sleep and resumes only on wake; on 2026-10-03 one sat 83 minutes that way (`pmset -g log` shows
  it). The runner is resumable, so a stalled batch can be stopped and restarted.
- **Run anything that fetches or renders from the owner's Terminal panel** (or Bash with the sandbox
  off): Chromium cannot start in the sandboxed shell.
- **A reference corridor:** `germany/IN/GB/tourism` answered visa required, `verified`, five fresh
  runs of five through the real API route on 2026-09-25, with the same checklist source and place to
  apply. Japan `IN/GB` answers "visa required", `verified`, 8 of 8 (entry 197).
- **Leftovers to tidy, not urgent:** uncommitted run outputs under `var/` (`git status` lists
  them — the pilot's logs, item 63's rounds, the embedding and boost captures) and `nohup.out`.

## Rebuilding a store

1. **From the owner's terminal, or Bash with the sandbox off.**
2. Run `.venv/bin/visa-discover eu-store` first (entry 201).
3. `var/item70-2026-09-24/rebuild.sh CC ...` — resumable, backs each country up first, and skips a
   country listed in its `done.txt`.
4. Wait on a queue with `pgrep -f "bash var/item70"`, **never** `pgrep -f rebuild.sh`: every waiting
   shell's own command line matches the second, so the waiters wait on each other for ever.
5. **Read each build's `links rejected by rule` block** (entry 200). A rule throwing away many
   addresses that say "visa" is how entry 203 was found.

**Do not read a store's `built_at` as its rebuild date.** A corridor that resolves writes its pages
back and `merge` resets `built_at`. What was rebuilt is what the `done.txt` files under
`var/rebuild-pilot-2026-09-25/` and `var/item70-2026-09-24/rebuild/` list.

---

## Where it stands

**The goal:** visa plans where every claim is grounded in an official government source, and the
traveller is told plainly what could not be verified. What this phase is for, and where each earlier
goal ended, is at the top of [TODO.md](TODO.md).

| | |
| --- | --- |
| **Reachable destinations** | **55 of 198** — the rows in `config/authority_domains.yaml`; every row carries a confirmable domain. A country with no row is refused, never bootstrapped live (entry 38), and is not offered on the page (entry 228). `visa-discover audit` prints the split |
| **Stores** | **All 55 have a page corpus and a page-text index**, rebuilt 2026-09-24/25 (entries 193, 203, 204) and partly again 2026-09-26 (entry 223): 285,261 pages; `var/pagetext/` is 942 MB. Plus a shared EU store for Schengen visa decisions (entry 201) |
| **Runtime** | `source_mode: live`, `extraction_mode: openai`, `render_mode: on_demand`, `discovery_decider: model`, `discovery_selector: model`, `destination_mode: automatic`, `model_route: personas` — see `config/runtime.yaml` |
| **Model calls** | Through Ofself Personas since 2026-09-24, on Ofself's account (entry 188). Graded against the direct route: selection 41 of 48 roles against 39 of 48 |
| **Selection** | The model selector sees the fusion top 120 plus 40 best-linked pages with no stored text (entry 195). On `oracle/selection_oracle.yaml`: 100% role recall against the heuristic's 70% at matched budget (entry 87) |
| **Sign-in** | Optional since 2026-10-06 (entry 262): ten free plans per address without an Ofself session, counted in `var/allowance/`; sign-in imports the traveller's details and lifts the limit. `REQUIRE_SIGN_IN=true` locks every plan behind sign-in (entry 191). The deployed server still runs 191's rule until updated |
| **Ofself app** | "Visa Research Desk", app id `ed21d312-1c8a-487e-9de3-38ed61abb013`, client id `tp_hErNm3BbU_ISDz_Q703QpVIbTTZ87Ixt6H306ihMtAU`, incubator mode, redirect `http://localhost:8000/oauth/callback`. Design in [CRUX.md](CRUX.md); the DLR is live; details in TODO item 55 |

**Speed (entry 171).** A fresh request is ~55s: ~25s of research and ~29s writing the plan. The
graded runs of 2026-09-25 took 35–70s. A repeat within 24 hours reuses the model's draft (entry 178),
not timed. Across six corridors (entry 143) the stages split `fetch` 31%, `adjudicate` 29%,
`select` 23%, `search` 8%, `crawl` 7%, `corpus` 2%, with wide per-corridor ranges — read them as an
aggregate. The same corridor's model time swings ~40% between identical runs (entry 144).

**Cost (entries 159, 171).** A fresh corridor was $0.251 in model calls at OpenAI's direct prices
(selection 63%, roles 19%, plan 18%) plus about $0.054 of Brave search. Selection input has since
fallen ~42% (entry 195). A corpus build is 28–70 search queries and no model cost.

**Answers.** The last broad measurement is entry 58 (2026-08-24): 75% of twenty corridors confirmed
the decision and 50% yielded a checklist — a marginal pass, and really five destinations replicated
four times. Item 63's rounds are above. **Every number here measures whether it *answered*, not
whether it was *right*** (known problem 26).

**Search stays on every corridor** (entries 159, 160, 173): both conditional shapes were measured and
neither pays, and the purpose query finds pages no other query does.

---

## Known problems

**Numbering is append-only** — CLAUDE.md, ARCHITECTURE.md, TODO.md and code comments cite these
numbers. Each says what is true now; how it was learned is in the DECISIONS entry named.

2. **The trust rule's governmental half fails for governments with no hostname marker.** 16 of the
   51 countries measured have none (entry 65). The fix is a reviewed row per country with its
   evidence — TLS certificate, Wikidata, or the owner's judgement — never a wider regex (entries 33,
   66, 110, 111). All 55 current rows are done; every new country (item 64, parked) may need one.
   Schengen's visa decision is answered by the EU tier (entry 201). Frozen in
   `tests/test_trust_coverage.py`.
   TODO item 2, parked.

5. **A request with every cache cold has never been timed.** A fresh corridor through the web app
   measures ~55s (entry 171). The main live lever left in research is search, three queries per
   trusted domain (entries 141, 159).

6. **The domain cap is uncalibrated.** At most five of a destination's own domains are used (entry
   22); a country whose guidance spans six or more loses one, and `withheld_domains` is the only
   warning. Nothing reports whether an accepted domain set plausibly holds a visa authority at all.

8. **Nothing distinguishes "no checklist is published" from "we failed to find it."** Nobody can show
   a checklist does not exist, so the plan says what was found among the pages read, and names a
   likely checklist it could not open with its link (entries 153, 154, 213). A limit, not a task.

9. **The link scorer is a recall gate built on English vocabulary and city labels.** It decides which
   candidates the model selector may see at all; stored text now also admits five per role (entry
   158), so what the link score gates alone is the part of the corpus with no stored text. It will
   keep degrading on new languages. A post named as a bare path segment
   (`gov.si/assets/predstavnistva/new-delhi/…`) is still not recognised as another post (entry 72).

10. **The model calls are non-deterministic, and that is the main variance left.** Identical packets
    give different roles and decisions — Egypt `BD/SA` credits its decision 1 run in 3 on a
    byte-identical packet (entry 204); South Korea's wrong checklist is ~1 in 18. Because a refusal is
    never stored and a resolution is kept a week (entry 258), a flipping corridor is retried until one run
    resolves — the owner's open decision, parked under TODO item 7 (entry 151).

11. **Bot-blocked portals are a real limit, but not the largest.** `visa-discover audit` buckets every
    unreadable page by typed outcome. Most `403`s were challenges, answered by our renderer (entries
    73, 75); about half the challenges met cannot be answered because they need
    `challenges.cloudflare.com` (entries 179, 202) — `travel.state.gov` holds no stored page for
    this reason. Real refusals remain (Greece's `www.mfa.gr`), and every one is named, never worked
    around.

12. **Discovered pages have no staleness check.** A date read from the path is reported to the
    adjudicator, not used as a veto. Content-hash drift detection covers configured sources only.

13. **Scoring is English-only.** A destination publishing solely in its own language scores near zero.

14. **`xuatnhapcanh.gov.vn/en` is broken server-side** — `200` with `location:
    http://localhost:4000/vi` and an empty body; rendering does not fix it. The site root works.

15. **An authority's own outdated microsite is undetectable** — right domain, live, linked,
    text-rich.

16. **Mission detection by `_mission_domains` returns `[]` for every discovered destination**, because
    it reads `destination.sources` and the automatic path builds a config with none. Mission detection
    survives only through `mission_affinity`'s host-label check, so a consolidated portal (Brazil's
    `www.gov.br`, post in the path) is not told apart.

17. **The retrieval cache is not re-validated against changed rules.** Clear `var/cache/` when testing
    a retrieval change, for both arms of a comparison or neither (entry 136).

19. **The candidate set can vary between runs.** Largely answered by the corpus (entries 44–53, 58);
    open for runs days apart. Every run writes `var/recall/<corridor>.json` so it is diagnosable.


24. **A corpus build that loses a host to a transient failure may not notice.** Entry 207 made each
    build ask again for up to 100 transiently-failed *entries*; whether a host lost whole during a
    build is recovered has not been checked.

26. **Every number this project quotes about itself measures whether it *answered*, not whether the
    answer was *right*.** A pipeline replying "visa required" to everyone would score full marks on
    entry 58's 75%. Correctness is checked by the owner outside this repository (entry 68); **do not
    build a truth set, grader or accuracy metric without asking.**

27. **184 of 198 nationalities have no demonym**, so the nationality bonus misses "Kenyan nationals".
    Measured as costing nothing: every demonym-only match was noise (entry 70). Do not write 184
    demonym lists on a recall argument.

29. **The selection oracle has two travellers and one purpose.** Twenty `IN/GB` and `PH/PH` rows, all
    `tourism`; the same stores answer 47 of 60 roles for one traveller and 41 of 60 for the other
    (entry 91). Plus one row curated from the whole corpus (entry 127).

30. **The oracle cannot name an answer nobody could read.** Thirteen of its sixty roles are
    `unanswered` and twelve candidates `unverifiable`, so its recall is "recall over what is legible".
    The curation tool is offline and cannot judge a page the text index holds no body for — the
    product would simply fetch it (formerly also problem 37).

31. **A corpus serves the travellers whose pages an authority publishes, and no more.** For most
    residences the Netherlands publishes its checklist on VFS Global, which trust refuses; such a page
    is named with the government page that appointed it, never read (entry 89). Nothing verifies a
    delegate's URL still resolves.

32. **The Netherlands' per-traveller families are not fully walked.** `coverage` reads `incomplete`
    there (entry 88). Singapore's per-nationality pages are behind a selector, so no crawl reaches
    them. TODO item 35, parked.

33. **`coverage`'s known-answer half is two travellers, and it never votes.** Do not quote its 100%
    as evidence a corpus is ready; the verdict comes from the per-traveller family half alone (entry
    90).

35. **The coverage gate can only see families the corpus recorded**, so a country whose crawl never
    reached a family reads *no per-traveller dimension*, indistinguishable from one that publishes
    centrally. 37 of 53 read `ungraded` when last counted (entry 137), before the full rebuild.

36. **A correct "no visa required" can read as a thin corridor** in any metric that counts unanswered
    roles. The oracle records `not_applicable` only where a page answers `visa_decision` (entry 94).

42. **Nothing refreshes the stores on a schedule.** TODO item 20's weekly job is not built. Since
    entry 249 a page that cannot be refreshed is served from the cache flagged stale for up to 90
    days, so an old store costs a `verified` grade, not an answer; past 90 days it is refused. Entry
    248 had lost five corridors to the old 168-hour ceiling; all five resolve under 2160.

43. **The traveller profile cannot hold the facts transit and study turn on** — layover, leaving
    the airport, onward country, length of stay. Since entry 250 a plan states the answer for the
    side a source states and shows the fact beside it ("Only if …"), graded `partial`; no field was
    added, and the search queries do not need one. A corridor still refuses where no page about the
    purpose was read — Japan, Vietnam, Switzerland and Thailand transit.
45. **A few corridors still vary on the plan call alone** (entries 263, 264). Australia `US/US`
    answers "visa required" or null where its packet names no ETA, only "a visa or travel authority".
    Japan `IN/GB` study and South Korea `IN/IN` transit each gave one null in three. United States
    `GB/GB` now answers "no visa" every time, but holds on "you hold a qualifying e-passport", which
    is arguably an entry step.

46. **Home Affairs' ETA page is held out of the pool by the breadth penalty** (entries 265–267).
    It ranks 55th by stored text for `visa_decision` on Australia `US/US`, against about 20 places
    a role, because scoring on five roles multiplies it by 0.63. Australia's corridors now read it
    on every run through `config/always_read.yaml`, which settled `US/US` at 3 of 3. That entry
    covers this miss, and should come out once the ranking finds the page unaided.

**Retired numbers**, kept so the numbering keeps its meaning: **44** (entry 263), **1** (entry 58), **3** (entries 34,
38), **4** (entries 56, 57), **7** (entry 152), **18** (entry 42), **21** and **22** (entry 157),
**25** (entries 56, 57), **28** (entry 87), **34** (recall logs predating `RecallRecord.selector` are
refused rather than graded, by design — entries 91, 97), **37** (merged into 30), **38** (stale
failure reasons clear on rebuild, and every store was rebuilt 2026-09-25 — entry 92), **39** (entry
149), **40** (entry 150), **20** (no claim carries a quote since entry 227), **41** (the
selection oracle was refreshed against the rebuilt stores, entry 239).

---

## Working agreements

- **Update this file at the end of a session** — *Next session*, *Where it stands*, *Known problems*.
  A stale handoff is worse than none, because it is believed.
- **Keep it short.** History goes in DECISIONS; the queue in TODO; this file links.
- **Record decisions in [DECISIONS.md](DECISIONS.md) as they are made**, with the reasoning and what
  was rejected, and add the entry to its index.
- Do not record a problem as fixed unless it is fixed, or a result as verified unless it was run.
- Before handing off: `ruff check .`, `ruff format --check .`, `mypy`, `pytest`.
