"""What identifies a document checklist, and which post serves this traveller.

These pin the two signals added after Brazil — the first corridor that tested discovery's ranking
out of sample, and failed it. Both cases below are taken from the real pages:

  * a generic "how to apply on e-consular" page in Riyadh repeated "documents required" and
    "required documents" and named no document at all, and it beat Edinburgh's real tourism
    checklist, which names a passport, a bank statement and an itinerary and never says
    "checklist";
  * Brazil publishes every mission under one host with the post in the path, so a host-based
    check could not tell `consulado-edimburgo` from `embaixada-riade`, and four of six resolved
    roles came from missions on the wrong continent.
"""

import re

from visa_research_agent.discovery.lexicon import get_country_registry, get_lexicon
from visa_research_agent.discovery.models import Corridor, DiscoveryRole, PageLink, RoleScores
from visa_research_agent.discovery.scoring import (
    POST_SPECIFIC_ROLES,
    _matches_country,
    foreign_post_labels,
    is_archived,
    is_boilerplate,
    mission_affinity,
    mission_in_path,
    names_documents,
    score_body,
    score_link,
    wrong_country,
)
from visa_research_agent.discovery.urls import published_date_in_path
from visa_research_agent.domain.models import TravelPurpose

BR = "https://www.gov.br/mre/pt-br"
VIVIS = "visa-section/types-of-visa/visit-visa-vivis-1/tourism-and-transit-vivis"
EDINBURGH = f"{BR}/consulado-edimburgo/{VIVIS}"
RIYADH = f"{BR}/embaixada-riade/how-to-apply-for-services-on-e-consular"
NEW_DELHI_AU = "https://india.embassy.gov.au/ndli/visas_and_migration.html"
LONDON_AU = "https://uk.embassy.gov.au/lhlh/visas_and_migration.html"

# Shortened from the real pages, keeping what each one does with the word "document".
CHECKLIST_TEXT = (
    "Tourism and Transit (VIVIS). Applicants must upload a passport valid for at least six "
    "months, a recent passport photo, the completed application form, a bank statement for the "
    "last three months as proof of funds, a travel itinerary and a return ticket."
)
ABOUT_DOCUMENTS_TEXT = (
    "How to apply for services on e-consular. Select the service, then upload the documents "
    "required for your application. The required documents vary by service. Applications with "
    "missing application documents will be returned. Check the necessary documents before you "
    "submit and remember that documents required for tourism differ from other purposes."
)


def corridor() -> Corridor:
    return Corridor(
        destination_slug="brazil",
        passport_nationality="IN",
        applying_from="GB",
        purpose="tourism",
    )


def link_at(url: str, text: str = "") -> PageLink:
    return PageLink(url=url, text=text, heading="", depth=0, discovered_from="seed")


def checklist_body(text: str, title: str) -> float:
    registry = get_country_registry()
    scores = score_body(text, title, corridor(), get_lexicon(), registry.require("IN"))
    return scores.score_for("document_checklist")


def checklist_link(url: str, text: str = "") -> float:
    registry = get_country_registry()
    scores = score_link(
        link_at(url, text),
        corridor(),
        get_lexicon(),
        registry.require("IN"),
        registry.require("GB"),
    )
    return scores.score_for("document_checklist")


# --- naming a post from the path ------------------------------------------------------------


def test_a_post_is_read_from_the_path_when_the_host_cannot_distinguish_it() -> None:
    lexicon = get_lexicon()

    assert mission_in_path(EDINBURGH, lexicon) == "edimburgo"
    assert mission_in_path(RIYADH, lexicon) == "riade"


def test_a_path_naming_no_post_returns_nothing() -> None:
    lexicon = get_lexicon()

    assert mission_in_path("https://www.gov.br/mre/pt-br/assuntos/portal-consular", lexicon) is None
    # A bare section index names no particular post, so it must not be read as one.
    assert mission_in_path("https://example.gov.br/embassy/index.html", lexicon) is None


def test_the_post_serving_the_traveller_is_recognised_in_a_foreign_language() -> None:
    """Brazil writes the UK posts in Portuguese, so the labels have to be data, not code."""

    lexicon, gb = get_lexicon(), get_country_registry().require("GB")

    assert mission_affinity(EDINBURGH, gb, lexicon) == "own"
    assert mission_affinity(f"{BR}/consulado-londres/visas", gb, lexicon) == "own"


def test_another_post_is_recognised_as_another_post() -> None:
    lexicon, gb = get_lexicon(), get_country_registry().require("GB")

    assert mission_affinity(RIYADH, gb, lexicon) == "other"
    assert mission_affinity(f"{BR}/embaixada-kuala-lumpur/eng/visit-visa", gb, lexicon) == "other"
    assert mission_affinity(f"{BR}/consulado-atlanta/visas", gb, lexicon) == "other"


def test_a_page_belonging_to_no_post_is_neither() -> None:
    # Most ministry pages belong to no mission, and must not be penalised as though they did.
    lexicon, gb = get_lexicon(), get_country_registry().require("GB")

    assert mission_affinity("https://www.ica.gov.sg/enter-transit-depart", gb, lexicon) is None
    assert mission_affinity(f"{BR}/mre/pt-br/assuntos/portal-consular", gb, lexicon) is None


