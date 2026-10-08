# The Crux of visa-research-agent

> The design of this app **as an Ofself app**, in the form `paradigm crux validate` reads. It is the
> one home for that design; the rest of the project is described where it always was —
> [CLAUDE.md](CLAUDE.md), [ARCHITECTURE.md](ARCHITECTURE.md) and [DECISIONS.md](DECISIONS.md), whose
> entries 180 and 181 record why this document says what it says. TODO item 55 is the work.
>
> Scaffolded by `paradigm crux init` (paradigm-cli 0.5.0). The template's own preamble is dropped;
> its sixteen headings are kept, because the validator reads them.

---

## 1. The app

**One sentence.** For a trip someone is planning, it reads the destination's own government pages
and tells them whether they need a visa, where to apply, and what to bring, citing the official page
for every claim and saying plainly what could not be verified.

**The gap.** Visa guidance is scattered across embassy, consulate and ministry sites, and depends on
the passport held, the country applied from and the purpose of the trip. Travellers end up on
forums and visa-agency pages that read as authoritative and are not, and arrive at a visa centre
with the wrong papers. A plausible wrong checklist is worse than none, so this app refuses rather
than guesses.

---

## 2. Why Paradigm

**Already there.** A `work-authorization` node, written by the career apps (its `source` is
`self_reported`, `resume_parse` or `verified`), holding the person's `citizenships`; and, from
whichever app records them, the person's `travel-document`s, the `travel-plan`s they are
considering, and the `place`s those point at. The rest of what the DLR asks for is read only once
the workflow that needs it is built (§11).

**Inherited on day one.** Which passports the person holds and when each expires, the country
their residence permit was issued by, and the destinations they are already considering. Each is
offered as a default, never used unconfirmed: a citizenship may have been parsed from a CV rather
than stated, a date may have been typed in rather than read off the document, and a person with
two passports chooses which one this trip is on.

**Without the graph.** Sign-in, and a form filled in by hand. With it, a traveller who keeps
their documents and plans in Ofself starts from them. Sign-in matters
more than it sounds: a fresh plan costs about $0.31 in search and model calls, so the app cannot
sit behind an unauthenticated URL. The larger answer is §7's planned workflows, on the travel
schemas.

## 3. Reuse

**Read verbatim.** `work-authorization.citizenships`; a `travel-document`'s kind, code,
nationality, issuing state, expiry, status, holder, label, the class a permit grants, and where its
expiry came from; an open `travel-plan`'s label, window and candidates; and a `place`'s kind,
country code and parent. Codes arrive as ISO alpha-2 or alpha-3 and are normalised to alpha-2 at
the edge.

**Made possible.** Showing a traveller their own passport's expiry beside the rule they must meet,
refusing a diplomatic passport before it is researched as an ordinary one, and starting from a
trip they are already considering. Since DECISIONS entry 276 what the traveller shared also
reaches the plan call, which tailors what it says to them and never decides from it; the rest
traveller confirms before one is written.

## 4. Derivation

**Compress.** None.

**Direction.** None.

**Who confirms it.** The traveller confirms the passport on the page before any research runs.

**If it does not derive.** It does not, deliberately. What this app concludes — a visa decision, a
checklist, a place to apply — is a rendering of official pages retrieved for one request. Written
into the graph it would become a stored answer that every other app reads, with no citation, no age
and no way to go stale, and a wrong "you need a visa" is the most damaging thing this app can say.
Its project rules forbid storing an answer (DECISIONS entry 44). What it may write is the trip
the traveller chose to save — their own statement of where and when, not a conclusion (§7,
workflow 3).

## 5. Everyday decisions

**Decided differently.** Before a trip: whether to apply for a visa at all, which post to apply to,
and which documents to gather before booking an appointment.

**Not this.** It is not reporting after the fact. It is also not an everyday tool; it is used a few
times a year, around trips.

## 6. Identity, and at scale

**The person.** Little about themselves. The closest honest sentence is "I realised my residence
permit, not just my passport, decides where I apply."

