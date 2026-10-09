"""Choosing what to read by asking a model: what it may return, and what it may never carry.

Offline throughout. The test that matters is `test_a_selection_cannot_carry_a_word_of_stored_text`:
it is the barrier that lets `page_text.text_for_selection` exist at all (DECISIONS entry 83).
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from visa_research_agent.discovery.models import CandidatePage, Corridor, PageLink, RoleScores
from visa_research_agent.discovery.page_text import PageTextStore, StoredPage
from visa_research_agent.discovery.selection import (
    MAXIMUM_EXCERPT_CHARACTERS,
    MINIMUM_EXCERPT_CHARACTERS,
    Selection,
    admitted_on_text,
    build_selection_packet,
    excerpt_budget,
    fusion_order,
    link_order_by_role,
    load_selection_prompt,
    shown_to_selector,
    validated_selection,
)

NOW = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


def corridor() -> Corridor:
    return Corridor(
        destination_slug="japan", passport_nationality="IN", applying_from="GB", purpose="tourism"
    )


def candidate(url: str, text: str = "") -> CandidatePage:
    return CandidatePage(
        link=PageLink(url=url, text=text, heading="", depth=1, discovered_from="seed")
    )


def test_a_selection_cannot_carry_a_word_of_stored_text() -> None:
    """The barrier that permits `text_for_selection` to exist.

    Entry 78 forbade an accessor for stored bodies because a sentence written from one would be
    guidance served outside `source_maximum_stale_hours`. Entry 83 moved that barrier rather than
    removing it: bodies may be read, and the response type has nowhere to put prose. This test is
    the barrier. If it fails, the reason entry 78 gave has come back.
    """

    assert set(Selection.model_fields) == {"source_ids"}
    field = Selection.model_fields["source_ids"]
    assert field.annotation == list[str]


def test_a_candidate_with_no_stored_text_is_marked_rather_than_dropped() -> None:
    """Entry 80's failure, stated so a model can avoid it.

    A scalar cannot say "nothing is known about this page" — absent text scores zero and zero
    competes. The packet says it in words instead, and the candidate is still offered.
    """

    candidates = {
        "known": candidate("https://a.gov.example/known.html"),
        "unknown": candidate("https://a.gov.example/unknown.html"),
    }
    packet = build_selection_packet(corridor(), candidates, {"known": "Checklist for tourism..."})

    assert "no_stored_text" in packet
    assert "unknown.html" in packet, "a candidate nothing is known about is still offered"
    assert "stored_excerpt" in packet


def test_a_wide_field_shortens_excerpts_rather_than_dropping_candidates() -> None:
    """Dropping the 300th candidate would rebuild the recall gate this call exists to remove."""

    assert excerpt_budget(10, total=400_000) == MAXIMUM_EXCERPT_CHARACTERS
    assert excerpt_budget(705, total=400_000) < MAXIMUM_EXCERPT_CHARACTERS
    assert excerpt_budget(100_000, total=400_000) == MINIMUM_EXCERPT_CHARACTERS
    assert excerpt_budget(0, total=400_000) == MAXIMUM_EXCERPT_CHARACTERS


def test_the_packet_withholds_the_heuristic_scores() -> None:
    """`build_candidate_packet` withholds them for the same reason: passing them would anchor the
    model to the ranking this call exists to replace."""

    picked = candidate("https://a.gov.example/x.html")
    picked.link_scores.scores["visa_decision"] = 91.5

    packet = build_selection_packet(corridor(), {"x": picked}, {})

    assert "91.5" not in packet
    assert "link_scores" not in packet


def test_an_invented_id_is_discarded_and_named() -> None:
    candidates = {"real": candidate("https://a.gov.example/real.html")}

    kept, notes = validated_selection(
        Selection(source_ids=["real", "invented", "real"]), candidates
    )

    assert kept == ["real"], "duplicates collapse and unknown ids are dropped"
    assert any("invented" in note for note in notes)


def test_the_prompt_forbids_writing_anything_a_traveller_reads() -> None:
    """The type makes prose impossible; the prompt has to say why, or the model tries anyway."""

    prompt = load_selection_prompt()

    assert "no_stored_text" in prompt, "the model must be told what an absence means"
    assert "out of date" in prompt


def test_stored_text_is_returned_only_for_pages_the_index_holds(tmp_path: Path) -> None:
    store = PageTextStore(tmp_path)
    store.write("JP", [StoredPage(url="https://a.gov.example/a", fetched_at=NOW, body="x" * 400)])

    held = store.text_for_selection("JP", ["https://a.gov.example/a", "https://a.gov.example/b"])

    assert list(held) == ["https://a.gov.example/a"]
    assert store.text_for_selection("XX", ["https://a.gov.example/a"]) == {}


@pytest.mark.parametrize("count", [1, 72, 705])
def test_every_candidate_reaches_the_packet(count: int) -> None:
    candidates = {f"id{i}": candidate(f"https://a.gov.example/{i}.html") for i in range(count)}

    packet = build_selection_packet(corridor(), candidates, {})

    for i in range(count):
        assert f'"id{i}"' in packet


def test_a_candidate_carries_only_what_differs_from_the_next() -> None:
    """Two sentences repeated on every candidate, empty labels and indentation were 13–40% of a
    packet that is most of the bill (entry 164). Each is said once now, or not at all."""

    candidates = {
        "known": candidate("https://a.gov.example/known.html"),
        "unknown": candidate("https://a.gov.example/unknown.html", text="Visa checklist"),
    }

    packet = build_selection_packet(corridor(), candidates, {"known": "Checklist for tourism..."})
    entries = {entry["source_id"]: entry for entry in json.loads(packet)["candidates"]}

    assert "\n" not in packet and '": ' not in packet, "compact: whitespace is billed too"
    assert "stored_excerpt_note" not in packet
    assert entries["unknown"]["no_stored_text"] is True
    assert entries["unknown"]["link_text"] == "Visa checklist"
    assert "heading" not in entries["unknown"] and "title" not in entries["unknown"]
    assert entries["known"]["stored_excerpt"] == "Checklist for tourism..."
    assert "link_text" not in entries["known"], "an empty label is left out, not sent empty"


def test_an_identical_excerpt_is_shown_once_and_every_candidate_is_still_offered() -> None:
    """The same page at several addresses carried its excerpt once per address — 37% of
    Thailand's excerpt text (entry 164). The copies point at the first instead."""

    candidates = {
        "first": candidate("https://a.gov.example/checklist"),
        "copy": candidate("http://a.gov.example/checklist"),
        "other": candidate("https://a.gov.example/fees"),
    }
    text = {"first": "Checklist: passport", "copy": "Checklist: passport", "other": "Fees: £4"}

    entries = {
        entry["source_id"]: entry
        for entry in json.loads(build_selection_packet(corridor(), candidates, text))["candidates"]
    }

    assert set(entries) == {"first", "copy", "other"}
    assert entries["first"]["stored_excerpt"] == "Checklist: passport"
    assert entries["copy"]["stored_excerpt_same_as"] == "first"
    assert "stored_excerpt" not in entries["copy"]
    assert entries["other"]["stored_excerpt"] == "Fees: £4"


