"""
Pannello semplificato: gestione clienti/tenant, ricerca log, allarmi,
notifiche — senza passare dalla UI nativa di Graylog. Ogni rotta applica
automaticamente lo scoping per tenant: un utente non-admin (tenant != None)
vede e agisce SOLO sul proprio tenant.
"""
from functools import wraps
from .csrf import csrf_protect, generate_csrf_token

from flask import Blueprint, jsonify, request, g

from .models import db, Tenant, User
from .auth_utils import get_current_user
from . import graylog_client as gl

dash = Blueprint("dashboard", __name__, url_prefix="/_authgate/dashboard")


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify(error="non autenticato"), 401
        g.current_user = user
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if g.current_user.tenant:  # un utente con tenant impostato non è admin globale
            return jsonify(error="richiede accesso amministratore"), 403
        return fn(*args, **kwargs)
    return wrapper


def _tenant_or_403(tenant_name: str):
    """Un utente tenant può operare solo sul proprio; l'admin su qualunque."""
    if g.current_user.tenant and g.current_user.tenant != tenant_name:
        return None
    return Tenant.query.filter_by(name=tenant_name).first()


@dash.get("/whoami")
@login_required
def whoami():
    return jsonify(username=g.current_user.username, tenant=g.current_user.tenant,
                    is_admin=not g.current_user.tenant)


# --- Gestione clienti/tenant (solo admin) ---

@dash.get("/tenants")
@admin_required
def list_tenants():
    tenants = Tenant.query.order_by(Tenant.name).all()
    return jsonify([{"name": t.name, "display_name": t.display_name,
                      "stream_id": t.graylog_stream_id} for t in tenants])


@dash.post("/tenants")
@admin_required
@csrf_protect
def create_tenant_route():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip().lower()
    display_name = data.get("display_name") or name
    retention_days = int(data.get("retention_days") or 90)

    if not name or not name.replace("-", "").isalnum():
        return jsonify(error="nome tenant non valido (solo lettere, numeri, trattini)"), 400
    if Tenant.query.filter_by(name=name).first():
        return jsonify(error="tenant già esistente"), 409

    try:
        result = gl.create_tenant_stream(name, retention_days=retention_days)
    except gl.GraylogError as e:
        return jsonify(error=f"errore Graylog: {e}"), 502

    t = Tenant(name=name, display_name=display_name,
               graylog_stream_id=result["stream_id"], graylog_index_set_id=result["index_set_id"])
    db.session.add(t)
    db.session.commit()
    return jsonify(name=t.name, display_name=t.display_name, stream_id=t.graylog_stream_id), 201


# --- Ricerca log ---

@dash.get("/search")
@login_required
def search_route():
    tenant_name = request.args.get("tenant") or g.current_user.tenant
    if not tenant_name:
        return jsonify(error="specifica un tenant (parametro 'tenant')"), 400

    t = _tenant_or_403(tenant_name)
    if not t:
        return jsonify(error="tenant non trovato o non autorizzato"), 403

    query = request.args.get("q", "*")
    range_minutes = int(request.args.get("range_minutes", 60))
    limit = min(int(request.args.get("limit", 150)), 500)

    try:
        result = gl.search(t.graylog_stream_id, query=query, range_minutes=range_minutes, limit=limit)
    except gl.GraylogError as e:
        return jsonify(error=f"errore Graylog: {e}"), 502

    messages = [m["message"] for m in result.get("messages", [])]
    return jsonify(total=result.get("total_results", len(messages)), messages=messages)


# --- Allarmi ---

@dash.get("/alarms")
@login_required
def list_alarms_route():
    tenant_name = request.args.get("tenant") or g.current_user.tenant
    if not tenant_name:
        return jsonify(error="specifica un tenant"), 400
    t = _tenant_or_403(tenant_name)
    if not t:
        return jsonify(error="tenant non trovato o non autorizzato"), 403

    try:
        alarms = gl.list_event_definitions(stream_id=t.graylog_stream_id)
    except gl.GraylogError as e:
        return jsonify(error=f"errore Graylog: {e}"), 502
    return jsonify(alarms)


