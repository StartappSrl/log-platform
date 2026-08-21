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