def test_a_post_named_by_city_in_the_host_is_recognised() -> None:
    """Singapore's London high commission is named by city, so a country-code check missed it."""

    lexicon, gb = get_lexicon(), get_country_registry().require("GB")

    assert mission_affinity("https://london.mfa.gov.sg/visa", gb, lexicon) == "own"


def test_a_post_named_by_country_in_the_host_is_another_post() -> None:
    """`india.embassy.gov.au` is the New Delhi post, and for a traveller in Great Britain it is
    somebody else's — but until 2026-08-25 it scored as belonging to no post at all, because
    "other" could only ever be concluded from a path. DECISIONS entry 72.
    """

    lexicon, registry = get_lexicon(), get_country_registry()
    gb = registry.require("GB")
    posts = foreign_post_labels(registry, "AU", gb)

    assert mission_affinity(NEW_DELHI_AU, gb, lexicon, other_posts=posts) == "other"
    assert mission_affinity(LONDON_AU, gb, lexicon, other_posts=posts) == "own"


def test_a_governments_own_hostname_is_never_another_post() -> None:
    """The safety of the rule, and the half that measurement found rather than reasoning.

    `cz` is one of Czechia's own mission labels, so without exempting the destination every page
    on `mzv.gov.cz` read as another post for a traveller in Great Britain — 146 pages that had
    correctly filled a role were penalised for sitting on their own government's hostname.
    A country's code inside its own public suffix must never mean somebody else's mission.
    """

    lexicon, registry = get_lexicon(), get_country_registry()
    gb = registry.require("GB")
    posts = foreign_post_labels(registry, "CZ", gb)

    ministry = "https://mzv.gov.cz/jnp/en/information_for_aliens/short_stay_visa/index.html"
    assert mission_affinity(ministry, gb, lexicon, other_posts=posts) is None
    assert "cz" not in posts

    portugal = foreign_post_labels(registry, "PT", gb)
    fees = "https://vistos.mne.gov.pt/en/short-stay-visas-schengen/general-information/visa-fees"
    assert mission_affinity(fees, gb, lexicon, other_posts=portugal) is None
    # The same host with a real post in front of it is still caught.
    delhi = "https://novadeli.embaixadaportugal.mne.gov.pt/en/consular-section/visas"
    assert mission_affinity(delhi, gb, lexicon, other_posts=portugal) == "other"


def test_the_post_governs_fees_and_processing_times_but_never_the_visa_decision() -> None:
    """Which roles a different post loses points for, and the two it deliberately does not.

    A fee is quoted in the post's own currency and a processing time is that post's queue, so a
    corridor taking Brazil's Edinburgh fee page for a traveller in the United States is wrong.
    Whether a passport needs a visa is set by the destination's law and is identical at every
    consulate, so `visa_decision` is left alone — demoting the only page that states it would
    refuse corridors to buy nothing. DECISIONS entry 72.
    """

    assert POST_SPECIFIC_ROLES == (
        "document_checklist",
        "application_route",
        "fees",
        "processing_times",
    )
    assert "visa_decision" not in POST_SPECIFIC_ROLES
    assert "general_entry" not in POST_SPECIFIC_ROLES


# --- naming documents rather than talking about them ----------------------------------------


def test_a_checklist_is_recognised_by_the_documents_it_names() -> None:
    named = names_documents(CHECKLIST_TEXT.lower(), get_lexicon())

    assert {"passport", "bank statement", "itinerary", "return ticket"} <= set(named)


def test_a_page_about_documents_names_none_of_them() -> None:
    assert names_documents(ABOUT_DOCUMENTS_TEXT.lower(), get_lexicon()) == []


def test_the_real_checklist_outscores_the_page_that_only_talks_about_documents() -> None:
    """The Brazil regression, in one assertion.

    The Riyadh page repeats four separate checklist phrases and still must lose to a page that
    names actual documents and never says "checklist".
    """

    real = checklist_body(CHECKLIST_TEXT, "Tourism and Transit (VIVIS)")
    about = checklist_body(ABOUT_DOCUMENTS_TEXT, "How to apply for services on e-consular")

    assert real > about, f"checklist {real} did not beat the page about checklists {about}"


def test_one_document_in_passing_does_not_make_a_page_a_checklist() -> None:
    # Nearly every page on a visa site mentions a passport once.
    assert checklist_body("You will need a valid passport to enter.", "Entry") == 0.0


# --- the post decides the two roles it governs ----------------------------------------------


def test_another_posts_page_loses_the_roles_that_post_governs() -> None:
    own = checklist_link(EDINBURGH, "Tourism and Transit")
    other = checklist_link(
        f"{BR}/embaixada-riade/visa-section/types-of-visa/tourism-and-transit-vivis",
        "Tourism and Transit",
    )

    assert own > other, "the traveller's own post must outrank an identical page at another post"


def test_a_ministry_page_is_not_penalised_for_belonging_to_no_post() -> None:
    """Singapore and Japan resolve from ministry pages, and must not be caught by this."""

    registry, lexicon = get_country_registry(), get_lexicon()
    scores = score_link(
        link_at("https://www.ica.gov.sg/enter-transit-depart/entering-singapore/visa_requirements"),
        Corridor(
            destination_slug="singapore",
            passport_nationality="IN",
            applying_from="GB",
            purpose="tourism",
        ),
        lexicon,
        registry.require("IN"),
        registry.require("GB"),
    )

    assert not any(
        "other-mission" in signal for signals in scores.signals.values() for signal in signals
    )


