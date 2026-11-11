"""Whiteboard für Gäste: alle im AluPC-WLAN (oder nur mit Erlaubnis am PC) schreiben auf die Tafel auf Monitor 2.

Einstellung (Whiteboard → „Gäste im AluPC-WLAN“): „aus“ · „alle“ · „frage“ (erst am PC erlauben). Gäste öffnen auf
der Seite /tafel (QR-Code unten rechts auf der Tafel), sehen das Live-Bild der Tafel und zeichnen mit dem Finger/Stift. Jeder Strich kommt
als Ganzes (bei langen Strichen in Stücken) – so stören sich mehrere Gäste gleichzeitig nicht. Radieren, Seiten,
Rückgängig bleiben beim PC und bei freigegebenen Steuer-Handys.
"""

from __future__ import annotations

import re
import threading
import time

MODES = {"aus": "Aus – nur PC und Steuer-Handys", "alle": "Alle im AluPC-WLAN dürfen schreiben",
         "frage": "Nur nach Erlaubnis am PC"}
TOOLS = ("pen", "marker")
MAX_POINTS = 400  # je Anfrage
MAX_PER_SECOND = 12  # Anfragen je Gerät


def clean_name(name: str) -> str:
    return re.sub(r"[^\w .\-'äöüÄÖÜß]", "", str(name or ""))[:20].strip() or "Gast"


class BoardGuests:
    """Wer darf (je Gerät eine zufällige ID aus dem Browser), offene Fragen, Bremse gegen Spam. Thread-sicher."""

    def __init__(self):
        self.lock = threading.Lock()
        self.allowed: dict[str, str] = {}  # Geräte-ID → Name (am PC erlaubt)
        self.denied: set[str] = set()
        self.waiting: dict[str, dict] = {}  # Geräte-ID → {"name", "ip", "t"}
        self.names: dict[str, str] = {}
        self._hits: dict[str, list[float]] = {}
        self._mode = ""  # Modus gewechselt → alte Erlaubnisse/Fragen gelten nicht mehr

    @staticmethod
    def valid_id(gid: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9_-]{8,40}", str(gid or "")))


    def state(self, mode: str, gid: str) -> str:
        """„aus“, „frei“ (darf schreiben), „warten“, „nein“, „fragen“ (noch nicht gefragt)."""
        if mode != self._mode:
            self.reset()
            self._mode = mode
        if mode not in ("alle", "frage"):
            return "aus"
        if mode == "alle":
            return "frei"
        with self.lock:
            if gid in self.allowed:
                return "frei"
            if gid in self.denied:
                return "nein"
            if gid in self.waiting:
                return "warten"
        return "fragen"

    def ask(self, gid: str, name: str, ip: str) -> bool:
        """Gast bittet ums Schreiben. False = schon gefragt/abgelehnt (keine neue Frage am PC)."""
        name = clean_name(name)
        with self.lock:
            self.names[gid] = name
            if gid in self.allowed or gid in self.denied or gid in self.waiting:
                return False
            if sum(1 for w in self.waiting.values() if w["ip"] == ip) >= 2:
                return False
            self.waiting[gid] = {"name": name, "ip": ip, "t": time.monotonic()}
        return True

    def answer(self, gid: str, allow: bool) -> bool:
        with self.lock:
            req = self.waiting.pop(gid, None)
            if req is None:
                return False
            if allow:
                self.allowed[gid] = req["name"]
            else:
                self.denied.add(gid)
        return True

    def reset(self) -> None:
        """Modus gewechselt / alle Gäste raus: Erlaubnisse und offene Fragen vergessen."""
        with self.lock:
            self.allowed.clear()
            self.denied.clear()
            self.waiting.clear()

    def throttle(self, ip: str, now: float | None = None) -> bool:
        """True = zu viele Anfragen von diesem Gerät gerade."""
        now = time.monotonic() if now is None else now
        with self.lock:
            hits = [t for t in self._hits.get(ip, []) if now - t < 1.0]
            hits.append(now)
            self._hits[ip] = hits
            return len(hits) > MAX_PER_SECOND


def parse_stroke(data: dict) -> dict | None:
    """Strich vom Gast prüfen: Punkte 0…1 im sichtbaren Ausschnitt der Tafel, Werkzeug, Farbe, Stärke 1–3."""
    tool = str(data.get("tool", "pen"))
    color = str(data.get("color", "#111827"))
    if tool not in TOOLS or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        return None
    pts = data.get("pts")
    if not isinstance(pts, list) or not 1 <= len(pts) <= MAX_POINTS:
        return None
    out = []
    for p in pts:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            return None
        x, y = float(p[0]), float(p[1])
        pressure = float(p[2]) if len(p) > 2 else 0.5
        if not (0 <= x <= 1 and 0 <= y <= 1):
            return None
        out.append((x, y, max(0.05, min(1.0, pressure))))
    try:
        size = max(1, min(3, int(data.get("size", 2))))
    except (TypeError, ValueError):
        size = 2
    return {"tool": tool, "color": color, "size": size, "pts": out}


