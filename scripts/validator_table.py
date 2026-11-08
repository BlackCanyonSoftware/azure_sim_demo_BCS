#!/usr/bin/env python3
"""Run the validator's good/bad table (each rule, both ways) with real output read. Fresh subprocesses.
Runner of record: project .venv. This file is the command behind docs/validator-table-output.txt."""
import json, math, subprocess, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "app"))
import validate  # noqa: E402
GP = "Design a two-storey studio building around a shaped arrangement"   # no area, no outdoor words -> rule (a) silent
AP = "Design a two-storey studio building totaling approximately 140 m2 of indoor space around a central courtyard"
def ans(vols, idea, spaces): return {"title":"t","summary":"s","spaces":spaces,"options":[{"id":"A","name":"opt","idea":idea,"volumes":vols}],"next":"n"}
S0=[{"name":"living","floors":"both","area_m2":100}]
SA=[{"name":"living","floors":"both","area_m2":115},{"name":"V2","floors":"both","area_m2":20},{"name":"courtyard","floors":"outdoor","area_m2":30}]
US=[{"name":"West Wing","x":0,"y":0,"w":6,"d":14,"h":7},{"name":"Middle","x":6,"y":0,"w":14,"d":6,"h":7},{"name":"East Wing","x":20,"y":0,"w":6,"d":14,"h":7}]
CR=[{"name":"West Wing","x":0,"y":0,"w":10,"d":24,"h":7},{"name":"North Wing","x":10,"y":14,"w":20,"d":10,"h":7},{"name":"East Wing","x":30,"y":0,"w":10,"d":24,"h":7},{"name":"South Wing","x":10,"y":0,"w":20,"d":8,"h":3.5}]
LC=[{"name":"a","x":0,"y":0,"w":10,"d":10,"h":7},{"name":"b","x":10,"y":10,"w":10,"d":10,"h":7}]
FS=[{"name":"Studio","x":0,"y":0,"w":12,"d":9,"h":7},{"name":"Link","x":12,"y":0,"w":5,"d":6,"h":3.5}]
DET=[{"name":"a","x":0,"y":0,"w":10,"d":10,"h":7},{"name":"b","x":22,"y":4,"w":8,"d":8,"h":7}]
OVL=[{"name":"a","x":0,"y":0,"w":6,"d":10,"h":7},{"name":"b","x":3,"y":4,"w":6,"d":10,"h":7}]
D52=[{"name":"a","x":0,"y":0,"w":12,"d":9,"h":5.2},{"name":"b","x":12,"y":0,"w":5,"d":6,"h":5.2}]
B=lambda s:[f"option A: idea names an open {s} side but the volumes do not leave that side open"]
ROWS=[
 ("ushape: idea names north = its real open side", ans(US,"opens to the north",S0), GP, []),
 ("ushape: idea names south (row closed)",          ans(US,"opens to the south",S0), GP, B("south")),
 ("enclosed ring: idea names north",                ans(CR,"opens to the north",S0), GP, B("north")),
 ("enclosed ring: no-side idea",                    ans(CR,"",S0), GP, []),
 ("L-corner: names north (one of four open)",       ans(LC,"opens to the north",S0), GP, []),
 ("L-corner: names east",                            ans(LC,"opens to the east",S0), GP, []),
 ("face-share: names north",                         ans(FS,"opens to the north",S0), GP, []),
 ("face-share: names west (covered)",                ans(FS,"opens to the west",S0), GP, B("west")),
 ("word form: northern maps to north",               ans(US,"the northern side is open",S0), GP, []),
 ("rule c: detached pair (one line per volume)",     ans(DET,"",S0), GP, ["option A: volume 'a' touches no other volume","option A: volume 'b' touches no other volume"]),
 ("rule c: overlap (reports both boxes)",            ans(OVL,"",S0), GP, ["option A: volume 'a' touches no other volume","option A: volume 'b' touches no other volume","option A: volumes overlap"]),
 ("rule d: 7 + 3.5 whole storeys",                   ans(FS,"",S0), GP, []),
 ("rule d: 5.2 m (one line per volume)",             ans(D52,"",S0), GP, ["option A height 5.2 m is not a whole number of storeys of about 3.5 m"]*2),
 ("rule a: courtyard listed apart + flagged",        ans(CR,"",SA), AP, []),
 ("rule a: garden named, floors=ground",             ans(CR,"",[{"name":"living","floors":"both","area_m2":115},{"name":"V2","floors":"both","area_m2":20},{"name":"garden","floors":"ground","area_m2":30}]), AP, ["outdoor space 'garden' is not flagged floors='outdoor'"]),
 ("rule a: courtyard asked, none listed",            ans(CR,"",[{"name":"living","floors":"both","area_m2":115},{"name":"V2","floors":"both","area_m2":20}]), AP, ["the prompt asked for a courtyard/garden but the answer lists none — list it as its own space, flagged floors='outdoor', out of the total"]),
 ("fix_note(fail row) non-empty", None, None, "non-empty"),
 # ---- rule (e) -----------------------------------------------------------------
 ("rule e good: courtyard named, real pocket open north", ans(US,"opens to the north",[{"name":"living","floors":"both","area_m2":115},{"name":"V2","floors":"both","area_m2":20},{"name":"courtyard","floors":"outdoor","area_m2":30}]), AP, []),
 ("rule e good: enclosed ring, pocket strictly inside",   ans(CR,"encloses the courtyard on all sides",[{"name":"living","floors":"both","area_m2":115},{"name":"V2","floors":"both","area_m2":20},{"name":"courtyard","floors":"outdoor","area_m2":30}]), AP, []),
 ("rule e bad: the old GIF's option A (flush pair)",       ans([{"name":"Left Volume","x":0,"y":0,"w":7,"d":10,"h":7},{"name":"Right Volume","x":7,"y":0,"w":7,"d":10,"h":7}],"two volumes on either side of a central courtyard forming a U-shape",[{"name":"living","floors":"both","area_m2":115},{"name":"V2","floors":"both","area_m2":20},{"name":"courtyard","floors":"outdoor","area_m2":30}]), AP, ["option A: the name/idea speaks of a courtyard, but the volumes leave no empty area inside their combined outline"]),
 ("rule e bad: enclosed wording, pocket reaches edge",     ans([{"name":"West Wing","x":0,"y":0,"w":10,"d":24,"h":7},{"name":"East Wing","x":30,"y":0,"w":10,"d":24,"h":7},{"name":"South Wing","x":10,"y":0,"w":20,"d":8,"h":3.5}],"an enclosed courtyard; the garden side opens north",[{"name":"living","floors":"both","area_m2":115},{"name":"V2","floors":"both","area_m2":20},{"name":"courtyard","floors":"outdoor","area_m2":30}]), AP, ["option A: the text says enclosed but the open area reaches the outline's edge"]),
]
allok=True
for name,data,prompt,exp in ROWS:
    got=validate.check(data,prompt) if data is not None else [validate.fix_note(B("west")) or "EMPTY"]
    if exp=="non-empty": ok=bool(got and got[0]!="EMPTY"); got=[str(ok)]
    else: ok=sorted(got)==sorted(exp)
    print(f"| {name} | {len(exp) if isinstance(exp,list) else exp} | {got} | {'OK' if ok else '**FAIL**'} |")
    allok &= ok
print("ALL ROWS OK:", allok); sys.exit(0 if allok else 1)
