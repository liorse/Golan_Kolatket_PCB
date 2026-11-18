"""
JLCPCB assembly files from a routed board: BOM (grouped, with LCSC numbers) and CPL (placement).

    python.exe jlc_outputs.py <board.kicad_pcb> <out_dir>

Coordinates are in the same frame as the Gerbers (KiCad absolute mm, Y up = -Y of the editor),
so JLCPCB lines the parts up with the board without any offset; check rotations in its preview.
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


# JLCPCB models whose reference point / orientation differ from the KiCad footprint
# (checked in JLCPCB's placement preview): footprint name -> (use the centre of the SMD pads,
# rotation correction in degrees)
JLC_FIX = {"ESP32-WROOM-32E": (True, -90), "ESP32-WROOM-32E_Via0.3": (True, -90)}
# package families whose JLCPCB models are drawn rotated against KiCad's footprints
# (pin-1 marker checked in the preview): footprint-name prefix -> rotation correction
JLC_ROT = [("SOIC-", -90), ("TSSOP-", -90), ("HTSSOP-", -90), ("TI_SO-PowerPAD", -90),
           ("SOT-223", 180), ("SOT-23-6", -90), ("SOT-23", 180)]

rows, groups = [], {}
for fp in sorted(board.GetFootprints(), key=lambda f: f.GetReference()):
    ref = fp.GetReference()
    if ref.startswith(("H", "#", "JP")) or fp.IsExcludedFromBOM() or not fp.Pads():   # JP = copper solder jumper
        continue
    smd = all(not p.HasHole() or p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH for p in fp.Pads())
    pos = fp.GetPosition()
    plated = [p for p in fp.Pads() if p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH]
    if plated and len(plated) == len([p for p in fp.Pads() if p.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH]):
        # all-through-hole connectors have their origin on pin 1; JLCPCB centres its model on the
        # given point, so give it the centre of the pin row instead
        xs = [p.GetPosition().x for p in plated]
        ys = [p.GetPosition().y for p in plated]
        pos = pcbnew.VECTOR2I((min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2)
    rot = fp.GetOrientationDegrees()
    fpname = fp.GetFPID().GetLibItemName().wx_str()
    rot += next((r for prefix, r in JLC_ROT if fpname.startswith(prefix)), 0)
    fix = JLC_FIX.get(fpname)
    if fix:
        if fix[0]:
            smd_pads = [p for p in fp.Pads() if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD]
            xs = [p.GetBoundingBox().GetX() for p in smd_pads] + [p.GetBoundingBox().GetRight() for p in smd_pads]
            ys = [p.GetBoundingBox().GetY() for p in smd_pads] + [p.GetBoundingBox().GetBottom() for p in smd_pads]
            pos = pcbnew.VECTOR2I((min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2)
        rot += fix[1]
    rows.append([ref, fp.GetValue(), fp.GetFPID().GetLibItemName().wx_str(),
                 "%.3f" % pcbnew.ToMM(pos.x), "%.3f" % -pcbnew.ToMM(pos.y),
                 "Top" if fp.GetLayer() == pcbnew.F_Cu else "Bottom",
                 "%.1f" % (rot % 360)])
    # one BOM line per orderable part: same LCSC number = same line (e.g. RESET and BOOT switches)
    lcsc = field(fp, "LCSC")
    key = (lcsc,) if lcsc else (fp.GetValue(), fp.GetFPID().GetLibItemName().wx_str(), field(fp, "MPN"))
    g = groups.setdefault(key, {"refs": [], "smd": smd, "vals": [], "fp": fp.GetFPID().GetLibItemName().wx_str(),
                                "lcsc": lcsc, "mpn": field(fp, "MPN")})
    g["refs"].append(ref)
    if fp.GetValue() not in g["vals"]:
        g["vals"].append(fp.GetValue())

with open(os.path.join(out, name + "_CPL_JLCPCB.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["Designator", "Val", "Package", "Mid X", "Mid Y", "Layer", "Rotation"])
    w.writerows(rows)

with open(os.path.join(out, name + "_BOM_JLCPCB.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #", "MPN", "Qty", "Mount"])
    for g in sorted(groups.values(), key=lambda g: g["refs"][0]):
        w.writerow(["/".join(g["vals"]), ",".join(g["refs"]), g["fp"], g["lcsc"], g["mpn"], len(g["refs"]),
                    "SMD" if g["smd"] else "THT"])

missing = [g for g in groups.values() if not g["lcsc"]]
print("CPL rows:", len(rows), " BOM lines:", len(groups), " without LCSC number:", len(missing))
for g in missing:
    print("   ", ",".join(g["refs"]), "/".join(g["vals"]), g["mpn"])
