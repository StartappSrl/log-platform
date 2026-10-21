"""
Controllo di scripts/verifica_backup.sh su backup finti: sano, corrotto, incompleto, vecchio, assente,
e il caso del FALSO ALLARME (SIGPIPE con pipefail) che ha fatto fallire per errore un backup sano:
un volume archivi grande, verificato piu' volte, deve dare SEMPRE lo stesso esito.
"""
import io, os, subprocess, sys, tarfile, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import SCRIPTS  # noqa: E402

SCRIPT = os.path.join(SCRIPTS, "verifica_backup.sh")
ROOT = tempfile.mkdtemp()
fallimenti = []


def tgz(path, nomi):
    with tarfile.open(path, "w:gz") as t:
        for n in nomi:
            ti = tarfile.TarInfo(n); ti.size = 1
            t.addfile(ti, io.BytesIO(b"x"))


def backup(nome, extra=0, ledger=True):
    d = os.path.join(ROOT, nome)
    os.makedirs(os.path.join(d, "module-state", "logplatform-auth16"))
    with open(os.path.join(d, "mariadb-all.sql"), "w") as f:
        for tab in ("users", "tenants", "smtp_settings", "audit_log"):
            f.write(f"CREATE TABLE `{tab}` (id int);\n")
        f.write("-- Dump completed on 2026-10-19 02:50:00\n")
    with open(os.path.join(d, "mongodb-graylog.archive"), "wb") as f:
        f.write(b'\x00 "db":"graylog"')
    tgz(os.path.join(d, "ca-volume.tar.gz"), ["./ca-key.pem", "./ca.pem", "./certs.db"])
    voci = (["./archive_ledger.db"] if ledger else []) + [f"./startapp/2026/10/archivio-log-molto-lungo-nome-{i:06d}.jsonl.gz" for i in range(extra)]
    tgz(os.path.join(d, "archives-volume.tar.gz"), voci or ["./altro-file.txt"])
    open(os.path.join(d, "module-state", "logplatform-auth16", "environment"), "w").write("y")
    os.chmod(d, 0o700)
    return d


def verifica(d, env=None):
    p = subprocess.run(["bash", SCRIPT, d] if d else ["bash", SCRIPT], capture_output=True, text=True,
                       env=dict(os.environ, **(env or {})))
    return p.returncode, p.stdout


def atteso(nome, rc, rc_atteso, contiene=None, out=""):
    ok = rc == rc_atteso and (contiene is None or contiene in out)
    print(f"{'OK    ' if ok else 'ERRATO'} {nome} (uscita {rc}, attesa {rc_atteso})")
    if not ok:
        fallimenti.append(nome)
        print(out[-600:])


rc, out = verifica(backup("sano")); atteso("backup sano", rc, 0, "backup integro", out)

d = backup("corrotto"); open(os.path.join(d, "ca-volume.tar.gz"), "wb").write(b"\x1f\x8b troncato")
rc, out = verifica(d); atteso("tar della CA corrotto", rc, 1, "corrotto", out)

d = backup("sql"); s = open(os.path.join(d, "mariadb-all.sql")).read().replace("`users`", "`altro`").replace("-- Dump completed on 2026-10-19 02:50:00\n", "")
open(os.path.join(d, "mariadb-all.sql"), "w").write(s)
rc, out = verifica(d); atteso("SQL senza tabella users e senza chiusura", rc, 1, "users", out)

d = backup("mongo"); open(os.path.join(d, "mongodb-graylog.archive"), "w").close()
rc, out = verifica(d); atteso("archivio mongo vuoto", rc, 1, "mongodb-graylog.archive", out)

d = backup("senzachiave"); tgz(os.path.join(d, "ca-volume.tar.gz"), ["./ca.pem", "./certs.db"])
rc, out = verifica(d); atteso("volume CA senza la chiave privata", rc, 1, "ca-key.pem", out)

d = backup("vecchio"); t = time.time() - 5 * 86400; os.utime(d, (t, t))
rc, out = verifica(d); atteso("backup vecchio di 5 giorni", rc, 1, "vecchio", out)

vuota = os.path.join(ROOT, "radice-vuota"); os.makedirs(vuota)
rc, out = verifica(None, {"BACKUP_DIR": vuota}); atteso("nessun backup presente", rc, 1, "Nessun backup", out)

d = backup("permessi"); os.chmod(d, 0o755)
rc, out = verifica(d); atteso("permessi 755 (solo avviso)", rc, 0, "permessi", out)

rc, out = verifica(backup("senzaregistro", ledger=False)); atteso("volume archivi senza registro", rc, 1, "archive_ledger.db", out)

# --- il falso allarme: stesso backup sano, verificato piu' volte, con volumi grandi ---
RIPETIZIONI = 12
for extra in (600, 30000):
    d = backup(f"grande{extra}", extra=extra)
    esiti = [verifica(d)[0] for _ in range(RIPETIZIONI)]
    ok = esiti == [0] * RIPETIZIONI
    print(f"{'OK    ' if ok else 'ERRATO'} volume con {extra} file, registro presente: {esiti.count(0)}/{RIPETIZIONI} verifiche superate")
    if not ok:
        fallimenti.append(f"falso allarme con {extra} file")
d = backup("grande-senza", extra=30000, ledger=False)
esiti = [verifica(d)[0] for _ in range(RIPETIZIONI)]
ok = esiti == [1] * RIPETIZIONI
print(f"{'OK    ' if ok else 'ERRATO'} volume con 30000 file, registro ASSENTE: {esiti.count(1)}/{RIPETIZIONI} verifiche fallite (devono esserlo tutte)")
if not ok:
    fallimenti.append("registro assente non rilevato sempre")

if fallimenti:
    print("\nFALLITI:", fallimenti)
    sys.exit(1)
print("\nTUTTI I CONTROLLI DEL BACKUP PASSATI")