# --- publication dates in CMS paths ---------------------------------------------------------


def test_a_cms_date_in_the_path_is_read_rather_than_guessed_at() -> None:
    """Widened from the old rule, which only saw a bare four-digit year segment.

    China's UK embassy serves its current checklist from `/201303/t20130315_...`, so the date was
    invisible to `is_archived` and nothing reported it.
    """

    china = "https://gb.china-embassy.gov.cn/eng/visa/qzxz/201303/t20130315_3383966.htm"
    fees = "https://manchester.china-consulate.gov.cn/eng/visa/visa/202408/t20240802_11465159.htm"

    assert published_date_in_path(china) == "2013-03"
    assert published_date_in_path(fees) == "2024-08"


def test_a_path_with_no_date_reports_none() -> None:
    for url in (
        "https://www.uk.emb-japan.go.jp/itpr_en/sightseeing.html",
        "https://www.gov.br/mre/pt-br/consulado-edimburgo/visa-section",
        "https://x.gov.example/visa/100000/page.htm",
        "https://x.gov.example/visa/201399/page.htm",
    ):
        assert published_date_in_path(url) is None, url


def test_a_publication_date_does_not_archive_a_page() -> None:
    """The load-bearing distinction: publication is not staleness.

    Both of China's correct picks carry dated paths, and one is from 2013. Treating a dated path as
    an archive marker would have discarded the only two roles that corridor resolved.
    """

    lexicon = get_lexicon()
    china = "https://gb.china-embassy.gov.cn/eng/visa/qzxz/201303/t20130315_3383966.htm"

    assert published_date_in_path(china) is not None
    assert not is_archived(china, lexicon)


def test_an_explicit_archive_section_is_still_vetoed() -> None:
    # Widening detection must not have loosened the veto that already worked.
    lexicon = get_lexicon()

    assert is_archived("https://immigration.gov.example/visa/2019/tourist-checklist.html", lexicon)
    assert is_archived("https://immigration.gov.example/visa/archive/checklist.html", lexicon)


def test_a_year_under_an_uploads_folder_is_an_upload_date_not_an_archive() -> None:
    """TODO item 70. Malta's current list of nationalities that need a visa is filed at
    `wp-content/uploads/2023/10/`, and the bare-year veto kept it out of every build and every
    search. Under `uploads` the year is when the file was uploaded, so it is reported instead."""

    lexicon = get_lexicon()
    malta = (
        "https://identita.gov.mt/wp-content/uploads/2023/10/"
        "List-of-Third-Countries-whose-nationals-must-be-in-possession-of-a-visa.pdf"
    )

    assert not is_archived(malta, lexicon)
    assert published_date_in_path(malta) == "2023-10"
    assert is_archived("https://immigration.gov.example/news/2019/visa-rules.html", lexicon)
    assert is_archived("https://immigration.gov.example/archive/uploads/2023/10/a.pdf", lexicon)


def test_the_other_filing_forms_are_dates_not_archives() -> None:
    """Entry 203. The 2026-09-25 rebuild's reports showed the year veto still discarding current
    guidance filed in three forms Malta's fix did not cover."""

    lexicon = get_lexicon()
    filed = {
        # WordPress multisite, South Africa's Juba mission
        "https://dirco.gov.za/juba/wp-content/uploads/sites/57/2024/07/BI-84-Application.pdf": (
            "2024-07"
        ),
        # a multisite media folder, Cyprus
        "https://www.gov.cy/media/sites/19/2024/06/Visa-Information-System-VIS.pdf": "2024-06",
        # and the same site's plain media folder
        "https://www.gov.cy/media/2024/06/Annex-II-Guidelines.pdf": "2024-06",
        # Croatia's CMS
        "https://mvep.gov.hr/UserDocsImages/2023/datoteke/Kenya_supporting_documents.pdf": "2023",
        # a post's permalink
        "https://dirco.gov.za/juba/2024/07/12/application-for-a-business-visa": "2024-07-12",
    }
    for url, date in filed.items():
        assert not is_archived(url, lexicon), url
        assert published_date_in_path(url) == date, url


def test_a_filing_date_spares_only_its_own_year() -> None:
    lexicon = get_lexicon()

    # a day's listing, not a post
    assert is_archived("http://phitsanulok.immigration.go.th/2024/05/01", lexicon)
    # year and month with no day is not a permalink
    assert is_archived("https://nyidanmark.dk/en-GB/News-Front-Page/2022/09/Changed-rules", lexicon)
    # an explicit archive section still wins
    assert is_archived("https://x.gov.example/archive/2024/07/12/visa-post", lexicon)
    # a second, older year outside the filing is still a veto
    assert is_archived("https://x.gov.example/2019/uploads/2023/10/a.pdf", lexicon)
    # a sites folder whose next segment is not a number is not a multisite
    assert is_archived("https://x.gov.example/sites/default/2019/a.pdf", lexicon)


# --- a country name must not match inside a word ---------------------------------------------


