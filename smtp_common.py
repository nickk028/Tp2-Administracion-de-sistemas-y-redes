"""
smtp_common.py — Constantes y utilidades compartidas RFC 5321
"""
import re
# ──────────────────────────────────────────────
#  Puerto y host por defecto
# ──────────────────────────────────────────────
SMTP_HOST = "127.0.0.1"
SMTP_PORT = 2525          # Puerto no privilegiado para pruebas (el estándar es 25)

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
#  Códigos de respuesta SMTP (RFC 5321 §4.2)
# ──────────────────────────────────────────────
R_HELP_MESSAGE        = "214"   # Información sobre cómo utilizar el receptor
R_SERVICE_READY       = "220"   # Saludo inicial
R_GOODBYE             = "221"   # Cierre de conexión
R_OK                  = "250"   # Acción completada
R_FORWARD             = "251"   # Usuario no local, se reenvía
R_VRFY_CANNOT         = "252"   # No se puede verificar, pero se intenta entrega
R_START_MAIL          = "354"   # Inicio de datos; terminar con <CRLF>.<CRLF>
R_SERVICE_UNAVAIL     = "421"   # Servicio no disponible (cierra)
R_MAILBOX_BUSY        = "450"   # Buzón no disponible (temporal)
R_LOCAL_ERROR         = "451"   # Error local de procesamiento
R_INSUFF_STORAGE      = "452"   # Sin almacenamiento temporal / demasiados dest.
R_SYNTAX_ERROR        = "500"   # Error de sintaxis / comando desconocido
R_PARAM_ERROR         = "501"   # Error de sintaxis en parámetros
R_NOT_IMPLEMENTED     = "502"   # Comando no implementado
R_BAD_SEQUENCE        = "503"   # Secuencia incorrecta de comandos
R_PARAM_NOT_IMPL      = "504"   # Parámetro de comando no implementado
R_MBOX_UNAVAIL        = "550"   # Buzón no disponible / no existe
R_USER_NOT_LOCAL      = "551"   # Usuario no local
R_EXCEEDED_STORAGE    = "552"   # Almacenamiento excedido
R_NAME_NOT_ALLOWED    = "553"   # Nombre de buzón no permitido
R_TRANSACTION_FAILED  = "554"   # Transacción fallida

# ──────────────────────────────────────────────
#  Secuencia de fin de datos RFC 5321 §4.1.1.4
# ──────────────────────────────────────────────
DATA_TERMINATOR = b"\r\n.\r\n"

# ──────────────────────────────────────────────
#  Buzones reservados (insensibles a mayúsculas)
# ──────────────────────────────────────────────
RESERVED_MAILBOXES = {"ipineda", "ifalcone", "rpodazza"}

# ──────────────────────────────────────────────
#  Utilidades
# ──────────────────────────────────────────────

HELP_LINES = [
    " Lista de ayuda:",
    "",
    " Codigos de respuesta SMTP:",
    "  214 Help Message         -> Información sobre cómo utilizar el receptor.",
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
    "  EHLO <dominio>           -> Inicia la comunicacion con el servidor.",
    "  MAIL FROM:<direccion>    -> Define el remitente del mensaje.",
    "  RCPT TO:<direccion>      -> Agrega un destinatario al mensaje.",
    "  DATA                     -> Inicia el envio del cuerpo del mensaje.",
    "                              Finalizar con una linea que contenga solo '.'",
    "  RSET                     -> Cancela la transaccion actual y limpia el estado.",
    "  VRFY <direccion>         -> Consulta si un usuario existe.",
    "  NOOP                     -> Mantiene activa la conexion sin realizar acciones.",
    "  HELP                     -> Muestra esta ayuda.",
    "  QUIT                     -> Finaliza la sesion SMTP.",
]

def encode_line(text: str) -> bytes:
    """Codifica una línea de texto a bytes CRLF."""
    return (text + "\r\n").encode("utf-8")


def decode_line(data: bytes) -> str:
    """Decodifica bytes y elimina CRLF/LF del final."""
    return data.decode("utf-8", errors="replace").rstrip("\r\n")


def dot_stuff(message_lines: list[str]) -> list[str]:
    """
    Dot-stuffing (RFC 5321 §4.5.2):
    Si una línea empieza con '.', se agrega un '.' adicional al inicio.
    El servidor debe revertirlo al almacenar.
    """
    stuffed = []
    for line in message_lines:
        if line.startswith("."):
            stuffed.append("." + line)
        else:
            stuffed.append(line)
    return stuffed


def dot_unstuff(message_lines: list[str]) -> list[str]:
    """Revierte el dot-stuffing en el lado del servidor."""
    unstuffed = []
    for line in message_lines:
        if line.startswith(".."):
            unstuffed.append(line[1:])
        else:
            unstuffed.append(line)
    return unstuffed


def parse_address(token: str) -> str:
    """
    Extrae la dirección de un token MAIL FROM:<addr> o RCPT TO:<addr>.
    Acepta tanto <addr> como addr sin ángulos.
    """
    token = token.strip()
    if "<" in token and ">" in token:
        start = token.index("<") + 1
        end   = token.index(">")
        return token[start:end].strip()
    # Sin ángulos → tomar la parte después del primer espacio/':'
    if ":" in token:
        return token.split(":", 1)[1].strip()
    return token

def mail_regex_validator(mail: str) -> bool:
    return re.fullmatch(r"(([^@]+)@([^@]+)\.([^@]+))", mail)