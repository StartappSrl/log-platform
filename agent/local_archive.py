"""
Modulo condiviso (Linux + Windows, solo libreria standard) per l'archiviazione
locale, compressa e sigillata, di TUTTI i log che l'agent raccoglie — non
solo quelli di accesso amministratori (quello è un meccanismo server-side
separato, vedi scripts/seal-admin-logs.sh sul server).

Ogni sorgente (file o canale Event Log) scrive le proprie righe in un file
di log giornaliero locale. Al cambio di giorno (o alla chiusura pulita
dell'agent), il file del giorno precedente viene:
  1. compresso (gzip)
  2. concatenato in una hash chain (ogni giorno include l'hash del giorno
     prima: un'alterazione a posteriori si vede)
  3. marcato temporalmente via TSA esterna (RFC 3161), se configurata e se
     'openssl' è disponibile nel PATH — su Windows non è scontato che ci
     sia: se manca, l'archiviazione compressa e la hash chain funzionano
     comunque, solo senza la marca temporale esterna (verrà segnalato).

Limite onesto, identico a quello del meccanismo server-side: la sigillatura
locale rende evidente una manomissione a posteriori, ma non è WORM hardware
- chi ha accesso pieno alla macchina del cliente potrebbe comunque alterare
i file prima che vengano sigillati (cioè nella finestra tra scrittura e
rotazione). Per la conservazione a norma con valore legale più solido, il
meccanismo server-side (log amministratori, con retention e controlli di
accesso separati) resta quello di riferimento primario.
"""
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import date, datetime
from pathlib import Path


def _today_str() -> str:
    return date.today().isoformat()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _which_openssl() -> str | None:
    return shutil.which("openssl")


