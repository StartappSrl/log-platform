"""
Costruisce il pacchetto agent (tar.gz) per un tenant, con certificato
client firmato dalla CA interna, pronto da scaricare dal pannello.

I file sorgente dell'agent (agent.py, agent_windows.py, ecc.) sono inclusi
nell'immagine Docker di auth-service sotto AGENT_TEMPLATES_DIR (vedi
Dockerfile: COPY agent_templates /agent_templates) - non generati qui, solo
copiati e personalizzati con i dati del tenant.
"""
import io
import os
import tarfile
from pathlib import Path

from . import ca_manager

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
    """Ritorna il contenuto del file tar.gz (bytes), pronto per il download."""
    client_cert_pem, client_key_pem = ca_manager.issue_certificate(tenant, is_server=False)
    ca_pem = ca_manager.get_ca_cert_pem()
    agent_ini = _agent_ini_content(tenant)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # File statici presi dal template incluso nell'immagine
        for name in ("agent.py", "agent_windows.py", "gelf_transport.py", "local_archive.py",
                     "inventory.py", "requirements-windows.txt", "logplatform-agent.service.example"):
            src = AGENT_TEMPLATES_DIR / name
            if src.exists():
                tar.add(src, arcname=f"agent-{tenant}/{name}")

        # File generati al volo, specifici per questo tenant
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
