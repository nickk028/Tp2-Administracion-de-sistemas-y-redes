"""
smtp_common.py — Constantes y utilidades compartidas RFC 5321
"""
import re

# ──────────────────────────────────────────────
#  Puerto y host por defecto
# ──────────────────────────────────────────────
SMTP_HOST = "127.0.0.1"
SMTP_PORT = 2525          # Puerto no privilegiado para pruebas (estándar: 25)

# ──────────────────────────────────────────────
#  Timeouts requeridos por RFC 5321 §4.5.3.2
#  (en segundos)
# ──────────────────────────────────────────────
TIMEOUT_GREETING    = 300   # Espera del 220 inicial            → 5 min
TIMEOUT_MAIL_RCPT   = 300   # Respuesta a MAIL FROM / RCPT TO   → 5 min
TIMEOUT_DATA_INIT   = 120   # Espera del 354 tras DATA           → 2 min
TIMEOUT_DATA_BLOCK  = 180   # Envío del cuerpo del mensaje       → 3 min
TIMEOUT_DATA_END    = 600   # Espera del 250 tras el punto final → 10 min
TIMEOUT_SERVER_WAIT = 300   # Espera de próximo comando          → 5 min

# ──────────────────────────────────────────────
#  Límites RFC 5321 §4.5.3.1
# ──────────────────────────────────────────────
MAX_RECIPIENTS  = 100    # Máximo de destinatarios por transacción
MAX_MESSAGE_SIZE = 10_485_760  # 10 MB — anunciado en EHLO SIZE
MAX_LINE_LENGTH  = 998   # Máximo de caracteres por línea (sin CRLF)

# ──────────────────────────────────────────────
#  Códigos de respuesta SMTP (RFC 5321 §4.2)
# ──────────────────────────────────────────────
R_HELP_MESSAGE        = "214"   # Información de ayuda
R_SERVICE_READY       = "220"   # Saludo inicial
R_GOODBYE             = "221"   # Cierre de conexión
R_OK                  = "250"   # Acción completada
R_FORWARD             = "251"   # Usuario no local, se reenvía
R_VRFY_CANNOT         = "252"   # No se puede verificar, pero se intenta entrega
R_START_MAIL          = "354"   # Inicio de datos; terminar con <CRLF>.<CRLF>
R_SERVICE_UNAVAIL     = "421"   # Servicio no disponible (cierra)
R_MAILBOX_BUSY        = "450"   # Buzón no disponible (temporal)
R_LOCAL_ERROR         = "451"   # Error local de procesamiento
R_INSUFF_STORAGE      = "452"   # Sin almacenamiento / demasiados destinatarios
R_SYNTAX_ERROR        = "500"   # Error de sintaxis / comando desconocido
R_PARAM_ERROR         = "501"   # Error de sintaxis en parámetros
R_NOT_IMPLEMENTED     = "502"   # Comando no implementado
R_BAD_SEQUENCE        = "503"   # Secuencia incorrecta de comandos
R_PARAM_NOT_IMPL      = "504"   # Parámetro de comando no implementado
R_MBOX_UNAVAIL        = "550"   # Buzón no disponible / no existe
R_USER_NOT_LOCAL      = "551"   # Usuario no local
R_EXCEEDED_STORAGE    = "552"   # Almacenamiento excedido / mensaje demasiado grande
R_NAME_NOT_ALLOWED    = "553"   # Nombre de buzón no permitido
R_TRANSACTION_FAILED  = "554"   # Transacción fallida

# ──────────────────────────────────────────────
#  Buzones reservados (insensibles a mayúsculas)
# ──────────────────────────────────────────────
RESERVED_MAILBOXES = {"ipineda", "ifalcone", "rpodazza", "postmaster", "abuse"}