def wrong_country_for(text: str, purpose: TravelPurpose = "business") -> str | None:
    registry = get_country_registry()
    return wrong_country(
        PageLink(
            url="https://www.mofa.go.jp/visa",
            text=text,
            heading="",
            depth=0,
            discovered_from="seed",
        ),
        Corridor(
            destination_slug="japan",
            passport_nationality="IN",
            applying_from="GB",
            purpose=purpose,
        ),
        registry,
        "JP",
    )


def test_a_country_code_inside_an_ordinary_word_is_not_a_country() -> None:
    """`wrong_country` is a veto, so a substring match silently threw the best page away.

    "us" sits inside "business", "house" and "because" — every business-purpose page was being
    rejected as though it were about the United States.
    """

    for harmless in ("Business visa", "Because of the pandemic", "Campus visit", "Chadwick House"):
        assert wrong_country_for(harmless) is None, harmless


def test_a_country_named_in_full_is_still_vetoed() -> None:
    # The fix must not blunt the veto: a page genuinely about another country still goes.
    assert wrong_country_for("Visa for United States nationals") == "United States"
    assert wrong_country_for("Visa requirements for China") == "China"


def test_the_country_prefilter_names_exactly_what_a_full_scan_names() -> None:
    """`possible_for` is an optimisation, so it has to be invisible in the output.

    It replaced a scan of all 198 countries per candidate — 3.3s of a 54s corridor once a
    3,216-entry corpus was being scored (DECISIONS entry 50). It is a *superset* prefilter followed
    by the same exact check in the same registry order, so any divergence here is a bug in the
    index rather than a tuning question.

    The cases are the ones where an index could plausibly disagree: overlapping names, a country
    inside another country's name, a two-letter code that is also an English word, a name in a URL
    path rather than in text, and a host label.
    """

    registry = get_country_registry()
    corridor = Corridor(
        destination_slug="japan", passport_nationality="IN", applying_from="GB", purpose="tourism"
    )

    def full_scan(link: PageLink) -> str | None:
        allowed = {"IN", "GB", "JP"}
        for country in registry.countries:
            if country.code in allowed:
                continue
            if _matches_country(link, country):
                return country.name
        return None

    links = [
        PageLink(url=url, text=text, heading="", depth=0, discovered_from="seed")
        for url, text in (
            ("https://www.mofa.go.jp/visa", "Guinea-Bissau"),
            ("https://www.mofa.go.jp/visa", "Papua New Guinea"),
            ("https://www.mofa.go.jp/visa", "Democratic Republic of the Congo"),
            ("https://www.mofa.go.jp/visa", "South Africa"),
            ("https://www.mofa.go.jp/visa", "Business visa"),
            ("https://www.mofa.go.jp/visa", "Chadwick House"),
            ("https://www.mofa.go.jp/visa/detail/china.html", ""),
            ("https://www.mofa.go.jp/visa/united-states-of-america", ""),
            ("https://uk.emb-japan.go.jp/visa", "Visa"),
            ("https://www.mofa.go.jp/information/index.html", "Information"),
        )
    ]

    for link in links:
        assert wrong_country(link, corridor, registry, "JP") == full_scan(link), link


def test_every_country_has_the_field_the_trust_rule_depends_on() -> None:
    """`tlds` decides whether a domain is the destination's own government.

    A country without it is not merely weaker at scoring — it can never have a domain trusted, so
    it would silently be unresearchable.
    """

    countries = get_country_registry().countries

    assert len(countries) > 150, "destinations should not be limited to a curated handful"
    assert all(country.tlds for country in countries)
    assert all(country.code.isupper() and len(country.code) == 2 for country in countries)


# --- who a page is about, and which post published it ----------------------------------------


def scored_link(
    url: str, text: str = "", heading: str = "", *, role: DiscoveryRole, applying_from: str = "GB"
) -> float:
    registry = get_country_registry()
    scores = score_link(
        PageLink(url=url, text=text, heading=heading, depth=0, discovered_from="seed"),
        corridor().model_copy(update={"applying_from": applying_from}),
        get_lexicon(),
        registry.require("IN"),
        registry.require(applying_from),
    )
    return scores.score_for(role)


def route_link(url: str, text: str = "", heading: str = "") -> float:
    return scored_link(url, text, heading, role="application_route")


def decision_link(url: str, text: str = "", heading: str = "") -> float:
    return scored_link(url, text, heading, role="visa_decision")


def test_the_post_a_traveller_applies_at_outranks_the_post_of_their_own_country() -> None:
    """France's India post outranked its UK post for a traveller applying from the UK.

    `in.diplomatie.gouv.fr` is France's mission *in India*, so that label says which post published
    the page. Reading it as "this page is for Indian nationals" handed the nationality bonus to
    everything that post publishes — its accessibility statement included — and 40 beat the 30 the
    UK post earns for actually serving this traveller: 65.6 against 55.6 on the identical page.
    """

    india_post = route_link(
        "https://in.diplomatie.gouv.fr/en/applying-for-a-visa", "Applying for a visa"
    )
    uk_post = route_link(
        "https://uk.diplomatie.gouv.fr/en/applying-for-a-visa", "Applying for a visa"
    )

    assert uk_post > india_post


