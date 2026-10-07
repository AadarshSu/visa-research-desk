# Ofself platform feedback

What integrating this app with Ofself (Paradigm, its CLI, Personas) showed about the platform, for
Ofself's developers. This app's own design is in [CRUX.md](CRUX.md), DECISIONS entry 180 and TODO
item 55; this file is only about the platform.

Each point says how it is known — **[observed]** against the live API or CLI, **[read]** in the CLI's
source or the authorize page's JavaScript, **[docs]** from the developer guide alone. Seen with
`paradigm-cli` 0.5.0 against `api.ofself.ai`, 2026-09-16 to 2026-09-24. Mark a point resolved
rather than deleting it.

---

## 1. The live API differs from the guide

- **1.1 `GET /nodes` returns `"total": null`** [observed]. The guide's paging examples compare against
  a count, so they stop after one page or raise. *Suggest:* return the count, or document paging on
  a short page.
- **1.2 `POST /nodes` returns the node unwrapped** [observed]. The guide's test-user example reads
  `resp.json()["node"]["id"]`, which raises `KeyError`.
- **1.3 A DLR can request single fields** [read, observed]. The guide says field masks are set only
  by the user. But `paradigm dlr add-read --fields` exists, the app record stores `fields`, the
  consent card shows it, and a real grant carries it (9.8). Whether a read then returns only those
  fields is still unseen.
- **1.4 `GET /my-permissions` also returns `effective_access`** [observed], the full granted access
  document. The guide doesn't mention it, and it's the field that shows which schemas and fields were
  granted.
- **1.5 A schema outside the grant reads as empty, not refused** [observed, 2026-09-21]. With a grant
  covering only `work-authorization`, `GET /nodes?schema_id=travel-document` and `?schema_id=place`
  answered `200` with `"nodes": []` — the same answer as a granted schema with nothing recorded. So
  an app cannot tell "not shared with you" from "doesn't exist", and one that says "you have no
  passport on file" will be wrong for every user whose grant predates a DLR change. *Suggest:* a
  `403` with a scope code, or a header naming what was filtered.

## 2. The docs disagree with themselves or the CLI

- **2.1 Where the DLR lives: three answers** [read, observed].
  - The guide says §9 of a nine-section `CRUX.md`.
  - `crux init` scaffolds sixteen sections and says there is no DLR section, and `crux sync` calls §9
    "legacy".
  - `crux validate` then fails until a `dlr.requests` block is added, while `crux check` reports the
    same file complete.
  - The bundled skill still says §9.
- **2.2 When a DLR expands** [docs; observed 2026-09-21]. §15 says existing grants are paused. §34.4
  says `app push` requires `--ep-action cancel|continue`, which is what the CLI does. **Observed:**
  `paradigm commit` of a widened DLR answered `EP_ACTION_REQUIRED` — *"cancel (revoke existing
  authorizations) or continue (keep existing authorizations; new permissions will fail until users
  re-authorize)"*. Nothing is paused; §15 is wrong. Two smaller points: `crux sync`'s own prompt
  doesn't mention that the commit will ask, and "will fail" doesn't say how — a schema outside the
  grant already reads as empty rather than refused (1.5).
- **2.3 Two error envelopes, two code vocabularies** [docs; one observed].
  - §18 shows `{"error": "forbidden", …}` and §29 shows `{"error": {"code", "message"}}`.
  - The codes are `NO_AUTHORIZATION`, `NO_PERMISSIONS` and the `EP_*` family.
  - Live, an unauthorised user got `EP_NOT_FOUND` in the §29 shape.
- **2.4 Three names for the permissions endpoint** [docs]: `/my-permissions`, `/third-party/me`,
  `/third-party/my-permissions`.
- **2.5 "Never sees plaintext" vs delegated decryption** [docs]. §1 says an app's infrastructure never
  sees plaintext. §6 hands the app the user's whole private key (`enc_user_privkey`), which isn't
  scoped to the grant. This app declined encrypted fields partly for that.
- **2.6 "`app push` asks for the app's metadata once"** [read]. It doesn't ask. Without the
  `.paradigm/pending.toml` that `init` writes, it exits with "Not a Paradigm project".

## 3. Integrating an existing codebase

