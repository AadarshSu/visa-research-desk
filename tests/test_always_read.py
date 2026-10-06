"""Pages a country's corridors read on every run, named by a person (DECISIONS entry 267)."""

from pathlib import Path

import pytest
from test_automatic_destinations import NOW, StubProvider, StubResolver, corridor, france, registry
from test_automatic_destinations import resolved as resolved_corridor

from visa_research_agent.discovery.always_read import (
    DISCOVERED_FROM,
    AlwaysReadRegistry,
    check_trusted,
    load_always_read,
)
from visa_research_agent.discovery.automatic import AutomaticDestinationService
from visa_research_agent.discovery.corridor_store import FileCorridorStore
from visa_research_agent.discovery.registry import get_authority_registry

FR_PAGE = "https://france-visas.gouv.fr/en/web/france-visas/visa-wizard"


def named(url: str = FR_PAGE, purposes: tuple[str, ...] = ("tourism",)) -> AlwaysReadRegistry:
    return AlwaysReadRegistry.model_validate(
        {
            "schema_version": 1,
            "countries": [
                {
                    "code": "FR",
                    "pages": [
                        {
                            "url": url,
                            "title": "Visa wizard",
                            "purposes": list(purposes),
                            "why": "a test page",
                        }
                    ],
                }
            ],
        }
    )


def test_the_committed_file_loads_and_every_page_is_on_its_countrys_trusted_domains() -> None:
    loaded = load_always_read(authorities=get_authority_registry())

    assert loaded.pages_for("AU", "tourism"), "Australia's ETA page is the first entry"


def test_a_page_off_the_countrys_trusted_domains_is_refused_when_the_file_loads() -> None:
    with pytest.raises(ValueError, match="trusted domains"):
        check_trusted(named("https://www.australia.com/en/visa.html"), registry(france()))


def test_a_page_not_served_over_https_is_refused() -> None:
    with pytest.raises(ValueError, match="https"):
        check_trusted(named(FR_PAGE.replace("https", "http")), registry(france()))


def test_a_country_with_no_trusted_domains_can_name_no_page() -> None:
    with pytest.raises(ValueError, match="none"):
        check_trusted(named(), registry())


def test_pages_are_read_only_for_the_purposes_they_name() -> None:
    pages = named(purposes=("tourism",))

    assert [link.url for link, _ in pages.pages_for("FR", "tourism")] == [FR_PAGE]
    assert pages.pages_for("FR", "study") == []
    assert pages.pages_for("DE", "tourism") == []
    link, title = pages.pages_for("FR", "tourism")[0]
    assert link.discovered_from == DISCOVERED_FROM and title == "Visa wizard"


@pytest.mark.anyio
async def test_a_corridor_is_resolved_with_its_countrys_named_pages(tmp_path: Path) -> None:
    given: list[dict[str, object]] = []

    def build(**kwargs: object) -> StubResolver:
        given.append(kwargs)
        return StubResolver(resolved_corridor())

    service = AutomaticDestinationService(
        StubProvider(["https://france-visas.gouv.fr/en/applying"]),
        build,  # type: ignore[arg-type]
        FileCorridorStore(tmp_path / "corridors"),
        authorities=registry(france()),
        always_read=named(),
        now=lambda: NOW,
    )

    await service.destination_for("France", corridor())
    await service.destination_for("France", corridor().model_copy(update={"purpose": "study"}))

    urls = [[link.url for link, _ in call["always_read"]] for call in given]  # type: ignore[attr-defined]
    tourism, study = urls
    assert FR_PAGE in tourism
    assert FR_PAGE not in study
