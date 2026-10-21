import contextlib, email, glob, io, json, os, shutil, socket, subprocess, sys, tempfile, threading, time
from datetime import datetime, timedelta, timezone
from email import policy
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import APP_SRC  # noqa: E402
ROOT = tempfile.mkdtemp(); APP = os.path.join(ROOT, "app"); os.makedirs(APP)
INBOX = os.path.join(ROOT, "inbox"); os.makedirs(INBOX)
DBURL = f"sqlite:///{ROOT}/test.db"

# ---------------------------------------------------------------- app finta (struttura reale)
open(os.path.join(APP, "models.py"), "w").write('''from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
db = SQLAlchemy()


class Tenant(db.Model):
    __tablename__ = "tenants"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(200), nullable=True)
    graylog_stream_id = db.Column(db.String(64), nullable=True)
    graylog_index_set_id = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Anagrafica cliente (facoltativa, per intestare bene report/fatture)
    ragione_sociale = db.Column(db.String(255), nullable=True)
    partita_iva = db.Column(db.String(50), nullable=True)
    indirizzo = db.Column(db.String(500), nullable=True)
    referente = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    note = db.Column(db.Text, nullable=True)
    weekly_report_enabled = db.Column(db.Boolean, default=False, nullable=False)


class FailedLogin(db.Model):
    __tablename__ = "failed_logins"
    id = db.Column(db.Integer, primary_key=True)


class SmtpSettings(db.Model):
    __tablename__ = "smtp_settings"
    id = db.Column(db.Integer, primary_key=True)
    smtp_host = db.Column(db.String(255)); smtp_port = db.Column(db.Integer)
    smtp_user = db.Column(db.String(255)); smtp_password = db.Column(db.String(255))
    smtp_from = db.Column(db.String(255)); smtp_use_tls = db.Column(db.Boolean)
    report_recipients = db.Column(db.String(1000))


class AuditLog(db.Model):
    __tablename__ = "audit_log"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), nullable=False)
    action = db.Column(db.String(120), nullable=False)
    details = db.Column(db.Text, nullable=True)
    ip = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=True)
''')
open(os.path.join(APP, "__init__.py"), "w").write('''import os
from flask import Flask
from .models import db

def create_app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]
    db.init_app(app)
    with app.app_context():
        db.create_all()
    return app
''')
open(os.path.join(APP, "graylog_client.py"), "w").write('''import json, os, requests
class GraylogError(Exception): pass
def _request(method, path, **kwargs):
    try:
        r = requests.request(method, os.environ["FAKE_GRAYLOG"] + path, params=kwargs.get("params"), timeout=15)
    except requests.exceptions.RequestException as e:
        raise GraylogError(str(e))
    if r.status_code != 200: raise GraylogError(f"HTTP {r.status_code}")
    return r.json()
def get_latest_inventory_per_host(stream_id):
    return json.loads(os.environ.get("FAKE_INVENTORY", "{}")).get(stream_id, [])
''')
for _f in ("email_sender.py", "send_html_stdin.py", "customer_report.py", "send_customer_reports.py"):
    shutil.copy(os.path.join(APP_SRC, _f), APP)

# ---------------------------------------------------------------- Graylog finto (in-thread, con registro delle richieste)
now = datetime.now(timezone.utc)
DATA, REQ, FAIL = {}, [], set()

