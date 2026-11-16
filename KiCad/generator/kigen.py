"""
kigen.py - generate a matching KiCad 10 schematic + PCB from a Python netlist.

Each part is declared once (symbol, footprint, PCB position, pin->net map).
The schematic is written as S-expression text, with a net label on every
connected pin and a no-connect flag on every unused pin. The PCB is built with
the pcbnew API and linked to the schematic symbols, so "Update PCB from
Schematic" in KiCad finds no differences.

Run with KiCad's bundled interpreter:
    "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" front_panel.py
"""
import json
import os
import re
import uuid as _uuid

KICAD_SHARE = r"C:\Program Files\KiCad\10.0\share\kicad"
SYM_DIR = os.path.join(KICAD_SHARE, "symbols")
FP_DIR = os.path.join(KICAD_SHARE, "footprints")


def uid():
    return str(_uuid.uuid4())


# --------------------------------------------------------------------------
# Minimal S-expression reader / writer
# --------------------------------------------------------------------------
_TOKEN = re.compile(r'\s*(\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+)', re.S)


class Q(str):
    """A string that must be written quoted."""


def parse_sexpr(text):
    stack, cur = [], []
    for m in _TOKEN.finditer(text):
        t = m.group(1)
        if t == "(":
            stack.append(cur)
            cur = []
        elif t == ")":
            done = cur
            cur = stack.pop()
            cur.append(done)
        elif t.startswith('"'):
            cur.append(Q(t[1:-1].replace('\\"', '"').replace("\\\\", "\\")))
        else:
            cur.append(t)
    return cur


def dump_sexpr(node, indent=0):
    if not isinstance(node, list):
        if isinstance(node, Q):
            return '"' + node.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
        return str(node)
    simple = all(not isinstance(x, list) for x in node)
    if simple:
        return "(" + " ".join(dump_sexpr(x) for x in node) + ")"
    pad = "\t" * (indent + 1)
    parts = []
    for x in node:
        if isinstance(x, list):
            parts.append("\n" + pad + dump_sexpr(x, indent + 1))
        else:
            parts.append((" " if parts else "") + dump_sexpr(x))
    return "(" + "".join(parts) + "\n" + "\t" * indent + ")"


def find(node, key):
    for x in node:
        if isinstance(x, list) and x and x[0] == key:
            return x
    return None


def find_all(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]


# --------------------------------------------------------------------------
# Symbol library access
# --------------------------------------------------------------------------
_lib_cache = {}


def _load_lib(lib):
    if lib not in _lib_cache:
        path = lib if os.path.isabs(lib) else os.path.join(SYM_DIR, lib + ".kicad_sym")
        with open(path, encoding="utf-8") as f:
            tree = parse_sexpr(f.read())[0]
        _lib_cache[lib] = {s[1]: s for s in find_all(tree, "symbol")}
    return _lib_cache[lib]


def _rename_units(sym, old, new):
    for sub in find_all(sym, "symbol"):
        if sub[1].startswith(old + "_"):
            sub[1] = Q(new + sub[1][len(old):])


def get_symbol(lib_id):
    """Return a flattened deep copy of the library symbol named lib_id."""
    import copy
    lib, name = lib_id.split(":")
    syms = _load_lib(LIB_PATHS.get(lib, lib))
    sym = copy.deepcopy(syms[name])
    ext = find(sym, "extends")
    if ext:
        parent = copy.deepcopy(syms[ext[1]])
        _rename_units(parent, ext[1], name)
        parent[1] = Q(name)
        own_props = {p[1]: p for p in find_all(sym, "property")}
        parent[:] = [x for x in parent if not (isinstance(x, list) and x[0] == "property" and x[1] in own_props)]
        idx = max(i for i, x in enumerate(parent) if isinstance(x, list) and x[0] == "property") + 1 \
            if find_all(parent, "property") else 2
        for p in own_props.values():
            parent.insert(idx, p)
        sym = parent
    out = copy.deepcopy(sym)
    out[1] = Q(lib_id)
    return out


LIB_PATHS = {}   # optional: "MyLib" -> absolute path of a project symbol library

