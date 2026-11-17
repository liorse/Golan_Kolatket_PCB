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
    # keep vias off silkscreen text and logos so the legend stays clean
    silk = []
    for g in board.GetDrawings():
        if g.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS) and (
                isinstance(g, pcbnew.PCB_TEXT) or g.GetShape() == pcbnew.SHAPE_T_POLY):
            bb = g.GetBoundingBox()
            m = mm(via_d / 2 + 0.2)
            silk.append((bb.GetX() - m, bb.GetY() - m, bb.GetRight() + m, bb.GetBottom() + m))

    def clear(pt):
        seg = pcbnew.SEG(pt, pt)
        if not (x0 <= pt.x <= x1 and y0 <= pt.y <= y1):
            return False
        if any(a <= pt.x <= c and b <= pt.y <= d for (a, b, c, d) in silk):
            return False
        for o in obstacles:
            for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
                if o.IsOnLayer(layer) and o.GetEffectiveShape(layer).Collide(seg, keep):
                    return False
            if isinstance(o, pcbnew.PAD) and o.HasHole():
                r = max(o.GetDrillSize().x, o.GetDrillSize().y) / 2
                if (o.GetPosition() - pt).EuclideanNorm() < r + keep + mm(0.25):
                    return False
        return True

    def add_via(pt):
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(pt); v.SetWidth(mm(via_d)); v.SetDrill(mm(drill)); v.SetNet(gnd)
        board.Add(v); obstacles.append(v)

    # one via beside every through-hole GND pad, so its local pour patch reaches the other layer
    import math
    n = 0
    for pad in list(board.GetPads()):
        if pad.GetNetCode() != gnd.GetNetCode() or not pad.HasHole():
            continue
        c = pad.GetPosition()
        for rr in (2.4, 3.0, 3.6, 4.2, 5.0, 6.0):
            done = False
            for a in range(0, 360, 15):
                pt = pcbnew.VECTOR2I(int(c.x + mm(rr) * math.cos(math.radians(a))),
                                     int(c.y + mm(rr) * math.sin(math.radians(a))))
                if clear(pt):
                    add_via(pt); n += 1; done = True
                    break
            if done:
                break
    y = y0
    while y <= y1:
        x = x0
        while x <= x1:
            pt = pcbnew.VECTOR2I(int(x), int(y))
            if clear(pt):
                add_via(pt); n += 1
            x += mm(pitch)
        y += mm(pitch)
    return n


def prune_stitching(board, vias):
    """Remove stitching vias that do not land in the filled GND pour on both layers."""
    gnd_zones = [z for z in board.Zones() if z.GetNetname() == "GND"]
    removed = 0
    for v in vias:
        pos = v.GetPosition()
        ok = all(any(z.IsOnLayer(l) and z.HitTestFilledArea(l, pos) for z in gnd_zones)
                 for l in (pcbnew.F_Cu, pcbnew.B_Cu))
        if not ok:
            board.Remove(v); removed += 1
    return removed


before = {id(t) for t in board.GetTracks()}
n = stitch_gnd(board)
stitch = [t for t in board.GetTracks() if isinstance(t, pcbnew.PCB_VIA) and t.GetNetname() == "GND"
          and t.GetWidth(pcbnew.F_Cu) == pcbnew.FromMM(0.8) and t.GetDrillValue() == pcbnew.FromMM(0.4)]
pcbnew.ZONE_FILLER(board).Fill(board.Zones())
print("GND stitching vias: %d placed, %d removed (outside the pour)" % (n, prune_stitching(board, stitch)))
pcbnew.ZONE_FILLER(board).Fill(board.Zones())
board.Save(pcb)
for f in (dsn, ses):
    os.remove(f)
print("routed:", pcb)
