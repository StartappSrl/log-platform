# Generazione pacchetto agent dal pannello + input GELF su NS8

Questo pacchetto aggiunge: una CA interna gestita da `auth-service` (persistente),
un bottone nel pannello per scaricare il pacchetto agent di un cliente, e
l'azione NS8 mancante per collegare Graylog alla stessa CA (input GELF TLS).

**Tutto testato con handshake mTLS reali in locale** (non solo letto/scritto):
CA → certificato server → certificato client → connessione TLS vera riuscita,
sia isolatamente sia attraverso l'app Flask completa via HTTP.

## Ordine delle operazioni (importante, ha dipendenze)

### 1. Repo principale (`final-package`) — applica le modifiche

**1a.** Aggiungi a `auth-service/requirements.txt`:
```
cryptography==43.0.1
```

**1b.** Copia questi 4 file dentro `auth-service/app/`:
- `ca_manager.py` → `auth-service/app/ca_manager.py`
- `setup_routes.py` → `auth-service/app/setup_routes.py`
- `agent_packager.py` → `auth-service/app/agent_packager.py`
- `dashboard_additions.py` → **non copiarlo direttamente**: apri il tuo
  `auth-service/app/dashboard.py` esistente e aggiungi in fondo il
  contenuto di questo file (import in cima al file esistente, funzione in
  fondo). Non sovrascrivere: contiene solo la rotta nuova.

**1c.** In `auth-service/app/__init__.py`, aggiungi la registrazione del
nuovo blueprint (vicino a dove registri già `dash`):
```python
from .setup_routes import setup_bp
app.register_blueprint(setup_bp)
```

