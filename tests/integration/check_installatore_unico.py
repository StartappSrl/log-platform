"""
Controllo: l'installatore unico per Windows costruito dal portale.

Il portale prende l'installatore generico della Release e ci attacca in coda la
configurazione del cliente; l'installatore (agent/installer_logic.py) la deve
rileggere uguale. Qui si usano il codice vero di entrambe le parti, con CA,
certificati e GitHub simulati.
"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import REPO  # noqa: E402

os.environ["ARCHIVE_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(REPO, "auth-service"))
sys.path.insert(0, os.path.join(REPO, "agent"))

import installer_logic  # noqa: E402
from app import agent_packager  # noqa: E402

falliti = []


def passo(nome, cond, dett=""):
    print(("OK    " if cond else "ERRATO") + " " + nome + (f" | {dett}" if not cond else ""))
    if not cond:
        falliti.append(nome)


agent_packager.cert_ledger.issue_and_record_certificate = lambda tenant, is_server=False: (
    f"CERT DI {tenant}", f"CHIAVE DI {tenant}")
agent_packager.ca_manager.get_ca_cert_pem = lambda: "CA INTERNA"

setup = b"MZ" + os.urandom(50000)  # finto installatore generico della Release
exe = agent_packager.build_agent_installer_windows("acme", setup_exe=setup)

passo("l'installatore del cliente comincia con l'exe generico, intatto", exe.startswith(setup))
letti = installer_logic.read_tenant_payload(exe)
passo("l'installatore legge la configurazione del cliente",
      letti is not None and set(letti) == {"agent.ini", "ca.pem", "acme.pem", "acme-key.pem"}, letti and sorted(letti))
ini = letti["agent.ini"].decode()
passo("agent.ini del cliente giusto, certificati relativi alla cartella dell'agent",
      "tenant = acme" in ini and "client_key = acme-key.pem" in ini and "ca_cert = ca.pem" in ini)
passo("certificato e chiave del cliente", letti["acme.pem"] == b"CERT DI acme" and letti["acme-key.pem"] == b"CHIAVE DI acme")
passo("stesso marcatore nelle due parti", agent_packager.PAYLOAD_MAGIC == installer_logic.PAYLOAD_MAGIC)

try:
    agent_packager.build_agent_installer_windows("acme", setup_exe=exe)
    passo("un installatore già personalizzato non viene personalizzato di nuovo", False)
except ValueError:
    passo("un installatore già personalizzato non viene personalizzato di nuovo", True)

print()
if falliti:
    print(f"{len(falliti)} controlli falliti")
    sys.exit(1)
print("installatore unico: tutti i controlli superati")
