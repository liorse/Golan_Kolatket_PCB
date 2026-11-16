"""
Autoroute a board with Freerouting, then refill zones.

    python.exe route.py <board.kicad_pcb> <java.exe> <freerouting.jar> [passes]

Flow: pcbnew -> Specctra DSN -> Freerouting (headless) -> SES -> pcbnew -> zone fill.
"""
import os, subprocess, sys
import pcbnew

pcb, java, jar = sys.argv[1:4]
passes = sys.argv[4] if len(sys.argv) > 4 else "100"
base = os.path.splitext(pcb)[0]
dsn, ses = base + ".dsn", base + ".ses"

board = pcbnew.LoadBoard(pcb)
# Freerouting ignores zones; remove existing tracks so reruns start clean
for t in list(board.GetTracks()):
    board.Remove(t)
if not pcbnew.ExportSpecctraDSN(board, dsn):
    sys.exit("DSN export failed")

if os.path.exists(ses):
    os.remove(ses)
cmd = [java, "-jar", jar, "-de", dsn, "-do", ses, "-mp", passes,
       "--gui.enabled=false", "--api_server.enabled=false",
       "--usage_and_diagnostic_data.disable_analytics=true"]
print(" ".join(cmd))
r = subprocess.run(cmd, capture_output=True, text=True)
tail = (r.stdout + r.stderr).splitlines()
print("\n".join(l for l in tail if "INFO" in l or "WARN" in l or "ERROR" in l)[-3000:])
if not os.path.exists(ses):
    sys.exit("Freerouting produced no session file")

if not pcbnew.ImportSpecctraSES(board, ses):
    sys.exit("SES import failed")


def stitch_gnd(board, pitch=7.5, via_d=0.8, drill=0.4, clearance=0.35, edge=2.0):
    """Drop GND vias on a grid wherever they clear all other copper, tying the F.Cu and
    B.Cu GND pours together (no stranded islands around GND pads)."""
    mm = pcbnew.FromMM
    gnd = board.FindNet("GND")
    if not gnd:
        return 0
    bb = board.GetBoardEdgesBoundingBox()
    x0, y0, x1, y1 = bb.GetX() + mm(edge), bb.GetY() + mm(edge), bb.GetRight() - mm(edge), bb.GetBottom() - mm(edge)
    obstacles = [p for p in board.GetPads()] + [t for t in board.GetTracks()]
    keep = mm(via_d / 2 + clearance)
    n, y = 0, y0
    while y <= y1:
        x = x0
        while x <= x1:
            pt = pcbnew.VECTOR2I(int(x), int(y))
            seg = pcbnew.SEG(pt, pt)
            hit = False
            for o in obstacles:
                # copper on either layer (any net) needs full clearance
                for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
                    if o.IsOnLayer(layer) and o.GetEffectiveShape(layer).Collide(seg, keep):
                        hit = True
                        break
                # drill holes too (mounting holes have no copper)
                if not hit and isinstance(o, pcbnew.PAD) and o.HasHole():
                    r = max(o.GetDrillSize().x, o.GetDrillSize().y) / 2
                    if (o.GetPosition() - pt).EuclideanNorm() < r + keep + mm(0.25):
                        hit = True
                if hit:
                    break
            if not hit:
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(pt); v.SetWidth(mm(via_d)); v.SetDrill(mm(drill)); v.SetNet(gnd)
                board.Add(v); obstacles.append(v); n += 1
            x += mm(pitch)
        y += mm(pitch)
    return n


print("GND stitching vias:", stitch_gnd(board))
pcbnew.ZONE_FILLER(board).Fill(board.Zones())
board.Save(pcb)
for f in (dsn, ses):
    os.remove(f)
print("routed:", pcb)