- **3.1 No supported way to register an existing project** [read, observed].
  - `app push` needs `pending.toml`, which only `init` writes.
  - `init` refuses when `README.md` or `.env.example` exist. With `--force` it overwrites them.
  - `init`'s default setup installs `paradigm-sdk` from git into the project's `.venv`.

  This app wrote `pending.toml` by hand. *Suggest:* `app create --name … --redirect-uri …`, or an
  `init` that writes only the binding.
- **3.2 `crux sync` says "Pushed" when it only staged** [observed]. `dlr show` read `requests (0)` and
  the consent card was empty until `paradigm commit`. The guide's DLR sections don't mention the
  staging step.
- **3.3 `app push` registers with an empty DLR** [read, observed]. It doesn't read `CRUX.md`. Worth
  saying when it finishes.
- **3.4 Good: `crux sync` refuses a non-interactive shell** [read], so an AI assistant can't confirm a
  consent screen on a developer's behalf.
- **3.5 Publishing the DLR doesn't publish the CRUX, and nothing says so** [observed, 2026-09-21].
  `crux sync` pushes §9 to the DLR; `crux push` is a separate command that stores the design doc on
  the app registration. This app ran the first and not the second, so four days later the portal
  reported no CRUX and `paradigm readiness` refused to score the app:

  > This app has no CRUX yet. The evaluations read it … Write it first.

  Its remedies are `crux init` and `crux questions`, which scaffold and fill a file that already
  existed, validated and was committed. The accurate advice was `crux push`. *Suggest:* have
  `crux sync` end by saying the doc itself is not published, and have readiness distinguish "no
  CRUX written" from "written but never pushed".
- **3.6 `paradigm doctor` reports the local scaffold, not the registered app** [observed]. In this
  project it said `dlr schemas none declared` and `redirect uris none declared` while the live record
  holds both, and `✓ crux validates` while the live record's `crux` is `null`. So it passes the one
  thing that is actually missing and warns about two that aren't. *Suggest:* read the app record and
  compare the two.
- **3.7 `crux push` prefers a file the CLI cannot produce** [read]. It reads a JSON answers block
  from `CRUX.html`, and without one falls back to *"a best-effort extraction from CRUX.md"*. But
  nothing in `paradigm-cli` 0.5.0 writes a `CRUX.html`: `crux init` renders `CRUX.md.tmpl` and there
  is no HTML template, while `crux pull` only injects into an HTML file that already exists and
  otherwise prints the live CRUX to the terminal. So a CLI-only developer never has the preferred
  file, is told about it in two commands' help, and is never told where it comes from — presumably
  an authoring surface in the portal. *Suggest:* say which surface writes it, and have `crux pull`
  offer to create one.

  The fallback is better than its name: it sends every filled section's full text plus the parsed
  DLR block, so no prose is lost. What differs is the shape — `format: "md"` with sections keyed by
  the CLI's own heading *regexes* rather than the structured answers the HTML block carries.
  *Suggest:* say whether a markdown CRUX scores the same as an HTML one, since `readiness` grades
  what the record holds.

## 4. Sandbox users

- **4.1 Full access only** [observed]. A sandbox grant ignores the DLR: the app read `notes`,
  `source` and `work_authorized_in` although it asks for `citizenships` alone. So narrowed grants and
  hidden fields can't be tested without a real authorisation. *Suggest:* a sandbox user whose grant
  is exactly the DLR.

## 5. Gaps in the docs

- **5.1 Hosting** [docs, CLI]. Registration implies the developer hosts the app, and the CLI has no
  deploy command. Nothing says:
  - whether Ofself hosts apps, and any provider or data-residency preference
  - whether apps are linked out to or embedded in app.ofself.ai
  - domain rules
  - mobile support: deep-link redirect URIs, a mobile `@ofself/shell`
  - what happens when an app goes down or is retired, including paid subscribers and whether
    grants are revoked
- **5.2 `http://localhost` redirect URIs are accepted** [observed]. Say so outright.
- **5.3 The guides can't be read by tools** [observed]. `init` copies `DEVELOPER_GUIDE.md` and
  `API_REFERENCE.md` from package data, but 0.5.0 ships neither. The hosted reference is a
  JavaScript app. Personas' plain-markdown `/api/v1/docs` is the model to copy.
