"""
Client per l'API REST di Graylog, usato dal pannello semplificato al posto
della UI nativa di Graylog. Le credenziali (GRAYLOG_API_USER/PASSWORD) sono
lato server: gli utenti finali non le vedono mai.
"""
import json
import os
import requests

GRAYLOG_URL = os.environ.get("GRAYLOG_URL", "http://graylog:9000")
GRAYLOG_API_USER = os.environ.get("GRAYLOG_API_USER", "admin")
GRAYLOG_API_PASSWORD = os.environ.get("GRAYLOG_API_PASSWORD", "")

_AUTH = (GRAYLOG_API_USER, GRAYLOG_API_PASSWORD)
_HDRS = {"Content-Type": "application/json", "Accept": "application/json",
         "X-Requested-By": "logplatform-dashboard"}


class GraylogError(Exception):
    pass


def _request(method: str, path: str, **kwargs) -> dict:
    url = f"{GRAYLOG_URL}{path}"
    try:
        resp = requests.request(method, url, auth=_AUTH, headers=_HDRS, timeout=15, **kwargs)
    except requests.exceptions.RequestException as e:
        raise GraylogError(f"{method} {path} -> impossibile raggiungere Graylog: {e}")
    if not resp.ok:
        raise GraylogError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
    if resp.content:
        try:
            return resp.json()
        except ValueError:
            return {}
    return {}


def create_tenant_stream(tenant: str, retention_days: int = 90) -> dict:
    """Crea index set + stream + regola di instradamento per un nuovo tenant.
    Equivalente a scripts/provision-tenant.sh, richiamabile via API."""
    index_set = _request("POST", "/api/system/indices/index_sets", json={
        "title": f"tenant-{tenant}",
        "description": f"Log del tenant {tenant}",
        "index_prefix": f"tenant-{tenant}",
        "rotation_strategy_class": "org.graylog2.indexer.rotation.strategies.TimeBasedRotationStrategy",
        "rotation_strategy": {"type": "org.graylog2.indexer.rotation.strategies.TimeBasedRotationStrategyConfig", "rotation_period": "P1D"},
        "retention_strategy_class": "org.graylog2.indexer.retention.strategies.DeletionRetentionStrategy",
        "retention_strategy": {"type": "org.graylog2.indexer.retention.strategies.DeletionRetentionStrategyConfig", "max_number_of_indices": retention_days},
        "index_analyzer": "standard",
        "shards": 1,
        "replicas": 0,
        "index_optimization_max_num_segments": 1,
        "index_optimization_disabled": False,
        "field_type_refresh_interval": 5000,
        "writable": True,
    })
    index_set_id = index_set["id"]

    stream = _request("POST", "/api/streams", json={
        "title": f"tenant-{tenant}",
        "description": f"Stream log tenant {tenant}",
        "index_set_id": index_set_id,
        "matching_type": "AND",
        "remove_matches_from_default_stream": True,
    })
    stream_id = stream["stream_id"]

    _request("POST", f"/api/streams/{stream_id}/rules", json={
        "field": "tenant", "type": 1, "value": tenant, "inverted": False,
    })
    _request("POST", f"/api/streams/{stream_id}/resume")

    return {"stream_id": stream_id, "index_set_id": index_set_id}


def search(stream_id: str, query: str = "*", range_minutes: int = 60, limit: int = 150) -> dict:
    """Ricerca semplice, ultimi N minuti, scoperta al singolo stream (tenant).
    'fields' e' obbligatorio per questo endpoint (altrimenti risponde 400
    'must not be empty, arg6'). Il campo che identifica l'host che ha
    inviato il messaggio si chiama 'source' nella risposta di Graylog, NON
    'host' (che pure e' il nome del campo GELF originale) - scoperto
    testando dal vivo, entrambe le cose non erano ovvie/documentate in
    modo chiaro.

    IMPORTANTE: il filtro per stream su questo endpoint NON si passa con
    un parametro 'streams' (viene ignorato silenziosamente, senza errore
    - bug serio scoperto testando dal vivo con due tenant reali: le
    ricerche restituivano i dati di TUTTI i tenant, non solo di quello
    richiesto). Il parametro giusto e' 'filter=streams:<ID>'."""
    return _request("GET", "/api/search/universal/relative", params={
        "query": query or "*",
        "range": range_minutes * 60,
        "limit": limit,
        "filter": f"streams:{stream_id}",
        "sort": "timestamp:desc",
        "fields": "timestamp,message,full_message,source",
    })


def list_event_definitions(stream_id: str | None = None) -> list:
    data = _request("GET", "/api/events/definitions")
    definitions = data.get("event_definitions", [])
    if stream_id:
        definitions = [d for d in definitions if stream_id in (d.get("config", {}).get("streams") or [])]
    return definitions