def test_the_prompt_explains_an_excerpt_that_points_at_another() -> None:
    prompt = load_selection_prompt()

    assert "stored_excerpt_same_as" in prompt
    assert "not a reason to reject it" in prompt


# --- what stored text may put back into the pool, TODO item 31 ---------------------------------


def text_scores(**by_role: float) -> RoleScores:
    return RoleScores(scores=dict(by_role))


def test_a_page_the_link_scored_zero_is_admitted_on_its_own_text() -> None:
    """Entry 127's case as arithmetic. Czechia's list of supporting documents for applicants in the
    United Kingdom scored zero on its link for every role, so the selector was never shown it, while
    its own text names the documents."""

    page = candidate("https://a.gov.example/uk.pdf")

    admitted = admitted_on_text([page], {page.link.url: text_scores(document_checklist=85.0)})

    assert admitted == [page]


def test_a_page_with_no_stored_text_or_nothing_scoring_in_it_stays_out() -> None:
    """An unread page has nothing to be judged on, and admitting it anyway is the unbounded widening
    the per-role bound exists to prevent. A page whose text scores zero or below — Liechtenstein's
    Casino Ordinance, a title naming the wrong purpose — is chaff and stays out with it."""

    unread = candidate("https://a.gov.example/unread.html")
    empty = candidate("https://a.gov.example/casino-ordinance.html")
    off_scope = candidate("https://a.gov.example/student-visa.html")

    admitted = admitted_on_text(
        [unread, empty, off_scope],
        {empty.link.url: RoleScores(), off_scope.link.url: text_scores(fees=-5.0)},
    )

    assert admitted == []


