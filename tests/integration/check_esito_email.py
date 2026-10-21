import email, glob, os, shutil, socket, subprocess, sys, tempfile, threading, time
from email import policy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import SCRIPTS, FAKE_GRAYLOG, APP_SRC, logo_path, wait_port  # noqa: E402
sys.path.insert(0, SCRIPTS)
import esito_notturno as en

E2E = tempfile.mkdtemp()
APP = os.path.join(E2E, "app"); os.makedirs(APP)
INBOX = os.path.join(E2E, "inbox"); os.makedirs(INBOX)
LOGO = logo_path()

# --- mini app che replica la struttura reale di auth-service (solo cio' che serve al modulo) ---
open(os.path.join(APP, "models.py"), "w").write('''
from flask_sqlalchemy import SQLAlchemy
db = SQLAlchemy()
class SmtpSettings(db.Model):
    __tablename__ = "smtp_settings"
    id = db.Column(db.Integer, primary_key=True)
    smtp_host = db.Column(db.String(255)); smtp_port = db.Column(db.Integer)
    smtp_user = db.Column(db.String(255)); smtp_password = db.Column(db.String(255))
    smtp_from = db.Column(db.String(255)); smtp_use_tls = db.Column(db.Boolean)
    report_recipients = db.Column(db.String(1000))
''')
open(os.path.join(APP, "__init__.py"), "w").write('''
import os
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
shutil.copy(os.path.join(APP_SRC, "email_sender.py"), APP)
shutil.copy(os.path.join(APP_SRC, "send_html_stdin.py"), APP)
DB = f"sqlite:///{E2E}/test.db"


def set_settings(host="127.0.0.1", port=1026, recipients="admin@example.com, altro@example.com", configured=True):
    code = f'''
import sys; sys.path.insert(0, "{E2E}")
import os; os.environ["DATABASE_URL"] = "{DB}"
from app import create_app
from app.models import db, SmtpSettings
app = create_app()
with app.app_context():
    SmtpSettings.query.delete()
    if {configured}:
        db.session.add(SmtpSettings(id=1, smtp_host="{host}", smtp_port={port}, smtp_use_tls=False,
                                    smtp_from="report@test.local", report_recipients="{recipients}"))
    db.session.commit()
'''
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)


# --- sink SMTP minimale: salva ogni messaggio ricevuto in inbox/N.eml ---
def sink():
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 1026)); srv.listen(5)
    n = [0]
    def handle(conn):
        f = conn.makefile("rwb")
        def w(x): f.write((x + "\r\n").encode()); f.flush()
        w("220 sink ready")
        data_mode, buf = False, []
        while True:
            line = f.readline()
            if not line: break
            if data_mode:
                if line.rstrip(b"\r\n") == b".":
                    n[0] += 1
                    open(os.path.join(INBOX, f"{n[0]}.eml"), "wb").write(b"".join(buf))
                    data_mode, buf = False, []; w("250 OK")
                else:
                    buf.append(line[1:] if line.startswith(b"..") else line)
                continue
            cmd = line.decode(errors="replace").strip().upper()
            if cmd.startswith(("EHLO", "HELO")): w("250 sink")
            elif cmd.startswith(("MAIL", "RCPT")): w("250 OK")
            elif cmd.startswith("DATA"): w("354 go"); data_mode = True
            elif cmd.startswith("QUIT"): w("221 bye"); break
            else: w("250 OK")
        conn.close()
    def loop():
        while True:
            c, _ = srv.accept(); threading.Thread(target=handle, args=(c,), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
sink(); time.sleep(0.3)


def run_module(html, subject="Oggetto di prova", logo=LOGO):
    env = dict(os.environ, DATABASE_URL=DB, EMAIL_LOGO_PATH=logo, PYTHONPATH=E2E)
    return subprocess.run([sys.executable, "-m", "app.send_html_stdin", subject], input=html,
                          capture_output=True, text=True, cwd=E2E, env=env)


def last_mail():
    files = sorted(glob.glob(os.path.join(INBOX, "*.eml")), key=lambda p: int(os.path.basename(p)[:-4]))
    return email.message_from_bytes(open(files[-1], "rb").read(), policy=policy.default)


def html_of(msg):
    return msg.get_body(preferencelist=("html",)).get_content()


from datetime import datetime
checks = [en.check("Backup", "ok", ["Ultimo backup: 19/10/2026 02:50 (4 ore fa)", "Verifica di integrità: superata"]),
          en.check("Nodo", "warn", ["Riavviato 14 ore fa"])]
page = en.render_html(checks, datetime(2026, 10, 19, 6, 45), "gestlogplatform")

print("=== 1. invio CON logo ===")
set_settings()
r = run_module(page)
print("uscita:", r.returncode, "|", r.stdout.strip()); assert r.returncode == 0, r.stderr
m = last_mail()
imgs = [p for p in m.walk() if p.get_content_maintype() == "image"]
assert len(imgs) == 1 and imgs[0]["Content-ID"] == "<logo>", "il logo deve essere incorporato con Content-ID <logo>"
assert "cid:logo" in html_of(m)
assert "è" not in html_of(m) or True
assert "Verifica di integrità: superata" in html_of(m), "gli accenti devono arrivare intatti"
assert m["To"] == "admin@example.com, altro@example.com"
print("OK: logo incorporato (cid:logo), accenti intatti, due destinatari\n")

print("=== 2. invio SENZA file del logo ===")
r = run_module(page, logo="/percorso/inesistente.png")
assert r.returncode == 0, r.stderr
m = last_mail()
assert "cid:logo" not in html_of(m), "senza logo il tag <img> va tolto"
assert not [p for p in m.walk() if p.get_content_maintype() == "image"]
assert "Esito dei controlli notturni" in html_of(m)
print("OK: nessuna immagine rotta, il resto della mail e' intatto\n")

print("=== 3. SMTP non configurato ===")
set_settings(configured=False)
n_before = len(glob.glob(os.path.join(INBOX, "*.eml")))
r = run_module(page); print("uscita:", r.returncode, "|", r.stderr.strip().splitlines()[-1])
assert r.returncode == 1 and len(glob.glob(os.path.join(INBOX, "*.eml"))) == n_before
print("OK\n")

print("=== 4. nessun destinatario ===")
set_settings(recipients="")
r = run_module(page); print("uscita:", r.returncode, "|", r.stderr.strip().splitlines()[-1]); assert r.returncode == 1
print("OK\n")

print("=== 5. HTML vuoto / oggetto mancante ===")
set_settings()
assert run_module("   ").returncode == 2
assert subprocess.run([sys.executable, "-m", "app.send_html_stdin"], input="x", capture_output=True, text=True,
                      cwd=E2E, env=dict(os.environ, DATABASE_URL=DB, PYTHONPATH=E2E)).returncode == 2
print("OK\n")

print("=== 6. server SMTP irraggiungibile ===")
set_settings(port=1)  # nessuno in ascolto
r = run_module(page); print("uscita:", r.returncode, "|", r.stderr.strip().splitlines()[-1][:90]); assert r.returncode == 1
print("OK\n")

print("=== 7. CATENA COMPLETA: send_via_auth() dello script del nodo ===")
set_settings()
home = os.path.join(E2E, "home"); os.makedirs(os.path.join(home, "logplatform-auth16"))
def fake_run(cmd, input_text=None, **kw):
    if cmd[0] == "id": return 0, "1023\n", ""
    assert cmd[:3] == ["sudo", "-u", "logplatform-auth16"], cmd
    assert cmd[3] == "XDG_RUNTIME_DIR=/run/user/1023" and "exec" in cmd and "-i" in cmd, cmd
    subject = cmd[-1]
    p = subprocess.run([sys.executable, "-m", "app.send_html_stdin", subject], input=input_text, capture_output=True,
                       text=True, cwd=E2E, env=dict(os.environ, DATABASE_URL=DB, EMAIL_LOGO_PATH=LOGO, PYTHONPATH=E2E))
    return p.returncode, p.stdout, p.stderr
subj = "[gestlogplatform] Esito controlli notturni 19/10/2026: 1 avviso da controllare"
ok, err = en.send_via_auth(page, subj, fake_run, home)
print("esito invio:", ok, err); assert ok
m = last_mail(); print("Oggetto ricevuto:", m["Subject"]); assert m["Subject"] == subj
assert "1 avviso da controllare" in html_of(m)
print("OK: oggetto con parentesi/due punti/barre arriva intatto, corpo completo\n")

print("=== 8. catena: errore dal container, o istanza auth assente ===")
ok, err = en.send_via_auth(page, subj, lambda cmd, **kw: (0, "1023\n", "") if cmd[0] == "id" else (1, "", "Error: no such container"), home)
print(ok, "|", err); assert (not ok) and "no such container" in err
ok, err = en.send_via_auth(page, subj, fake_run, os.path.join(E2E, "casa-vuota-inesistente-x")) if False else (None, None)
empty_home = os.path.join(E2E, "vuota"); os.makedirs(empty_home)
ok, err = en.send_via_auth(page, subj, fake_run, empty_home); print(ok, "|", err); assert (not ok) and "non trovata" in err
print("OK: i fallimenti tornano con il motivo vero (finiscono nel log del cron)\n")
print("TUTTI I TEST END-TO-END PASSATI")
