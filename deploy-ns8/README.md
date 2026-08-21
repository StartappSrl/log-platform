# Deploy su NethServer 8

NethServer 8 esegue i moduli come container **Podman** rootless, orchestrati
da `systemd` (systemd-quadlet / `podman generate systemd`), e li espone
tramite il **modulo Traefik** integrato (reverse proxy centrale di NS8),
non tramite nginx dedicato. Ci sono due strade, in ordine di sforzo:

## Opzione A — più rapida: dentro un modulo "Loop"/SSH, come stack Docker/Podman

1. Installa un nodo NS8 e abilita l'accesso SSH root al nodo.
2. Copia `final-package/` sul nodo, es. in `/srv/logplatform`.
3. NS8 usa Podman al posto di Docker: o installi `docker-compose` con il
   binario compatibile Podman (`podman-compose`), oppure traduci
   `docker-compose.yml` in systemd unit con `podman generate systemd`
   dopo aver creato i container manualmente con `podman run`.
   Il modo più semplice:
   ```bash
   dnf install -y podman-compose   # o pip install podman-compose
   cd /srv/logplatform
   podman-compose up -d
   ```
4. **Esposizione pubblica**: NS8 di norma vuole che i servizi passino dal
   suo Traefik centrale (per TLS/Let's Encrypt centralizzato). In questa
   modalità "manuale" invece pubblichi tu nginx direttamente su una porta
   host (es. `8443`) e crei una regola nel firewall di NS8, oppure registri
   il servizio nel Traefik di NS8 con le label appropriate (vedi doc NS8
   "expose an application").
5. Backup/restore, watchdog, cifratura disco: gli script in `scripts/`
   funzionano invariati (sono bash generici), basta avere `docker`/`podman`
   con l'alias compatibile nel PATH.

Questa opzione è più veloce ma "vive fuori" dal Cockpit NS8: non compare
come modulo installabile/gestibile dalla UI di NS8, va amministrata da
riga di comando.

## Opzione B — modulo NS8 nativo (scaffold in `../ns8-logplatform/`)

Un modulo NS8 "vero" è un pacchetto con:
- `module.json` (metadati, porte, permessi)
- script di lifecycle (`install`, `configure`, `update`, `remove`) in
  `imageroot/`
- systemd unit per i container che compongono il modulo
- integrazione con Traefik di NS8 per il routing HTTPS
- (opzionale) una UI in Cockpit

Lo scaffold in `ns8-logplatform/` fornisce l'ossatura (metadati, script di
lifecycle che richiamano gli stessi container Podman dello stack, unit
systemd generate da `podman generate systemd`) ma **non è stato validato
su un'installazione NS8 reale** — è il punto più onestamente "aperto" di
questo progetto. Prima di usarlo in produzione:

1. Segui la guida ufficiale NethServer "Develop a module" per verificare
   la struttura esatta attesa da `module.json` nella versione di NS8 che usi
   (i campi cambiano tra release).
2. Testa `install.sh` su un nodo NS8 di prova, non in produzione.
3. Configura l'integrazione con Traefik (il modulo deve registrarsi con le
   label/route corrette) al posto del blocco nginx incluso in questo
   pacchetto, che è pensato per un deploy standalone (Opzione A).
4. Valuta se OpenSearch (che richiede risorse non banali: RAM, `vm.max_map_count`)
   è compatibile con le risorse del nodo NS8 target.

## Cosa resta genuinamente da fare per un modulo NS8 completo

- Pagina di amministrazione nativa in Cockpit (oggi lo scaffold espone solo
  un link diretto all'app dietro Traefik).
- Test end-to-end di installazione/aggiornamento/rimozione su un cluster
  NS8 reale.
- Eventuale adattamento della persistenza dei volumi alle convenzioni NS8
  (di norma sotto `/home/<modulo>/`, gestite da systemd-tmpfiles).
