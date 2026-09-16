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

import json
import secrets as _secrets
import bcrypt as _bcrypt

BACKUP_CODE_COUNT = 8


def generate_backup_codes() -> list:
    """8 codici monouso, formato leggibile (XXXX-XXXX) per essere
    trascritti facilmente su carta."""
    return [
        f"{_secrets.token_hex(2).upper()}-{_secrets.token_hex(2).upper()}"
        for _ in range(BACKUP_CODE_COUNT)
    ]


def hash_backup_codes(codes: list) -> str:
    hashed = [_bcrypt.hashpw(c.encode(), _bcrypt.gensalt()).decode() for c in codes]
    return json.dumps(hashed)


def verify_and_consume_backup_code(stored_hashes_json: str, code: str) -> tuple:
    """Controlla se 'code' corrisponde a uno dei codici salvati (hash) -
    se si', lo consuma (non sara' piu' utilizzabile) e ritorna
    (True, nuovo_json_senza_quel_codice). Se non corrisponde a nessuno,
    ritorna (False, stored_hashes_json invariato)."""
    if not stored_hashes_json:
        return False, stored_hashes_json
    try:
        hashes = json.loads(stored_hashes_json)
    except (ValueError, TypeError):
        return False, stored_hashes_json

    code_normalized = code.strip().upper()
    for i, h in enumerate(hashes):
        if _bcrypt.checkpw(code_normalized.encode(), h.encode()):
            remaining = hashes[:i] + hashes[i + 1:]
            return True, json.dumps(remaining)
    return False, stored_hashes_json
