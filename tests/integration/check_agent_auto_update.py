"""
Controllo: aggiornamento automatico dell'agent, dal rilascio firmato all'installazione.

- la verifica Ed25519 in Python puro dell'agent concorda con la libreria 'cryptography';
- lo script di rilascio crea pacchetto Linux, manifesto e firma;
- il portale risponde solo ai clienti con l'aggiornamento acceso, scarica il pacchetto
  in sottofondo e poi lo serve;
- l'agent accetta solo manifesti firmati con la chiave giusta, per la sua piattaforma e
  più nuovi della versione installata, e solo pacchetti che corrispondono al manifesto;
- su Linux sostituisce i file .py lasciando stare agent.ini e certificati; un pacchetto con
  percorsi pericolosi viene rifiutato senza toccare niente;
- su Windows prepara la cartella e lo script di installazione (con ripristino).
"""
import base64, hashlib, io, json, os, subprocess, sys, tarfile, tempfile, time, zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import REPO  # noqa: E402

lavoro = Path(tempfile.mkdtemp())
os.environ["ARCHIVE_DIR"] = str(lavoro / "archivi")
os.environ["AGENT_UPDATE_CACHE_DIR"] = str(lavoro / "cache")
sys.path.insert(0, os.path.join(REPO, "agent"))
sys.path.insert(0, os.path.join(REPO, "auth-service"))
sys.path.insert(0, os.path.join(REPO, "scripts"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from flask import Flask  # noqa: E402

import auto_update  # noqa: E402
import ed25519_verify  # noqa: E402
import rilascio_agent  # noqa: E402
from app import agent_update_routes, archive_storage, github_release  # noqa: E402

falliti = []


def passo(nome, cond, dett=""):
    print(("OK    " if cond else "ERRATO") + " " + nome + (f" | {dett}" if not cond else ""))
    if not cond:
        falliti.append(nome)


def raw_pub(k):
    return k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


# 1. Verifica Ed25519 dell'agent contro la libreria di riferimento
esiti = []
for i in range(20):
    k = Ed25519PrivateKey.generate()
    msg = os.urandom(i * 13)
    firma = k.sign(msg)
    alterata = bytearray(firma); alterata[i % 64] ^= 0x40
    esiti.append(ed25519_verify.verify(raw_pub(k), msg, firma)
                 and not ed25519_verify.verify(raw_pub(k), msg, bytes(alterata))
                 and not ed25519_verify.verify(raw_pub(k), msg + b"!", firma)
                 and not ed25519_verify.verify(raw_pub(Ed25519PrivateKey.generate()), msg, firma))
passo("Ed25519 in Python puro: accetta le firme giuste e rifiuta quelle alterate", all(esiti))
passo("chiave pubblica di rilascio nell'agent: 32 byte", len(auto_update.PUBLIC_KEY) == 32)

# 2. Rilascio: pacchetto Linux + manifesto + firma (con una chiave di prova)
chiave = Ed25519PrivateKey.generate()
seme = chiave.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                            serialization.NoEncryption())
os.environ["AGENT_SIGNING_KEY"] = base64.b64encode(seme).decode()
pacchetto = lavoro / "logplatform-agent-linux.tar.gz"
rilascio_agent.build_linux("9.9.9", pacchetto)
rilascio_agent.sign(pacchetto, "linux", "v9.9.9")
manifesto = Path(f"{pacchetto}.manifest.json").read_bytes()
firma = Path(f"{pacchetto}.sig").read_bytes()
m = json.loads(manifesto)
passo("manifesto: piattaforma, versione, dimensione e SHA-256",
      m["platform"] == "linux" and m["version"] == "9.9.9" and m["size"] == pacchetto.stat().st_size
      and m["sha256"] == hashlib.sha256(pacchetto.read_bytes()).hexdigest(), m)
passo("firma del manifesto valida", ed25519_verify.verify(raw_pub(chiave), manifesto, firma))
with tarfile.open(pacchetto) as t:
    nomi = set(t.getnames())
passo("pacchetto Linux: tutti i file dell'agent, niente di Windows",
      auto_update.LINUX_REQUIRED <= nomi and "agent_windows.py" not in nomi, sorted(nomi))

# 3. Portale (blueprint vero, GitHub simulato)
release = {"tag_name": "v9.9.9", "assets": [
    {"name": "logplatform-agent-linux.tar.gz", "url": "pkg"},
    {"name": "logplatform-agent-linux.tar.gz.manifest.json", "url": "man"},
    {"name": "logplatform-agent-linux.tar.gz.sig", "url": "sig"},
]}
contenuti = {"pkg": pacchetto.read_bytes(), "man": manifesto, "sig": firma}
github_release.latest_release = lambda max_age=0: release
github_release.download_asset = lambda asset, timeout=60: contenuti[asset["url"]]

web_app = Flask(__name__)
web_app.register_blueprint(agent_update_routes.agent_update_bp)
web = web_app.test_client()
token = archive_storage.get_or_create_upload_token("cliente-a")
H = {"X-Tenant": "cliente-a", "X-Upload-Token": token}
URL = "/_authgate/agent-update?platform=linux&current=0.17.12"

passo("token sbagliato -> 403",
      web.get(URL, headers={**H, "X-Upload-Token": "no"}).status_code == 403)
passo("aggiornamento spento per il cliente -> 204", web.get(URL, headers=H).status_code == 204)
archive_storage.set_agent_auto_update("cliente-a", True)
passo("acceso: primo controllo -> 204 mentre scarica il pacchetto in sottofondo",
      web.get(URL, headers=H).status_code == 204)
for _ in range(50):
    if (lavoro / "cache" / "9.9.9" / "linux" / "package").exists():
        break
    time.sleep(0.1)
r = web.get(URL, headers=H)
passo("pacchetto pronto -> 200 con manifesto e firma", r.status_code == 200
      and base64.b64decode(r.get_json()["manifest"]) == manifesto, r.status_code)
passo("download del pacchetto", web.get("/_authgate/agent-update/download?platform=linux&version=9.9.9",
                                        headers=H).data == pacchetto.read_bytes())
passo("piattaforma non valida -> 400",
      web.get("/_authgate/agent-update?platform=mac", headers=H).status_code == 400)


class Risposta:
    def __init__(self, r):
        self.status, self._d = r.status_code, r.data

    def read(self, n=-1):
        return self._d if n < 0 else self._d[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_portale(req, timeout=None):
    """Fa arrivare le richieste urllib dell'agent al portale di prova."""
    url = req.full_url.replace("https://portale.test", "")
    return Risposta(web.get(url, headers=dict(req.header_items())))


BASE = "https://portale.test/_authgate/agent-update"
pub = raw_pub(chiave)

# 4. Agent: controllo e download attraverso il portale
mf = auto_update.check_for_update(BASE, "cliente-a", token, "linux", "0.17.12", opener_portale, pub)
passo("agent: versione nuova trovata e firma verificata", mf and mf["version"] == "9.9.9", mf)
dati = auto_update.download_package(BASE, "cliente-a", token, mf, opener_portale)
passo("agent: pacchetto scaricato e corrispondente al manifesto", dati == pacchetto.read_bytes())
passo("agent: stessa versione o più vecchia -> niente aggiornamento",
      auto_update.check_for_update(BASE, "cliente-a", token, "linux", "9.9.9", opener_portale, pub) is None)
try:
    auto_update.check_for_update(BASE, "cliente-a", token, "linux", "0.1.0", opener_portale,
                                 raw_pub(Ed25519PrivateKey.generate()))
    passo("agent: firma di un'altra chiave rifiutata", False)
except auto_update.UpdateError:
    passo("agent: firma di un'altra chiave rifiutata", True)
try:
    auto_update.download_package(BASE, "cliente-a", token, {**mf, "sha256": "0" * 64}, opener_portale)
    passo("agent: pacchetto diverso dal manifesto rifiutato", False)
except auto_update.UpdateError:
    passo("agent: pacchetto diverso dal manifesto rifiutato", True)
man_win = json.dumps({**mf, "platform": "windows"}).encode()
corpo_win = json.dumps({"manifest": base64.b64encode(man_win).decode(),
                        "signature": base64.b64encode(chiave.sign(man_win)).decode()}).encode()


class RispostaFissa(Risposta):
    def __init__(self, corpo):
        self.status, self._d = 200, corpo


try:
    auto_update.check_for_update(BASE, "cliente-a", token, "linux", "0.1.0",
                                 lambda req, timeout=None: RispostaFissa(corpo_win), pub)
    passo("agent: manifesto firmato ma di un'altra piattaforma rifiutato", False)
except auto_update.UpdateError:
    passo("agent: manifesto firmato ma di un'altra piattaforma rifiutato", True)
passo("indirizzo del portale ricavato da quello di caricamento degli archivi",
      auto_update.update_url_from_upload_url("https://logs.x.it/_authgate/archive-upload")
      == "https://logs.x.it/_authgate/agent-update"
      and auto_update.update_url_from_upload_url("http://logs.x.it/x") is None)

# 5. Installazione Linux
install = lavoro / "opt-agent"
install.mkdir()
(install / "agent.ini").write_text("[agent]\ntenant = cliente-a\n")
(install / "cliente-a-key.pem").write_text("CHIAVE")
(install / "agent.py").write_text("# versione vecchia\n")
aggiornati = auto_update.apply_linux(dati, install, mf)
passo("Linux: file sostituiti con la versione nuova",
      'AGENT_VERSION = "9.9.9"' in (install / "agent_version.py").read_text()
      and (install / "agent.py").read_text() == (Path(REPO) / "agent" / "agent.py").read_text(), aggiornati)
passo("Linux: agent.ini e chiave non toccati",
      (install / "agent.ini").read_text().startswith("[agent]") and (install / "cliente-a-key.pem").read_text() == "CHIAVE")
passo("Linux: copia della versione precedente",
      (install / ".versione-precedente" / "agent.py").read_text() == "# versione vecchia\n")
r = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import auto_update, agent_version; "
                    "print(agent_version.AGENT_VERSION)", str(install)], capture_output=True, text=True)
