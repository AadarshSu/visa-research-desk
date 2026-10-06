"""Deciding what a page is, deterministically.

Two rules do most of the work:

  * Link text outweighs the URL. Japan's tourism checklist lives at "index_000070.html" and is
    identifiable only by being labelled "Temporary Visitor Visa". A URL-only scorer fails outright.
  * Wrong-audience pages must be pushed down hard. A spouse-visa checklist on the correct domain,
    in the correct format, is the most dangerous candidate there is, because every other check
    passes and the traveller is told to bring the wrong documents.

No model is involved. Scores are explainable, repeatable, and free.
"""

import re
from urllib.parse import urlsplit

from visa_research_agent.discovery.lexicon import (
    Country,
    CountryRegistry,
    Lexicon,
    PurposeTerms,
)
from visa_research_agent.discovery.models import (
    ROLE_ORDER,
    CandidatePage,
    Corridor,
    DiscoveryRole,
    PageLink,
    RoleScores,
)
from visa_research_agent.discovery.urls import filed_date_in_path, is_pdf_url, path_segments
from visa_research_agent.domain.models import SourceKind
from visa_research_agent.domain.trust import host_is_within, host_of, registrable_domain

_WORDS = re.compile(r"\w+")


def _contains_phrase(haystack: str, phrase: str) -> bool:
    return phrase.lower() in haystack.lower()


def _contains_word(haystack: str, phrase: str) -> bool:
    """True when a phrase appears as whole words rather than inside a longer one.

    Country tokens must never match inside a word. "us" sits in "business", "house" and "because";
    "chad" in "Chadwick"; "oman" in "Romania". Since `wrong_country` is a veto, a substring match
    silently rejected the most relevant page a corridor had — every business-purpose page was
    being thrown away as though it were about the United States.
    """

    return re.search(rf"\b{re.escape(phrase.lower())}\b", haystack.lower()) is not None


def searchable_url(url: str) -> str:
    """Flatten a URL so prose phrases match it.

    URLs separate words with hyphens, underscores and slashes where the vocabulary uses spaces, so
    without this "documents-required.pdf" would never match the phrase "documents required".
    """

    lowered = url.lower()
    for separator in ("-", "_", "/", ".", "+"):
        lowered = lowered.replace(separator, " ")
    return lowered


def link_words(link: PageLink) -> set[str]:
    """Every word a link's own text, path and host contain, for the country prefilter.

    Computed once per link. `_matches_country` derives the same three things — the path segments,
    the lowered text, the host labels — and `wrong_country` used to call it once per country, so
    each was rebuilt 198 times for every candidate. That is most of what made it the corridor's
    hottest function once the corpus grew. See `CountryRegistry.possible_for`.
    """

    words = set(_WORDS.findall(link.text.lower()))
    for segment in path_segments(link.url):
        words.update(_WORDS.findall(segment))
    words.update(host_of(link.url).split("."))
    return words


def _matches_country(link: PageLink, country: Country) -> bool:
    """True when a link plainly refers to a country.

    Two-letter codes are matched only as a whole path segment or host label, never inside a word,
    or "in" would match "information" on almost every government page.
    """

    segments = path_segments(link.url)
    text = link.text.lower()
    host_labels = host_of(link.url).split(".")

    for token in country.text_tokens:
        if _contains_word(text, token):
            return True
        if any(token == segment or token in segment.split("-") for segment in segments):
            return True
    return any(label in host_labels for label in country.host_labels)


def _segment_names(segment: str, token: str) -> bool:
    """True when a path segment names a country, including a multi-word name written with hyphens.

    A URL writes "United Kingdom" as `united-kingdom`, and the obvious test — `token == segment or
    token in segment.split("-")` — matches neither that nor `apply-united-kingdom`, because the
    token carries a space and the segment's words carry none. So **every country whose name is more
    than one word was invisible to this check unless the anchor text happened to say it**, which is
    much of the world: the Netherlands publishes its UK tourism checklist at a path ending
    `/united-kingdom`, labels it "Checklist: tourism", and it named no country at all as far as
    scoring was concerned. Matching a contiguous run of the token's words against the segment's
    hyphen-separated words covers all three shapes and leaves single-word tokens unchanged.
    """

    words = segment.split("-")
    wanted = token.split()
    if not wanted:
        return False
    return any(
        words[start : start + len(wanted)] == wanted
        for start in range(len(words) - len(wanted) + 1)
    )