def gen(host, offset_h, step_h, back_days=9):
    out, k = [], 0
    while offset_h + k * step_h < back_days * 24:
        ts = now - timedelta(hours=offset_h + k * step_h)
        out.append({"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "source": host, "_dt": ts}); k += 1
    return out

DATA["S1"] = gen("ACME-SRV01", 2, 3) + gen("ACME-PC02", 30, 5)       # SRV01 attivo; PC02 muto da 30 ore
DATA["S2"] = gen("BETA-SECRET-NAS", 1, 2)
DATA["S3"] = []                                                        # nessun dispositivo
DATA["S4"] = gen("DELTA-PC", 3, 4)
DATA["S5"] = gen("EPS-PC", 1, 4); FAIL.add("S5")                       # Graylog fallisce per questo stream
os.environ["FAKE_INVENTORY"] = json.dumps({"S1": [{"hostname": "ACME-SRV01", "pending_updates": [1, 2, 3]},
                                                   {"hostname": "ACME-PC02", "pending_updates": []}]})

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        REQ.append(q)
        flt = q.get("filter", "")
        sid = flt.split(":", 1)[1] if flt.startswith("streams:") else None
        if sid in FAIL:
            self.send_response(500); self.end_headers(); return
        pool = DATA.get(sid) if sid else [m for v in DATA.values() for m in v]    # senza filtro: TUTTI (sarebbe una fuga)
        t = datetime.now(timezone.utc)
        sel = sorted([m for m in (pool or []) if (t - m["_dt"]).total_seconds() <= int(q["range"])], key=lambda m: m["_dt"], reverse=True)
        out = {"total_results": len(sel), "messages": [{"message": {k: v for k, v in m.items() if k != "_dt"}} for m in sel[:int(q["limit"])]]}
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps(out).encode())

def serve(port, handler):
    srv = HTTPServer(("127.0.0.1", port), handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
serve(18800, H)
os.environ["FAKE_GRAYLOG"] = "http://127.0.0.1:18800"; os.environ["DATABASE_URL"] = DBURL

# ---------------------------------------------------------------- SMTP finto
def sink():
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); srv.bind(("127.0.0.1", 1027)); srv.listen(5)
    n = [0]
    def handle(conn):
        f = conn.makefile("rwb")
        def w(x): f.write((x + "\r\n").encode()); f.flush()
        w("220 sink"); data, buf = False, []
        while True:
            line = f.readline()
            if not line: break
            if data:
                if line.rstrip(b"\r\n") == b".":
                    n[0] += 1; open(os.path.join(INBOX, f"{n[0]:03d}.eml"), "wb").write(b"".join(buf)); data, buf = False, []; w("250 OK")
                else: buf.append(line[1:] if line.startswith(b"..") else line)
                continue
            c = line.decode(errors="replace").strip().upper()
            if c.startswith(("EHLO", "HELO")): w("250 sink")
            elif c.startswith(("MAIL", "RCPT")): w("250 OK")
            elif c.startswith("DATA"): w("354 go"); data = True
            elif c.startswith("QUIT"): w("221 bye"); break
            else: w("250 OK")
        conn.close()
    def loop():
        while True:
            c, _ = srv.accept(); threading.Thread(target=handle, args=(c,), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
sink(); time.sleep(0.3)

def inbox():
    out = []
    for p in sorted(glob.glob(os.path.join(INBOX, "*.eml"))):
        m = email.message_from_bytes(open(p, "rb").read(), policy=policy.default)
        out.append({"to": m["To"], "subject": m["Subject"], "html": m.get_body(preferencelist=("html",)).get_content()})
    return out

sys.path.insert(0, ROOT)
from app import create_app
from app.models import db, Tenant, SmtpSettings, AuditLog
import app.customer_report as cr
import app.send_customer_reports as scr

# ---------------------------------------------------------------- 1. raccolta dati e ISOLAMENTO
print("=== 1. RACCOLTA DATI di un cliente ===")
d = cr.collect_customer_data("Acme Srl", "S1")
nomi = {x["nome"]: x for x in d["dispositivi"]}
print({k: (v["stato"], v["aggiornamenti"]) for k, v in nomi.items()}, "| totale", d["totale"])
assert nomi["ACME-SRV01"]["stato"] == "ok" and nomi["ACME-PC02"]["stato"] == "warn"
assert nomi["ACME-SRV01"]["aggiornamenti"] == 3 and nomi["ACME-PC02"]["aggiornamenti"] == 0
assert d["dispositivi"][0]["nome"] == "ACME-PC02", "i dispositivi con problemi vanno in cima"
tz = cr.local_tz()
atteso = {}
for m in DATA["S1"]:
    giorno = m["_dt"].astimezone(tz).date(); atteso[giorno] = atteso.get(giorno, 0) + 1
midnight = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
for giorno, n in d["giorni"]:
    assert abs(n - atteso.get(giorno, 0)) <= 1, (giorno, n, atteso.get(giorno))
assert len(d["giorni"]) == 7 and d["totale"] == sum(n for _, n in d["giorni"])
print("OK: 7 giorni di calendario, somma dei giorni = totale, conteggi uguali a un calcolo indipendente\n")

print("=== 2. ISOLAMENTO fra clienti ===")
REQ.clear(); cr.collect_customer_data("Acme Srl", "S1")
assert REQ and all(q.get("filter") == "streams:S1" for q in REQ), "OGNI richiesta deve portare il filtro del cliente"
print(f"OK: tutte le {len(REQ)} richieste a Graylog portano 'filter=streams:S1'")
h1 = cr.render_customer_html(cr.collect_customer_data("Acme Srl", "S1"))
h2 = cr.render_customer_html(cr.collect_customer_data("Beta Spa", "S2"))
assert "BETA-" not in h1 and "ACME-" not in h2
assert "ACME-SRV01" in h1 and "BETA-SECRET-NAS" in h2
print("OK: la mail di Acme non contiene nulla di Beta e viceversa\n")

print("=== 3. SINTESI (riquadro in cima) ===")
st = lambda name, sid: cr.banner(cr.collect_customer_data(name, sid))
assert st("A", "S1") == ("warn", "1 dispositivo non invia dati da più di 24 ore"), st("A", "S1")
assert st("B", "S2") == ("ok", "Il servizio funziona regolarmente")
assert st("G", "S3") == ("fail", "Nessun dispositivo ha inviato dati negli ultimi 7 giorni")
print("OK: avviso / regolare / nessun dispositivo\n")

print("=== 4. ESCAPING e fallimento di Graylog ===")
bad = cr.collect_customer_data("Bad <b>X</b>", "S2"); bad["dispositivi"][0]["nome"] = "<script>alert(1)</script>"
hb = cr.render_customer_html(bad); assert "<script>" not in hb and "&lt;script&gt;" in hb and "<b>X</b>" not in hb
try:
    cr.collect_customer_data("Eps", "S5"); raise SystemExit("doveva fallire")
except Exception as exc:
    print("OK: Graylog in errore -> eccezione, non un report vuoto:", str(exc)[:40])
print()

# ---------------------------------------------------------------- 5. CLI: sicurezze
def setup_db(smtp=True):
    app = create_app()
    with app.app_context():
        db.drop_all(); db.create_all()
        rows = [("acme", "Acme Srl", "S1", "referente@acme.it", True), ("beta", "Beta Spa", "S2", "non-una-email", True),
                ("gamma", "Gamma", "S3", "g@gamma.it", False), ("delta", "Delta", "S4", None, True),
                ("eps", "Epsilon", "S5", "e@eps.it", True)]
        for n, dn, sid, mail, en in rows:
            db.session.add(Tenant(name=n, display_name=dn, graylog_stream_id=sid, email=mail, weekly_report_enabled=en))
        if smtp:
            db.session.add(SmtpSettings(id=1, smtp_host="127.0.0.1", smtp_port=1027, smtp_use_tls=False,
                                        smtp_from="report@test.local", report_recipients="me@provider.it"))
        db.session.commit()

def cli(*argv):
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = scr.main(list(argv))
    return rc, buf.getvalue(), err.getvalue()

def clear_inbox():
    for p in glob.glob(os.path.join(INBOX, "*.eml")): os.remove(p)

print("=== 5a. INVIO VERO (nessuna opzione) ===")
setup_db(); clear_inbox()
rc, out, err = cli(); print(out.strip()); print(err.strip())
mails = inbox()
assert rc == 1, "epsilon fallisce: l'esito complessivo deve essere un errore"
assert [m["to"] for m in mails] == ["referente@acme.it", "me@provider.it"], [m["to"] for m in mails]
assert mails[1]["subject"].startswith("[Copia inviata a referente@acme.it]")
assert "SALTATO beta: indirizzo non valido" in out and "SALTATO delta: nessuna email del referente" in out
assert "ERRORE eps" in err and "NON riceve nulla" in err
assert not any("e@eps.it" in (m["to"] or "") or "g@gamma.it" in (m["to"] or "") for m in mails)
assert "ACME-SRV01" in mails[0]["html"] and "Acme Srl" in mails[0]["html"], "la mail al cliente deve contenere i SUOI dati"
assert "BETA-" not in mails[0]["html"] and "BETA-" not in mails[1]["html"], "nessun dato di altri clienti"
with create_app().app_context():
    righe = AuditLog.query.filter_by(action="invio_report_cliente").all()
    assert len(righe) == 1 and righe[0].username == "sistema" and "cliente=acme" in righe[0].details
print("OK: scrive solo ad Acme (gamma non attivato, beta/delta senza email valida, epsilon con Graylog in errore resta senza mail), copia interna, riga nel log di controllo\n")

print("=== 5b. MODALITA' PROVA (--to ... --include-disabled) ===")
clear_inbox(); setup_db()
rc, out, err = cli("--to", "test@miodominio.it", "--include-disabled"); print(out.strip())
mails = inbox()
assert all(m["to"] == "test@miodominio.it" for m in mails), [m["to"] for m in mails]
assert len(mails) == 4 and all(m["subject"].startswith("[PROVA] ") for m in mails), len(mails)
assert not any("referente@acme.it" in (m["to"] or "") for m in mails)
with create_app().app_context(): assert AuditLog.query.count() == 0, "la prova non deve lasciare tracce di invii ai clienti"
print("OK: 4 mail tutte e solo a test@miodominio.it con '[PROVA]', nessuna ai clienti, nessuna copia, nessun audit\n")

print("=== 5c. --include-disabled da solo e indirizzo di prova sbagliato ===")
clear_inbox(); rc, out, err = cli("--include-disabled"); print(err.strip()); assert rc == 2 and not inbox()
rc, out, err = cli("--to", "non-valido", "--include-disabled"); assert rc == 2 and not inbox()
print("OK: rifiutati prima di inviare qualsiasi cosa\n")

print("=== 5d. --dry-run ===")
clear_inbox()
for p in glob.glob("/tmp/riepilogo-cliente-*.html"): os.remove(p)
rc, out, err = cli("--dry-run", "--include-disabled"); print(out.strip())
assert not inbox() and os.path.exists("/tmp/riepilogo-cliente-acme.html") and os.path.exists("/tmp/riepilogo-cliente-gamma.html")
print("OK: nessuna mail, HTML scritti in /tmp\n")

print("=== 5e. --tenant e uscita pulita ===")
clear_inbox(); setup_db(); rc, out, err = cli("--tenant", "acme"); assert rc == 0 and len(inbox()) == 2; print(out.strip().splitlines()[-1])
rc, out, err = cli("--tenant", "inesistente"); assert rc == 0 and "Nessun cliente selezionato" in out
print("OK\n")

print("=== 5f. SMTP non configurato ===")
clear_inbox(); setup_db(smtp=False); rc, out, err = cli(); print(err.strip()); assert rc == 1 and not inbox()
rc, out, err = cli("--dry-run", "--include-disabled"); assert rc in (0, 1) and "SMTP" not in err
print("OK: nessun invio; il dry-run funziona comunque\n")

print("=== 5g. nessun cliente attivato ===")
setup_db()
with create_app().app_context():
    Tenant.query.update({"weekly_report_enabled": False}); db.session.commit()
clear_inbox(); rc, out, err = cli(); assert rc == 0 and not inbox() and "Nessun cliente selezionato" in out
print("OK: di default (tutto spento) non parte nessuna mail\n")

print("TUTTI I TEST DEL RIEPILOGO CLIENTI PASSATI")