passo("Linux: il codice installato si importa", r.stdout.strip() == "9.9.9", r.stderr[-300:])

cattivo = io.BytesIO()
with tarfile.open(fileobj=cattivo, mode="w:gz") as t:
    for nome in sorted(auto_update.LINUX_REQUIRED) + ["../fuori.py"]:
        info = tarfile.TarInfo(nome); info.size = 1
        t.addfile(info, io.BytesIO(b"#"))
prima = sorted(p.name for p in install.iterdir())
try:
    auto_update.apply_linux(cattivo.getvalue(), install, {"version": "9.9.10"})
    passo("Linux: pacchetto con percorsi pericolosi rifiutato", False)
except auto_update.UpdateError:
    passo("Linux: pacchetto con percorsi pericolosi rifiutato",
          sorted(p.name for p in install.iterdir()) == prima and not (lavoro / "fuori.py").exists())

rotto = io.BytesIO()
with tarfile.open(fileobj=rotto, mode="w:gz") as t:
    for nome in sorted(auto_update.LINUX_REQUIRED) + ["local_archive.py", "inventory.py"]:
        dati_file = b"def (:\n" if nome == "agent.py" else b"\n"
        info = tarfile.TarInfo(nome); info.size = len(dati_file)
        t.addfile(info, io.BytesIO(dati_file))