# ──────────────────────────────────────────────
#  Texto de ayuda (HELP — RFC 5321 §4.1.1.8)
# ──────────────────────────────────────────────
HELP_LINES = [
    " Lista de ayuda:",
    "",
    " Codigos de respuesta SMTP:",
    "  220 Service ready        -> El servidor esta listo para recibir conexiones.",
    "  221 Goodbye              -> La conexion se cerro correctamente.",
    "  250 OK                   -> La accion solicitada se completo con exito.",
    "  251 Forward              -> El usuario no es local; el mensaje sera reenviado.",
    "  252 VRFY cannot          -> No se puede verificar el usuario, pero se intentara entregar.",
    "  354 Start mail input     -> Comenzar envio de datos; finalizar con <CRLF>.<CRLF>.",
    "  421 Service unavailable  -> Servicio no disponible; se cerrara la conexion.",
    "  450 Mailbox busy         -> El buzon no esta disponible temporalmente.",
    "  451 Local error          -> Error local durante el procesamiento.",
    "  452 Insufficient storage -> Espacio insuficiente o demasiados destinatarios.",
    "  500 Syntax error         -> Error de sintaxis o comando desconocido.",
    "  501 Parameter error      -> Error de sintaxis en parametros o argumentos.",
    "  502 Not implemented      -> Comando no implementado por el servidor.",
    "  503 Bad sequence         -> Secuencia incorrecta de comandos.",
    "  504 Param not impl       -> Parametro no soportado por el servidor.",
    "  550 Mailbox unavailable  -> El buzon no existe o no esta disponible.",
    "  551 User not local       -> El usuario no pertenece a este servidor.",
    "  552 Exceeded storage     -> Se excedio la capacidad de almacenamiento.",
    "  553 Name not allowed     -> Nombre o direccion de buzon invalida.",
    "  554 Transaction failed   -> La transaccion de correo fallo.",
    "",
    " Comandos SMTP disponibles:",
    "  EHLO <dominio>           -> Saludo extendido; negocia extensiones con el servidor.",
    "  HELO <dominio>           -> Saludo basico (compatibilidad con servidores viejos).",
    "  MAIL FROM:<dir> [SIZE=n] -> Define el remitente e informa el tamanio estimado.",
    "  RCPT TO:<direccion>      -> Agrega un destinatario (maximo 100).",
    "  DATA                     -> Inicia el envio del cuerpo del mensaje.",
    "                              Finalizar con una linea que contenga solo '.'",
    "  RSET                     -> Cancela la transaccion actual y limpia el estado.",
    "  VRFY <direccion>         -> Consulta si un usuario existe.",
    "  NOOP                     -> Mantiene activa la conexion sin realizar acciones.",
    "  HELP                     -> Muestra esta ayuda.",
    "  QUIT                     -> Finaliza la sesion SMTP.",
]

# ──────────────────────────────────────────────
#  Utilidades de E/S
# ──────────────────────────────────────────────

def encode_line(text: str) -> bytes:
    """Codifica una línea de texto a bytes con CRLF (RFC 5321 §2.3.8)."""
    return (text + "\r\n").encode("utf-8")


def decode_line(data: bytes) -> str:
    """Decodifica bytes y elimina CRLF/LF del final."""
    return data.decode("utf-8", errors="replace").rstrip("\r\n")


# ──────────────────────────────────────────────
#  Lectura de socket por buffer (mejora #9)
#  En vez de recv(1), lee chunks y acumula.
# ──────────────────────────────────────────────

RECV_BUFFER_SIZE = 4096

def recv_line_buffered(sock, buf: bytearray) -> tuple[str | None, bytearray]:
    """
    Lee una línea CRLF del socket usando un buffer acumulador.
    Retorna (línea_decodificada, buffer_restante).
    Si no hay datos (conexión cerrada) retorna (None, buf).

    Recibe y devuelve el buffer para que el llamador lo persista
    entre llamadas: los bytes sobrantes de una lectura se reusan
    en la siguiente.
    """
    while True:
        # ¿Ya hay una línea completa en el buffer acumulado?
        idx = buf.find(b"\n")
        if idx != -1:
            line = bytes(buf[:idx + 1])
            del buf[:idx + 1]           # consumir la línea del buffer
            return decode_line(line), buf

        # Necesitamos más datos del socket
        try:
            chunk = sock.recv(RECV_BUFFER_SIZE)
        except OSError:
            return None, buf
        if not chunk:
            return None, buf
        buf.extend(chunk)


