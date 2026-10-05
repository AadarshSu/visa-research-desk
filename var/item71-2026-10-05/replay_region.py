"""Replay the plan call on captured packets with a region of residence added (TODO item 71).

usage (repo root): replay_region.py OUT.jsonl RUNS CASE [CASE ...]   where CASE is PACKET_DIR=Region

Adds `region_of_residence` to the packet's traveller_profile, with the committed prompt unchanged,
and records where the plan says to apply and every unresolved question. Not validated or graded.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from visa_research_agent.research.openai_extraction import load_extraction_prompt
from visa_research_agent.research.personas import (
    PersonasPlanGenerator,
    personas_client_from_settings,
)


async def one(generator, prompt, case, run, out, lock):
    packet_dir, region = case.split("=", 1)
    packet = json.loads((Path(packet_dir) / "plan_packet.json").read_text(encoding="utf-8"))
    if region != "none":
        packet["traveller_profile"]["region_of_residence"] = region
    started = time.monotonic()
    row: dict[str, object] = {"packet": packet_dir, "region": region, "run": run}
    try:
        draft = await generator.generate(prompt, json.dumps(packet, ensure_ascii=False, indent=2))
        row.update(
            visa_required=draft.visa_required,
            where_to_apply=draft.where_to_apply.model_dump(mode="json")
            if draft.where_to_apply
            else None,
            steps=[s.model_dump(mode="json") for s in draft.application_steps],
            unresolved=draft.unresolved_questions,
        )
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"[:300]
    row["seconds"] = round(time.monotonic() - started, 1)
    async with lock:
        with out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(
        f"{Path(packet_dir).parent.name} {region} run {run}: "
        f"{row.get('error', 'ok')} ({row['seconds']}s)",
        flush=True,
    )


async def main() -> None:
    out, runs, cases = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3:]
    prompt = load_extraction_prompt()
    generator = PersonasPlanGenerator(personas_client_from_settings())
    lock, gate = asyncio.Lock(), asyncio.Semaphore(4)

    async def gated(case, run):
        async with gate:
            await one(generator, prompt, case, run, out, lock)

    await asyncio.gather(*(gated(c, r) for c in cases for r in range(1, runs + 1)))


asyncio.run(main())
