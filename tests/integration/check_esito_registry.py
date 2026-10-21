import contextlib, glob, io, json, os, subprocess, sys, tempfile, time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import SCRIPTS, FAKE_GRAYLOG, APP_SRC, logo_path, wait_port  # noqa: E402
sys.path.insert(0, SCRIPTS)
import esito_notturno as en

home = tempfile.mkdtemp(); os.makedirs(os.path.join(home, "logplatform-auth16"))
now = datetime.now()
PORT = [18900]


def utc_ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).replace(microsecond=0).isoformat()


def runner(port):
    def run(cmd, input_text=None, **kw):
        if cmd[0] == "id": return 0, "1023\n", ""
        env = dict(os.environ, GRAYLOG_URL=f"http://127.0.0.1:{port}", GRAYLOG_API_USER="a", GRAYLOG_API_PASSWORD="b")
        if "-e" in cmd:
            k, v = cmd[cmd.index("-e") + 1].split("=", 1); env[k] = v
        p = subprocess.run([sys.executable, "-W", "ignore", "-"], input=input_text, capture_output=True, text=True, env=env)
        return p.returncode, p.stdout, p.stderr
    return run


def flow(scenario, reg_path, persist=True):
    PORT[0] += 1
    srv = subprocess.Popen([sys.executable, FAKE_GRAYLOG, scenario, str(PORT[0])]); wait_port(PORT[0])
    try:
        return en.collect_flow(runner(PORT[0]), now, home, registry_path=reg_path, persist=persist)
    finally:
        srv.terminate(); srv.wait()


def write_reg(path, reg):
    os.makedirs(os.path.dirname(path), exist_ok=True); json.dump(reg, open(path, "w"))


def read_reg(path): return json.load(open(path))


def fresh(): return os.path.join(tempfile.mkdtemp(), "sub", "dir", "dispositivi.json")


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = en.main(list(argv))
    return rc, out.getvalue(), err.getvalue()


print("R1. primo avvio: il registro nasce (anche in una cartella che non esiste) e non segnala nulla")
p = fresh(); c = flow("recente", p)
print("   ", c["stato"], c["righe"])
assert c["stato"] == "ok" and not any("non inviano" in r for r in c["righe"])
reg = read_reg(p)
assert set(reg["startapp"]) == {"PST-TCN-01", "NAS-STARTAPP"} and set(reg["difraia"]) == {"SERVER-DIFRAIA"}, reg
assert oct(os.stat(os.path.dirname(p)).st_mode & 0o777) == "0o700", "la cartella del registro deve essere riservata (700)"
assert not os.path.exists(p + ".tmp"), "nessun file temporaneo lasciato in giro"
print("    OK: registro creato con i 3 dispositivi, cartella 700, nessun residuo\n")

print("R2. un dispositivo noto che non invia da 30 ore -> avviso")
p = fresh(); write_reg(p, {"startapp": {"PC-OLD": utc_ago(hours=30)}})
c = flow("recente", p); print("   ", c["stato"], [r for r in c["righe"] if "PC-OLD" in r or "dismesso" in r])
assert c["stato"] == "warn"
assert "PC-OLD (cliente startapp): ultimo messaggio 30 ore fa" in c["righe"]
assert any("--forget NOME" in r for r in c["righe"])
assert "PC-OLD" in read_reg(p)["startapp"], "il dispositivo muto resta nel registro finche' non lo si toglie"
print("    OK\n")

print("R3. muto da 4 giorni -> problema (rosso)")
p = fresh(); write_reg(p, {"startapp": {"PC-OLD": utc_ago(days=4)}})
c = flow("recente", p); print("   ", c["stato"], [r for r in c["righe"] if "PC-OLD" in r])
assert c["stato"] == "fail" and "PC-OLD (cliente startapp): ultimo messaggio 4 giorni fa" in c["righe"]
print("    OK\n")

print("R4. --forget toglie un dispositivo dismesso (senza distinguere maiuscole)")
p = fresh(); write_reg(p, {"startapp": {"PC-OLD": utc_ago(days=4), "ALTRO": utc_ago(hours=1)}, "difraia": {"PC-OLD": utc_ago(days=9)}})
en.REGISTRY_PATH = p
rc, out, err = cli("--forget", "pc-old", "--cliente", "startapp"); print("   ", out.strip())
assert rc == 0 and "Tolto dal registro: PC-OLD (cliente startapp)" in out
assert "PC-OLD" not in read_reg(p)["startapp"] and "PC-OLD" in read_reg(p)["difraia"], "con --cliente tocca solo quel cliente"
rc, out, err = cli("--forget", "PC-OLD"); assert rc == 0 and "difraia" in out
assert "difraia" not in read_reg(p), "un cliente senza piu' dispositivi esce dal registro"
rc, out, err = cli("--forget", "NON-ESISTE"); print("   ", err.strip())
assert rc == 1 and "ALTRO (startapp)" in err, "se non lo trova, elenca quelli noti"
c = flow("recente", p)
assert not any("PC-OLD" in r for r in c["righe"]), "il dispositivo tolto non compare piu'"
assert c["stato"] == "warn" and any(r.startswith("ALTRO (cliente startapp)") for r in c["righe"]), "ALTRO non invia in questo scenario: giusto che resti segnalato"
rc, out, err = cli("--forget", "ALTRO"); assert rc == 0
c = flow("recente", p); assert c["stato"] == "ok", c["righe"]
print("    OK\n")

