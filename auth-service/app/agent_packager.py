"""
Costruisce i pacchetti agent per un tenant, con certificato client firmato
dalla CA interna, pronti da scaricare dal pannello.

Due varianti:
  build_agent_package()             -> tar.gz con gli script Python
                                        (Linux, o Windows con Python installato)
  build_agent_package_windows_exe() -> zip con l'eseguibile .exe (scaricato
                                        dall'ultima Release GitHub) - stessa
                                        configurazione/certificati del
                                        tenant, cosi' resta tracciabile a
                                        quale cliente appartiene, invece di
                                        essere un binario "anonimo" scollegato
                                        da qualunque tenant.
"""
import io
import os
import tarfile
import zipfile
from pathlib import Path

from . import ca_manager
from . import github_release

AGENT_TEMPLATES_DIR = Path(os.environ.get("AGENT_TEMPLATES_DIR", "/agent_templates"))
PUBLIC_DOMAIN = os.environ.get("PUBLIC_DOMAIN", "logs.tuodominio.it")
GELF_PORT = int(os.environ.get("GELF_PORT", "12201"))


def _agent_ini_content(tenant: str) -> str:
    return f"""[agent]
tenant = {tenant}
# Lascia vuoto/commentato per rilevare automaticamente l'hostname della
# macchina su cui gira l'agent (comportamento di default). Scommenta e
# personalizza solo se vuoi un nome diverso da quello di sistema.
# hostname = nome-personalizzato
graylog_host = {PUBLIC_DOMAIN}
graylog_port = {GELF_PORT}
ca_cert = ca.pem
client_cert = {tenant}.pem
client_key = {tenant}-key.pem
log_files = /var/log/syslog
# admin_log_files = /var/log/auth.log
# event_logs = Application, System
# admin_event_logs = Security

local_archive_dir = /var/lib/logplatform-agent/archive
# local_archive_tsa_url =

inventory_interval_hours = 24
"""


def build_agent_package(tenant: str) -> bytes:
    """tar.gz con gli script Python (Linux, o Windows con Python+pywin32)."""
    client_cert_pem, client_key_pem = ca_manager.issue_certificate(tenant, is_server=False)
    ca_pem = ca_manager.get_ca_cert_pem()
    agent_ini = _agent_ini_content(tenant)

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
    """zip con l'eseguibile .exe (dall'ultima Release GitHub) + certificato
    e configurazione DI QUESTO TENANT - cosi' resta chiaro a quale cliente
    appartiene, invece di essere un binario scollegato da un tenant."""
    client_cert_pem, client_key_pem = ca_manager.issue_certificate(tenant, is_server=False)
    ca_pem = ca_manager.get_ca_cert_pem()
    agent_ini = _agent_ini_content(tenant)
    exe_bytes = github_release.get_windows_exe_bytes()  # solleva GitHubReleaseError se fallisce

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"agent-{tenant}-windows/logplatform-agent.exe", exe_bytes)
        zf.writestr(f"agent-{tenant}-windows/agent.ini", agent_ini)
        zf.writestr(f"agent-{tenant}-windows/ca.pem", ca_pem)
        zf.writestr(f"agent-{tenant}-windows/{tenant}.pem", client_cert_pem)
        zf.writestr(f"agent-{tenant}-windows/{tenant}-key.pem", client_key_pem)

    return buf.getvalue()
