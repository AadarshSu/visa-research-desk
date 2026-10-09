"""Entry 285: what the selector is shown with the link-only 40 taken by best link score (before)
against role by role (after). No network, no model.

For each capture in cap/new: the oracle roles whose answering page is shown (credited as
`selection-recall` credits it, via ../embed-replay-2026-09-28/common.py), the pages scoring
`travel_authorisation` that are shown, and every page one arm shows and the other does not.

usage: EMBED_CAPTURES=cap/new python compare.py [--detail]
"""

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "embed-replay-2026-09-28"))

import common  # noqa: E402

from visa_research_agent.discovery.models import ROLE_ORDER, CandidatePage  # noqa: E402
from visa_research_agent.discovery.page_text import PageTextStore  # noqa: E402
from visa_research_agent.discovery.selection import (  # noqa: E402
    DEFAULT_SELECTION_BLIND,
    DEFAULT_SELECTION_SHOWN,
    fusion_order,
    shown_to_selector,
)
from visa_research_agent.discovery.selection_recall import load_oracle  # noqa: E402


def before(pool, scores, has_text) -> list[CandidatePage]:
    """`shown_to_selector` as shipped until entry 285: the blind 40 by best link score."""

    ordered = fusion_order(pool, scores)
    if len(ordered) <= DEFAULT_SELECTION_SHOWN:
        return ordered
    rest = ordered[DEFAULT_SELECTION_SHOWN:]
    unread = sorted(
        (c for c in rest if not has_text(c.link.url)),
        key=lambda c: (-c.link_scores.best()[1], c.link.url),
    )[:DEFAULT_SELECTION_BLIND]
    added = {c.link.url for c in unread}
    return ordered[:DEFAULT_SELECTION_SHOWN] + [c for c in rest if c.link.url in added]


def describe(page: CandidatePage) -> str:
    role, score = page.link_scores.best()
    return f"{role:22} {score:5.1f} {page.link.url}"


def main() -> None:
    detail = "--detail" in sys.argv
    oracle = {row.corridor: row for row in load_oracle(common.ORACLE).corridors}
    totals: Counter[str] = Counter()
    for path in sorted(common.CAPTURES.glob("*.json")):
        cap = common.load(path.stem)
        data = json.loads(path.read_text(encoding="utf-8"))
        c = data["corridor"]
        key = "/".join(
            c[k] for k in ("destination_slug", "passport_nationality", "applying_from", "purpose")
        )
        pool, scores, held = cap["pool"], cap["scores"], cap["held"]
        has_text = held.__contains__
        old = {x.link.url: x for x in before(pool, scores, has_text)}
        new = {x.link.url: x for x in shown_to_selector(pool, scores, has_text)[0]}
        line = [f"{key:42} pool {len(pool):4}"]

        row = oracle.get(key)
        if row is not None:
            answer_urls = {u for role in ROLE_ORDER for u in row.answering_urls(role)}
            texts = dict(held)
            texts.update(
                PageTextStore(common.PAGETEXT).text_for_selection(
                    cap["code"], answer_urls - texts.keys()
                )
            )
            got = {"old": 0, "new": 0, "roles": 0}
            changed = []
            for role in ROLE_ORDER:
                answers = row.answering_urls(role)
                if not answers:
                    continue
                got["roles"] += 1
                o = common.credited(texts, set(old), answers)
                n = common.credited(texts, set(new), answers)
                got["old"] += o
                got["new"] += n
                if o != n:
                    changed.append(f"{role} {'lost' if o else 'gained'}")
            totals["roles"] += got["roles"]
            totals["old"] += got["old"]
            totals["new"] += got["new"]
            line.append(f"oracle {got['old']:2}→{got['new']:2} of {got['roles']:2}")
            if changed:
                line.append("; ".join(changed))

        def ta(shown: dict) -> int:
            return sum(
                1 for x in shown.values() if x.link_scores.score_for("travel_authorisation") > 0
            )

        line.append(f"travel_authorisation shown {ta(old)}→{ta(new)}")
        lost = [x for u, x in old.items() if u not in new]
        gained = [x for u, x in new.items() if u not in old]
        line.append(
            "lost "
            + str(dict(Counter(x.link_scores.best()[0] for x in lost)))
            + " gained "
            + str(dict(Counter(x.link_scores.best()[0] for x in gained)))
        )
        print("  ".join(line))
        if detail:
            for x in lost:
                print(f"    - {describe(x)}")
            for x in gained:
                print(f"    + {describe(x)}")
    if totals["roles"]:
        print(
            f"\noracle roles shown: before {totals['old']}, after {totals['new']}, "
            f"of {totals['roles']}"
        )


main()