- **5.4 Domains are moving** [observed]. `app.ofself.ai` redirects to `nucleus.ofself.com`, and
  `personas.ofself.ai` to `personas.ofself.com`. The guides still give `.ai`.
- **5.5 No app-side per-user cost cap** [docs]. Only the user can set an exposure profile's
  `rate_limit`. That matters for an app paying about $0.31 in search and model calls per request.

- **5.6 The readiness ladder requires writing to the graph** [observed, 2026-09-21]. With the CRUX
  pushed, `paradigm readiness` scores this app **2/5, "Wired"**, and to reach 3 it asks that *"It has
  written to the graph"*. The "What compounds" section marks it down the same way: *"The app requests
  no write access, so nothing it does is available to anything else."*

  The value score says the same thing in prose. After `crux review` passed all five criteria, the
  app scores **1/5, "Convenience"**, with: *"the claim is still travel administration that writes
  nothing reusable, feeds no other app, and does not reshape daily decisions or self-understanding."*
  So two of the three headline scores are capped by the same decision.

  This app writes nothing on purpose, and the reason is the strongest one it has: what it concludes
  is a rendering of pages read at that moment, never a fact (DECISIONS entry 44). A wrong "needs a
  visa" sitting in a graph every other app reads, with no citation and no age, is exactly the
  failure it exists to prevent. As scored, an app is penalised for that restraint and could raise
  its grade by writing something. *Suggest:* let a CRUX declare that an app writes nothing by
  design, have the review judge that claim, and score a justified read-only app on its own terms.
  The ladder currently measures contribution to the graph, not value to the person.

- **5.7 A billable command that names no price and no payer** [read, observed]. `crux review` runs a
  model on Ofself's backend over the app's CRUX. Its own docstring says *"Slower and billable, so it
  is its own command"*, and nothing — help text, output, guide — says who is billed, how much, or
  whether a developer account has a budget. The verdicts are cached against the `spec_version` they
  were made about, so every `crux push` marks them stale and a re-review presumably bills again.
  *Suggest:* print the price before running it, and say in the help whether the developer or the
  platform pays.

  **The verdict also records nothing about what produced it** [observed]. `readiness --json` keeps
  `available`, `ran_at`, `spec_version` and `stale` — no model, no provider, no rubric version —
  while the 503 path shows the provider is a backend setting (`CRUX_REVIEW_PROVIDER`). So a grade
  can move because Ofself changed a prompt or a model, with nothing on the app's side to show it,
  and a developer can neither reproduce a verdict nor diff two. *Suggest:* record the model and
  rubric version alongside `ran_at`.

## 6. Schema registry

- **6.1 Nothing for travel identity** [observed]. No passport, nationality, residence or
  immigration schema. `work-authorization.citizenships` is the nearest. The owner plans to propose
  one. **Resolved 2026-09-21:** six public v1 travel schemas now exist, and this app reads them.
- **6.2 `citizenships` allows alpha-2 or alpha-3** [observed], so every reader must accept both. One
  format would spare them.
- **6.3 A DLR narrows fields but not rows** [docs, CLI]. `place` can't be limited to a home, or
  `trip` to future trips, so this app asks the user instead.
- **6.4 `fact` invites misplaced trust** [observed]. It stores "a tax rule, a visa floor, a fee" with
  a `source` URL written by another app. That URL isn't verification, and this app never reads `fact`
  as evidence. Worth a note on what `basis` and `source` establish.
- **6.5 `schema search` repeats names** [observed], e.g. `nutrition:food`, apparently once per
  version.
- **6.6 No `preferences` schema** [observed, 2026-10-01]. An Ofself developer suggested using a
  person's preferences; the registry has only `work-preferences` and `user-preferences`, both for
  other domains. `constraint` is the nearest. Worth naming which schema is meant.
- **6.7 Nothing writes `travel-document`** [observed, 2026-10-04]. The schema is ready for an upload
  (`photo_ids`, per-field `field_provenance`), but no app we know of turns an uploaded passport into
  one, so a reader has nothing to read until the person types it in somewhere.

## 7. Security and setup

