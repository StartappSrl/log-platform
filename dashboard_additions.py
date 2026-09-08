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