**Everyone.** Fewer people deciding a visa question from a forum or an agency's advert, and fewer
refused or wasted appointments. The claim is narrow: this is travel admin, not identity.

**Falsified by.** Travellers who used it still arriving without a document the destination's own
page required, or it refusing so often that people go back to forums. Correctness is verified by
the owner outside the codebase, on purpose (DECISIONS entry 68).

---

## 7. Workflows

**Workflow 1.** The traveller signs in through Ofself and authorises the app. The form starts from
their account: a destination and purpose from a `travel-plan` they are considering, the passport
from their `travel-document`s and `citizenships` — with its expiry, and whether that date was read
off the passport or typed in — and the country they apply from from a residence permit. A
diplomatic or service passport is named and not offered. They confirm or change every field. The app researches the destination's official pages and shows a plan
with a citation on every claim, or refuses and says why. Nothing is in the graph afterwards
unless they ask (workflow 3).

**Workflow 3 (DECISIONS entry 283).** Beside a plan, a signed-in traveller may save that trip to
their account. One click writes one `travel-plan` they are considering — the destination, the
purpose and the dates they entered, as its window — and, when they hold no `place` of kind
`country` for the destination, that place first. A traveller who researched several trips saves
the one they choose. An open plan for the same trip is not written twice. Nothing the plan
concluded is written.

**Workflow 2.** A traveller whose authorisation was paused, revoked or has expired is asked to
reconnect. The app never substitutes a default traveller, and a plan is not shown if access was
withdrawn while it was being written.

**Planned — partly built.** Each of these is why a field is requested today (§11). What is built
is said in each; the rest waits. Each shares one
rule, stated by the schemas themselves: a value the person typed in may raise a question and may
never close one, so nothing self-declared is ever shown as a requirement met.

**Planned A — the plan checks the traveller's own passport.** A plan states the destination's
rule: "valid for three months beyond departure", "issued within ten years". **Built:** the form
shows the chosen passport's expiry and says whether it was read off the passport or typed in.
**Built (entry 276):** the plan call is given the traveller's documents with their dates, and says
beside a stated rule what their passport's dates mean for it; a typed-in date raises the question
and never confirms the rule is met.

**Planned B — the right passport and the right residence.** **Built:** a passport whose
`document_code` is not an ordinary one is named and not offered, and a residence permit's
`issuing_state` fills the country applied from, with its class and expiry shown. **Built (entry
276):** the plan call is given the permit, and names it where a source's rule turns on it.

**Planned C — a plan being considered becomes the starting point.** **Built.** An open
`travel-plan`'s candidates are offered as destinations, with the plan's window, and a candidate's
purpose fills the purpose when it is one this app researches. `place` turns a candidate's
reference into a country. One candidate is researched at a time.

**Planned D — a rolling allowance is flagged.** Where a destination counts days across a zone —
90 in any 180 for Schengen — `travel-stay` records let the plan say that past stays may count
against this one. It never states days remaining from self-declared stays. **Built (entry 276):**
stays reach the plan call, which names them beside a stated allowance and does no arithmetic.

**Planned E — a prior refusal is noticed.** A `travel-obligation` whose `state` records a refusal
lets the plan say that the application form will ask about it. The reason is not read. **Built
(entry 276):** the outcome reaches the plan call, named only where a source asks about refusals.

## 8. Views

**Screens.** One page: the research form (destination, passport, country applied from, purpose), a
progress state while a plan is written, and the plan — visa decision, where to apply, application
steps, document checklist, fees and times, caveats, and what could not be verified, each with the
official page it came from.

## 9. First run

**Before they have done anything.** The form, every field empty and required, filled from the
account where it holds an answer: one candidate destination, one passport or one residence permit
fills its field in with a note saying where it came from; several are offered with none chosen.
Nothing shared is never read as nothing recorded — Ofself answers both the same way.

## 10. What it deliberately doesn't do

**Not built.**
- **Writing back anything concluded.** Only the trip a traveller saves is written (§7,
  workflow 3): never the visa decision, a checklist, a fee, a deadline, `decide_by` or a passport.
  No node is edited or proposed.