- **7.1 The install leaves the developer token behind** [observed]. `--extra-index-url
  https://paradigm:<TOKEN>@…` puts the `ofs_dev_` token in shell history and pipx's
  `pipx_metadata.json`. *Suggest:* keyring, netrc or a prompt.
- **7.2 §26 suggests appending user-written prompts to an app's model prompt** [docs]. That's outside
  text steering the model. It needs a caution.
- **7.3 `paradigm` isn't on `PATH` after install on macOS** [observed]. Mention `pipx ensurepath`.

## 8. Personas, for this app's model calls

First evaluated 2026-09-17 against a shorter guide, after Personas headless mode was suggested for
model credits. Re-read 2026-09-18 against the full snapshot in
[docs/ofself/PERSONAS_GUIDE.md](docs/ofself/PERSONAS_GUIDE.md) and the live `/api/v1/docs`. Every
point from the first reading still holds.

**Re-read 2026-09-23 against the live guide, which had grown from 589 lines to 1,321.** It now
documents a model-only call — `capabilities: []` with a per-run `llm_config` — so 8.1, 8.2, 8.3,
8.7 and 8.11 are resolved or mostly resolved, as marked. What that still leaves for this app is in
TODO item 62, and 8.14–8.16 are what the new sections raised.

- **8.1 No structured output** [docs]. The guide says *"There is no `response_format` / JSON-schema
  enforcement."* The nearest thing is asking the agent in the prompt to call `save_artifact`, which
  is a request, not a constraint. This app needs a JSON schema on every model call. **Resolved
  2026-09-23** [docs]: `llm_config.response_format` goes to OpenAI natively and is refused, with a
  reason, on Anthropic. Its only example is `{"type": "json_object"}`; see 8.14.
- **8.2 A wrapper prompt that can't be switched off** [docs]. Personas builds the system prompt
  *"fresh, in layers"*: its sandbox rules, then the app's prompt, a state-machine block, a context
  block fetched from Paradigm, and its formatting rules. Stored silent context is also injected
  before each message. No field removes any of it. Small prompt changes here have altered visa
  decisions. **Mostly resolved 2026-09-23** [docs]: `capabilities: []` sends *"your system prompt
  and the conversation, no platform text, no tools"*, priced at 0 characters in §4.2. One exception
  is unclear; see 8.15.
- **8.3 Model settings stop at provider, model and temperature** [docs]. There's no reasoning effort
  and no cache control. Passing `llm_model` once changes the agent permanently. **Mostly resolved
  2026-09-23** [docs]: `llm_config` sets `reasoning.effort`, `max_tokens` and an optional key per run,
  and does not persist onto the agent. Cache control is still absent, and unknown `llm_config`
  fields are refused, so none can be passed through.
- **8.4 Every run needs a Paradigm user** [docs]. `paradigm_user_id` is required on every endpoint.
- **8.5 `hmac_key` travels in the body as well as signing it** [docs].
- **8.6 Cost and limits are undocumented** [docs]. Nothing says who pays, what `llm_provider:
  "ofself"` costs, or what the limits are.
- **8.7 No model-only mode** [docs]. Every documented entry point runs the full agent loop:
  - `/run` and `/run/stream`
  - `/internal/headless/invoke`
  - `/plugin/invoke`
  - `/automation/run`

  None of them sends one prompt, returns one schema-checked reply and stops. So an app can't use
  Ofself's model access for calls that aren't about a user's data. *Suggest:* a completion endpoint
  authenticated by the app alone. It would take a JSON schema, pass through provider settings such
  as reasoning effort and caching, add nothing to the prompt, store nothing, and return full usage
  and cost. **Resolved 2026-09-23** [docs]: §0 now opens *"at zero capabilities this IS that
  call"*. Two parts of the suggestion remain open: every run still needs a user (8.4) and is still
  stored (8.8).
- **8.8 Every run is stored as a conversation** [docs]. *"All messages saved to DB (full loop history
  + thinking)."* The only way to remove them is to delete them afterwards. That matters for an app
  sending third-party text that the user never sees.