def test_a_page_whose_own_words_name_the_nationality_still_earns_it() -> None:
    """The narrowing must not cost the real signal. A path or a title naming the country is the
    page describing itself, which is a different thing from the host it sits on."""

    about_indians = route_link(
        "https://in.diplomatie.gouv.fr/en/applying-for-a-visa-indian-nationals",
        "Applying for a visa: Indian nationals",
    )
    generic = route_link(
        "https://in.diplomatie.gouv.fr/en/applying-for-a-visa", "Applying for a visa"
    )

    assert about_indians > generic


def test_a_page_about_where_they_apply_from_is_scored_at_all() -> None:
    """Canada publishes 635 "where to submit your application" pages, one per country of
    application. For an Indian national in Britain the `?country=GB` page — the one that answers
    them — scored **-8.0** for `application_route`, so it failed `best_combined() > 0` and was never
    shown to the selector at all, while its `?country=IN` sibling scored 32.0. Nothing rewarded a
    page for saying which country it is about; only `wrong_country` read that, and only to reject.

    **What is asserted is that it is scored, not that it wins.** Crossing zero is the whole of what
    the request path consumes: `_choose_what_to_read` pools on that boolean and hands the pool to
    the model *unsorted*, with the scores withheld. The two pages now tie at 32.0, and that is the
    intended end state — the ordering between them is not a question this scorer should be
    answering from a URL. DECISIONS entry 126.
    """

    submit_from_britain = route_link(
        "https://ircc.canada.ca/english/where-submit-application.asp?country=GB&lob=citizenship",
        "United Kingdom",
        "If you're a parent or legal guardian applying for a minor child",
    )
    submit_from_india = route_link(
        "https://ircc.canada.ca/english/where-submit-application.asp?country=IN&lob=citizenship",
        "India",
        "If you're a parent or legal guardian applying for a minor child",
    )

    assert submit_from_britain > 0
    assert submit_from_britain >= submit_from_india


def test_a_page_about_the_passport_country_is_never_demoted_for_it() -> None:
    """The residence bonus adds and never subtracts, and this is the case that decided it.

    This first shipped as a *swap* — the nationality bonus withdrawn from the post-specific roles
    and the residence bonus put in its place. New Zealand publishes `checklists/china/` and
    `checklists/india/` and **no** British checklist, so for an Indian applying from Britain the
    swap took 40 points off the only visitor checklist New Zealand publishes for them, with nothing
    to lose to. Over 53 corpora the withdrawal removed 25 pages from the selector's pool and added
    none. DECISIONS entry 126.
    """

    inz = "https://www.immigration.govt.nz/assets/inz/documents/checklists/india/checklist.pdf"
    for role in ("document_checklist", "application_route", "fees", "processing_times"):
        assert scored_link(inz, "Checklist for India", role=role) == scored_link(
            inz, "Checklist for India", role=role, applying_from="IN"
        )


def test_a_country_written_with_hyphens_in_a_path_is_recognised() -> None:
    """`united-kingdom` is how a URL writes a two-word country, and the check read neither it nor
    `apply-united-kingdom`: the token carries a space and the segment's words carry none. So every
    country whose name is more than one word was invisible unless the anchor text happened to say
    it — the Netherlands' own `…/checklist-schengen-visa-tourism/united-kingdom` is labelled
    "Checklist: tourism" and named no country at all. DECISIONS entry 126."""

    dutch = "https://www.netherlandsworldwide.nl/visa-the-netherlands"
    role: DiscoveryRole = "document_checklist"
    named = scored_link(
        f"{dutch}/checklist-schengen-visa-tourism/united-kingdom", "Checklist: tourism", role=role
    )
    generic = scored_link(
        f"{dutch}/checklist-schengen-visa-tourism", "Checklist: tourism", role=role
    )

    assert generic > 0
    assert named > generic


def test_the_decision_is_still_the_passport_country_s_to_answer() -> None:
    """Only the post-specific roles gain a residence bonus. Whether an Indian passport needs a visa
    is set by the destination's law and is the same at every consulate, so the page about India
    keeps its bonus for `visa_decision` and the page about Britain earns nothing there. Entry 72
    left `visa_decision` and `general_entry` out of post-preference for this reason; entry 126
    keeps them out.
    """

    about_india = decision_link(
        "https://ircc.canada.ca/english/visa-requirements-india", "Visa requirements: India"
    )
    about_britain = decision_link(
        "https://ircc.canada.ca/english/visa-requirements-united-kingdom",
        "Visa requirements: United Kingdom",
    )

    assert about_india > about_britain


def test_a_traveller_applying_from_home_is_scored_exactly_as_before() -> None:
    """The residence bonus fires only where the two countries differ. For a Filipino applying in
    the Philippines the passport and the post are one question, and the nationality bonus already
    answers it — doubling it here would just inflate every page about their own country. So `PH/PH`
    and every corridor like it is untouched by entry 126, which is half the selection oracle."""

    apply_from_india = scored_link(
        "https://ircc.canada.ca/english/where-submit-application.asp?country=IN&lob=citizenship",
        "India",
        "If you're a parent or legal guardian applying for a minor child",
        role="application_route",
        applying_from="IN",
    )
    apply_from_britain = scored_link(
        "https://ircc.canada.ca/english/where-submit-application.asp?country=GB&lob=citizenship",
        "United Kingdom",
        "If you're a parent or legal guardian applying for a minor child",
        role="application_route",
        applying_from="IN",
    )

    assert apply_from_india > apply_from_britain


