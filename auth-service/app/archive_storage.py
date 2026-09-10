"""
Storage lato server per gli archivi sigillati (log compressi + hash chain
+ marca temporale opzionale) caricati dagli agent - invece di restare
solo sul PC del cliente, una copia arriva e resta sul portale.

Persistenza: ARCHIVE_DIR (default /data/archives) deve essere un volume
che sopravvive ai riavvii del container, come CA_DIR.
"""
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

ARCHIVE_DIR = Path(os.environ.get("ARCHIVE_DIR", "/data/archives"))
LEDGER_PATH = ARCHIVE_DIR / "archive_ledger.db"

DEFAULT_RETENTION_MONTHS = int(os.environ.get("ARCHIVE_RETENTION_MONTHS", "24"))


def _connect():
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LEDGER_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tenant_tokens (
            tenant TEXT PRIMARY KEY,
            token TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS uploaded_days (
            tenant TEXT NOT NULL,
            hostname TEXT NOT NULL,
            source_id TEXT NOT NULL,
            day TEXT NOT NULL,
            sha256 TEXT,
            chain_hash TEXT,
            has_tsr INTEGER NOT NULL DEFAULT 0,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            uploaded_at TEXT NOT NULL,
            PRIMARY KEY (tenant, hostname, source_id, day)
        )
    """)
    conn.commit()
    return conn


def get_or_create_upload_token(tenant: str) -> str:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT token FROM tenant_tokens WHERE tenant = ?", (tenant,)).fetchone()
        if row:
            return row[0]
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO tenant_tokens (tenant, token) VALUES (?, ?)", (tenant, token))
        conn.commit()
        return token


def verify_upload_token(tenant: str, token: str) -> bool:
    if not token:
        return False
    with closing(_connect()) as conn:
        row = conn.execute("SELECT token FROM tenant_tokens WHERE tenant = ?", (tenant,)).fetchone()
        return bool(row) and row[0] == token


def _host_dir(tenant: str, hostname: str, source_id: str) -> Path:
    safe = lambda s: "".join(c if c.isalnum() or c in "-_." else "_" for c in s)
    return ARCHIVE_DIR / safe(tenant) / safe(hostname) / safe(source_id)


def store_uploaded_day(tenant: str, hostname: str, source_id: str, day: str,
                        gz_bytes: bytes, sha256: str, chain_hash: str,
                        tsr_bytes: bytes | None = None) -> None:
    d = _host_dir(tenant, hostname, source_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day}.log.gz").write_bytes(gz_bytes)
    if tsr_bytes:
        (d / f"{day}.tsr").write_bytes(tsr_bytes)

    with closing(_connect()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO uploaded_days "
            "(tenant, hostname, source_id, day, sha256, chain_hash, has_tsr, size_bytes, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tenant, hostname, source_id, day, sha256, chain_hash, int(bool(tsr_bytes)),
             len(gz_bytes), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def list_archives_for_tenant(tenant: str) -> list:
    """Ritorna un elenco raggruppato per host+sorgente, con il conteggio
    giorni disponibili e l'intervallo di date."""
    with closing(_connect()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT hostname, source_id, day, has_tsr FROM uploaded_days "
            "WHERE tenant = ? ORDER BY hostname, source_id, day",
            (tenant,),
        ).fetchall()

    grouped = {}
    for r in rows:
        key = (r["hostname"], r["source_id"])
        if key not in grouped:
            grouped[key] = {"hostname": r["hostname"], "source_id": r["source_id"],
                             "days": [], "days_with_tsr": 0}
        grouped[key]["days"].append(r["day"])
        if r["has_tsr"]:
            grouped[key]["days_with_tsr"] += 1

    result = []
    for (hostname, source_id), info in grouped.items():
        days = sorted(info["days"])
        result.append({
            "hostname": hostname, "source_id": source_id,
            "total_days": len(days), "days_with_tsr": info["days_with_tsr"],
            "first_day": days[0], "last_day": days[-1],
        })
    return sorted(result, key=lambda x: (x["hostname"], x["source_id"]))


def list_archive_files_for_tenant(tenant: str) -> list:
    """Elenco 'piatto': una riga per ogni singolo file archiviato (un
    giorno di un host/sorgente), non raggruppato - per una tabella stile
    file manager con un pulsante Download per riga."""
    with closing(_connect()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT hostname, source_id, day, has_tsr, size_bytes, uploaded_at "
            "FROM uploaded_days WHERE tenant = ? ORDER BY day DESC, hostname, source_id",
            (tenant,),
        ).fetchall()
        return [dict(r) for r in rows]


def build_zip_for_single_day(tenant: str, hostname: str, source_id: str, day: str) -> bytes:
    """ZIP con il file di un singolo giorno (+ la sua marca temporale, se
    presente) - per il pulsante Download di una singola riga."""
    import zipfile
    import io

    d = _host_dir(tenant, hostname, source_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for suffix in (".log.gz", ".tsr"):
            f = d / f"{day}{suffix}"
            if f.exists():
                zf.write(f, arcname=f.name)
    return buf.getvalue()


def build_zip_for_entire_tenant(tenant: str) -> bytes:
    """ZIP con TUTTI gli archivi di un cliente, organizzati in cartelle
    per host/sorgente dentro lo ZIP - per il pulsante 'Scarica tutto'."""
    import zipfile
    import io

    safe = lambda s: "".join(c if c.isalnum() or c in "-_." else "_" for c in s)
    tenant_dir = ARCHIVE_DIR / safe(tenant)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if tenant_dir.exists():
            for f in sorted(tenant_dir.rglob("*")):
                if f.is_file():
                    # percorso relativo dentro lo zip: hostname/sorgente/giorno.log.gz
                    arcname = f.relative_to(tenant_dir)
                    zf.write(f, arcname=str(arcname))
    return buf.getvalue()


def list_available_days_for_tenant(tenant: str) -> list:
    """Elenco dei giorni distinti per cui esiste almeno un archivio di
    questo cliente - per popolare il selettore 'Scarica giorno'."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT DISTINCT day FROM uploaded_days WHERE tenant = ? ORDER BY day DESC",
            (tenant,),
        ).fetchall()
        return [r[0] for r in rows]


def build_zip_for_tenant_day(tenant: str, day: str) -> bytes:
    """ZIP con TUTTI gli host/sorgenti di un cliente ma solo per UN
    giorno specifico, organizzati in cartelle per host/sorgente dentro
    lo ZIP - per il pulsante 'Scarica giorno'."""
    import zipfile
    import io

    with closing(_connect()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT hostname, source_id, has_tsr FROM uploaded_days WHERE tenant = ? AND day = ?",
            (tenant, day),
        ).fetchall()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for r in rows:
            d = _host_dir(tenant, r["hostname"], r["source_id"])
            for suffix in (".log.gz", ".tsr"):
                f = d / f"{day}{suffix}"
                if f.exists():
                    arcname = f"{r['hostname']}/{r['source_id']}/{f.name}"
                    zf.write(f, arcname=arcname)
    return buf.getvalue()


def build_zip_for_host_source(tenant: str, hostname: str, source_id: str) -> bytes:
    import zipfile
    import io

    d = _host_dir(tenant, hostname, source_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if d.exists():
            for f in sorted(d.iterdir()):
                zf.write(f, arcname=f.name)
    return buf.getvalue()



def prune_expired_archives(retention_months: int | None = None) -> int:
    """Cancella file e voci di ledger piu' vecchie della soglia di
    conservazione configurata. Ritorna il numero di giorni rimossi.
    Va richiamata periodicamente (es. ad ogni caricamento, o ad ogni
    apertura della schermata Archivi nel pannello - e' economica se non
    c'e' nulla da rimuovere)."""
    months = retention_months if retention_months is not None else DEFAULT_RETENTION_MONTHS
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=months * 31)).date()

    removed = 0
    with closing(_connect()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT tenant, hostname, source_id, day FROM uploaded_days").fetchall()
        for r in rows:
            try:
                day_date = datetime.strptime(r["day"], "%Y-%m-%d").date()
            except ValueError:
                continue
            if day_date < cutoff_date:
                d = _host_dir(r["tenant"], r["hostname"], r["source_id"])
                for suffix in (".log.gz", ".tsr"):
                    p = d / f"{r['day']}{suffix}"
                    if p.exists():
                        p.unlink()
                conn.execute(
                    "DELETE FROM uploaded_days WHERE tenant=? AND hostname=? AND source_id=? AND day=?",
                    (r["tenant"], r["hostname"], r["source_id"], r["day"]),
                )
                removed += 1
        conn.commit()
    return removed