def _describes_country(link: PageLink, country: Country) -> bool:
    """True when a link's own words say it is *about* a country.

    Deliberately narrower than `_matches_country`, and the whole difference is the host. A host
    label names which **post** published a page — `in.diplomatie.gouv.fr` is France's mission in
    India — and that says nothing about who the page is for: the post's accessibility statement,
    privacy notice and legal notice all sit on that same host. Who a page is *about* comes from its
    path and its title.

    Keeping the two apart matters because the two bonuses would otherwise fight. Which post serves
    a traveller is decided by where they are applying from (`mission_affinity`), so letting the
    nationality bonus also fire on a host label made the post for the applicant's *home* country
    outrank the post they must actually apply at.
    """

    segments = path_segments(link.url)
    text = link.text.lower()
    for token in country.text_tokens:
        if _contains_word(text, token):
            return True
        if any(_segment_names(segment, token) for segment in segments):
            return True
    return False


def is_boilerplate(url: str, lexicon: Lexicon) -> bool:
    """True when a path marks a page as site furniture rather than guidance.

    An accessibility statement, a privacy notice and a legal notice are on every page of a
    government site, and they cannot be visa guidance whatever they score — so this rejects rather
    than penalises, exactly as `is_archived` does.

    They scored well for a reason worth knowing: `extract_links` gives a link the last heading seen
    above it, and footer links sit below everything, so France's legal notice inherited the heading
    of a news article about visa requirements and collected the heading bonus twice.
    """

    segments = path_segments(url)
    return any(token in segments for token in lexicon.boilerplate_tokens)


def mission_in_path(url: str, lexicon: Lexicon) -> str | None:
    """The post a URL belongs to, when its path names one.

    Brazil publishes every mission under one host as `/consulado-edimburgo`, `/embaixada-riade`,
    so a host-based check cannot tell them apart. Returns what follows the marker — "edimburgo",
    "riade" — or None when the path names no post at all, which is the common case.
    """

    for segment in path_segments(url):
        for marker in lexicon.mission_path_markers:
            if segment == marker:
                continue  # A bare "/embassy/" section index names no particular post.
            for separator in ("-", "_"):
                prefix = f"{marker}{separator}"
                if segment.startswith(prefix):
                    return segment[len(prefix) :]
    return None


# The roles whose answer is a property of the *post*, so a different post's page is the wrong page.
#
# Deliberately not every role. `visa_decision` is left out because whether a passport needs a visa
# is set by the destination's law and is the same at every consulate — demoting the page that
# states it would cost answers to buy nothing, and it is the one role whose absence refuses a
# corridor. `general_entry` is left out for the same reason: Schengen entry conditions do not vary
# by where the traveller lodges.
#
# `fees` and `processing_times` were added on 2026-08-25 after a corridor was measured taking
# Brazil's **Edinburgh** fee page for a traveller in the United States (DECISIONS entry 72). A fee
# is quoted in the post's own currency and a processing time is that post's queue.
POST_SPECIFIC_ROLES: tuple[str, ...] = (
    "document_checklist",
    "application_route",
    "fees",
    "processing_times",
)


def foreign_post_labels(
    countries: CountryRegistry, destination_code: str | None, residence: Country
) -> frozenset[str]:
    """Mission labels that can only mean *somebody else's* post, for this corridor.

    A label is included only when **no** country claiming it is the destination or the residence.
    That exemption is the whole safety of the rule and it was found by measurement, not by
    reasoning: without it, `mzv.gov.cz` reads as another post for a traveller in Great Britain
    because `cz` is one of Czechia's own mission labels, and 146 pages that had correctly filled a
    role were penalised for sitting on their own government's hostname (DECISIONS entry 72).
    """

    exempt = {code for code in (destination_code, residence.code) if code}
    claimed: dict[str, set[str]] = {}
    for country in countries.countries:
        for label in country.mission_labels:
            claimed.setdefault(label.lower(), set()).add(country.code)
    return frozenset(label for label, owners in claimed.items() if not owners & exempt)


