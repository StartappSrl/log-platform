import contextlib, email, glob, io, os, re, shutil, socket, subprocess, sys, tempfile, threading, time
from datetime import datetime, timedelta, timezone
from email import policy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import APP_SRC, logo_path  # noqa: E402
ROOT = tempfile.mkdtemp(); APP = os.path.join(ROOT, "app"); os.makedirs(APP)
INBOX = os.path.join(ROOT, "inbox"); os.makedirs(INBOX)
LOGO = logo_path()
os.environ.update(DATABASE_URL=f"sqlite:///{ROOT}/t.db", EMAIL_LOGO_PATH=LOGO, PUBLIC_BASE_URL="https://logplatform.test")

# ---------------------------------------------------------------- app finta con la struttura reale
w = lambda name, text: open(os.path.join(APP, name), "w").write(text)
w("models.py", '''from flask_sqlalchemy import SQLAlchemy
db = SQLAlchemy()
class Tenant(db.Model):
    __tablename__ = "tenants"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120)); display_name = db.Column(db.String(200)); graylog_stream_id = db.Column(db.String(64))
class SmtpSettings(db.Model):
    __tablename__ = "smtp_settings"
    id = db.Column(db.Integer, primary_key=True)
    smtp_host = db.Column(db.String(255)); smtp_port = db.Column(db.Integer); smtp_user = db.Column(db.String(255))
    smtp_password = db.Column(db.String(255)); smtp_from = db.Column(db.String(255)); smtp_use_tls = db.Column(db.Boolean)
    report_recipients = db.Column(db.String(1000))
''')
w("__init__.py", '''import os
from flask import Flask
from .models import db
def create_app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ["DATABASE_URL"]
    db.init_app(app)
    with app.app_context(): db.create_all()
    return app
''')
w("graylog_client.py", '''class GraylogError(Exception): pass
DASH, INV = {}, {}
def get_device_dashboard_for_tenant(stream_id):
    d = DASH[stream_id]
    if isinstance(d, Exception): raise d
    return d
def get_latest_inventory_per_host(stream_id):
    v = INV.get(stream_id, [])
    if isinstance(v, Exception): raise v
    return v
''')
w("platform_health.py", "STATE = {'graylog': {'ok': True, 'error': None}, 'mariadb': {'ok': True, 'error': None}}\ndef check_platform_health(): return STATE\n")
w("cert_ledger.py", "CERTS = []\ndef get_certificates_expiring_soon(days_threshold=30): return CERTS\n")
for _f in ("email_sender.py", "send_html_stdin.py", "nightly_report.py", "send_nightly_report.py"):
    shutil.copy(os.path.join(APP_SRC, _f), APP)   # i file VERI del repository
src = open(os.path.join(APP, "send_nightly_report.py")).read()
assert "render_donut_chart_png" not in src and "chart_images(reports)" in src and "prepare_html(html)" in src, \
    "send_nightly_report.py non e' collegato al nuovo report (logo e grafici)"

sys.path.insert(0, ROOT)
import app.nightly_report as nr
import app.graylog_client as gl
import app.platform_health as ph
import app.cert_ledger as cl

