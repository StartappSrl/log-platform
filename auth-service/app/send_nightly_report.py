"""
Script per l'invio del report notturno - pensato per essere eseguito via
cron con 'podman exec' dentro il container di auth-service. La
configurazione SMTP viene letta dal database (schermata Impostazioni),
non da variabili d'ambiente.

Uso (dentro il container):
    python3 -m app.send_nightly_report
"""
import sys
from datetime import datetime, timezone

from . import create_app
from .models import db, Tenant, SmtpSettings
from .nightly_report import build_tenant_report_data, render_donut_chart_png, build_nightly_report_html
from .email_sender import send_html_email_with_images


def main():
    app = create_app()
    with app.app_context():
        settings = SmtpSettings.query.get(1)
        if not settings or not settings.smtp_host or not settings.report_recipients:
            print("ERRORE: SMTP non configurato o nessun destinatario impostato "
                  "(vai in Impostazioni nel pannello). Nessun invio.", file=sys.stderr)
            sys.exit(1)

        recipients = [r.strip() for r in settings.report_recipients.split(",") if r.strip()]

        tenants = Tenant.query.order_by(Tenant.name).all()
        if not tenants:
            print("Nessun cliente configurato, niente da riportare.")
            return

        reports = []
        for t in tenants:
            print(f"Raccolgo dati per {t.name}...")
            reports.append(build_tenant_report_data(t.name, t.display_name or t.name, t.graylog_stream_id))

        generated_at = datetime.now(timezone.utc)
        html = build_nightly_report_html(reports, generated_at=generated_at)
        images = {f"chart{i}": render_donut_chart_png(r["devices"]) for i, r in enumerate(reports)}
        subject = f"Report log notturno — {generated_at.strftime('%d/%m/%Y')}"

        send_html_email_with_images(
            subject, html, recipients,
            smtp_host=settings.smtp_host, smtp_port=settings.smtp_port or 587,
            smtp_user=settings.smtp_user or "", smtp_password=settings.smtp_password or "",
            smtp_from=settings.smtp_from or "", smtp_use_tls=settings.smtp_use_tls,
            inline_images=images,
        )
        print(f"Report inviato a: {', '.join(recipients)}")


if __name__ == "__main__":
    main()