**1d.** Crea la cartella `auth-service/agent_templates/` e copiaci dentro
(dalla cartella `agent/` del repo, quella con l'agent Linux/Windows):
```bash
mkdir -p auth-service/agent_templates
cp agent/agent.py agent/agent_windows.py agent/gelf_transport.py \
   agent/local_archive.py agent/requirements-windows.txt \
   agent/logplatform-agent.service.example \
   auth-service/agent_templates/
```

**1e.** Sostituisci `auth-service/Dockerfile` con `auth-service-Dockerfile`
(rinominalo in `Dockerfile`).

**1f.** In `docker-compose.yml`, nel servizio `auth-service`, aggiungi il
volume per la CA persistente:
```yaml
  auth-service:
    ...
    volumes:
      - auth_ca_data:/data/ca
```
E nella sezione `volumes:` in fondo al file, aggiungi:
```yaml
volumes:
  ...
  auth_ca_data:
```

**1g.** Nel tuo `.env`, verifica che `PUBLIC_DOMAIN` sia già impostato
correttamente (serve per generare i pacchetti agent con l'hostname giusto).

Poi commit/tag/push come al solito (consiglio `v0.9.1` o superiore).

### 2. Dashboard — aggiungi il bottone (facoltativo ma consigliato)

In `dashboard.html`, dentro la tabella dei clienti (`tenants-table`), aggiungi
una colonna con un link di download. Esempio minimo da inserire nella riga
generata da `loadTenants()`:
```javascript
tr.innerHTML = `<td>${t.name}</td><td>${t.display_name || ''}</td><td>${t.stream_id || ''}</td>
  <td><a href="/_authgate/dashboard/tenants/${t.name}/agent-package">Scarica agent</a></td>`;
```
(e aggiungi una colonna `<th>Agent</th>` nell'intestazione della tabella)

### 3. NS8 — modulo `auth`

Aggiorna 3 file nel repo `ns8-logplatform-auth`:
- `imageroot/actions/configure-module/20configure` → sostituisci con
  `ns8-auth-20configure`
- `imageroot/actions/configure-module/validate-input.json` → sostituisci
  con `ns8-auth-validate-input.json`
- `imageroot/systemd/user/logplatform-auth.service` → sostituisci con
  `ns8-auth-logplatform-auth.service`

Ricostruisci l'immagine wrapper (stessa procedura di sempre: pulisci cache,
`build-images.sh`, `buildah push`), poi rimuovi/reinstalla l'istanza `auth`,
configurandola con **un parametro in più rispetto a prima**, `public_domain`:

```bash
api-cli run module/<istanza-auth>/configure-module --data '{"mariadb_port": ..., "mariadb_user": "...", "mariadb_password": "...", "mariadb_database": "...", "graylog_port": ..., "graylog_api_user": "admin", "graylog_api_password": "...", "public_domain": "logplatform.startappitalia.it"}'
```

### 4. NS8 — modulo `graylog`

**ATTENZIONE — rischio dati**: aggiornare il systemd unit di `graylog`
richiede ricostruire l'immagine e reinstallare l'istanza, come per gli altri
moduli. A differenza degli altri, `graylog` contiene i log già raccolti
(incluso il tenant `startapp` che hai già creato). Se `remove-module
--no-preserve` cancella anche i volumi dati, **perderai i log già raccolti
finora** (non le configurazioni, quelle sono ricreabili, ma la cronologia
log sì). Su un nodo di test è probabilmente accettabile; se non lo è,
fermati qui e fammelo sapere prima di procedere.

Aggiorna:
- `imageroot/systemd/user/logplatform-graylog.service` → sostituisci con
  `ns8-graylog-logplatform-graylog.service` (aggiunge il mount dei
  certificati e pubblica la porta 12201 su tutte le interfacce, non solo
  127.0.0.1 — necessario perché gli agent client si connettono da fuori
  dal nodo)
- Crea una nuova azione `imageroot/actions/configure-gelf-input/20configure`
  con il contenuto di `configure-gelf-input-action.py` (crea prima le
  cartelle: `mkdir -p imageroot/actions/configure-gelf-input`)

```bash
chmod +x imageroot/actions/configure-gelf-input/20configure
```

Ricostruisci, ripubblica, rimuovi/reinstalla `graylog` come al solito.

**Poi**, con `graylog` di nuovo su, apri la porta nel firewall del nodo
(comando indicativo, verifica con la tua distribuzione):
```bash
firewall-cmd --add-port=12201/tcp --permanent
firewall-cmd --reload
```

### 5. Collega le due cose: emetti il certificato server e configuralo su graylog

Prima, recupera il certificato CA e un certificato server dal modulo `auth`
(chiamando l'endpoint di setup **direttamente sul nodo**, non tramite gate):

```bash
sudo -u <istanza-auth> XDG_RUNTIME_DIR=/run/user/$(id -u <istanza-auth>) \
  curl -s -X POST http://127.0.0.1:<porta-auth>/_authgate/setup/issue-server-cert \
  -H "Content-Type: application/json" \
  -d '{"common_name": "graylog", "dns_names": ["graylog"]}' \
  > /tmp/graylog-cert-response.json

cat /tmp/graylog-cert-response.json | python3 -m json.tool
```

Poi usa quell'output per configurare l'input su `graylog` (estrai
manualmente `cert_pem`, `key_pem`, `ca_pem` dal JSON e incollali):

```bash
python3 -c "
import json
d = json.load(open('/tmp/graylog-cert-response.json'))
payload = {
    'ca_pem': d['ca_pem'], 'server_cert_pem': d['cert_pem'], 'server_key_pem': d['key_pem'],
    'graylog_api_user': 'admin', 'graylog_api_password': 'Start@2026@@'
}
print(json.dumps(payload))
" > /tmp/gelf-input-payload.json

api-cli run module/<istanza-graylog>/configure-gelf-input --data "$(cat /tmp/gelf-input-payload.json)"
```

### 6. Verifica finale

Dal pannello, scarica il pacchetto agent per un cliente esistente e prova a
farlo connettere (anche solo in locale sul nodo stesso, per un primo test):

```bash
tar xzf agent-<tenant>.tar.gz
cd agent-<tenant>
python3 agent.py --config agent.ini
```

Se si connette senza errori TLS, l'intera catena (CA in auth-service →
certificato server su Graylog → certificato client nel pacchetto) funziona
davvero end-to-end.
