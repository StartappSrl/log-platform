# Checklist di verifica prima della produzione

Queste prove richiedono il tuo ambiente reale: da qui non si possono fare.
Spunta ogni riga solo dopo averla vista funzionare con i tuoi occhi.

## A. Pianificazioni (cron) — fare PER PRIMA

Le righe di cron date in precedenza per report notturno e controllo offline usavano
`podman exec` da root, che con container rootless **non funziona** (root non vede i
container degli utenti di servizio). Se le avevi già inserite, **non hanno mai girato**.

1. `crontab -l` → confronta con la sezione "Operazioni pianificate" di `ARCHITETTURA.md`;
   correggi/aggiungi le righe mancanti con `crontab -e`.
2. Lancia **a mano, una alla volta**, ciascun comando preso dal cron e verifica l'esito:
   - [ ] report notturno → arriva l'email
   - [ ] controllo offline → nessun errore nel log `/var/log/logplatform-offline-check.log`
   - [ ] `check_disk_space.sh` → stampa la percentuale; prova con `DISK_THRESHOLD_PERCENT=1`
         per forzare l'avviso: deve arrivare l'email
   - [ ] `backup.sh` → completa senza `ATTENZIONE`
   - [ ] `verifica_backup.sh` → `ESITO: backup integro`
3. Il giorno dopo: controlla che i file `/var/log/logplatform-*.log` siano stati
   aggiornati dal cron (data di modifica di stamattina).

## B. Backup: verifica oggi, ripristino su macchina di prova

**Non eseguire MAI `restore.sh` sul server di produzione**: sovrascrive MariaDB,
MongoDB, CA e archivi con il contenuto del backup.

1. Sul server di produzione (sicuro): `scripts/verifica_backup.sh` → deve dire "backup integro".
2. Prova di ripristino vera, su una VM/nodo di test con i moduli NS8 installati ma vuoti:
   - [ ] copia lì una cartella di backup e lancia `restore.sh <cartella>`
   - [ ] il login nel pannello funziona con le credenziali di produzione
   - [ ] i clienti e gli stream configurati ci sono
   - [ ] un pacchetto agent scaricato dal ripristinato ha **la stessa CA** (stesso `ca.pem`) di produzione
   - [ ] gli archivi caricati sono visibili nella scheda Archivi
   - Annota cosa ha richiesto interventi manuali: va scritto in `RIPRISTINO.md`.
3. Ricorda: il backup **non include gli indici OpenSearch** (i log veri e propri).

## C. Blocco USB e installer su un PC Windows di PROVA (non di un cliente)

1. [ ] Scarica un pacchetto fresco, estrai, doppio clic su `installer_gui.exe`: UAC compare da solo
2. [ ] Installazione **senza** spuntare il blocco USB: servizio "In esecuzione" e **Avvio: Automatico**
3. [ ] Scheda "Ripristino" delle proprietà del servizio: azioni = "Riavvia il servizio" per i primi 3 errori
4. [ ] Termina il processo `logplatform-agent.exe` da Gestione attività: dopo ~60 secondi
       il servizio riparte da solo
5. [ ] I log del PC compaiono nel pannello (Ricerca) entro pochi minuti
6. [ ] Rilancia `installer_gui.exe` con il servizio già presente: la finestra dice
       "Rilevata un'installazione precedente", e la reinstallazione riesce senza errore 1073
7. Ora con la casella **"Blocca storage USB"** spuntata:
   - [ ] inserisci una chiavetta **dopo** l'installazione: NON compare come disco
   - [ ] tastiera, mouse e stampante USB funzionano normalmente
   - [ ] togli la spunta e reinstalla: la chiavetta, riconnessa, torna a funzionare
8. [ ] Lascia il servizio acceso 24-48 ore. Se si ferma, **non reinstallare subito**:
       copia `agent-stderr.log` e `agent-stdout.log` e leggi il Visualizzatore eventi
       (Sistema e Applicazione) — è l'unico modo di scoprire la causa.

## D. Relay syslog con un dispositivo vero

1. [ ] Su una macchina Linux/Windows di prova abilita `local_syslog_udp_port` nell'`agent.ini`
2. [ ] Configura un firewall/NAS/switch reale per inviare syslog all'IP di quella macchina
3. [ ] In Ricerca compaiono i log col **nome del dispositivo** (non quello del relay)
4. [ ] Verifica che la gravità sia sensata (errori come errori)

## E. Credenziali — prima di aprire ai clienti

Nel corso dello sviluppo, password e token sono comparsi in chiaro in chat, log e
terminale. Ruota almeno:
- [ ] password admin di Graylog (`GRAYLOG_API_PASSWORD` in `.config/state/environment` di `auth`)
- [ ] password dell'utente MariaDB `authuser` (e root)
- [ ] token GitHub usato per `buildah login` / push
- [ ] `GRAYLOG_WEBHOOK_SECRET` (compare nei documenti di consegna)
Procedura: `ROTAZIONE-CREDENZIALI.md`. Dopo ogni rotazione riavvia i moduli e ricontrolla il login.

## F. Ultimo giro prima di dire "in produzione"

- [ ] Tutte le Action GitHub verdi (build immagini, build Windows, test)
- [ ] `crontab -l` coerente con `ARCHITETTURA.md`
- [ ] Un cliente di prova creato da zero → pacchetto → installazione → log visibili → report ricevuto
- [ ] Eliminazione di quel cliente di prova: nessun residuo (stream, archivi, certificati revocati)