now = datetime.now(timezone.utc)
iso = lambda **kw: (now - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
def dev(h, total, err=0, warn=0, online=True, ago=None, disk=None):
    d = {"hostname": h, "total": total, "errors": err, "warnings": warn, "ok": total - err - warn, "online": online}
    if ago is not None: d["last_seen"] = iso(**ago)
    if disk is not None: d["disk_percent"] = disk
    return d
def rep(name, devices, upd=None, inv=None, total=None, tid=None):
    return {"tenant": tid or name.lower(), "display_name": name, "devices": devices,
            "total_messages": sum(d["total"] for d in devices) if total is None else total,
            "pending_updates_by_host": upd or {}, "inventory_hosts": inv or []}
OK_H = {"graylog": {"ok": True, "error": None}, "mariadb": {"ok": True, "error": None}}
def html(reports, health=OK_H, certs=None, at=None): return nr.build_nightly_report_html(reports, generated_at=at or now, platform_health=health, expiring_certs=certs)
def banner(h):
    m = re.search(r'<div style="background:#[0-9A-F]+;color:#[0-9A-F]+;padding:12px 16px;">\s*<div style="font-size:16px;font-weight:bold;">(.*?)</div>\s*<div style="font-size:13px;padding-top:3px;">(.*?)</div>', h, re.S)
    return m.group(1), m.group(2)

print("=== U1. sintesi in cima ===")
b = banner(html([rep("A", [dev("PC1", 10, online=True)])])); print("  ", b); assert b[0] == "Tutto regolare"
b = banner(html([rep("A", [dev("PC1", 10), dev("PC2", 5, online=False)])])); print("  ", b)
assert b == ("Alcuni punti da controllare", "1 dispositivo non invia log.")
b = banner(html([rep("A", [dev("PC1", 10)], upd={"X": [1]}, inv=["X", "Y"])])); print("  ", b)
assert b[1] == "1 dispositivo non invia log.", "di default: solo l'host con aggiornamenti in sospeso (Y non ha aggiornamenti)"
nr.INCLUDI_HOST_SENZA_AGGIORNAMENTI = True
b = banner(html([rep("A", [dev("PC1", 10)], upd={"X": [1]}, inv=["X", "Y"])])); assert b[1] == "2 dispositivi non inviano log.", b
nr.INCLUDI_HOST_SENZA_AGGIORNAMENTI = False
b = banner(html([rep("A", [dev("PC1", 10)])], health={"graylog": {"ok": False, "error": "timeout"}, "mariadb": {"ok": True, "error": None}}))
print("  ", b); assert b[0] == "Ci sono problemi da risolvere" and "Graylog non risponde" in b[1] and "MariaDB" not in b[1]
b = banner(html([rep("A", [dev("PC1", 10)])], certs=[{"common_name": "c1", "days_remaining": -2}])); assert b[0] == "Ci sono problemi da risolvere" and "1 certificato agent scaduto" in b[1]
b = banner(html([rep("A", [dev("PC1", 10)])], certs=[{"common_name": "c1", "days_remaining": 5}, {"common_name": "c2", "days_remaining": 9}]))
print("  ", b); assert b[0] == "Alcuni punti da controllare" and "2 certificati agent in scadenza" in b[1]
b = banner(html([rep("A", []), rep("B", [])])); print("  ", b); assert b[0] == "Alcuni punti da controllare" and "nessun dispositivo ha inviato log oggi" in b[1].lower()
b = banner(html([rep("A", [dev("PC1", 10)])], health={"graylog": {"ok": False, "error": "x"}, "mariadb": {"ok": False, "error": "y"}}))
assert "MariaDB non risponde" in b[1] and "MariaDB non risponde" in b[1], "'MariaDB' non deve diventare 'mariadb'"
print("   OK: sei situazioni distinte, e 'MariaDB' resta con le maiuscole\n")

print("=== U2. dispositivi: unione di log e inventario, ordine, aggiornamenti accanto al dispositivo ===")
r = rep("Startapp", [dev("PST-TCN-01", 92, 0, 4, True, {"minutes": 3})], upd={"DESKTOP-M62ML69": list(range(9))}, inv=["DESKTOP-M62ML69"])
rows = nr._device_rows(r, now)
print("  ", [(x["host"], x["senza_log"], x["updates"]) for x in rows])
assert [x["host"] for x in rows] == ["DESKTOP-M62ML69", "PST-TCN-01"], "chi non invia va in cima"
assert rows[0]["senza_log"] and rows[0]["updates"] == 9 and rows[1]["updates"] == 0
r = rep("X", [dev("A", 5), dev("B", 5, online=False), dev("C", 5, err=9), dev("D", 50)], upd={"FANTASMA": [1, 2]})
assert [x["host"] for x in nr._device_rows(r, now)] == ["B", "FANTASMA", "C", "D", "A"], [x["host"] for x in nr._device_rows(r, now)]
print("   OK: offline e senza log per primi, poi per errori e volume; un host con aggiornamenti ma sconosciuto compare comunque\n")

print("=== U3. grafico: solo con almeno due dispositivi con log; riferimenti e immagini coincidono ===")
reports = [rep("Zero", []), rep("Uno", [dev("A", 10)]), rep("Due", [dev("A", 10), dev("B", 5)]), rep("ZeroLog", [dev("A", 0), dev("B", 0)]), rep("Tre", [dev("A", 3), dev("B", 2), dev("C", 1)])]
h = html(reports); imgs = nr.chart_images(reports)
rif = set(re.findall(r"cid:(chart\d+)", h)); print("  ", sorted(rif), sorted(imgs))
assert rif == set(imgs) == {"chart2", "chart4"}, "il grafico compare solo per i clienti con >=2 dispositivi con log"
assert h.count("cid:logo") == 1 and 'charset="utf-8"' in h and "lang=\"it\"" in h
for cid, png in imgs.items(): assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) < 60000, (cid, len(png))
for devs in ([], [dev("A", 10)], [dev(f"H{i}", 100 - i) for i in range(12)]):
    png = nr.render_donut_chart_png(devs); assert png[:8] == b"\x89PNG\r\n\x1a\n"
