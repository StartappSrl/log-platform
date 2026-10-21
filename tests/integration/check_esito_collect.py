import io, os, shutil, sys, tarfile, tempfile, time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import SCRIPTS, FAKE_GRAYLOG, APP_SRC, logo_path, wait_port  # noqa: E402
sys.path.insert(0, SCRIPTS)
import esito_notturno as en

VERIFY = os.path.join(SCRIPTS, "verifica_backup.sh")
now = datetime.now()
tmp = tempfile.mkdtemp()


def make_backup(root, name="20261019-025000"):
    d = os.path.join(root, name)
    os.makedirs(os.path.join(d, "module-state", "logplatform-auth16"))
    with open(os.path.join(d, "mariadb-all.sql"), "w") as f:
        for t in ("users", "tenants", "smtp_settings", "audit_log"):
            f.write(f"CREATE TABLE `{t}` (id int);\n")
        f.write("-- Dump completed on 2026-10-19 02:50:00\n")
    with open(os.path.join(d, "mongodb-graylog.archive"), "wb") as f:
        f.write(b'\x00\x01 "db":"graylog" "coll":"streams"')
    def tgz(path, names):
        with tarfile.open(path, "w:gz") as t:
            for n in names:
                data = b"x"
                ti = tarfile.TarInfo("./" + n); ti.size = len(data)
                t.addfile(ti, io.BytesIO(data))
    tgz(os.path.join(d, "ca-volume.tar.gz"), ["ca-key.pem", "ca.pem", "certs.db"])
    tgz(os.path.join(d, "archives-volume.tar.gz"), ["archive_ledger.db"])
    with open(os.path.join(d, "module-state", "logplatform-auth16", "environment"), "w") as f:
        f.write("y")
    os.chmod(d, 0o700)
    return d


def fresh_root(name):
    r = os.path.join(tmp, name); os.makedirs(r); return r


def with_verify(root):
    # lo script legge la radice da BACKUP_DIR solo senza argomento; qui passiamo la cartella
    return lambda cmd, **kw: en.run_cmd(cmd, **kw)


print("=== BACKUP (script di verifica VERO su backup finti) ===")
r = fresh_root("sano"); make_backup(r)
c = en.collect_backup(en.run_cmd, now, r, VERIFY)
print(c["stato"], c["righe"]); assert c["stato"] == "ok"
assert "(0 ore fa)" in c["righe"][0], "mai un'eta' negativa"
assert any("Dimensione" in x for x in c["righe"]) and any("superata" in x for x in c["righe"])

r = fresh_root("corrotto"); d = make_backup(r)
open(os.path.join(d, "ca-volume.tar.gz"), "wb").write(b"\x1f\x8b troncato")
c = en.collect_backup(en.run_cmd, now, r, VERIFY)
print(c["stato"], c["righe"]); assert c["stato"] == "fail"
assert any("corrotto" in x for x in c["righe"]), "deve riportare il motivo vero del fallimento"

r = fresh_root("vecchio"); d = make_backup(r)
old = time.time() - 5 * 86400; os.utime(d, (old, old))
c = en.collect_backup(en.run_cmd, now, r, VERIFY)
print(c["stato"], c["righe"][0], "|", c["righe"][-1]); assert c["stato"] == "fail"
assert any("vecchio" in x for x in c["righe"])

r = fresh_root("avvisi"); d = make_backup(r); os.chmod(d, 0o755)
c = en.collect_backup(en.run_cmd, now, r, VERIFY)
print(c["stato"], c["righe"][-1]); assert c["stato"] == "warn"

c = en.collect_backup(en.run_cmd, now, fresh_root("vuota"), VERIFY)
print(c["stato"], c["righe"]); assert c["stato"] == "fail"
c = en.collect_backup(en.run_cmd, now, "/percorso/inesistente", VERIFY)
assert c["stato"] == "fail"
print("OK: backup sano/corrotto/vecchio/con avvisi/assente distinti correttamente\n")

print("=== DISCO ===")
for pct, atteso in ((12, "ok"), (80, "warn"), (93, "fail"), (75, "warn"), (90, "fail")):
    c = en.collect_disk("/", usage_fn=lambda p, pct=pct: (1000 * 1024**3, pct * 10 * 1024**3, (100 - pct) * 10 * 1024**3))
    assert c["stato"] == atteso, (pct, c); print(f"{pct}% ->", c["stato"])
print("OK: soglie 75/90 rispettate anche sui valori limite\n")

print("=== LOG DEI JOB (report e controllo offline) ===")
def log(name, text, age_h):
    p = os.path.join(tmp, name); open(p, "w").write(text)
    t = time.time() - age_h * 3600; os.utime(p, (t, t)); return p