GUEST_PAGE = r"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<title>AluPC – Whiteboard</title><style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;height:100%;overscroll-behavior:none}
body{font-family:system-ui,-apple-system,sans-serif;background:#0e0f13;color:#eceef1;display:flex;flex-direction:column;
gap:10px;padding:max(10px,env(safe-area-inset-top)) 12px max(12px,env(safe-area-inset-bottom));-webkit-user-select:none;user-select:none}
header{display:flex;align-items:center;gap:10px}header b{font-size:18px;flex:1}
#gname{width:min(44vw,220px);font-size:16px;padding:9px 12px;border-radius:12px;border:2px solid #2b2f39;
background:#17191f;color:#fff}#gname:focus{outline:none;border-color:#7383fb}
.msg{background:#21242c;border-radius:14px;padding:10px 14px;font-weight:700;text-align:center}
.msg.ok{background:linear-gradient(135deg,#16a34a,#0d9488)}.msg.bad{background:#7f1d1d}
#wrap{position:relative;flex:1;min-height:0;display:flex;align-items:center;justify-content:center}
#board{position:relative;aspect-ratio:16/9;width:100%;max-height:100%;background:#fff;border-radius:14px;overflow:hidden;
touch-action:none;box-shadow:0 10px 30px rgba(0,0,0,.4)}
#board img,#board canvas{position:absolute;inset:0;width:100%;height:100%;object-fit:fill}
#board img{pointer-events:none}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.bar button{border:0;border-radius:14px;padding:12px 14px;font-size:15px;font-weight:800;color:#fff;background:#21242c}
.bar button.on{background:linear-gradient(135deg,#7383fb,#7383fb)}
.colors{display:flex;gap:8px;flex-wrap:wrap}
.c{width:38px;height:38px;border-radius:50%;padding:0!important;border:3px solid transparent!important;box-shadow:inset 0 0 0 2px rgba(255,255,255,.28)}
.c.on{border-color:#fff!important;transform:scale(1.1)}
#ask{width:100%;padding:16px;border-radius:16px;border:0;font-size:18px;font-weight:800;color:#fff;
background:linear-gradient(135deg,#7383fb,#ec4899)}
@media (orientation:landscape) and (min-width:900px){body{display:grid;grid-template-columns:minmax(0,1fr) 220px;
grid-template-rows:auto auto 1fr;column-gap:12px}.bar{min-width:0}.bar button{width:100%}.bar .c{width:auto;aspect-ratio:1}
header,.msg,#ask{grid-column:1/-1}#wrap{grid-row:3}.bar{grid-row:3;flex-direction:column;align-items:stretch;align-content:start}
.bar .colors{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}}
</style></head><body>
<header><b>✏️ Whiteboard</b><input id="gname" maxlength="20" placeholder="Dein Name" autocomplete="off"></header>
<div class="msg" id="msg">Verbinde …</div>
<button id="ask" style="display:none">✋ Am PC fragen, ob ich schreiben darf</button>
<div id="wrap"><div id="board"><img id="img" alt=""><canvas id="ink"></canvas></div></div>
<div class="bar" id="bar">
 <button id="t-pen" class="on">✏️ Stift</button><button id="t-marker">🖍️ Marker</button>
 <button id="s-1">dünn</button><button id="s-2" class="on">mittel</button><button id="s-3">dick</button>
 <div class="colors" id="colors"></div>
</div>
<script>
const COLORS = ["#111827", "#ef4444", "#f59e0b", "#22c55e", "#3b82f6", "#a855f7", "#ec4899", "#ffffff"];
let gid = "", name = "", st = {state: "aus"}, tool = "pen", size = 2, color = COLORS[1];
try { gid = localStorage.getItem("alupc-gid") || ""; name = localStorage.getItem("alupc-name") || ""; } catch (e) {}
if (!/^[A-Za-z0-9_-]{8,40}$/.test(gid)) { gid = Array.from(crypto.getRandomValues(new Uint8Array(12)), b => (b % 36).toString(36)).join("");
  try { localStorage.setItem("alupc-gid", gid); } catch (e) {} }
const $ = id => document.getElementById(id);
$("gname").value = name;
$("gname").addEventListener("input", () => { name = $("gname").value.trim();
  try { localStorage.setItem("alupc-name", name); } catch (e) {} });
function msg(t, cls) { $("msg").textContent = t; $("msg").className = "msg " + (cls || ""); }
async function post(body) {
  try { const r = await fetch("/api/tafel", {method: "POST", body: JSON.stringify(Object.assign({g: gid, name}, body))});
    const d = await r.json().catch(() => ({})); if (!r.ok) { msg(d.error || "Geht gerade nicht", "bad"); return null; } return d;
  } catch (e) { msg("Keine Verbindung – im AluPC-WLAN?", "bad"); return null; } }
function show() {
  const s = st.state, can = s === "frei" && st.active;
  $("ask").style.display = s === "fragen" ? "block" : "none";
  $("bar").style.visibility = can ? "visible" : "hidden";
  if (s === "aus") msg("Das Whiteboard für Gäste ist aus.", "bad");
  else if (s === "nein") msg("Am PC abgelehnt.", "bad");
  else if (s === "warten") msg("Warte auf Erlaubnis am PC …");
  else if (s === "fragen") msg("Zum Schreiben erst am PC fragen.");
  else if (!st.active) msg("Das Whiteboard ist gerade nicht auf dem Bildschirm – kurz warten.");
  else msg("Schreib los – erscheint sofort auf dem Bildschirm", "ok");
}
async function poll() { try { const r = await fetch("/api/tafel?g=" + gid); if (r.ok) { st = await r.json(); show(); } } catch (e) {} }
$("ask").onclick = async () => { if (!name) { $("gname").focus(); msg("Erst deinen Namen eingeben – dann fragen.", "bad"); return; }
  const d = await post({action: "frage"}); if (d) { st = d; show(); } };
for (const t of ["pen", "marker"]) $("t-" + t).onclick = () => { tool = t; for (const k of ["pen", "marker"]) $("t-" + k).classList.toggle("on", k === t); };
for (const s of [1, 2, 3]) $("s-" + s).onclick = () => { size = s; for (const k of [1, 2, 3]) $("s-" + k).classList.toggle("on", k === s); };
for (const c of COLORS) { const b = document.createElement("button"); b.className = "c" + (c === color ? " on" : ""); b.style.background = c;
  b.onclick = () => { color = c; for (const x of $("colors").children) x.classList.toggle("on", x === b); }; $("colors").appendChild(b); }
// Live-Bild der Tafel
async function frame() { if (st.state === "frei" && st.active) { try { const r = await fetch("/api/tafel/bild?g=" + gid);
  if (r.status === 200) { const b = await r.blob(); const u = URL.createObjectURL(b); const old = $("img").src; $("img").src = u;
    $("img").onload = () => { const i = $("img"); if (i.naturalWidth) $("board").style.aspectRatio = i.naturalWidth + "/" + i.naturalHeight; };
    if (old.startsWith("blob:")) URL.revokeObjectURL(old); } } catch (e) {} } setTimeout(frame, 700); }
// Zeichnen: Striche lokal sofort zeigen, in Stücken schicken
const cv = $("ink"), ctx = cv.getContext("2d"); let pts = [], down = false, last = null, timer = 0;
function fit() { const r = cv.getBoundingClientRect(); cv.width = r.width * devicePixelRatio; cv.height = r.height * devicePixelRatio; }
new ResizeObserver(fit).observe(cv);
function at(e) { const r = cv.getBoundingClientRect(); return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
  Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)), e.pressure && e.pointerType === "pen" ? e.pressure : 0.5]; }
function flush(end) { if (pts.length) { const send = pts; pts = end ? [] : [send[send.length - 1]];
  if (send.length > 1 || end) post({action: "strich", tool, color, size, pts: send}); } }
cv.addEventListener("pointerdown", e => { if (!(st.state === "frei" && st.active)) return; down = true; cv.setPointerCapture(e.pointerId);
  last = at(e); pts = [last]; clearInterval(timer); timer = setInterval(() => flush(false), 350); });
cv.addEventListener("pointermove", e => { if (!down) return; const p = at(e); pts.push(p);
  ctx.strokeStyle = color; ctx.globalAlpha = tool === "marker" ? .35 : 1; ctx.lineCap = "round";
  ctx.lineWidth = devicePixelRatio * (tool === "marker" ? 14 : 2 + size * 1.5);
  ctx.beginPath(); ctx.moveTo(last[0] * cv.width, last[1] * cv.height); ctx.lineTo(p[0] * cv.width, p[1] * cv.height); ctx.stroke();
  last = p; if (pts.length >= 300) flush(false); });
function up() { if (!down) return; down = false; clearInterval(timer); flush(true);
  setTimeout(() => ctx.clearRect(0, 0, cv.width, cv.height), 1600); }
cv.addEventListener("pointerup", up); cv.addEventListener("pointercancel", up);
poll(); setInterval(poll, 1500); frame();
</script></body></html>"""