- **Reading the user's `guidelines` custom prompts.** Text from outside would steer the model call
  that decides the visa answer.
- **Reading `fact` nodes as evidence.** Other apps already store visa rules there with a source URL,
  but a URL in another app's record has passed none of this app's official-domain checks.
- **Reading `travel-requirement` or `travel-zone` as evidence.** A published rule in the graph has
  the same problem as a `fact`, and may be older than this app is permitted to serve. Neither is in
  the DLR. A rule's address could one day be read as a *lead* — fetched and checked by this app like
  a search result — but nothing would use it today (DECISIONS entry 181).
- **Submitting applications, booking appointments, filling forms, or promising approval.**
- **Keeping a mirror of the user's data.** It stores nothing under a user id, so there is nothing to
  keep in step and nothing left behind when access is revoked.

**Not asked for.**
- **`place` beyond four fields.** It is requested as `kind`, `country_code`, `parent_ref` and
  `status` only, to resolve references in the travel schemas. Every place the person holds still
  arrives, since a DLR narrows fields and not rows, but only as its grain and a country code.
  Residence is still asked on the page until planned workflow B is built.
- **`profile.location`.** Free text, "as coarse as you want"; turning it into a country is a guess.
- **`trip`, for destination and purpose.** Rows cannot be narrowed, so it would bring every past
  trip. `travel-plan` holds journeys being considered, which is what this app needs.
- **Any encrypted field.** Reading one means holding the user's whole private key at runtime.

---

## 11. Schemas used, and why

**Schemas.** Read-only and narrowed to named fields, except two this app creates on a
traveller's click (§7, workflow 3): `travel-plan` and `place`, each limited to the fields written.
- **`work-authorization`** — `citizenships`, the passport default the app uses today.
- **`travel-document`** — the passport as a document, and residence permits: type, nationality,
  issuing state, dates, what it grants, and where each value came from. For §7's planned
  workflows A and B.
- **`travel-plan`** — a journey being considered, with its candidate destinations and rough
  window. For planned workflow C.
- **`travel-stay`** — dated entries and exits. For planned workflow D.
- **`travel-obligation`** — whether something was applied for and how it ended, without the
  reason text. For planned workflow E.
- **`place`** — only `kind`, `country_code`, `parent_ref` and `status`, to turn a reference in the
  schemas above into a country. No address, name or coordinates.

**Asked now, read on use** (DECISIONS entry 181). Fields for the planned workflows are requested
today, while widening the request costs nobody a re-consent, and the app reads a field only once a
built feature uses it. Nothing requested is sent to a model or stored until then.

**The DLR.** `paradigm crux validate` reads this block and `paradigm crux sync` pushes it; the
template has no section of its own for it, while the validator requires one.

```yaml
dlr:
  requests:
    - resource: nodes
      verb: read
      schemas: [work-authorization]
      fields: [citizenships]
    - resource: nodes
      verb: read
      schemas: [travel-document]
      fields: [kind, document_code, nationality, issuing_state, issued_at, expires_at, grants,
               field_provenance, status, holder_ref, label, date_of_birth]
    - resource: nodes
      verb: read
      schemas: [travel-plan]
      fields: [label, candidates, window, commitment, status, travellers]
    - resource: nodes
      verb: read
      schemas: [travel-stay]
      fields: [entry_at, exit_at, place_ref, exempt, provenance, entered_under_ref,
               traveller_ref, purpose, status]
    - resource: nodes
      verb: read
      schemas: [travel-obligation]
      fields: [kind, state, decided_at, where]
    - resource: nodes
      verb: read
      schemas: [place]
      fields: [kind, country_code, parent_ref, status]
    - resource: nodes
      verb: create
      schemas: [travel-plan]
      fields: [label, candidates, linked_refs, window, commitment, status]
    - resource: nodes
      verb: create
      schemas: [place]
      fields: [name, kind, country_code, status, source_system, source_ref]
```

