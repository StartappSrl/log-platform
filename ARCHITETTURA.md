# Log Platform — Documentazione d'insieme

Questo documento serve per orientarsi nell'architettura senza dover
rileggere l'intera cronologia di sviluppo. Aggiornalo quando cambi
qualcosa di strutturale — un documento vecchio è peggio di nessun
documento, perché dà falsa sicurezza.

## Cos'è

Piattaforma multi-cliente (multi-tenant) di raccolta e consultazione log,
basata su Graylog, con un pannello web semplificato al posto
dell'interfaccia nativa di Graylog. Pensata per un system integrator che
gestisce i log di più clienti su un'unica installazione condivisa,
mantenendo i dati di ciascuno isolati dagli altri.

## I moduli (NS8, ognuno un container separato)

| Modulo | Cosa fa | Dati che possiede |
|---|---|---|
| `mariadb` | Database relazionale | Utenti, tenant, impostazioni SMTP, log di controllo, stato archivi/alert |
| `mongodb` | Configurazione interna di Graylog | Stream, index set, allarmi, notifiche, dashboard |
| `opensearch` | Motore di ricerca | Gli indici dei log veri e propri (il grosso dei dati, non nel backup per motivi di spazio) |
| `graylog` | Riceve i log (GELF, syslog), li instrada per tenant | Nessuno stato proprio oltre a MongoDB/OpenSearch |
| `auth` | Il "cervello" applicativo: pannello, login, MFA, API | Codice (`auth-service/`), volumi: CA (`logplatform-auth-ca`), archivi caricati dagli agent (`logplatform-auth-archives`) |
| `gate` | Reverse proxy + pagina di login | `login.html`, instrada le richieste verso `auth` dopo autenticazione |

## Come i log arrivano e restano isolati per tenant

1. **Agent** (Python, gira sul PC/server del cliente): legge file di log
   o Windows Event Log, li impacchetta in formato GELF, li manda a
   Graylog via TCP+TLS con **certificato client** firmato dalla CA
   interna (mutua autenticazione - solo chi ha il certificato giusto
   può mandare dati).
2. Ogni messaggio GELF include un campo `_tenant` con il nome del
   cliente (l'agent lo sa dalla sua configurazione, `agent.ini`).
3. Su Graylog, ogni tenant ha il suo **stream** con una regola che
   filtra `tenant = <nome>` - solo i messaggi con quel campo finiscono
   in quello stream, quindi in quell'index set (dati fisicamente
   separati in OpenSearch).
4. **Relay syslog** (opzionale, per dispositivi che parlano solo
   syslog): l'agent stesso apre una porta locale, riceve syslog dalla
   rete del cliente, e lo inoltra come GELF con lo stesso meccanismo -
   il dispositivo non tocca mai Internet direttamente.

**Limite noto**: la CA interna, se compromessa, invaliderebbe la fiducia
in tutti i certificati - non c'è ancora un vero enforcement della revoca
(vedi `NGINX-CRL-PROXY.md` per il disegno di una soluzione, non applicata).

## Struttura del repository (`final-package/`)

```
auth-service/
  app/            - il codice Flask (dashboard.py ha quasi tutte le rotte)
  static/         - dashboard.html, login.html (il frontend, tutto in questi due file)
  agent_templates/ - copia dei file agent, usata per generare i pacchetti scaricabili
agent/            - il codice sorgente dell'agent (agent.py, agent_windows.py, ecc.)
gate/             - login.html, nginx.conf.template
scripts/          - backup.sh, seal-admin-logs.sh, check_disk_space.sh, script di provisioning
ns8-modules/ (repo separati) - un repo per modulo NS8 (mariadb, mongodb, graylog, auth, gate)
```

**Nota importante**: `auth-service/app/models.py` e `dashboard.py` sono
diventati grandi nel tempo, costruiti per accumulo (nuove funzioni
accodate in fondo) - non sono organizzati per argomento. Cercare con
`grep` per nome di funzione è più affidabile che scorrere il file.

## Tabelle principali in MariaDB

- `tenants` - un cliente per riga, con anagrafica (ragione sociale, P.IVA, ecc.)
- `users` - utenti del pannello (admin o legati a un tenant), con MFA e codici di recupero
- `smtp_settings` - configurazione email per il report notturno (una riga sola, id=1)
- `audit_log` - azioni amministrative sensibili (chi ha fatto cosa)
- `offline_alert_state` - traccia quali host sono già stati segnalati offline (evita spam)
- `failed_logins` - tentativi di login/MFA falliti (per il blocco anti brute-force)

## Operazioni comuni

**Aggiungere un cliente**: scheda Clienti nel pannello - crea automaticamente
stream+index set Graylog, certificato, e i pacchetti agent scaricabili.

**Aggiornare il codice** (dopo una modifica): commit+tag su Git → aspetta
CI verde → aggiorna la versione in `build-images.sh` del modulo NS8
interessato → ricostruisci con `build-images.sh` → `buildah push` →
`update-module` sul nodo → verifica/correggi la variabile immagine in
`.config/state/environment` se necessario → riavvia il servizio →
**controlla sempre i log** (`journalctl --user -u <servizio>`), non
fidarti solo che il comando sia andato a buon fine.

**Rotazione credenziali**: vedi `ROTAZIONE-CREDENZIALI.md`.

**Ripristino da backup**: vedi `scripts/RIPRISTINO.md`.

## Cose da sapere prima di modificare qualcosa

- I comandi `sudo -u <istanza>` falliscono se lanciati da una cartella
  di proprietà di `root` (es. dentro il repo) - fai sempre `cd /tmp`
  prima.
- I volumi podman con SELinux (di default su questo sistema, RHEL-based)
  possono dare problemi di permessi con bind-mount ad-hoc - preferisci
  `podman exec` dentro un container già in esecuzione quando possibile.
- `update-module` non sempre rigenera il file systemd dal template -
  verifica sempre con `grep` dopo, non darlo per scontato.
- Ogni modifica a `dashboard.html` va fatta con uno script di patch che
  verifica il testo esatto prima di sostituire (mai una sostituzione
  "alla cieca") - il file è troppo importante e troppo modificato nel
  tempo per rischiare di romperlo con un replace impreciso.

## Cosa manca ancora (stato a questa data)

- Enforcement reale della revoca certificati (CRL) - disegnato, non applicato
- Rotazione credenziali - mai fatta da quando sono state generate
- Marca temporale RFC3161 qualificata (Namirial) - in attesa di contratto/credenziali
- Ridondanza multi-nodo - tutto gira su una singola macchina
- Suite di test automatici in CI - i bug finora sono stati trovati testando a mano
