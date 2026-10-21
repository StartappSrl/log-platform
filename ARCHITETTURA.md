# Log Platform — Documentazione d'insieme

Aggiornata allo stato di fine sviluppo (sostituisce la versione precedente).
Aggiornala quando cambi qualcosa di strutturale: un documento vecchio è
peggio di nessun documento, perché dà falsa sicurezza.

## Cos'è

Piattaforma multi-cliente (multi-tenant) di raccolta e consultazione log,
basata su Graylog, con un pannello web semplificato al posto
dell'interfaccia nativa. Pensata per un system integrator che gestisce i log
di più clienti su un'unica installazione, con i dati di ciascuno isolati.

## I moduli (NS8, ognuno un container podman rootless, utente di servizio proprio)

| Modulo | Cosa fa | Dati |
|---|---|---|
| `mariadb` | Database relazionale | Utenti, tenant, impostazioni SMTP, audit log, stato avvisi |
| `mongodb` | Configurazione interna di Graylog | Stream, index set, allarmi, notifiche |
| `opensearch` | Motore di ricerca | Gli indici dei log (NON nel backup, per spazio) |
| `graylog` | Riceve i log e li instrada per tenant | Nessuno stato proprio |
| `auth` | Pannello, login, MFA, API | Codice in `auth-service/`; volumi: CA (`logplatform-auth-ca`), archivi (`logplatform-auth-archives`) |
| `gate` | Reverse proxy + pagina di login | `login.html` |

**Importante**: i container sono rootless, quindi `podman ps` da root NON li
mostra. Si opera sempre come `sudo -u <istanza> XDG_RUNTIME_DIR=/run/user/$(id -u <istanza>) podman ...`
e **da una cartella accessibile a quell'utente** (`cd /tmp` prima), altrimenti
errore "cannot chdir".

## Come arrivano i log e come restano isolati

1. **Agent** (Python, sul PC/server del cliente) legge file di log o Windows
   Event Log e invia a Graylog in GELF su TCP+TLS con **certificato client**
   (mTLS) firmato dalla CA interna.
2. Ogni messaggio porta `_tenant`; su Graylog ogni tenant ha uno **stream** con
   regola `tenant = <nome>` e un **index set** separato in OpenSearch.
3. **Relay syslog**: l'agent può ascoltare syslog (UDP/TCP) sulla rete locale e
   inoltrare come GELF, con l'hostname reale del dispositivo e la gravità dal
   campo PRI. Il dispositivo non tocca mai Internet.
4. Gravità nei log Linux generici: euristica su parole chiave (error/warning/...)
   in `agent.py`; i log Windows hanno un livello vero.