# Project footprint libraries shared by both boards (KiCad/lib/*.pretty)
PROJECT_LIB_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
FP_PATHS = {"Peeling": os.path.join(PROJECT_LIB_DIR, "Peeling.pretty")}


def write_fp_lib_table(project_dir):
    rows = "".join('\t(lib (name "%s") (type "KiCad") (uri "${KIPRJMOD}/../lib/%s.pretty") (options "") (descr "Peeling instrument project footprints"))\n'
                   % (n, n) for n in FP_PATHS)
    with open(os.path.join(project_dir, "fp-lib-table"), "w", encoding="utf-8", newline="\n") as f:
        f.write("(fp_lib_table\n\t(version 7)\n" + rows + ")\n")


def symbol_pins(sym):
    """[(number, name, x, y, angle)] for unit 0/1, in symbol coordinates (y up)."""
    pins = []
    for sub in find_all(sym, "symbol"):
        m = re.search(r"_(\d+)_(\d+)$", sub[1])
        if m and int(m.group(1)) > 1:
            continue
        for p in find_all(sub, "pin"):
            at = find(p, "at")
            num = find(p, "number")[1]
            name = find(p, "name")[1]
            pins.append((str(num), str(name), float(at[1]), float(at[2]), float(at[3])))
    return pins


def symbol_bbox(sym):
    xs, ys = [0.0], [0.0]
    for _, _, x, y, _ in symbol_pins(sym):
        xs.append(x); ys.append(y)
    for sub in find_all(sym, "symbol"):
        for r in find_all(sub, "rectangle"):
            s, e = find(r, "start"), find(r, "end")
            xs += [float(s[1]), float(e[1])]; ys += [float(s[2]), float(e[2])]
    return min(xs), min(ys), max(xs), max(ys)


# --------------------------------------------------------------------------
# Design description
# --------------------------------------------------------------------------
class Part:
    def __init__(self, ref, lib_id, value, footprint, nets, pos=None, rot=0.0,
                 side="F", fp_anchor="origin", fields=None, sch=None, group=None):
        """
        nets      : {pin_number: net_name or None(=no-connect)}. Pins not listed -> no-connect.
        footprint : "Lib:Name" or None (schematic-only part)
        pos       : (x, y) mm on the board, relative to the board's top-left corner
        fp_anchor : "origin" -> pos is the footprint origin; (dx, dy) -> pos is that offset point
        """
        self.ref, self.lib_id, self.value, self.footprint = ref, lib_id, value, footprint
        self.nets, self.pos, self.rot, self.side = nets, pos, rot, side
        self.fp_anchor = fp_anchor
        self.fields = fields or {}
        self.sch, self.group = sch, group   # schematic position (mm) and functional block
        self.uuid = uid()


class Design:
    def __init__(self, name, title, rev="A", company="", comments=()):
        self.name, self.title, self.rev, self.company = name, title, rev, company
        self.comments = list(comments)
        self.parts = []
        self.texts = []          # schematic notes: (x, y, text)
        self.groups = []         # schematic blocks: [(key, title)]

    def add(self, *a, **k):
        p = Part(*a, **k)
        self.parts.append(p)
        return p

    def nets(self):
        names = set()
        for p in self.parts:
            names.update(n for n in p.nets.values() if n)
        return sorted(names)


# --------------------------------------------------------------------------
# Schematic writer
# --------------------------------------------------------------------------
def _prop(name, value, x, y, hide=False, angle=0):
    eff = ["effects", ["font", ["size", "1.27", "1.27"]]]
    if hide:
        eff.append(["hide", "yes"])
    return ["property", Q(name), Q(value), ["at", _f(x), _f(y), str(angle)], eff]