def test_site_furniture_is_vetoed_however_well_it_scores() -> None:
    """These took three of France's ten fetch places. A footer link inherits the last heading above
    it, so the legal notice collected the heading bonus from a news article about visa
    requirements — and a legal notice cannot be visa guidance whatever it scores."""

    lexicon = get_lexicon()

    assert is_boilerplate("https://in.diplomatie.gouv.fr/accessibilite", lexicon)
    assert is_boilerplate("https://in.diplomatie.gouv.fr/en/donnees-personnelles", lexicon)
    assert is_boilerplate("https://in.diplomatie.gouv.fr/mentions-legales", lexicon)
    assert is_boilerplate("https://example.gov.uk/privacy", lexicon)
    assert is_boilerplate("https://canada.diplo.de/ca-en/newsletter", lexicon)
    assert is_boilerplate("https://www.gov.za/af/subscribe-mailing-lists", lexicon)
    # An e-visa portal's account page is where a traveller applies, not site furniture (entry 200).
    assert not is_boilerplate("https://thaievisa.go.th/sign-up", lexicon)
    # Real guidance is untouched, including a path that merely mentions a document.
    assert not is_boilerplate("https://uk.diplomatie.gouv.fr/en/applying-for-a-visa", lexicon)
    assert not is_boilerplate(f"{EDINBURGH}", lexicon)


def test_the_heading_a_footer_link_inherits_cannot_carry_it_alone() -> None:
    """The mechanism behind the last test, recorded so it is not mistaken for a scoring accident:
    the boilerplate page's score came almost entirely from a heading about someone else's page."""

    registry = get_country_registry()
    inherited = score_link(
        PageLink(
            url="https://in.diplomatie.gouv.fr/mentions-legales",
            text="Mentions légales",
            heading="France lifts airport transit visa requirements for Indian nationals",
            depth=0,
            discovered_from="seed",
        ),
        corridor(),
        get_lexicon(),
        registry.require("IN"),
        registry.require("GB"),
    ).best()[1]

    # The shortlist ranks on the best role, and this reached 69 in the real run — high enough to
    # take a fetch place. It still scores; the veto is what stops it, not the score.
    assert inherited > 20
    assert is_boilerplate("https://in.diplomatie.gouv.fr/mentions-legales", get_lexicon())


def test_a_page_that_states_the_answer_scores_as_the_visa_decision() -> None:
    """The vocabulary asked the question and could not recognise the answer.

    Every `visa_decision` term was a way of *asking* — "do I need a visa", "visa requirements" — so
    Sweden's `list-of-foreign-citizens-who-require-visa-for-entry-into-sweden` matched none of them.
    It scored `general_entry` instead, on the word "entering" in its title, and therefore could not
    qualify its own refusal under DECISIONS entry 32: `government.se` blocked that exact page, the
    block was correctly reported, and the corridor refused rather than handing the traveller the
    URL. Both the slug and the engine's title are checked, because which one a run sees depends on
    whether the candidate arrived from the corpus or from search — and on 2026-08-23 it was search.
    """

    registry = get_country_registry()
    corridor = Corridor(
        destination_slug="sweden", passport_nationality="IN", applying_from="GB", purpose="tourism"
    )
    url = (
        "https://www.government.se/government-policy/migration-and-asylum/"
        "list-of-foreign-citizens-who-require-visa-for-entry-into-sweden"
    )
    title = (
        "List of third countries whose nationals must be in possession of visas "
        "when entering Sweden - Government.se"
    )
    for label, text in (("the slug alone", ""), ("the engine's title", title)):
        scores = score_link(
            PageLink(url=url, text=text, heading="", depth=0, discovered_from="seed"),
            corridor,
            get_lexicon(),
            registry.require("IN"),
            registry.require("GB"),
        )
        assert scores.score_for("visa_decision") > 0, label


def test_a_page_stating_entry_conditions_scores_for_general_entry() -> None:
    """The role had three terms and scored **zero** candidates in Germany and Japan (entry 103).

    A page scoring nothing for a role is not merely ranked low — it can never be shortlisted or
    selected *for* that role at any budget, which is entry 78's finding in a second place. These
    are real pages' words: Japan's landing permission, Germany's Schengen subsistence rule, and
    Singapore's arrival-card conditions. Each must score something.
    """

    corridor = Corridor(
        destination_slug="testland",
        passport_nationality="IN",
        applying_from="GB",
        purpose="tourism",
    )
    registry = get_country_registry()
    pages = [
        ("landing permission at the port of entry", "https://mfa.gov.example/landing-permission"),
        ("means of subsistence and travel insurance", "https://mfa.gov.example/entry-conditions"),
        ("show sufficient funds and onward travel", "https://ica.gov.example/entering"),
        ("how long can I stay: period of stay", "https://mfa.gov.example/period-of-stay"),
    ]
    for text, url in pages:
        scores = score_link(
            PageLink(url=url, text=text, heading="", depth=0, discovered_from="seed"),
            corridor,
            get_lexicon(),
            registry.require("IN"),
            registry.require("GB"),
        )
        assert scores.score_for("general_entry") > 0, text


