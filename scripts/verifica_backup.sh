#!/usr/bin/env bash
# Verifica NON DISTRUTTIVA dell'ultimo backup: non ripristina nulla, non
# tocca i dati in produzione - controlla solo che i file esistano, non
# siano vuoti/troncati, e che il contenuto sia quello atteso.
#
# NON sostituisce una vera prova di ripristino su una macchina pulita
# (vedi CHECKLIST-VERIFICA-PRE-PRODUZIONE.md): dice che il backup e'
# integro e recente, non che il ripristino funzioni.
#
# Uso: ./verifica_backup.sh [cartella-backup]
#   (default: ultima sottocartella di /var/lib/logplatform-backups)
# Codice di uscita: 0 = tutto ok, 1 = almeno un controllo fallito.

set -uo pipefail

BACKUP_ROOT="${BACKUP_DIR:-/var/lib/logplatform-backups}"
MAX_AGE_HOURS="${MAX_BACKUP_AGE_HOURS:-48}"

if [[ $# -ge 1 ]]; then
    TARGET="$1"
else
    TARGET=$(ls -td "${BACKUP_ROOT}"/*/ 2>/dev/null | head -1)
    TARGET="${TARGET%/}"
fi

FAILS=0
WARNS=0
ok()   { echo "  [OK]    $1"; }
fail() { echo "  [FALLITO] $1"; FAILS=$((FAILS + 1)); }
warn() { echo "  [ATTENZIONE] $1"; WARNS=$((WARNS + 1)); }

if [[ -z "$TARGET" || ! -d "$TARGET" ]]; then
    echo "Nessun backup trovato in ${BACKUP_ROOT}"
    exit 1
fi
echo "Verifica di: $TARGET"
echo

# --- Eta' del backup (rileva un cron che non gira piu') ---
NOW=$(date +%s)
MTIME=$(stat -c %Y "$TARGET")
AGE_H=$(( (NOW - MTIME) / 3600 ))
if [[ $AGE_H -gt $MAX_AGE_HOURS ]]; then
    fail "backup vecchio di ${AGE_H} ore (soglia ${MAX_AGE_HOURS}h): il backup notturno gira ancora?"
else
    ok "backup recente (${AGE_H} ore fa)"
fi

# --- MariaDB ---
SQL="$TARGET/mariadb-all.sql"
if [[ ! -s "$SQL" ]]; then
    fail "mariadb-all.sql mancante o vuoto"
else
    ok "mariadb-all.sql presente ($(du -h "$SQL" | cut -f1))"
    LAST=$(tail -n 1 "$SQL")
    if [[ "$LAST" == *"Dump completed"* ]]; then
        ok "dump MariaDB completo (riga finale di chiusura presente)"
    else
        warn "il dump non termina con 'Dump completed' - potrebbe essere troncato. Ultima riga: ${LAST:0:80}"
    fi
    for tabella in users tenants smtp_settings audit_log; do
        if grep -q "CREATE TABLE \`$tabella\`" "$SQL"; then
            ok "tabella '$tabella' presente nel dump"
        else
            fail "tabella '$tabella' NON trovata nel dump"
        fi
    done
fi

# --- MongoDB (configurazione Graylog) ---
MONGO="$TARGET/mongodb-graylog.archive"
if [[ ! -s "$MONGO" ]]; then
    fail "mongodb-graylog.archive mancante o vuoto"
elif grep -a -q "graylog" "$MONGO"; then
    ok "mongodb-graylog.archive presente ($(du -h "$MONGO" | cut -f1)) e contiene il database graylog"
else
    fail "mongodb-graylog.archive non sembra contenere il database graylog"
fi

# --- Volume CA ---
CA="$TARGET/ca-volume.tar.gz"
if [[ ! -s "$CA" ]]; then
    fail "ca-volume.tar.gz mancante o vuoto"
elif ! tar -tzf "$CA" >/dev/null 2>&1; then
    fail "ca-volume.tar.gz corrotto (tar non riesce a leggerlo)"
else
    ELENCO=$(tar -tzf "$CA")
    for f in ca-key.pem ca.pem certs.db; do
        if echo "$ELENCO" | grep -q "$f"; then
            ok "volume CA contiene $f"
        else
            fail "volume CA NON contiene $f (senza la chiave della CA i certificati non sono ripristinabili)"
        fi
    done
fi

# --- Volume archivi ---
ARC="$TARGET/archives-volume.tar.gz"
if [[ ! -s "$ARC" ]]; then
    fail "archives-volume.tar.gz mancante o vuoto"
elif ! tar -tzf "$ARC" >/dev/null 2>&1; then
    fail "archives-volume.tar.gz corrotto (tar non riesce a leggerlo)"
elif tar -tzf "$ARC" | grep -q "archive_ledger.db"; then
    ok "volume archivi integro e contiene il registro (archive_ledger.db)"
else
    fail "volume archivi NON contiene archive_ledger.db"
fi

# --- Stato dei moduli NS8 ---
if [[ -d "$TARGET/module-state" ]] && [[ -n "$(ls -A "$TARGET/module-state" 2>/dev/null)" ]]; then
    ok "configurazione moduli NS8 presente ($(ls "$TARGET/module-state" | wc -l) moduli)"
else
    warn "module-state assente o vuoto"
fi

# --- Permessi (il backup contiene hash di password e la chiave della CA) ---
PERM=$(stat -c %a "$TARGET")
if [[ "$PERM" == "700" ]]; then
    ok "permessi della cartella corretti (700)"
else
    warn "permessi della cartella ${PERM}: dovrebbero essere 700 (il backup contiene la chiave privata della CA)"
fi

echo
if [[ $FAILS -eq 0 ]]; then
    echo "ESITO: backup integro (${WARNS} avvisi)."
    exit 0
else
    echo "ESITO: ${FAILS} controlli FALLITI (${WARNS} avvisi) - NON fidarti di questo backup."
    exit 1
fi