def mission_affinity(
    url: str,
    residence: Country,
    lexicon: Lexicon,
    *,
    other_posts: frozenset[str] = frozenset(),
) -> str | None:
    """Whether a page belongs to the post serving this traveller, another post, or no post.

    Returns "own", "other", or None.

    **A host may now conclude "other", where before only a path could.** The original caution was
    right about the general case — inferring "other" from any host label would misread the many
    ministry pages that belong to no mission at all — but it left a gap with a name:
    `india.embassy.gov.au` is unmistakably the New Delhi post, and for a traveller in Great Britain
    it scored as though it belonged to no post whatsoever. `other_posts` closes it without
    reopening the original problem, because it holds only labels that some *third* country claims
    as a post of its own: `www`, `mfa` and `mzv` are in it for nobody.

    Only labels outside the registrable domain are read. `gov.au` is Australia's public suffix, not
    a post, and a country's own code sitting in its own TLD must never read as somebody else's
    mission. Measured over 132 recorded corridors: 703 candidates become "other", and the single
    one of them that had filled a role is the New Delhi page this exists to demote.
    """

    labels = {label.lower() for label in residence.mission_labels}
    host = host_of(url)
    host_labels = host.split(".")
    subdomain_labels = host_labels[: -len(registrable_domain(host).split("."))]
    if labels and any(label in host_labels for label in labels):
        return "own"

    post = mission_in_path(url, lexicon)
    if post is not None:
        if post.lower() in labels or any(part in labels for part in post.lower().split("-")):
            return "own"
        return "other"

    if any(label.lower() in other_posts for label in subdomain_labels):
        return "other"
    return None


def names_documents(haystack: str, lexicon: Lexicon) -> list[str]:
    """The distinct documents a page names.

    Distinct, not counted occurrences: a page repeating "passport" twenty times is a passport page,
    not a list of what to bring.
    """

    return [noun for noun in lexicon.document_nouns if _contains_phrase(haystack, noun)]


def is_archived(url: str, lexicon: Lexicon) -> bool:
    """True when a path marks a page as superseded.

    An archived checklist that still reads plausibly is exactly what must never be selected, so
    this rejects rather than penalises.
    """

    segments = path_segments(url)
    if any(token in segments for token in lexicon.archive_tokens):
        return True
    # A year two or more years old in the path is a strong archive signal — **except where it says
    # when something was filed**: an upload folder or a post's permalink (`filed_date_in_path`).
    # Malta's current visa list sits at `uploads/2023/10/` (entry 198), and South Africa's, Cyprus's
    # and Croatia's current guidance in the other forms (entry 203). There it is a publication date,
    # which `published_date_in_path` reports to the adjudicator instead, as entry 15 decided.
    filed = filed_date_in_path(url)
    spared = filed[0] if filed is not None else None
    return any(
        segment.isdigit() and len(segment) == 4 and 1990 <= int(segment) <= 2024 and index != spared
        for index, segment in enumerate(segments)
    )


def wrong_audience(link: PageLink, corridor: Corridor, lexicon: Lexicon) -> str | None:
    """Name the audience a page is for when it is plainly not this traveller.

    A veto rather than a penalty. A diplomatic-passport exemption list names many nationalities and
    reads like authoritative guidance, so no accumulated score should be able to carry it through.
    """

    own = {term.lower() for term in lexicon.purposes.get(corridor.purpose, PurposeTerms()).terms}
    haystack = f"{link.text} {link.heading} {searchable_url(link.url)}".lower()
    # A page that asks the visa question for everyone is not for another audience because it also
    # mentions one of them: the Netherlands' checker is "Do I need a visa and/or a residence permit"
    # (TODO item 70). Only the terms the lexicon lists yield, so a diplomatic list never does.
    asks_visa_question = any(term.lower() in haystack for term in lexicon.visa_question_terms)
    yielding = {term.lower() for term in lexicon.hard_off_scope_beside_visa_question}
    for term in lexicon.hard_off_scope:
        if term.lower() in own:
            continue
        if asks_visa_question and term.lower() in yielding:
            continue
        if term.lower() in haystack:
            return term
    return None


def wrong_country(
    link: PageLink,
    corridor: Corridor,
    registry: CountryRegistry,
    destination_code: str | None,
) -> str | None:
    """Name a country the page is plainly about that is not part of this corridor.

    Used only to reject. The reverse inference is not safe: a page mentioning India is not
    necessarily a page *for* Indians.
    """

    allowed = {corridor.passport_nationality, corridor.applying_from}
    if destination_code:
        allowed.add(destination_code)
    # Prefiltered, then checked exactly. `possible_for` returns a superset in registry order, so
    # the country named is the same one the full scan would have named — only the number of exact
    # checks changes. Measured on Canada's 3,216-entry corpus: 3,277ms to 98ms, identical output on
    # every entry. DECISIONS entry 50.
    for country in registry.possible_for(link_words(link)):
        if country.code in allowed:
            continue
        if _matches_country(link, country):
            return country.name
    return None