def test_a_page_saying_what_it_costs_or_how_long_it_takes_scores_for_that_role() -> None:
    """`fees` had four terms and `processing_times` three, and between them they scored **zero**
    candidates in fourteen of the twenty oracle corridors (entry 104).

    Every phrase here is off a real page: GOV.UK's "visa decision waiting times", Sweden's
    `you-are-waiting-for-a-decision` URL path, the Netherlands' "consular fees in India".
    """

    corridor = Corridor(
        destination_slug="testland",
        passport_nationality="IN",
        applying_from="GB",
        purpose="tourism",
    )
    registry = get_country_registry()
    pages: list[tuple[DiscoveryRole, str, str]] = [
        ("fees", "consular fees in India", "https://gov.example/consular-fees/india"),
        ("fees", "how much does it cost", "https://gov.example/cost"),
        ("processing_times", "visa decision waiting times", "https://gov.example/waiting-times"),
        ("processing_times", "", "https://gov.example/you-are-waiting-for-a-decision"),
        (
            "processing_times",
            "a decision usually takes 15 working days",
            "https://gov.example/wait",
        ),
    ]
    for role, text, url in pages:
        scores = score_link(
            PageLink(url=url, text=text, heading="", depth=0, discovered_from="seed"),
            corridor,
            get_lexicon(),
            registry.require("IN"),
            registry.require("GB"),
        )
        assert scores.score_for(role) > 0, f"{role}: {text or url}"


def test_paying_a_fee_is_not_the_same_question_as_what_the_fee_is() -> None:
    """`payment` was tried in `fees` and rejected (entry 104). It raised Canada's top candidate
    from 51 to 61 and the page it promoted was `epay/order.do` — "Pay Your Application Fees, Online
    Payment" — over the fee schedule. A traveller needs the amount, not the till, so a checkout
    page must not outrank a page that states a fee."""

    corridor = Corridor(
        destination_slug="testland",
        passport_nationality="IN",
        applying_from="GB",
        purpose="tourism",
    )
    registry = get_country_registry()

    def score(text: str, url: str) -> float:
        return score_link(
            PageLink(url=url, text=text, heading="", depth=0, discovered_from="seed"),
            corridor,
            get_lexicon(),
            registry.require("IN"),
            registry.require("GB"),
        ).score_for("fees")

    schedule = score("consular fees in India", "https://gov.example/consular-fees/india")
    checkout = score("pay your application fees online", "https://gov.example/epay/order.do")

    assert schedule > checkout, "the page stating the fee must outrank the page collecting it"


def test_no_new_overlapping_lexicon_terms() -> None:
    """A term inside another term makes both fire, so the page scores twice.

    Seven such pairs already exist and are frozen below rather than fixed: correcting them would
    move every score by up to 2× and needs its own measurement. What this guards is *new* ones —
    adding `need a visa` beside `check if you need` inflated a Caribbean page past the Netherlands'
    own United Kingdom application page, and the shortlist diff was the only thing that showed it.
    """

    known = {
        ("visa_decision", "visa requirement", "visa requirements"),
        ("visa_decision", "visa exemption", "visa exemptions"),
        ("application_route", "apply", "how to apply"),
        ("application_route", "apply", "apply online"),
        ("fees", "visa fee", "visa fees"),
        ("fees", "fees", "visa fees"),
        ("processing_times", "processing time", "processing times"),
    }
    # A whole-word role matches "eta" in "gov.uk/eta" and never inside "nzeta", so for it only a
    # term that is a whole word of another overlaps (entry 275).
    found = {
        (role, inner.phrase, outer.phrase)
        for role, terms in get_lexicon().roles.items()
        for inner in terms.terms
        for outer in terms.terms
        if inner.phrase != outer.phrase
        and (
            re.search(rf"\b{re.escape(inner.phrase)}\b", outer.phrase)
            if terms.whole_words
            else inner.phrase in outer.phrase
        )
    }
    assert found == known, f"new overlaps: {found - known}; resolved: {known - found}"


def test_a_post_named_after_the_country_it_serves_is_recognised() -> None:
    """`mission_labels` carried the ISO code and little else for 184 of 198 countries, so a post
    named after its country or its city was invisible to `mission_affinity`.

    Saudi Arabia is the case that found it: it held **one** label, `sa`, against the United Arab
    Emirates' six, so Australia's own `saudiarabia.embassy.gov.au` — 35 pages of it in the corpus —
    read as belonging to no post at all for a traveller applying from Riyadh. DECISIONS entry 134.
    """

    registry, lexicon = get_country_registry(), get_lexicon()
    saudi = registry.require("SA")
    others = foreign_post_labels(registry, "AU", saudi)

    for url in (
        "https://saudiarabia.embassy.gov.au/ryad/visas_and_migration.html",
        "https://sa.china-embassy.gov.cn/eng/lsfw/x.htm",
        "https://riad.diplo.de/sa-en/service/visa",
    ):
        assert mission_affinity(url, saudi, lexicon, other_posts=others) == "own", url