print("   OK: nessun riferimento senza immagine e nessuna immagine non usata; PNG validi anche con 0, 1 e 12 dispositivi\n")

print("=== U4. sicurezza del contenuto (escaping) ===")
evil = "<script>alert(1)</script>"
h = html([rep("Beta <b>& Co</b>", [dev(evil, 5)], upd={evil: [1]}, inv=[evil])],
         health={"graylog": {"ok": False, "error": evil}, "mariadb": {"ok": True, "error": None}}, certs=[{"common_name": evil, "days_remaining": 3}])
assert "<script>" not in h and "<b>& Co</b>" not in h
assert h.count("&lt;script&gt;") == 3, "tre punti: nome nella tabella (host unico), errore Graylog, nome certificato"
print("   OK: nessun <script> iniettabile da nomi di host, clienti, certificati o errori\n")

print("=== U5. dati incompleti e formati diversi di 'ultimo log' ===")
minimi = {"hostname": "SOLO-NOME", "total": 3, "errors": 0, "warnings": 1}      # solo le 4 chiavi del vecchio report
h = html([rep("M", [minimi])]); assert "SOLO-NOME" in h and "Online" not in h and "Disco" not in h
for val, atteso in ((iso(minutes=2), "2 minuti fa"), (iso(hours=5), "5 ore fa"), (iso(days=3), "3 giorni fa"),
                    (iso(minutes=1), "1 minuto fa"), (int((now - timedelta(hours=2)).timestamp()), "2 ore fa"),
                    (int((now - timedelta(hours=2)).timestamp() * 1000), "2 ore fa"),
                    ((now - timedelta(minutes=10)).replace(tzinfo=None).isoformat(), "10 minuti fa")):
    assert nr._fmt_eta(nr._eta_seconds(val, now)) == atteso, (val, nr._fmt_eta(nr._eta_seconds(val, now)))
for brutto in (None, "", "boh", [], {}): assert nr._eta_seconds(brutto, now) is None
print("   OK: nessun crash con chiavi mancanti; ISO con Z, ISO senza fuso, epoch in secondi e millisecondi; testi non validi ignorati\n")

print("=== U6. molti dispositivi, disco, plurali ===")
h = html([rep("Grande", [dev(f"PC-{i:02d}", 100 + i, disk=40 + i) for i in range(30)])])
assert "… e altri 5 dispositivi" in h and h.count("PC-") == 25 + 0 or True
assert len(re.findall(r">PC-\d\d<", h)) == 25 and "… e altri 5 dispositivi" in h
h = html([rep("Disco", [dev("A", 5, disk=93), dev("B", 5, disk=80), dev("C", 5, disk=20)])])
assert 'color:#B91C1C;font-weight:bold;">93%' in h and 'color:#B45309;font-weight:bold;">80%' in h and 'color:#334155;font-weight:normal;">20%' in h
assert "1 dispositivo con log" in html([rep("U", [dev("A", 5)])]) and "2 dispositivi con log" in html([rep("U", [dev("A", 5), dev("B", 5)])])
import html as _h      # nell'HTML l'apostrofo e' scritto &#x27; (corretto): confronto sul testo decodificato
assert "dall'inventario" not in _h.unescape(html([rep("U", [], inv=["X", "Y"])])), "di default un host senza aggiornamenti non compare"
nr.INCLUDI_HOST_SENZA_AGGIORNAMENTI = True
assert "1 dispositivo noto dall'inventario non ha inviato log" in _h.unescape(html([rep("U", [], inv=["X"])]))
assert "2 dispositivi noti dall'inventario non hanno inviato log" in _h.unescape(html([rep("U", [], inv=["X", "Y"])]))
nr.INCLUDI_HOST_SENZA_AGGIORNAMENTI = False
assert "1 dispositivo noto dall'inventario non ha inviato log" in _h.unescape(html([rep("U", [], upd={"X": [1]})])), "con aggiornamenti compare sempre"
assert nr._int(1234567) == "1.234.567" and nr._int(None) == "–"
print("   OK: elenco limitato a 25 con riepilogo, colori del disco alle soglie 75/90, plurali corretti, numeri all'italiana\n")

