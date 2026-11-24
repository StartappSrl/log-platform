"""
Funzioni pure (senza GUI, senza chiamate a sc.exe) usate dall'installer
grafico - separate cosi' da poterle testare senza un vero Windows.
"""
import io
import os
import re
import struct
import zipfile

# --- Installatore unico (.exe) ------------------------------------------------------------
# Il workflow di rilascio costruisce un installatore generico (logplatform-agent-setup.exe)
# che contiene già l'agent. Quando un cliente lo scarica dal pannello, il portale ci attacca
# in coda la configurazione e i certificati DI QUEL CLIENTE:
#     <exe> <zip con agent.ini e certificati> <lunghezza zip: 8 byte> <PAYLOAD_MAGIC>
# Un solo file da scaricare e lanciare, nessuno zip da estrarre a mano.
PAYLOAD_MAGIC = b"LPAGCFG1"
_TRAILER = struct.Struct("<Q")
_ALLOWED_CONFIG = re.compile(r"^(agent\.ini|[A-Za-z0-9_.-]+\.pem)$")
MAX_CONFIG_BYTES = 1024 * 1024
INSTALL_FOLDER_NAME = "LogPlatform Agent"


def build_tenant_payload(files: dict) -> bytes:
    """Zip con la configurazione del cliente + lunghezza + marcatore, da attaccare all'exe."""
    for name in files:
        if not _ALLOWED_CONFIG.match(name):
            raise ValueError(f"nome non ammesso nella configurazione: {name!r}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data if isinstance(data, bytes) else data.encode("utf-8"))
    blob = buf.getvalue()
    return blob + _TRAILER.pack(len(blob)) + PAYLOAD_MAGIC


def read_tenant_payload(tail: bytes) -> dict | None:
    """Dalla parte finale dell'eseguibile: {nome file: contenuto} oppure None se assente.
    Solo agent.ini e file .pem, senza percorsi."""
    if len(tail) < _TRAILER.size + len(PAYLOAD_MAGIC) or not tail.endswith(PAYLOAD_MAGIC):
        return None
    end = len(tail) - len(PAYLOAD_MAGIC)
    (size,) = _TRAILER.unpack(tail[end - _TRAILER.size:end])
    start = end - _TRAILER.size - size
    if size == 0 or size > MAX_CONFIG_BYTES or start < 0:
        raise ValueError("configurazione in coda all'installatore non valida")
    files = {}
    with zipfile.ZipFile(io.BytesIO(tail[start:end - _TRAILER.size])) as z:
        for entry in z.infolist():
            if entry.is_dir() or not _ALLOWED_CONFIG.match(entry.filename):
                raise ValueError(f"file non ammesso nella configurazione: {entry.filename!r}")
            files[entry.filename] = z.read(entry)
    if "agent.ini" not in files:
        raise ValueError("agent.ini mancante nella configurazione")
    return files


def read_tenant_payload_from_file(path: str) -> dict | None:
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - MAX_CONFIG_BYTES - _TRAILER.size - len(PAYLOAD_MAGIC)))
        return read_tenant_payload(f.read())


def default_install_dir(environ=None) -> str:
    environ = os.environ if environ is None else environ
    base = environ.get("ProgramW6432") or environ.get("ProgramFiles") or r"C:\Program Files"
    return base.rstrip("\\") + "\\" + INSTALL_FOLDER_NAME


def exe_dir_from_image_path(image_path: str | None) -> str | None:
    """Cartella dell'eseguibile dal valore ImagePath del servizio ('"C:\\x\\agent.exe" args')."""
    if not image_path:
        return None
    m = re.match(r'^\s*"?(.*?\.exe)', image_path, re.IGNORECASE)
    if not m or "\\" not in m.group(1):
        return None
    return m.group(1).rsplit("\\", 1)[0]


def set_usb_block_in_ini(ini_path: str, enabled: bool) -> None:
    """Trova la riga 'block_usb_storage' (commentata o no) in agent.ini
    e la imposta secondo 'enabled' - se la riga non esiste proprio (ini
    molto vecchio, da prima che esistesse questa opzione), la aggiunge
    in fondo al file."""
    with open(ini_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    found = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped == "block_usb_storage = true" or stripped == "# block_usb_storage = true":
            new_lines.append("block_usb_storage = true\n" if enabled else "# block_usb_storage = true\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append("block_usb_storage = true\n" if enabled else "# block_usb_storage = true\n")

    with open(ini_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def detect_existing_installation(returncode: int) -> bool:
    """Interpreta il CODICE DI USCITA di 'sc query <nome servizio>' -
    0 significa che il servizio esiste, qualunque altro valore (tipico:
    1060, 'il servizio non esiste') significa che non c'e'.

    NON usiamo piu' il testo della risposta (cercare 'SERVICE_NAME' o
    'STATE') perche' su Windows in lingue diverse dall'inglese quel
    testo viene tradotto (es. in italiano) - il codice di uscita invece
    resta identico in qualunque lingua. Bug vero, trovato dal vivo su
    un PC con Windows in italiano: il controllo falliva sempre,
    facendo credere che il servizio non esistesse anche quando c'era
    gia', causando poi l'errore 1073 (servizio gia' esistente) al
    tentativo di crearlo di nuovo."""
    return returncode == 0


def build_sc_create_args(service_name: str, exe_path: str) -> list:
    """Costruisce gli argomenti per 'sc create' - NOTA IMPORTANTE: sc.exe
    richiede uno spazio esatto dopo ogni '=' (es. 'start= auto', non
    'start=auto') - un errore comune e facile da sbagliare a mano."""
    return [
        "create", service_name,
        "binPath=", f'"{exe_path}"',
        "start=", "auto",
        "DisplayName=", "Log Platform Agent",
    ]


def build_sc_failure_args(service_name: str) -> list:
    return [
        "failure", service_name,
        "reset=", "86400",
        "actions=", "restart/60000/restart/60000/restart/60000",
    ]


# SID fissi: uguali in ogni lingua di Windows, a differenza dei nomi
# ("Administrators" / "Amministratori").
SID_SYSTEM = "*S-1-5-18"
SID_ADMINISTRATORS = "*S-1-5-32-544"


def build_icacls_lockdown_commands(folder: str) -> list:
    r"""Comandi icacls che rendono la cartella dell'agent accessibile SOLO ad
    Amministratori e SYSTEM.

    Perché: il servizio gira come SYSTEM. In una cartella creata sotto C:\
    (es. C:\agent-startapp-windows) Windows dà a tutti gli utenti il permesso
    di modificare ("Authenticated Users: M"): chiunque potrebbe sostituire
    logplatform-agent.exe e far girare il proprio programma come SYSTEM, e
    leggere la chiave privata del certificato dell'agent.

    1. proprietario = Amministratori (chi ha estratto i file non deve poter
       rimettere i permessi di prima);
    2. via i permessi ereditati: solo SYSTEM e Amministratori, controllo completo;
    3. file e sottocartelle tornano a ereditare solo da questa cartella.
    """
    return [
        ["icacls", folder, "/setowner", SID_ADMINISTRATORS, "/T", "/C", "/Q"],
        ["icacls", folder, "/inheritance:r", "/grant:r",
         f"{SID_SYSTEM}:(OI)(CI)F", f"{SID_ADMINISTRATORS}:(OI)(CI)F", "/Q"],
        ["icacls", folder + "\\*", "/reset", "/T", "/C", "/Q"],
    ]
