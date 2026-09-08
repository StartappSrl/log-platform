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