- **8.9 Usage has three shapes, and none of them gives cache or reasoning tokens** [docs].
  - The first guide's `done.usage` is `{input_tokens, output_tokens, total_tokens}`, and its
    `message_complete` carries per-message tokens and the model.
  - The second guide's `done.usage` has no `total_tokens`, and its `message_complete` has no tokens.
  - The blocking `/run` returns `{total_input_tokens, total_output_tokens, model}`.

  None separates cached, cache-written or reasoning tokens, and none gives a cost. So an app can't
  price a call the way this one does (DECISIONS entries 164 and 167).
- **8.10 Which models `llm_model` accepts isn't listed** [docs]. The default is `gpt-5.5`, the
  examples use `gpt-5.2`, and `llm_provider` is `'anthropic' | 'ofself'`. Nothing says what
  `ofself` routes to.
- **8.11 When `temperature` applies is stated two ways** [docs]. The first guide says it *"truly
  applies only at creation"*. The second lists it in the run body as `0.0–2.0` with no caveat, and
  `PATCH` can change it. **Resolved 2026-09-23** [docs]: the live guide says it *"had never reached
  a model"* on any path, and `llm_config` now refuses it.
- **8.12 The second guide's Python signing example fails verification** [docs; checked locally].
  `sign_body` signs `json.dumps(body, separators=(',', ':'))`, then sends `requests.post(json=payload)`.
  `requests` re-serialises with spaces, so the bytes sent aren't the bytes signed. The first guide
  warns about exactly this (*"serialize once, sign those bytes, send those bytes"*). Its JS example
  is consistent.
- **8.13 Two current guides that disagree, and one endpoint serves only the first** [observed]. Both
  are current Ofself developer docs, the owner confirmed on 2026-09-19:
  - the *Personas App Integration Guide*, which `/api/v1/docs` serves
  - the *Personas Headless Agent API*, which it doesn't

  The docs don't yet say how the two relate, and Ofself plans to make that clear. Until then they
  disagree on usage shapes (8.9), `temperature` (8.11) and signing (8.12). The served guide also
  changed on 2026-09-18: agents are now owned by the app that created them. *Suggest:* serve both
  from `/api/v1/docs` and reconcile the three points.
- **8.14 Whether `response_format` accepts a strict JSON schema is unstated** [docs, 2026-09-23].
  The table says OpenAI's is *"native"* and §5.2 speaks of *"your own schema"*, but the only example
  is `{"type": "json_object"}`, which guarantees JSON and not its shape. This app's calls use strict
  `json_schema`. *Suggest:* show a `json_schema` example with `strict: true`, or say it isn't
  supported. **Answered by a probe, 2026-09-24** [observed]: a strict `json_schema` is applied, even
  on Ofself's account. Still worth an example in the guide.
- **8.15 A date line may be added even at zero capabilities** [docs, 2026-09-23]. §4.2 prices the
  platform text at 0 characters for `[]`. But the run body's `timezone` sets *"what 'today' means in
  the prompt's date line"*, and the `scope` event's `timezone` is *"always a string"*. So either
  every prompt carries a date line, or `[]` is an exception nobody wrote down. For an app whose
  output shifts with small prompt changes, that is the difference. *Suggest:* say which. **Answered
  by a probe, 2026-09-24** [observed]: at `[]` the `debug` request event held exactly the app's
  system prompt and message, with no date line.
- **8.16 Model-only calls still need a user and are still stored** [docs, 2026-09-23]. The floor is
  documented as *"that call"*, but `paradigm_user_id` is required and every run becomes a stored
  conversation per user and agent. An app calling a model for work that serves no one — ranking,
  classifying, an offline build — has to borrow some person's identity, and its prompts accumulate
  in that person's history. *Suggest:* let an app-authenticated call with `capabilities: []` omit
  the user and skip storage.
- **8.17 Without the app's own key, `llm_config.model` is ignored** [observed, 2026-09-24]. Asked
  for `gpt-5.6-terra` with `provider` set to `openai` or `ofself`, every run used the agent's stored
  `gpt-5.5`. `response_format` and `reasoning` in the same `llm_config` *were* applied, and the
  agent-level `llm_model` did select `gpt-5.6-terra`. The guide's *"Omit `api_key` and nothing
  changes"* reads as billing; in practice it also means the model. *Suggest:* apply the model, or
  refuse it with a reason, as `base_url` is refused.
