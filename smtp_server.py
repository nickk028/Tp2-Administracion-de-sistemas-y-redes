"""
smtp_server.py — Servidor SMTP según RFC 5321
Uso: python smtp_server.py

El operador puede aceptar o rechazar manualmente:
  - Cada comando RCPT TO  (y/n)
  - La transacción completa al recibir DATA (y/n)
"""

import socket
import threading
import datetime
import os

from smtp_common import (
    SMTP_HOST, SMTP_PORT,
    TIMEOUT_SERVER_WAIT,
    R_SERVICE_READY, R_GOODBYE, R_OK, R_VRFY_CANNOT,
    R_START_MAIL, R_SERVICE_UNAVAIL, R_INSUFF_STORAGE,
    R_SYNTAX_ERROR, R_PARAM_ERROR, R_NOT_IMPLEMENTED,
    R_BAD_SEQUENCE, R_MBOX_UNAVAIL, R_TRANSACTION_FAILED,
    encode_line, decode_line, dot_unstuff, mail_regex_validator, parse_address,
    RESERVED_MAILBOXES, R_HELP_MESSAGE
)

# ──────────────────────────────────────────────
#  Directorio donde se guardan los mensajes
# ──────────────────────────────────────────────
MAILBOX_DIR = os.path.join(os.path.dirname(__file__), "mailbox")
os.makedirs(MAILBOX_DIR, exist_ok=True)

SERVER_DOMAIN = "localhost"

# Lock para que la consola interactiva no se mezcle entre hilos
_console_lock = threading.Lock()


# ══════════════════════════════════════════════
#  Sesión SMTP (un hilo por cliente)
# ══════════════════════════════════════════════

