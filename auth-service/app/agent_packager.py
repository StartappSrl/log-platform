"""
Costruisce i pacchetti agent per un tenant, con certificato client firmato
dalla CA interna, pronti da scaricare dal pannello.

Due varianti:
  build_agent_package()             -> tar.gz con gli script Python
                                        (Linux, o Windows con Python installato)
  build_agent_package_windows_exe() -> zip con l'intera cartella
                                        dell'agent Windows (PyInstaller
                                        --onedir, scaricata dall'ultima
                                        Release GitHub) + certificato e
                                        configurazione DI QUESTO TENANT.
"""
import io
import os
import tarfile
import zipfile
from pathlib import Path

from . import ca_manager
from . import cert_ledger
from . import github_release
from . import archive_storage

AGENT_TEMPLATES_DIR = Path(os.environ.get("AGENT_TEMPLATES_DIR", "/agent_templates"))
PUBLIC_DOMAIN = os.environ.get("PUBLIC_DOMAIN", "logs.tuodominio.it")
GELF_PORT = int(os.environ.get("GELF_PORT", "12201"))


def _agent_ini_content_linux(tenant: str) -> str:
    upload_token = archive_storage.get_or_create_upload_token(tenant)
    return f"""[agent]
tenant = {tenant}
# hostname = nome-personalizzato
graylog_host = {PUBLIC_DOMAIN}
graylog_port = {GELF_PORT}
ca_cert = ca.pem
client_cert = {tenant}.pem
client_key = {tenant}-key.pem
log_files = /var/log/syslog
# admin_log_files = /var/log/auth.log

local_archive_dir = /var/lib/logplatform-agent/archive
# local_archive_tsa_url =
# Carica anche una copia sul portale (scheda Archivi), oltre a quella
# locale - lascia vuoto per disattivare (resta solo in locale).
local_archive_upload_url = https://{PUBLIC_DOMAIN}/_authgate/archive-upload
local_archive_upload_token = {upload_token}

inventory_interval_hours = 24
"""


def _agent_ini_content_windows(tenant: str) -> str:
    upload_token = archive_storage.get_or_create_upload_token(tenant)
    return f"""[agent]
tenant = {tenant}
# hostname = nome-personalizzato
graylog_host = {PUBLIC_DOMAIN}
graylog_port = {GELF_PORT}
ca_cert = ca.pem
client_cert = {tenant}.pem
client_key = {tenant}-key.pem

event_logs = Application, System
# admin_event_logs = Security

local_archive_dir = C:\\ProgramData\\LogPlatformAgent\\archive
# local_archive_tsa_url =
local_archive_upload_url = https://{PUBLIC_DOMAIN}/_authgate/archive-upload
local_archive_upload_token = {upload_token}

inventory_interval_hours = 24
"""


def build_agent_package(tenant: str) -> bytes:
    """tar.gz con gli script Python (Linux, o Windows con Python+pywin32)."""
    client_cert_pem, client_key_pem = cert_ledger.issue_and_record_certificate(tenant, is_server=False)
    ca_pem = ca_manager.get_ca_cert_pem()
    agent_ini = _agent_ini_content_linux(tenant)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in ("agent.py", "agent_windows.py", "gelf_transport.py", "local_archive.py",
                     "inventory.py", "requirements-windows.txt", "logplatform-agent.service.example"):
            src = AGENT_TEMPLATES_DIR / name
            if src.exists():
                tar.add(src, arcname=f"agent-{tenant}/{name}")

        for name, content in (
            ("ca.pem", ca_pem),
            (f"{tenant}.pem", client_cert_pem),
            (f"{tenant}-key.pem", client_key_pem),
            ("agent.ini", agent_ini),
        ):
            data = content.encode()
            info = tarfile.TarInfo(name=f"agent-{tenant}/{name}")
            info.size = len(data)
            info.mode = 0o600 if name.endswith("-key.pem") else 0o644
            tar.addfile(info, io.BytesIO(data))

    return buf.getvalue()


def build_agent_package_windows_exe(tenant: str) -> bytes:
    """zip con la cartella completa dell'agent Windows (dall'ultima
    Release GitHub, PyInstaller --onedir) + certificato e configurazione
    DI QUESTO TENANT."""
    client_cert_pem, client_key_pem = cert_ledger.issue_and_record_certificate(tenant, is_server=False)
    ca_pem = ca_manager.get_ca_cert_pem()
    agent_ini = _agent_ini_content_windows(tenant)
    release_zip_bytes = github_release.get_windows_release_zip_bytes()

    out_buf = io.BytesIO()
    root = f"agent-{tenant}-windows"
    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as out_zip:
        # Ricopia tutti i file della release (exe + dipendenze) dentro la
        # stessa sottocartella del pacchetto finale
        with zipfile.ZipFile(io.BytesIO(release_zip_bytes)) as release_zip:
            for entry in release_zip.infolist():
                if entry.is_dir():
                    continue
                data = release_zip.read(entry.filename)
                out_zip.writestr(f"{root}/{entry.filename}", data)

        out_zip.writestr(f"{root}/agent.ini", agent_ini)
        out_zip.writestr(f"{root}/ca.pem", ca_pem)
        out_zip.writestr(f"{root}/{tenant}.pem", client_cert_pem)
        out_zip.writestr(f"{root}/{tenant}-key.pem", client_key_pem)

    return out_buf.getvalue()
