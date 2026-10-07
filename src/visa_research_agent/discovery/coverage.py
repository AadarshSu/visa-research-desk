"""Whether a country's stored corpus is good enough to serve a corridor, measured offline.

This is the gate TODO item 37 asks for, and the reason it is two halves rather than one number is
the whole of why it exists.

**The obvious version of this measurement returns 100% and says nothing.** Asked of
`oracle/selection_oracle.yaml` on 2026-08-28, *"is the page that answers each role already in the
corpus"* comes back **47 of 47**. That is true and nearly useless: every corridor in that oracle is
`IN/GB/tourism`, and the Netherlands' three answers were held both before and after entry 88's
rebuild — so the fix that measurement should have detected improved Philippine, Pakistani and
Chinese residents by a great deal and `IN/GB` by nothing. A gate that reproduces the 100% is not a
gate.

So half one is kept and demoted to what it actually is — a **regression** check that should stay at
100% — and half two measures the dimension that varies. They are reported separately and never
added together, which is the discipline `audit.py` exists to keep: a single number covering both
would hide which half failed.

**Half two is the per-traveller family.** An authority that publishes one page per traveller —
`…/schengen-visa/apply-{country}` — has a coverage dimension a single-traveller oracle cannot see.
For each such family the report says how many members are held, how many were ever opened, whether
opening one leads anywhere, and how much of it the text index can read. Entry 88 is why: a corpus
build opens 3 to 15% of what it records, and the page answering a *specific* traveller is usually
one hop below something recorded and never opened.

**Nothing here fetches, searches, or calls a model, and that is a requirement rather than a
convenience.** Entry 81 measured "roles filled" swinging by two on identical input, and entries 79
to 81 are three consecutive entries that were wrong because they leaned on it. This command grades
the *store*; whether a corridor then finds what the store holds is `selection-recall`'s question and
the two must not be merged.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from visa_research_agent.discovery.corpus import CountryCorpus, canonical_key
from visa_research_agent.discovery.corpus_build import CORPUS_FAMILY_PATTERN
from visa_research_agent.discovery.crawl import DEFAULT_FAMILY_MINIMUM
from visa_research_agent.discovery.models import ROLE_ORDER
from visa_research_agent.discovery.selection_recall import SelectionOracle
from visa_research_agent.discovery.urls import country_family_keys

# What share of the world's countries a family must cover before the corpus is credited with
# holding it. Below this the members that are missing are missing because **nothing the crawl saw
# linked them**, which is entry 82's form wall: the United Kingdom publishes its per-nationality fee
# tables behind a country selector, and no crawl budget crosses a selector.
#
# Measured over the ten corpora before it was chosen, so it can be seen not to be delicately
# placed. Every qualifying family sits at either 91-95% or 7-16% of the world and none sits between:
#
#     NL  making-appointment/{}                188/198   95%
#     NL  visa-the-netherlands/…/apply-{}      184/198   93%
#     NL  consular-fees/{}                     180/198   91%
#     SG  …/visa_requirements/…/{}              32/198   16%
#     SG  …/travel-advisories-…/{}              15/198    8%
#     GB  visa-fees.homeoffice.gov.uk/y?…={}    14/198    7%
FAMILY_COMPLETE_SHARE = 0.5

# Below this share of a complete gateway family **read**, a rebuild with the family reservation
# still has coverage to buy. Not a quality bar and not tuned: it is the point past which "most of
# it" is a fair description.
#
# Measured against `read` rather than `opened` since entry 101. On `opened` the Netherlands'
# schengen family scored 39% and the gate said a rebuild would buy coverage; every one of its 184
# grouped members had already been fetched, and the rebuild run to check that bought 27 entries
# out of 2,965 pages crawled.
FAMILY_OPENED_SHARE = 0.75

FamilyShape = Literal["gateway", "leaf", "unopened"]

Verdict = Literal[
    "ungraded", "no per-traveller dimension", "covered", "incomplete", "bounded by the authority"
]

VERDICT_MEANING: dict[str, str] = {
    "ungraded": (
        "no per-traveller family and no oracle row — nothing here has graded this country, "
        "and reading it as a pass is the mistake this verdict exists to prevent"
    ),
    "no per-traveller dimension": (
        "the guidance is centralised, so the known-answer half settles this country"
    ),
    "covered": "the families exist and the corpus holds and has walked them",
    "incomplete": (
        "a gateway family is held and mostly unread; only a crawl seeded from those "
        "addresses reaches them — a plain rebuild re-walks its search seeds (entry 101)"
    ),
    "bounded by the authority": (
        "the family exists and cannot be crawled — the rest is behind a selector"
    ),
}


@dataclass(frozen=True)
class KnownAnswer:
    """Half one, for one oracle corridor: is the page a human named already in the corpus?

    The regression half. It should stay at 47 of 47 and it can only ever speak for the one traveller
    the oracle covers — known problem 33.
    """

    corridor: str
    held: int
    answerable: int
    """How many of this corridor's six roles a human could name **any** answer for.

    The number that actually moved when the oracle gained a second traveller, and the reason half
    one is reported as two figures rather than one. `held/answerable` came back 100% for both
    travellers; `answerable` itself is 47 of 60 for `IN/GB` and 24 of 60 for `PH/PH`. So the corpus
    holds what can be answered, and far less can be answered for the second traveller — which is
    invisible in a percentage whose denominator is itself the finding."""

    missing: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """Role to the URLs the oracle named for it, where the corpus holds none of them."""

    roles: int = len([role for role in ROLE_ORDER if role != "travel_authorisation"])
    """Every role the oracle curates, so `answerable` has a denominator that does not move. Not
    `travel_authorisation` (entry 275): no oracle row names one, and most corridors have none to
    find, so counting it would read as an open role everywhere."""

    @property
    def traveller(self) -> str:
        """`PH/PH/tourism` — the corridor without its destination, which is what a row is grouped
        by now that a country carries more than one."""

        return "/".join(self.corridor.split("/")[1:])

    not_applicable: tuple[str, ...] = ()
    """Roles that do not arise for this traveller, because the decision is that no visa is needed.

    Its own column, never a gap. Singapore's Philippine row read **two of six** while resolving
    perfectly: a Filipino needs no visa, so there is no application, no checklist to bring to one,
    no fee and nothing to process. Calling those four `unanswered` said the store had failed to find
    four pages, when what it had found was that there is nothing to find.

    Distinct from `settled` as well as from a gap. A tool-settled role is a real question the
    traveller must still go and resolve; this is a question that never arises."""

    settled: tuple[str, ...] = ()
    """Roles no page states and an official questionnaire settles, per entries 59 and 60.

    **Counted, and counted apart.** This column was missing until 2026-08-28 and its absence made
    the metric say the opposite of what the product does. `audit.py` has always put
    `resolved_decision_tool` in the *resolved* group — "the authority publishes it only as a tool",
    no cost to our posture — because handing a traveller the authority's own mechanism is an
    outcome, not a gap. Entry 60's heading is "a questionnaire **is** an answer"; what it forbids
    is filling the role's *content* from one, which is a different claim and still holds: a
    tool-settled `document_checklist` still means `application_document_source_ids` stays empty and
    not one requirement may be listed.

    So it is never added into `held` or `answerable`. France reads two roles answered and three
    settled: a traveller gets an authoritative path for five of six, and this project may still not
    write down what the Wizard would say."""

    aliased: tuple[str, ...] = ()
    """Roles held only under a different host or scheme — `www.gdrfad.gov.ae` against
    `gdrfad.gov.ae`. Not a miss, and reported because the first run of this measurement counted one
    as a miss and read 46 of 47. A coverage number that treats those as different pages is wrong in
    the direction that looks like a finding."""


@dataclass(frozen=True)
class Family:
    """One per-traveller family the corpus holds, and how far into it a crawl has got."""

    key: str
    """The shared address with the country blanked out: `…/schengen-visa/apply-{}`."""

    countries: int
    """How many countries exist, which is the size this family would have if it were complete."""

    held: int
    """Members the corpus knows the address of. **A floor, not a count of the family.** A member
    whose country token the registry does not recognise is not grouped: Singapore's `visa-detail-
    page/` holds 34 pages and 32 of them group, because `kosovo` and `democratic-people's-republic-
    of-korea` are not slugs. That is the crawler's limitation too — `_queue` groups the same way —
    so the report describes the family the reservation would actually spend budget on."""

    opened: int
    children: int
    """Distinct pages discovered from an opened member. Attributed to the *first* page that led to
    one, because `merge` keeps the shallowest `discovered_from`, so a leaf several members link is
    counted once. That undercounts, and it undercounts a gateway more than a leaf — which is the
    safe direction here, since it can only make a gateway look less productive than it is."""

    country_named_children: int
    """Of those, how many are themselves about one country. **This is what tells a gateway from a
    leaf**, and a plain child count does not: opening two Singaporean members yields three children
    and opening the Dutch equivalent yields 2.4 apiece, which are not far enough apart to divide on.
    Asking whether the child is *per traveller* separates them completely — the Netherlands' 169 are
    168 country-named and Singapore's three are none, because a Singaporean member **is** the
    answer and its children are a landing page, an advisory and a user manual."""

    reservable: bool
    """Whether `LinkCrawler`'s reservation could ever spend budget on this family.

    **The one place this module deliberately does not group the way the crawler does.** `_queue`
    groups the links found on *one page*, because a per-traveller family is a list an authority
    published in one place and siblings on unrelated pages are a coincidence. This groups across the
    whole corpus, because the question here is what the authority publishes rather than what a crawl
    can act on — and the difference is a finding rather than a discrepancy.

    Measured 2026-08-28: per-page grouping gives **NL 9, SG 1, zero for CA, JP and GB**, reproducing
    entry 88's counts exactly. Corpus-wide gives **NL 13, SG 2, GB 1**. The extra Dutch four are the
    `checklist-schengen-visa-…/{}` leaves, which exist one per gateway and are listed nowhere
    together; the extra British one is `visa-fees.homeoffice.gov.uk/y?previous-answer={}`, entry
    82's fee wall showing up as a family for the first time. A report that grouped per page would
    have said the United Kingdom has no per-traveller dimension, which is false."""

    text_held: int
    """How many members the page-text index holds a body for. The model selector reads stored text,
    so a family the corpus holds and the index cannot read is chosen from blind — Singapore holds 32
    members and text for **four** of them."""

    @property
    def completeness(self) -> float:
        return self.held / self.countries if self.countries else 0.0

    @property
    def opened_share(self) -> float:
        return self.opened / self.held if self.held else 0.0

    @property
    def read(self) -> int:
        """Members the crawl actually **fetched**, which is not the same as `opened`.

        `opened` counts a member some other entry names as its `discovered_from` — a member that
        *fathered a recorded link*. A member that was fetched and linked nothing therefore reads as
        never fetched, and on the Netherlands that gap is the whole finding: the schengen family
        counts **72 opened against 185 fetched**, and the missing 113 are the pages that link
        nothing because for most residences the checklist is on VFS Global (entry 89). The gate was
        reporting a documented ceiling as a crawl gap and advising a rebuild that cannot help — and
        a rebuild was run to check, crawling 2,965 pages for 27 new entries and no change of
        verdict (entry 101).

        The maximum of the two signals, because neither is sound alone: the index misses a page
        fetched with no readable body, and `discovered_from` misses a page that led nowhere. A
        country with no text index falls back to `opened` and behaves exactly as before.
        """

        return max(self.opened, self.text_held)

    @property
    def read_share(self) -> float:
        return self.read / self.held if self.held else 0.0

    @property
    def shape(self) -> FamilyShape:
        """Gateway, leaf, or not yet known — detected by counting children, never guessed.

        An unopened family is honestly `unopened`: nothing distinguishes a gateway from a leaf
        before one member is read, which is the same fact that forces `FamilyQueues` to give every
        family its turn rather than back a winner.

        **The gateway test stays on `opened`, deliberately.** It asks how many country-named
        children an opened member yields, so both sides of it have to be counted the same way.
        Swapping in `read` would divide the same children by 2.6× the members on the Netherlands
        and flip its schengen family — a gateway by any reading — to `leaf`. What `read` is for is
        the verdict: whether a rebuild has anything left to fetch.
        """

        if not self.read:
            return "unopened"
        if not self.opened:
            return "leaf"
        return "gateway" if self.country_named_children >= self.opened else "leaf"

    @property
    def is_complete(self) -> bool:
        return self.completeness >= FAMILY_COMPLETE_SHARE


@dataclass(frozen=True)
class CountryCoverage:
    """One country's answer to "can a corridor into it be served from the store"."""

    code: str
    entries: int
    pages_opened: int
    """Distinct pages the crawl actually read, from `discovered_from`. Entry 88's 3 to 15%."""

    delegations: int
    """Pages of this country's guidance published on a contractor (entry 89). Reported because it
    bounds what any rebuild can buy: for most residences the Netherlands' checklist is on VFS
    Global, so opening every gateway it holds still reaches no checklist for them."""

    families: tuple[Family, ...]
    known: tuple[KnownAnswer, ...]

    @property
    def verdict(self) -> Verdict:
        """Which of five things is true of this country, from the families alone.

        Half one's *content* deliberately does not enter this. It covers one traveller, so letting
        it vote would let a 100% that means "fine for `IN/GB`" outvote a family measurement that
        means "unserved for the other 197 residences" — which is the exact failure this module
        exists to avoid.

        **Its presence does, and only ever to withhold a verdict.** `no per-traveller dimension`
        is not a measurement — it is a *deferral*, and its sentence says so: "the known-answer half
        settles this country". Measured 2026-09-01, **42 of the 53 built countries** were deferring
        to a half with no rows in it at all, and the report read exactly like 42 passes. The other
        three verdicts stand on the families alone and are untouched; a country outside the oracle
        with a family is still graded normally, which is why Portugal reads `bounded by the
        authority` rather than `ungraded`. Nothing here can turn a fail into a pass — only a pass
        that was never earned into an admission that nobody looked. TODO item 43.
        """

        if not self.families:
            return "no per-traveller dimension" if self.known else "ungraded"
        complete = [family for family in self.families if family.is_complete]
        if not complete:
            return "bounded by the authority"
        if any(
            family.shape in ("gateway", "unopened") and family.read_share < FAMILY_OPENED_SHARE
            for family in complete
        ):
            return "incomplete"
        return "covered"


@dataclass(frozen=True)
class CoverageReport:
    countries: tuple[CountryCoverage, ...]
    unbuilt: tuple[str, ...] = ()
    """Countries asked about that have no corpus at all. Not a failure of coverage — a job nobody
    has run — and kept apart from the verdicts for the reason `audit.py` keeps its two halves
    apart."""


def known_answers(oracle: SelectionOracle, corpus: CountryCorpus, slug: str) -> list[KnownAnswer]:
    """Half one for one country: how many of the oracle's named answers the corpus already holds.

    **One row per curated traveller, not one per country**, which it was until the oracle had a
    second traveller in it. Taking the first corridor for a slug would have reported the Netherlands
    on `IN/GB` alone and silently dropped the `PH/PH` row — reproducing, inside the gate built to
    detect it, exactly the blindness known problem 33 describes.

    Compared on `canonical_key`, which folds scheme, case and a leading `www.` — never on the raw
    string. `www.gdrfad.gov.ae/en/services/727c…` and `gdrfad.gov.ae/en/services/727c…` are one
    page, and the first run of this reported 46 of 47 by treating them as two.
    """

    keys = {canonical_key(entry.url) for entry in corpus.entries}
    exact = {entry.url for entry in corpus.entries}
    rows: list[KnownAnswer] = []
    for corridor in oracle.corridors:
        # A corridor with **no** answers is kept, not skipped. France and Sweden answer none of
        # their six roles for the Philippine traveller, and dropping them took `answerable` from
        # 24 of 60 to 24 of 48 — a denominator error that flatters exactly the traveller the second
        # half of this fixture exists to be honest about.
        if corridor.slug != slug:
            continue
        held = 0
        missing: dict[str, tuple[str, ...]] = {}
        aliased: list[str] = []
        for role, pages in corridor.answers.items():
            urls = tuple(page.url for page in pages)
            if not any(canonical_key(url) in keys for url in urls):
                missing[role] = urls
                continue
            held += 1
            if not any(url in exact for url in urls):
                aliased.append(role)
        rows.append(
            KnownAnswer(
                corridor=corridor.corridor,
                held=held,
                answerable=len(corridor.answers),
                missing=missing,
                # A role a page answers is answered, whatever else names it. Only a role with no
                # page answer is credited to its tool, so the two columns can never double-count.
                settled=tuple(role for role in corridor.tools if role not in corridor.answers),
                not_applicable=tuple(corridor.not_applicable),
                aliased=tuple(aliased),
            )
        )
    return rows


def families_in(
    corpus: CountryCorpus,
    slugs: frozenset[str],
    *,
    countries: int,
    minimum: int = DEFAULT_FAMILY_MINIMUM,
    pattern: re.Pattern[str] = CORPUS_FAMILY_PATTERN,
    indexed: Callable[[Sequence[str]], set[str]] | None = None,
) -> list[Family]:
    """Every per-traveller family the corpus holds, largest first.

    Same `country_family_keys` as `LinkCrawler._queue`, same `CORPUS_FAMILY_PATTERN`, same minimum,
    and the same rule that a URL belonging to two families is counted under the larger one — so a
    family reported here is one the crawl's reservation understands. The single difference is that
    `_queue` groups the links found on **one page** and this groups across the whole corpus, which
    is what `reservable` records; see that field for what it costs and what it buys.

    A destination is named in its own addresses — `…/visa-the-netherlands/schengen-visa/apply-india`
    carries two country tokens — so every key each address could carry is considered and the largest
    grouping wins. Taking the first found no Dutch family at all (entry 88).
    """

    children: dict[str, list[str]] = defaultdict(list)
    siblings: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for entry in corpus.entries:
        if entry.discovered_from:
            children[canonical_key(entry.discovered_from)].append(entry.url)

    grouped: dict[str, list[str]] = defaultdict(list)
    for entry in corpus.entries:
        for key in country_family_keys(entry.url, slugs):
            if not pattern.search(key):
                continue
            grouped[key].append(entry.url)
            if entry.discovered_from:
                siblings[key][canonical_key(entry.discovered_from)].add(entry.url)

    best: dict[str, tuple[int, str]] = {}
    for key, members in grouped.items():
        if len(members) < minimum:
            continue
        for url in members:
            if best.get(url, (0, ""))[0] < len(members):
                best[url] = (len(members), key)

    by_key: dict[str, list[str]] = defaultdict(list)
    for url, (_, key) in best.items():
        by_key[key].append(url)

    families: list[Family] = []
    for key, members in by_key.items():
        opened = [url for url in members if canonical_key(url) in children]
        found = {child for url in opened for child in children[canonical_key(url)]}
        families.append(
            Family(
                key=key,
                countries=countries,
                held=len(members),
                opened=len(opened),
                children=len(found),
                country_named_children=sum(
                    1 for child in found if country_family_keys(child, slugs)
                ),
                reservable=any(len(found) >= minimum for found in siblings.get(key, {}).values()),
                text_held=len(indexed(sorted(members))) if indexed is not None else 0,
            )
        )
    return sorted(families, key=lambda family: (-family.held, family.key))


def coverage(
    corpus: CountryCorpus,
    oracle: SelectionOracle,
    *,
    slug: str,
    slugs: frozenset[str],
    countries: int,
    indexed: Callable[[Sequence[str]], set[str]] | None = None,
) -> CountryCoverage:
    """Both halves for one country, from the store alone."""

    return CountryCoverage(
        code=corpus.country_code,
        entries=len(corpus.entries),
        pages_opened=len(
            {canonical_key(e.discovered_from) for e in corpus.entries if e.discovered_from}
        ),
        delegations=len(corpus.delegations),
        families=tuple(families_in(corpus, slugs, countries=countries, indexed=indexed)),
        known=tuple(known_answers(oracle, corpus, slug)),
    )


def report(rows: Iterable[CountryCoverage], unbuilt: Iterable[str] = ()) -> CoverageReport:
    return CoverageReport(
        countries=tuple(sorted(rows, key=lambda row: row.code)), unbuilt=tuple(sorted(unbuilt))
    )
