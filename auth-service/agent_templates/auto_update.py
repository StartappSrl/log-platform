"""
Aggiornamento automatico dell'agent.

Ogni qualche ora l'agent chiede al portale se c'è una versione nuova
(/_authgate/agent-update, autenticato con lo stesso token usato per caricare
gli archivi). Il portale risponde solo se per quel cliente l'aggiornamento
automatico è acceso nel pannello.

Sicurezza: l'agent installa SOLO file descritti da un "manifesto" firmato con
la chiave Ed25519 di rilascio. La chiave privata esiste solo nei segreti
GitHub del repository log-platform (la usa il workflow che costruisce i
rilasci); qui c'è solo la pubblica. Quindi nemmeno chi controllasse il portale
potrebbe far installare codice ai PC dei clienti. Il manifesto indica
piattaforma, versione, dimensione e SHA-256 del pacchetto; si accettano solo
versioni più nuove di quella installata (niente ritorno a versioni vecchie e
vulnerabili).

Installazione:
  Linux   -> sostituisce i file .py accanto ad agent.py (dopo aver provato che
             si importano) e chiude il processo: systemd (Restart=always) lo
             riavvia con il codice nuovo. agent.ini e certificati non si toccano.
  Windows -> estrae il pacchetto in una cartella del profilo SYSTEM (non
             scrivibile dagli utenti), poi un'attività pianificata come SYSTEM
             ferma il servizio, copia i file nuovi (agent.ini, certificati e
             stato esclusi), lo riavvia e, se dopo un minuto non è in
             esecuzione, rimette la versione precedente.
  Docker  -> nessun aggiornamento automatico (si aggiorna l'immagine).
"""
import base64
import hashlib
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import ed25519_verify
from agent_version import AGENT_VERSION

# Chiave pubblica di firma dei rilasci (Ed25519). La privata è il segreto
# AGENT_SIGNING_KEY del repository GitHub StartappSrl/log-platform.
PUBLIC_KEY = bytes.fromhex("0a9cf032322515a7c9f0194f90830a6a95f918d1983422de734c24bf0cb8bf09")

CHECK_INTERVAL_HOURS = 6
FIRST_CHECK_DELAY_S = (300, 1800)    # primo controllo tra 5 e 30 minuti dall'avvio
MAX_PACKAGE_BYTES = 300 * 1024 * 1024
WINDOWS_SERVICE = "LogPlatformAgent"
WINDOWS_TASK = "LogPlatformAgentAggiornamento"
LINUX_REQUIRED = {"agent.py", "gelf_transport.py", "auto_update.py", "ed25519_verify.py", "agent_version.py"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


class UpdateError(Exception):
    pass


def parse_version(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.strip().lstrip("v").split("-")[0].split("."))
    except (ValueError, AttributeError):
        return (0,)


def update_url_from_upload_url(upload_url: str | None) -> str | None:
    """https://portale/_authgate/archive-upload -> https://portale/_authgate/agent-update"""
    if not upload_url:
        return None
    parts = urllib.parse.urlsplit(upload_url)
    if parts.scheme != "https" or not parts.netloc:
        return None
    return f"https://{parts.netloc}/_authgate/agent-update"


def _headers(tenant: str, token: str) -> dict:
    return {"X-Tenant": tenant, "X-Upload-Token": token,
            "User-Agent": f"logplatform-agent/{AGENT_VERSION}"}


def check_for_update(base_url: str, tenant: str, token: str, platform: str,
                     current: str = AGENT_VERSION, opener=urllib.request.urlopen,
                     public_key: bytes = PUBLIC_KEY) -> dict | None:
    """Ritorna il manifesto (già verificato) di una versione più nuova, oppure None."""
    query = urllib.parse.urlencode({"platform": platform, "current": current})
    req = urllib.request.Request(f"{base_url}?{query}", headers=_headers(tenant, token))
    with opener(req, timeout=30) as resp:
        if resp.status == 204:
            return None
        data = json.loads(resp.read(1024 * 1024))
    try:
        manifest_bytes = base64.b64decode(data["manifest"], validate=True)
        signature = base64.b64decode(data["signature"], validate=True)
    except (KeyError, ValueError, TypeError) as e:
        raise UpdateError(f"risposta del portale non valida: {e}")
    if not ed25519_verify.verify(public_key, manifest_bytes, signature):
        raise UpdateError("firma del manifesto NON valida: aggiornamento rifiutato")
    manifest = json.loads(manifest_bytes)
    if manifest.get("platform") != platform:
        raise UpdateError(f"manifesto per un'altra piattaforma ({manifest.get('platform')})")
    if not isinstance(manifest.get("size"), int) or not 0 < manifest["size"] <= MAX_PACKAGE_BYTES:
        raise UpdateError("dimensione del pacchetto non valida")
    if not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("sha256", ""))):
        raise UpdateError("SHA-256 mancante nel manifesto")
    if parse_version(str(manifest.get("version", ""))) <= parse_version(current):
        return None
    return manifest


