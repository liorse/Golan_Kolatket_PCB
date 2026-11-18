"""
Autoroute a board with Freerouting, then refill zones.

    python.exe route.py <board.kicad_pcb> <java.exe> <freerouting.jar> [passes] [extra freerouting options]

Progress: Freerouting logs every pass to %TEMP%/freerouting/freerouting.log.

Flow: pcbnew -> Specctra DSN -> Freerouting (headless) -> SES -> pcbnew -> zone fill.
"""
import os, subprocess, sys
import pcbnew

# --plane-nets=GND,+3V3 : these nets are not autorouted. Every SMD pad on them gets a short track +
# via into its plane / pour before routing (the router sees them as fixed obstacles), so the router
# only handles signals. --gnd-pour is short for --plane-nets=GND. Off by default.
PLANE_NETS = []
for a in sys.argv:
    if a.startswith("--plane-nets="):
        PLANE_NETS += [n for n in a.split("=", 1)[1].split(",") if n]
    elif a == "--gnd-pour":
        PLANE_NETS.append("GND")
# --stub-nets=+24V : SMD pads on these nets narrower than 0.6 mm (fine-pitch IC pins) get a short
# narrow track + via before routing, locked, so the router can reach them with wide tracks.
STUB_NETS = [n for a in sys.argv if a.startswith("--stub-nets=") for n in a.split("=", 1)[1].split(",") if n]
# --stub-pads=C64:1,... : the same locked track + via for these particular pads (gives an isolated
# group of a net an entry point to the other layer)
STUB_PADS = [tuple(x.split(":")) for a in sys.argv if a.startswith("--stub-pads=") for x in a.split("=", 1)[1].split(",") if x]
args = [a for a in sys.argv if not (a.startswith(("--plane-nets=", "--ses=", "--stub-nets=", "--stub-pads=")) or a == "--gnd-pour")]
pcb, java, jar = args[1:4]
passes = args[4] if len(args) > 4 else "100"
base = os.path.splitext(pcb)[0]
dsn, ses = base + ".dsn", base + ".ses"


def _drop_blocks(text, head):
    """Remove every balanced S-expression starting with `head` (e.g. '(net GND')."""
    out, i = [], 0
    while True:
        j = text.find(head, i)
        if j < 0 or text[j + len(head)] not in " \n\r\t)":
            if j < 0:
                out.append(text[i:])
                return "".join(out)
            out.append(text[i:j + len(head)]); i = j + len(head); continue
        out.append(text[i:j])
        depth, k = 0, j
        while True:
            if text[k] == "(":
                depth += 1
            elif text[k] == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        i = k + 1


def net_item(board, name):
    """The NETINFO_ITEM of a net, taken from one of its pads (board.FindNet sometimes hands back an
    untyped SWIG object after many track removals)."""
    return next((p.GetNet() for p in board.GetPads() if p.GetNetname() == name), None)


FANOUT_RECORD = []      # (net, pad position, via position, via_d, drill, width) for exact re-creation