def score_role_vocabulary(link: PageLink, lexicon: Lexicon) -> RoleScores:
    """How much this link looks like visa guidance **for anybody** — no traveller involved.

    The corridor-independent half of `score_link`, which is the whole of what a page's own words can
    say before you know who is asking. Everything the corridor decides — whether the page names this
    traveller's nationality, whether it is the post serving where they live, whether their purpose
    matches — is layered on top by `score_link` and stays there.

    Extracted rather than copied on 2026-08-22, for the corpus crawl (DECISIONS entry 44). That job
    walks a country's sites with no traveller at all, so it needs exactly this and must not have the
    rest: a corpus guided by one nationality's vocabulary would be a corpus quietly built for that
    nationality, which is the corridor-dependence entry 44 takes out of the store.
    """

    url_text = searchable_url(link.url)
    label = link.text.strip().lower()
    heading = link.heading.strip().lower()
    anchor = f"{label} {heading}".strip()
    scores: dict[str, float] = {}
    signals: dict[str, list[str]] = {}

    for role_name in ROLE_ORDER:
        role_terms = lexicon.roles.get(role_name)
        if role_terms is None:
            continue
        total = 0.0
        reasons: list[str] = []
        for term in role_terms.terms:
            if _contains_phrase(url_text, term.phrase):
                total += term.weight
                reasons.append(f"url:{term.phrase}+{term.weight:g}")
            if label and _contains_phrase(label, term.phrase):
                weighted = term.weight * lexicon.link_text_weight
                total += weighted
                reasons.append(f"text:{term.phrase}+{weighted:g}")
            # A heading describes the section, not this link, so it counts for less. Without
            # this every form under "Visa Application Documents" reads as a checklist.
            elif heading and _contains_phrase(heading, term.phrase):
                weighted = term.weight * lexicon.heading_weight
                total += weighted
                reasons.append(f"heading:{term.phrase}+{weighted:g}")
        if total:
            scores[role_name] = total
            signals[role_name] = reasons

    mentions_visa = "visa" in url_text or (anchor and "visa" in anchor)
    if not scores and mentions_visa:
        scores["visa_decision"] = lexicon.base_visa_weight
        signals["visa_decision"] = [f"mentions-visa+{lexicon.base_visa_weight:g}"]
    elif mentions_visa and "visa_decision" in scores:
        scores["visa_decision"] += lexicon.base_visa_weight
        signals["visa_decision"].append(f"mentions-visa+{lexicon.base_visa_weight:g}")

    return RoleScores(scores=scores, signals=signals)


def score_link_in_context(link: PageLink, lexicon: Lexicon) -> RoleScores:
    """`score_role_vocabulary`, plus what surrounds the link — **for a corpus build only.**

    A build decides what to open from a link's label, heading and address, and a label like
    "United Kingdom (PDF, 332 KB)" or "here" says nothing. Three things from the page that held the
    link say more, and each is weighted below the label (entry 189):

    - **the text around it** — its list item, paragraph or table cell, and its `title` and
      `aria-label` — counted at `context_weight` for phrases the label and heading do not already
      carry, so a link is not paid twice for one phrase;
    - **the page's own title**, inherited only by a **document** link with no signal of its own, at
      `linking_title_weight`. A crawl never follows a PDF, so this reorders the PDF pass and cannot
      give a host's HTML links a score that lets them read past its share (entry 186);
    - **a footer**, which halves everything, because that is where site furniture lives.

    Never used to rank a corridor's candidates. A corridor's link score decides what its selector
    is shown, so a change there is a recall change priced in model input (entries 126 and 158);
    here it only decides what an offline build reads.
    """

    base = score_role_vocabulary(link, lexicon)
    scores = dict(base.scores)
    signals = {role: list(reasons) for role, reasons in base.signals.items()}
    own = f"{link.text} {link.heading}".lower()
    context = link.context.lower()

    for role_name in ROLE_ORDER:
        role_terms = lexicon.roles.get(role_name)
        if role_terms is None or not context:
            continue
        for term in role_terms.terms:
            if _contains_phrase(context, term.phrase) and not _contains_phrase(own, term.phrase):
                weighted = term.weight * lexicon.context_weight
                scores[role_name] = scores.get(role_name, 0.0) + weighted
                signals.setdefault(role_name, []).append(f"context:{term.phrase}+{weighted:g}")

    if not scores and link.linking_title and is_pdf_url(link.url):
        inherited = score_role_vocabulary(
            PageLink(url=link.url, text=link.linking_title, depth=link.depth), lexicon
        )
        for inherited_role, value in inherited.scores.items():
            if value > 0:
                weighted = value * lexicon.linking_title_weight
                scores[inherited_role] = weighted
                signals[inherited_role] = [f"linking-title:{weighted:g}"]

    if link.region == "footer":
        for scored_role in list(scores):
            scores[scored_role] *= lexicon.footer_factor
            signals.setdefault(scored_role, []).append(f"footer x{lexicon.footer_factor:g}")

    return RoleScores(scores=scores, signals=signals)