def test_another_countrys_post_still_reads_as_another_post() -> None:
    """The half that pays for the half above, and the half entry 72 broke 165 pages getting wrong.

    Enriching the labels widens *both* rules: a page is likelier to be recognised as the traveller's
    own post, and likelier to be recognised as somebody else's. Measured over Germany's corpus, 71
    pages left the selector's pool and every one was another country's German mission — Colombo,
    Taipei, Windhoek, Addis Ababa — appointment and contact pages for posts that do not serve a
    British resident. No page the oracle names as answering a role lost a single point.
    """

    registry, lexicon = get_country_registry(), get_lexicon()
    britain = registry.require("GB")
    others = foreign_post_labels(registry, "DE", britain)

    assert (
        mission_affinity(
            "https://uk.diplo.de/uk-en/02/visa/x", britain, lexicon, other_posts=others
        )
        == "own"
    )
    for url in (
        "https://india.diplo.de/in-en/service/x",
        "https://colombo.diplo.de/lk-en/service/x",
        "https://addis-abeba.diplo.de/et-en/service/appointment",
    ):
        assert mission_affinity(url, britain, lexicon, other_posts=others) == "other", url


def test_a_label_two_countries_could_claim_is_held_by_neither() -> None:
    """`foreign_post_labels` turns a label no corridor endpoint claims into another post, worth -45
    on the roles a checklist lives in. So an ambiguous label does not merely fail to help — it
    demotes a page for the wrong country, and the enrichment drops any label two countries claim."""

    registry = get_country_registry()
    owners: dict[str, list[str]] = {}
    for country in registry.countries:
        for label in country.mission_labels:
            owners.setdefault(label, []).append(country.code)

    shared = {label: codes for label, codes in owners.items() if len(codes) > 1}
    # Hong Kong's two forms predate this and are China's; they are the documented exception.
    assert set(shared) <= {"hong-kong", "hongkong"}, shared


def test_a_destinations_own_name_is_not_read_as_a_translation() -> None:
    """Entry 220. As a substring, "thai" sat in every "Thailand" and "german" in every "Germany", so
    a destination's own name cost its pages the translated-copy penalty: Thailand's announcement
    naming India scored -19 for the decision. A language named as a word still pays it."""

    registry = get_country_registry()
    corridor = Corridor(destination_slug="thailand", passport_nationality="IN", applying_from="IN")
    url = "https://thailand.prd.go.th/en/content/category/detail/id/2078/iid/522327"

    def reasons(text: str) -> list[str]:
        scores = score_link(
            PageLink(url=url, text=text, depth=0),
            corridor,
            get_lexicon(),
            registry.require("IN"),
            registry.require("IN"),
        )
        return [reason for signals in scores.signals.values() for reason in signals]

    own_name = reasons("Thailand Revises Visa Policy for 65 Countries & Territories")
    assert not any(reason.startswith("translation:") for reason in own_name)
    assert any(reason.startswith("translation:thai") for reason in reasons("Visa policy (Thai)"))


def authorisation_body(text: str, title: str, url: str) -> RoleScores:
    registry = get_country_registry()
    return score_body(
        text,
        title,
        corridor(),
        get_lexicon(),
        registry.require("US"),
        url=url,
        residence=registry.require("US"),
    )


def test_a_page_is_about_an_authorisation_only_when_its_title_or_address_names_one() -> None:
    """Entry 275: every GOV.UK visa page carries the ETA notice, so a body match credited 100
    pages and the ETA's own page never reached the selector."""

    notice = "You must have an electronic travel authorisation (ETA) or a visa to travel to the UK."
    visit = authorisation_body(
        notice, "Visit the UK as a Standard Visitor", "https://www.gov.uk/standard-visitor"
    )
    eta = authorisation_body(
        notice, "Get an electronic travel authorisation (ETA)", "https://www.gov.uk/eta"
    )
    by_address = authorisation_body("Apply here.", "Apply", "https://www.gov.uk/eta/apply")

    assert visit.score_for("travel_authorisation") == 0
    assert eta.score_for("travel_authorisation") > 0
    assert by_address.score_for("travel_authorisation") > 0


def test_an_authorisation_term_matches_whole_words_only() -> None:
    """ "eta" sits inside "details" and "Beta"; only the word counts."""

    registry = get_country_registry()
    details = score_link(
        link_at("https://www.gov.uk/visa-details", "Beta service: visa details"),
        corridor(),
        get_lexicon(),
        registry.require("IN"),
        registry.require("GB"),
    )
    named = score_link(
        link_at("https://www.gov.uk/eta", "Apply for an ETA"),
        corridor(),
        get_lexicon(),
        registry.require("IN"),
        registry.require("GB"),
    )

    assert details.score_for("travel_authorisation") == 0
    assert named.score_for("travel_authorisation") > 0


def test_naming_an_authorisation_does_not_count_toward_breadth() -> None:
    """A title signal says what the page is, not that it is a directory, so it neither widens the
    breadth count nor is dampened by it."""

    text = (
        "How to apply online. The fee is 16 pounds. You will usually get a decision within 3 "
        "working days. Check if you need a visa."
    )
    plain = authorisation_body(text, "Visiting the UK", "https://www.gov.uk/visiting")
    named = authorisation_body(text, "Get an ETA to visit the UK", "https://www.gov.uk/eta")

    for role in ("visa_decision", "application_route", "processing_times"):
        assert named.score_for(role) == plain.score_for(role)
    assert any(signal.startswith("breadth") for signal in plain.signals["visa_decision"])
    assert not any(signal.startswith("breadth") for signal in named.signals["travel_authorisation"])