WARN = ("/usr/local/lib/python3.12/site-packages/requests/__init__.py:113: RequestsDependencyWarning: urllib3 doesn't match\n"
        "  warnings.warn(\n/app/app/x.py:34: LegacyAPIWarning: The Query.get() method is considered legacy\n")
MK = "Report inviato a"
c = en.collect_log_job("Report", log("r1", WARN + "Report inviato a: a@b.it\n", 4), now, 26, MK, "Ultimo invio riuscito")
print("successo coi warning Python ->", c["stato"], c["righe"]); assert c["stato"] == "ok"
c = en.collect_log_job("Report", log("r2", "Error: no container with name or ID \"logplatform-auth\" found: no such container\n" + WARN + "Report inviato a: a@b.it\n", 4), now, 26, MK, "ok")
assert c["stato"] == "ok"; print("vecchio errore poi successo -> ok (vince l'ultimo)")
c = en.collect_log_job("Report", log("r3", "Report inviato a: a@b.it\nERRORE: SMTP non configurato\n", 4), now, 26, MK, "ok")
print("successo poi errore ->", c["stato"], c["righe"]); assert c["stato"] == "fail"
c = en.collect_log_job("Report", log("r4", "Report inviato a: a@b.it\n", 40), now, 26, MK, "ok")
print("log vecchio di 40h ->", c["stato"], c["righe"][0]); assert c["stato"] == "fail"
c = en.collect_log_job("Report", os.path.join(tmp, "non-esiste"), now, 26, MK, "ok"); assert c["stato"] == "fail"
c = en.collect_log_job("Offline", log("o1", WARN + "Controllo dispositivi offline completato.\n", 0.1), now, 1, "Controllo dispositivi offline completato", "ok")
print("offline ok ->", c["stato"], c["righe"]); assert c["stato"] == "ok"
c = en.collect_log_job("Offline", log("o2", "SMTP non configurato: nessun controllo/avviso eseguito.\n", 0.1), now, 1, "Controllo dispositivi offline completato", "ok")
assert c["stato"] == "fail"
c = en.collect_log_job("Offline", log("o3", WARN, 0.1), now, 1, "Controllo dispositivi offline completato", "ok")
print("solo warning, nessun esito ->", c["stato"]); assert c["stato"] == "warn"
c = en.collect_log_job("Offline", log("o4", WARN + "Controllo dispositivi offline completato.\n", 3), now, 1, "Controllo dispositivi offline completato", "ok")
print("controllo offline fermo da 3h ->", c["stato"]); assert c["stato"] == "fail"
print("OK: i warning Python non danno falsi errori; vince l'ultimo esito; log vecchio = cron fermo\n")

print("=== MODULI ===")
home = os.path.join(tmp, "home"); os.makedirs(home)
for m, _ in en.MODULES:
    os.makedirs(os.path.join(home, f"logplatform-{m}{3}"))
def fake_run(down=()):
    def run(cmd, **kw):
        if cmd[0] == "id": return 0, "1001\n", ""
        cname = cmd[cmd.index("--filter") + 1].split("=")[1]
        return 0, ("" if cname in down else "Up 14 hours\n"), ""
    return run
c = en.collect_modules(fake_run(), home); print(c["stato"], c["righe"][:2]); assert c["stato"] == "ok"
assert c["righe"][0] == "mariadb: in esecuzione da 14 ore", "durata tradotta in italiano"
c = en.collect_modules(fake_run(down=("logplatform-graylog",)), home)
assert c["stato"] == "fail" and any("graylog: NON in esecuzione" in x for x in c["righe"]); print("graylog fermo ->", c["stato"])
shutil.rmtree(os.path.join(home, "logplatform-gate3"))
c = en.collect_modules(fake_run(), home)
assert c["stato"] == "fail" and any("gate: istanza non trovata" in x for x in c["righe"]); print("gate assente ->", c["stato"])
print("OK\n")

print("=== TRADUZIONE DURATE PODMAN ===")
casi = {"Up 14 hours": "14 ore", "Up 1 hour": "1 ora", "Up About an hour": "circa un'ora", "Up 2 days": "2 giorni",
        "Up 1 day": "1 giorno", "Up 3 weeks (healthy)": "3 settimane", "Up 45 seconds": "45 secondi",
        "Up Less than a second": "meno di un secondo", "Up 5 minutes": "5 minuti", "Up 2 months": "2 mesi",
        "Up qualcosa di mai visto": "qualcosa di mai visto"}
for inp, att in casi.items():
    assert en.it_uptime(inp) == att, (inp, en.it_uptime(inp), att)
print("OK: %d formati tradotti, quelli sconosciuti restano com'e' (nessun errore)\n" % len(casi))

print("=== NODO ===")
assert en.collect_host(5 * 3600)["stato"] == "warn"; assert en.collect_host(20 * 86400)["stato"] == "ok"
print("OK: riavvio recente segnalato, nodo stabile ok\n")