def score_link(
    link: PageLink,
    corridor: Corridor,
    lexicon: Lexicon,
    nationality: Country,
    residence: Country,
    *,
    host_kind: SourceKind | None = None,
    mission_domains: list[str] | None = None,
    other_posts: frozenset[str] = frozenset(),
) -> RoleScores:
    """Score a link for every role, from its URL, anchor text and heading."""

    url_text = searchable_url(link.url)
    label = link.text.strip().lower()
    heading = link.heading.strip().lower()
    # Kept together only for the cheap "does this mention X at all" checks below.
    anchor = f"{label} {heading}".strip()
    vocabulary = score_role_vocabulary(link, lexicon)
    scores: dict[str, float] = dict(vocabulary.scores)
    signals: dict[str, list[str]] = {
        role: list(reasons) for role, reasons in vocabulary.signals.items()
    }

    # A link labelled only with the traveller's purpose is still worth reading. Japan's tourism
    # checklist is reached by a link saying just "Tourism", whose URL never mentions visas, so
    # without this the correct page is scored zero and never even fetched.
    purpose_label = lexicon.purposes.get(corridor.purpose)
    if (
        purpose_label
        and label
        and any(_contains_phrase(label, term) for term in purpose_label.terms)
    ):
        for role_name in ("document_checklist", "application_route"):
            if role_name not in scores:
                scores[role_name] = lexicon.base_purpose_weight
                signals[role_name] = [
                    f"purpose-label:{corridor.purpose}+{lexicon.base_purpose_weight:g}"
                ]

    if not scores:
        return RoleScores()

    shared = 0.0
    shared_reasons: list[str] = []

    if _describes_country(link, nationality):
        shared += lexicon.nationality_weight
        shared_reasons.append(f"nationality:{nationality.code}+{lexicon.nationality_weight:g}")

    purpose_terms = lexicon.purposes.get(corridor.purpose)
    if purpose_terms and any(
        _contains_phrase(url_text, term) or (anchor and _contains_phrase(anchor, term))
        for term in purpose_terms.terms
    ):
        shared += purpose_terms.weight
        shared_reasons.append(f"purpose:{corridor.purpose}+{purpose_terms.weight:g}")

    # One penalty, not one per term: the presence of a wrong-audience signal is what matters.
    off_scope = lexicon.off_scope_terms_for(corridor.purpose)
    hit = next(
        (
            term
            for term in off_scope
            if _contains_phrase(url_text, term) or (anchor and _contains_phrase(anchor, term))
        ),
        None,
    )
    if hit is not None:
        shared += lexicon.off_scope.weight
        shared_reasons.append(f"off-scope:{hit}{lexicon.off_scope.weight:g}")

    # A blank form is not a list of what to bring, and a translated copy is not a new page.
    form_hit = next(
        (
            term
            for term in lexicon.form_terms
            if _contains_phrase(url_text, term) or (anchor and _contains_phrase(anchor, term))
        ),
        None,
    )
    if form_hit is not None:
        shared += lexicon.form_penalty
        shared_reasons.append(f"form:{form_hit}{lexicon.form_penalty:g}")

    # Whole words only (entry 220): as a substring, "thai" sat in every "Thailand" and "german" in
    # every "Germany", so a destination's own name cost its pages 25 points — Thailand's 15 July
    # announcement naming India scored -19 for the decision and was offered 57th of 160.
    language_hit = next(
        (term for term in lexicon.language_terms if anchor and _contains_word(anchor, term)),
        None,
    )
    if language_hit is not None:
        shared += lexicon.language_penalty
        shared_reasons.append(f"translation:{language_hit}{lexicon.language_penalty:g}")

    segments = path_segments(link.url)
    stem = segments[-1].rsplit(".", 1)[0] if segments else ""
    if stem in lexicon.index_page_stems:
        shared += lexicon.index_page_penalty
        shared_reasons.append(f"section-index{lexicon.index_page_penalty:g}")

    if len(path_segments(link.url)) <= 4:
        shared += lexicon.shallow_path_weight
        shared_reasons.append(f"shallow+{lexicon.shallow_path_weight:g}")

    if link.depth:
        shared += lexicon.depth_penalty_weight * link.depth
        shared_reasons.append(f"depth{link.depth}{lexicon.depth_penalty_weight * link.depth:g}")

    if host_kind:
        bonus = lexicon.authority_kind_bonus.get(host_kind, 0.0)
        if bonus:
            shared += bonus
            shared_reasons.append(f"{host_kind}+{bonus:g}")

    for scored_role in list(scores):
        scores[scored_role] += shared
        signals[scored_role].extend(shared_reasons)

    # A page about the country the traveller applies *from* earns something on the roles the post
    # governs, where before it earned nothing at all: `wrong_country` read "this page is about
    # country X" only to reject, so the fact could exclude a page and never promote one. Canada
    # publishes 635 "where to submit your application" pages, one per country of application, and
    # for an Indian national in Britain the `?country=GB` page — the one that answers them — scored
    # -8.0 for `application_route` while its `?country=IN` sibling scored 32.0. DECISIONS entry 126.
    #
    # **It adds and never subtracts, which is the amendment and not the original shape.** This first
    # shipped as a *swap*: the nationality bonus withdrawn from these four roles and this one put in
    # its place, so the two pages changed seats. That inferred "for applicants in country X" from
    # "about country X", which is a weaker claim than the one `mission_affinity` makes about a
    # *publisher*, and it cost New Zealand's `checklists/india/…` 40 points for a traveller in
    # Britain when New Zealand publishes no British checklist for it to lose to. Withdrawing on
    # evidence a page's own stored text already carries is the wrong layer; TODO item 31.
    #
    # Only where the two differ. A traveller applying from their own country of nationality is
    # unchanged, because for them the two questions are one question and the nationality bonus above
    # already answers it — doubling it here would just inflate every page about their own country.
    if residence.code != nationality.code and _describes_country(link, residence):
        for post_role in POST_SPECIFIC_ROLES:
            if post_role in scores:
                scores[post_role] += lexicon.residence_weight
                signals[post_role].append(
                    f"residence:{residence.code}+{lexicon.residence_weight:g}"
                )

    # How this traveller applies is set by the mission serving where they live, so it outranks a
    # ministry's general pages for the post-specific roles — and a *different* post's page loses
    # them, because its fees, address and appointment system are not the ones this traveller uses.
    on_mission_host = bool(mission_domains) and host_is_within(
        host_of(link.url), mission_domains or []
    )
    affinity = mission_affinity(link.url, residence, lexicon, other_posts=other_posts)
    if on_mission_host and affinity is None:
        affinity = "own"
    if affinity is not None:
        adjustment = (
            lexicon.mission_host_bonus if affinity == "own" else lexicon.other_mission_penalty
        )
        label_text = "mission" if affinity == "own" else "other-mission"
        for mission_role in POST_SPECIFIC_ROLES:
            if mission_role in scores:
                scores[mission_role] += adjustment
                signals[mission_role].append(f"{label_text}{adjustment:+g}")

    # Authorities routinely publish checklists as PDFs, so a PDF is evidence for that role only.
    if is_pdf_url(link.url) and "document_checklist" in scores:
        scores["document_checklist"] += lexicon.pdf_checklist_bonus
        signals["document_checklist"].append(f"pdf+{lexicon.pdf_checklist_bonus:g}")

    return RoleScores(scores=scores, signals=signals)


