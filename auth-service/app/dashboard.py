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
                      "stream_id": t.graylog_stream_id,
                      "ragione_sociale": t.ragione_sociale, "partita_iva": t.partita_iva,
                      "indirizzo": t.indirizzo, "referente": t.referente,
                      "email": t.email, "note": t.note} for t in tenants])


@dash.post("/tenants")
@admin_required
@csrf_protect
def create_tenant_route():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip().lower()
    display_name = data.get("display_name") or name
    retention_days = int(data.get("retention_days") or 90)
    ragione_sociale = (data.get("ragione_sociale") or "").strip() or None
    partita_iva = (data.get("partita_iva") or "").strip() or None
    indirizzo = (data.get("indirizzo") or "").strip() or None
    referente = (data.get("referente") or "").strip() or None
    email = (data.get("email") or "").strip() or None
    note = (data.get("note") or "").strip() or None

    if not name or not name.replace("-", "").isalnum():
        return jsonify(error="nome tenant non valido (solo lettere, numeri, trattini)"), 400
    if Tenant.query.filter_by(name=name).first():
        return jsonify(error="tenant già esistente"), 409

    try:
        result = gl.create_tenant_stream(name, retention_days=retention_days)
    except gl.GraylogError as e:
        return jsonify(error=f"errore Graylog: {e}"), 502

    t = Tenant(name=name, display_name=display_name,
               graylog_stream_id=result["stream_id"], graylog_index_set_id=result["index_set_id"],
               ragione_sociale=ragione_sociale, partita_iva=partita_iva, indirizzo=indirizzo,
               referente=referente, email=email, note=note)
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
# --- Da aggiungere a app/dashboard.py (in fondo al file) ---

from . import archive_storage


@dash.get("/archives")
@admin_required
def list_archives_route():
    tenant_name = request.args.get("tenant")
    if not tenant_name:
        return jsonify(error="specifica un tenant"), 400
    archive_storage.prune_expired_archives()
    return jsonify(archive_storage.list_archives_for_tenant(tenant_name))


@dash.get("/archives/download")
@admin_required
def download_archive_route():
    tenant_name = request.args.get("tenant")
    hostname = request.args.get("hostname")
    source_id = request.args.get("source_id")
    day = request.args.get("day")
    if not tenant_name or not hostname or not source_id or not day:
        return jsonify(error="specifica tenant, hostname, source_id e day"), 400

    zip_bytes = archive_storage.build_zip_for_single_day(tenant_name, hostname, source_id, day)
    return send_file(
        io.BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"log-{tenant_name}-{hostname}-{source_id}-{day}.zip",
    )


@dash.get("/archives/download-all")
@admin_required
def download_all_archives_route():
    tenant_name = request.args.get("tenant")
    if not tenant_name:
        return jsonify(error="specifica un tenant"), 400

    zip_bytes = archive_storage.build_zip_for_entire_tenant(tenant_name)
    return send_file(
        io.BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"archivio-completo-{tenant_name}.zip",
    )


@dash.get("/archives/days")
@admin_required
def list_archive_days_route():
    tenant_name = request.args.get("tenant")
    if not tenant_name:
        return jsonify(error="specifica un tenant"), 400
    return jsonify(archive_storage.list_available_days_for_tenant(tenant_name))


@dash.get("/archives/download-day")
@admin_required
def download_day_archives_route():
    tenant_name = request.args.get("tenant")
    day = request.args.get("day")
    if not tenant_name or not day:
        return jsonify(error="specifica tenant e day"), 400

    zip_bytes = archive_storage.build_zip_for_tenant_day(tenant_name, day)
    return send_file(
        io.BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"archivio-{tenant_name}-{day}.zip",
    )


# --- Da aggiungere in un NUOVO blueprint separato (vedi archive_upload_routes.py) ---
# Questa rotta la chiama l'AGENT, non il browser: nessun login utente,
# autenticata con il token per-tenant invece del cookie di sessione.
# --- Da aggiungere a app/dashboard.py (in fondo al file) ---

@dash.get("/device-dashboard")
@login_required
def device_dashboard_route():
    tenant_name = request.args.get("tenant") or g.current_user.tenant
    if not tenant_name:
        return jsonify(error="specifica un tenant"), 400
    t = _tenant_or_403(tenant_name)
    if not t:
        return jsonify(error="tenant non trovato o non autorizzato"), 403

    try:
        result = gl.get_device_dashboard_for_tenant(t.graylog_stream_id)
    except gl.GraylogError as e:
        return jsonify(error=f"errore Graylog: {e}"), 502
    return jsonify(result)

