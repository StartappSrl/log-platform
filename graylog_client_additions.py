# --- Da aggiungere a app/graylog_client.py (in fondo al file) ---
import json


def get_latest_inventory_per_host(stream_id: str, range_hours: int = 192, limit: int = 500) -> list:
    """Ritorna l'ultimo inventario ricevuto per ciascun host del tenant
    (uno per macchina, non uno per messaggio: se una macchina ha inviato
    più inventari nel periodo, tiene solo il più recente)."""
    result = search(stream_id, query="log_class:inventory", range_minutes=range_hours * 60, limit=limit)
    messages = result.get("messages", [])

    latest_by_host = {}
    for entry in messages:
        m = entry.get("message", entry)
        host = m.get("host")
        ts = m.get("timestamp", "")
        if not host:
            continue
        if host not in latest_by_host or ts > latest_by_host[host]["timestamp"]:
            try:
                inv = json.loads(m.get("full_message", "{}"))
            except (ValueError, TypeError):
                inv = {}
            latest_by_host[host] = {
                "hostname": host,
                "timestamp": ts,
                "os": inv.get("os"),
                "cpu": inv.get("cpu"),
                "memory": inv.get("memory"),
                "disks": inv.get("disks"),
                "network": inv.get("network"),
                "software_count": inv.get("software_count"),
            }
    return sorted(latest_by_host.values(), key=lambda x: x["hostname"])


def get_inventory_software_for_host(stream_id: str, hostname: str, range_hours: int = 192) -> list:
    """Ritorna l'elenco software completo per un singolo host (rotta
    separata dalla vista d'insieme, per non appesantirla con centinaia di
    pacchetti per ogni macchina)."""
    query = f'log_class:inventory AND host:"{hostname}"'
    result = search(stream_id, query=query, range_minutes=range_hours * 60, limit=1)
    messages = result.get("messages", [])
    if not messages:
        return []
    m = messages[0].get("message", messages[0])
    try:
        inv = json.loads(m.get("full_message", "{}"))
    except (ValueError, TypeError):
        return []
    return inv.get("software", [])