class LocalArchiver:
    """Un'istanza per ogni sorgente (file o canale event log) da archiviare.

    source_id: identificatore breve e stabile della sorgente (es. nome file
               sanificato, o nome canale event log) - usato per il nome
               delle sottocartelle.
    archive_dir: cartella radice locale dove finiscono gli archivi.
    tenant, hostname: inclusi nel manifest per contesto.
    tsa_url/tsa_user/tsa_password/tsa_client_cert/tsa_client_key: opzionali,
        stessa configurazione usata lato server per Namirial o altra TSA.
    """

    def __init__(self, source_id: str, archive_dir: str, tenant: str, hostname: str,
                 tsa_url: str | None = None, tsa_user: str | None = None,
                 tsa_password: str | None = None, tsa_client_cert: str | None = None,
                 tsa_client_key: str | None = None, upload_url: str | None = None,
                 upload_token: str | None = None):
        self.source_id = source_id
        safe_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in source_id)
        self.dir = Path(archive_dir) / safe_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tenant = tenant
        self.hostname = hostname
        self.tsa_url = tsa_url
        self.tsa_user = tsa_user
        self.tsa_password = tsa_password
        self.tsa_client_cert = tsa_client_cert
        self.tsa_client_key = tsa_client_key
        self.upload_url = upload_url
        self.upload_token = upload_token

        self._lock = threading.Lock()
        self._current_day = _today_str()
        self._fh = open(self.dir / f"{self._current_day}.log", "a", encoding="utf-8", errors="replace")

    def write_line(self, line: str):
        """Scrive una riga nell'archivio locale del giorno corrente,
        ruotando (e sigillando il giorno precedente) se necessario."""
        with self._lock:
            today = _today_str()
            if today != self._current_day:
                self._rotate_locked(today)
            self._fh.write(line.rstrip("\n") + "\n")
            self._fh.flush()

    def _rotate_locked(self, new_day: str):
        old_day = self._current_day
        self._fh.close()
        self._seal_day(old_day)
        self._current_day = new_day
        self._fh = open(self.dir / f"{new_day}.log", "a", encoding="utf-8", errors="replace")

    def close(self):
        """Da chiamare all'arresto pulito dell'agent: sigilla il giorno
        corrente (anche se non ancora concluso) così non resta mai un
        giorno di log completamente non sigillato per più di un riavvio."""
        with self._lock:
            self._fh.close()
            self._seal_day(self._current_day)

    def _seal_day(self, day: str):
        raw_path = self.dir / f"{day}.log"
        if not raw_path.exists() or raw_path.stat().st_size == 0:
            return  # niente da sigillare

        gz_path = self.dir / f"{day}.log.gz"
        # Se esiste già un .gz per questo giorno (riavvio dell'agent lo
        # stesso giorno), append is not safe per gzip: ricomprimiamo da
        # capo includendo anche le righe già presenti, sovrascrivendo.
        with open(raw_path, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

        file_hash = _sha256_file(gz_path)

        chain_file = self.dir / "chain-state.txt"
        prev_hash = chain_file.read_text().strip() if chain_file.exists() else "0" * 64
        chain_hash = hashlib.sha256((prev_hash + file_hash).encode()).hexdigest()
        chain_file.write_text(chain_hash)

        tsr_name = self._request_timestamp(chain_hash, day)

        manifest_entry = {
            "sealed_at": datetime.now().isoformat(),
            "day": day,
            "tenant": self.tenant,
            "hostname": self.hostname,
            "file": gz_path.name,
            "sha256": file_hash,
            "chain_hash": chain_hash,
            "tsr": tsr_name,
        }
        with open(self.dir / "manifest.jsonl", "a", encoding="utf-8") as mf:
            mf.write(json.dumps(manifest_entry) + "\n")

        # Prova a caricare una copia sul portale, oltre a quella locale.
        # Se fallisce (rete assente, server irraggiungibile, ecc.) non
        # blocca ne' fa fallire la sigillatura: la copia locale resta
        # comunque la fonte primaria, l'upload e' un "anche", non un
        # "invece di". Non c'e' ancora un ritentativo automatico se
        # fallisce - resta solo in locale finche' l'agent non sigilla il
        # prossimo giorno (non e' perso, va solo recuperato a mano se serve).
        if self.upload_url and self.upload_token:
            try:
                tsr_path = (self.dir / tsr_name) if tsr_name else None
                self._upload_sealed_day(day, gz_path, file_hash, chain_hash, tsr_path)
            except Exception as e:
                print(f"Archiviazione: upload al portale fallito per {day} "
                      f"({self.source_id}): {e} - resta comunque salvato in locale.")

        # Il file raw non compresso non serve più una volta sigillato il .gz
        try:
            raw_path.unlink()
        except OSError:
            pass

    def _upload_sealed_day(self, day: str, gz_path: Path, sha256: str, chain_hash: str,
                            tsr_path: Path | None):
        """Carica l'archivio sigillato di un giorno sul portale, con un
        multipart/form-data costruito a mano (solo libreria standard,
        niente 'requests' - lo stesso principio gia' usato per il resto
        dell'agent, per non aggiungere dipendenze da installare sui
        client)."""
        import urllib.request
        import uuid

        boundary = uuid.uuid4().hex
        parts = []

        def add_file_part(field_name: str, filename: str, content: bytes):
            parts.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n".encode()
                + content + b"\r\n"
            )

        add_file_part("archive", gz_path.name, gz_path.read_bytes())
        if tsr_path and tsr_path.exists():
            add_file_part("tsr", tsr_path.name, tsr_path.read_bytes())
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)

        req = urllib.request.Request(
            self.upload_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "X-Tenant": self.tenant,
                "X-Upload-Token": self.upload_token,
                "X-Hostname": self.hostname,
                "X-Source-Id": self.source_id,
                "X-Day": day,
                "X-Sha256": sha256,
                "X-Chain-Hash": chain_hash,
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                raise RuntimeError(f"risposta HTTP {resp.status}")

    def _request_timestamp(self, chain_hash: str, day: str) -> str | None:
        if not self.tsa_url:
            return None
        openssl = _which_openssl()
        if not openssl:
            return None  # nessuna marca temporale possibile senza openssl nel PATH

        tsq_path = self.dir / f"{day}.tsq"
        tsr_path = self.dir / f"{day}.tsr"
        try:
            subprocess.run(
                [openssl, "ts", "-query", "-digest", chain_hash, "-sha256", "-no_nonce", "-out", str(tsq_path)],
                check=True, capture_output=True, timeout=15,
            )
        except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
            return None

        try:
            import urllib.request
            req = urllib.request.Request(
                self.tsa_url,
                data=tsq_path.read_bytes(),
                headers={"Content-Type": "application/timestamp-query"},
                method="POST",
            )
            if self.tsa_user:
                import base64
                creds = base64.b64encode(f"{self.tsa_user}:{self.tsa_password or ''}".encode()).decode()
                req.add_header("Authorization", f"Basic {creds}")
            with urllib.request.urlopen(req, timeout=15) as resp:
                tsr_path.write_bytes(resp.read())
            return tsr_path.name
        except Exception:
            return None


def make_archivers_from_config(sources: list[str], archive_dir: str, tenant: str, hostname: str,
                                tsa_url=None, tsa_user=None, tsa_password=None,
                                tsa_client_cert=None, tsa_client_key=None) -> dict:
    """Crea un LocalArchiver per ciascuna sorgente elencata (nomi file o
    canali event log), ritorna un dict {source_id: LocalArchiver}."""
    return {
        src: LocalArchiver(src, archive_dir, tenant, hostname, tsa_url, tsa_user,
                            tsa_password, tsa_client_cert, tsa_client_key)
        for src in sources
    }
