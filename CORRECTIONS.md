# Corrections

Written-down diagnoses that a run then contradicted, one row each, newest at the bottom. Moved out of
[CLAUDE.md](CLAUDE.md) on 2026-09-24 so it is not loaded into every session; CLAUDE.md says when to
read it. Add a row whenever a run contradicts what one of these files said.

| what the file said | what a run showed |
| --- | --- |
| the trust rule's gap is in the TLD half | it is the governmental half (entry 33) |
| a blocked authority never reaches the plan | it reaches it by two routes (known problem 7) |
| consuming the corpus is slow because of *scoring* | it was `wrong_country`, 33× (entry 50) |
| removing the crawl risks *reporting* | reporting held; **qualification** broke (entries 55–56) |
| `visa_decision` needs its floor guard removed | the vocabulary could not recognise an answer (entry 56) |
| bot-blocks are the largest coverage limit | the **wizard** is (entry 58) |
| the UK's wizard page "was ranked, shortlisted and fetched" | for NG and PH; **not** for IN or CN (entry 59) |
| the UK answer is behind a tool we cannot drive | it is on a static URL — the reason not to is different (entry 59) |
| a wizard is a blockade in front of the guidance | it **is** the guidance, in the form published (entry 60) |
| the UK checker misses the shortlist because one host hogs places | it is **5th** for its role and three were reserved (entry 61) |
| a wider shortlist is the cheap fix for bad ranking | widening alone does nothing; the *per-role depth* is the gate (entry 61) |
| the scorer rewards *naming* a country, not being about one | it is token-based already; the page really is about India (entry 62) |
| a floor-only role score is safe to withhold bonuses from | a terse per-nationality decision page is floor-only too (entry 62) |
| the corpus ranks the answering page too low | it files it under the **wrong role**; no shortlist depth recovers it (entry 78) |
| junk anchors like "click here" are what lose the page | 2% of entries; the real case is a *good* 40-char label (entry 78) |
| a bigger page budget bought nothing, so depth is not the issue | 91% of links never cleared the *request path's* score threshold (entry 78) |
| BM25 is a safe way to pick what the real scorer sees | the answering page is 116th of 122 by BM25 (entry 78) |
| index text can just be assigned to `body_scores` | a zero would then *sink* a page for holding its text (entry 79) |
| the corpus-only arm lost its checklist to ranking | both pages were shortlisted **and fetched**; it is adjudication variance (entry 79) |
| ...so the checklist loss was adjudication noise | 3 of 3 identical runs: a stable *worse* answer, not noise (entry 80) |
| a lift that never lowers a score is safe | it protects the score, not the **place** — a shortlist is finite (entry 80) |
| the index made japan fill all six roles | one run; three of the same configuration give 3, 5, 5 (entry 80) |
| the lift ranked by who was crawled | the no-lift shortlist was **already 94% indexed** (entry 81) |
| ...so the lift cost japan two roles | six runs of *identical* code give 4,4,4,4,5,6 — it is inside the noise (entry 81) |
| role count measures a ranking change | it grades the adjudicator; the pages were shortlisted in every arm (entry 81) |
| raising the page budget will lift text coverage past the bar | 90% of candidates score zero and can never be shortlisted (entry 81) |
| ...so it is the even *split*, not the total, that starves a host | the UK's fee host went 15 → 20 nationalities; it was never budget-limited (entry 82) |
| a per-nationality URL space is crawlable, canada proves it | canada published a link index; the UK published a **form** (entry 82) |
| letting a productive host spend more is a clean win | the surplus goes to the *largest* host — gov.uk took 4,252 entries (entry 82) |
| a model picking 7 pages beats a heuristic picking 35 | it picked a landing page over its content child, with no redundancy left (entry 83) |
| ...so a model selector is worse than ranking | let it pick 20 and it finds 85% against 55%, reading half as many (entry 84) |
| ...and that +30 points is what it buys | four of those five corridors were the UK; over ten it is **+7** (entry 85) |
| ...and +7 is the selector's margin | that compared 35 picks against 11; at matched budget it is **+41** (entry 86) |
| the model lost the UAE and the US on thin text | at matched budget it wins the UAE and ties the US — it was the budget (entry 86) |
| "prefer fewer" is sensible advice for a page budget | a fetch is cheap and a missed role is not — it had the trade backwards (entry 84) |
| ...and +41 is the selector's margin | on an oracle neither arm built it is **+30**; both arms made the old one (entry 87) |
| a page proven to fill a role is one page | ICA publishes the same page at three addresses; the old oracle held one (entry 87) |
| the UK's per-nationality fee table is the answer it hides | it is keyed on the country you *apply from*, not the passport (entry 87) |
| a page titled "Document Checklist" is a checklist | `imm5484.html` is a download page whose text explains Acrobat Reader (entry 87) |
| a fetch-everything oracle is the automatable version of the same thing | it would still be URLs somebody fetched, so it inherits the alias bug (entry 87) |
| one oracle row per country is enough, the pages are the pages | the same store answers 47 of 60 roles for one traveller and 41 for another (entry 91) |
| a 100% held means the corpus is ready for that traveller | it is 100% of what *can* be answered; the denominator is the finding (entry 91) |
| ...and that 100% held is a finding for both travellers | curating from the corpus makes it circular; only `IN/GB` was curated wider (entry 91) |
| a keyed service not applying means the country cannot answer | one UAE page answers five roles for anybody; the row was three candidates deep (entry 91) |
| a page that answers a role scores something for that role | it answers `fees`, `times` and `entry` scoring **0.0** for all three (entry 91) |
| re-check a shallow row by ranking its unanswered roles again | that ranking filters on the link score, so a 0.0 page still cannot appear (entry 91) |
| the whole fixture needs redoing, the method was too shallow | the `IN/GB` rows already named the pages; only the new curation was thin (entry 91) |
| a corridor with no answers can be skipped when totalling | dropping the two that answered none read 24 of **48**, not 24 of 60 (entry 91) |
| singapore's three entry duties say what a visa-free list looks like | japan states ~5 and the UK ~7 — the range has no floor to pick (entry 96) |
| a visa-free plan has nowhere to apply, so force `where_to_apply` to null | a visa-free american still needs a UK ETA; forcing null deletes it (entry 96) |
| entry 95 named the three validators in the way | there were five — the checklist's third clause and the status grade (entry 96) |
| entry 91 fixed the log so it records which selector ran | it records which was *configured*; a failed model call logs as `model` (entry 97) |
| a model selector configured is a model selector that chose | four paths fall back to the heuristic, and a credit outage took seven (entry 97) |
| entry 95 named three validators, entry 96 found five | there are six — a guard in extraction reads an empty checklist as failure (entry 98) |
| the entry plan passed, so the no-floor decision is proven | three runs gave 6, 4 and 5 steps; the floor never bit (entry 98) |
| 46% text coverage in contention means the selector picks blind | France scores 100% at 7%; all 7 misses had text the model read (entry 99) |
| the UK's `PH/PH` row is the weakest, so look there next | it scores 2/5 and filled **every** role — the oracle names another page (entry 99) |
| `selection-recall`'s role recall says how well a corridor did | it says how well the model agreed with pages a person named (entry 99) |
| the UK row's misses are the oracle naming other valid pages | one is the *same document* at another URL; one is the oracle being wrong (entry 100) |
| a wrong oracle row makes the selector number untrustworthy | the errors run against the model, so 92% is a floor — leave it (entry 100) |
| a rebuild opens the addresses the last build recorded and skipped | it re-walks from search seeds; 2,965 pages crawled bought 27 entries (entry 101) |
| a family member nothing was discovered from was never fetched | it was fetched and linked nothing — 72 "opened" against 185 read (entry 101) |
| the netherlands is `incomplete` because its gateway is 39% walked | it is 100% walked; three of the families holding the verdict are out of scope (entry 101) |
| a family gate matching `apply` finds pages travellers apply on | it finds dutch citizens renewing passports; require a *visa* word (entry 102) |
| `document_checklist` is the gap that recurs, 8 of 20 corridors | that counts tool-settled roles; open is `general_entry` 7, checklist 3 (entry 103) |
| a role goes unanswered because the pages are not in the corpus | the three thinnest vocabularies are the three scoring **zero** candidates (entry 103) |
| widening a role's vocabulary will fill it | germany stays 0 of 83 — its pages are in the index, not the corpus (entry 103) |
| a term that raises a role's top score is a term that helps | `payment` raised it by promoting the checkout page over the fee table (entry 104) |
| a role with zero scoring candidates cannot be filled | germany fills fees and times off a page that entered as `application_route` (entry 105) |
| widening the vocabulary helps the model selector most | it helps the **heuristic** most, +12 points — that is the arm made of words (entry 105) |
| 21 unfilled roles is 21 problems to go and solve | 4 are correct, 7 just closed, 9 are two named causes (entry 106) |
| germany's corpus is thin because the crawl did not reach | it is 1,565 pages of **one host**: `diplo.de` is `unconfirmable` (entry 106) |
| the US render gap is application portals, expect little | it is `travel.state.gov` — the whole guidance tree, 0 pages stored (entry 106) |
| a domain the trust rule refused leaves evidence to review later | it leaves none — `is_crawlable` drops it before recording (entry 107) |
| the US corpus just needs the render budget france and sweden got | it got it; `travel.state.gov` still stores **0** (entry 108) |
| ...so the US challenge is one our renderer cannot answer | it is not a challenge — "you have been blocked", and we rendered past it (entry 109) |
| a cloudflare marker in the body means a challenge to answer | `cdn-cgi/challenge-platform` is on the **block** page too (entry 109) |
| the US corridor cannot be helped, the pages are refused | naming them turns a 503 into a plan: `resolved_decision_blocked` (entry 109) |
| `unconfirmable` is a shortlist of the authority's real domains | three for three the right one was absent — ask Wikidata, don't promote (entry 110) |
| a domain under the country's own TLD that migration bodies use is fine | `iom.sk` is the **International** Organization for Migration (entry 110) |
| `reviewed` in `authority_domains.yaml` means independently confirmed | it means a person decided — three tiers, each marked in the entry (entry 111) |
| the corpus is tuned for two travellers, expect a third to do worse | `NG/NG` scores **87%** against their 82.5%, on per-country pages (entry 112) |
| a reviewed domain is confirmed, so it is safe to ship | `gov.bg` is a public suffix; Bulgaria failed at *construction*, not at crawl (entry 113) |
| a bad page costs a build that page | one PDF's NUL bytes discarded China's whole 18-minute crawl (entry 114) |
| a shallow crawl needs a bigger page budget | the Philippines spent 425 of 1,200 and stopped anyway (entry 115) |
| `coverage` is the promotion gate for a new country | outside the oracle its verdict defers to an empty half — vacuous (entry 116) |
| a two-host corpus is starved, like germany's one-host one | HR and SI have exactly two domains and both are the right ministries (entry 116) |
| a TLS failure is a missing intermediate to bundle | egypt's certificate expired in May 2025; bundling cannot fix that (entry 116) |
| DK, LT and SK refuse every passport and no corpus will fix them | with corpora DK fills 4 roles and SK 2; only LT still fills none (entry 116) |
| a corridor exiting 0 answered its six roles | bulgaria exits 0 having filled two — the other four are silent, not counted (entry 116) |
| a country with a large corpus no longer needs live search | bulgaria holds 7,098 pages and gets its decision from a search-only pdf (entry 173) |
| the corpus write-back keeps every run's findings | it runs on the api path only; `visa-discover corridor` folds nothing back (entry 173) |
| liechtenstein fills nothing because its corpus is thin or german | it holds the right 29 pages; each stores a cloudflare interstitial (entry 117) |
| a challenge marker test only needs the top of the page | cloudflare puts `_cf_chl_opt` at index 24,915 of 29,336 (entry 117) |
| the renderer waits out a challenge until it clears | it polled on `is_challenge`, which said 'cleared' on iteration one (entry 117) |
| retrieval re-checks an answered challenge the way the crawl does | it checked thinness only; the interstitial became a citable source (entry 117) |
| the philippines is thin — it never finds a checklist | it is **visa-free** for an indian passport; no application, no checklist (entry 118) |
| lithuania's corridor is stopped by the robots `Disallow` entry 116 found | that limits its *corpus*; the corridor loses 8 pages to a challenge and 1 to NXDOMAIN (entry 118) |
| all five US role gaps are `travel.state.gov` | 4 are `uk.usembassy.gov`, never requested at all (entry 118) |
| purging the interstitials will surface `egov.uscis.gov/processing-times` | it is in neither the US corpus nor its index; purging never adds a candidate (entry 118) |
| the model flip costs a role, never a corridor | two US runs, no search: one refused, one resolved `resolved_decision_blocked` (entry 118) |
| a re-run adds a row to the recall log | it is keyed on the corridor — the baseline is **overwritten** (entry 118) |
| an oversized `robots.txt` means the authority publishes a huge crawl policy | 5 of 5 such hosts served a **web page**; not one was a policy (entry 119) |
| `no per-traveller dimension` is a verdict the families computed | it is a **deferral** to half one — and for 42 of 53 countries that half was empty (entry 120) |
| the 43 need oracle rows so the gate can grade them | 17 of 42 already resolve every passport; of the 9 that resolve none, 6 have a named cause (entry 120) |
| a country that has never resolved a passport needs curating | 5 of the 9 were last run **before their corpus existed** — re-run first (entry 120) |
| romania's `mae.ro` hosts answer 503, so the verdict will stand | `eviza.mae.ro` answers everything — romania fills **5 of 6** (entry 121) |
| austria loses 23 pages to a `Disallow`, so the verdict will stand | 6 now, and it fills the checklist and the route (entry 121) |
| a page with too little text to trust is a thin or broken page | morocco's is an F5 *"Request Rejected"* — a refusal served as **HTTP 200** (entry 121) |
| a country reported as having no per-traveller family has none | romania has **58**, named in romanian; the detector matches english slugs (entry 121) |
| a sibling-run probe can count families without a country list | it missed romania's own — its members sit under different numeric parents (entry 121) |
| `str(exc)` is a reason | `httpx.ConnectTimeout` carries an empty one: *"the request failed ()"* (entry 122) |
| the model selector replaced the shortlist, so ranking no longer gates recall | it pools `score > 0` — the model is shown **6%** of the corpus (entry 123) |
| the model-vs-heuristic numbers are invalid if ranking gates the pool | both arms filter `> 0`; they raced the same 6%, the comparison stands (entry 123) |
| "100% role recall" means the selector found what the corpus holds | the oracle was curated from candidates scoring above zero (entry 123) |
| entry 91's 0.0 UAE page proves the pool gate hides answers | its `best_combined()` is **49.6** — it is *in* the pool; that was a per-role filter (entry 123) |
| check the pool gate by finding oracle pages that score zero | 88 of 88 are in the pool — a fixture curated from the pool cannot name one outside it (entry 123) |
| the pool gate is why liechtenstein and romania fill little | romania's discards are legislation pdfs; the miss was a **missing residence score** (entry 124) |
| the scorer weighs passport against residence, so retune it | there is **no residence signal at all** — only a passport one (entry 124) |
| canada ranks the right per-residence page below the wrong one | it scores the wrong one **32.0** and the right one **0.0** — not a ranking, an absence (entry 124) |
| romania's family is invisible because it is named in romanian | its anchor text is english; `country_family_keys` reads the **URL** only (entry 124) |
| the family detector needs 198 country names in every language | the blind spot is english **aliases and territories** — `czech-republic`, `kosovo` (entry 124) |
| search is load-bearing for 17 roles, so the corpus is not ready | the gate admits search at 49% and the corpus at 5.5% — that 17 is an upper bound (entry 125) |
| adding a residence bonus fixes the page scored below its wrong sibling | adding without withdrawing gives a **tie**, 32 against 32 — they must swap (entry 126) |
| ...so the two bonuses must swap, or the defect stands | the swap took **25 pages out of the pool and added none**; the tie was the answer (entry 126) |
| the scorer ranks the candidates the model then chooses between | the pool goes to the model **unsorted**, scores withheld — ranking is consumed by nothing (entry 126) |
| a page whose path ends `/united-kingdom` is a page about the UK | the token carries a space and the segment does not — every multi-word country was invisible (entry 126) |
| a demoted page is safe because it keeps a positive score | 37 role-scores fell to zero or below, and zero is the whole gate (entry 126) |
| the per-country dimension is 21 corpora and 5,901 pages | the *application* families inside it are **4 corpora and 944 pages** (entry 126) |
| `country_family_keys` misses a family when the slug is foreign | it also misses Canada's `?country=IN` — a two-letter code below its floor (entry 126) |
| a residence bonus widens the pool the anchor scorer gates | 12,573 → 12,583 of 186,596 — it re-orders the 6%, it does not widen it (entry 126) |
| the discarded 94% is chaff, so the gate may be fine | czechia's UK supporting-documents list is in it, at **0.0 for every role** (entry 127) |
| curate the gate's blind spot from the corridors with the smallest pools | LI's whole discarded set is its law collection — the answer was in CZ, pool 268 (entry 127) |
| `selection-recall` can grade a change that widens the pool | its oracle shares the filter, so 88 of 88 was a tautology until entry 127 |
| the discarded 94% holds answers, so widen the gate | 19 of 21 corridors lose **nothing** to it; the prize is 3 roles of 35 (entry 128) |
| a relevant page outside the pool is a page the gate cost us | not if the pool already answers that role — measure the **marginal** cost (entry 128) |
| "19 of 21 lose nothing" is the measured result | 19 of those rows were curated *from* the pool; of the 2 curated outside it, **both** lose (entry 128) |
| a role the corpus cannot answer is a crawling gap | **40% are an official tool** holding the answer — not a gap at all (entry 129) |
| search finds hosts the trust configuration missed | all 78 pages it supplied are on hosts the corpus already crawls (entry 129) |
| a finding in one traveller's oracle row is that traveller's | the Dutch EES leaflet was already true of the other row; nobody had looked (entry 129) |
| take search out of the request path | 18 of 25 load-bearing pages are countries with a **named permanent ceiling** — make it per country (entry 129) |
| a page the corpus lacks is a page the crawl could not reach | 52% of hosts were entered below their root, which was never visited (entry 130) |
| a superset check over `canonical_key` is exact | it counts `…/en/index.html` as missing when a run fetched `…/en`; 1 of 78 here (entry 130) |
| thailand's corpus is 4,393 pages, so it is well covered | 2,617 are **provincial office** WordPress sites; the national site has 41 (entry 130) |
| a huge host eats the crawl budget | it does not — TH opened 41-42 on *every* host; fair shares between unequal hosts (item 48) |
| the per-host budget counts an authority once | `host_of` keeps `www.`, so BG's interior ministry took 3 shares and 72% of the budget (item 48) |
| a corpus failure reason describes the site | BG's "redirected off the approved domains" is Radware's CAPTCHA host (entry 131) |
| check a suspect crawl failure by opening the URL in a browser | a browser passes the bot check the crawler is failing — it cannot see it (entry 131) |
| a rebuild will clear a stale failure | 7,149 pages crawled bought 193 entries and the failure count went **up** (entry 131) |
| the corpus holds a country's mission network, so it holds the post that serves you | AU holds 1,599 pages on `embassy.gov.au` and **0** on `uae.embassy.gov.au` (entry 132) |
| "too little readable text" means the page is thin | 12 of them were one host that ate the corridor's whole render budget (entry 132) |
| the request path has 12 renders per corridor | 12 is the *crawl* fetcher's; the shortlist shares **5** in one `fetch` call (entry 135) |
| a page reported as having too little readable text was read by a browser | it may have been rendered, or never opened — one sentence said both (entry 135) |
| cap a greedy host with a share of the render budget | a share throttles a host where rendering **works**; count consecutive failures (entry 135) |
| capping a greedy host recovers the roles it was hiding | AU is 4/6 before **and** after — a cap stops it starving *others* (entry 136) |
| clear `var/cache` to test a retrieval change, then compare | a cold arm against a warm baseline is not a comparison: blocked 1 → 15 (entry 136) |
| a two-point drop after a change is the change's cost | 28 extra **network** failures explained it; the code contributed nothing (entry 136) |
| the corpus is tuned for three travellers, a fourth will do worse | `BD/AE` filled **88%** over 27 countries, 75% of it corpus-served (entry 132) |
| the corpus missed the UAE post because search never surfaced it | **24 of 27** hold no post for *either* residence tried (entry 133) |
| a post absent from a corpus is a post the crawl could not reach | AU holds its Riyadh post and 0 on its Dubai one — same domain (entry 133) |
| `Country.mission_labels` is even enough to measure posts with | AE carries six, SA carries **one** — it cannot match `saudiarabia.embassy.gov.au` (entry 133) |
| enriching mission labels only helps, it is a lookup table | it widens the **-45** too; 141 pages left Germany's pool (entry 134) |
| ...so enriching them cost recall | all 141 were other countries' German missions; 0 of 154 oracle pages moved (entry 134) |
| the nine countries with new domains still need a corpus rebuild | all 53 corpora already carry their current domains — nothing to run (entry 123) |
| the interface tells a challenged authority it "does not permit" retrieval | `challenged` is its own outcome; `app.js` branches on `blocked` (entry 123) |
| the grader compares a model against a heuristic | nothing recorded which selector ran; six logs put the heuristic in both arms (entry 91) |
| a corpus that holds a page can serve any traveller who needs it | it holds 219 apply pages and **five** checklists; the leaf is a hop deeper (entry 88) |
| a gateway yields more children than a leaf, so count them | 2.4 apiece against 1.5 — ask if the child is *per traveller* (entry 90) |
| the netherlands is the covered case, entry 88 finished it | three complete families were never opened; it reads `incomplete` (entry 90) |
| CA, JP and GB have no per-traveller family | GB has one — the fee wizard; `_queue` groups per *page* (entry 90) |
| the corpus-sufficiency number is 47/47, so build the gate around it | the gate's verdict ignores it; one traveller cannot outvote 197 (entry 90) |
| the corpus is thin because the crawl did not go deep enough | it opened 3–15% of what it recorded; the rest are addresses (entry 88) |
| reserving budget for a family is enough to reach it | one pool is score-ordered, so the fee family took all of it (entry 88) |
| a family key can blank the first country it finds | the destination is named in its own path; all 219 got different keys (entry 88) |
| opening the gateway buys a checklist per residence | 113 of 185 link nothing — the checklist is on VFS Global (entry 88) |
| the biggest country-shaped family is the one worth crawling | Canada's is 176 travel advisories, Japan's 141 country pages (entry 88) |
| a contractor's checklist is a ceiling on what we can offer | it is a ceiling on *reading*; naming it was always allowed (entry 89) |
| the authority's own page linking it is warrant enough | the link comes out of HTML, which is untrusted content (entry 89) |
| most contractor links are the guidance | 44 of 236 are "track your application"; 30 are documents (entry 89) |
| singapore is the next family reservation win, it fills five roles | its page is a leaf, and ICA's index yields 6 children not 198 |
| the corpus build does not answer a browser challenge, unlike the request path | it always did; the budget was **12** renders against France's 64 challenges (entry 92) |
| france is the corpus the render budget cost most | sweden lost 216 pages to it against france's 66 — count before crawling (entry 92) |
| rebuilding france fixes its two weak oracle rows | 92 newly readable pages bought **one** role; the rest is behind the wizard (entry 92) |
| the wizard states the 3 roles, so reading it would answer them | its first step needs 4 fields a corridor lacks; 2 of them change the answer (entry 92) |
| ...so a role behind a tool is a gap in the coverage metric | the product has called it *resolved* since entry 63; only the metric disagreed (entry 93) |
| france answers 2 of 6 for a filipino traveller | 2 by page and 3 by the wizard — the traveller can act on **5** (entry 93) |
| singapore answers 2 of 6, so its corpus is thin there | 4 of the 6 questions do not arise — no visa, so no application (entry 94) |
| a corpus build records whether it could read a page | it wrote only `unreadable` or `unknown`, so a stale failure never cleared (entry 92) |
| raising the render budget is the whole fix | an unanswerable host would then spend 400 renders proving it — cap per host (entry 92) |
| the corpus is not yet good enough to serve a corridor alone | for `IN/GB` it holds 47 of 47 answerable roles, and did before entry 88 |
| so a corpus-sufficiency number settles it | that one is blind to the traveller dimension; 100% and uninformative |
| a page per nationality is the real nationality risk | not one of 41 countries had that shape; the shape is the **post** (entry 70) |
| a missing demonym can cost the answering page its place | the 22 places demonyms won were all noise, none filled a role (entry 70) |
| an outright `403` has not cost a corridor yet | Lithuania and Slovakia lose their whole trusted set to one (entry 70) |
| a wider sweep only tests the countries it runs | breadth found two defects five countries never could (entry 71) |
| a challenge just needs a longer settle | 9,000ms is *worse* than 2,500ms — it races the redirect (entry 75) |
| japan's corpus misses the london embassy on recall | that host answers a genuine `403`; nothing can fetch it (entry 77) |
| the corpus exists to reach depth the request path cannot | it exists for **latency**; both paths must find the right page (entry 77) |
| a corpus is judged by how deep it crawled | judge it by its hit rate on role-filling pages — Japan 3/5 (entry 77) |
| Cyprus's `403` is a refusal, so entry 41 does not apply | Azure declares its challenge in the **body**; it is answerable (entry 73) |
| three countries send a UK resident to their New Delhi post | Brazil sent them to Edinburgh; only one case was real (entry 72) |
| treating another country's label as another post is the fix | it broke 165 correct pages — the destination's own code (entry 72) |
| a build seeds from search, so seed the mission index from search | 44 of 53 corpora already record it and 34 never opened it (entry 137) |
| so open the mission index where the corpus recorded it | its far end is depth 4 against a ceiling of 3 — only a seed reaches it (entry 137) |
| the family gate finds the per-traveller families a corpus holds | it refused Australia's largest: `…/australian-embassy-{}` has no visa word (entry 137) |
| canada has no per-traveller family, `coverage` says so | it has one — `travel.gc.ca/assistance/embassies-consulates/{}`; the gate could not see it (entry 137) |
| widening a crawl gate only changes the crawl | `coverage` shares it: `ungraded` went 42 → 37 and bulgaria became `incomplete` (entry 137) |
| the mission index yields 194 members for the reservation | it groups **70**; 124 name their country mid-address and form no family (entry 137) |
| seeding the index gets the post into the corpus | it gets the *family* in: 1 member recorded → 166, the post still 0 (entry 138) |
| a rebuild's new mission hosts are the new seed working | all ten came from their own search seeds; one page came from a mission page (entry 138) |
| a reserved family queue opens its members | all members score 0.0, so the order is the page's — the alphabet's tail is never reached (entry 138) |
| order the family queue by the traveller's residence | a corpus build has no traveller — order on what the **store** lacks (entry 139) |
| giving up on a host that will not answer loses pages | it freed the budget: +445 entries, +14 hosts, reads 3 → 12 (entry 139) |
| the ordering fix will reach the post it was built for | 6 of 11 new hosts came through the directory; the UAE is 169 members deep (entry 139) |
| the goal is a corpus that answers without searching | the goal is **latency**; the owner's re-scoping keeps search in the flow (entry 140) |
| adjudication is ~60% of a corridor, optimise there | `search_all` is 19.0s of 27.4s — it was timed at 2 domains, not 5 (entry 140) |
| the search phase is the search engine | one query is 1.0s; 18.2s of the 19.0s is our own pacing lock (entry 140) |
| a goal stated in seconds has seconds behind it | the recall log records no timings at all — nobody could produce the number (entry 140) |
| the search pace is protecting a rate limit | brave allows **50 q/s**; the lock paced at 0.77 — 65× too slow (entry 141) |
| 70 queries failed four-at-a-time, so pacing fixed it | that outage was a **spend cap**, which pacing cannot affect (entry 141) |
| going faster costs more | the same 15 queries are sent either way; pace is free (entry 141) |
| a corridor's role count after a change is that change's doing | a transient 500 and a spent render budget, with no matched baseline (entry 141) |
| adjudication is the model cost in a corridor | **selection** is bigger — 7.3s against 6.1s, and nobody had priced it (entry 142) |
| with search fixed, the rest is adjudication | **fetch is 43%** and was never the suspect (entry 142) |
| a phase named `crawl` measures crawling | it is the span between two steps — 2.0s on a run that skipped the crawl (entry 142) |
| fetch is 43% of a corridor, optimise it | that was one corridor; over six it is **31%** and the model calls are 52% (entry 143) |
| one corridor's timings say where the program spends time | fetch ranges 14–51% — every corridor names a different winner (entry 143) |
| the page-related stages scale with pages read, so read fewer | CA reads 20 in 25.8s, JP 16 in 43.0s — page count is not the variable (entry 143) |
| a slow model call was given more to read | r=+0.33 and +0.48; NL sends the 2nd-biggest packet and is fastest (entry 144) |
| the 4× adjudication spread is a property of the corridor | the same corridor swings 40% between identical runs (entry 144) |
| time a model change on one corridor before and after | germany moved 69% on its own — entry 81's rule, new place (entry 144) |
| input size does not matter, entry 144 measured it | that was latency; it is **96% of the money** (entry 145) |
| output tokens are the expensive half of a model bill | 588k in against 4k out — input is 96% of it (entry 145) |
| a reasoning model bills reasoning on top of output | it is inside `output_tokens`: 185 out, 131 of them reasoning (entry 145) |
| prompt caching will cut the selection bill | only for a corridor just run; six distinct ones cached 2,029 tokens (entry 145) |
| put the country-stable part of the packet first and it caches | reordering alone is ~500 tokens, under the 1,024 minimum (entry 146) |
| the traveller block is what breaks the cache prefix | it is five things; the fifth is the corridor-dependent pool gate (entry 146) |
| a traveller-independent candidate set means showing far more | JP's three pools intersect at 93% of their union — about 7% more (entry 146) |
| the goal is latency, so search should leave the request path | the goal is **right information**; latency and cost are the constraint (entry 147) |
| search is justified because it finds pages nothing else does | that is recall; nothing here measures whether the answer was **true** (entry 147) |
| a corpus gap search already covers is next in the queue | per-traveller pages are search's job; the corpus holds what all share (entry 148) |
| `visa-discover corridor` measured what the web app served | for SG and JP the app served hand-pinned pages, the command ran discovery (entry 149) |
| a hand-pinned checklist hands a Filipino London's list | the model declined it and a guard turned that into a 503 (entry 149) |
| a plan with an unconfirmed decision is never `verified` | only a block or a tool downgraded it; a model's own null did not (entry 150) |
| the US flip was the blocked-page judgement answering both ways | the refusing run filled no role, so it refused whatever that said (entry 151) |
| a repeat corpus-routed US run spends no search | search runs on every corridor — 15 queries a run (entry 151) |
| a refusal is not retried, only re-searching on refusal would do that | refusals are not stored, so the next request retries them (entry 151) |
| a stored corridor only risks serving an older page | the US plan came from one 16 days old and omitted the London embassy (entry 152) |
| a corridor's notes tell the traveller what was not requested | notes never reach a plan or the interface (entry 152) |
| a missing checklist means none exists or we failed to find it | nobody can show none exists — say what was found among pages read (entry 153) |
| a plan with no checklist only hedges about what it could not find | the prompt told the model the authority "publishes no document checklist" (entry 153) |
| a reviewed per-country declaration can settle that none exists | a reviewer can no more prove absence than the pipeline — withdrawn (entry 153) |
| a recall log's `fetched` marks every page a run tried to read | only pages it read; a refused page is not `fetched`, so count failures apart (entry 153) |
| the refused-page judgement picks the same pages each run | 08-29 marked `visitor.html`; 09-14 marked the India page and not it (entry 155) |
| a model's copied quotes need fuzzy matching to verify | 80 of 80 matched after normalising only spacing, quote marks, dashes and case (entry 156) |
| a quote found on the cited page supports its claim | it proves the words exist, not that they fit — one decision quote was a caveat (entry 156) |
| capping the pool is the most attractive fix for the gate | it displaced 1,813 pooled pages with no text, and 5 the fixture names (entry 158) |
| reading stored text at the gate adds seconds to every corridor | step 3b already scored every candidate's text, then threw it away (entry 158) |
| admitting every page whose text scores fixes the gate | it recovers the same 4 answers at +68% input, mostly chaff — five per role costs +16% (entry 158) |
| czechia has never been run, so it has no recall log | it had one from 08-25, before its corpus and before logs named a selector — ungradable, not absent (entry 158) |
| a search page's recall log names the query that found it | not if the corpus holds the same page scoring higher — the corpus record replaces it (item 51) |
| searching only when the corpus leaves a gap saves what search costs | a gap corridor redoes select, fetch and adjudicate — projected −3% money, +4% seconds (entry 159) |
| a corridor without search is a much cheaper corridor | search is $0.054 of $0.322; the model calls barely shrink without it (entry 159) |
| a role the corpus fills needs no search | Japan and UK `PH/PH` filled it with a general page; search held the traveller's own (entry 159) |
| a corpus holding pages from a post holds that post's answer | Japan holds 5 London-embassy pages and none of the 4 that answered (entry 159) |
| a role a corpus-only run leaves open is a page the corpus lacks | 6 of 9 were on pages it holds — the model picked differently (entry 159) |
| the purpose query only finds what the corpus already holds | first to return 45 of 194 search-only reads, 25 checklist-shaped (entry 159) |
| the purpose query carries no traveller detail, so it finds nothing traveller-specific | `site:` a post's domain, it returns that post's checklist — Japan's London embassy, Norway's India PDF (entry 160) |
| "first query to return a page" overstates what a query alone finds | re-issued, 28 of 45 purpose-first pages came back from the purpose query alone (entry 160) |
| role counts show what dropping a query costs | it swapped pages: Norway's checklist became an older file, Japan's a questionnaire (entry 160) |
| seeding a host's root reaches the pages a build entered sideways | 0 of 9 targets from 8 roots — Thailand's root *is* the form, Japan London's answers 404 (entry 161) |
| a page a build's search returned is a page its corpus holds | a seed was kept only if something linked to it — Norway's rebuild kept 174 of 198 (entry 161) |
| the corpus lacks what only live search supplies | the build's own search returned Norway's checklist and TDAC's form, and threw them away (entry 161) |
| a PDF a build's search returns is read in the PDF pass | that pass read only linked PDFs; a PDF seed was never read at all (entry 161) |
| a kept seed only matters when live search misses a page it usually finds | Thailand's decision PDF comes from a build query corridors never ask — old corpus 0 of 4, new 4 of 4 (entry 161) |
| a page the corpus holds and pools is a page the model reads | Norway's January 2024 checklist was pooled and passed over for its 2018 sibling (entry 161) |
| one failed search query costs a corpus build one query | `search_all` raised and the command exited — Japan's whole build lost to a DNS blip (entry 162) |
| a host is a site for a crawl's budget | `host_of` keeps `www.` — 44 of 53 corpora held a split site taking two shares (entry 163) |
| catching `httpx.HTTPError` catches every way a fetch can fail | a malformed redirect raises `httpx.InvalidURL`, which is not one — it ended China's rebuild (entry 176) |
| a 53-country corpus rebuild is about $18.55 of search | measured 2026-09-15: ~2,590 queries, ~$13, ten hours two at a time (entry 161) |
| a corridor's model cost is the selection and roles calls | the plan-writing call runs on every web request, stored corridors too, and was never priced (entry 164) |
| the recorded tokens price the model calls exactly | OpenAI bills cache writes at 1.25× on GPT-5.6+ and the recorder never reads them (entry 164) |
| japan's 93% pool overlap makes a traveller-independent pool cheap | across four travellers JP, DE and GB overlap 79–80% (entry 164) |
| a selection packet's tokens are candidate evidence | 13–40% is repeated notes and JSON layout; half the UK's excerpt text is lines repeated across pages (entry 164) |
| a cache write happens only where a prefix gets reused | on gpt-5.6-terra all 24 live calls wrote every uncached input token but 3 (entry 167) |
| the plan call could be a large share of a request | 13% of a fresh corridor, and 69% of its own cost is output (entry 167) |
| a corridor priced at $2/M input is priced completely | written prompt tokens bill at $2.50/M and this model writes nearly all of them — a fifth low (entry 167) |
| the usage dashboard can check one sweep | it buckets by day, and its costs implied fewer tokens than the window alone logged (entry 167) |
| a cache only pays for itself once there is traffic | implicit caching was *costing* a fifth on every call; explicit mode stops that with no traffic at all (entry 169) |
| stripping boilerplate from excerpts cuts selection cost | excerpts are cut to a budget, so it saves 0–4%; the repeated notes and layout were the cost (entry 170) |
| a leaner selection packet costs recall | 39 of 48 roles with either packet over ten corridors, at −31% input — one run each (entry 170) |
| a plan that turns `partial` after a selection change was caused by selection | Singapore's pages were identical; its plan call leaves the decision open in both message shapes (entry 171) |
| time windows alone attribute a sweep's calls to its requests | a request's last call lands in the second the next starts — match on corridor too (entry 171) |
| a fully cached plan call is a cheap one | the five warm repeats read their whole prompt from cache and cost about the same — output (entry 167) |
| short source ids in the plan packet change nothing a traveller reads | 2 refused plans and 2 wrong "no visa" for Japan in 48 calls; none in 48 on today's ids (entry 174) |
| rule 8e's bounds are proven, Singapore checked them | Japan's exemption list held 16 of 16 — and a packet change broke it twice (entry 174) |
| trim 2 shortens a quote by quoting the checklist heading | it drops the second quote: under-40-character quotes 7% → 5%, mean 86 → 81 characters (entry 175) |
| a prompt that shortens the plan's prose shortens its wait as much | visible plan −5–13%, billed output ~150 tokens: hidden reasoning did not shrink with it (entry 175) |
| a faster, cheaper model is a safe latency lever for the plan call | `gpt-5.6-luna` halved it and wrote Japan a "no visa required" plan (entry 177) |
| reasoning effort `none` only costs detail | Japan answered "visa required" 3 of 3 where no page states it (entry 177) |
| Fast mode speeds every call about the same | plan −43%, selection −23%, roles −13% — it speeds generation, and selection is mostly fixed (entry 177) |
| item 5 is undone — a `403` never reaches the renderer | built 08-25; four files kept saying otherwise (entry 179) |
| the challenge scripts are same-origin, so answering one trusts nothing new | 12 of 24 need `challenges.cloudflare.com`; the gate aborts it and all 12 stay challenged (entry 179) |
| france's corridor loses its portal to the challenge | to the budget — 14 of 17 challenged pages were never rendered (entry 179) |
| lithuania's challenge fingerprints past the user agent | what was seen is the gate aborting cloudflare's script; passing with it is untested (entry 179) |
| a web page at `/robots.txt` should close the host | 401 origins, 4,687 read pages; the one real policy among them is already obeyed (entry 179) |
| a DLR asks for a whole schema; only the user can narrow fields | `dlr add-read --fields` asks for single fields; the app reads `citizenships` alone (entry 180) |
| a better heuristic could replace the selection call | at the same page count the best reaches 60% to the model's 83%; it can only shrink the packet (entry 183) |
| builds open zero-scoring pages because of frontier order or the family share | an even per-host split drops the visa host's scored links at the cap — Japan's decision page among them (entry 185) |
| the pilot's 75 → 68 is the selector choosing worse from bigger pools | five runs each: −2.8 of 90, all in secondary roles; decision + checklist rose 23.2 → 25.0; four "lost" roles were noise or a month-old OpenAI baseline (entry 194) |
| a fixed text budget is why a big pool loses — give the model more text | doubling it to 800k characters made recall worse, 73.3 against 76.6; a shorter list helped (entry 194) |
| stripping boilerplate changes what the selector reads for nothing (entry 170) | it changes picks both ways — UK checklist 1/5 → 5/5, Germany's three roles 3/5 → 0/5 — and nets zero (entry 194) |
| strict role recall grades a packet that reorders the pool | fusion top 160 read 70.0 strict and 78.3 counting the same page at another address (entry 194) |
| 14 corridors read their authority's pages and credited no decision — evidence read and not credited | re-run three times each, the roles call was right on every packet it refused; what broke was two vetoes, a depth penalty, the selector, the plan's size guard and its status check, and pages that are an image, unreadable, or EU-level (entry 198) |
| the UAE `IN/GB` read the answer and no decision was credited | the decision was credited; the application graded the plan `verified` beside a page discovery found refused, then refused its own plan (entry 198) |
| Italy's extracted list lost which list Bangladesh is on | the adjudicator credited it and the plan said "visa required" 3 of 3 (entry 198) |
| the selector needs only a page's head: a page whose head does not say what it is will not be saved by a window further in | Slovenia's New Delhi page says what it is in its head and states the answer 8,000 characters later; head only, picked 2 of 5 on a fixed packet, 5 of 5 with windows around the traveller's country (TODO item 70) |
| Belgium's list was lost to the depth penalty, so stored PDFs should escape it | the list's link states the answer and the lexicon could not read it; teaching the phrase recovers it with no pool changing size, where the PDF rule grew the US pool 60% (entry 199) |
| the archived-year veto misses three address forms, and dated news can be kept out by widening only by form | Cyprus's re-run showed a fourth, `gov.cy/media/2024/06/`; and a post permalink cannot tell South Africa's Juba application pages from Indonesia's press releases, so both are admitted and the adjudicator reads the date (entry 203) |
| Egypt `BD/SA` falling from 3 of 3 to 1 of 3 after its rebuild is the rebuild | its three roles packets were byte-identical and held the deciding sentence; the model credited it once in three (entry 204) |
| Brazil's first store is thin, so a re-run should let `brazil/IN/GB` answer | the re-run doubled the store and the selector picks the visa table, which `gov.br/robots.txt` disallows; the refusal is correct (entry 204) |
| after the full rebuild, next are items 67 and 64 (the rebuild sequence's last step, copied into the handoff) | 67 waits on OpenAI credit, since Personas offers no embeddings, and 64 asks the owner before a batch; the queue was re-surveyed: 63, then 57, then 64 (TODO *Now*, 2026-09-25) |
| the null decisions in item 63 are the corpus missing the answer | three of four had the page credited by the roles call and held at null by rule 8f, for want of a stated general rule (entry 205) |
| a "verified" plan quoting its authority is safe to show | South Africa's quoted the row "India / 90 Days / 90 Days / 90 Days / No" and said no visa; the empty *Ordinary* cell was lost when the table was flattened (entry 205) |
| Thailand's "no visa, 60 days" was right because the ministry's latest list says so | the stay had fallen to 30 days on 15 September; the ministry's list was stale, its May revision had been a `429` never asked for again, and the change was stated on a domain Thailand's row did not list (entry 207) |
| Spain's consulate pages "mostly fail to render", so its checklist is lost to the render budget (entry 212, the handoff) | they never needed a browser: each answers `200` with its full text, and the cleaner deleted the `<form>` ASP.NET wraps round the whole page, leaving 14 of 13,870 characters (entry 215) |
| Australia's step-by-step page was never rendered because incidental Home Affairs pages used the host's strikes first (entry 212) | Home Affairs' CDN answers every render with Akamai's "Access Denied"; no ordering would have reached it (entry 216) |
| South Korea's checklist address "answered with the site's front page" (entry 212) | the ministry sends our client to its waiting room; `curl` gets the 15-page PDF, and its tourist section names the visa, C-3-9 (entry 216) |
| South Korea's corridor request for its checklist PDF "is sent to the ministry's waiting room" (entry 216) | the run of 2026-09-25 landed on `/returnInfoPage.do`, the ministry's directory of missions; only a probe met the waiting room (entry 219) |
| Japan `IN/GB` open ×2 on 2026-09-25 looked like a regression (entry 215) | the next two runs were `verified`; it was selection and roles variance (entry 219) |
| Thailand's refusal was a 1-in-18 selection miss, and Japan's open decisions were variance (entries 215, 219) | replayed, Thailand's selection missed 4 of 20 on the same packet, and Japan's roles call credited the list only 6 of 10; both were structural — a decision resting on two pages judged one at a time (entries 221, 222) |
| item 67's bar at the pre-filter cut is `fusion_order`'s 80.0 of 90 | 80.0 is the selector's score on that cut; the cut itself keeps 91 of 92 oracle roles, so no ranking can gain there (entry 236) |
| embedding the oracle pools on Voyage's free tier is slow because of the volume | 4.6M tokens took two minutes with a card; without one, each 32-chunk request exceeded a minute's token allowance and was refused (entry 236) |
| the selector on an embedding-ranked 40 + 40 packet loses 7.6 roles a run, over the bar (entry 236, first draft) | half the lost role-runs picked a page stating the answer that the oracle, curated before the rebuild, does not list; corrected it is 1.6–3.8, at or under the bar (entry 236) |
| the smaller embedding-ranked packet is roughly level, 1.6–3.8 roles a run behind by hand (entry 236) | re-graded on the refreshed oracle it is 1.6 behind, under the bar, and the cut at 40 keeps 109 of 111 answers against today's 91 (entry 239) |
| the UK and US Indian rows cannot state a visa decision; Germany's checklists are outside the trusted set (the oracle, 2026-08/09) | the UK visa-national list and the US Visa Waiver list name the answer, and the rebuilt stores hold Germany's missions (entry 239) |
| today's ranking degrades as pools grow, so embeddings would matter more at scale (TODO item 75, from pool size across corridors) | growing the same pools with every stored page outside them leaves today's order at 91 of 111 at a 40 cut and 111 at 120; the difference was between countries, not sizes (entry 240) |
| fixing the stored-text bonuses would lift exactly the pages both smaller shortlists lose (entry 244) | offline it recovered 8 of the 22 answers today's order drops at a 40 cut; with the search boost the selector got back 2 of the 3 lost roles, not France's Indian processing time (entry 245) |
| with the boost at 60 + 40 what is still lost is France's Indian processing time (entry 245, from the replay) | live, the Indian traveller's was filled in every run of both arms from `…/en/royaume-uni`; the Filipino traveller's was lost, 0 of 3 against 3 of 3 (entry 246) |
| a Personas call hung 83 minutes because the client timeout is per read (draft entry 251, never pushed) | the Mac went into clamshell sleep 9 seconds before the run started and slept until it was found (`pmset -g log`); a deadline on the monotonic clock would not have fired either. Dropped with its fix (entry 250) |
| India publishes no travel advice found (entry 259) | the first advice survey found `mea.gov.in/travel-advisories`: India advises its citizens on 11 countries — Myanmar, Russia, Ukraine, Iran, Yemen, China, Iraq, Syria, Libya, Cambodia, Niger — and marks the rest as having none (entry 260) |
| New Zealand `US/US` answering both ways is two defensible readings of 8g, from different pages (known problem 44, first written) | two live runs that read the same three pages gave opposite answers, and replays of one packet split 5 to 5: the model, not retrieval (entry 263) |
| the stored-text scorer credits a nationality "in the title or the URL, not anywhere in the text" (`score_body`'s comment) | it matched with a substring test, so the United States' "us" fired on "australia", "business" and "about-us", and Peru's "Passports for Australians" scored as written for Americans (entry 266) |
| adding Tourism Australia's visa FAQ would confirm Australia's decision (the owner, entry 265) | it reached the selector's pool in 3 of 3 runs and was never picked: its own text says Tourism Australia is not the visa authority (entry 265) |
| storing Home Affairs' ETA page with `corpus-add` would let Australia `US/US` find it (entry 265) | it never reached the 160-page pool, ranking 65th by stored text for the decision; two scoring fixes took it to 55th (entries 265, 266) |
| `selection-recall` measures whether a corridor finds its answer, so it grades a ranking change | it replays the rankings recall logs recorded and printed byte-identical output before and after a scoring change (entry 266) |
| ARCHITECTURE's store table: evidence refused past 168h, corridor resolutions kept 3 weeks | `runtime.yaml` refuses past 2,160h (entry 249) and `corridor_maximum_age_hours` is one week; corrected 2026-10-07 |
| United Kingdom `US/US`'s band would lead with GOV.UK's checker on every run once entry 272 kept it out of where to apply (TODO, 2026-10-07) | in 5 runs the checker was never filed as where to apply; the band led with it on 2, and 3 stated "no visa" from the carriers' list under 8e, with nothing said about the ETA (entry 272) |
| no role asks for an authorisation page, so a role with the vocabulary for one will surface it (the owner's request, entry 275) | scored on the body it did not: 100 GOV.UK pages carry the ETA notice and `gov.uk/eta` stayed out of the selector's 160; scored on the title and address, all 18 ETA pages were shown (entry 275) |
| once discovery finds the authorisation page, the plan names it (entry 275's design) | the role filled Canada `GB/GB` 2 of 2 and the plan named the eTA 0 of 2 — it is required only to fly in, and the profile records neither the way of travel nor the British status (entry 275) |
| a stored plan draft holds the whole `TravellerProfile` (TODO item 55) | `VisaPlanDraft` has no profile field; what a draft can hold of the traveller is its prose, which since entry 276 may name what they shared (entry 276) |
| New Zealand `US/US` names the NZeTA in its plan once the role exists (entry 275's replays, 4 of 4) | the owner's run served a corridor resolved the day before the role existed, and named none; stored corridors were never re-checked against changed discovery rules (entry 277) |
| entries 220–222 fixed Thailand `IN/IN` once the in-force notice was in the pool, and entry 245 found every decision at today's cut (TODO item 85, PROJECT_HANDOFF) | entry 245's purpose rule had already pushed the notice from 77th offered to withheld; Thailand refused 2 runs in 4 until it was named in `always_read.yaml` (entry 281) |
| a faulty United Kingdom `US/US` run, cached, "becomes the answer for everyone who asks next" (TODO item 82) | none of its 31 runs from 2026-10-06 to 10-08 was stored: the Home Office processing-times page went unanswered on every run, and entry 258 stores nothing built while a chosen page failed "for now". The wrong results the store did keep were plan-reading faults on the right pages (entry 282) |
| a plan marked `verified` is the likelier one to be right (TODO item 82's first candidate) | 17 of the 19 plans the owner's rulings make wrong were `verified`; 26 of the 88 good ones were `partial` (entry 282) |
