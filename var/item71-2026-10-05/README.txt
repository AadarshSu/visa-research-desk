TODO item 71, 2026-10-05: does a region of residence settle the "which post serves you" question?
replay_region.py adds traveller_profile.region_of_residence to a captured plan_packet.json and
replays the plan call (committed prompt, Personas), 2 runs per case, 40 calls, 0 errors.
Raw rows: replay.jsonl.

Page states the split (Korea, China IN/IN; Germany, Japan IN/GB):
  no region      -> 8 of 8 runs ask which post
  deciding region -> 12 of 12 name the post the page assigns, none asks:
     Korea Maharashtra->Mumbai, Tamil Nadu->Chennai; China Maharashtra->Mumbai,
     West Bengal->Kolkata CVASC; Germany Scotland->Edinburgh; Japan Scotland->Edinburgh, Wales->London
  region too coarse -> still asks, 4 of 4: Japan England (northern counties go to Edinburgh),
     Germany England (London or Manchester)
Page does not state it (Turkey, Spain, South Africa, Maharashtra): no post invented in 6 of 6;
  5 of 6 still ask.
France PH: NCR -> no question, one run names the Manila centre; Central Visayas -> asks, 2 of 2.
Switzerland IN/IN study: every state goes to New Delhi; where_to_apply is null in 1 of 2 runs with
  and without a region, so that is noise unrelated to the region.

With plan rules 13 and 14 (replay_rules.jsonl, 40 calls, 0 errors):
  no region -> asks, 10 of 10 (Switzerland now asks too, though every state goes to New Delhi)
  deciding region -> right post 12 of 12; Japan Wales once also asks to confirm London
  Japan England -> asks for the county 1 of 2; the other names London without asking
  Germany England -> London or Manchester, asks 2 of 2
  no stated split -> no post invented 6 of 6
  in_person: required where a page says to attend; null where none says (Korea, Turkey, SA)
Rule 8e regression (regression.jsonl, 4 runs each): Japan IN/GB visa required 4/4 (in_person
  not_required 3/4: its eVISA page applies online for tourism); Singapore PH/PH no visa 4/4.