- **8.18 The CLI registers against a host that redirects, and fails misleadingly** [observed,
  2026-09-24]. `paradigm personas register` defaults to `personas.ofself.ai`, which answers `301` to
  `.com`. Python's `urllib` follows a `301` on a POST by sending a GET without the body, and a GET on
  `…/apps/register` answers `404 "App not found"`, reading `register` as an app id. *Suggest:* change
  the default host, and answer a POST route's GET with `405`.
- **8.19 The "exact context" read-back isn't what the model received** [observed, 2026-09-24].
  `POST …/conversations/<id>/context` returned the full platform prompt — tool doctrine, the
  `paradigm.*` reference — for a `capabilities: []` run billed 23 input tokens, whose `debug` request
  event held only two short messages. The endpoint seems to rebuild the agent's default rather than
  the run's own. *Suggest:* return what the run actually sent.
- **8.20 `debug` reports a temperature the guide says is never sent** [observed, 2026-09-24]. The
  request event shows `"temperature": 0.4`; §3.2 says it *"is in the kwargs of neither"* provider.
  One of the two is wrong.

- **8.21 Which models the account serves is undocumented, and a missing one surfaces as an Azure
  error** [observed, 2026-09-25]. Asked for `gpt-6-sol` and `gpt-6-luna` (released by OpenAI on
  2026-09-22) on a probe agent, each run failed with `AGENT_ERROR … 404 DeploymentNotFound: The API
  deployment for this resource does not exist`. `gpt-5.6-terra` answered on the same agent shape.
  The guide names defaults (`gpt-5.5`, examples with `gpt-5.2`) and no list. *Suggest:* publish
  the models `llm_provider: "ofself"` serves (or a `GET /models`), and answer an unknown model with a
  plain "model not available on this account" rather than the provider's deployment error.
- **8.22 Four concurrent large runs, then every run answered `504` for about ten minutes**
  [observed, 2026-09-24]. One selection run (~73k input tokens) answered in 5.9s. The next four,
  sent together at ~80–105k input tokens each, all answered `504` after 55s, and so did the 41
  behind them and a one-candidate run of a few hundred tokens. About ten minutes later a trivial
  run answered in 18.5s, and 737 more runs sent one at a time all succeeded, in about 6s each. Not
  known: whether the burst caused it, whether the timed-out runs kept running (and were billed)
  behind the gateway, and what the concurrency limit is. *Suggest:* document a per-app or per-user
  concurrency limit and answer past it with `429` and a `Retry-After`, not a gateway timeout; and
  say whether a run the gateway gave up on still completes.
- **8.23 Twelve calls in a row answered `500` within a second, one at a time** [observed,
  2026-09-24]. Between 14:02:46 and 14:04:46 UTC every Personas call this app made — selection, roles
  and plan calls, sent strictly one after another — answered HTTP 500 in 0.4–1.0s; the 70 calls
  before and after succeeded. Unlike 8.22 there was no burst to blame. *Suggest:* a status page or an
  error body that says whether a `500` is the platform or the provider, so a client can tell "retry
  in a minute" from "your request is wrong". **Again on 2026-09-25:** among 20 plan calls sent one
  at a time, four answered `502` or `504` (0.5–30s) between successes.
- **8.24 A content filter refused a packet of government visa pages, then passed the same packet**
  [observed, 2026-09-29]. One selection call (~56k input tokens: Indonesian immigration and
  embassy pages about visas on arrival) answered `AGENT_ERROR: Agent failed: Error code: 400` —
  "This content was flagged for possible cybersecurity risk. If this seems wrong, try rephrasing
  your request. To get authorized for security work, …". Four earlier runs of the byte-identical
  packet had succeeded, and a retry minutes later succeeded too; 59 of 60 calls in the batch passed.
  *Suggest:* say which layer flags (Personas or the provider), whether a flag is deterministic,
  and how an app doing non-security work is meant to avoid or appeal one — a production request
  that hits it fails a traveller's plan with no way to rephrase.
