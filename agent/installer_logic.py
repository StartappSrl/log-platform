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


def detect_existing_installation(sc_query_output: str) -> bool:
    """Interpreta l'output di 'sc query <nome servizio>' - separata dalla
    chiamata vera a sc.exe per poter testare la logica di
    interpretazione con un output finto."""
    return "SERVICE_NAME" in sc_query_output or "STATE" in sc_query_output


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