@dash.post("/alarms")
@login_required
@csrf_protect
def create_alarm_route():
    data = request.get_json(force=True, silent=True) or {}
    tenant_name = data.get("tenant") or g.current_user.tenant
    t = _tenant_or_403(tenant_name)
    if not t:
        return jsonify(error="tenant non trovato o non autorizzato"), 403

    title = data.get("title")
    query = data.get("query", "")
    threshold = int(data.get("threshold", 1))
    window_minutes = int(data.get("window_minutes", 5))
    notification_ids = data.get("notification_ids", [])

    if not title:
        return jsonify(error="titolo mancante"), 400

    try:
        result = gl.create_alert(title, t.graylog_stream_id, query, threshold, window_minutes, notification_ids)
    except gl.GraylogError as e:
        return jsonify(error=f"errore Graylog: {e}"), 502
    return jsonify(result), 201


# --- Notifiche (email/webhook) ---
# Nota: le notifiche create qui sono globali lato Graylog (non hanno un
# concetto nativo di tenant); le associ a un allarme specifico quando crei
# l'allarme (notification_ids). Un utente tenant può crearne di nuove ma
# non vede quelle create da altri tenant/dall'admin.

@dash.get("/notifications")
@login_required
def list_notifications_route():
    try:
        notifications = gl.list_notifications()
    except gl.GraylogError as e:
        return jsonify(error=f"errore Graylog: {e}"), 502
    # Filtro basico per non-admin: mostra solo quelle il cui titolo inizia
    # con il nome del tenant (convenzione applicata in create_notification_route).
    if g.current_user.tenant:
        notifications = [n for n in notifications if n.get("title", "").startswith(f"[{g.current_user.tenant}]")]
    return jsonify(notifications)


@dash.post("/notifications")
@login_required
@csrf_protect
def create_notification_route():
    data = request.get_json(force=True, silent=True) or {}
    tenant_name = data.get("tenant") or g.current_user.tenant
    if g.current_user.tenant and g.current_user.tenant != tenant_name:
        return jsonify(error="non autorizzato per questo tenant"), 403

    kind = data.get("kind")  # 'email' o 'webhook'
    target = data.get("target")
    title_suffix = data.get("title", kind)
    if kind not in ("email", "webhook") or not target:
        return jsonify(error="parametri mancanti: kind ('email'/'webhook') e target"), 400

    title = f"[{tenant_name}] {title_suffix}"
    try:
        result = gl.create_notification(title, kind, target)
    except gl.GraylogError as e:
        return jsonify(error=f"errore Graylog: {e}"), 502
    return jsonify(result), 201
# --- Da aggiungere a app/dashboard.py (in fondo al file) ---

@dash.get("/inventory")
@login_required
def list_inventory_route():
    tenant_name = request.args.get("tenant") or g.current_user.tenant
    if not tenant_name:
        return jsonify(error="specifica un tenant"), 400
    t = _tenant_or_403(tenant_name)
    if not t:
        return jsonify(error="tenant non trovato o non autorizzato"), 403

    try:
        hosts = gl.get_latest_inventory_per_host(t.graylog_stream_id)
    except gl.GraylogError as e:
        return jsonify(error=f"errore Graylog: {e}"), 502
    return jsonify(hosts)


@dash.get("/inventory/software")
@login_required
def inventory_software_route():
    tenant_name = request.args.get("tenant") or g.current_user.tenant
    hostname = request.args.get("hostname")
    if not tenant_name or not hostname:
        return jsonify(error="specifica tenant e hostname"), 400
    t = _tenant_or_403(tenant_name)
    if not t:
        return jsonify(error="tenant non trovato o non autorizzato"), 403

    try:
        software = gl.get_inventory_software_for_host(t.graylog_stream_id, hostname)
    except gl.GraylogError as e:
        return jsonify(error=f"errore Graylog: {e}"), 502
    return jsonify(software)