- **8.25 A traveller's plan took over two minutes because two calls in one request failed slowly**
  [observed, 2026-10-05]. Australia for an Indian passport living in Singapore: the selection call
  answered `504` after **55.3 s**, and the plan call failed after **36.9 s**; the same three calls
  on a re-run minutes later took 5.5 s, 7.0 s and 8.6 s. It is 8.22 and 8.23 again, now on a
  traveller's request rather than a batch. Over this app's logged calls, 488 selections succeeded
  in at most 17 s, yet a failed one can hold the request for the gateway's full 55 s. Our side now
  gives up on selection at 30 s and role calls at 40 s, and refuses rather than continuing on a
  worse path (entry 258). *Suggest*, again: a status page, an error that says whether to retry,
  `429` with `Retry-After` for load rather than a gateway timeout, and whether a run the gateway
  abandoned still completes and is billed. A reliability figure for `/run` would let an app set
  its own limits from a number rather than from its logs.

## 9. Signing a user in

The guide shows only SDK helpers (`build_oauth_url`, `verify_callback`), the SDK repository is
private, and the reference isn't shipped (5.3). So this flow was read from the authorize page's
JavaScript and confirmed with one real sign-in.

- **9.1 The callback trusts nothing** [read]. It redirects with `code=success` (a literal),
  `client_id`, `user_id`, `username` and `sid_code`. An app that trusts `user_id` signs a visitor in
  as anyone a link names. `verify_callback(code, user_id, username)` suggests exactly that.
- **9.2 `sid_code` is the only proof, and it's undocumented** [read, observed].
  - An app redeems it at `POST /api/v1/auth/session/exchange` with `X-API-Key` and `{"code": …}`,
    found by probing: `404 INVALID_CODE`, `400 VALIDATION_ERROR`, `401 MISSING_API_KEY`.
  - A real sign-in on 2026-09-17 succeeded and named the user as a UUID, but which response field
    held it wasn't recorded.
  - The page silently omits `sid_code` if `POST /auth/session/start` fails.

  *Suggest:* document the exchange, and make `sid_code` required.
- **9.3 No `state`** [read]. A callback can't be tied to the sign-in that began it, so login CSRF is
  open. This app's short-lived cookie narrows it. *Suggest:* echo `state`, per OAuth 2.0.
- **9.4 Three authorize addresses** [docs, read]:
  - `paradigm.ofself.ai` in `build_oauth_url`
  - `app.ofself.ai/authorize/<app_id>` in the skill
  - `app.ofself.ai/authorize?client_id=…` in webhooks and Personas

  The page reads `client_id` and `redirect_uri`.
- **9.5 `redirect_uri` is sent to the server at approval** [read], so it can be checked against the
  registered URIs. Not tested. If it isn't checked, `sid_code` could be sent anywhere.
- **9.6 The consent page contradicts the server** [observed]. With no `work-authorization` record, the
  page said "Full Access… Reaches none of what Visa Research Desk requires". `GET /authorize/preview`
  said Full Access `fits: true`, `missing: []`. It appears to count data held, not permission.
  *Suggest:* "You have none of this yet" when the realm fits.
- **9.7 `sid_code` lands in server logs** [observed]. Uvicorn's default access log recorded it, and a
  refused callback leaves it unredeemed. This app now redacts it. *Suggest:* seconds-long expiry, or
  deliver it by POST or fragment.
- **9.8 A real grant carries the DLR's `fields`** [observed]. `effective_access` was exactly
  `nodes:read` on `work-authorization`, `fields: ["citizenships"]`, and no other records were
  readable. The grant expires in 30 days, and the guide doesn't say if that's the default.
  **Re-consenting doesn't restart it** [observed, 2026-09-21]: after the DLR widened and the owner
  approved the new request, `effective_access` held all six schemas with exactly the requested
  fields, and `expires_at` was still 2026-10-17 — the first grant's date. So a user who re-approves a
  larger request on day 29 gets one more day of it.

