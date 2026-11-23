"""
Controllo: un registro degli archivi (archive_ledger.db) creato da una versione precedente, senza la
colonna size_bytes, viene aggiornato da solo e i caricamenti tornano a funzionare. Era il guasto visto
in produzione: dal 16/09 ogni caricamento falliva con "no column named size_bytes" e il portale
mostrava solo i giorni registrati prima.
"""
import os, sqlite3, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import REPO  # noqa: E402

cartella = tempfile.mkdtemp()
os.environ["ARCHIVE_DIR"] = cartella
sys.path.insert(0, os.path.join(REPO, "auth-service", "app"))
import archive_storage as s  # noqa: E402

falliti = []


def passo(nome, cond, dett=""):
    print(("OK    " if cond else "ERRATO") + " " + nome + (f" | {dett}" if not cond else ""))
    if not cond:
        falliti.append(nome)


# Registro "vecchio", come quello creato in produzione prima dell'aggiunta di size_bytes
vecchio = sqlite3.connect(os.path.join(cartella, "archive_ledger.db"))
vecchio.execute("CREATE TABLE tenant_tokens (tenant TEXT PRIMARY KEY, token TEXT NOT NULL)")
vecchio.execute("""CREATE TABLE uploaded_days (tenant TEXT NOT NULL, hostname TEXT NOT NULL,
    source_id TEXT NOT NULL, day TEXT NOT NULL, sha256 TEXT, chain_hash TEXT,
    has_tsr INTEGER NOT NULL DEFAULT 0, uploaded_at TEXT NOT NULL,
    PRIMARY KEY (tenant, hostname, source_id, day))""")
vecchio.execute("INSERT INTO uploaded_days VALUES ('t','PC-01','System','2026-09-10','a','b',0,'2026-09-10T00:00:00')")
vecchio.commit(); vecchio.close()

try:
    s.store_uploaded_day("t", "PC-01", "System", "2026-11-22", b"dati-gz", "sha", "chain", tsr_bytes=b"tsr")
    passo("caricamento su registro vecchio", True)
except Exception as e:  # noqa: BLE001
    passo("caricamento su registro vecchio", False, repr(e))

archivi = s.list_archives_for_tenant("t")
passo("giorno vecchio e nuovo visibili", len(archivi) == 1 and archivi[0]["total_days"] == 2
      and archivi[0]["last_day"] == "2026-11-22" and archivi[0]["days_with_tsr"] == 1, archivi)
c = sqlite3.connect(os.path.join(cartella, "archive_ledger.db"))
passo("dimensione registrata", c.execute("SELECT size_bytes FROM uploaded_days WHERE day='2026-11-22'").fetchone() == (7,))
passo("riga vecchia conservata (size_bytes 0)", c.execute("SELECT size_bytes FROM uploaded_days WHERE day='2026-09-10'").fetchone() == (0,))
s.store_uploaded_day("t", "PC-01", "System", "2026-11-23", b"x", "sha", "chain")
passo("aggiornamento ripetibile (seconda apertura)", len(s.list_archives_for_tenant("t")) == 1)

print()
if falliti:
    print(f"{len(falliti)} controlli falliti")
    sys.exit(1)
print("registro archivi: tutti i controlli superati")
