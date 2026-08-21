#!/usr/bin/env bash
# Lifecycle "install" del modulo NS8 logplatform — SCAFFOLD NON VALIDATO.
#
# Idea generale (da adattare alla versione NS8 reale):
# 1. NS8 chiama questo script fornendo variabili d'ambiente per l'istanza
#    (nome modulo, path dati, ecc. — vedi doc "Develop a module").
# 2. Copiamo i container definiti nello stack standalone (../../final-package)
#    come systemd unit rootless Podman, sotto l'utente di sistema del modulo.
# 3. Registriamo le route HTTP presso il Traefik centrale di NS8 al posto
#    del blocco nginx dedicato usato nel deploy standalone.
set -euo pipefail

MODULE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${DATA_DIR:-/home/logplatform}"
SOURCE_STACK="${MODULE_DIR}/../../final-package"

echo "Copio i sorgenti dello stack in ${DATA_DIR}..."
mkdir -p "$DATA_DIR"
cp -r "$SOURCE_STACK"/{agent,auth-service,ai-service,nginx,scripts} "$DATA_DIR"/ 2>/dev/null || true
mkdir -p "$DATA_DIR/ca"

echo "Genero le unit systemd per i container (podman generate systemd)..."
echo "NB: da eseguire DOPO aver creato i container con 'podman run' o 'podman-compose',"
echo "    oppure sostituendo con quadlet (.container files) se la tua versione NS8 li supporta."

cat <<'EOF'
--------------------------------------------------------------------
Passi manuali ancora da automatizzare in questo scaffold:
  1. Creare la rete podman dedicata al modulo.
  2. Avviare mongodb/opensearch/mariadb/graylog/auth-service/ai-service
     come container podman con nomi prevedibili (es. logplatform-graylog).
  3. Registrare le route presso il Traefik di NS8 (invece del blocco nginx
     usato nel deploy standalone) puntando a auth-service (per il gate MFA)
     e a graylog per l'interfaccia, con lo stesso auth_request pattern.
  4. Abilitare le unit con `systemctl --user enable --now <unit>`.
--------------------------------------------------------------------
EOF