# --- Da aggiungere a app/dashboard.py (nuove rotte, in fondo al file) ---

from flask import send_file
import io
from . import agent_packager


@dash.get("/tenants/<tenant_name>/agent-package")
@login_required
def download_agent_package(tenant_name):
    t = _tenant_or_403(tenant_name)
    if not t:
        return jsonify(error="tenant non trovato o non autorizzato"), 403

    try:
        package_bytes = agent_packager.build_agent_package(tenant_name)
    except FileNotFoundError as e:
        return jsonify(error=f"file template agent mancante nell'immagine: {e}"), 500
    except Exception as e:
        return jsonify(error=f"errore nella generazione del pacchetto: {e}"), 500

    return send_file(
        io.BytesIO(package_bytes),
        mimetype="application/gzip",
        as_attachment=True,
        download_name=f"agent-{tenant_name}.tar.gz",
    )
# --- Da aggiungere a app/dashboard.py (in fondo al file) ---

from . import github_release


@dash.get("/tenants/<tenant_name>/agent-package-windows")
@login_required
def download_agent_package_windows(tenant_name):
    t = _tenant_or_403(tenant_name)
    if not t:
        return jsonify(error="tenant non trovato o non autorizzato"), 403

    try:
        package_bytes = agent_packager.build_agent_package_windows_exe(tenant_name)
    except github_release.GitHubReleaseError as e:
        return jsonify(error=f"errore nel recupero dell'eseguibile da GitHub: {e}"), 502
    except Exception as e:
        return jsonify(error=f"errore nella generazione del pacchetto: {e}"), 500

    return send_file(
        io.BytesIO(package_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"agent-{tenant_name}-windows.zip",
    )
# --- Da aggiungere a app/dashboard.py (in fondo al file) ---

from . import inventory_pdf


@dash.get("/inventory/export")
@login_required
def export_inventory_pdf():
    tenant_name = request.args.get("tenant") or g.current_user.tenant
    hostname = request.args.get("hostname")
    if not tenant_name or not hostname:
        return jsonify(error="specifica tenant e hostname"), 400
    t = _tenant_or_403(tenant_name)
    if not t:
        return jsonify(error="tenant non trovato o non autorizzato"), 403

    try:
        inventory = gl.get_full_inventory_for_host(t.graylog_stream_id, hostname)
        pdf_bytes = inventory_pdf.build_inventory_pdf(inventory, tenant_name, hostname)
    except gl.GraylogError as e:
        return jsonify(error=f"errore Graylog: {e}"), 502

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"inventario-{tenant_name}-{hostname}.pdf",
    )
# --- Da aggiungere a app/dashboard.py (in fondo al file) ---

from flask import Response

from . import cert_ledger


@dash.get("/csrf-token")
@login_required
def get_csrf_token():
    return jsonify(csrf_token=generate_csrf_token(g.current_user.username))


@dash.get("/certificates")
@admin_required
def list_certificates_route():
    return jsonify(cert_ledger.list_issued_certificates())


@dash.post("/certificates/<serial_number>/revoke")
@admin_required
@csrf_protect
def revoke_certificate_route(serial_number):
    ok = cert_ledger.revoke_certificate(serial_number)
    if not ok:
        return jsonify(error="seriale non trovato o gia' revocato"), 404
    return jsonify(ok=True)


@dash.get("/certificates/crl")
@login_required
def download_crl_route():
    """La CRL in se' non e' un segreto (contiene solo seriali revocati,
    nessuna chiave privata) - la lascio raggiungibile da qualunque utente
    autenticato, utile anche per un eventuale proxy/script esterno che la
    scarichi periodicamente."""
    crl_pem = cert_ledger.generate_crl_pem()
    return Response(crl_pem, mimetype="application/pkix-crl")