def score_body(
    text: str,
    title: str,
    corridor: Corridor,
    lexicon: Lexicon,
    nationality: Country,
    *,
    url: str = "",
    residence: Country | None = None,
    other_posts: frozenset[str] = frozenset(),
) -> RoleScores:
    """Score a page from its own text, confirming or contradicting what the link suggested.

    **With `residence`, the traveller signals follow the rules the link scorer already keeps**
    (DECISIONS entry 245, TODO item 77). Measured on the 27 oracle corridors, the pages every
    smaller shortlist lost ranked first by link and 70th to 110th by stored text, because this
    score credited the wrong things:

    - the nationality bonus read the URL's *host*, so `india.diplo.de` — the German embassy in
      India — read as written for Indians and outscored the UK embassy for an Indian in Britain.
      With `residence` it reads the title and the path only, as `_describes_country` does;
    - nothing credited the post serving the traveller. With `residence` a page whose title or path
      names the country of residence earns `residence_weight` on the post-specific roles, and
      `mission_affinity` adds the own post's bonus or the other post's penalty, as for links;
    - the purpose bonus reached `visa_decision`, which a country table never earns by mentioning
      tourism, so the table lost to any page that did. With `residence` it no longer does.

    Without `residence` nothing changes. Only stored-text scoring passes it (`score_held`); a page
    fetched live is scored as before, which the measurement did not cover.
    """

    post_aware = residence is not None
    haystack = f"{title}\n{text}".lower()
    scores: dict[str, float] = {}
    signals: dict[str, list[str]] = {}

    for role_name in ROLE_ORDER:
        role_terms = lexicon.roles.get(role_name)
        if role_terms is None:
            continue
        # The strongest single phrase, not the sum of every synonym for it. "documents required",
        # "required documents", "application documents" and "necessary documents" all assert the
        # same one thing, and summing them let a page earn 86 points for saying it four ways —
        # which is precisely how a generic "how to apply" page outscored a real checklist. This is
        # the rule the off-scope penalty already follows: what matters is that the signal is
        # present, not how many ways the page phrases it.
        matched = [term for term in role_terms.terms if _contains_phrase(haystack, term.phrase)]
        if matched:
            strongest = max(matched, key=lambda term: term.weight)
            scores[role_name] = strongest.weight
            signals[role_name] = [f"body:{strongest.phrase}+{strongest.weight:g}"]
            if len(matched) > 1:
                signals[role_name].append(f"({len(matched) - 1} more phrasings, not added)")

    # What separates a checklist from a page about checklists: the documents it names. Every
    # phrase in roles.document_checklist is met by talking *about* documents, which is how a
    # generic "how to apply" page outscored a real tourism checklist that says "checklist" nowhere.
    named = names_documents(haystack, lexicon)
    if len(named) >= lexicon.minimum_document_nouns:
        bonus = min(len(named), lexicon.document_noun_cap) * lexicon.document_noun_weight
        scores["document_checklist"] = scores.get("document_checklist", 0.0) + bonus
        signals.setdefault("document_checklist", []).append(
            f"names {len(named)} documents ({', '.join(named[:4])})+{bonus:g}"
        )

    if not scores:
        return RoleScores()

    shared = 0.0
    shared_reasons: list[str] = []
    # In the title or the URL, not anywhere in the text. This is the same discipline the
    # off-scope check below already applies, for the same reason: a passing mention is normal and
    # harmless. Japan's ministry-wide checklist names India once, inside a table of nationality
    # exceptions; that made it read as a page written for Indians and beat the UK post's own
    # tourism checklist. Singapore's genuinely per-nationality page says so in its URL.
    written_for_nationality = _names_nationality(title, url, nationality, post_aware=post_aware)
    if written_for_nationality:
        shared += lexicon.nationality_weight
        shared_reasons.append(f"body-nationality:{nationality.code}+{lexicon.nationality_weight:g}")

    purpose_terms = lexicon.purposes.get(corridor.purpose)
    purpose_weight = (
        purpose_terms.weight
        if purpose_terms and any(_contains_phrase(haystack, t) for t in purpose_terms.terms)
        else 0.0
    )

    # In the body, a wrong-audience word only counts against the page when it is in the title,
    # because a passing mention in a list of visa types is normal and harmless.
    title_lower = title.lower()
    hit = next(
        (
            term
            for term in lexicon.off_scope_terms_for(corridor.purpose)
            if _contains_phrase(title_lower, term)
        ),
        None,
    )
    if hit is not None:
        shared += lexicon.off_scope.weight
        shared_reasons.append(f"title-off-scope:{hit}{lexicon.off_scope.weight:g}")

    for scored_role in list(scores):
        scores[scored_role] += shared
        signals[scored_role].extend(shared_reasons)
        # A country table never names a purpose, so with the post known the bonus stops reaching
        # the decision, where it only rewarded pages that happened to mention tourism (entry 245).
        if purpose_weight and not (post_aware and scored_role == "visa_decision"):
            scores[scored_role] += purpose_weight
            signals[scored_role].append(f"body-purpose:{corridor.purpose}+{purpose_weight:g}")

    # A page that scores on many roles is usually a directory, not an answer, so breadth is
    # dampened. The exception is a page written for this nationality: Singapore's per-nationality
    # page genuinely covers the decision, the documents, the fee and the timing, and penalising it
    # for being comprehensive hands the checklist role to a narrower, wrong page.
    breadth = len([role for role, value in scores.items() if value > 0])
    if breadth > lexicon.breadth_threshold and not written_for_nationality:
        factor = (lexicon.breadth_threshold / breadth) ** 0.5
        for scored_role in list(scores):
            scores[scored_role] *= factor
            signals[scored_role].append(f"breadth:{breadth}x{factor:.2f}")

    if residence is not None:
        _credit_the_post(scores, signals, url, title, lexicon, nationality, residence, other_posts)
    return RoleScores(scores=scores, signals=signals)