class SMTPSession(threading.Thread):
    """Maneja una conexión SMTP entrante siguiendo RFC 5321."""

    def __init__(self, conn: socket.socket, addr):
        super().__init__(daemon=True)
        self.conn   = conn
        self.addr   = addr
        self._reset_transaction()
        self.greeted      = False   # ¿Ya envió EHLO/HELO?
        self.running      = True

    # ──────────────────────────────────────────
    #  Estado de la transacción
    # ──────────────────────────────────────────

    def _reset_transaction(self):
        self.mail_from   = None
        self.rcpt_list   = []
        self.in_data     = False
        self.data_lines  = []

    # ──────────────────────────────────────────
    #  E/S con timeout
    # ──────────────────────────────────────────

    def _send(self, code: str, message: str):
        line = f"{code} {message}"
        print(f"  S: {line}")
        try:
            self.conn.sendall(encode_line(line))
        except OSError:
            self.running = False

    def _recv_line(self, timeout: float) -> str | None:
        """Lee una línea del socket con timeout. Retorna None si hay timeout/error."""
        self.conn.settimeout(timeout)
        buf = b""
        try:
            while True:
                ch = self.conn.recv(1)
                if not ch:
                    return None
                buf += ch
                if buf.endswith(b"\n"):
                    return decode_line(buf)
        except socket.timeout:
            return None
        except OSError:
            return None

    # ──────────────────────────────────────────
    #  Interacción con el operador del servidor
    # ──────────────────────────────────────────

    def _ask_operator(self, prompt: str) -> bool:
        """Pregunta al operador en consola (hilo-seguro). Retorna True = aceptar."""
        with _console_lock:
            while True:
                try:
                    ans = input(f"\n[OPERADOR] {prompt} (y/n): ").strip().lower()
                except EOFError:
                    return True   # Sin terminal interactiva → aceptar todo
                if ans in ("y", "n"):
                    return ans == "y"
                print("  Por favor ingresá 'y' o 'n'.")

    # ──────────────────────────────────────────
    #  Generación de notificación de no entrega
    # ──────────────────────────────────────────

    def _send_bounce(self, reason: str):
        """
        RFC 5321 §6.1 — Si la entrega falla después de que el servidor
        aceptó la responsabilidad, genera un bounce al remitente.
        """
        if not self.mail_from or self.mail_from == "<>":
            return
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        bounce_path = os.path.join(MAILBOX_DIR, f"bounce_{timestamp}.txt")
        with open(bounce_path, "w", encoding="utf-8") as f:
            f.write(f"From: MAILER-DAEMON@{SERVER_DOMAIN}\r\n")
            f.write(f"To: {self.mail_from}\r\n")
            f.write(f"Subject: Delivery failure notification\r\n")
            f.write(f"Date: {datetime.datetime.now().strftime('%a, %d %b %Y %H:%M:%S +0000')}\r\n")
            f.write(f"\r\n")
            f.write(f"Su mensaje no pudo ser entregado.\r\n")
            f.write(f"Motivo: {reason}\r\n")
        print(f"  [BOUNCE] Notificación generada → {bounce_path}")

    # ──────────────────────────────────────────
    #  Almacenamiento del mensaje
    # ──────────────────────────────────────────

    def _store_message(self, received_header: str):
        """Guarda el mensaje en el directorio mailbox/."""
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        msg_path  = os.path.join(MAILBOX_DIR, f"msg_{timestamp}.txt")
        lines     = dot_unstuff(self.data_lines)
        with open(msg_path, "w", encoding="utf-8") as f:
            f.write(received_header.rstrip("\r\n") + "\n")
            for line in lines:
                f.write(line.rstrip("\r\n") + "\n")
        print(f"  [STORE] Mensaje guardado → {msg_path}")

    # ──────────────────────────────────────────
    #  Cabecera Received (RFC 5321 §4.4)
    # ──────────────────────────────────────────

    def _make_received_header(self) -> str:
        now = datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")
        return (
            f"Received: from {self.addr[0]} "
            f"by {SERVER_DOMAIN} "
            f"with SMTP; {now}"
        )

    # ──────────────────────────────────────────
    #  Handlers de comandos
    # ──────────────────────────────────────────

    def _handle_helo(self, arg: str):
        if not arg:
            self._send(R_PARAM_ERROR, "Se requiere el dominio del cliente")
            return
        self.greeted = True
        self._reset_transaction()
        self._send(R_OK, f"{SERVER_DOMAIN} Hola {arg}, encantado de conocerte")

    def _handle_ehlo(self, arg: str):
        if not arg:
            self._send(R_PARAM_ERROR, "Se requiere el dominio del cliente")
            return
        self.greeted = True
        self._reset_transaction()
        # Respuesta multi-línea con extensiones soportadas
        extensions = [
            f"250-{SERVER_DOMAIN} Saluda a {arg}",
            "250-SIZE 10485760",
            "250-8BITMIME",
            "250-VRFY",
            "250 HELP",
        ]
        for line in extensions:
            print(f"  S: {line}")
            try:
                self.conn.sendall(encode_line(line))
            except OSError:
                self.running = False
                return

    def _handle_mail(self, arg: str):
        if not self.greeted:
            self._send(R_BAD_SEQUENCE, "Primero enviá EHLO/HELO")
            return
        if self.mail_from:
            self._send(R_BAD_SEQUENCE, "Transacción ya iniciada, usá RSET")
            return
        addr = parse_address(arg)
        if mail_regex_validator(addr):
            self.mail_from = addr if addr else "<>"
            self._send(R_OK, f"De acuerdo, remitente: {self.mail_from}")
        else:
            self._send(R_PARAM_ERROR, "Formato de mail inválido")
    def _handle_rcpt(self, arg: str):
        if not self.greeted:
            self._send(R_BAD_SEQUENCE, "Primero enviá EHLO/HELO")
            return
        if not self.mail_from:
            self._send(R_BAD_SEQUENCE, "Primero enviá MAIL FROM")
            return
        addr = parse_address(arg)

        if not mail_regex_validator(addr):
            self._send(R_PARAM_ERROR, "Formato de mail inválido")
            return

        if not addr:
            self._send(R_PARAM_ERROR, "Dirección de destinatario inválida")
            return

        local_part = addr.split("@")[0].lower() if "@" in addr else addr.lower()

        # Postmaster siempre se acepta (RFC 5321 §4.5.1)
        if local_part in RESERVED_MAILBOXES:
            self.rcpt_list.append(addr)
            self._send(R_OK, f"Destinatario aceptado: {addr}")
            return

        accept = self._ask_operator(f"¿Aceptar destinatario '{addr}'?")
        if accept:
            self.rcpt_list.append(addr)
            self._send(R_OK, f"Destinatario aceptado: {addr}")
        else:
            self._send(R_MBOX_UNAVAIL, f"Buzón no disponible: {addr}")

    def _handle_data(self):
        if not self.greeted:
            self._send(R_BAD_SEQUENCE, "Primero enviá EHLO/HELO")
            return
        if not self.mail_from:
            self._send(R_BAD_SEQUENCE, "Primero enviá MAIL FROM")
            return
        if not self.rcpt_list:
            self._send(R_BAD_SEQUENCE, "Se requiere al menos un RCPT TO")
            return

        self._send(R_START_MAIL, "Inicio de datos; terminá con <CRLF>.<CRLF>")
        self.in_data    = True
        self.data_lines = []

        # Recibir líneas de datos con timeout de bloque (3 min)
        while True:
            line = self._recv_line(timeout=180)
            if line is None:
                self._send(R_SERVICE_UNAVAIL, "Timeout durante recepción de datos")
                self.running = False
                return
            if line == ".":          # Línea terminadora
                break
            self.data_lines.append(line)

        self.in_data = False
        received_hdr = self._make_received_header()

        # El operador decide si acepta el mensaje completo
        accept = self._ask_operator(
            f"¿Aceptar mensaje de '{self.mail_from}' "
            f"para {self.rcpt_list} ({len(self.data_lines)} líneas)?"
        )
        if accept:
            try:
                self._store_message(received_hdr)
                self._send(R_OK, "Mensaje aceptado y almacenado")
            except OSError as e:
                self._send_bounce(f"Error al almacenar: {e}")
                self._send(R_INSUFF_STORAGE, "Error de almacenamiento")
        else:
            self._send_bounce("Mensaje rechazado manualmente por el operador")
            self._send(R_TRANSACTION_FAILED, "Transacción rechazada")

        self._reset_transaction()

    def _handle_rset(self):
        self._reset_transaction()
        self._send(R_OK, "Estado reiniciado")

    def _handle_vrfy(self, arg: str):
        if not arg:
            self._send(R_PARAM_ERROR, "Se requiere un argumento")
            return
        if not mail_regex_validator(arg):
            self._send(R_PARAM_ERROR, "Formato de mail inválido")
            return

        local = arg.split("@")[0].lower() if "@" in arg else arg.lower()
        if local in RESERVED_MAILBOXES:
            self._send(R_OK, f"{arg} <{arg}@{SERVER_DOMAIN}>")
        else:
            # RFC 5321 §3.5.2 — puede responder 252 sin verificar
            self._send(R_VRFY_CANNOT, f"No se puede verificar {arg}, pero se intentará la entrega")

    def _handle_noop(self):
        self._send(R_OK, "OK")

    def _handle_quit(self):
        self._send(R_GOODBYE, f"{SERVER_DOMAIN} Cerrando conexión. Hasta luego.")
        self.running = False

    def _handle_help(self):
        print(f"{R_HELP_MESSAGE} Códigos de respuesta SMTP:")
        print(f"{R_HELP_MESSAGE} 220 Service ready        -> El servidor está listo para recibir conexiones.")
        print(f"{R_HELP_MESSAGE} 221 Goodbye              -> La conexión se cerró correctamente.")
        print(f"{R_HELP_MESSAGE} 250 OK                   -> La acción solicitada se completó con éxito.")
        print(f"{R_HELP_MESSAGE} 251 Forward              -> El usuario no es local; el mensaje será reenviado.")
        print(f"{R_HELP_MESSAGE} 252 VRFY cannot          -> No se puede verificar el usuario, pero se intentará entregar.")
        print(f"{R_HELP_MESSAGE} 354 Start mail input     -> Comenzar envío de datos; finalizar con <CRLF>.<CRLF>.")
        print(f"{R_HELP_MESSAGE} 421 Service unavailable  -> Servicio no disponible; se cerrará la conexión.")
        print(f"{R_HELP_MESSAGE} 450 Mailbox busy         -> El buzón no está disponible temporalmente.")
        print(f"{R_HELP_MESSAGE} 451 Local error          -> Error local durante el procesamiento.")
        print(f"{R_HELP_MESSAGE} 452 Insufficient storage -> Espacio insuficiente o demasiados destinatarios.")
        print(f"{R_HELP_MESSAGE} 500 Syntax error         -> Error de sintaxis o comando desconocido.")
        print(f"{R_HELP_MESSAGE} 501 Parameter error      -> Error de sintaxis en parámetros o argumentos.")
        print(f"{R_HELP_MESSAGE} 502 Not implemented      -> Comando no implementado por el servidor.")
        print(f"{R_HELP_MESSAGE} 503 Bad sequence         -> Secuencia incorrecta de comandos.")
        print(f"{R_HELP_MESSAGE} 504 Param not impl       -> Parámetro no soportado por el servidor.")
        print(f"{R_HELP_MESSAGE} 550 Mailbox unavailable  -> El buzón no existe o no está disponible.")
        print(f"{R_HELP_MESSAGE} 551 User not local       -> El usuario no pertenece a este servidor.")
        print(f"{R_HELP_MESSAGE} 552 Exceeded storage     -> Se excedió la capacidad de almacenamiento.")
        print(f"{R_HELP_MESSAGE} 553 Name not allowed     -> Nombre o dirección de buzón inválida.")
        print(f"{R_HELP_MESSAGE} 554 Transaction failed   -> La transacción de correo falló.")
        print(f"{R_HELP_MESSAGE} ")
        print(f"{R_HELP_MESSAGE} Comandos SMTP disponibles:")
        print(f"{R_HELP_MESSAGE} HELO <dominio>           -> Inicia la comunicación con el servidor.")
        print(f"{R_HELP_MESSAGE} MAIL FROM:<direccion>    -> Define el remitente del mensaje.")
        print(f"{R_HELP_MESSAGE} RCPT TO:<direccion>      -> Agrega un destinatario al mensaje.")
        print(f"{R_HELP_MESSAGE} DATA                     -> Inicia el envío del cuerpo del mensaje.")
        print(f"{R_HELP_MESSAGE}                            Finalizar con una línea que contenga solo '.'")
        print(f"{R_HELP_MESSAGE} RSET                     -> Cancela la transacción actual y limpia el estado.")
        print(f"{R_HELP_MESSAGE} VRFY <direccion>         -> Consulta si un usuario existe.")
        print(f"{R_HELP_MESSAGE} NOOP                     -> Mantiene activa la conexión sin realizar acciones.")
        print(f"{R_HELP_MESSAGE} HELP                     -> Muestra esta ayuda.")
        print(f"{R_HELP_MESSAGE} QUIT                     -> Finaliza la sesión SMTP.")

    # ──────────────────────────────────────────
    #  Bucle principal de la sesión
    # ──────────────────────────────────────────

    def run(self):
        print(f"\n[SERVER] Nueva conexión de {self.addr[0]}:{self.addr[1]}")
        self._send(R_SERVICE_READY, f"{SERVER_DOMAIN} Servicio SMTP listo (RFC 5321)")

        while self.running:
            raw = self._recv_line(timeout=TIMEOUT_SERVER_WAIT)

            if raw is None:
                print(f"  [SERVER] Timeout o conexión cerrada por {self.addr}")
                try:
                    self._send(R_SERVICE_UNAVAIL, "Timeout de inactividad, cerrando")
                except Exception:
                    pass
                break

            if not raw.strip():
                continue

            print(f"  C: {raw}")

            # Separar comando y argumentos
            parts   = raw.strip().split(None, 1)
            command = parts[0].upper()
            arg     = parts[1] if len(parts) > 1 else ""

            # Despachar comando
            if   command == "HELO":      self._handle_helo(arg)
            elif command == "EHLO":      self._handle_ehlo(arg)
            elif command == "MAIL":      self._handle_mail(arg)
            elif command == "RCPT":      self._handle_rcpt(arg)
            elif command == "DATA":      self._handle_data()
            elif command == "RSET":      self._handle_rset()
            elif command == "VRFY":      self._handle_vrfy(arg)
            elif command == "NOOP":      self._handle_noop()
            elif command == "QUIT":      self._handle_quit()
            elif command == "HELP":      continue
            elif command in ("EXPN"):
                self._send(R_NOT_IMPLEMENTED, f"Comando {command} no implementado")
            else:
                self._send(R_SYNTAX_ERROR, f"Comando desconocido: {command}")

        try:
            self.conn.close()
        except OSError:
            pass
        print(f"  [SERVER] Sesión cerrada con {self.addr[0]}:{self.addr[1]}")


# ══════════════════════════════════════════════
#  Servidor principal
# ══════════════════════════════════════════════

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((SMTP_HOST, SMTP_PORT))
    srv.listen(5)
    print(f"[SERVER] Escuchando en {SMTP_HOST}:{SMTP_PORT}")
    print(f"[SERVER] Mensajes se guardarán en: {MAILBOX_DIR}/")
    print(f"[SERVER] Presioná Ctrl+C para detener.\n")

    try:
        while True:
            conn, addr = srv.accept()
            session = SMTPSession(conn, addr)
            session.start()
    except KeyboardInterrupt:
        print("\n[SERVER] Deteniendo servidor...")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