try:
    auto_update.apply_linux(rotto.getvalue(), install, {"version": "9.9.11"})
    passo("Linux: versione che non si importa rifiutata, installazione intatta", False)
except auto_update.UpdateError:
    passo("Linux: versione che non si importa rifiutata, installazione intatta",
          'AGENT_VERSION = "9.9.9"' in (install / "agent_version.py").read_text())

# 6. Preparazione Windows (lo script vero gira solo su Windows)
zbuf = io.BytesIO()
with zipfile.ZipFile(zbuf, "w") as z:
    z.writestr("logplatform-agent.exe", b"MZ")
    z.writestr("_internal/python312.dll", b"dll")
    z.writestr("installer_gui.exe", b"MZ")
script = auto_update.prepare_windows(zbuf.getvalue(), Path(r"C:\Program Files\LogPlatform Agent"),
                                     {"version": "9.9.9"}, lavoro / "win")
testo = script.read_text(encoding="utf-8-sig")
passo("Windows: file estratti nella cartella di lavoro",
      (lavoro / "win" / "versione-9.9.9" / "_internal" / "python312.dll").read_bytes() == b"dll")
passo("Windows: lo script esclude configurazione, certificati e stato, e prevede il ripristino",
      "/XF agent.ini *.pem agent_state.json" in testo and "rimessa la versione precedente" in testo
      and "'C:\\Program Files\\LogPlatform Agent'" in testo and "versione-scartata.txt" in testo)
zcattivo = io.BytesIO()
with zipfile.ZipFile(zcattivo, "w") as z:
    z.writestr("logplatform-agent.exe", b"MZ")
    z.writestr("../../Windows/evil.dll", b"x")
try:
    auto_update.prepare_windows(zcattivo.getvalue(), Path("C:/x"), {"version": "9.9.12"}, lavoro / "win")
    passo("Windows: zip con percorsi pericolosi rifiutato", False)
except auto_update.UpdateError:
    passo("Windows: zip con percorsi pericolosi rifiutato", True)

print()
if falliti:
    print(f"{len(falliti)} controlli falliti")
    sys.exit(1)
print("aggiornamento automatico dell'agent: tutti i controlli superati")