def _names_nationality(title: str, url: str, nationality: Country, *, post_aware: bool) -> bool:
    """Whether a page's title or address says it is written for this nationality, on whole words.

    It used to be a substring test over the title and the flattened URL, so the United States'
    token "us" matched "a**us**tralia", "b**us**iness" and "about-us": for an American going to
    Australia, Peru's "Passports for Australians" and Sri Lanka's job vacancies read as written for
    Americans and took +40 each, above Home Affairs' own ETA page (entry 266). `_contains_word`
    exists for exactly this, and the link scorer already uses it.

    A two-letter token is also an English word ("us", "in"), so it counts in a title only in
    capitals ("Visas for US citizens", not "Contact us"), and in a path only as a whole segment
    (`/us/`, not `/about-us/`). Longer tokens match as whole words, or as a run of a segment's
    hyphenated words ("united-states").
    """

    short = [token for token in nationality.text_tokens if len(token) <= 2]
    long = [token for token in nationality.text_tokens if len(token) > 2]
    if any(_contains_word(title, token) for token in long):
        return True
    if any(re.search(rf"\b{re.escape(token.upper())}\b", title) for token in short):
        return True
    segments = [segment.split(".")[0] for segment in path_segments(url)]
    if not post_aware:
        segments += host_of(url).split(".")
    for segment in segments:
        if any(segment == token for token in short):
            return True
        if any(_segment_names(segment, token) for token in long):
            return True
    return False