@dash.get("/device-dashboard-all")
@login_required
def device_dashboard_all_route():
    """Come /device-dashboard, ma raggruppato per TUTTI i clienti insieme
    (per l'admin, che non deve piu' scegliere un cliente alla volta) -
    per un utente legato a un singolo cliente, ritorna solo il suo
    (comunque nella stessa forma 'a lista', per usare la stessa schermata
    in entrambi i casi). Se un cliente specifico da' errore verso
    Graylog, non blocca gli altri: compare con lista vuota e si continua
    con il resto."""
    if g.current_user.tenant:
        tenants = Tenant.query.filter_by(name=g.current_user.tenant).all()
    else:
        tenants = Tenant.query.order_by(Tenant.name).all()

    result = []
    for t in tenants:
        try:
            data = gl.get_device_dashboard_for_tenant(t.graylog_stream_id)
        except gl.GraylogError:
            data = {"devices": [], "total_messages_today": 0}
        result.append({
            "tenant": t.name,
            "display_name": t.display_name or t.name,
            "devices": data["devices"],
            "total_messages_today": data["total_messages_today"],
        })
    return jsonify(result)

from .models import SmtpSettings


def _get_smtp_settings() -> SmtpSettings:
    s = SmtpSettings.query.get(1)
    if not s:
        s = SmtpSettings(id=1, smtp_use_tls=True)
        db.session.add(s)
        db.session.commit()
    return s


@dash.get("/settings/smtp")
@admin_required
def get_smtp_settings_route():
    s = _get_smtp_settings()
    return jsonify({
        "smtp_host": s.smtp_host or "",
        "smtp_port": s.smtp_port or 587,
        "smtp_user": s.smtp_user or "",
        # la password non si rimanda mai indietro in chiaro - solo se e' impostata o no
        "smtp_password_set": bool(s.smtp_password),
        "smtp_from": s.smtp_from or "",
        "smtp_use_tls": s.smtp_use_tls,
        "report_recipients": s.report_recipients or "",
    })


@dash.post("/settings/smtp")
@admin_required
@csrf_protect
def save_smtp_settings_route():
    data = request.get_json(force=True, silent=True) or {}
    s = _get_smtp_settings()

    s.smtp_host = (data.get("smtp_host") or "").strip() or None
    s.smtp_port = int(data.get("smtp_port") or 587)
    s.smtp_user = (data.get("smtp_user") or "").strip() or None
    s.smtp_from = (data.get("smtp_from") or "").strip() or None
    s.smtp_use_tls = bool(data.get("smtp_use_tls", True))
    s.report_recipients = (data.get("report_recipients") or "").strip() or None

    # la password si aggiorna SOLO se ne viene mandata una nuova (non vuota) -
    # cosi' non serve reinserirla ogni volta solo per cambiare un altro campo
    new_password = data.get("smtp_password")
    if new_password:
        s.smtp_password = new_password

    db.session.commit()
    return jsonify(ok=True)


@dash.post("/send-nightly-report-now")
@admin_required
@csrf_protect
def send_nightly_report_now_route():
    """Invia subito il report (stessa logica dello script per il cron),
    utile per testare la configurazione SMTP senza aspettare la notte."""
    from .nightly_report import build_tenant_report_data, render_donut_chart_png, build_nightly_report_html
    from .email_sender import send_html_email_with_images
    from datetime import datetime, timezone

    settings = _get_smtp_settings()
    if not settings.smtp_host or not settings.report_recipients:
        return jsonify(error="SMTP non configurato o nessun destinatario impostato (vai in Impostazioni)"), 400

    recipients = [r.strip() for r in settings.report_recipients.split(",") if r.strip()]

    tenants = Tenant.query.order_by(Tenant.name).all()
    if not tenants:
        return jsonify(error="nessun cliente configurato"), 400

    reports = [build_tenant_report_data(t.name, t.display_name or t.name, t.graylog_stream_id)
               for t in tenants]

    generated_at = datetime.now(timezone.utc)
    html = build_nightly_report_html(reports, generated_at=generated_at)
    images = {f"chart{i}": render_donut_chart_png(r["devices"]) for i, r in enumerate(reports)}
    subject = f"Report log notturno (test manuale) — {generated_at.strftime('%d/%m/%Y %H:%M')}"

    try:
        send_html_email_with_images(
            subject, html, recipients,
            smtp_host=settings.smtp_host, smtp_port=settings.smtp_port or 587,
            smtp_user=settings.smtp_user or "", smtp_password=settings.smtp_password or "",
            smtp_from=settings.smtp_from or "", smtp_use_tls=settings.smtp_use_tls,
            inline_images=images,
        )
    except Exception as e:
        return jsonify(error=f"invio fallito: {e}"), 502

    return jsonify(ok=True, sent_to=recipients)