def fanout(board, nets, via_d=0.6, drill=0.3, width=0.4, clearance=0.2, max_pad=None, lock=False, only=None):
    """A short F.Cu track + via from every SMD pad on `nets` into that net's plane / pour. Pads whose
    footprint already carries thermal vias on the same pad number (exposed pads) are skipped.
    Deterministic: the same board gives the same via positions."""
    import math
    mm = pcbnew.FromMM
    codes = {board.GetNetcodeFromNetname(n) for n in nets} - {-1}
    bb = board.GetBoardEdgesBoundingBox()
    edge = mm(0.6)
    added, placed = 0, []
    holes = [p for p in board.GetPads() if p.HasHole()]
    for pad in sorted(board.GetPads(), key=lambda p: (p.GetParentFootprint().GetReference(), p.GetNumber())):
        code = pad.GetNetCode()
        if code not in codes or pad.HasHole() or not pad.IsOnLayer(pcbnew.F_Cu):
            continue
        if max_pad and min(pad.GetSize().x, pad.GetSize().y) >= mm(max_pad):
            continue
        if only is not None and (pad.GetParentFootprint().GetReference(), pad.GetNumber()) not in only:
            continue
        fp = pad.GetParentFootprint()
        if any(p.HasHole() and p.GetNumber() == pad.GetNumber() for p in fp.Pads()):
            continue
        obstacles = [p for p in board.GetPads() if p.GetNetCode() != code] +                     [t for t in board.GetTracks() if t.GetNetCode() != code]
        c = pad.GetPosition()
        done = False
        # first choice: a straight track into a bigger pad of the same net in the same footprint
        # (e.g. a TMC2226 GND pin into its own exposed pad, which carries thermal vias) - keeps the
        # chip's escape routes free of vias
        for big in fp.Pads():
            if big.GetNetCode() != code or big is pad or big.GetSize().x * big.GetSize().y <= 2 * pad.GetSize().x * pad.GetSize().y:
                continue
            bb2 = big.GetBoundingBox()
            if not (bb2.GetY() < c.y < bb2.GetBottom() or bb2.GetX() < c.x < bb2.GetRight()):
                continue
            if bb2.GetY() < c.y < bb2.GetBottom():
                end = pcbnew.VECTOR2I(max(bb2.GetX() + mm(0.3), min(c.x, bb2.GetRight() - mm(0.3))), c.y)
            else:
                end = pcbnew.VECTOR2I(c.x, max(bb2.GetY() + mm(0.3), min(c.y, bb2.GetBottom() - mm(0.3))))
            seg = pcbnew.SEG(c, end)
            if any(o.IsOnLayer(pcbnew.F_Cu) and o.GetEffectiveShape(pcbnew.F_Cu).Collide(seg, mm(0.15 + clearance))
                   for o in obstacles):
                continue
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(c); t.SetEnd(end); t.SetWidth(mm(0.3)); t.SetLayer(pcbnew.F_Cu); t.SetNet(pad.GetNet())
            board.Add(t)
            FANOUT_RECORD.append((pad.GetNetname(), c, end, None, None, 0.3))
            added += 1; done = True
            break
        for rr in (1.0, 1.3, 1.6, 2.0, 2.5, 3.0) if not done else ():
            for a in (270, 90, 0, 180) + tuple(range(15, 360, 15)):
                pt = pcbnew.VECTOR2I(int(c.x + mm(rr) * math.cos(math.radians(a))),
                                     int(c.y + mm(rr) * math.sin(math.radians(a))))
                if not (bb.GetX() + edge < pt.x < bb.GetRight() - edge and bb.GetY() + edge < pt.y < bb.GetBottom() - edge):
                    continue
                via_seg, trk = pcbnew.SEG(pt, pt), pcbnew.SEG(c, pt)
                ok = all((q - pt).EuclideanNorm() > mm(via_d + 0.35) for q in placed)
                # keep clear of every drilled hole, same net included (hole-to-hole rule)
                ok = ok and all((h.GetPosition() - pt).EuclideanNorm() >
                                max(h.GetDrillSize().x, h.GetDrillSize().y) / 2 + mm(drill / 2 + 0.35)
                                for h in holes)
                for o in obstacles if ok else ():
                    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
                        if not o.IsOnLayer(layer):
                            continue
                        sh = o.GetEffectiveShape(layer)
                        if sh.Collide(via_seg, mm(via_d / 2 + clearance)) or                                 (layer == pcbnew.F_Cu and sh.Collide(trk, mm(width / 2 + clearance))):
                            ok = False
                            break
                    if not ok:
                        break
                    if isinstance(o, pcbnew.PAD) and o.HasHole() and                             (o.GetPosition() - pt).EuclideanNorm() < max(o.GetDrillSize().x, o.GetDrillSize().y) / 2 + mm(via_d / 2 + 0.3):
                        ok = False
                        break
                if not ok:
                    continue
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(pt); v.SetWidth(mm(via_d)); v.SetDrill(mm(drill)); v.SetNet(pad.GetNet())
                board.Add(v)
                t = pcbnew.PCB_TRACK(board)
                t.SetStart(c); t.SetEnd(pt); t.SetWidth(mm(width)); t.SetLayer(pcbnew.F_Cu); t.SetNet(pad.GetNet())
                board.Add(t)
                if lock:
                    v.SetLocked(True); t.SetLocked(True)
                placed.append(pt)
                FANOUT_RECORD.append((pad.GetNetname(), c, pt, via_d, drill, width))
                added += 1; done = True
                break
            if done:
                break
        if not done:
            print("no fanout room for %s pad %s-%s" % (pad.GetNetname(), fp.GetReference(), pad.GetNumber()))
    return added


board = pcbnew.LoadBoard(pcb)
# Freerouting ignores zones; remove existing tracks so reruns start clean
for t in list(board.GetTracks()):
    board.Remove(t)
if PLANE_NETS:
    print("plane-net fanout vias:", fanout(board, PLANE_NETS))
if STUB_NETS:
    print("fine-pitch stubs:", fanout(board, STUB_NETS, width=0.3, max_pad=0.6, lock=True))
if STUB_PADS:
    pads = {p for p in STUB_PADS}
    nets = {p.GetNetname() for p in board.GetPads() if (p.GetParentFootprint().GetReference(), p.GetNumber()) in pads}
    # the pad search must not take the exposed-pad shortcut: only real vias count here
    print("pad stubs:", fanout(board, sorted(nets), lock=True, only=pads))
if not pcbnew.ExportSpecctraDSN(board, dsn):
    sys.exit("DSN export failed")