def _f(v):
    s = ("%.4f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _snap(v, g=2.54):
    return round(v / g) * g


POWER_SYMBOLS = {"GND": "power:GND", "+3V3": "power:+3V3"}   # nets drawn as power symbols
STUB = 2.54


def _label_len(text):
    return 1.27 * 0.95 * len(text) + 3.0


def write_schematic(design, path):
    """
    Readable schematic: parts are placed at Part.sch (symbol origin, mm on an A3 sheet)
    and grouped by Part.group into titled boxes (design.groups = [(key, title)]).
    Every connected pin gets a short wire stub ending in a global label, or in a
    power symbol for GND / +3V3. Unused pins get a no-connect flag.
    """
    import math
    root_uuid = uid()
    lib_syms, used = [], {}

    def use(lib_id):
        if lib_id not in used:
            used[lib_id] = get_symbol(lib_id)
            lib_syms.append(used[lib_id])
        return used[lib_id]

    def instance(lib_id, ref, value, x, y, rot, props, in_bom=True, on_board=True, u=None):
        sym = use(lib_id)
        inst = ["symbol", ["lib_id", Q(lib_id)], ["at", _f(x), _f(y), str(int(rot))], ["unit", "1"],
                ["exclude_from_sim", "no"], ["in_bom", "yes" if in_bom else "no"],
                ["on_board", "yes" if on_board else "no"], ["dnp", "no"], ["uuid", Q(u or uid())]]
        inst += props
        for num, *_ in symbol_pins(sym):
            inst.append(["pin", Q(num), ["uuid", Q(uid())]])
        inst.append(["instances", ["project", Q(design.name),
                                   ["path", Q("/" + root_uuid), ["reference", Q(ref)], ["unit", "1"]]]])
        return inst

    body, boxes = [], {}
    pwr_n = [0]
    flagged = set()

    def grow(group, x, y):
        b = boxes.setdefault(group, [x, y, x, y])
        b[0], b[1], b[2], b[3] = min(b[0], x), min(b[1], y), max(b[2], x), max(b[3], y)

    def power_symbol(net, x, y, rot, group):
        pwr_n[0] += 1
        # unit vector from the pin toward the symbol body, for each rotation (KiCad rotates CCW)
        body_dir = {0: (0, 1), 90: (1, 0), 180: (0, -1), 270: (-1, 0)}
        if net != "GND":
            body_dir = {r: (-v[0], -v[1]) for r, v in body_dir.items()}
        bx, by = body_dir[rot]
        # value text centred just beyond the symbol (justification would flip with rotation)
        vx, vy = x + bx * (4.0 + 0.6 * len(net)), y + by * 4.0
        val = _prop("Value", net, vx, vy, angle=90 if bx else 0)   # fields rotate with the symbol
        body.append(instance(POWER_SYMBOLS[net], "#PWR%02d" % pwr_n[0], net, x, y, rot,
                             [_prop("Reference", "#PWR%02d" % pwr_n[0], x, y, hide=True), val,
                              _prop("Footprint", "", x, y, hide=True), _prop("Datasheet", "~", x, y, hide=True)],
                             in_bom=False, on_board=False))
        L = _label_len(net) if bx else 3
        grow(group, x - 3 + min(0, bx * (4.5 + L)), y - 3 + min(0, by * 6))
        grow(group, x + 3 + max(0, bx * (4.5 + L)), y + 3 + max(0, by * 6))

    def power(net, x, y, d, group):
        # d = stub direction (unit vector, schematic coords): the symbol body continues along it
        if net == "GND":
            rot = {(0, 1): 0, (1, 0): 90, (0, -1): 180, (-1, 0): 270}[d]
        else:
            rot = {(0, -1): 0, (-1, 0): 90, (0, 1): 180, (1, 0): 270}[d]
        power_symbol(net, x, y, rot, group)
    # auto-place anything without an explicit position, below the sheet content
    auto_x = 30.48
    for p in design.parts:
        if getattr(p, "sch", None) is None:
            p.sch = (auto_x, 270.0); auto_x += 25.4

    for p in design.parts:
        sym = use(p.lib_id)
        group = getattr(p, "group", None) or "_misc"
        sx, sy = _snap(p.sch[0]), _snap(p.sch[1])
        bx0, by0, bx1, by1 = symbol_bbox(sym)
        pins = symbol_pins(sym)
        grow(group, sx + bx0, sy - by1); grow(group, sx + bx1, sy - by0)
        vertical = pins and all(int(a) in (90, 270) for *_, a in pins)
        if vertical:
            r = _prop("Reference", p.ref, sx + 3.81, sy - 1.27); r[-1].append(["justify", "left"])
            v = _prop("Value", p.value, sx + 3.81, sy + 1.27); v[-1].append(["justify", "left"])
            fields = [r, v]
            grow(group, sx + 3.81 + _label_len(p.value), sy)
        elif not pins:
            fields = [_prop("Reference", p.ref, sx, sy - 3.81), _prop("Value", p.value, sx, sy + 3.81)]
            grow(group, sx - _label_len(p.value) / 2, sy + 4); grow(group, sx + _label_len(p.value) / 2, sy - 4)
        else:
            # use the library symbol's own Reference/Value placement (it clears the body graphics)
            fields = []
            for key, text in (("Reference", p.ref), ("Value", p.value)):
                lp = next(x for x in find_all(sym, "property") if x[1] == key)
                at = find(lp, "at")
                fx, fy = sx + float(at[1]), sy - float(at[2])
                f = _prop(key, text, fx, fy, angle=int(float(at[3])) if len(at) > 3 else 0)
                eff = find(lp, "effects")
                j = find(eff, "justify") if eff else None
                if j:
                    f[-1].append(list(j))
                fields.append(f)
                grow(group, fx - _label_len(text) / 2, fy - 1.5); grow(group, fx + _label_len(text) / 2, fy + 1.5)
        fields.append(_prop("Footprint", p.footprint or "", sx, sy, hide=True))
        fields.append(_prop("Datasheet", p.fields.get("Datasheet", "~"), sx, sy, hide=True))
        for k, v in p.fields.items():
            if k != "Datasheet":
                fields.append(_prop(k, v, sx, sy, hide=True))
        body.append(instance(p.lib_id, p.ref, p.value, sx, sy, 0, fields,
                             in_bom=not p.lib_id.startswith("Mechanical:"), on_board=bool(p.footprint),
                             u=p.uuid))

        for num, name, px, py, ang in pins:
            ax, ay = sx + px, sy - py
            net = p.nets.get(num)
            if not net:
                body.append(["no_connect", ["at", _f(ax), _f(ay)], ["uuid", Q(uid())]])
                continue
            a = math.radians(ang)
            d = (round(-math.cos(a)), round(math.sin(a)))
            ex, ey = ax + STUB * d[0], ay + STUB * d[1]
            body.append(["wire", ["pts", ["xy", _f(ax), _f(ay)], ["xy", _f(ex), _f(ey)]],
                         ["stroke", ["width", "0"], ["type", "default"]], ["uuid", Q(uid())]])
            if net in POWER_SYMBOLS:
                power(net, ex, ey, d, group)
                continue
            la = int((ang + 180) % 360)
            just = "left" if la in (0, 90) else "right"
            body.append(["global_label", Q(net), ["shape", "passive"],
                         ["at", _f(ex), _f(ey), str(la)], ["fields_autoplaced", "yes"],
                         ["effects", ["font", ["size", "1.27", "1.27"]], ["justify", just]],
                         ["uuid", Q(uid())],
                         ["property", Q("Intersheetrefs"), Q("${INTERSHEET_REFS}"),
                          ["at", _f(ex), _f(ey), "0"],
                          ["effects", ["font", ["size", "1.27", "1.27"]], ["hide", "yes"]]]])
            L = _label_len(net)
            grow(group, ex + L * d[0], ey + L * d[1] - 1.5); grow(group, ex + L * d[0], ey + L * d[1] + 1.5)

    fx, fy = getattr(design, "flag_pos", (30.48, 30.48))
    fgroup = getattr(design, "flag_group", "_misc")
    for i, net in enumerate(sorted(n for n in POWER_SYMBOLS if any(net == n for p in design.parts for net in p.nets.values()))):
        x, y = _snap(fx + 15.24 * i), _snap(fy)
        power_symbol(net, x, y, 0, fgroup)
        frot = 0 if net == "GND" else 180
        body.append(instance("power:PWR_FLAG", "#FLG%02d" % (i + 1), "PWR_FLAG", x, y, frot,
                             [_prop("Reference", "#FLG%02d" % (i + 1), x, y, hide=True),
                              _prop("Value", "PWR_FLAG", x, y + (-5.08 if frot == 0 else 5.08)),
                              _prop("Footprint", "", x, y, hide=True), _prop("Datasheet", "~", x, y, hide=True)],
                             in_bom=False, on_board=False))
        grow(fgroup, x - 6, y - 7); grow(fgroup, x + 6, y + 7)

    # group boxes with titles
    titles = dict(getattr(design, "groups", []))
    for key, (x0, y0, x1, y1) in boxes.items():
        if key == "_misc":
            continue
        m = 3.81
        x0, y0, x1, y1 = x0 - m, y0 - m - 2.54, x1 + m, y1 + m
        x1 = max(x1, x0 + 2.0 * 0.95 * len(titles.get(key, key)) + 3.0)    # fit the title
        body.append(["rectangle", ["start", _f(x0), _f(y0)], ["end", _f(x1), _f(y1)],
                     ["stroke", ["width", "0"], ["type", "dash"]], ["fill", ["type", "none"]],
                     ["uuid", Q(uid())]])
        body.append(["text", Q(titles.get(key, key)), ["exclude_from_sim", "no"],
                     ["at", _f(x0 + 1.27), _f(y0 + 1.27), "0"],
                     ["effects", ["font", ["size", "2", "2"], ["thickness", "0.4"], ["bold", "yes"]],
                      ["justify", "left", "top"]],
                     ["uuid", Q(uid())]])

    for (tx, ty, text) in design.texts:
        body.append(["text", Q(text), ["exclude_from_sim", "no"], ["at", _f(tx), _f(ty), "0"],
                     ["effects", ["font", ["size", "1.524", "1.524"]], ["justify", "left", "top"]],
                     ["uuid", Q(uid())]])

    tb = ["title_block", ["title", Q(design.title)], ["rev", Q(design.rev)], ["company", Q(design.company)]]
    for i, c in enumerate(design.comments[:9], 1):
        tb.append(["comment", str(i), Q(c)])
    doc = ["kicad_sch", ["version", "20250114"], ["generator", Q("eeschema")], ["generator_version", Q("9.0")],
           ["uuid", Q(root_uuid)], ["paper", Q("A3")], tb, ["lib_symbols"] + lib_syms] + body + \
          [["sheet_instances", ["path", Q("/"), ["page", Q("1")]]], ["embedded_fonts", "no"]]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(dump_sexpr(doc) + "\n")
    return root_uuid


def write_sym_lib_table(project_dir):
    with open(os.path.join(project_dir, "sym-lib-table"), "w", encoding="utf-8", newline="\n") as f:
        f.write('(sym_lib_table\n\t(version 7)\n'
                '\t(lib (name "Peeling") (type "KiCad") (uri "${KIPRJMOD}/../lib/Peeling.kicad_sym") (options "") (descr "Peeling instrument project symbols"))\n)\n')


# --------------------------------------------------------------------------
# Project file
# --------------------------------------------------------------------------
def write_project(design, path, netclasses, patterns):
    """netclasses: {name: dict(track_width=, clearance=, via_diameter=, via_drill=)}"""
    classes = []
    for i, (name, nc) in enumerate(netclasses.items()):
        classes.append({
            "name": name, "priority": 2147483647 if name == "Default" else i,
            "clearance": nc.get("clearance", 0.2), "track_width": nc["track_width"],
            "via_diameter": nc.get("via_diameter", 0.8), "via_drill": nc.get("via_drill", 0.4),
            "microvia_diameter": 0.3, "microvia_drill": 0.1,
            "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
            "wire_width": 6, "bus_width": 12, "line_style": 0,
            "schematic_color": "rgba(0, 0, 0, 0.000)", "pcb_color": "rgba(0, 0, 0, 0.000)",
        })
    proj = {
        "board": {"design_settings": {
            "defaults": {"board_outline_line_width": 0.1, "copper_line_width": 0.2,
                         "silk_line_width": 0.12, "silk_text_size_h": 1.0, "silk_text_size_v": 1.0,
                         "silk_text_thickness": 0.15},
            "rules": {"min_clearance": 0.2, "min_track_width": 0.2, "min_via_diameter": 0.6,
                      "min_through_hole_diameter": 0.3, "min_copper_edge_clearance": 0.4,
                      "min_hole_clearance": 0.25, "min_hole_to_hole": 0.25,
                      "min_silk_clearance": 0.0, "min_text_height": 0.8, "min_text_thickness": 0.08},
            "track_widths": [0.0, 0.3, 0.5, 0.8, 1.5, 2.5],
            "via_dimensions": [{"diameter": 0.0, "drill": 0.0}, {"diameter": 0.8, "drill": 0.4},
                               {"diameter": 1.2, "drill": 0.6}],
        }},
        "net_settings": {"classes": classes, "meta": {"version": 4},
                         "netclass_patterns": [{"netclass": c, "pattern": p} for p, c in patterns]},
        "meta": {"filename": os.path.basename(path), "version": 3},
        "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
        "sheets": [], "text_variables": {},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(proj, f, indent=2)
    # Custom DRC rules: every GND pad is also routed with tracks by the autorouter,
    # so a single thermal spoke into the GND pour is sufficient.
    with open(os.path.splitext(path)[0] + ".kicad_dru", "w", encoding="utf-8", newline="\n") as f:
        f.write("(version 1)\n"
                "(rule \"GND thermal spokes\"\n"
                "\t(condition \"A.Type == 'Pad'\")\n"
                "\t(constraint min_resolved_spokes (min 1)))\n")


# --------------------------------------------------------------------------
# PCB builder (requires pcbnew)
# --------------------------------------------------------------------------
def build_pcb(design, path, outline, origin=(100.0, 50.0), zones=True, corner_radius=0.0,
              silk_texts=(), keepouts=(), silk_rects=()):
    import pcbnew
    mm = pcbnew.FromMM
    V = lambda x, y: pcbnew.VECTOR2I(mm(origin[0] + x), mm(origin[1] + y))

    board = pcbnew.NewBoard(path)
    board.SetCopperLayerCount(2)
    tb = board.GetTitleBlock()
    tb.SetTitle(design.title); tb.SetRevision(design.rev); tb.SetCompany(design.company)
    for i, c in enumerate(design.comments[:9]):
        tb.SetComment(i, c)

    # Unused symbol pins get KiCad's own "unconnected-(REF-PIN-PadN)" net names,
    # which is what Update-PCB-from-Schematic would assign.
    for p in design.parts:
        p._pad_nets = dict(p.nets)
        if not p.footprint:
            continue
        for num, name, _, _, _ in symbol_pins(get_symbol(p.lib_id)):
            if not p.nets.get(num):
                label = ("%s-%s-Pad%s" % (p.ref, name, num)) if name not in ("~", "") else ("%s-Pad%s" % (p.ref, num))
                p._pad_nets[num] = "unconnected-(%s)" % label.replace("/", "{slash}")

    nets = {}
    all_nets = sorted({n for p in design.parts for n in p._pad_nets.values() if n})
    for n in all_nets:
        ni = pcbnew.NETINFO_ITEM(board, n)
        board.Add(ni)
        nets[n] = ni

    sch_file = design.name + ".kicad_sch"
    for p in design.parts:
        if not p.footprint:
            continue
        lib, name = p.footprint.split(":")
        fp = pcbnew.FootprintLoad(FP_PATHS.get(lib, os.path.join(FP_DIR, lib + ".pretty")), name)
        if fp is None:
            raise RuntimeError("footprint not found: " + p.footprint)
        fp.SetFPID(pcbnew.LIB_ID(lib, name))
        fp.SetReference(p.ref)
        fp.SetValue(p.value)
        for k, v in p.fields.items():          # keep footprint fields in sync with the symbol
            if k != "Datasheet":
                fp.SetField(k, v)
        if p.lib_id.startswith("Mechanical:"):
            fp.Reference().SetVisible(False)
        fp.SetPath(pcbnew.KIID_PATH("/" + p.uuid))
        try:
            fp.SetSheetfile(sch_file); fp.SetSheetname("")
        except AttributeError:
            pass
        board.Add(fp)
        if p.side == "B":
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetOrientationDegrees(p.rot)
        x, y = p.pos
        if p.fp_anchor != "origin":
            # pos names a point that sits at fp_anchor in footprint coordinates
            dx, dy = p.fp_anchor
            if p.side == "B":
                dx = -dx
            import math
            a = math.radians(-p.rot)
            rx, ry = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
            x, y = x - rx, y - ry
        fp.SetPosition(V(x, y))
        for pad in fp.Pads():
            n = p._pad_nets.get(pad.GetNumber())
            if n:
                pad.SetNet(nets[n])

    # ---- outline
    w, h = outline
    shape = pcbnew.PCB_SHAPE(board)
    shape.SetShape(pcbnew.SHAPE_T_RECTANGLE if hasattr(pcbnew, "SHAPE_T_RECTANGLE") else pcbnew.SHAPE_T_RECT)
    shape.SetStart(V(0, 0)); shape.SetEnd(V(w, h))
    shape.SetLayer(pcbnew.Edge_Cuts); shape.SetWidth(mm(0.1))
    if corner_radius:
        try:
            shape.SetCornerRadius(mm(corner_radius))
        except AttributeError:
            pass
    board.Add(shape)

    # silk text: (x, y, text, size, layer[, angle_deg[, "left"|"center"|"right"]])
    aligns = {"left": pcbnew.GR_TEXT_H_ALIGN_LEFT, "center": pcbnew.GR_TEXT_H_ALIGN_CENTER,
              "right": pcbnew.GR_TEXT_H_ALIGN_RIGHT}
    for item in silk_texts:
        (x, y, text, size, layer), extra = item[:5], item[5:]
        t = pcbnew.PCB_TEXT(board)
        t.SetText(text); t.SetPosition(V(x, y)); t.SetLayer(layer)
        t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size))); t.SetTextThickness(mm(size * 0.15))
        if len(extra) > 0:
            t.SetTextAngleDegrees(extra[0])
        if len(extra) > 1:
            t.SetHorizJustify(aligns[extra[1]])
        if layer == pcbnew.B_SilkS:
            t.SetMirrored(True)
        board.Add(t)

    for (x0, y0, x1, y1, layer) in silk_rects:
        r = pcbnew.PCB_SHAPE(board)
        r.SetShape(pcbnew.SHAPE_T_RECTANGLE if hasattr(pcbnew, "SHAPE_T_RECTANGLE") else pcbnew.SHAPE_T_RECT)
        r.SetStart(V(x0, y0)); r.SetEnd(V(x1, y1)); r.SetLayer(layer); r.SetWidth(mm(0.12))
        board.Add(r)

    for (x0, y0, x1, y1, layers) in keepouts:
        z = pcbnew.ZONE(board)
        z.SetIsRuleArea(True)
        z.SetDoNotAllowTracks(True); z.SetDoNotAllowVias(True); z.SetDoNotAllowZoneFills(True)
        z.SetDoNotAllowPads(False); z.SetDoNotAllowFootprints(False)
        ls = pcbnew.LSET()
        for l in layers:
            ls.AddLayer(l)
        z.SetLayerSet(ls)
        o = z.Outline(); o.NewOutline()
        for (px, py) in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            o.Append(V(px, py))
        board.Add(z)

    if zones and "GND" in nets:
        for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
            z = pcbnew.ZONE(board)
            z.SetLayer(layer); z.SetNet(nets["GND"])
            z.SetLocalClearance(mm(0.3)); z.SetMinThickness(mm(0.25))
            z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
            z.SetThermalReliefGap(mm(0.4)); z.SetThermalReliefSpokeWidth(mm(0.5))
            o = z.Outline(); o.NewOutline()
            for (px, py) in ((0, 0), (w, 0), (w, h), (0, h)):
                o.Append(V(px, py))
            board.Add(z)

    board.Save(path)
    return board


def fill_and_save(path):
    import pcbnew
    board = pcbnew.LoadBoard(path)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(path)