def create_notification(title: str, kind: str, target: str) -> dict:
    """kind: 'email' o 'webhook'. target: indirizzo email o URL webhook.

    'email' NON usa il transport nativo di Graylog (richiederebbe una
    configurazione server separata in graylog.conf, non fatta qui) -
    usa invece una notifica HTTP verso un nostro webhook, che manda
    l'email con il sistema SMTP gia' configurato in Impostazioni."""
    if kind == "email":
        base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
        webhook_secret = os.environ.get("GRAYLOG_WEBHOOK_SECRET", "")
        if not base_url or not webhook_secret:
            raise GraylogError(
                "PUBLIC_BASE_URL o GRAYLOG_WEBHOOK_SECRET non configurate lato server - "
                "servono per instradare le notifiche email attraverso il nostro webhook."
            )
        webhook_url = f"{base_url}/_authgate/dashboard/webhook/graylog-notification?to={target}&secret={webhook_secret}"
        config = {
            "type": "http-notification-v1",
            "url": webhook_url,
            "api_key": "", "api_secret": "", "basic_auth": "",
            "skip_tls_verification": False,
        }
    elif kind == "webhook":
        config = {
            "type": "http-notification-v1",
            "url": target,
            "api_key": "", "api_secret": "", "basic_auth": "",
            "skip_tls_verification": False,
        }
    else:
        raise ValueError("kind deve essere 'email' o 'webhook'")

    return _request("POST", "/api/events/notifications", json={
        "title": title, "description": f"Creata dal pannello Log Platform ({kind})", "config": config,
    })


def list_notifications() -> list:
    return _request("GET", "/api/events/notifications").get("notifications", [])


def create_alert(title: str, stream_id: str, query: str, threshold: int,
                  window_minutes: int, notification_ids: list) -> dict:
    """Allarme semplificato."""
    series_id = "count-messages"
    return _request("POST", "/api/events/definitions", json={
        "title": title,
        "description": "Creato dal pannello Log Platform",
        "priority": 2,
        "alert": True,
        "config": {
            "type": "aggregation-v1",
            "query": query or "",
            "streams": [stream_id],
            "group_by": [],
            "series": [
                {"id": series_id, "function": "count", "field": None}
            ],
            "conditions": {
                "expression": {
                    "expr": ">=",
                    "left": {"expr": "number-ref", "ref": series_id},
                    "right": {"expr": "number", "value": threshold},
                }
            },
            "search_within_ms": window_minutes * 60 * 1000,
            "execute_every_ms": 60000,
        },
        "field_spec": {},
        "key_spec": [],
        "notification_settings": {"grace_period_ms": 300000, "backlog_size": 10},
        "notifications": [{"notification_id": nid} for nid in notification_ids],
    })


def get_latest_inventory_per_host(stream_id: str, range_hours: int = 192, limit: int = 500) -> list:
    """Ritorna l'ultimo inventario ricevuto per ciascun host del tenant."""
    result = search(stream_id, query="log_class:inventory", range_minutes=range_hours * 60, limit=limit)
    messages = result.get("messages", [])

    latest_by_host = {}
    for entry in messages:
        m = entry.get("message", entry)
        host = m.get("source")
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
                "pending_updates": inv.get("pending_updates") or [],
            }
    return sorted(latest_by_host.values(), key=lambda x: x["hostname"])


def get_inventory_software_for_host(stream_id: str, hostname: str, range_hours: int = 192) -> list:
    """Ritorna l'elenco software completo per un singolo host."""
    query = f'log_class:inventory AND source:"{hostname}"'
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


def get_full_inventory_for_host(stream_id: str, hostname: str, range_hours: int = 192) -> dict:
    """Ritorna l'inventario COMPLETO (non solo il software) per un singolo
    host - usato per l'export PDF di un endpoint."""
    query = f'log_class:inventory AND source:"{hostname}"'
    result = search(stream_id, query=query, range_minutes=range_hours * 60, limit=1)
    messages = result.get("messages", [])
    if not messages:
        return {}
    m = messages[0].get("message", messages[0])
    try:
        return json.loads(m.get("full_message", "{}"))
    except (ValueError, TypeError):
        return {}

from datetime import datetime, timezone, timedelta

OFFLINE_THRESHOLD_MINUTES = 20


