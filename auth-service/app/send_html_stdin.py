"""
Invia una mail HTML letta da stdin, usando l'SMTP configurato in
Impostazioni e gli stessi destinatari del report notturno.

Pensato per essere chiamato da script sul nodo tramite 'podman exec -i'
(vedi scripts/esito_notturno.py):

    python3 -m app.send_html_stdin "Oggetto" < pagina.html

Se nell'HTML c'e' <img src="cid:logo">, il logo (/logos/defendia-logo.png)
viene incorporato nella mail; se il file del logo non esiste, il tag
<img> viene rimosso (meglio nessun logo che un'icona di immagine rotta).

Codici di uscita: 0 inviata, 1 invio fallito, 2 input mancante.
"""
import os
import re
import sys

from . import create_app
from .models import SmtpSettings
from .email_sender import send_html_email_with_images

LOGO_PATH = os.environ.get("EMAIL_LOGO_PATH", "/logos/defendia-logo.png")


def prepare_html(html_body: str):
    """Ritorna (html, immagini_inline)."""
    images = {}
    if os.path.exists(LOGO_PATH):
        with open(LOGO_PATH, "rb") as f:
            images["logo"] = f.read()
    else:
        html_body = re.sub(r"<img[^>]*cid:logo[^>]*>", "", html_body)
    return html_body, images


def main() -> int:
    if len(sys.argv) != 2:
        print("Uso: python3 -m app.send_html_stdin 'Oggetto' < pagina.html", file=sys.stderr)
        return 2
    subject = sys.argv[1]
    html_body = sys.stdin.read()
    if not html_body.strip():
        print("Nessun HTML ricevuto da stdin.", file=sys.stderr)
        return 2

    app = create_app()
    with app.app_context():
        s = SmtpSettings.query.get(1)
        if not s or not s.smtp_host:
            print("SMTP non configurato (Impostazioni): mail non inviata.", file=sys.stderr)
            return 1
        recipients = [r.strip() for r in (s.report_recipients or "").split(",") if r.strip()]
        if not recipients:
            print("Nessun destinatario configurato (Impostazioni): mail non inviata.", file=sys.stderr)
            return 1

        html_body, images = prepare_html(html_body)
        try:
            send_html_email_with_images(
                subject, html_body, recipients,
                smtp_host=s.smtp_host, smtp_port=s.smtp_port or 587,
                smtp_user=s.smtp_user or "", smtp_password=s.smtp_password or "",
                smtp_from=s.smtp_from or "", smtp_use_tls=s.smtp_use_tls,
                inline_images=images,
            )
        except Exception as exc:
            print(f"Invio fallito: {exc}", file=sys.stderr)
            return 1
    print(f"Mail inviata a: {', '.join(recipients)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
