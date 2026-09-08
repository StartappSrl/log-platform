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
# "Accept: application/json" e' fondamentale: senza, alcuni endpoint di
# ricerca (es. /api/search/universal/relative con 'fields' impostato)
# rispondono in text/csv invece che in JSON. Scoperto testando dal vivo
# contro un Graylog reale, non documentato in modo ovvio.
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
    'must not be empty, arg6') - scoperto testando dal vivo."""
    return _request("GET", "/api/search/universal/relative", params={
        "query": query or "*",
        "range": range_minutes * 60,
        "limit": limit,
        "streams": stream_id,
        "sort": "timestamp:desc",
        "fields": "timestamp,message,full_message,host",
    })


def list_event_definitions(stream_id: str | None = None) -> list:
    data = _request("GET", "/api/events/definitions")
    definitions = data.get("event_definitions", [])
    if stream_id:
        definitions = [d for d in definitions if stream_id in (d.get("config", {}).get("streams") or [])]
    return definitions


def create_notification(title: str, kind: str, target: str) -> dict:
    """kind: 'email' o 'webhook'. target: indirizzo email o URL webhook."""
    if kind == "email":
        config = {
            "type": "email-notification-v1",
            "sender": os.environ.get("GRAYLOG_ALERT_EMAIL_FROM", "alerts@logplatform.local"),
            "subject": "[Log Platform] Allarme: ${event_definition_title}",
            "body_template": "Allarme: ${event_definition_title}\\n${event.message}",
            "user_recipients": [],
            "email_recipients": [target],
            "time_zone": "Europe/Rome",
        }
    elif kind == "webhook":
        config = {
            "type": "http-notification-v1",
            "url": target,
            "headers": "",
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
    """Ritorna l'elenco software completo per un singolo host."""
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