def _path_of(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def _credit_the_post(
    scores: dict[str, float],
    signals: dict[str, list[str]],
    url: str,
    title: str,
    lexicon: Lexicon,
    nationality: Country,
    residence: Country,
    other_posts: frozenset[str],
) -> None:
    """The link scorer's two post signals, applied to a page scored by its own text (entry 245).

    After the breadth dampening, as the link scorer adds them after its own: they say which post a
    page belongs to, which is not something a directory has more of.
    """

    if residence.code != nationality.code:
        about = PageLink(url=url, text=title[:300], heading="", depth=0, discovered_from="")
        if _describes_country(about, residence):
            for role in POST_SPECIFIC_ROLES:
                if role in scores:
                    scores[role] += lexicon.residence_weight
                    signals[role].append(
                        f"body-residence:{residence.code}+{lexicon.residence_weight:g}"
                    )
    affinity = mission_affinity(url, residence, lexicon, other_posts=other_posts)
    if affinity is not None:
        adjustment = (
            lexicon.mission_host_bonus if affinity == "own" else lexicon.other_mission_penalty
        )
        label = "body-mission" if affinity == "own" else "body-other-mission"
        for role in POST_SPECIFIC_ROLES:
            if role in scores:
                scores[role] += adjustment
                signals[role].append(f"{label}{adjustment:+g}")


def rank_for_role(
    candidates: list[CandidatePage], role: DiscoveryRole
) -> list[tuple[CandidatePage, float]]:
    """Candidates for one role, best first, with ties broken by URL for determinism."""

    scored = [(candidate, candidate.combined(role)) for candidate in candidates]
    positive = [(candidate, score) for candidate, score in scored if score > 0]
    positive.sort(key=lambda pair: (-pair[1], pair[0].link.url))
    return positive
