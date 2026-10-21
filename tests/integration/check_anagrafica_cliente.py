"""
Controllo delle rotte di modifica dell'anagrafica cliente (GET/POST /tenants/<nome>/anagrafica).
Estrae dal dashboard.py VERO il blocco racchiuso tra i marcatori "Anagrafica cliente: inizio/fine" e lo
esegue con un'app Flask e un database di prova: se i marcatori vengono spostati o cancellati, fallisce.
Principio verificato: si aggiornano SOLO i campi inviati, tutto il resto resta com'e'.
"""
import os, re, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import APP_SRC  # noqa: E402

from flask import Blueprint, Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy

SRC = open(os.path.join(APP_SRC, "dashboard.py"), encoding="utf-8").read()
m = re.search(r"# --- Anagrafica cliente: inizio ---\n(.*?)# --- Anagrafica cliente: fine ---", SRC, re.S)
assert m, "blocco 'Anagrafica cliente' non trovato in dashboard.py (marcatori spostati o cancellati?)"

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{tempfile.mkdtemp()}/t.db"
db = SQLAlchemy(app)


class Tenant(db.Model):
    __tablename__ = "tenants"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    display_name = db.Column(db.String(200)); graylog_stream_id = db.Column(db.String(64)); graylog_index_set_id = db.Column(db.String(64))
    ragione_sociale = db.Column(db.String(255)); partita_iva = db.Column(db.String(50)); indirizzo = db.Column(db.String(500))
    referente = db.Column(db.String(255)); email = db.Column(db.String(255)); note = db.Column(db.Text)
    weekly_report_enabled = db.Column(db.Boolean, default=False, nullable=False)


AUDIT = []
dash = Blueprint("dash", __name__)


def admin_required(f):                       # equivalente semplificato: serve il ruolo amministratore
    from functools import wraps
    @wraps(f)
    def w(*a, **k):
        if request.headers.get("X-Role") != "admin":
            return jsonify(error="forbidden"), 403
        return f(*a, **k)
    return w


def csrf_protect(f):                         # equivalente semplificato: serve il token anti-falsificazione
    from functools import wraps
    @wraps(f)
    def w(*a, **k):
        if request.headers.get("X-CSRF-Token") != "tok":
            return jsonify(error="csrf"), 403
        return f(*a, **k)
    return w


def record_audit(action, detail):
    AUDIT.append((action, detail))


exec(compile(m.group(1), "dashboard.py:anagrafica", "exec"), globals())
app.register_blueprint(dash, url_prefix="/d")
with app.app_context():
    db.create_all()

ADMIN = {"X-Role": "admin"}
POSTH = {"X-Role": "admin", "X-CSRF-Token": "tok"}
c = app.test_client()


def nuovo(nome="acme", **campi):
    with app.app_context():
        db.session.add(Tenant(name=nome, graylog_stream_id="S-" + nome, **campi)); db.session.commit()


def riga(nome="acme"):
    with app.app_context():
        t = Tenant.query.filter_by(name=nome).first()
        return {k: getattr(t, k) for k in ("display_name", "ragione_sociale", "partita_iva", "indirizzo", "referente", "email", "note",
                                           "graylog_stream_id", "weekly_report_enabled")}


def post(nome, corpo, headers=POSTH):
    return c.post(f"/d/tenants/{nome}/anagrafica", json=corpo, headers=headers)


print("B1. GET: valori attuali, vuoti come stringa vuota, nessun id interno")
nuovo("acme", display_name="Acme", ragione_sociale="Acme Srl", partita_iva="01234567890", referente=None, email=None)
r = c.get("/d/tenants/acme/anagrafica", headers=ADMIN); d = r.get_json()
assert r.status_code == 200 and d["ragione_sociale"] == "Acme Srl" and d["referente"] == "" and d["email"] == "" and d["note"] == "", d
assert "graylog_stream_id" not in d and "id" not in d, "non espone gli identificatori interni"
assert c.get("/d/tenants/nessuno/anagrafica", headers=ADMIN).status_code == 404
assert c.get("/d/tenants/acme/anagrafica").status_code == 403, "serve il ruolo amministratore"
print("    OK")

print("B2. aggiungere SOLO l'email: tutto il resto resta com'era")
prima = riga("acme")
r = post("acme", {"email": "ref@acme.it"}); d = r.get_json()
assert r.status_code == 200 and d["modificati"] == ["email"] and d["email"] == "ref@acme.it", d
dopo = riga("acme")
assert {k: v for k, v in dopo.items() if k != "email"} == {k: v for k, v in prima.items() if k != "email"}, "gli altri campi non cambiano"
print("    OK: modificato solo 'email'")

print("B3. aggiungere campi mancanti senza toccare quelli compilati")
nuovo("beta", display_name="Beta", ragione_sociale="Beta Spa", partita_iva="09876543210", email="x@beta.it")
r = post("beta", {"referente": "Giulia Bianchi", "indirizzo": "Via Roma 1, Milano"}); d = r.get_json()
assert d["modificati"] == ["indirizzo", "referente"] and d["ragione_sociale"] == "Beta Spa" and d["email"] == "x@beta.it", d
print("    OK")

