"""
Rotta chiamata dagli AGENT (non dal browser) per caricare l'archivio
sigillato di un giorno sul portale, invece di lasciarlo solo sul PC del
cliente. Autenticata con un token per-tenant (non il cookie di sessione
del pannello, dato che qui non c'e' un utente loggato).
"""
from flask import Blueprint, jsonify, request

from . import archive_storage

archive_upload_bp = Blueprint("archive_upload", __name__, url_prefix="/_authgate/archive-upload")


@archive_upload_bp.post("")
def upload_archive():
    tenant = request.headers.get("X-Tenant", "")
    token = request.headers.get("X-Upload-Token", "")
    hostname = request.headers.get("X-Hostname", "")
    source_id = request.headers.get("X-Source-Id", "")
    day = request.headers.get("X-Day", "")
    sha256 = request.headers.get("X-Sha256", "")
    chain_hash = request.headers.get("X-Chain-Hash", "")

    if not all([tenant, hostname, source_id, day]):
        return jsonify(error="header mancanti (X-Tenant/X-Hostname/X-Source-Id/X-Day)"), 400

    if not archive_storage.verify_upload_token(tenant, token):
        return jsonify(error="token non valido"), 403

    gz_bytes = request.files["archive"].read() if "archive" in request.files else request.data
    tsr_bytes = request.files["tsr"].read() if "tsr" in request.files else None

    if not gz_bytes:
        return jsonify(error="corpo della richiesta vuoto"), 400

    archive_storage.store_uploaded_day(
        tenant, hostname, source_id, day, gz_bytes, sha256, chain_hash, tsr_bytes=tsr_bytes,
    )
    return jsonify(ok=True)
