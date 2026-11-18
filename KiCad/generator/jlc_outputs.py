"""
JLCPCB assembly files from a routed board: BOM (grouped, with LCSC numbers) and CPL (placement).

    python.exe jlc_outputs.py <board.kicad_pcb> <out_dir>

Coordinates are mm from the board's bottom-left corner, Y up (KiCad position-file convention);
JLCPCB shows each part on its own preview, so check the rotations there before ordering.
"""
import csv, os, sys
import pcbnew

pcb, out = sys.argv[1:3]
os.makedirs(out, exist_ok=True)
board = pcbnew.LoadBoard(pcb)
bb = board.GetBoardEdgesBoundingBox()
x0, y0 = bb.GetX(), bb.GetY()
name = os.path.splitext(os.path.basename(pcb))[0]


def field(fp, key):
    return next((f.GetText() for f in fp.GetFields() if f.GetName() == key), "")


rows, groups = [], {}
for fp in sorted(board.GetFootprints(), key=lambda f: f.GetReference()):
    ref = fp.GetReference()
    if ref.startswith(("H", "#", "JP")) or fp.IsExcludedFromBOM() or not fp.Pads():   # JP = copper solder jumper
        continue
    smd = all(not p.HasHole() or p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH for p in fp.Pads())
    pos = fp.GetPosition()
    rows.append([ref, fp.GetValue(), fp.GetFPID().GetLibItemName().wx_str(),
                 "%.3f" % pcbnew.ToMM(pos.x - x0), "%.3f" % pcbnew.ToMM(y0 + bb.GetHeight() - pos.y),
                 "Top" if fp.GetLayer() == pcbnew.F_Cu else "Bottom",
                 "%.1f" % (fp.GetOrientationDegrees() % 360)])
    key = (fp.GetValue(), fp.GetFPID().GetLibItemName().wx_str(), field(fp, "LCSC"), field(fp, "MPN"))
    g = groups.setdefault(key, {"refs": [], "smd": smd})
    g["refs"].append(ref)

with open(os.path.join(out, name + "_CPL_JLCPCB.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["Designator", "Val", "Package", "Mid X", "Mid Y", "Layer", "Rotation"])
    w.writerows(rows)

with open(os.path.join(out, name + "_BOM_JLCPCB.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #", "MPN", "Qty", "Mount"])
    for (val, fpn, lcsc, mpn), g in sorted(groups.items(), key=lambda kv: kv[1]["refs"][0]):
        w.writerow([val, ",".join(g["refs"]), fpn, lcsc, mpn, len(g["refs"]), "SMD" if g["smd"] else "THT"])

missing = [(k, g["refs"]) for k, g in groups.items() if not k[2]]
print("CPL rows:", len(rows), " BOM lines:", len(groups), " without LCSC number:", len(missing))
for (val, fpn, lcsc, mpn), refs in missing:
    print("   ", ",".join(refs), val, mpn)
