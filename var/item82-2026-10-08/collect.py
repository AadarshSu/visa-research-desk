import json
import re
import sys
from pathlib import Path

base = Path(sys.argv[1])
AUTH = re.compile(
    r"\b(ETA|eTA|ESTA|NZeTA|K-ETA|electronic travel authori[sz]ation|travel authori[sz]ation)\b"
)
rows = []
for res in sorted(base.glob("*2026-10-0[678]*/*/*/result.json")):
    d = res.parent
    r = json.loads(res.read_text())
    p = json.loads((d / "plan.json").read_text()) if (d / "plan.json").exists() else {}
    roles = (
        json.loads((d / "roles_answer.json").read_text())
        if (d / "roles_answer.json").exists()
        else {}
    )
    ch = {c["role"]: c["source_id"] for c in roles.get("choices", [])}
    ta = p.get("travel_authorisation")
    steps = " ".join(json.dumps(s) for s in p.get("application_steps", []))
    wta = json.dumps(p.get("where_to_apply"))
    stored = list((d / "corridors").glob("*.json")) if (d / "corridors").exists() else []
    rows.append(
        dict(
            batch=d.parent.parent.name,
            corridor=d.parent.name,
            run=d.name,
            research=r.get("research"),
            plan=r.get("plan"),
            vr=p.get("visa_required", r.get("visa_required")),
            status=p.get("status", r.get("status")),
            sec=r.get("seconds"),
            cond=bool(p.get("decision_condition")),
            ta_field=(ta or {}).get("name") if isinstance(ta, dict) else ta,
            ta_role=ch.get("travel_authorisation"),
            auth_in_steps=bool(AUTH.search(steps)),
            auth_in_where=bool(AUTH.search(wta)),
            auth_in_uq=bool(AUTH.search(json.dumps(p.get("unresolved_questions", [])))),
            stored=len(stored),
        )
    )
json.dump(rows, open(sys.argv[2], "w"), indent=1)
COLUMNS = ("batch", "corridor", "run", "research", "vr", "status", "ta_field", "ta_role")
FLAGS = ("auth_in_steps", "auth_in_where", "auth_in_uq", "cond")
for x in rows:
    cells = " ".join(f"{str(x[name])[:26]:26}" for name in COLUMNS)
    flags = " ".join(f"{name}={int(x[name])}" for name in FLAGS)
    print(f"{cells} {flags} stored={x['stored']} {x['sec']}")
