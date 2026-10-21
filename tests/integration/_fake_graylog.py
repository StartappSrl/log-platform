import json, random, sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
SCEN, PORT = sys.argv[1], int(sys.argv[2])
random.seed(3)
now = datetime.now(timezone.utc)
devices = {"startapp": ["PST-TCN-01", "NAS-STARTAPP"], "difraia": ["SERVER-DIFRAIA"]}
MSGS = []
def add(count, max_age_h, min_age_h=0):
    for i in range(count):
        t = random.choice(list(devices)); h = random.choice(devices[t])
        ts = now - timedelta(hours=random.uniform(min_age_h, max_age_h))
        MSGS.append({"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "source": h, "tenant": t, "_dt": ts})
if SCEN == "recente": add(400, 20, 0.05); add(800, 24 * 25, 25)
elif SCEN == "fermo": add(800, 24 * 25, 24 * 15)
elif SCEN == "tanti": add(6000, 20, 0.05)
elif SCEN == "quieto":                       # 6000 messaggi recenti di due host + un host quieto di 20 ore fa
    for h in ("BUSY-1", "BUSY-2"):
        for i in range(3000):
            ts = now - timedelta(hours=random.uniform(0.05, 5))
            MSGS_EXTRA = None
            MSGS.append({"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "source": h, "tenant": "startapp", "_dt": ts})
    ts = now - timedelta(hours=20)
    MSGS.append({"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "source": "QUIET-SRV", "tenant": "startapp", "_dt": ts})
elif SCEN == "vuoto": pass
MSGS.sort(key=lambda m: m["_dt"], reverse=True)
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if SCEN == "errore":
            self.send_response(500); self.end_headers(); return
        sec = int(q["range"][0]); limit = int(q["limit"][0])
        import re as _re
        sel = [m for m in MSGS if (now - m["_dt"]).total_seconds() <= sec]
        mq = _re.fullmatch(r'source:"((?:[^"\\]|\\.)*)" AND tenant:"((?:[^"\\]|\\.)*)"', q.get("query", ["*"])[0])
        if mq:
            un = lambda v: _re.sub(r"\\(.)", r"\1", v)
            sel = [m for m in sel if m["source"] == un(mq.group(1)) and m["tenant"] == un(mq.group(2))]
        out = {"total_results": len(sel), "messages": [{"message": {k: v for k, v in m.items() if k != "_dt"}} for m in sel[:limit]]}
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps(out).encode())
HTTPServer(("127.0.0.1", PORT), H).serve_forever()
