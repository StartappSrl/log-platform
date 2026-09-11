"""
Invio email via SMTP generico - la configurazione arriva come parametri
espliciti (letti dal database, tabella smtp_settings, tramite la
schermata Impostazioni), non piu' da variabili d'ambiente.
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage


def send_html_email_with_images(subject: str, html_body: str, to_addrs: list,
                                  smtp_host: str, smtp_port: int, smtp_user: str,
                                  smtp_password: str, smtp_from: str, smtp_use_tls: bool = True,
                                  inline_images: dict | None = None) -> None:
    """inline_images: {"chart0": png_bytes, ...} - le chiavi corrispondono
    ai riferimenti cid: nell'HTML (es. cid:chart0)."""
    if not smtp_host:
        raise ValueError("SMTP non configurato (host mancante) - vai in Impostazioni e configuralo")

    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = smtp_from or smtp_user or "no-reply@logplatform.local"
    msg["To"] = ", ".join(to_addrs)

    msg.attach(MIMEText(html_body, "html", "utf-8"))

    for cid, img_bytes in (inline_images or {}).items():
        img = MIMEImage(img_bytes)
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline", filename=f"{cid}.png")
        msg.attach(img)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        if smtp_use_tls:
            server.starttls()
        if smtp_user:
            server.login(smtp_user, smtp_password)
        server.sendmail(msg["From"], to_addrs, msg.as_string())