print("=== U7. nessun cliente, fuso orario, link ===")
assert "Nessun cliente configurato" in html([])
h = html([rep("A", [dev("PC1", 5)])], at=datetime(2026, 10, 21, 1, 0, tzinfo=timezone.utc))
loc = nr._local_tz()
if getattr(loc, "key", None) == "Europe/Rome":
    assert "21/10/2026" in h and "generato alle 03:00" in h, "01:00 UTC = 03:00 in Italia (ora legale)"
    print("   orario locale corretto: 01:00 UTC -> 03:00")
assert 'href="https://logplatform.test"' in h
os.environ["PUBLIC_BASE_URL"] = "javascript:alert(1)"; assert "javascript:" not in html([rep("A", [dev("PC1", 5)])])
os.environ["PUBLIC_BASE_URL"] = "https://logplatform.test"
assert html([rep("A", [dev("PC1", 5)])], at=datetime(2026, 10, 21, 1, 0)), "data senza fuso accettata"
print("   OK: link solo http(s), niente 'javascript:'\n")

print("=== U8. raccolta dati dal Graylog (build_tenant_report_data) ===")
gl.DASH["S"] = {"devices": [dev("A", 5)], "total_messages_today": 5}
gl.INV["S"] = [{"hostname": "A", "pending_updates": [1]}, {"hostname": "B", "pending_updates": []}]
d = nr.build_tenant_report_data("t", "T", "S"); assert d["inventory_hosts"] == ["A", "B"] and list(d["pending_updates_by_host"]) == ["A"]
gl.INV["S"] = RuntimeError("inventario giu'"); d = nr.build_tenant_report_data("t", "T", "S")
assert d["inventory_hosts"] == [] and d["devices"], "un errore dell'inventario non deve far fallire il report"
gl.DASH["S"] = gl.GraylogError("giu'"); d = nr.build_tenant_report_data("t", "T", "S"); assert d["devices"] == [] and d["total_messages"] == 0
print("   OK: inventario o Graylog in errore non fanno fallire la raccolta\n")

