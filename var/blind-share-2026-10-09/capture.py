"""Copied from var/selection-replay-2026-09-24/capture.py for entry 285, current stores only.

Capture each oracle corridor's candidate set at the moment `_choose_what_to_read` is called.

Runs search, corpus merge and step-3b scoring exactly as a corridor does, then aborts before the
selection call, the fetch and every model call. Search is memoized per process, so the old-store
and new-store arms of one corridor see identical search results and only the store differs.

usage: capture.py OUTDIR [slug/NAT/RES ...]
"""

import asyncio
import json
import sys
from pathlib import Path

from visa_research_agent.discovery import cli
from visa_research_agent.discovery.corpus import FileCorpusStore
from visa_research_agent.discovery.models import Corridor
from visa_research_agent.discovery.page_text import PageTextStore
from visa_research_agent.discovery.selection_recall import load_oracle

ROOT = Path("/Users/aadarsh/Documents/Visa Research Agent")
STORES = {"new": (ROOT / "var/corpus", ROOT / "var/pagetext")}


class Captured(Exception):
    pass


class MemoSearch:
    def __init__(self, inner):
        self.inner = inner
        self.memo = {}
        self.calls = 0

    async def search(self, query, *, count):
        key = (query, count)
        if key not in self.memo:
            self.calls += 1
            self.memo[key] = await self.inner.search(query, count=count)
        return self.memo[key]

    def __getattr__(self, name):
        return getattr(self.inner, name)


async def capture(outdir: Path, key: str, search: MemoSearch) -> None:
    slug, nat, res, purpose = (key.split("/") + ["tourism"])[:4]
    corridor = Corridor(
        destination_slug=slug, passport_nationality=nat, applying_from=res, purpose=purpose
    )
    destination = cli.corridor_destination(slug, corridor, sys.stderr)
    country = cli.get_country_registry().by_slug(slug)
    for arm, (corpus_dir, text_dir) in STORES.items():
        path = outdir / arm / f"{slug}_{nat}_{res}_{purpose}.json"
        if path.exists():
            continue
        resolver = cli.build_resolver(
            None, None, corpus=FileCorpusStore(corpus_dir).load(country.code)
        )
        resolver.provider = search
        resolver.page_text = PageTextStore(text_dir)
        resolver.recall_log = None
        resolver.usage_log = None

        async def grab(destination, corridor, candidates, stored_scores, notes, trace, path=path):
            payload = {
                "corridor": corridor.model_dump(mode="json"),
                "destination_slug": destination.slug,
                "code": country.code,
                "candidates": [c.model_dump(mode="json") for c in candidates.values()],
                "stored_scores": {u: s.model_dump(mode="json") for u, s in stored_scores.items()},
                "notes": list(notes),
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
            raise Captured

        resolver._choose_what_to_read = grab
        try:
            await resolver.resolve(destination, corridor)
            print(f"{key} {arm}: resolved without reaching selection?", flush=True)
        except Captured:
            print(f"{key} {arm}: captured, searches so far {search.calls}", flush=True)


async def main() -> None:
    outdir = Path(sys.argv[1])
    keys = sys.argv[2:] or [
        row.corridor for row in load_oracle(ROOT / "oracle/selection_oracle.yaml").corridors
    ]
    search = MemoSearch(cli.build_search_provider())
    for key in keys:
        try:
            await capture(outdir, key, search)
        except Exception as exc:  # report and carry on
            print(f"{key}: FAILED {type(exc).__name__}: {exc}", flush=True)


asyncio.run(main())
