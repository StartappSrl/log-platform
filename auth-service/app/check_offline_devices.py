"""
Controlla se qualche dispositivo e' passato offline e manda un avviso
via email - pensato per girare via cron ogni 15-30 minuti (non ogni
notte come il report riepilogativo). Manda un avviso una volta sola per
transizione online->offline (non ripete ad ogni esecuzione finche' resta
offline), e un avviso di "tornato online" quando si riconnette.

Uso (dentro il container):
    python3 -m app.check_offline_devices
"""
from datetime import datetime

from . import create_app
from .models import db, Tenant, SmtpSettings, OfflineAlertState
from . import graylog_client as gl
from .email_sender import send_html_email_with_images


def _send_simple_alert(settings: SmtpSettings, subject: str, html_body: str):
    recipients = [r.strip() for r in (settings.report_recipients or "").split(",") if r.strip()]
    if not recipients:
        return
    send_html_email_with_images(
        subject, html_body, recipients,
        smtp_host=settings.smtp_host, smtp_port=settings.smtp_port or 587,
        smtp_user=settings.smtp_user or "", smtp_password=settings.smtp_password or "",
        smtp_from=settings.smtp_from or "", smtp_use_tls=settings.smtp_use_tls,
    )


def main():
    app = create_app()
    with app.app_context():
        settings = SmtpSettings.query.get(1)
        if not settings or not settings.smtp_host or not settings.report_recipients:
            print("SMTP non configurato: nessun controllo/avviso eseguito.")
            return

        tenants = Tenant.query.order_by(Tenant.name).all()
        for t in tenants:
            try:
                data = gl.get_device_dashboard_for_tenant(t.graylog_stream_id)
            except gl.GraylogError as e:
                print(f"Attenzione: Graylog non raggiungibile per {t.name}: {e}")
                continue

            for d in data["devices"]:
                existing_alert = OfflineAlertState.query.filter_by(
                    tenant=t.name, hostname=d["hostname"]
                ).first()

                if not d["online"] and not existing_alert:
                    # Transizione online -> offline: manda l'avviso, registra lo stato
                    print(f"ALERT: {t.name}/{d['hostname']} e' offline, invio avviso.")
                    _send_simple_alert(
                        settings,
                        subject=f"[{t.display_name or t.name}] {d['hostname']} non invia più log",
                        html_body=(
                            f"<p>Il dispositivo <b>{d['hostname']}</b> del cliente "
                            f"<b>{t.display_name or t.name}</b> non invia più log da almeno 20 minuti.</p>"
                            f"<p>Ultimo log ricevuto: {d.get('last_seen', 'sconosciuto')}</p>"
                        ),
                    )
                    db.session.add(OfflineAlertState(tenant=t.name, hostname=d["hostname"],
                                                       alerted_at=datetime.utcnow()))
                    db.session.commit()

                elif d["online"] and existing_alert:
                    # E' tornato online: avviso di recupero, pulisco lo stato
                    print(f"RECUPERO: {t.name}/{d['hostname']} e' tornato online.")
                    _send_simple_alert(
                        settings,
                        subject=f"[{t.display_name or t.name}] {d['hostname']} è tornato online",
                        html_body=(
                            f"<p>Il dispositivo <b>{d['hostname']}</b> del cliente "
                            f"<b>{t.display_name or t.name}</b> ha ripreso a inviare log.</p>"
                        ),
                    )
                    db.session.delete(existing_alert)
                    db.session.commit()

        print("Controllo dispositivi offline completato.")


if __name__ == "__main__":
    main()