- **9.9 A developer token can no longer write the developer's own identity data, and the guide
  still says it can** [observed 2026-10-07, docs]. `POST /api/v1/nodes` with a PAT, creating a
  `travel-document` in the owner's own account to test entry 276, answered `403
  DEV_PAT_NOT_ALLOWED`: "Developer tokens do developer things… They cannot read or write identity
  data". The developer guide's §2.1 shows exactly that call ("Create a node as yourself") and lists
  "exploring what data your own Paradigm account holds" as a use. **The ask:** update the guide, and
  say how a developer seeds test identity data for their own account — the app key here is
  read-only by design, and there is no documented way left.

## 10. Making it easier to connect an existing app

In order of what would have saved the most time here:

1. **Machine-readable docs that match the CLI.** Paradigm's guide and API reference as plain markdown,
   like Personas' `/api/v1/docs`, plus an OpenAPI spec. One source of truth for where the DLR lives
   (2.1, 5.3).
2. **Sign-in as HTTP, not SDK helpers.** Document the authorize URL, the callback's parameters, the
   `sid_code` exchange (request, response, expiry) and `state`, and warn never to trust `user_id` from
   the address. Make the SDK public, or say what `verify_callback` checks (9.1–9.3).
3. **A path for existing projects.** A register-only command that scaffolds nothing, touches no
   `.venv` and overwrites nothing (3.1). And a ~100-line plain-HTTP example covering login,
   callback, session, one schema read, each authorisation error, and logout.
4. **Sandbox users that behave like real ones.** A sandbox grant equal to the DLR, and a way to
   put it into paused, revoked or expired (4.1).
5. **CLI state and next steps.** Say whether a change is staged or live (3.2). End `app push` with
   what comes next (3.3). Add a readiness check for a connected app: key valid, DLR live, redirect
   registered, test exchange passes.
6. **For AI assistants.** `--json` and non-interactive flags on every step except consent. Help text
   saying which commands need a person and why, as `crux sync` does (3.4). A bundled skill that
   doesn't assume a new project.
7. **Safe defaults, written down.** Install without the token in a URL (7.1). Redact or quickly
   expire `sid_code` (9.7). Echo `state` (9.3). Show `--fields` in the guide's DLR examples (1.3).

## 11. What worked well

- **`crux validate`** resolves schemas against the registry and pushes reuse over invention.
- **`paradigm dlr preview`** shows the exact consent card before publishing.
- **Error messages are actionable,** e.g. `EP_NOT_FOUND`: "The user must complete the OAuth
  authorization flow first."
- **Sandbox users:** one command, a working `X-User-ID`, no OAuth.
- **Personas' `/api/v1/docs`** is versioned with the deployment and readable as markdown.

---

## Open questions

| Question | Raised | Put to Ofself | Answer |
| --- | --- | --- | --- |
| Does Ofself host apps; linked out or embedded? (5.1) | 09-17 | — | — |
| Does any app record a `trip` before it happens? | 09-17 | — | — |
| Direct model access behind the `ofself` provider, and who pays? (8) | 09-17 | — | **Documented 2026-09-23:** `capabilities: []` with `llm_config` (8.7). Billed to Ofself's account unless the app sends its own key. Price and limits still unstated (8.6) |
| Does a read return only a DLR's `fields`? (1.3, 9.8) | 09-17 | — | In the grant yes (9.8); on data, untested |
| What does `/auth/session/exchange` return, and can `sid_code` be relied on? (9.2) | 09-17 | — | — |
| Will the authorize redirect echo `state`? (9.3) | 09-17 | — | — |
| Is `redirect_uri` checked at approval? (9.5) | 09-17 | — | — |
| Is a 30-day grant expiry the default? (9.8) | 09-18 | — | — |
| Does an upload to Ofself produce a `travel-document`, and is there a screen to add one by hand? (6.7) | 10-04 | — | — |
| May an app propose a change to a `travel-plan` another app wrote? | 10-02 | — | — |
| Can an app reach a person who isn't on its page, e.g. a notification? | 10-02 | — | — |
| Does a `belongs_to` edge to another person's node make it readable to them alone? (for travelling with friends, later) | 10-02 | — | — |
| Is there a status page or reliability figure for Personas `/run`, and does a run the gateway abandoned at 55 s still complete and bill? (8.22, 8.25) | 10-05 | — | — |
| Assimilation's news plugin: its manifest; can it take a country and date window instead of the person's interests; must it save articles into the user's account; can an app trigger it (guide §11 says only the user, §28 and §31 say an app can); how fast; which sources | 10-05 | — | — |
