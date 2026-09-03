# Changelog

Tutte le modifiche rilevanti di questo progetto sono documentate qui.
Formato ispirato a [Keep a Changelog](https://keepachangelog.com/it/1.0.0/),
versionamento secondo [SemVer](https://semver.org/lang/it/).

Finché la versione resta `0.x.y`, l'API/configurazione può ancora cambiare
in modo non retrocompatibile tra una MINOR e l'altra: si esce da `0.x`
(passando a `1.0.0`) solo dopo una validazione reale su un ambiente di
produzione, incluso NethServer 8.

## [Non rilasciato]
- (spazio per le prossime modifiche)

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
