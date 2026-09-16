# Procedura di ripristino da backup

**IMPORTANTE**: questa procedura non è mai stata provata su una macchina
reale (non ho un ambiente NS8 completo disponibile qui per testarla). È
scritta con attenzione, basandosi sui comandi che abbiamo già usato con
successo in questa sessione per situazioni simili — ma un backup/ripristino
mai testato davvero è, in pratica, un backup di cui non ti puoi fidare
fino a prova contraria. **Fai una prova completa** (magari su una VM di
test, non in produzione) prima di considerarlo affidabile per un'emergenza
vera.

## Prerequisiti

- I moduli NS8 (mariadb, mongodb, graylog, auth, gate) devono essere già
  installati sulla macchina di destinazione, anche vuoti/appena creati
- Hai il backup completo generato da `backup.sh` (cartella con
  `mariadb-all.sql`, `mongodb-graylog.archive`, `ca-volume.tar.gz`,
  `archives-volume.tar.gz`, `module-state/`)

## 1. Ripristino MariaDB

```bash
sudo -u <istanza-mariadb> XDG_RUNTIME_DIR=/run/user/$(id -u <istanza-mariadb>) \
  podman cp mariadb-all.sql logplatform-mariadb:/tmp/restore.sql

sudo -u <istanza-mariadb> XDG_RUNTIME_DIR=/run/user/$(id -u <istanza-mariadb>) \
  podman exec -i logplatform-mariadb mariadb -u root -p'PASSWORD_ROOT' -e "source /tmp/restore.sql"
```

## 2. Ripristino MongoDB (configurazione Graylog)

```bash
sudo -u <istanza-mongodb> XDG_RUNTIME_DIR=/run/user/$(id -u <istanza-mongodb>) \
  podman cp mongodb-graylog.archive logplatform-mongodb:/tmp/restore.archive

sudo -u <istanza-mongodb> XDG_RUNTIME_DIR=/run/user/$(id -u <istanza-mongodb>) \
  podman exec logplatform-mongodb mongorestore --archive=/tmp/restore.archive --drop
```

## 3. Ripristino del volume CA (certificati)

**Attenzione**: questo sovrascrive la CA esistente. Se il modulo `auth`
sulla macchina di destinazione ha già generato una CA propria (diversa),
tutti i certificati agent già distribuiti smetteranno di funzionare
finché non li rigeneri — questo ripristino ha senso soprattutto quando
la macchina di destinazione è "vuota" (mai avuta una CA prima).

```bash
sudo -u <istanza-auth> XDG_RUNTIME_DIR=/run/user/$(id -u <istanza-auth>) \
  podman run --rm -v logplatform-auth-ca:/data -v /percorso/backup:/backup:ro \
  alpine sh -c "rm -rf /data/* && tar -xzf /backup/ca-volume.tar.gz -C /data"
```

## 4. Ripristino del volume archivi

```bash
sudo -u <istanza-auth> XDG_RUNTIME_DIR=/run/user/$(id -u <istanza-auth>) \
  podman run --rm -v logplatform-auth-archives:/data -v /percorso/backup:/backup:ro \
  alpine sh -c "rm -rf /data/* && tar -xzf /backup/archives-volume.tar.gz -C /data"
```

## 5. Riavvia tutti i moduli

```bash
for istanza in <istanza-mariadb> <istanza-mongodb> <istanza-auth> <istanza-graylog> <istanza-gate>; do
  sudo -u "$istanza" XDG_RUNTIME_DIR=/run/user/$(id -u "$istanza") \
    systemctl --user restart "logplatform-${istanza%[0-9]*}.service"
done
```
(verifica il nome esatto del servizio per ciascun modulo - potrebbe non
seguire esattamente questo pattern, controlla con `systemctl --user list-units` prima)

## 6. Verifica

- Login nel pannello funziona con le credenziali che avevi prima del
  backup (conferma che MariaDB è tornato)
- I clienti/stream configurati prima ci sono ancora (conferma MongoDB)
- Un pacchetto agent scaricato ora ha lo stesso certificato CA di prima,
  non uno nuovo (conferma il volume CA)
- Gli archivi caricati prima del backup sono ancora visibili nella
  scheda Archivi (conferma il volume archivi)

## Cosa NON viene ripristinato

- Gli indici OpenSearch (i log stessi) - il backup non li include per
  motivi di spazio, come documentato in `backup.sh`
- Eventuali modifiche fatte al codice sorgente stesso dopo il backup
  (quello vive nel repository Git, non in questo backup)