**Trappole note di Graylog**: la ricerca deve usare `filter=streams:<ID>` (il
parametro `streams=` è ignorato in silenzio e causa fuga di dati tra tenant —
c'è un test di non-regressione); il campo host nei risultati è `source`, non `host`.

## Windows: installazione e blocco USB

- Il pacchetto scaricabile contiene `logplatform-agent.exe`, `installer_gui.exe`,
  `agent.ini`, certificati. **`installer_gui.exe`** (Tkinter, richiede UAC
  automaticamente): rileva un'installazione precedente, la ferma e rimuove, crea
  il servizio con avvio **automatico**, configura il **riavvio automatico in caso
  di arresto** (`sc failure`), lo avvia. Casella opzionale "blocca storage USB".
- Il rilevamento del servizio usa il **codice di uscita** di `sc.exe`, non il
  testo: su Windows in italiano il testo è tradotto (bug reale già visto, errore 1073).
- **Blocco USB** (`usb_block.py`): imposta `Start=4` sul driver `USBSTOR` nel
  registro, riaffermato ogni ora. Blocca solo lo storage; un dispositivo già
  inserito resta attivo finché non viene riconnesso; un admin locale può
  rimuoverlo. **Mai verificato su un Windows reale.**
- Diagnostica: se il servizio parte da SCM, `stdout/stderr` sono rediretti su
  `agent-stdout.log` / `agent-stderr.log` nella cartella dell'agent.
- **Problema aperto**: in passato il servizio su un PC si è fermato dopo qualche
  ora con errore generico (`%%536870913`), causa mai identificata. Il riavvio
  automatico ne riduce l'impatto, non è la cura. Se si ripresenta: leggere
  `agent-stderr.log` e il Visualizzatore eventi (Sistema + Applicazione).

## Notifiche degli allarmi

Le notifiche "email" **non** usano il transport email nativo di Graylog (non
configurato in `graylog.conf`): sono notifiche HTTP verso il nostro webhook
`/_authgate/dashboard/webhook/graylog-notification`, protetto da
`GRAYLOG_WEBHOOK_SECRET`, che invia con l'SMTP configurato in Impostazioni.
Serve `PUBLIC_BASE_URL` + `GRAYLOG_WEBHOOK_SECRET` nell'unit systemd di `auth`.
Il campo `headers` NON è valido nella config HTTP di Graylog (rifiutato con 400).

## Struttura del repository (`final-package/`)

```
auth-service/app/   dashboard.py (quasi tutte le rotte), models.py, graylog_client.py,
                    routes.py (login/MFA), auth_utils.py (sessioni), mfa.py, cert_ledger.py,
                    archive_storage.py, agent_packager.py, nightly_report.py, email_sender.py,
                    send_alert_email.py, send_html_stdin.py, customer_report.py, send_customer_reports.py, send_nightly_report.py, check_offline_devices.py,
                    platform_health.py, inventory_pdf.py
auth-service/static/ dashboard.html, login (frontend tutto in questi file)
auth-service/agent_templates/  copia dei file agent usata per generare i pacchetti
agent/              agent.py, agent_windows.py, inventory.py, local_archive.py, gelf_transport.py,
                    syslog_relay.py, syslog_parser.py, usb_block.py, installer_gui.py, installer_logic.py
gate/               login.html, nginx.conf.template
scripts/            backup.sh, restore.sh, verifica_backup.sh, esito_notturno.py, check_disk_space.sh, seal-admin-logs.sh
tests/              test di non-regressione (pytest, girano in CI)
tests/integration/  controlli di integrazione check_*.py (mail, backup, flusso dei log): vedi "Test automatici"
.github/workflows/  build-images.yml, build-windows-agent.yml, tests.yml
```
`models.py` e `dashboard.py` sono cresciuti per accumulo (funzioni accodate in
fondo): cercare con `grep` per nome di funzione, non scorrere il file.

## Tabelle principali (MariaDB)

`tenants` (anagrafica, `weekly_report_enabled`) · `users` (MFA, `mfa_backup_codes`, `session_version`) ·
`smtp_settings` (una riga, id=1) · `audit_log` · `offline_alert_state` · `failed_logins`.
Dopo ogni nuova colonna/tabella serve una migrazione SQL manuale (non c'è un tool di migrazioni).

## Sicurezza (stato reale)

- Login + **MFA** (TOTP) con **codici di recupero** monouso; blocco dopo 5 tentativi falliti/15 min
- **CSRF** su tutte le rotte che modificano, header di sicurezza (CSP, HSTS, X-Frame-Options)
- Cambio password / reset MFA **invalida le sessioni aperte** (`session_version` nel cookie firmato)
- **Audit log** di: crea/elimina utente, attiva/disattiva utente, reset MFA, crea/elimina
  cliente, revoca certificato, crea/elimina notifica, crea allarme, modifica SMTP,
  cambio password. Mai registrati: password, token, URL di destinazione dei webhook.
  **Non coperti**: emissione certificati (a ogni download pacchetto agent), download archivi.
- Revoca certificati: la CRL viene generata ma **Graylog non la applica** (vedi `NGINX-CRL-PROXY.md`)

## Operazioni pianificate (cron di root sul nodo) — da verificare con `crontab -l`

Da root, un `podman exec` semplice NON funziona (container rootless): serve il giro
`sudo -u` e `cd /tmp`. Righe corrette:

```
0 3 * * *    cd /tmp && sudo -u logplatform-auth16 XDG_RUNTIME_DIR=/run/user/$(id -u logplatform-auth16) podman exec logplatform-auth python3 -m app.send_nightly_report >> /var/log/logplatform-report.log 2>&1
*/20 * * * * cd /tmp && sudo -u logplatform-auth16 XDG_RUNTIME_DIR=/run/user/$(id -u logplatform-auth16) podman exec logplatform-auth python3 -m app.check_offline_devices >> /var/log/logplatform-offline-check.log 2>&1
0 * * * *    /root/logmanager/final-package/scripts/check_disk_space.sh >> /var/log/logplatform-disk-check.log 2>&1
30 2 * * *   /root/logmanager/final-package/scripts/backup.sh >> /var/log/logplatform-backup.log 2>&1
30 6 * * *   /root/logmanager/final-package/scripts/verifica_backup.sh >> /var/log/logplatform-backup-verify.log 2>&1 || { cd /tmp && sudo -u logplatform-auth16 XDG_RUNTIME_DIR=/run/user/$(id -u logplatform-auth16) podman exec logplatform-auth python3 -m app.send_alert_email "Backup NON valido su $(hostname)" "verifica_backup.sh ha segnalato problemi: vedi /var/log/logplatform-backup-verify.log"; }
45 6 * * *   /usr/bin/python3 /root/logmanager/final-package/scripts/esito_notturno.py >> /var/log/logplatform-esito.log 2>&1
# (attivare dopo il collaudo) riepilogo settimanale ai clienti, il lunedì alle 07:30
# 30 7 * * 1   cd /tmp && sudo -u logplatform-auth16 XDG_RUNTIME_DIR=/run/user/$(id -u logplatform-auth16) podman exec logplatform-auth python3 -m app.send_customer_reports >> /var/log/logplatform-customer-reports.log 2>&1
```
(la riga di `verifica_backup.sh` manda un'email SOLO se la verifica del backup fallisce: le graffe
sono necessarie, senza `A || B && C` si leggerebbe `(A || B) && C` e l'email
partirebbe sempre)

## Mail di esito dei controlli notturni

Ogni mattina alle 06:45 `scripts/esito_notturno.py` (gira sul nodo) manda una mail HTML con
l'esito di: flusso dei log (messaggi e dispositivi attivi nelle ultime 24 ore, rosso se zero),
backup (verifica di integrità, età, dimensione), disco, ultimo report notturno,
ultimo controllo dispositivi offline, moduli in esecuzione, ultimo riavvio del nodo. Parte
**anche quando va tutto bene**: se una mattina non arriva, il controllo (o il nodo) non gira.
L'invio passa dal container `auth` (`python3 -m app.send_html_stdin`, SMTP di Impostazioni,
destinatari del report notturno). `--dry-run` genera l'HTML in /tmp senza inviare.
**Registro dei dispositivi noti** (`/var/lib/logplatform-esito/dispositivi.json`, solo sul nodo): la mail
elenca i dispositivi già visti che ora non inviano da più di 24 ore (avviso fino a 3 giorni, poi problema).
`esito_notturno.py --list` li mostra; `--forget NOME [--cliente C]` toglie un dispositivo dismesso.
Si popola con i dispositivi visti dai controlli *veri* (il `--dry-run` non lo modifica); se lo perdi si
ricostruisce in un giorno. È una seconda linea di difesa: la prima è l'avviso dei 20 minuti.
Limite: se il nodo intero è spento non parte nulla, serve un controllo esterno (uptime monitor).

## Riepilogo settimanale ai clienti

Mail HTML destinata al CLIENTE (non all'amministratore): stato dei suoi dispositivi, attività
della settimana per giorno, aggiornamenti in sospeso. Codice: `customer_report.py` (raccolta e
impaginazione) e `send_customer_reports.py` (comando, `python3 -m app.send_customer_reports`).
- **Spento di default**: si attiva cliente per cliente (`tenants.weekly_report_enabled`) con la casella nella
  colonna "Riepilogo clienti" della scheda Clienti (chiede conferma; disabilitata se manca l'email del referente).
- **Isolamento**: ogni interrogazione a Graylog porta `filter=streams:<stream del cliente>`
  (c'è un test che lo verifica); la mail non contiene mai dati di altri clienti né dettagli interni.
- **Niente falsi allarmi nostri**: se Graylog non risponde per un cliente, quel cliente non riceve nulla.
- Ogni invio vero lascia una riga nel log di controllo (utente `sistema`) e una copia ai destinatari
  del report notturno.
- Collaudo: `--dry-run` (scrive l'HTML in /tmp), `--to indirizzo --include-disabled` (tutto solo a
  quell'indirizzo con oggetto `[PROVA]`, mai ai clienti). `--include-disabled` senza `--to`/`--dry-run` è rifiutato.
- Limite: l'elenco dispositivi si ricava dai messaggi più recenti (fino a 5000); con volumi molto alti
  un dispositivo muto da giorni potrebbe non comparire (la mail lo segnala).

## Test automatici (girano in CI a ogni push su main e su ogni pull request)

- `tests/test_*.py`: test unitari con pytest (gravità dei log, client Graylog, inventario).
- `tests/integration/check_*.py`: controlli di integrazione, ciascuno uno script autonomo (il nome `check_`
  è voluto: pytest non deve importarli). Usano un Graylog e un server SMTP simulati e una app finta costruita
  al volo con i file VERI del repository:
  `check_customer_report` (riepilogo ai clienti: isolamento fra clienti, nessuna mail a clienti non attivati,
  niente mail se Graylog fallisce), `check_nightly_report` (report notturno e suo invio), `check_esito_*`
  (mail di esito: raccoglitori, flusso dei log, registro dei dispositivi, invio), `check_verifica_backup`
  (compreso il falso allarme SIGPIPE con volumi grandi).
- Si lanciano a mano dalla radice del repository: `python3 tests/integration/check_esito_registry.py`
  (servono flask, flask_sqlalchemy, requests, matplotlib). Falliscono con uscita diversa da zero.
- Regola: se aggiungi o cambi una funzione che invia mail, tocca il backup o isola i dati dei clienti, aggiungi
  o aggiorna il controllo corrispondente. Verificato con difetti iniettati apposta: il filtro dello stream tolto,
  `--include-disabled` senza `--to`, il falso allarme SIGPIPE, l'HTML non neutralizzato, il registro che non
  segnala più i dispositivi muti: ogni difetto fa fallire il controllo giusto.
- Non coperto dai test: la resa delle mail nei client di posta, le rotte del pannello (`dashboard.py`) e il
  giro vero con podman/NS8 sul nodo.

## Procedura di aggiornamento

commit + tag → CI verde → versione in `build-images.sh` del modulo → `./build-images.sh` →
`buildah push` → `update-module` → controlla `AUTH_SERVICE_IMAGE` in
`.config/state/environment` e che il file systemd live contenga le variabili attese
(`update-module` non sempre lo rigenera) → riavvia → **leggi i log**.
Le patch ai file usano ancoraggi di testo esatto (confronto su testo grezzo): attenzione
a backslash e indentazione. Un cambiamento a `gate/login.html` richiede di ricostruire anche `gate`.

## Cosa manca ancora (stato onesto)

- **Credenziali mai ruotate** dalla prima generazione (Graylog admin, MariaDB, token GitHub)
- **Ripristino da backup mai provato** su una macchina pulita
- **Blocco USB e installer grafico** mai verificati su un Windows reale oltre all'esito del rilevamento servizio
- **Relay syslog** mai provato con un dispositivo di rete reale
- Servizio Windows che si ferma: causa ignota
- Proxy CRL (revoca reale), marca temporale qualificata (Namirial), ridondanza multi-nodo
- Nessun ambiente di staging separato
