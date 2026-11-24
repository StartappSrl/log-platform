"""
Controllo: l'agent non smette di inviare se Graylog è irraggiungibile per un po' (riavvio, aggiornamento).
Era il guasto visto in produzione il 24/11/2026: durante un riavvio di Graylog la riconnessione falliva con
"Connection refused", l'eccezione chiudeva il thread e i log non partivano più fino al riavvio del servizio.
"""
import os, sys, threading, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import REPO  # noqa: E402

sys.path.insert(0, os.path.join(REPO, "agent"))
import gelf_transport  # noqa: E402
import syslog_relay  # noqa: E402

falliti = []


def passo(nome, cond, dett=""):
    print(("OK    " if cond else "ERRATO") + " " + nome + (f" | {dett}" if not cond else ""))
    if not cond:
        falliti.append(nome)


class SocketFinto:
    def __init__(self, rotto=False):
        self.rotto, self.inviati = rotto, []

    def sendall(self, dati):
        if self.rotto:
            raise BrokenPipeError(32, "Broken pipe")
        self.inviati.append(dati)


tentativi = {"n": 0}
buono = SocketFinto()


def connect_finto(*_args):
    tentativi["n"] += 1
    if tentativi["n"] <= 3:  # Graylog giù per i primi tre tentativi
        raise ConnectionRefusedError(111, "Connection refused")
    return buono


gelf_transport.connect = connect_finto
mai = lambda: False  # noqa: E731

# 1. Connessione caduta e Graylog giù per qualche tentativo: il messaggio parte comunque
holder = {"sock": SocketFinto(rotto=True)}
inizio = time.time()
ok = gelf_transport.send_with_reconnect(holder, b"msg1", (), mai, first_delay=0.5, max_delay=1)
passo("invio riuscito dopo 3 connessioni rifiutate", ok and buono.inviati == [b"msg1"], (ok, buono.inviati))
passo("nessuna eccezione verso il chiamante, attese crescenti ma limitate", time.time() - inizio < 6,
      round(time.time() - inizio, 1))
passo("la connessione nuova resta condivisa", holder["sock"] is buono)

# 2. Avvio con Graylog non raggiungibile (sock None): si collega al primo invio
tentativi["n"] = 0
holder = {"sock": None}
ok = gelf_transport.send_with_reconnect(holder, b"msg2", (), mai, first_delay=0.5, max_delay=1)
passo("avvio senza connessione: si collega da solo", ok and buono.inviati[-1] == b"msg2")

# 3. Arresto dell'agent mentre Graylog è giù: esce subito senza bloccarsi
tentativi["n"] = -10**9
holder = {"sock": None}
stop = threading.Event()
threading.Timer(0.6, stop.set).start()
inizio = time.time()
ok = gelf_transport.send_with_reconnect(holder, b"msg3", (), stop.is_set, first_delay=0.5, max_delay=60)
passo("arresto rispettato entro pochi secondi", ok is False and time.time() - inizio < 3,
      (ok, round(time.time() - inizio, 1)))

# 4. Relay syslog: usa la stessa riconnessione (prima aspettava che un altro thread si ricollegasse)
tentativi["n"] = 2
buono.inviati.clear()
holder = {"sock": SocketFinto(rotto=True), "connect_args": ()}
syslog_relay.send_with_reconnect = lambda h, p, a, s: gelf_transport.send_with_reconnect(
    h, p, a, s, first_delay=0.5, max_delay=1)
syslog_relay._forward("t", "relay", "<13>Nov 24 08:00:00 nas1 prova: ciao", "10.0.0.5",
                      gelf_transport.build_gelf_message, holder)
passo("relay syslog: messaggio inviato dopo la riconnessione", len(buono.inviati) == 1
      and b"ciao" in buono.inviati[0], buono.inviati)

print()
if falliti:
    print(f"{len(falliti)} controlli falliti")
    sys.exit(1)
print("agent: riconnessione a Graylog, tutti i controlli superati")