def get_device_dashboard_for_tenant(stream_id: str, range_minutes: int = 1440) -> dict:
    """Panoramica dispositivi per un tenant: stato online/offline (nessun
    log negli ultimi OFFLINE_THRESHOLD_MINUTES minuti) e conteggi per
    gravita' (basati sul livello GELF: <=3 errore, 4 avviso, resto normale).

    Limite onesto: la classificazione per gravita' e' accurata per i log
    che arrivano gia' con un livello reale (es. Windows Event Log); i file
    di log generici su Linux, se l'agent non analizza il testo, arrivano
    tutti con livello di default (informativo) - vedi inventory.py e
    agent.py per dove eventualmente estendere questo in futuro."""
    result = _request("GET", "/api/search/universal/relative", params={
        "query": "*",
        "range": range_minutes * 60,
        "limit": 5000,
        "filter": f"streams:{stream_id}",
        "sort": "timestamp:desc",
        "fields": "timestamp,source,level",
    })
    messages = result.get("messages", [])

    now = datetime.now(timezone.utc)
    hosts = {}
    total_today = 0

    for entry in messages:
        m = entry.get("message", entry)
        host = m.get("source")
        if not host:
            continue
        total_today += 1

        ts_str = m.get("timestamp", "")
        level = m.get("level")

        if host not in hosts:
            hosts[host] = {"hostname": host, "total": 0, "errors": 0,
                            "warnings": 0, "ok": 0, "last_seen": ts_str}
        h = hosts[host]
        h["total"] += 1

        try:
            level_num = int(level) if level is not None else 6
        except (TypeError, ValueError):
            level_num = 6

        if level_num <= 3:
            h["errors"] += 1
        elif level_num == 4:
            h["warnings"] += 1
        else:
            h["ok"] += 1

        # i messaggi arrivano ordinati dal piu' recente (sort=timestamp:desc),
        # quindi il primo che vediamo per ogni host e' gia' il piu' recente -
        # non serve confrontare, basta non sovrascriverlo con uno successivo
        if "last_seen_set" not in h:
            h["last_seen"] = ts_str
            h["last_seen_set"] = True

    device_list = []
    for h in hosts.values():
        h.pop("last_seen_set", None)
        online = False
        if h["last_seen"]:
            try:
                last_seen_dt = datetime.fromisoformat(h["last_seen"].replace("Z", "+00:00"))
                online = (now - last_seen_dt) < timedelta(minutes=OFFLINE_THRESHOLD_MINUTES)
            except ValueError:
                pass
        h["online"] = online
        device_list.append(h)

    # Spazio disco: preso dall'inventario piu' recente (raccolto
    # separatamente dai log), non dai messaggi di log stessi - un
    # fallimento qui non deve far fallire tutta la dashboard.
    try:
        inventory_by_host = {inv["hostname"]: inv for inv in get_latest_inventory_per_host(stream_id)}
    except Exception:
        inventory_by_host = {}

    for d in device_list:
        d["disk_percent"] = None
        inv = inventory_by_host.get(d["hostname"])
        if inv and inv.get("disks"):
            percentages = []
            for disk in inv["disks"]:
                total = disk.get("total_gb") or 0
                used = disk.get("used_gb") or 0
                if total > 0:
                    percentages.append(round(used / total * 100))
            if percentages:
                # Il disco PIU' pieno, non la media - un secondo disco
                # quasi esaurito e' un problema tanto quanto il primo.
                d["disk_percent"] = max(percentages)

    return {
        "devices": sorted(device_list, key=lambda x: x["hostname"]),
        "total_messages_today": total_today,
    }

def delete_stream_and_index(stream_id: str, index_set_id: str) -> None:
    """Cancella lo stream e l'index set di un tenant da Graylog - lo
    stream va fermato PRIMA di essere cancellato (Graylog lo richiede),
    l'index set va cancellato CON i suoi dati (delete_indices=true),
    altrimenti resterebbero indici orfani sul disco."""
    try:
        _request("POST", f"/api/streams/{stream_id}/pause")
    except GraylogError:
        pass  # se e' gia' in pausa o non esiste piu', non e' un problema bloccante
    _request("DELETE", f"/api/streams/{stream_id}")
    _request("DELETE", f"/api/system/indices/index_sets/{index_set_id}?delete_indices=true")

def delete_notification(notification_id: str) -> None:
    _request("DELETE", f"/api/events/notifications/{notification_id}")

def get_index_set_storage_bytes(index_set_id: str) -> int | None:
    """Spazio occupato in OpenSearch dall'indice di un tenant, in byte -
    dato preciso (non una stima), a differenza di una ripartizione per
    singolo dispositivo che invece si potrebbe solo stimare.

    ATTENZIONE - non verificato dal vivo: non ho un Graylog reale a
    disposizione per confermare la forma esatta della risposta di
    questo endpoint. Prova diversi nomi di campo plausibili (varianti
    viste in versioni diverse di Graylog) prima di arrendersi, ma va
    controllato/corretto contro la tua installazione reale - stesso
    tipo di correzione gia' fatta insieme per le notifiche."""
    try:
        result = _request("GET", f"/api/system/indices/index_sets/{index_set_id}/stats")
    except GraylogError:
        return None

    for field_name in ("size", "store_size", "size_in_bytes", "total_size"):
        if field_name in result and isinstance(result[field_name], (int, float)):
            return int(result[field_name])

    # Alcune versioni annidano il dato sotto 'indices' come somma da fare
    indices = result.get("indices")
    if isinstance(indices, dict):
        total = 0
        found_any = False
        for idx_stats in indices.values():
            for field_name in ("size", "store_size", "size_in_bytes"):
                if isinstance(idx_stats, dict) and field_name in idx_stats:
                    total += idx_stats[field_name]
                    found_any = True
                    break
        if found_any:
            return total

    return None


def format_bytes_human(num_bytes: int) -> str:
    """Formatta byte in una stringa leggibile (KB/MB/GB/TB)."""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
        value /= 1024
    return f"{value:.1f} TB"
