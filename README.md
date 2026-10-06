# Visa Research Agent

Generates bounded, source-backed visa application plans. For a traveller — passport, where they apply
from, purpose — and a destination, it reads that country's own government pages, cites every claim,
and states plainly what it could not verify. It does not guarantee eligibility, completeness or
approval, and it never submits, books or fills anything on anyone's behalf.

> **Picking the project up?** Start with [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md). Then
> [ARCHITECTURE.md](ARCHITECTURE.md) for how it is built, [DECISIONS.md](DECISIONS.md) for why, and
> [TODO.md](TODO.md) for what remains. This README covers what it does and how to run it.

## What it does

- **Finds its own sources.** Nobody curates URLs. Which domains a country may be researched from is
  generated offline, reviewed, and committed in `config/authority_domains.yaml`; **55 of 198 countries
  have a row**, and a country without one is refused rather than guessed at. Each of the 55 has an
  offline page corpus and a page-text index; a request picks from those plus live search, and reads
  the chosen pages live.
- **Trusts a domain, never a page.** A page is evidence only if it sits on the destination's own
  government domain — governmental *and* under that country's top-level domain — checked when
  configuration loads, after every redirect, and after every meta-refresh forward. For a Schengen
  member, the EU may also answer the visa decision.
- **Behaves as an honest client.** It identifies itself, obeys `robots.txt`, answers a browser
  challenge only by rendering the page under its own user agent, and never works around a refusal. A
  page it was refused is named with its link, never read.
- **Reads documents, not just pages** — PDFs, the forwarding pages authorities hide them behind, and
  client-rendered pages through a headless browser that widens no trust.
- **Asks a model to choose, never to invent.** One call picks which stored pages to read, one decides
  which fetched page answers each question, and one writes the plan. The model can only cite pages
  the application fetched, and a visa
  decision no page stated is forced to *unknown*.
- **Hands over an official questionnaire** when that is how the authority publishes the answer
  (the UK's visa checker, France's Visa Wizard), rather than refusing or driving it.
- **Degrades honestly.** Every source resolves to a typed outcome; a plan is `verified` or `partial`;
  a corridor that cannot be established is refused with a diagnosis.

**Hosted on one AWS EC2 instance**, and it runs locally; either way a plan requires signing in with
[Ofself](CRUX.md). Whether an answer is **correct** is checked by the owner outside this repository;
the project's own measures report whether a corridor answered, never whether it was right.

## Evidence status

| Outcome | Usable | Meaning |
| --- | --- | --- |
| `ok` | yes | Retrieved fresh, or cached inside the TTL, from a trusted domain |
| `stale` | yes | A refresh failed, so cached text was served inside the stale ceiling |
| `untrusted` | no | The final URL left the approved domains |
| `unreachable` | no | Timeout, connection error, or an error status |
| `unusable` | no | Retrieved but not evidence — a client-rendered shell, an unreadable PDF, too little text |
| `blocked` | no | The authority refused this client (`401`, `403`, `429`) |
| `disallowed` | no | The host's `robots.txt` excluded this client, or could not be read |
| `challenged` | no | A browser check was not answered; the authority stated nothing |

## Requirements

- Python 3.12 or newer
- No API key is needed for the offline fixture path or the tests
- Optional: `pip install -e ".[render]"` and `playwright install chromium` for `render_mode: on_demand`

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
```

Run the app and open <http://127.0.0.1:8000/> (API docs at `/docs`):

```bash
.venv/bin/uvicorn visa_research_agent.api.app:create_app --factory
```

Run the checks:

```bash
ruff check .
ruff format --check .
mypy
pytest
```

## API

- `GET /health` — application health.
- `GET /destinations` — the destinations the interface offers.
- `POST /visa-plans` — a plan for `{"destination": "japan", "traveller": {"passport_nationality":
  "IN", "country_of_residence": "GB", "travel_purpose": "tourism"}}`. Without an Ofself session, ten
  free plans per address (`ANONYMOUS_PLAN_ALLOWANCE`), then `429`; `REQUIRE_SIGN_IN=true` requires a
  session for every plan. Answers `503` naming what could not be verified.
- `POST /visa-plans/stream` — the same plan, as newline-delimited JSON: a `stage` event as each step
  starts, then one `plan`, `refusal` or `error` event. The page uses this one.
- `/oauth/login`, `/oauth/callback`, `/oauth/session`, `/oauth/traveller`, `/oauth/logout` — sign-in
  with Ofself.

## Source discovery

```bash
visa-discover registry --only FR,DE          # regenerate registry rows for these countries
visa-discover bootstrap --destination-name Brazil   # propose one country's domains, to read
visa-discover corpus --country CA            # build a country's offline page corpus
visa-discover corridor --destination japan --nationality IN --from GB --purpose tourism
visa-discover audit var/recall/              # why travellers go unanswered, counted
```

More commands, and when to use each, are in [CLAUDE.md](CLAUDE.md) and [AGENTS.md](AGENTS.md).

## Configuration

| Where | Holds | Committed? |
| --- | --- | --- |
| `src/visa_research_agent/config/runtime.yaml` | source mode, extraction mode, render mode, model route, cache TTL, stale ceiling | **Yes** — they decide whether government sites are contacted, whether a paid model is called, and when stale guidance is refused |
| `config/authority_domains.yaml`, `supranational_authorities.yaml`, `service_providers.yaml` | who may be believed, and which contractors may be named | **Yes** — the trust anchor |
| `config/destinations.yaml` | seven hand-configured destinations, used only under `destination_mode: configured` | **Yes** |
| `config/discovery_*.yaml`, `countries.yaml` | scoring vocabulary, denylist, country reference data | **Yes** |
| `.env` | API keys (OpenAI, Brave search, Personas, Paradigm), session secret, timeouts, cache directory | **Never** |

The fully deterministic path, for development and tests:

```yaml
source_mode: fixtures
extraction_mode: fixture
```

It knows only Singapore, from saved evidence under `src/visa_research_agent/fixtures/singapore/`.
The model's instructions are in `src/visa_research_agent/prompts/`. Runtime state lives under `var/`
and is not committed.