# ──────────────────────────────────────────────
#  Dot-stuffing / unstuffing (RFC 5321 §4.5.2)
# ──────────────────────────────────────────────

def dot_stuff(message_lines: list[str]) -> list[str]:
    """
    Si una línea empieza con '.', inserta un '.' adicional al inicio.
    El servidor debe revertirlo al almacenar.
    """
    return [("." + ln if ln.startswith(".") else ln) for ln in message_lines]


def dot_unstuff(message_lines: list[str]) -> list[str]:
    """Revierte el dot-stuffing en el lado del servidor."""
    return [(ln[1:] if ln.startswith("..") else ln) for ln in message_lines]


# ──────────────────────────────────────────────
#  Validación de direcciones
# ──────────────────────────────────────────────

def mail_regex_validator(mail: str) -> bool:
    """
    Valida formato user@domain.tld.
    La dirección nula <> es válida (RFC 5321 §4.5.5) y se trata
    como caso especial ANTES de llamar a esta función.
    """
    return bool(re.fullmatch(r"([^@\s]+)@([^@\s]+)\.([^@\s]+)", mail))


def is_null_address(addr: str) -> bool:
    """
    RFC 5321 §4.5.5 — La dirección nula '' o '<>' es válida en MAIL FROM.
    Se usa para bounces automáticos y evitar bucles de notificación.
    """
    return addr in ("", "<>")


# ──────────────────────────────────────────────
#  Extracción de parámetros de MAIL FROM
# ──────────────────────────────────────────────

def parse_mail_from(arg: str) -> tuple[str, dict[str, str]]:
    """
    Parsea 'FROM:<addr> [PARAM=valor ...]' (RFC 1870 / RFC 5321 §4.1.1.2).
    Retorna (dirección, {parámetro: valor}).
    Ejemplo: 'FROM:<user@x.com> SIZE=5000' → ('user@x.com', {'SIZE': '5000'})
    """
    arg = arg.strip()
    params: dict[str, str] = {}

    # Extraer la dirección
    if "<" in arg and ">" in arg:
        start = arg.index("<") + 1
        end   = arg.index(">")
        addr  = arg[start:end].strip()
        rest  = arg[end + 1:].strip()
    elif ":" in arg:
        parts = arg.split(":", 1)
        addr  = parts[1].split()[0].strip() if parts[1].strip() else ""
        rest  = " ".join(parts[1].split()[1:])
    else:
        addr = arg
        rest = ""

    # Parsear parámetros opcionales (ej. SIZE=1234)
    for token in rest.split():
        if "=" in token:
            k, v = token.split("=", 1)
            params[k.upper()] = v
        else:
            params[token.upper()] = ""

    return addr, params


def parse_address(token: str) -> str:
    """
    Extrae la dirección de un token RCPT TO:<addr>.
    Versión simple sin parámetros extendidos.
    """
    token = token.strip()
    if "<" in token and ">" in token:
        return token[token.index("<") + 1 : token.index(">")].strip()
    if ":" in token:
        return token.split(":", 1)[1].strip()
    return token


# ──────────────────────────────────────────────
#  Generación de Message-ID (RFC 5322 §3.6.4)
# ──────────────────────────────────────────────

import uuid
import datetime

def make_message_id(domain: str) -> str:
    """
    Genera un Message-ID único según RFC 5322 §3.6.4.
    Formato: <timestamp.uuid@domain>
    """
    ts  = datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    uid = uuid.uuid4().hex[:12]
    return f"<{ts}.{uid}@{domain}>"