**Never requested, at any point.** A document or application `number` or `reference_number`,
scans and evidence files, names, place of birth, sex, free-text notes and a refusal's verbatim
reason. This app never fills a form or submits an application, so no plan could use them.

**Golden schemas.** None. The app touches nothing in self, belief, value, goal, percept, act or
learn.

**Nearly right.**
- **`work-authorization`**: citizenship, not the passport held, and no passport type. Until the
  planned workflows are built, the app researches ordinary passports only and must not
  approximate any other kind.
- **`place`**: no schema holds only where a person lives, and a DLR narrows fields but not rows, so
  every place the person holds arrives — as its grain and a country code only.
- **`travel-plan`**: holds several candidates and several travellers; one plan here is one traveller
  and one destination, so each candidate is researched on its own.

## 12. Custom schemas

**Only if you have one.** None. The travel schemas published on 2026-09-21 hold what this app
would otherwise have needed its own schema for: the passport as a document of a type, and
residence with its status and expiry.

## 13. Cross-app connections

**Who reads what you write.** Any app reading `travel-plan` — this one included, which offers a
saved trip back as the form's starting point (§7, planned C). The `place` it mints is the
person's own country node, which any app's `place` reference may then point at.

**If nobody does.** The trip still starts this app's form next time. Nothing concluded feeds
outward, which matches §4.

## 14. Stratum & ratification

**Asserted.** The trip a traveller saves, on their click: their own statement of where they are
considering going, for what and when (§7, workflow 3). Nothing inferred.

**Proposed.** Nothing. The click on "Save this trip to Ofself" is the traveller's approval, so
the trip is written directly rather than proposed for a second approval on Ofself (DECISIONS entry
283). Which passport a trip is on is not written: no schema can link one to a trip.

**If nothing.** Only what the traveller stated is written, so no inference exists to compound.

## 15. Platform features

| Feature | Using it | Why / why not |
|---|---|---|
| `nodes:read` | yes | `work-authorization` for passport nationality today; the travel schemas and `place` for §7's planned workflows |
| `nodes:create` | yes | `travel-plan` and `place` only, on the traveller's click: the trip they chose to save (§7, workflow 3). Nothing concluded is stored; §4 |
| `nodes:edit` | no | Another app's record is not this app's to change |
| `nodes:delete` | no | As above |
| `relationships` | no | No graph structure is needed to answer a visa question |
| `personas` | no | Research is one bounded pipeline with its own model calls, not a conversational agent |
| `shell` | no | The app has its own page; adopting `@ofself/shell` is not planned yet |
| `webhooks` | no | No user data is mirrored; authorisation is checked on each request instead |
| `encryption` | no | Every field read is unencrypted; §10 |
| `identity_switching` | no | A visa plan is for one person |
| `discovery:groups` | no | Not needed |
| `discovery:friends` | no | Not needed |
| `discovery:realms` | no | Not needed |
| `sub_entity` | no | One pipeline, one grant |
| `plugins:execute` | no | Uses no other app's plugin |
| `is_plugin` | no | A plugin's output would be a stored answer; §4 |
| `activity` | no | Not needed |
| `automations` | no | Research runs only when a traveller asks |
| `nodes:propose` | no | Nothing is written back yet; §14 |
| `nodes:modify` | no | As `nodes:edit` |
| `golden` | no | §11 |
| `own_database` | yes | Stores of official pages and research results, keyed by country codes and never by user; §16 |

## 16. Its own data

**App-side.** Stores of official government pages and their text, a store of resolved research for
a combination of destination, passport, country applied from and purpose (country codes only, kept
a week), a store of model drafts reused for 24 hours, and usage logs. None of it is keyed by an
Ofself user id. In the graph: only what is read, and nothing written.

**Kept in step.** Nothing to keep in step: the traveller's details are read from Paradigm on each
request and not stored. One caveat is the draft store: a draft is written from the whole traveller
profile, so it can mention a city or a residence status, and it is reused for 24 hours but nothing
deletes it afterwards. That has to be fixed before real users (TODO item 55, *Left to do*).
