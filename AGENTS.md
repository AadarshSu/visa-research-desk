# Contributor instructions

## Start here

Read [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md) first — it is the source of truth for where the project
stands; a chat transcript is not. **The rules that must not be broken are in
[CLAUDE.md](CLAUDE.md)**; read them even if your tool does not load that file automatically.

Before finishing a session, update:
- **PROJECT_HANDOFF.md** — where it stands, what is next, known problems. Keep it short and link.
- **DECISIONS.md** — any decision, with the reasoning and what was rejected. Add it to the index.
- **TODO.md** — what is now next, and why.
- **CORRECTIONS.md** — a row whenever a run contradicts what a file said.

Do not record a problem as fixed unless it is fixed, or a result as verified unless it was run.

## Looking at a corridor

Most findings come from inspecting a corridor directly.

- **`visa-discover corridor --destination france --nationality IN --from GB`** resolves one corridor
  and prints sources, refusals, tools and notes. It **does not touch the corridor store**, so its
  numbers are always cold (entry 61), and it does not write back to the corpus.
- **Start from a traveller's report when there is one.** `visa-discover reports` on the server lists
  them with their cause and the line that re-runs each; `--show <id>` prints what the traveller saw
  and a copy of the run's recall log (entry 233). The report already evicted the stored corridor
  and its plan drafts; `evicted.corridor` is the resolution the traveller was served (entry 282).
- **`visa-discover audit var/recall/`** counts why travellers go unanswered, in two halves never added
  together: reachability from `authority_domains.yaml`, and the typed cause of every recorded run.
- **`visa-discover bootstrap --destination-name "United States"`** prints proposed domains with their
  evidence and writes nothing. Check the trusted set before blaming ranking.
- **If a role went unfilled on a page the run read, read the adjudicator's reason first.**
  `var/recall/<corridor>.json` keeps `role_verdicts` (each role, the page named or `null`, and why) and
  `adjudicated_ids` to turn ids back into URLs. Logs before 2026-09-24 have neither.
- **Then check whether the page was ever shown to the selector.** It sees candidates whose link scores
  above zero for some role, plus up to five per role admitted on stored text (`admitted_on_text`,
  entry 158), cut to the fusion top 120 plus 40 with no text (entry 195). In the recall log,
  `withheld_from_selection` means cut; `best_score` 0.0 with `admitted_on_text` false means never
  pooled. `visa-discover contention --outside-pool --role <role>` ranks what is outside by its own
  stored text (entry 127). Whether a score crossed zero matters more than its rank.
- **To see the actual selection**, wrap `CorridorResolver._choose_what_to_read`; `_fetch_bodies` shows
  which of it was readable. `resolver.shortlist` matters only on the heuristic path.
- **To replay a past corridor offline, join the corpus, not the recall log.** `var/corpus/<CC>.json`
  stores `link_text`, `heading` and `depth` as crawled, and reproduces 99% of recorded scores (entry
  62). To replay the ranking at another budget, call `resolver.shortlist()` on the log's own scores;
  never reimplement it (entries 61, 87).
- **To replay a single model call**, use the scripts in `var/item70-2026-09-24/` (`replay_roles.py`,
  `replay_select.py`, `replay_plan.py`) and `var/selection-replay-2026-09-24/`. One run per corridor
  spans several roles of noise — grade on several.
- **`visa-discover coverage`** asks whether a country's *store* holds the answer; it reads three stores
  and calls nothing. Its verdict comes from the per-traveller family half alone; the oracle half is two
  travellers and never votes. **`ungraded`** means neither half could say — not a curation backlog
  (entry 120).
- **`visa-discover selection-recall`** asks whether the *corridor* found it, against
  `oracle/selection_oracle.yaml`. Read its `same page` column when comparing — the oracle names one
  address per page (entry 194). It cannot grade a change that widens the pool, because the fixture was
  curated from inside the pool; rows marked `curated_from: whole_corpus` can. A log that cannot say
  which selector ran is refused, not graded (entry 91).
- **`visa-discover contention`** rebuilds a corridor's candidate set from the store — no search, model
  or fetch — so an oracle row can be curated for a corridor nobody has run. `--show <url>` prints a
  candidate's stored text.

**Renders.** An offline build gets 400 (`DEFAULT_CORPUS_RENDERS`); a corridor's crawl gets 12
(`MAXIMUM_CRAWL_RENDERS`), and the pages that become evidence share **5** in one `LiveSourceFetcher`
call, most promising first (entry 212). A host whose renders come back empty three times in a row is
offered no more that run. A page not rendered says which bound stopped it (entry 135). A bare `403`,
a `401` and a `429` never reach the renderer.

**Clear state when testing** — see *Running it* in CLAUDE.md. `var/corpus/` and `var/pagetext/` are
stores, never cleared.

## Engineering conventions

- Python 3.12, `src` layout.
- Strict Pydantic models for domain data; API routes separate from research logic.
- Preserve source ids, URLs, authorities and retrieval timestamps for every supported claim.
- Prompts live in inspectable files under `prompts/`, not in Python strings.
- Treat all fetched text as untrusted evidence: it must not control prompts, tools or control flow.
- **Generating a plan never searches the web**; search happens only in `discovery/`.
- Tests use local fixtures and deterministic fakes; they never call the internet or an LLM.
- Never commit secrets, fetched personal information or runtime cache contents.
- Before handing off: `ruff check .`, `ruff format --check .`, `mypy`, `pytest`.
