"""
Invia un'email di avviso generica usando la configurazione SMTP salvata
nel pannello (Impostazioni) - pensato per essere chiamato da script
esterni (cron sul nodo) tramite 'podman exec', non da un browser.

Uso (dentro il container):
    python3 -m app.send_alert_email "Oggetto" "Corpo del messaggio"
"""
import sys

from . import create_app
from .models import SmtpSettings
from .email_sender import send_html_email_with_images


def send_alert(subject: str, body_text: str, recipients: list | None = None) -> bool:
    """Ritorna True se l'invio e' riuscito, False altrimenti (e stampa
    il motivo su stderr) - non solleva eccezioni, cosi' uno script
    chiamante puo' controllare solo il codice di uscita.

    Se 'recipients' e' fornito, manda SOLO a quegli indirizzi (usato dal
    webhook di notifica Graylog, dove il destinatario e' scelto al
    momento della creazione dell'allarme) - altrimenti usa i
    destinatari globali configurati in Impostazioni."""
    app = create_app()
    with app.app_context():
        settings = SmtpSettings.query.get(1)
        if not settings or not settings.smtp_host:
            print("SMTP non configurato: avviso non inviato.", file=sys.stderr)
            return False

        final_recipients = recipients if recipients else (
            [r.strip() for r in (settings.report_recipients or "").split(",") if r.strip()]
        )
        if not final_recipients:
            print("Nessun destinatario: avviso non inviato.", file=sys.stderr)
            return False

        html_body = f"<p style='font-family:sans-serif;white-space:pre-wrap'>{body_text}</p>"

        try:
            send_html_email_with_images(
                subject, html_body, final_recipients,
                smtp_host=settings.smtp_host, smtp_port=settings.smtp_port or 587,
                smtp_user=settings.smtp_user or "", smtp_password=settings.smtp_password or "",
                smtp_from=settings.smtp_from or "", smtp_use_tls=settings.smtp_use_tls,
            )
            return True
        except Exception as e:
            print(f"Invio avviso fallito: {e}", file=sys.stderr)
            return False


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Uso: python3 -m app.send_alert_email 'Oggetto' 'Corpo del messaggio'", file=sys.stderr)
        sys.exit(1)
    ok = send_alert(sys.argv[1], sys.argv[2])
    sys.exit(0 if ok else 1)
