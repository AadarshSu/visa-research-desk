"""Entry 255: does the timeline prompt shorten the plan call, and does it keep the decision?

usage (repo root): replay_steps.py OUT.jsonl RUNS ARM PACKET [PACKET ...]

Replays the plan call alone on captured `plan_packet.json` inputs from the 2026-10-03 purpose runs
(`var/purposes-2026-10-03/rules250/<corridor>/1/`), with the prompt of whichever code is on
`PYTHONPATH`, so both arms read identical inputs and only the prompt and step rules differ:

    # before: the committed code, from a checkout of HEAD
    PYTHONPATH=<checkout>/src .venv/bin/python var/steps-2026-10-05/replay_steps.py \
        var/steps-2026-10-05/before.jsonl 2 before <packets>
    # after: the working tree
    .venv/bin/python var/steps-2026-10-05/replay_steps.py \
        var/steps-2026-10-05/after.jsonl 2 after <packets>

Calls go through Personas one at a time. Each appends one JSON line: the decision, how many steps
and how many characters they took, the whole draft's characters, the call's tokens and seconds. A
draft the step rules refuse is recorded as an error, which is itself a result.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from visa_research_agent.discovery.adjudication import UsageRecorder
from visa_research_agent.research.openai_extraction import load_extraction_prompt
from visa_research_agent.research.personas import (
    PersonasPlanGenerator,
    personas_client_from_settings,
)


async def main() -> None:
    out, runs, arm = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    prompt = load_extraction_prompt()
    generator = PersonasPlanGenerator(personas_client_from_settings())
    for packet_path in map(Path, sys.argv[4:]):
        packet = packet_path.read_text(encoding="utf-8")
        corridor = packet_path.parent.parent.name
        for run in range(1, runs + 1):
            usage = UsageRecorder()
            started = time.monotonic()
            row: dict[str, object] = {"corridor": corridor, "arm": arm, "run": run}
            try:
                draft = await generator.generate(prompt, packet, usage=usage)
                steps = [step.model_dump() for step in draft.application_steps]
                row.update(
                    visa_required=draft.visa_required,
                    decision_source_ids=draft.decision_source_ids,
                    steps=len(steps),
                    step_characters=len(json.dumps(steps, ensure_ascii=False)),
                    draft_characters=len(draft.model_dump_json()),
                    timings=[step["timing"] for step in steps],
                )
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"[:300]
            row.update(
                seconds=round(time.monotonic() - started, 1),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            )
            with out.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(
                f"{corridor} {arm} run {run}: {row.get('visa_required', row.get('error'))}, "
                f"{row.get('steps')} steps, {row['output_tokens']} out ({row['seconds']}s)",
                flush=True,
            )


asyncio.run(main())
