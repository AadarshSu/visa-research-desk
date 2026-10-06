"""Environment-backed runtime settings."""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings read from environment variables or a local `.env` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Visa Research Agent"
    cache_directory: Path = Path("var/cache")
    corridor_directory: Path = Path("var/corridors")
    # The page corpus (DECISIONS entry 44). Kept beside the other stores, but note the contract is
    # different: this one is depended on, so losing it costs coverage rather than a question.
    corpus_directory: Path = Path("var/corpus")
    # The body text of pages already fetched, indexed for ranking only and never quoted. Separate
    # from `cache_directory`, which holds the same text as *evidence* under its own freshness
    # rules: these two must not be conflated, because a body served from here would be guidance
    # with nothing to say how old it is. See `discovery/page_text.py`.
    page_text_directory: Path = Path("var/pagetext")
    # What each corridor considered, for diagnosing a refusal afterwards. One file per corridor,
    # overwritten by the newest run, read by nobody: deleting the directory costs a question, never
    # an answer.
    recall_log_directory: Path = Path("var/recall")
    # What every model call cost — selection, roles, blocked pages and the plan (DECISIONS entries
    # 165 and 166). One JSON line per call and one file per UTC day, appended rather than
    # overwritten, because the question is a day's spend against the provider's bill. Read by
    # nobody at runtime: deleting it costs a question, never an answer.
    model_usage_directory: Path = Path("var/usage")
    # One week — three until entry 258, which found a degraded resolution served for days. A
    # corridor is not evidence: which pages answer it changes when a site is
    # redesigned, not when its guidance is edited. The pages themselves are re-fetched under the
    # much shorter evidence TTL every time a plan is produced.
    corridor_maximum_age_hours: float = 24.0 * 7
    # Model drafts of a plan, reused for exactly the same inputs within `plan_reuse_hours`, which is
    # runtime policy (DECISIONS entry 178). A draft is kept, never a plan, so deleting the directory
    # costs a model call, never an answer.
    plan_directory: Path = Path("var/plans")
    # Travellers' reports (TODO item 74): never read on the request path, and not a cache to clear.
    report_directory: Path = Path("var/reports")
    maximum_fixture_characters: int = 50_000

    # Live retrieval tuning only. Which sources are contacted, which extractor runs, and when
    # stale evidence is refused are reviewable policy and live in `config/runtime.yaml`.
    source_fetch_timeout_seconds: float = 20.0
    source_fetch_concurrency: int = 4
    maximum_source_characters: int = 50_000
    minimum_source_characters: int = 400
    maximum_source_bytes: int = 12_000_000
    source_user_agent: str = (
        "VisaResearchAgent/0.1 (personal visa research; contact repository owner)"
    )

    # Rendering tuning only. Whether rendering happens at all is policy and lives in
    # `config/runtime.yaml`, because it changes how government sites are contacted.
    render_timeout_seconds: float = 20.0
    render_settle_milliseconds: int = 2_500
    render_challenge_settle_milliseconds: int = 20_000
    """How long to wait when the render is answering a challenge rather than reading a thin page.

    Measured 2026-08-25: `www.gov.cy` and `www.mzv.sk` both settle well inside this and neither
    does at 2,500ms, which is why the first corridors run against them still reported the challenge
    as unanswered. Cloudflare and Azure run a proof-of-work before replacing the interstitial, so
    this is a property of the challenge rather than of the site. DECISIONS entry 75.
    """
    # Separate allowances: discovery visits many more pages than retrieval, and a single shared
    # count let the crawl spend it all before the pages that become evidence were read.
    maximum_source_renders: int = 5
    maximum_crawl_renders: int = 12

    # Source discovery. The search key is a secret; the rest is machine-local tuning. Whether
    # discovery may run at all is not a setting: it is a separate command, run deliberately.
    search_api_key: SecretStr | None = None
    search_timeout_seconds: float = 15.0
    discovery_host_delay_seconds: float = 0.5

    # Ofself, through its developer platform Paradigm (TODO item 55). The API key is this app's own
    # credential and a secret; `paradigm app push` wrote it to `.paradigm/secrets.toml`, and it is
    # read from here like every other secret.
    paradigm_api_key: SecretStr | None = None
    paradigm_base_url: str = "https://api.ofself.ai"
    paradigm_timeout_seconds: float = 10.0
    # Sign-in with Ofself. The client id is public — it appears in the authorize link — and the
    # redirect URI must be one registered on the app. Sign-in stays off unless the client id, the
    # API key and a session secret are all set, so the anonymous form keeps working without them.
    paradigm_client_id: str | None = None
    paradigm_authorize_url: str = "https://app.ofself.ai/authorize"
    paradigm_redirect_uri: str = "http://localhost:8000/oauth/callback"
    # Signs the session cookie. A secret: generate one per deployment, never commit it.
    session_secret: SecretStr | None = None
    session_max_age_hours: float = 12.0
    # True wherever the app is served over HTTPS. False only so a localhost session works.
    session_cookie_secure: bool = False
    # Whether every plan needs an Ofself sign-in. Off by default: a visitor who has not signed in
    # may generate `anonymous_plan_allowance` plans, counted per address, and signing in is offered
    # for importing the traveller's details and for more plans (DECISIONS entry 262). True locks
    # every plan behind sign-in, as entry 191 had it.
    require_sign_in: bool = False
    anonymous_plan_allowance: int = 10
    # The counts behind it. A store, not a cache: deleting it hands every address its plans back.
    allowance_file: Path = Path("var/allowance/anonymous.json")

    # Model calls through Ofself Personas, when `model_route: personas` (TODO item 62). The app id
    # and HMAC key come from `paradigm personas register`; the key is a secret. Every run is made as
    # one Paradigm user, because these calls serve no particular traveller (OFSELF_FEEDBACK 8.16).
    personas_app_id: str | None = None
    personas_hmac_key: SecretStr | None = None
    personas_user_id: str | None = None
    personas_base_url: str = "https://personas.ofself.com"
    # Longer than the OpenAI timeout: Personas adds a hop in front of the same call.
    personas_timeout_seconds: float = 120.0
    # Shorter limits for the two calls that are always quick when they work, so a call stuck behind
    # Personas' gateway ends the run at once rather than at the gateway's 55 s (entry 258). Over the
    # logged calls to 2026-10-05: 488 selections succeeded in at most 17.0 s (99% within 9.4 s), 490
    # role calls in at most 23.2 s. The plan call keeps the general limit: it runs up to 51 s.
    personas_selection_timeout_seconds: float = 30.0
    personas_roles_timeout_seconds: float = 40.0

    # The plan call's input guard. 80,000 until 2026-09-24, which sat just above normal traffic —
    # median 17,918, 90th percentile 70,448, largest 73,630 over 131 logged plan calls — and
    # refused `india/BD/SA` in 3 of 8 runs at 91,787 after the answer was already credited. Replayed
    # on that input the plan answered "visa required" 3 of 3 in 17–22s (TODO item 70). Doubled:
    # it changes only calls that refused, and stays far below the 272K-token price threshold
    # (item 59).
    maximum_model_input_characters: int = 160_000
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    openai_request_timeout_seconds: float = 60.0
    openai_max_output_tokens: int = 6_000
    openai_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "low"


settings = Settings()
