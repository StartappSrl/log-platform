"""Percorsi del repository e un logo di prova, condivisi dai controlli di integrazione."""
import base64, os, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
APP_SRC = os.path.join(REPO, "auth-service", "app")
SCRIPTS = os.path.join(REPO, "scripts")
FAKE_GRAYLOG = os.path.join(HERE, "_fake_graylog.py")
_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def wait_port(port, timeout=20):
    """Attende che il server simulato accetti connessioni (meglio di un'attesa fissa: la CI puo' essere lenta)."""
    import socket, time
    fine = time.time() + timeout
    while time.time() < fine:
        try:
            socket.create_connection(("127.0.0.1", int(port)), timeout=0.5).close()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"il server simulato sulla porta {port} non risponde")


def logo_path():
    """Un PNG valido di 1x1 pixel: serve solo a provare che il logo venga incorporato nella mail."""
    p = os.path.join(tempfile.mkdtemp(), "logo.png")
    open(p, "wb").write(_PNG)
    return p
