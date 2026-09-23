"""
Blocco dello storage USB (chiavette, dischi esterni) su Windows - via
registro di sistema, lo stesso meccanismo usato dalle policy di gruppo
aziendali, non un tentativo di intercettare/disconnettere il dispositivo
al volo (che lascerebbe una finestra in cui il dispositivo resta comunque
utilizzabile per un istante).

Come funziona: il driver USBSTOR (quello che permette a Windows di
riconoscere una chiavetta come disco) ha un valore 'Start' nel registro
che ne controlla il caricamento - 3 significa "carica su richiesta"
(comportamento normale), 4 significa "disabilitato". Disabilitandolo,
Windows non riesce piu' a caricare il driver quando una nuova chiavetta
viene inserita, quindi non compare come disco.

LIMITI ONESTI (non e' un blocco assoluto):
- Un dispositivo GIA' inserito e riconosciuto PRIMA di attivare il
  blocco resta utilizzabile finche' non viene rimosso e reinserito -
  non serve riavviare Windows, ma serve scollegare/ricollegare.
- Blocca SOLO lo storage (chiavette, dischi esterni) - tastiere, mouse,
  stampanti USB continuano a funzionare normalmente (usano driver
  diversi da USBSTOR).
- Un utente con diritti di amministratore locale sulla macchina PUO'
  riattivare manualmente la chiave di registro - questo agent la
  riafferma periodicamente (vedi enforce_usb_block_if_configured), ma
  in una finestra fra un controllo e l'altro un admin locale potrebbe
  riattivarla temporaneamente.
- NON TESTATO su un vero Windows (nessun ambiente disponibile per chi
  ha scritto questo modulo) - va provato su una macchina di test prima
  di usarlo su un cliente vero, esattamente come raccomanda anche
  Gigasys per la stessa funzionalita' nel suo prodotto.
"""
import winreg

_USBSTOR_KEY_PATH = r"SYSTEM\CurrentControlSet\Services\USBSTOR"
_START_VALUE_BLOCKED = 4   # disabilitato
_START_VALUE_ALLOWED = 3   # caricamento su richiesta (comportamento normale di Windows)


def set_usb_storage_blocked(blocked: bool) -> bool:
    """Imposta il blocco (o lo rimuove). Ritorna True se la scrittura e'
    riuscita, False altrimenti (es. permessi insufficienti - il
    processo deve girare con privilegi di amministratore/SYSTEM, che
    l'agent installato come servizio Windows ha di norma)."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _USBSTOR_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            value = _START_VALUE_BLOCKED if blocked else _START_VALUE_ALLOWED
            winreg.SetValueEx(key, "Start", 0, winreg.REG_DWORD, value)
        return True
    except OSError:
        return False


def is_usb_storage_blocked() -> bool:
    """Legge lo stato attuale - usato sia per sapere se applicare il
    blocco sia per verificare (enforce) che non sia stato rimosso."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _USBSTOR_KEY_PATH, 0, winreg.KEY_QUERY_VALUE) as key:
            value, _ = winreg.QueryValueEx(key, "Start")
            return value == _START_VALUE_BLOCKED
    except OSError:
        return False


def enforce_usb_block_if_configured(should_be_blocked: bool) -> str:
    """Da chiamare periodicamente (es. ad ogni ciclo di inventario):
    se la configurazione dice che il blocco deve essere attivo ma non lo
    e' piu' (es. un amministratore locale l'ha rimosso), lo riapplica.
    Ritorna una stringa descrittiva dell'esito, utile per il log."""
    currently_blocked = is_usb_storage_blocked()

    if should_be_blocked and not currently_blocked:
        ok = set_usb_storage_blocked(True)
        return "blocco USB riattivato (era stato rimosso)" if ok else "ATTENZIONE: impossibile attivare il blocco USB (permessi insufficienti?)"

    if not should_be_blocked and currently_blocked:
        ok = set_usb_storage_blocked(False)
        return "blocco USB rimosso (non piu' richiesto dalla configurazione)" if ok else "ATTENZIONE: impossibile rimuovere il blocco USB"

    return "blocco USB gia' nello stato corretto, nessuna azione necessaria"