def download_package(base_url: str, tenant: str, token: str, manifest: dict,
                     opener=urllib.request.urlopen) -> bytes:
    query = urllib.parse.urlencode({"platform": manifest["platform"], "version": manifest["version"]})
    req = urllib.request.Request(f"{base_url}/download?{query}", headers=_headers(tenant, token))
    with opener(req, timeout=300) as resp:
        data = resp.read(MAX_PACKAGE_BYTES + 1)
    if len(data) != manifest["size"] or hashlib.sha256(data).hexdigest() != manifest["sha256"]:
        raise UpdateError("il pacchetto scaricato non corrisponde al manifesto firmato")
    return data


# --- Linux -------------------------------------------------------------------------------

def apply_linux(data: bytes, install_dir: Path, manifest: dict, python: str = sys.executable) -> list:
    """Sostituisce i file dell'agent in install_dir. Ritorna i nomi dei file aggiornati."""
    install_dir = Path(install_dir)
    staging = install_dir / f".aggiornamento-{manifest['version']}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(mode=0o700)
    try:
        names = []
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile() or not _SAFE_NAME.match(member.name) or member.name.startswith("."):
                    raise UpdateError(f"voce non ammessa nel pacchetto: {member.name!r}")
                (staging / member.name).write_bytes(tar.extractfile(member).read())
                names.append(member.name)
        missing = LINUX_REQUIRED - set(names)
        if missing:
            raise UpdateError(f"file mancanti nel pacchetto: {sorted(missing)}")
        # Il codice nuovo deve almeno importarsi senza errori prima di sostituire quello in uso
        prova = subprocess.run(
            [python, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
             "import agent, gelf_transport, auto_update, local_archive, inventory", str(staging)],
            capture_output=True, timeout=120)
        if prova.returncode != 0:
            raise UpdateError("la versione nuova non si avvia: "
                              + prova.stderr.decode(errors="replace")[-500:])
        backup = install_dir / ".versione-precedente"
        shutil.rmtree(backup, ignore_errors=True)
        backup.mkdir(mode=0o700)
        for name in names:
            if (install_dir / name).exists():
                shutil.copy2(install_dir / name, backup / name)
        for name in names:
            os.replace(staging / name, install_dir / name)
        return names
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# --- Windows -----------------------------------------------------------------------------

def windows_work_dir() -> Path:
    """Cartella nel profilo dell'account SYSTEM (il servizio gira come LocalSystem):
    gli utenti normali non possono scriverci, quindi nessuno può sostituire
    lo script o i file tra il download e l'installazione."""
    root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return root / "System32" / "config" / "systemprofile" / "AppData" / "Local" / "LogPlatformAgent"