if PLANE_NETS:
    import re
    t = open(dsn, encoding="utf-8").read()
    for net in PLANE_NETS:
        q = re.escape(net)
        t = _drop_blocks(t, "(plane %s" % net)
        # the network entry and any pre-placed wiring of the net: the fanout wires / vias drop out
        # of the DSN and are re-checked after import; the router keeps clear of their pads anyway
        t = _drop_blocks(t, "(net %s" % net)
        t = re.sub(r"(\(class [^()]*?)\s%s(?=[\s)])" % q, lambda m: m.group(1), t)
    open(dsn, "w", encoding="utf-8").write(t)

USE_SES = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--ses=")), None)
if USE_SES:                       # import a session routed earlier from the same DSN instead
    ses = USE_SES
else:
    if os.path.exists(ses):
        os.remove(ses)
    cmd = [java, "-jar", jar, "-de", dsn, "-do", ses, "-mp", passes]
    if "1.9" not in os.path.basename(jar):          # Freerouting 2.x options
        cmd += ["--gui.enabled=false", "--api_server.enabled=false",
                "--usage_and_diagnostic_data.disable_analytics=true"]
    cmd += [a for a in args[5:] if not a.startswith("--ses=")]   # extra Freerouting options
    print(" ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    tail = (r.stdout + r.stderr).splitlines()
    print("\n".join(l for l in tail if "INFO" in l or "WARN" in l)[-3000:])
    if not os.path.exists(ses):
        sys.exit("Freerouting produced no session file")

if not pcbnew.ImportSpecctraSES(board, ses):
    sys.exit("SES import failed")
if PLANE_NETS:
    codes = {board.GetNetcodeFromNetname(n) for n in PLANE_NETS} - {-1}
    for t in list(board.GetTracks()):
        if t.GetNetCode() == 0:                     # net-less copies of the fixed fanout, if any
            board.Remove(t)
    if not any(t.GetNetCode() in codes for t in board.GetTracks()):
        # re-create the plane-net fanout exactly where it was (the router kept those spots clear)
        mm = pcbnew.FromMM
        n = 0
        for net, a, b, via_d, drill, width in FANOUT_RECORD:
            if net not in PLANE_NETS:
                continue
            ni = net_item(board, net)
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(a); t.SetEnd(b); t.SetWidth(mm(width)); t.SetLayer(pcbnew.F_Cu); t.SetNet(ni)
            board.Add(t)
            if via_d:
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(b); v.SetWidth(mm(via_d)); v.SetDrill(mm(drill)); v.SetNet(ni)
                board.Add(v)
            n += 1
        print("re-created plane-net fanout:", n)


def stitch_gnd(board, pitch=7.5, via_d=0.8, drill=0.4, clearance=0.35, edge=2.0):
    """Drop GND vias on a grid wherever they clear all other copper, tying the F.Cu and
    B.Cu GND pours together (no stranded islands around GND pads)."""
    mm = pcbnew.FromMM
    gnd = net_item(board, "GND")
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


def snap_endpoints(board, tol=0.1):
    """Join track ends of the same net that the session import left a hair apart (rounding)."""
    tol = pcbnew.FromMM(tol)
    tracks = [t for t in board.GetTracks() if not isinstance(t, pcbnew.PCB_VIA)]
    anchors = {}
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            anchors.setdefault(t.GetNetCode(), []).append(t.GetPosition())
    for p in board.GetPads():
        anchors.setdefault(p.GetNetCode(), []).append(p.GetPosition())
    for t in tracks:
        anchors.setdefault(t.GetNetCode(), []).extend([t.GetStart(), t.GetEnd()])
    moved = 0
    for t in tracks:
        for getter, setter in ((t.GetStart, t.SetStart), (t.GetEnd, t.SetEnd)):
            p = getter()
            for q in anchors.get(t.GetNetCode(), []):
                d = (q - p).EuclideanNorm()
                if 0 < d <= tol:
                    setter(pcbnew.VECTOR2I(q.x, q.y)); moved += 1
                    break
    return moved



before = {id(t) for t in board.GetTracks()}
n = stitch_gnd(board)
stitch = [t for t in board.GetTracks() if isinstance(t, pcbnew.PCB_VIA) and t.GetNetname() == "GND"
          and t.GetWidth(pcbnew.F_Cu) == pcbnew.FromMM(0.8) and t.GetDrillValue() == pcbnew.FromMM(0.4)]
pcbnew.ZONE_FILLER(board).Fill(board.Zones())
print("GND stitching vias: %d placed, %d removed (outside the pour)" % (n, prune_stitching(board, stitch)))
pcbnew.ZONE_FILLER(board).Fill(board.Zones())
board.Save(pcb)
for f in (dsn,) if USE_SES else (dsn, ses):
    os.remove(f)
print("routed:", pcb)
