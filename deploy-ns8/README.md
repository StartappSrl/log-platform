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

## Collegare il repo GitHub a NS8 per install/aggiornamenti

Questo è il pezzo che rende gli aggiornamenti gestibili da NS8 invece che
manuali. Il meccanismo (verificalo contro la documentazione ufficiale NS8
aggiornata prima di usarlo in produzione — i comandi esatti possono variare
tra versioni):

### 1. Prepara il repository GitHub

- Usa lo stesso repo già configurato (con `git remote add origin ...`), oppure
  crea un repo dedicato solo per `ns8-logplatform/` se preferisci tenerlo
  separato dal resto dello stack.
- `.github/workflows/build-images.yml` (già incluso) builda e pubblica su
  **GitHub Container Registry** (`ghcr.io`) le immagini di `auth-service` e
  `ai-service` ogni volta che pushi un tag `v*.*.*` — lo stesso tag che usi
  per il CHANGELOG.
- Le altre immagini (graylog, mongodb, opensearch, mariadb, nginx) sono
  pubbliche upstream: non serve costruirle, basta referenziarle in
  `module.json` → `images`.

### 2. Rendi pubbliche (o autorizzate) le immagini ghcr.io

Dopo il primo push di un tag, su GitHub vai in **Packages** (della tua
organizzazione/utente) e imposta la visibilità dei package `auth-service`
e `ai-service` su **Public**, oppure configura un pull secret su NS8 se
preferisci tenerle private (dettaglio da verificare con la doc NS8 corrente
su come passare credenziali registry ai moduli).

### 3. Sostituisci i placeholder in `module.json`

In `ns8-logplatform/module.json`, sotto `"images"`, sostituisci
`ghcr.io/TUO-USER/TUO-REPO/...` con il path reale del tuo repository
GitHub (minuscolo, come richiesto da ghcr.io).

### 4. Installazione iniziale su un nodo NS8

Dal Cluster Admin di NS8 (UI "Software Center" → "Installa da URL", oppure
da CLI se disponibile sul tuo nodo):

```bash
# Sintassi indicativa - verifica il comando esatto della tua versione NS8
add-module https://github.com/TUO-USER/TUO-REPO v0.1.0
```

NS8 scarica il tarball del tag indicato, esegue `imageroot/install.sh`
(vedi sopra) che a sua volta legge `module.json` e tira giù le immagini
elencate.

### 5. Aggiornamento a una nuova versione

Flusso ricorrente, una volta che hai una nuova versione pronta:

```bash
# in locale, nel repo del progetto
git tag -a v0.2.0 -m "descrizione della release"
git push origin v0.2.0
# la Action builda e pubblica le nuove immagini auth-service/ai-service su ghcr.io
```

Poi, sul nodo NS8:

```bash
# Sintassi indicativa - verifica il comando esatto (potrebbe essere
# "update-module", un'azione dalla UI, o api-cli specifico)
update-module logplatform v0.2.0
```

Questo dovrebbe eseguire `imageroot/update.sh` (che nello scaffold fa un
backup e riavvia i container con le nuove immagini) **senza perdere i dati**
persistenti (volumi sotto `/home/logplatform/`, gestiti da NS8).

### Nota sulla verifica

I nomi esatti dei comandi (`add-module`, `update-module`), la posizione
del riferimento alle immagini dentro `module.json` (`images` potrebbe non
essere il campo giusto per la tua versione di NS8) e il meccanismo di
credenziali per registry privati sono i punti più a rischio di essere
cambiati o leggermente diversi da quanto scritto qui. Prima di affidarti a
questo flusso in produzione, confronta con la documentazione ufficiale
aggiornata di NethServer 8 sullo sviluppo/distribuzione moduli.

## Cosa resta genuinamente da fare per un modulo NS8 completo

- Pagina di amministrazione nativa in Cockpit (oggi lo scaffold espone solo
  un link diretto all'app dietro Traefik).
- Test end-to-end di installazione/aggiornamento/rimozione su un cluster
  NS8 reale.
- Eventuale adattamento della persistenza dei volumi alle convenzioni NS8
  (di norma sotto `/home/<modulo>/`, gestite da systemd-tmpfiles).
