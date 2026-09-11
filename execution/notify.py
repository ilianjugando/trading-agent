"""Avisos al telefono cuando el bot hace algo que importa.

Hasta ahora el sistema no notificaba nada: operaba solo, y si algo salia
mal habia que acordarse de abrir el dashboard para enterarse. Un bot que
opera sin vigilancia y no avisa cuando compra, cuando un stop lo saca o
cuando el corte de emergencia se dispara es un bot que te enteras tarde.

Dos transportes, los dos sin dependencias nuevas (urllib/smtplib de la
stdlib). Se usa el que este configurado en .env; si no hay ninguno, se
registra una vez y el sistema sigue igual.

    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID     (push instantaneo al celular)
    NOTIFY_EMAIL_TO / SMTP_HOST / SMTP_USER / SMTP_PASSWORD

Que se notifica y que no: SOLO eventos materiales. Un aviso por cada
ciclo entrenaria a ignorarlos, que es exactamente como una alerta deja de
servir -- el mismo criterio que la tolerancia de la reconciliacion.
"""
import json
import os
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage

# Codigos de decisions.log que merecen sacarte del telefono del bolsillo.
# Todo lo demas (escaneos, "ya la tenemos", stops subidos) se mira en el
# dashboard cuando uno quiere, no cuando el bot quiere.
NOTIFY_ON = {
    "executed": "Compra ejecutada",
    "stopped_out": "Posicion cerrada por stop",
    "halted": "BOT DETENIDO por el circuit breaker",
    "blocked_by_kill_switch": "Corte de emergencia activo",
    "order_rejected": "Orden rechazada por el broker",
    "order_not_filled": "La orden no se lleno",
    "exit_error": "FALLO al cerrar una posicion",
    "position_reconciliation_alert": "El estado no coincide con el broker",
    "phantom_position_cleared": "Posicion fantasma corregida",
    "error": "Error del ciclo",
}

_TIMEOUT = 10


def _telegram(text: str) -> bool:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=_TIMEOUT):
        return True


def _email(subject: str, text: str) -> bool:
    to, host = os.environ.get("NOTIFY_EMAIL_TO"), os.environ.get("SMTP_HOST")
    user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    if not (to and host and user and password):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content(text)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587)), timeout=_TIMEOUT) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg)
    return True


def is_configured() -> bool:
    return bool(
        (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))
        or (os.environ.get("NOTIFY_EMAIL_TO") and os.environ.get("SMTP_HOST"))
    )


def format_event(record: dict) -> str | None:
    """El evento en una linea legible, o None si no amerita aviso."""
    result = record.get("result")
    headline = NOTIFY_ON.get(result)
    if headline is None:
        return None

    pool = record.get("pool") or "?"
    symbol = record.get("symbol") or (record.get("signal") or {}).get("symbol") or ""
    parts = [f"[{pool}] {headline}"]
    if symbol:
        parts.append(symbol)

    sizing = record.get("sizing") or {}
    if sizing.get("usd"):
        parts.append(f"US$ {sizing['usd']:,.2f}")
    if record.get("price") is not None:
        parts.append(f"a {record['price']}")
    if record.get("won") is not None:
        parts.append("GANADA" if record["won"] else "PERDIDA")

    reason = record.get("reason") or record.get("detail")
    line = " · ".join(parts)
    if reason:
        line += f"\n{str(reason)[:300]}"
    return line


def send(record: dict) -> bool:
    """Notifica un evento si amerita. Nunca levanta: un fallo de red
    avisando no puede tumbar un ciclo de trading."""
    text = format_event(record)
    if text is None:
        return False
    try:
        return _telegram(text) or _email("Trading Agent", text)
    except Exception:
        return False


def send_text(text: str) -> bool:
    """Aviso suelto, para cosas que no son un evento de decisions.log."""
    try:
        return _telegram(text) or _email("Trading Agent", text)
    except Exception:
        return False


if __name__ == "__main__":  # prueba manual: python -m execution.notify
    from config.settings import load_settings

    load_settings()  # carga .env
    print("configurado:", is_configured())
    print("enviado:", send_text("Prueba de notificacion del Trading Agent."))
    print(json.dumps({"ejemplo": format_event({
        "pool": "crypto", "result": "executed", "symbol": "ETH-USDT",
        "sizing": {"usd": 1234.5},
    })}, ensure_ascii=False))
