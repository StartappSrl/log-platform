#!/usr/bin/env bash
# Cifra un dispositivo a blocchi con LUKS e lo monta, per rispettare il
# requisito ISO 27001 "cifratura dei dati a riposo" sul volume che ospiterà
# i dati di Graylog/OpenSearch/MariaDB.
#
# Uso: sudo ./setup-encrypted-volume.sh /dev/sdX /mnt/graylog-data
set -euo pipefail

DEVICE="${1:?Uso: $0 /dev/sdX /percorso/mount}"
MOUNTPOINT="${2:?Uso: $0 /dev/sdX /percorso/mount}"
MAPPER_NAME="graylog_data_crypt"

if [[ $EUID -ne 0 ]]; then
  echo "Esegui come root (sudo)." >&2
  exit 1
fi

if ! command -v cryptsetup >/dev/null; then
  echo "Installo cryptsetup..."
  apt-get update -qq && apt-get install -y cryptsetup
fi

echo "ATTENZIONE: questo formatterà $DEVICE cancellando tutti i dati presenti."
read -rp "Confermi? (scrivi SI in maiuscolo): " CONFIRM
[[ "$CONFIRM" == "SI" ]] || { echo "Annullato."; exit 1; }

echo "Formattazione LUKS di $DEVICE..."
cryptsetup luksFormat "$DEVICE"

echo "Apertura del volume cifrato..."
cryptsetup open "$DEVICE" "$MAPPER_NAME"

echo "Creazione filesystem ext4..."
mkfs.ext4 "/dev/mapper/$MAPPER_NAME"

mkdir -p "$MOUNTPOINT"
mount "/dev/mapper/$MAPPER_NAME" "$MOUNTPOINT"

UUID=$(blkid -s UUID -o value "$DEVICE")
echo
echo "Volume montato su $MOUNTPOINT."
echo "Per montarlo automaticamente al boot, aggiungi a /etc/crypttab:"
echo "  $MAPPER_NAME UUID=$UUID none luks"
echo "e a /etc/fstab:"
echo "  /dev/mapper/$MAPPER_NAME  $MOUNTPOINT  ext4  defaults  0  2"
echo
echo "NB: con /etc/crypttab così configurato verrà chiesta la passphrase al boot,"
echo "    oppure serve una keyfile protetta (vedi documentazione cryptsetup) per l'unlock automatico."
