"""The committed region list a traveller chooses from (TODO item 71, DECISIONS entry 252)."""

from visa_research_agent.config.regions import (
    get_regions,
    regions_for,
    regions_from_geonames,
    render_regions_yaml,
)
from visa_research_agent.discovery.lexicon import get_country_registry

GEONAMES = [
    "GB.SCT\tScotland\tScotland\t2638360\n",
    "GB.ENG\tEngland\tEngland\t6269131\n",
    "FR.11\tÎle-de-France\tIle-de-France\t3012874\n",
    "FR.11\tÎle-de-France\tIle-de-France\t3012874\n",
    "XX.01\tNowhere\tNowhere\t1\n",
    "malformed line\n",
]


def test_the_generator_keeps_wanted_countries_english_names_sorted_and_unique() -> None:
    regions = regions_from_geonames(GEONAMES, ["GB", "FR"])

    assert regions == {"FR": ["Île-de-France"], "GB": ["England", "Scotland"]}


def test_the_rendered_file_credits_geonames_and_reads_back() -> None:
    import yaml

    text = render_regions_yaml({"GB": ["England", "Scotland"]})

    assert "Creative Commons Attribution 4.0" in text
    assert yaml.safe_load(text) == {"GB": ["England", "Scotland"]}


def test_the_committed_list_is_keyed_by_countries_this_agent_holds() -> None:
    codes = {country.code for country in get_country_registry().countries}
    regions = get_regions()

    assert set(regions) <= codes
    assert all(names and len(set(names)) == len(names) for names in regions.values())


def test_the_jurisdiction_splits_item_71_was_measured_on_are_choosable() -> None:
    """Entry 252's replay: each of these decides a post on a page the plan read."""

    assert {"Maharashtra", "Tamil Nadu", "West Bengal"} <= set(regions_for("IN"))
    assert {"England", "Scotland", "Wales", "Northern Ireland"} == set(regions_for("GB"))
    assert regions_for("SG") == ()
