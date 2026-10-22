"""
Controllo: gli avvisi immediati dei dispositivi offline (cron ogni 20 minuti) si possono SPEGNERE apposta senza che
la mail di esito del mattino segnali un falso problema, e si possono riaccendere. Verifica anche che una riga
semplicemente SPARITA o commentata a mano continui a essere segnalata (potrebbe essere un errore), e che i comandi
sed di spegnimento/riaccensione siano ripetibili senza accumulare prefissi.
"""
import os, subprocess, sys, tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _paths import SCRIPTS  # noqa: E402

sys.path.insert(0, SCRIPTS)
import esito_notturno as en  # noqa: E402

RIGA = ("*/20 * * * * cd /tmp && sudo -u logplatform-auth16 XDG_RUNTIME_DIR=/run/user/1001 podman exec logplatform-auth "
        "python3 -m app.check_offline_devices >> /var/log/logplatform-offline-check.log 2>&1")
ALTRE = "0 3 * * * echo report\n30 2 * * * echo backup\n"
RIGA_OFFLINE = "Controllo dispositivi offline"
SPEGNI = "/^[0-9*]/ s|^\\(.*check_offline_devices.*\\)$|#SPENTO \\1|"
RIACCENDI = "s|^#SPENTO \\(.*check_offline_devices.*\\)$|\\1|"
now = datetime.now()
reg = os.path.join(tempfile.mkdtemp(), "r.json")
falliti = []


def nomi(cron, rc=0):
    def run(cmd, input_text=None, **kw):
        if cmd[:2] == ["crontab", "-l"]:
            return rc, cron, ""
        return 1, "", "n/d"
    return [c["nome"] for c in en.gather(run, now, registry_path=reg, persist=False)]


def sed(testo, script):
    return subprocess.run(["sed", script], input=testo, capture_output=True, text=True).stdout


def passo(nome, cond, dett=""):
    print(("OK    " if cond else "ERRATO") + " " + nome + (f" | {dett}" if not cond else ""))
    if not cond:
        falliti.append(nome)


print("S1. cron attivo: la riga c'e' (comportamento di sempre)")
n = nomi(ALTRE + RIGA + "\n"); passo("riga presente, 7 controlli", RIGA_OFFLINE in n and len(n) == 7, n)

print("S2. spento apposta (#SPENTO): la riga non c'e'")
n = nomi(ALTRE + "#SPENTO " + RIGA + "\n"); passo("riga assente, 6 controlli", RIGA_OFFLINE not in n and len(n) == 6, n)
passo("gli altri controlli restano tutti", {"Flusso dei log", "Backup", "Spazio disco", "Report notturno ai clienti", "Moduli della piattaforma", "Nodo"} <= set(n), n)

print("S3. riga SPARITA per errore: continua a essere segnalata")
n = nomi(ALTRE); passo("riga presente (verra' segnalata)", RIGA_OFFLINE in n, n)

print("S4. commentata a mano con un semplice '#': NON conta come spenta apposta")
n = nomi(ALTRE + "#" + RIGA + "\n"); passo("riga presente", RIGA_OFFLINE in n, n)

print("S5. riaccesa ma con la vecchia riga #SPENTO ancora nel file: vince la riga attiva")
n = nomi(ALTRE + "#SPENTO " + RIGA + "\n" + RIGA + "\n"); passo("riga presente", RIGA_OFFLINE in n, n)

print("S6. cron non leggibile: nel dubbio la riga resta")
n = nomi("", rc=1); passo("riga presente", RIGA_OFFLINE in n, n)

print("S7. #SPENTO riferito a un ALTRO job: non c'entra")
n = nomi(ALTRE + "#SPENTO 0 5 * * * /usr/bin/true altro_job\n"); passo("riga presente", RIGA_OFFLINE in n, n)

print("S8. i comandi sed: spegnere, ripetere, riaccendere")
base = ALTRE + RIGA + "\n"
spento = sed(base, SPEGNI)
passo("spegnere commenta solo la riga giusta", spento.count("#SPENTO ") == 1 and "#SPENTO */20" in spento and "0 3 * * * echo report" in spento and "#SPENTO 0 3" not in spento, spento)
passo("dopo lo spegnimento la mail non ha la riga", RIGA_OFFLINE not in nomi(spento))
due = sed(spento, SPEGNI)
passo("ripetere lo spegnimento non accumula prefissi", due == spento and "#SPENTO #SPENTO" not in due, due)
acceso = sed(spento, RIACCENDI)
passo("riaccendere ripristina la riga identica", acceso == base, acceso)
passo("dopo la riaccensione la riga e' di nuovo nella mail", RIGA_OFFLINE in nomi(acceso))
passo("riaccendere un cron gia' acceso non cambia nulla", sed(base, RIACCENDI) == base)
passo("una riga di commento che cita il job non viene toccata", sed(ALTRE + "# nota su check_offline_devices\n" + RIGA + "\n", SPEGNI).count("#SPENTO ") == 1)

print("S9. la mail si costruisce e il riepilogo non cambia a causa della riga assente")
def run_ok(cmd, input_text=None, **kw):
    return (0, "#SPENTO " + RIGA + "\n", "") if cmd[:2] == ["crontab", "-l"] else (1, "", "n/d")
html = en.render_html(en.gather(run_ok, now, registry_path=reg, persist=False), now, "nodo-prova")
passo("HTML prodotto senza la riga dei dispositivi offline", "dispositivi offline" not in html and "Flusso dei log" in html)

if falliti:
    print("\nFALLITI:", falliti); sys.exit(1)
print("\nTUTTI I CONTROLLI SUGLI AVVISI SPENTI PASSATI")