def test_each_role_admits_its_bound_best_first_with_ties_broken_by_address() -> None:
    pages = [candidate(f"https://a.gov.example/{i}.html") for i in range(8)]
    scored = {page.link.url: text_scores(fees=float(10 + i % 3)) for i, page in enumerate(pages)}

    admitted = admitted_on_text(pages, scored, per_role=3)

    assert [page.link.url for page in admitted] == [
        "https://a.gov.example/2.html",
        "https://a.gov.example/5.html",
        "https://a.gov.example/1.html",
    ]


def test_a_page_two_roles_want_is_admitted_once_and_each_role_still_gets_its_share() -> None:
    both = candidate("https://a.gov.example/both.html")
    fees = candidate("https://a.gov.example/fees.html")
    times = candidate("https://a.gov.example/times.html")
    scored = {
        both.link.url: text_scores(fees=50.0, processing_times=50.0),
        fees.link.url: text_scores(fees=40.0),
        times.link.url: text_scores(processing_times=40.0),
    }

    admitted = admitted_on_text([both, fees, times], scored, per_role=2)

    assert len(admitted) == 3
    assert {page.link.url for page in admitted} == {both.link.url, fees.link.url, times.link.url}


def test_only_a_page_outside_the_pool_can_be_admitted() -> None:
    """The resolver hands over the stored-text scores of *every* candidate, pooled ones included,
    because step 3b computed them all. Admission must never re-add or re-order the pool itself."""

    pooled = candidate("https://a.gov.example/pooled.html")
    outside = candidate("https://a.gov.example/outside.html")
    scored = {
        pooled.link.url: text_scores(document_checklist=99.0),
        outside.link.url: text_scores(document_checklist=30.0),
    }

    assert admitted_on_text([outside], scored) == [outside]


# --- what of a big pool the selector is shown, entries 194 and 195 -----------------------------


def linked(url: str, **by_role: float) -> CandidatePage:
    return CandidatePage(
        link=PageLink(url=url, text="", heading="", depth=1, discovered_from="seed"),
        link_scores=RoleScores(scores=dict(by_role)),
    )


def test_every_roles_best_comes_before_any_roles_second() -> None:
    """Roles are taken in turn, so a role with many strong candidates cannot fill the front of the
    list — the ranking is per role for the reason the shortlist's per-role depth is (entry 61)."""

    decisions = [
        linked(f"https://a.gov.example/decision-{i}", visa_decision=90.0 - i) for i in range(3)
    ]
    fee = linked("https://a.gov.example/fees", fees=10.0)

    order = fusion_order([*decisions, fee], {})

    assert [c.link.url for c in order[:2]] == [
        "https://a.gov.example/decision-0",
        "https://a.gov.example/fees",
    ]


def test_a_page_with_no_stored_text_still_ranks_on_its_link() -> None:
    """What separates this from the cap entry 158 rejected: text can lift a page, and the absence of
    text never sinks one below a page that scored nothing at all."""

    unread = linked("https://a.gov.example/unread", document_checklist=40.0)
    read = linked("https://a.gov.example/read", document_checklist=10.0)
    nothing = linked("https://a.gov.example/nothing")

    order = fusion_order(
        [nothing, read, unread], {read.link.url: text_scores(document_checklist=80.0)}
    )

    assert order[-1] is nothing
    assert unread in order[:2]


def test_a_page_search_returned_is_lifted_only_when_the_boost_is_asked_for() -> None:
    """Entries 243–245: the pages live search returned for this traveller are fused as a third
    ranking, so one of them passes a better-linked page search did not return. Off by default —
    showing the selector a shorter list on the strength of it is the owner's call (TODO item 78)."""

    corpus = [linked(f"https://a.gov.example/corpus-{i}", fees=90.0 - i) for i in range(3)]
    found = linked("https://a.gov.example/found", fees=50.0)
    found.searched = True

    assert fusion_order([*corpus, found], {})[-1] is found
    assert fusion_order([*corpus, found], {}, boost_searched=True)[0] is found

    offered, withheld = shown_to_selector(
        [*corpus, found], {}, lambda url: True, shown=1, blind=0, boost_searched=True
    )
    assert offered == [found]
    assert len(withheld) == 3


