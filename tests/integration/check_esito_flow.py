import os, subprocess, sys, tempfile, time
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import SCRIPTS, FAKE_GRAYLOG, APP_SRC, logo_path, wait_port  # noqa: E402
sys.path.insert(0, SCRIPTS)
import esito_notturno as en

home = tempfile.mkdtemp(); os.makedirs(os.path.join(home, "logplatform-auth16"))
now = datetime.now()
PORT = 18790

def runner(port, podman_rc=0, garbage=False):
    def run(cmd, input_text=None, **kw):
        if cmd[0] == "id": return 0, "1023\n", ""
        if podman_rc: return podman_rc, "", "Error: no such container"
        if garbage: return 0, "qualcosa di inatteso\n", ""
        env = dict(os.environ, GRAYLOG_URL=f"http://127.0.0.1:{port}", GRAYLOG_API_USER="a", GRAYLOG_API_PASSWORD="b")
        if "-e" in cmd:
            k, v = cmd[cmd.index("-e") + 1].split("=", 1); env[k] = v
        p = subprocess.run([sys.executable, "-W", "ignore", "-"], input=input_text, capture_output=True, text=True, env=env)
        return p.returncode, p.stdout, p.stderr
    return run

def scenario(name, port, **rk):
    srv = subprocess.Popen([sys.executable, FAKE_GRAYLOG, name, str(port)]); wait_port(port)
    try:
        return en.collect_flow(runner(port, **rk), now, home, registry_path=os.path.join(tempfile.mkdtemp(), 'r.json'))
    finally:
        srv.terminate(); srv.wait()

print("A. flusso normale"); c = scenario("recente", PORT)
print("  ", c["stato"], c["righe"]); assert c["stato"] == "ok"
assert any("Dispositivi attivi nelle ultime 24 ore: 3" in r for r in c["righe"]), "3 dispositivi (2 startapp + 1 difraia)"
assert "startapp: 2 dispositivi" in c["righe"] and "difraia: 1 dispositivo" in c["righe"]
assert any("minuti fa" in r for r in c["righe"])

print("B. fermo da 15 giorni (il caso del 4 ottobre)"); c = scenario("fermo", PORT + 1)
print("  ", c["stato"], c["righe"]); assert c["stato"] == "fail"
assert "Messaggi ricevuti nelle ultime 24 ore: 0" in c["righe"][0]
assert any("giorni fa" in r and "(cliente" in r for r in c["righe"]), "deve dire da quanto tempo e da chi"

print("C. volume alto: campione troncato"); c = scenario("tanti", PORT + 2)
print("  ", c["stato"], c["righe"][:2]); assert c["stato"] == "ok"
assert c["righe"][0] == "Messaggi ricevuti nelle ultime 24 ore: 6.000", c["righe"][0]
assert any("almeno" in r for r in c["righe"]), "con campione troncato deve dire 'almeno'"

print("D. Graylog risponde 500"); c = scenario("errore", PORT + 3)
print("  ", c["stato"], c["righe"]); assert c["stato"] == "fail" and "non ha risposto" in c["righe"][0]

print("E. nessun messaggio mai"); c = scenario("vuoto", PORT + 4)
print("  ", c["stato"], c["righe"][-1]); assert c["stato"] == "fail" and "Nessun messaggio ricevuto negli ultimi 30 giorni" in c["righe"][-1]

print("F. container non raggiungibile"); c = en.collect_flow(runner(0, podman_rc=125), now, home, registry_path=os.path.join(tempfile.mkdtemp(), 'r.json'))
print("  ", c["stato"], c["righe"]); assert c["stato"] == "fail" and "no such container" in c["righe"][1]

print("G. risposta illeggibile"); c = en.collect_flow(runner(0, garbage=True), now, home, registry_path=os.path.join(tempfile.mkdtemp(), 'r.json'))
print("  ", c["stato"], c["righe"]); assert c["stato"] == "fail" and "non leggibile" in c["righe"][0]

print("H. istanza auth assente"); c = en.collect_flow(runner(0), now, tempfile.mkdtemp(), registry_path=os.path.join(tempfile.mkdtemp(), 'r.json'))
print("  ", c["stato"], c["righe"]); assert c["stato"] == "fail"
print("\nTUTTI I TEST DEL FLUSSO PASSATI (8 scenari)")