def _ps_quote(path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def windows_apply_script(install_dir: Path, staging: Path, backup: Path, log: Path, version: str) -> str:
    return f"""$ErrorActionPreference = 'Stop'
$svc = '{WINDOWS_SERVICE}'
$inst = {_ps_quote(install_dir)}
$new = {_ps_quote(staging)}
$bak = {_ps_quote(backup)}
$log = {_ps_quote(log)}
function Write-Log($m) {{ Add-Content -Path $log -Value ("{{0}} {{1}}" -f (Get-Date -Format s), $m) }}
function Wait-Status($st, $sec) {{
  $t = 0
  while ((Get-Service $svc).Status -ne $st -and $t -lt $sec) {{ Start-Sleep 1; $t++ }}
  return ((Get-Service $svc).Status -eq $st)
}}
try {{
  Write-Log 'inizio aggiornamento alla versione {version}'
  Stop-Service $svc -Force
  if (-not (Wait-Status 'Stopped' 90)) {{ throw 'il servizio non si ferma' }}
  Start-Sleep 2
  if (Test-Path $bak) {{ Remove-Item $bak -Recurse -Force }}
  New-Item -ItemType Directory $bak | Out-Null
  robocopy $inst $bak /E /R:2 /W:2 /NFL /NDL /NJH /NJS | Out-Null
  if ($LASTEXITCODE -ge 8) {{ throw "copia di sicurezza non riuscita ($LASTEXITCODE)" }}
  robocopy $new $inst /E /R:5 /W:2 /XF agent.ini *.pem agent_state.json /NFL /NDL /NJH /NJS | Out-Null
  if ($LASTEXITCODE -ge 8) {{ throw "copia dei file nuovi non riuscita ($LASTEXITCODE)" }}
  Start-Service $svc
  Start-Sleep 60
  if ((Get-Service $svc).Status -ne 'Running') {{ throw 'il servizio non resta avviato con la versione nuova' }}
  Write-Log 'aggiornamento completato'
}} catch {{
  Write-Log ('errore: ' + $_)
  try {{
    Stop-Service $svc -Force -ErrorAction SilentlyContinue
    Wait-Status 'Stopped' 60 | Out-Null
    if (Test-Path (Join-Path $bak 'logplatform-agent.exe')) {{
      robocopy $bak $inst /E /R:5 /W:2 /NFL /NDL /NJH /NJS | Out-Null
      Write-Log 'rimessa la versione precedente'
    }}
    Set-Content -Path (Join-Path (Split-Path $log) 'versione-scartata.txt') -Value '{version}'
  }} catch {{ Write-Log ('errore nel ripristino: ' + $_) }}
  Start-Service $svc -ErrorAction SilentlyContinue
}} finally {{
  schtasks.exe /Delete /TN '{WINDOWS_TASK}' /F | Out-Null
  Remove-Item $new -Recurse -Force -ErrorAction SilentlyContinue
}}
"""


def prepare_windows(data: bytes, install_dir: Path, manifest: dict, work_dir: Path | None = None) -> Path:
    """Estrae il pacchetto e scrive lo script di installazione. Ritorna il percorso dello script."""
    work = Path(work_dir or windows_work_dir())
    work.mkdir(parents=True, exist_ok=True)
    staging = work / f"versione-{manifest['version']}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for entry in z.infolist():
            name = entry.filename.replace("\\", "/")
            parts = [p for p in name.split("/") if p]
            if (name.startswith("/") or ":" in name or any(p in ("..", ".") for p in parts)
                    or not parts):
                raise UpdateError(f"voce non ammessa nel pacchetto: {entry.filename!r}")
            target = staging.joinpath(*parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(entry))
    if not (staging / "logplatform-agent.exe").is_file():
        raise UpdateError("logplatform-agent.exe mancante nel pacchetto")
    script = work / "applica-aggiornamento.ps1"
    script.write_text(windows_apply_script(Path(install_dir), staging, work / "versione-precedente",
                                           work / "aggiornamento.log", manifest["version"]),
                      encoding="utf-8-sig")
    return script


def launch_windows(script: Path) -> None:
    """Fa eseguire lo script da un'attività pianificata come SYSTEM: deve
    sopravvivere all'arresto del servizio che lo lancia."""
    command = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{script}"'
    subprocess.run(["schtasks.exe", "/Create", "/TN", WINDOWS_TASK, "/TR", command, "/SC", "ONCE",
                    "/ST", "23:59", "/RU", "SYSTEM", "/RL", "HIGHEST", "/F"],
                   check=True, capture_output=True, timeout=60)
    subprocess.run(["schtasks.exe", "/Run", "/TN", WINDOWS_TASK], check=True, capture_output=True, timeout=60)


# --- Ciclo -------------------------------------------------------------------------------

def _interruptible_sleep(seconds: float, should_stop) -> bool:
    """Dorme fino a 'seconds'; ritorna True se nel frattempo l'agent si sta fermando."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if should_stop():
            return True
        time.sleep(min(1.0, max(0.0, end - time.monotonic())))
    return should_stop()


def _windows_discarded(version: str) -> bool:
    """True se questa versione era già stata provata e tolta perché il servizio non partiva."""
    f = windows_work_dir() / "versione-scartata.txt"
    try:
        return f.read_text(encoding="utf-8-sig").strip() == version
    except OSError:
        return False


def running_in_container() -> bool:
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def update_loop(platform: str, tenant: str, upload_url: str | None, token: str | None,
                install_dir: Path, should_stop, request_restart, log=print) -> None:
    """Thread dell'aggiornamento automatico. 'request_restart' (solo Linux) deve
    chiudere l'agent in modo pulito: ci pensa systemd a riavviarlo."""
    base_url = update_url_from_upload_url(upload_url)
    if not base_url or not token:
        log("Aggiornamento automatico non attivo: manca l'indirizzo del portale o il token in agent.ini")
        return
    if platform == "linux" and running_in_container():
        log("Aggiornamento automatico non attivo nel container: si aggiorna l'immagine")
        return
    if _interruptible_sleep(random.uniform(*FIRST_CHECK_DELAY_S), should_stop):
        return
    while not should_stop():
        try:
            manifest = check_for_update(base_url, tenant, token, platform)
            if manifest and platform == "windows" and _windows_discarded(manifest["version"]):
                log(f"La versione {manifest['version']} non si era avviata ed era stata tolta: non la riprovo")
                manifest = None
            if manifest:
                log(f"Aggiornamento dell'agent: {AGENT_VERSION} -> {manifest['version']}, scarico il pacchetto")
                data = download_package(base_url, tenant, token, manifest)
                if platform == "linux":
                    apply_linux(data, install_dir, manifest)
                    log(f"Versione {manifest['version']} installata, riavvio l'agent")
                    request_restart()
                else:
                    launch_windows(prepare_windows(data, install_dir, manifest))
                    log(f"Versione {manifest['version']} pronta, il servizio verrà riavviato")
                return
        except Exception as e:  # mai far cadere l'agent per un controllo non riuscito
            log(f"Controllo aggiornamenti non riuscito: {e}")
        jitter = random.uniform(-0.5, 0.5) * 3600
        if _interruptible_sleep(CHECK_INTERVAL_HOURS * 3600 + jitter, should_stop):
            return