def test_a_searched_page_the_links_score_nothing_for_is_still_ranked_by_the_boost() -> None:
    """Being returned by search is the signal, so the search ranking holds every searched page —
    as it was measured (entry 244) — and a page nothing else ranks no longer comes last."""

    scored = linked("https://a.gov.example/scored", fees=10.0)
    unscored = linked("https://a.gov.example/unscored")
    found = linked("https://a.gov.example/found")
    found.searched = True

    order = fusion_order([unscored, found, scored], {}, boost_searched=True)

    assert order.index(found) < order.index(unscored)


def test_one_role_cannot_fill_the_pages_shown_on_their_links_alone() -> None:
    """Entry 285: South Korea's 40 went 37 to K-ETA notices on best link score, and the consulate
    page deciding the visa, scoring 14, was withheld. Taken role by role, it is shown."""

    read = linked("https://a.gov.example/read", visa_decision=90.0)
    notices = [
        linked(f"https://eta.gov.example/notice-{i}", travel_authorisation=90.0 - i)
        for i in range(4)
    ]
    decision = linked("https://a.gov.example/waiver-list", visa_decision=14.0)
    has_text = {read.link.url}.__contains__

    offered, withheld = shown_to_selector(
        [read, *notices, decision], {}, has_text, shown=1, blind=2
    )

    assert [c.link.url for c in offered] == [
        "https://a.gov.example/read",
        "https://eta.gov.example/notice-0",
        "https://a.gov.example/waiver-list",
    ]
    assert [c.link.url for c in withheld] == [
        "https://eta.gov.example/notice-1",
        "https://eta.gov.example/notice-2",
        "https://eta.gov.example/notice-3",
    ]


def test_a_page_several_roles_score_takes_one_blind_place() -> None:
    both = linked("https://a.gov.example/both", visa_decision=50.0, fees=50.0)
    fees = linked("https://a.gov.example/fees", fees=40.0)
    other = linked("https://a.gov.example/other", visa_decision=30.0)

    assert link_order_by_role([other, fees, both]) == [
        "https://a.gov.example/both",
        "https://a.gov.example/other",
        "https://a.gov.example/fees",
    ]


def test_a_pool_no_larger_than_the_cut_is_shown_whole() -> None:
    pool = [linked(f"https://a.gov.example/{i}", fees=float(i)) for i in range(5)]

    offered, withheld = shown_to_selector(pool, {}, lambda url: True, shown=5, blind=2)

    assert len(offered) == 5
    assert withheld == []


def test_a_big_pool_shows_the_ranked_top_plus_the_best_linked_pages_nobody_read() -> None:
    """The blind share is entry 158's route kept: a page with no stored text reaches the model on
    its link. It is added, never displacing the top, and everything else is withheld and returned
    so the caller can say so."""

    read = [linked(f"https://a.gov.example/read-{i}", fees=100.0 - i) for i in range(4)]
    unread = [linked(f"https://a.gov.example/unread-{i}", fees=50.0 - i) for i in range(3)]
    has_text = {c.link.url for c in read}.__contains__

    offered, withheld = shown_to_selector(read + unread, {}, has_text, shown=2, blind=2)

    assert [c.link.url for c in offered] == [
        "https://a.gov.example/read-0",
        "https://a.gov.example/read-1",
        "https://a.gov.example/unread-0",
        "https://a.gov.example/unread-1",
    ]
    assert {c.link.url for c in withheld} == {
        "https://a.gov.example/read-2",
        "https://a.gov.example/read-3",
        "https://a.gov.example/unread-2",
    }


def test_a_page_naming_the_traveller_past_its_head_shows_what_it_says_there() -> None:
    """TODO item 70. Slovenia's New Delhi page states the answer for Bangladeshis thousands of
    characters in, behind a head that says only what the page is; shown the head, the selector
    picked it 2 of 5 times. A page not naming the traveller past its head is shown as before."""

    head = "Visa information of the Embassy New Delhi. " + "Consular hours and fees. " * 150
    answer = "Citizens of Bangladesh, Bhutan, Nepal and Sri Lanka: visa is required."
    candidates = {
        "delhi": candidate("https://mfa.example/new-delhi"),
        "other": candidate("https://mfa.example/x"),
    }
    text = {"delhi": head + answer, "other": head + "Nothing about anyone in particular."}

    packet = json.loads(
        build_selection_packet(
            corridor(), candidates, text, total_characters=4_000, anchor_terms=["Bangladesh"]
        )
    )
    by_id = {entry["source_id"]: entry for entry in packet["candidates"]}

    assert answer in by_id["delhi"]["stored_excerpt"]
    assert by_id["other"]["stored_excerpt"] == text["other"][:2_000]
