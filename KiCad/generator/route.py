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
pcbnew.ZONE_FILLER(board).Fill(board.Zones())
board.Save(pcb)
for f in (dsn, ses):
    os.remove(f)
print("routed:", pcb)
