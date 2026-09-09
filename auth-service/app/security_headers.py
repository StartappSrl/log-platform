"""
Header di sicurezza HTTP applicati a ogni risposta del pannello.

Nota onesta sulla CSP: il pannello attuale usa parecchio 'onclick=' inline
e blocchi <script> inline in dashboard.html/login.html. Una Content-
Security-Policy davvero rigorosa (senza 'unsafe-inline') richiederebbe di
spostare tutto il JavaScript in file esterni e sostituire gli onclick con
addEventListener - un refactor non da poco, non fatto qui per restare
dentro un intervento mirato alla sicurezza senza riscrivere il frontend.
La CSP sotto e' quindi un compromesso: blocca il caricamento di risorse
(script, stili, immagini, frame) da domini esterni non previsti, ma non
protegge da un'iniezione di script inline se un'altra falla (es. XSS)
permettesse di inserire markup arbitrario nella pagina. Il refactor per
eliminare 'unsafe-inline' resta una miglioria futura consigliata.
"""


def apply_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=(), payment=()"
    # Innocuo su HTTP semplice (il browser lo ignora se non su HTTPS),
    # utile quando si arriva via Traefik/gate in HTTPS.
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    return response
