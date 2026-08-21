# Log Platform — Graylog multi-tenant con triage AI

Piattaforma di log management basata su Graylog, pensata per gestire i log di più
clienti (multi-tenant), con accesso protetto da MFA e un servizio di triage
automatico degli allarmi basato su Claude.

> Questo pacchetto è una base solida e funzionante, non un prodotto enterprise
> "chiavi in mano". Ogni script ha commenti su cosa va adattato al tuo ambiente
> reale (dominio, IP, dimensionamento). Leggi `manuali/guida-installazione-log-platform.pdf`
> per la procedura passo-passo completa.

## Architettura (7 servizi Docker)

| Servizio      | Ruolo                                                            |
|---------------|-------------------------------------------------------------------|
| `mongodb`     | Storage di configurazione di Graylog                              |
| `opensearch`  | Storage e ricerca dei messaggi di log                              |
| `graylog`     | Server Graylog (ingestione, ricerca, UI, alerting)                 |
| `mariadb`     | Database utenti/MFA per `auth-service`                            |
| `auth-service`| Login + MFA (TOTP), gate di autenticazione davanti a Graylog       |
| `ai-service`  | Riceve gli allarmi Graylog e li fa analizzare da Claude            |
| `nginx`       | Reverse proxy TLS pubblico, applica il gate di autenticazione      |

Il traffico dei client (log) **non passa da nginx**: gli agent inviano log via
GELF TCP+TLS con certificato client, direttamente all'input Graylog esposto su
una porta dedicata (mai pubblicata su Internet senza motivo — vedi manuale).

```
Client (agent.py) --GELF/TLS(mTLS)--> Graylog input
Browser admin     --HTTPS-->  nginx --auth_request--> auth-service
                                   \-> proxy_pass --> Graylog UI
Graylog alert     --HTTP webhook--> ai-service --> Claude API --> notifica
```

## Struttura del pacchetto

```
final-package/
├── docker-compose.yml       Stack completo
├── .env.example             Variabili/password da compilare (copiare in .env)
├── agent/                   Agent Python da installare sui client
├── auth-service/            Login + MFA + MariaDB
├── ai-service/              Triage allarmi con Claude
├── nginx/                   Reverse proxy + gate MFA
├── ca/                      CA interna per mTLS (generata al primo avvio, vuota qui)
├── scripts/                 Provisioning, agent, backup/restore, cifratura, watchdog
├── deploy-ns8/              Guida per l'esecuzione su NethServer 8
├── ns8-logplatform/         Scaffold di modulo NS8 nativo (podman-based)
└── manuali/
    ├── guida-installazione-log-platform.pdf
    ├── iso27001-log-platform.pdf
    └── iso27001-politiche-organizzative.docx
```

## Quickstart

```bash
cp .env.example .env                      # compila TUTTE le password/segreti
sudo ./scripts/setup-encrypted-volume.sh /dev/sdX /mnt/graylog-data
./scripts/generate-ca.sh                  # CA interna per mTLS agent <-> Graylog
# metti un certificato TLS pubblico in nginx/certs/fullchain.pem e privkey.pem
docker compose up -d
./scripts/create-gelf-tls-input.sh        # crea l'input GELF TCP+TLS su Graylog
docker compose exec auth-service python -m app.create_user   # primo utente admin
./scripts/provision-tenant.sh acme        # crea stream+index set per il tenant "acme"
./scripts/generate-agent.sh acme          # pacchetto agent+certificato per "acme"
./scripts/create-alert-notification.sh acme   # collega gli allarmi del tenant all'AI
```

Poi apri `https://tuodominio/login.html`.

## Limiti onesti / cosa manca

- **Ridondanza**: lo stack è a singolo nodo. `docker-compose.yml` non fa HA;
  per HA reale servono più nodi OpenSearch e un failover del reverse proxy.
- **UI grafica del modulo NS8**: lo scaffold in `ns8-logplatform/` espone
  l'app via reverse proxy di NS8, ma non ha ancora una pagina di
  amministrazione nativa nel Cockpit di NS8 (solo un link diretto all'app).
- **Validazione su cluster NS8 vivo**: non è stata (né può essere) testata
  qui; vedi `deploy-ns8/README.md` per i punti da verificare all'installazione.

Tutto il resto — autenticazione MFA, multi-tenancy, mTLS per gli agent,
backup/restore, watchdog, triage AI degli allarmi — è funzionante nello stack
Docker e riutilizzabile as-is dentro NS8 (che esegue container via Podman).