print("B4. stesso valore: nessuna modifica e nessun audit")
n = len(AUDIT); r = post("beta", {"referente": "Giulia Bianchi", "email": "x@beta.it"}); d = r.get_json()
assert r.status_code == 200 and d["modificati"] == [] and len(AUDIT) == n
print("    OK")

print("B5. svuotare un campo e' possibile (e deliberato); resta nullo e torna come stringa vuota")
r = post("beta", {"indirizzo": ""}); assert r.get_json()["modificati"] == ["indirizzo"]
assert riga("beta")["indirizzo"] is None and c.get("/d/tenants/beta/anagrafica", headers=ADMIN).get_json()["indirizzo"] == ""
r = post("beta", {"note": None}); assert r.status_code == 200, "None equivale a vuoto"
print("    OK")

print("B6. email: validazione, segnaposto .invalid, piu' indirizzi normalizzati")
for brutta in ("non-una-email", "x@esempio.invalid", "a@b", "a b@c.it", "x'; DROP TABLE tenants;--@a.it"):
    r = post("acme", {"email": brutta}); assert r.status_code == 400 and "email non valida" in r.get_json()["error"], brutta
assert riga("acme")["email"] == "ref@acme.it", "dopo i tentativi sbagliati l'email e' invariata"
r = post("acme", {"email": " a@x.it ;b@y.com,  c@z.org "}); assert r.get_json()["email"] == "a@x.it, b@y.com, c@z.org", r.get_json()
print("    OK")

print("B7. campi non modificabili rifiutati, e la richiesta e' tutto-o-niente")
for campo in ("name", "graylog_stream_id", "graylog_index_set_id", "weekly_report_enabled", "id"):
    prima = riga("acme"); r = post("acme", {campo: "x"})
    assert r.status_code == 400 and campo in r.get_json()["error"], campo
    assert riga("acme") == prima
prima = riga("acme"); r = post("acme", {"referente": "Nuovo Nome", "name": "hack"})
assert r.status_code == 400 and riga("acme") == prima, "un campo non valido blocca anche quelli validi"
print("    OK")

print("B8. valori non validi, troppo lunghi, richieste malformate")
for v in (123, ["a"], {"x": 1}, True):
    assert post("acme", {"referente": v}).status_code == 400, v
assert post("acme", {"display_name": "x" * 201}).status_code == 400
assert post("acme", {"display_name": "x" * 200}).status_code == 200
assert post("acme", {"note": "n" * 5001}).status_code == 400
for corpo in ({}, [], "testo", None):
    assert post("acme", corpo).status_code == 400, corpo
assert c.post("/d/tenants/acme/anagrafica", data="non json", headers=POSTH).status_code == 400
print("    OK")

print("B9. togliere l'email con il riepilogo settimanale attivo e' vietato")
nuovo("gamma", email="g@gamma.it", weekly_report_enabled=True)
r = post("gamma", {"email": ""}); assert r.status_code == 400 and "disattivalo" in r.get_json()["error"]
assert riga("gamma")["email"] == "g@gamma.it"
assert post("gamma", {"email": "altra@gamma.it"}).status_code == 200, "cambiarla e' consentito"
nuovo("delta", email="d@delta.it", weekly_report_enabled=False)
assert post("delta", {"email": ""}).status_code == 200 and riga("delta")["email"] is None
print("    OK")

print("B10. protezioni: serve il ruolo e il token anti-falsificazione; cliente inesistente")
prima = riga("acme")
assert c.post("/d/tenants/acme/anagrafica", json={"referente": "X"}, headers=ADMIN).status_code == 403, "senza token CSRF"
assert c.post("/d/tenants/acme/anagrafica", json={"referente": "X"}, headers={"X-CSRF-Token": "tok"}).status_code == 403, "senza ruolo"
assert riga("acme") == prima
assert post("nessuno", {"referente": "X"}).status_code == 404
print("    OK")

print("B11. registro di controllo: nomi dei campi, mai i valori")
AUDIT.clear(); post("acme", {"note": "dato riservato del cliente", "email": "riservata@acme.it"})
assert AUDIT and AUDIT[0][0] == "cliente_modificato" and AUDIT[0][1] == "cliente=acme campi=email,note", AUDIT
assert "riservat" not in AUDIT[0][1] and "@" not in AUDIT[0][1]
print("    OK:", AUDIT[0])

print("B12. spazi tolti; testo con HTML/SQL salvato letteralmente (l'interfaccia lo mostra come testo)")
r = post("acme", {"referente": "  Mario <b>Rossi</b>  ", "note": "Robert'); DROP TABLE tenants;--"})
assert r.get_json()["referente"] == "Mario <b>Rossi</b>" and riga("acme")["note"] == "Robert'); DROP TABLE tenants;--"
with app.app_context():
    assert Tenant.query.count() >= 4, "la tabella esiste ancora"
print("    OK")
print("\nTUTTI I CONTROLLI DELL'ANAGRAFICA CLIENTE PASSATI")