print("=== ROBUSTEZZA: un controllo rotto non blocca la mail ===")
rc, out, err = en.run_cmd(["comando-inesistente-xyz"]); assert rc == 127 and "non trovato" in err
rc, out, err = en.run_cmd(["sleep", "5"], timeout=1); assert rc == 124 and "timeout" in err
print("comando assente -> 127, comando bloccato -> 124 (nessuna eccezione)")
original = en.collect_disk
def boom(*a, **k): raise OSError("disco illeggibile")
en.collect_disk = boom
res = en.gather(lambda cmd, **kw: (0, "", ""), now)
en.collect_disk = original
assert len(res) == 7, "tutti e sette i controlli devono comparire anche se uno si rompe"
rotto = [c for c in res if c["nome"] == "Spazio disco"][0]
assert rotto["stato"] == "fail" and "Controllo non eseguibile" in rotto["righe"][0] and "disco illeggibile" in rotto["righe"][0]
print("controllo disco rotto ->", rotto["righe"][0])
html_rotto = en.render_html(res, now, "x"); assert "Controllo non eseguibile" in html_rotto
print("OK: la mail viene comunque generata, con la riga rossa che spiega cosa si e' rotto\n")

print("=== TESTO DI SINTESI ===")
mk = lambda *s: [en.check("x", st, []) for st in s]
assert en.summary_text(mk("ok", "ok")) == "Tutto regolare"
assert en.summary_text(mk("ok", "warn")) == "1 avviso da controllare"
assert en.summary_text(mk("warn", "warn")) == "2 avvisi da controllare"
assert en.summary_text(mk("fail", "warn")) == "1 problema da risolvere"
assert en.summary_text(mk("fail", "fail", "ok")) == "2 problemi da risolvere"
print("OK: singolare/plurale e priorita' problema > avviso\n")

print("=== RENDER HTML ===")
host = "gestlogplatform"
ok_checks = [en.check("Flusso dei log", "ok", ["Messaggi ricevuti nelle ultime 24 ore: 18.452", "Dispositivi attivi nelle ultime 24 ore: 9", "difraia: 4 dispositivi", "startapp: 5 dispositivi", "Ultimo messaggio ricevuto: 3 minuti fa"]),
             en.check("Backup", "ok", ["Ultimo backup: 19/10/2026 02:50 (4 ore fa)", "Dimensione: 3.6 MB", "Verifica di integrità: superata"]),
             en.check("Spazio disco", "ok", ["Utilizzo: 12% di 80.0 GB", "Libero: 70.4 GB"]),
             en.check("Report notturno ai clienti", "ok", ["Ultimo invio riuscito", "Ultima esecuzione: 4 ore fa"]),
             en.check("Controllo dispositivi offline", "ok", ["Ultimo controllo riuscito", "Ultima esecuzione: 12 minuti fa"]),
             en.check("Moduli della piattaforma", "ok", [f"{m}: in esecuzione da 14 ore" for m, _ in en.MODULES]),
             en.check("Nodo", "ok", ["Attivo da 21 giorni senza riavvii"])]
bad_checks = [en.check("Flusso dei log", "fail", ["Messaggi ricevuti nelle ultime 24 ore: 0", "Dispositivi attivi nelle ultime 24 ore: nessuno", "Ultimo messaggio ricevuto: 15.0 giorni fa da DESKTOP-MUS7D6U (cliente startapp)"]),
              en.check("Backup", "fail", ["Ultimo backup: 16/09/2026 21:20 (779 ore fa)", "Dimensione: 3.6 MB", "Verifica di integrità: NON superata", "Problema: backup vecchio di 779 ore (soglia 48h): il backup notturno gira ancora?"]),
              en.check("Spazio disco", "warn", ["Utilizzo: 82% di 80.0 GB", "Libero: 14.4 GB"]),
              en.check("Report notturno ai clienti", "fail", ["Ultima esecuzione fallita", "Error: no container with name or ID \"logplatform-auth\" found <script>alert(1)</script>"]),
              en.check("Moduli della piattaforma", "fail", ["mariadb: in esecuzione da 14 ore", "graylog: NON in esecuzione"]),
              en.check("Nodo", "warn", ["Riavviato 14 ore fa", "Verifica che tutti i servizi siano ripartiti"])]
h_ok = en.render_html(ok_checks, now, host); h_bad = en.render_html(bad_checks, now, host)
assert "Tutto regolare" in h_ok and "Problema" not in h_ok.replace("Problema:", "")
assert "4 problemi da risolvere" in h_bad
assert "<script>" not in h_bad and "&lt;script&gt;" in h_bad, "l'HTML nei dettagli deve essere neutralizzato"
assert h_ok.count("cid:logo") == 1
print("OK: banner corretto, contenuto dinamico neutralizzato (nessun <script> iniettabile)")
print("\nTUTTI I TEST PASSATI")
