"""
Funzioni pure (senza GUI, senza chiamate a sc.exe) usate dall'installer
grafico - separate cosi' da poterle testare senza un vero Windows.
"""
import os


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
