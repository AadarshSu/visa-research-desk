import itertools
import json
import statistics
import sys
from collections import Counter, defaultdict

rows = json.load(open(sys.argv[1]))
NEEDS_AUTH = {
    "united-kingdom_US_US_tourism",
    "new-zealand_US_US_tourism",
    "canada_GB_GB_tourism",
    "united-states_GB_GB_tourism",
}


def label(x):
    if x["research"] != "resolved":
        return "refused"
    if x["corridor"] == "new-zealand_US_US_tourism" and x["vr"] is True:
        return "WRONG: NZeTA read as a visa"
    if x["vr"] is None:
        return "open"
    if (
        x["vr"] is False
        and x["corridor"] in NEEDS_AUTH
        and not (x["ta_field"] or x["auth_in_where"])
    ):
        return "WRONG: authorisation off the band" + (
            "" if (x["auth_in_steps"] or x["auth_in_uq"]) else ", nowhere"
        )
    return "good"


for x in rows:
    x["label"] = label(x)
    x["band"] = bool(x["ta_field"] or x["auth_in_where"])
    x["bad"] = x["label"].startswith("WRONG")
res = [x for x in rows if x["research"] == "resolved"]

print("labels:", Counter(x["label"] for x in rows))


def report(name, keep):
    kb = sum(1 for x in res if x["bad"] and keep(x))
    tb = sum(x["bad"] for x in res)
    good = [x for x in res if x["label"] == "good"]
    kg = sum(1 for x in good if keep(x))
    op = [x for x in res if x["label"] == "open"]
    ko = sum(1 for x in op if keep(x))
    print(
        f"{name:52} wrong kept {kb:2}/{tb}   good kept {kg:2}/{len(good)}"
        f"  (thrown {len(good) - kg:2})   open kept {ko}/{len(op)}"
    )


report("today, plan draft (every answered plan)", lambda x: True)
report("today, corridor store (as actually stored)", lambda x: x["stored"] > 0)
report("R1 only verified", lambda x: x["status"] == "verified")


def s1(x):  # role filled, plan names none
    return not (x["ta_role"] and not x["ta_field"])


def s2(x):  # no-visa plan mentions an authorisation below, not on the band
    return not (x["vr"] is False and (x["auth_in_steps"] or x["auth_in_uq"]) and not x["band"])


report("R3a shape: role filled, plan names none", s1)
report("R3b shape: authorisation below, not on band", s2)
report("R3 both shape checks", lambda x: s1(x) and s2(x))
report("R1 + R3", lambda x: x["status"] == "verified" and s1(x) and s2(x))
# two agreeing runs: every ordered pair within one batch and corridor
groups = defaultdict(list)
for x in res:
    groups[(x["batch"], x["corridor"])].append(x)
for keyname, key in [
    ("plan: decision + authorisation on band", lambda x: (x["vr"], x["band"])),
    ("plan: decision only", lambda x: (x["vr"],)),
]:
    kb = tb = kg = tg = 0
    for g in groups.values():
        for a, b in itertools.permutations(g, 2):
            keep = key(a) == key(b)
            if a["bad"]:
                tb += 1
                kb += keep
            elif a["label"] == "good":
                tg += 1
                kg += keep
    print(
        f"R2 two agree ({keyname}): wrong kept {kb}/{tb} pairs = {kb / tb:.0%};"
        f"  good kept {kg}/{tg} = {kg / tg:.0%}"
    )
print(
    "partial among wrong:",
    sum(1 for x in res if x["bad"] and x["status"] == "partial"),
    "/",
    sum(x["bad"] for x in res),
    " partial among good:",
    sum(1 for x in res if x["label"] == "good" and x["status"] == "partial"),
    "/",
    sum(1 for x in res if x["label"] == "good"),
)
print("median seconds a run:", statistics.median(x["sec"] for x in res))
print("\nwrong runs:")
for x in res:
    if x["bad"]:
        print(
            f"  {x['batch'][:26]:26} {x['corridor']:30} {x['run']} {x['status']:8}"
            f" stored={x['stored']} {x['label']}"
        )
print("\ngood thrown by R1:")
for x in res:
    if x["label"] == "good" and x["status"] != "verified":
        print(f"  {x['batch'][:26]:26} {x['corridor']:30} {x['run']}")