print("R5. campione TRONCATO: un dispositivo attivo ma quieto non deve essere scambiato per muto")
p = fresh(); write_reg(p, {"startapp": {"QUIET-SRV": utc_ago(hours=22), "BUSY-1": utc_ago(hours=1), "GHOST": utc_ago(days=2)}})
c = flow("quieto", p); print("   ", c["stato"], c["righe"])
assert c["righe"][0] == "Messaggi ricevuti nelle ultime 24 ore: 6.001", c["righe"][0]
assert "Dispositivi attivi nelle ultime 24 ore: almeno 3" in c["righe"]
assert not any(r.startswith("QUIET-SRV (cliente") for r in c["righe"]), "QUIET-SRV ha inviato 20 ore fa: NON e' muto"
assert "GHOST (cliente startapp): ultimo messaggio 2 giorni fa" in c["righe"], "GHOST invece non invia davvero"
assert c["stato"] == "warn"
ts = datetime.fromisoformat(read_reg(p)["startapp"]["QUIET-SRV"]); eta_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
assert 19 < eta_h < 21, f"l'ultimo contatto di QUIET-SRV si aggiorna a ~20 ore fa, non {eta_h:.1f}"
print("    OK: verifica mirata trovata per QUIET-SRV, GHOST segnalato, ultimo contatto aggiornato\n")

print("R6. registro corrotto: messo da parte, ricreato, avviso nella mail")
p = fresh(); os.makedirs(os.path.dirname(p)); open(p, "w").write("{non e' json")
c = flow("recente", p); print("   ", c["stato"], [r for r in c["righe"] if "illeggibile" in r])
assert c["stato"] == "warn" and any("Registro dei dispositivi illeggibile" in r for r in c["righe"])
assert glob.glob(p + ".corrotto-*") and open(glob.glob(p + ".corrotto-*")[0]).read() == "{non e' json", "il vecchio file e' conservato"
assert "PST-TCN-01" in read_reg(p)["startapp"], "il nuovo registro e' valido e gia' popolato"
open(p, "w").write("[1, 2, 3]")
c = flow("recente", p); assert any("illeggibile" in r for r in c["righe"]), "struttura inattesa = corrotto"
print("    OK\n")

print("R7. --dry-run (persist=False) non crea e non modifica il registro")
p = fresh(); flow("recente", p, persist=False); assert not os.path.exists(p), "non deve crearlo"
write_reg(p, {"startapp": {"PC-OLD": utc_ago(days=2)}}); prima = open(p).read()
flow("recente", p, persist=False); assert open(p).read() == prima, "non deve modificarlo"
print("    OK\n")

print("R8. stesso nome host in due clienti: restano distinti")
p = fresh(); write_reg(p, {"startapp": {"PST-TCN-01": utc_ago(hours=1)}, "difraia": {"PST-TCN-01": utc_ago(hours=26)}})
c = flow("recente", p)
assert "PST-TCN-01 (cliente difraia): ultimo messaggio 26 ore fa" in c["righe"]
assert not any("PST-TCN-01 (cliente startapp)" in r for r in c["righe"])
print("    OK\n")

print("R9. --list")
en.REGISTRY_PATH = p      # il registro del test R8, con due clienti e lo stesso host
rc, out, err = cli("--list"); print(out.strip().splitlines()[0])
assert rc == 0 and "startapp / PST-TCN-01" in out and "difraia / PST-TCN-01" in out
en.REGISTRY_PATH = fresh(); rc, out, err = cli("--list"); assert "Nessun dispositivo nel registro" in out
print("    OK\n")

print("R10. nessun messaggio in 24 ore, ma dispositivi noti: tutti elencati come muti")
p = fresh(); write_reg(p, {"startapp": {"A": utc_ago(days=15), "B": utc_ago(days=15)}})
c = flow("vuoto", p); print("   ", c["stato"], c["righe"][:4])
assert c["stato"] == "fail" and "Nessun messaggio ricevuto negli ultimi 30 giorni" in c["righe"]
assert "Dispositivi noti che non inviano da più di 24 ore: 2" in c["righe"]
print("    OK\n")

print("R11. orario illeggibile nel registro (modificato a mano): nessun crash")
p = fresh(); write_reg(p, {"startapp": {"X": "boh", "Y": utc_ago(hours=1).replace("+00:00", "")}})
c = flow("recente", p); print("   ", c["stato"], [r for r in c["righe"] if "(cliente startapp)" in r])
assert any(r.startswith("X (cliente startapp)") for r in c["righe"])
print("    OK\n")

print("R12. molti dispositivi muti: elenco limitato a 10")
p = fresh(); write_reg(p, {"startapp": {f"PC-{i:02d}": utc_ago(days=5) for i in range(14)}})
c = flow("recente", p)
assert sum(1 for r in c["righe"] if r.startswith("PC-") and "(cliente startapp)" in r) == 10 and "… e altri 4" in c["righe"]
print("    OK\n")

print("R13. durate al singolare/plurale")
for sec, att in ((20, "1 minuto fa"), (90, "2 minuti fa"), (3600, "60 minuti fa"), (8000, "2 ore fa"), (3 * 86400, "3 giorni fa"),
                 (86400 * 1.0, "24 ore fa")):
    assert en.fmt_age(sec) == att, (sec, en.fmt_age(sec), att)
assert en.fmt_age(30) == "1 minuto fa" and en.fmt_age(3.5 * 3600) == "4 ore fa" and en.fmt_age(200000) == "2 giorni fa"
print("    OK\n")
print("TUTTI I TEST DEL REGISTRO PASSATI (13 situazioni)")
