"""
Rotte di setup una tantum, protette da un token segreto (non dall'indirizzo
IP - vedi ca_manager.get_or_create_setup_token per il perche'): emissione
del certificato server per Graylog, lettura del certificato CA. Usate una
volta in fase di installazione per collegare l'input GELF TLS di Graylog
alla stessa CA che firma i certificati degli agent client.

Il token va recuperato con 'podman exec' (accesso diretto al container,
niente rete) e passato come header X-Setup-Token nelle chiamate a queste
rotte.
"""
from flask import Blueprint, jsonify, request

from . import ca_manager

setup_bp = Blueprint("setup", __name__, url_prefix="/_authgate/setup")


def _token_valid():
    provided = request.headers.get("X-Setup-Token", "")
    expected = ca_manager.get_or_create_setup_token()
    return provided and provided == expected


@setup_bp.get("/ca-cert")
def get_ca_cert():
    if not _token_valid():
        return jsonify(error="token di setup mancante o errato"), 403
    return jsonify(ca_pem=ca_manager.get_ca_cert_pem())


@setup_bp.post("/issue-server-cert")
def issue_server_cert():
    if not _token_valid():
        return jsonify(error="token di setup mancante o errato"), 403
    data = request.get_json(force=True, silent=True) or {}
    common_name = data.get("common_name")
    dns_names = data.get("dns_names") or [common_name]
    if not common_name:
        return jsonify(error="common_name mancante"), 400

    cert_pem, key_pem = ca_manager.issue_certificate(common_name, is_server=True, dns_names=dns_names)
    return jsonify(cert_pem=cert_pem, key_pem=key_pem, ca_pem=ca_manager.get_ca_cert_pem())