# ---------------------------------------------------------------- E2E: invio vero attraverso lo script patchato
def sink():
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); srv.bind(("127.0.0.1", 1031)); srv.listen(5)
    n = [0]
    def handle(conn):
        f = conn.makefile("rwb"); wr = lambda x: (f.write((x + "\r\n").encode()), f.flush())
        wr("220 sink"); data, buf = False, []
        while True:
            line = f.readline()
            if not line: break
            if data:
                if line.rstrip(b"\r\n") == b".":
                    n[0] += 1; open(os.path.join(INBOX, f"{n[0]:03d}.eml"), "wb").write(b"".join(buf)); data, buf = False, []; wr("250 OK")
                else: buf.append(line[1:] if line.startswith(b"..") else line)
                continue
            c = line.decode(errors="replace").strip().upper()
            if c.startswith(("EHLO", "HELO")): wr("250 sink")
            elif c.startswith(("MAIL", "RCPT")): wr("250 OK")
            elif c.startswith("DATA"): wr("354 go"); data = True
            elif c.startswith("QUIT"): wr("221 bye"); break
            else: wr("250 OK")
        conn.close()
    def loop():
        while True:
            c, _ = srv.accept(); threading.Thread(target=handle, args=(c,), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
sink(); time.sleep(0.3)

print("=== E2E. invio vero con lo script PATCHATO (testo identico a quello sul nodo) ===")
from app import create_app
from app.models import db, Tenant, SmtpSettings
import app.send_html_stdin as shs
import app.send_nightly_report as snr
with create_app().app_context():
    db.drop_all(); db.create_all()
    db.session.add_all([Tenant(name="difraia", display_name="Studio Di Fraia", graylog_stream_id="S1"),
                        Tenant(name="startapp", display_name="Startapp Srl", graylog_stream_id="S2"),
                        Tenant(name="beta", display_name="Beta Spa", graylog_stream_id="S3"),
                        SmtpSettings(id=1, smtp_host="127.0.0.1", smtp_port=1031, smtp_use_tls=False, smtp_from="n@t.local", report_recipients="a@t.local, b@t.local")])
    db.session.commit()
gl.DASH.update({"S1": {"devices": [], "total_messages_today": 0},
                "S2": {"devices": [dev("PST-TCN-01", 92, 0, 4, True, {"minutes": 3})], "total_messages_today": 92},
                "S3": {"devices": [dev("B1", 700, 1, 3, True, {"minutes": 2}), dev("B2", 300, 0, 0, True, {"minutes": 8})], "total_messages_today": 1000}})
gl.INV.update({"S2": [{"hostname": "DESKTOP-M62ML69", "pending_updates": list(range(9))}], "S3": []})

def esegui():
    for p in glob.glob(os.path.join(INBOX, "*.eml")): os.remove(p)
    out = io.StringIO()
    with contextlib.redirect_stdout(out): snr.main()
    p = sorted(glob.glob(os.path.join(INBOX, "*.eml")))
    assert len(p) == 1, p
    m = email.message_from_bytes(open(p[0], "rb").read(), policy=policy.default)
    corpo = m.get_body(preferencelist=("html",)).get_content()
    parti = {(x["Content-ID"] or "").strip("<>"): x for x in m.walk() if x.get_content_maintype() == "image"}
    return m, corpo, parti, out.getvalue()

m, corpo, parti, out = esegui(); print("  ", out.strip().splitlines()[-1])
oggi = datetime.now(timezone.utc).strftime("%d/%m/%Y")
assert m["Subject"] == f"Report log notturno — {oggi}", "l'oggetto resta identico (filtri e regole di posta)"
assert m["To"] == "a@t.local, b@t.local"
rif = set(re.findall(r"cid:([A-Za-z0-9]+)", corpo))
print("   riferimenti nell'HTML:", sorted(rif), "| immagini allegate:", sorted(parti))
assert rif == set(parti) == {"logo", "chart0"}, "ogni cid ha la sua immagine e ogni immagine e' usata (logo + il solo grafico di Beta, primo per ordine alfabetico)"
assert "DESKTOP-M62ML69" in corpo and "Nessun log oggi" in corpo and "Alcuni punti da controllare" in corpo
print("   OK: logo + un solo grafico (solo dove serve), oggetto invariato, due destinatari\n")

print("=== E2E. se il file del logo manca: mail comunque corretta, senza immagine rotta ===")
shs.LOGO_PATH = "/non/esiste.png"
m, corpo, parti, out = esegui()
assert "cid:logo" not in corpo and "logo" not in parti and "Report log notturno" in corpo and "chart0" in parti
print("   OK: nessun riferimento al logo, grafico e contenuto intatti\n")

print("=== E2E. problemi sulla piattaforma e certificati: la mail parte e li mostra ===")
shs.LOGO_PATH = LOGO
ph.STATE = {"graylog": {"ok": False, "error": "timeout"}, "mariadb": {"ok": True, "error": None}}
cl.CERTS = [{"common_name": "agent-1", "days_remaining": -1}]
m, corpo, parti, out = esegui()
assert "Ci sono problemi da risolvere" in corpo and "timeout" in corpo and "SCADUTO" in corpo
print("   OK\n")
print("TUTTI I TEST DEL NUOVO REPORT NOTTURNO PASSATI")
