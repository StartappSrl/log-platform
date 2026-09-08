"""
Rotte di setup una tantum, raggiungibili SOLO dal nodo stesso (non tramite
gate/Traefik dall'esterno): emissione del certificato server per Graylog,
lettura del certificato CA. Usate una volta in fase di installazione per
collegare l'input GELF TLS di Graylog alla stessa CA che firma i
certificati degli agent client.

Protezione: nessun login richiesto (per semplicità d'uso da curl in fase
di setup), ma la richiesta DEVE arrivare da 127.0.0.1/::1 - cioè da chi ha
già accesso alla shell del nodo. Una richiesta proxata da gate (che passa
dalla rete slirp4netns) non arriva mai da questo indirizzo, quindi non è
raggiungibile dall'esterno nemmeno per errore di instradamento.
"""
from flask import Blueprint, jsonify, request

from . import ca_manager

setup_bp = Blueprint("setup", __name__, url_prefix="/_authgate/setup")


def _local_only():
    return request.remote_addr in ("127.0.0.1", "::1")


@setup_bp.get("/ca-cert")
def get_ca_cert():
    if not _local_only():
        return jsonify(error="raggiungibile solo dal nodo stesso"), 403
    return jsonify(ca_pem=ca_manager.get_ca_cert_pem())


@setup_bp.post("/issue-server-cert")
def issue_server_cert():
    if not _local_only():
        return jsonify(error="raggiungibile solo dal nodo stesso"), 403
    data = request.get_json(force=True, silent=True) or {}
    common_name = data.get("common_name")
    dns_names = data.get("dns_names") or [common_name]
    if not common_name:
        return jsonify(error="common_name mancante"), 400

    cert_pem, key_pem = ca_manager.issue_certificate(common_name, is_server=True, dns_names=dns_names)
    return jsonify(cert_pem=cert_pem, key_pem=key_pem, ca_pem=ca_manager.get_ca_cert_pem())
