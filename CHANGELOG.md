# Changelog

Tutte le modifiche rilevanti di questo progetto sono documentate qui.
Formato ispirato a [Keep a Changelog](https://keepachangelog.com/it/1.0.0/),
versionamento secondo [SemVer](https://semver.org/lang/it/).

Finché la versione resta `0.x.y`, l'API/configurazione può ancora cambiare
in modo non retrocompatibile tra una MINOR e l'altra: si esce da `0.x`
(passando a `1.0.0`) solo dopo una validazione reale su un ambiente di
produzione, incluso NethServer 8.

## [Non rilasciato]
### Cambiato
- `docker-compose.yml` allineato alle versioni in produzione: MongoDB 8.0.38,
  OpenSearch 2.19.6, Graylog 6.3.15 (prima 6.0 / 2.15.0 / 6.1).

### Da fare (bloccato in attesa di dati esterni)
- Marca temporale qualificata Namirial: servono endpoint RFC 3161 e credenziali
  dal contratto Namirial (vedi 0.7.0). Finché mancano si usa la TSA gratuita.

## [0.17.14] - 2026-11-24
### Aggiunto
- **Installatore unico per Windows**: dal pannello si scarica un solo file,
  `logplatform-agent-setup-<cliente>.exe`. Contiene l'agent; il portale ci
  aggiunge in coda configurazione e certificati del cliente. Installa in
  `C:\Program Files\LogPlatform Agent`, riprende lo stato di un'eventuale
  installazione precedente e propone di cancellare la vecchia cartella.
  Il vecchio pacchetto zip resta disponibile. (#35)
- La build Windows gira anche sulle PR che toccano l'agent e prova
  l'installatore vero (versione 0.0.0, nessun rilascio).

### Sicurezza
- La cartella dell'agent Windows è accessibile solo ad Amministratori e SYSTEM.
  Prima l'agent restava dove si estraeva lo zip (es. `C:\agent-startapp-windows`),
  modificabile da qualunque utente del PC: si poteva sostituire l'eseguibile del
  servizio, che gira come SYSTEM, e leggere la chiave privata dell'agent.
  Anche l'installatore del vecchio zip ora protegge la cartella. (#35)

## [0.17.13] - 2026-11-24
### Aggiunto
- **Aggiornamento automatico dell'agent**, acceso per cliente dal pannello
  (colonna "Aggiornamento agent"). Installa solo pacchetti firmati al rilascio
  con una chiave Ed25519 che il portale non possiede, più nuovi della versione
  in uso; su Windows rimette la versione precedente se il servizio non riparte.
  Ogni messaggio porta il campo `agent_version`. (#32)
- Nuovo aspetto del pannello: barra laterale con icone, riquadri riassuntivi
  in dashboard, tema scuro, menu a scomparsa su telefono. (#33)
- Pagina di login con lo stesso stile, tema condiviso col pannello. (#34)
- Configurazione Renovate per il repository (14 giorni di attesa, major e
  Python su approvazione). (#29)

### Corretto
- L'agent (Linux, Windows, relay syslog) smetteva di inviare dopo un riavvio
  di Graylog finché non si riavviava il servizio: ora si ricollega da solo,
  anche se Graylog non risponde all'avvio. (#31)
- Gli allarmi creati dal pannello non venivano mai valutati da Graylog 6.x
  (serie nel vecchio formato). (#31)
- Archivi: un registro creato da una versione precedente non aveva la colonna
  `size_bytes` e dal 16/09 ogni caricamento falliva; ora si aggiorna da solo. (#30)
- Il pacchetto Linux scaricato dal portale non conteneva `syslog_relay.py` e
  `syslog_parser.py`. (#32)

### Sicurezza
- Librerie di auth-service: bcrypt 4.3.0, PyMySQL 1.2.4, pyotp 2.12.1,
  reportlab 4.5.1, requests 2.34.2. (#5, #6, #7, #9, #10)
- Token degli archivi confrontato a tempo costante. (#32)

## [0.17.12] - 2026-11-23
### Cambiato
- ai-service: anthropic 2.12, Flask 3.1.3, requests 2.34.2, gunicorn 23;
  modello predefinito `claude-opus-5-5` con ripiego automatico. (#3)

## [0.17.0 – 0.17.11] - 2026-09-23 / 2026-11-17
### Aggiunto
- Agent Windows: blocco facoltativo dello storage USB (0.17.0) e installatore
  grafico con elevazione automatica (0.17.2).
- Spazio disco in dashboard (0.17.0) e spazio occupato per cliente (0.17.1).
- Mail HTML con l'esito dei controlli notturni e registro dei dispositivi noti
  (0.17.7, 0.17.10); riepilogo settimanale ai clienti, spento di default (0.17.8).
- Modifica dell'anagrafica cliente dal pannello (0.17.10).
- `verifica_backup.sh`, checklist pre-produzione, controlli di integrazione
  nella CI (0.17.6, 0.17.9).

### Cambiato
- Conservazione predefinita dei log da 90 a 180 giorni (0.17.4).
- Avvisi immediati sui dispositivi offline spenti, resta il riepilogo
  giornaliero (0.17.11).

### Corretto
- Installatore: rilevamento del servizio esistente indipendente dalla lingua
  di Windows (0.17.3).
- Falso negativo in `verifica_backup.sh` (0.17.8).

### Sicurezza
- Le sessioni vengono invalidate al cambio password o reset MFA (0.17.5).
- Log di controllo esteso ad allarmi, notifiche, SMTP e cambio password (0.17.6).
- Flask 3.1.3, requests 2.33.0, cryptography 50.0.2 in auth-service (0.17.11).

## [0.16.0 – 0.16.5] - 2026-09-16 / 2026-09-17
### Aggiunto
- Monitoraggio dello spazio disco, avviso di backup fallito, script di
  ripristino riscritto, documentazione d'insieme, test automatici in CI,
  ricerca e paginazione nelle tabelle (0.16.0).
- Eliminazione di utenti e notifiche (0.16.5).

### Corretto
- `backup.sh`: permessi Unix, attraversamento cartelle e SELinux (0.16.0).
- Notifiche email degli allarmi instradate tramite un webhook interno con
  l'SMTP già configurato, invece del trasporto nativo di Graylog (0.16.3, 0.16.4).
- Agent Windows come servizio: output rediretto su file per non mascherare
  gli errori reali (0.16.1).

## [0.15.0] - 2026-09-16
### Aggiunto
- Log di controllo delle azioni amministrative, cancellazione dei dati di un
  cliente, codici di recupero MFA, script di backup riscritto per NS8/podman.

## [0.14.0 – 0.14.1] - 2026-09-16
### Aggiunto
- Aggiornamenti mancanti (report e PDF), avviso host offline, gestione utenti,
  password e MFA, monitoraggio della piattaforma, layout responsive, relay
  syslog (0.14.0).
- Scadenza dei certificati agent nel report notturno (0.14.1).

## [0.13.0 – 0.13.1] - 2026-09-11
### Aggiunto
- Menu laterale verticale e gruppi per cliente nella dashboard (0.13.0).
- Report notturno via email, configurabile da Impostazioni (0.13.1).

## [0.12.0 – 0.12.4] - 2026-09-10
### Aggiunto
- Sigillatura centralizzata dei log admin-access; caricamento degli archivi
  sul portale; dashboard dei dispositivi per cliente (0.12.0).
- Anagrafica cliente (0.12.1); scheda Archivi con download per file, per
  giorno e completo (0.12.2); caselle di gravità cliccabili (0.12.3).

## [0.11.0 – 0.11.6] - 2026-09-09 / 2026-09-10
### Sicurezza
- **Isolamento tra clienti nelle ricerche**: il filtro per stream veniva
  ignorato e Ricerca/Inventario restituivano i dati di tutti i clienti
  (0.11.0, 0.11.1).
- Limite ai tentativi su verify-mfa, CSRF, header di sicurezza, tracciamento
  e revoca dei certificati (0.11.1).

### Aggiunto
- Marchio StartApp/Defendia su login e report PDF, tema chiaro (0.11.3).

### Corretto
- Windows 11 riconosciuto nell'inventario (0.11.4); orari mostrati nel fuso
  locale (0.11.5, 0.11.6); errore all'avvio di dashboard.py (0.11.2).

## [0.10.0 – 0.10.16] - 2026-09-08 / 2026-09-09
### Aggiunto
- CA interna, pacchetto agent generato dal pannello, inventario hardware,
  software e risorse (0.10.0); agent Windows (.exe) per cliente (0.10.7);
  export PDF dell'inventario (0.10.11).

### Corretto
- Agent Windows come servizio: build PyInstaller `--onedir`, DLL di pywin32
  incluse, avvio dal Service Control Manager (0.10.12 – 0.10.16).
- Rotte di setup protette da token invece che dall'IP, inaffidabile con
  slirp4netns (0.10.2, 0.10.3); varie correzioni a ricerche e inventario.

## [0.9.0] - 2026-09-07
### Aggiunto
- Archiviazione locale compressa e sigillata (hash chain e marca temporale
  facoltativa) di tutti i log raccolti dagli agent Linux e Windows.

## [0.8.0 – 0.8.4] - 2026-09-04
### Aggiunto
- Pannello semplificato: Clienti, Ricerca, Allarmi, Notifiche (0.8.0).

### Corretto
- Creazione degli index set compatibile con Graylog 6.1 (0.8.3, 0.8.4).

## [0.7.1 – 0.7.5] - 2026-09-03
### Corretto
- CI: percorso immagine in minuscolo per ghcr.io, Dockerfile del gate (0.7.2, 0.7.3).
- ai-service: httpx fissato (0.7.4); API di auth-service spostate da `/api`
  a `/_authgate` (0.7.5).

## [0.7.0] - 2026-08-21
### Aggiunto
- `scripts/seal-admin-logs.sh`: supporto per TSA (marca temporale RFC 3161)
  con autenticazione — Basic Auth (`ADMIN_LOG_TSA_USER`/`ADMIN_LOG_TSA_PASSWORD`)
  o certificato client (`ADMIN_LOG_TSA_CLIENT_CERT`/`ADMIN_LOG_TSA_CLIENT_KEY`),
  necessario per fornitori qualificati eIDAS a pagamento come **Namirial**
  (finora supportata solo la TSA gratuita `freetsa.org`, senza autenticazione).
- `.env.example`: nuovi campi per la configurazione TSA, con fallback
  automatico alla TSA gratuita se lasciati vuoti (il meccanismo di hash
  chain resta operativo anche prima di avere le credenziali Namirial).
- Guida di installazione e documento tecnico ISO 27001 aggiornati.

### Da fare (bloccato in attesa di dati esterni)
- Endpoint RFC 3161 e metodo di autenticazione esatti di Namirial non
  ancora noti/configurati: l'utente deve recuperarli dal supporto/contratto
  Namirial in fase di attivazione del servizio.

## [0.6.0] - 2026-08-21
### Cambiato — architettura NS8 riscritta da zero su basi reali
- Rimosso lo scaffold `ns8-logplatform/` (basato su ipotesi non verificate:
  `module.json`, `install.sh`/`update.sh`).
- Studiati due moduli NS8 reali (ns8-kickstart, ns8-dokuwiki) per ricavare
  la struttura vera: script numerati in `imageroot/actions/`, metadati come
  label sull'immagine (non un file di config), systemd unit per-container,
  UI Vue2+Carbon, registrazione route Traefik con dominio+Let's Encrypt.
- La piattaforma è ora divisa in **7 moduli NS8 separati** (repository
  indipendenti in `ns8-modules/`, non incluso in questo ZIP):
  logplatform-mongodb, -opensearch, -mariadb, -graylog, -auth, -ai, -gate.
- Aggiunta l'immagine `gate` (nginx senza gestione TLS propria — la fa
  Traefik centralmente — con config via envsubst) alla CI del repo
  principale, insieme ad auth-service e ai-service.
- Ogni modulo validato con `py_compile`/`bash -n` su tutti gli script e
  JSON, ma **nessuno testato su un nodo NS8 reale**.

### Dichiarato esplicitamente come non confermato
- Il meccanismo di discovery tra i nostri moduli custom
  (`resolve_agent_id` + `tasks.run('get-configuration')`) è
  un'estrapolazione dal solo uso confermato per parlare con Traefik — mai
  visto in un esempio reale di due moduli applicativi che si scoprono a
  vicenda. Vedi `ns8-modules/README.md` per il piano di fallback se non
  funziona (parametri espliciti invece di discovery automatico).

## [0.5.0] - 2026-08-21
### Aggiunto
- Flusso di **conservazione a norma dei log amministratori di sistema**
  (Provvedimento Garante Privacy 27/11/2008): retention ≥ 6 mesi,
  completezza monitorabile, integrità verificabile.
- `agent.ini`: nuove chiavi `admin_log_files` / `admin_event_logs` per
  taggare i log di accesso amministrativo (`log_class=admin-access`),
  distinti dai log generici. Supportato sia da `agent.py` (Linux) sia da
  `agent_windows.py`.
- `scripts/provision-admin-log-stream.sh`: stream + index set dedicati per
  tenant, retention configurabile (default 200 giorni).
- `scripts/seal-admin-logs.sh`: sigillo giornaliero via hash chain SHA-256
  + marca temporale RFC 3161 opzionale (TSA esterna), immutabilità
  filesystem best-effort (`chattr +i`).
- `scripts/verify-admin-log-seal.sh`: verifica la catena di sigilli e
  segnala manomissioni o buchi — testata con simulazione locale (catena
  integra riconosciuta, manomissione rilevata correttamente).
- Guida di installazione: nuova sezione 13 con la procedura completa.
- Documento tecnico ISO 27001: nuova sezione dedicata al Provvedimento
  Garante Amministratori di Sistema.
- Template politiche organizzative: tabella per l'elenco nominativo degli
  amministratori di sistema con riesame annuale (richiesto dalla norma).

### Limiti noti
- `chattr +i` non è WORM hardware: un root compromesso può rimuoverlo. La
  robustezza reale viene dalla combinazione con la marca temporale esterna.
- TSA di default (`freetsa.org`) è gratuita e best-effort: per un valore
  probatorio più solido in contesti ad alto rischio, valutare una TSA
  qualificata a pagamento.

## [0.4.0] - 2026-08-21
### Aggiunto
- `agent/Dockerfile`: containerizza l'agent Linux per l'esecuzione su
  Synology (Container Manager, DSM 7+) e QNAP (Container Station), che
  supportano Docker/Compose nativamente sui modelli x86.
- `agent/docker-compose.nas.yml.example`: esempio di deploy con volumi per
  config e log del NAS montati in sola lettura.
- `scripts/generate-agent.sh` genera ora anche `config-nas/` (agent.ini con
  percorsi adattati all'esecuzione in container) dentro il pacchetto per
  ogni tenant.
- Guida di installazione aggiornata con la procedura Synology/QNAP.

### Non incluso (deliberatamente)
- Pacchetti nativi SPK (Synology) o QPKG (QNAP): richiedono i toolchain
  ufficiali dei vendor, non disponibili/validabili in questo ambiente. La
  via containerizzata copre lo stesso bisogno con molto meno rischio.

## [0.3.0] - 2026-08-21
### Aggiunto
- `agent/gelf_transport.py`: modulo condiviso (connessione mTLS + formato
  GELF) usato ora sia dall'agent Linux sia da quello Windows.
- `agent/agent_windows.py`: agent per Windows, installabile come **servizio
  Windows nativo** (via pywin32, senza tool esterni tipo NSSM). Legge sia
  file di log testuali sia canali dell'Event Log di Windows (Application,
  System, ecc.), con stato persistito in `agent_state.json` per non
  reinviare eventi già spediti dopo un riavvio del servizio.
- `agent/logplatform-agent.service.example`: unit systemd per far girare
  l'agent Linux come servizio persistente.
- `agent/requirements-windows.txt`: dipendenza `pywin32` per l'agent Windows.
- `scripts/generate-agent.sh` ora include nel pacchetto per il cliente sia
  l'agent Linux sia quello Windows, con istruzioni per entrambi.
- Guida di installazione aggiornata con le sezioni "systemd" (Linux) e
  "servizio Windows nativo".

## [0.2.0] - 2026-08-21
### Aggiunto
- `.github/workflows/build-images.yml`: build e pubblicazione automatica su
  GitHub Container Registry (ghcr.io) delle immagini `auth-service` e
  `ai-service` ad ogni tag `v*.*.*`.
- `ns8-logplatform/module.json`: sezione `images` con i riferimenti alle
  immagini (proprie su ghcr.io + upstream pubbliche) usate dallo scaffold.
- `ns8-logplatform/imageroot/install.sh` e `update.sh`: ora leggono
  `module.json` e fanno `podman pull` delle immagini indicate.
- `deploy-ns8/README.md`: procedura per collegare il repo GitHub a NS8 per
  installazione/aggiornamento del modulo (da verificare contro la doc NS8
  aggiornata: comandi CLI e nomi campi non ancora validati su nodo reale).

## [0.1.0] - 2026-08-21
### Aggiunto
- Stack Docker Compose iniziale a 7 servizi: mongodb, opensearch, graylog,
  mariadb, auth-service, ai-service, nginx.
- `auth-service`: login con password (bcrypt) + MFA TOTP obbligatoria al
  primo accesso, sessioni firmate, blocco dopo tentativi falliti ripetuti.
- `ai-service`: triage automatico degli allarmi Graylog tramite Claude,
  inoltro opzionale a webhook per tenant.
- `agent/agent.py`: invio log via GELF TCP con mTLS, tagging per tenant.
- Script: `setup-encrypted-volume.sh`, `generate-ca.sh`,
  `create-gelf-tls-input.sh`, `provision-tenant.sh`, `generate-agent.sh`,
  `backup.sh`, `restore.sh`, `watchdog.sh`, `create-alert-notification.sh`.
- Scaffold modulo NethServer 8 (`ns8-logplatform/`) — non ancora validato
  su un nodo reale.
- Manuali: guida installazione (PDF), mappatura controlli tecnici ISO
  27001 (PDF), template politiche organizzative ISO 27001 (DOCX).

### Noto/aperto
- Nessuna alta affidabilità (single point of failure, singolo nodo).
- Modulo NS8 nativo non testato su cluster reale.
- Backup non copre gli indici OpenSearch (solo config + utenti/MFA).
