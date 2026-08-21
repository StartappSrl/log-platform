import base64
import io

import pyotp
import qrcode


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_qr_base64(secret: str, username: str, issuer: str = "LogPlatform") -> str:
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def verify_code(secret: str, code: str) -> bool:
    try:
        return pyotp.TOTP(secret).verify(code, valid_window=1)
    except Exception:
        return False
