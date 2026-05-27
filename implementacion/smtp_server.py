import socket
import threading
import datetime
import os

from implementacion.smtp_common import (
    # Codigos numericos para errores y respuestas
    SMTP_HOST, SMTP_PORT,
    TIMEOUT_SERVER_WAIT, TIMEOUT_DATA_BLOCK,
    MAX_RECIPIENTS, MAX_MESSAGE_SIZE, MAX_LINE_LENGTH,
    R_HELP_MESSAGE, R_SERVICE_READY, R_GOODBYE, R_OK,
    R_VRFY_CANNOT, R_START_MAIL, R_SERVICE_UNAVAIL,
    R_INSUFF_STORAGE, R_SYNTAX_ERROR, R_PARAM_ERROR,
    R_BAD_SEQUENCE, R_MBOX_UNAVAIL,
    R_TRANSACTION_FAILED, R_EXCEEDED_STORAGE,
    HELP_LINES, RESERVED_MAILBOXES,

    # Funciones utiles de codificacion, decodificación, filtrado, etc
    encode_line,
    recv_line_buffered,
    dot_unstuff, mail_regex_validator,
    is_null_address, parse_mail_from,
    parse_address, make_message_id,
)

MAILBOX_DIR   = os.path.join(os.path.dirname(__file__), "mailbox")
os.makedirs(MAILBOX_DIR, exist_ok=True)

SERVER_DOMAIN = "localhost"

_console_lock = threading.Lock()


# ══════════════════════════════════════════════
#  Sesión SMTP (una terminal / thread por cliente)
# ══════════════════════════════════════════════

class SMTPSession(threading.Thread):

    def __init__(self, conn: socket.socket, addr):
        super().__init__(daemon=True)
        self.conn    = conn
        self.addr    = addr
        self.running = True
        # ── estado de saludo ──
        self.greeted      = False
        self.used_esmtp   = False
        # ── estado de transacción ──
        self._reset_transaction()
        # ── buffer de lectura de socket ──
        self._recv_buf = bytearray()

    # ──────────────────────────────────────────
    #  Estado de transacción
    # ──────────────────────────────────────────

    def _reset_transaction(self):
        """
        Reinicia toda la data guardada antes de enviar el mail
        """
        self.mail_from      = None
        self.mail_from_size = 0
        self.rcpt_list      = []
        self.in_data        = False
        self.data_lines     = []

    # ──────────────────────────────────────────
    #  Funciones de entrada y salida
    # ──────────────────────────────────────────

    def _send(self, code: str, message: str):
        """
        Envia las respuestas de las requests.
        Formato: S: [Codigo numerico] Mensaje.
        """
        line = f"{code} {message}"
        print(f"  S: {line}")
        try:
            self.conn.sendall(encode_line(line))
        except OSError:
            self.running = False

    def _recv_line(self, timeout: float) -> str | None:
        """
        Lee una línea usando el buffer compartido de la sesión.
        """
        self.conn.settimeout(timeout)
        try:
            line, self._recv_buf = recv_line_buffered(self.conn, self._recv_buf)
            return line
        except socket.timeout:
            return None
        except OSError:
            return None

    # ──────────────────────────────────────────
    #  Consola del operador
    # ──────────────────────────────────────────

    def _ask_operator(self, prompt: str) -> bool:
        """
        Le pide al usuario que acepte o decline la request
        Ejemplo: Aceptar o reclinar un mensaje entrante
        """
        with _console_lock:
            while True:
                try:
                    ans = input(f"\n[OPERADOR] {prompt} (y/n): ").strip().lower()
                except EOFError:
                    return True
                if ans in ("y", "n"):
                    return ans == "y"
                print("  Ingresá 'y' o 'n'.")

    # ──────────────────────────────────────────
    #  Bounce
    # ──────────────────────────────────────────

    def _send_bounce(self, reason: str):
        """
        Muestra y guarda el intento de mail con sus datos
        """
        if not self.mail_from or is_null_address(self.mail_from):
            return   # nunca hacer bounce a dirección nula (evita loops)
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(MAILBOX_DIR, f"bounce_{ts}.txt")
        now  = datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"From: MAILER-DAEMON@{SERVER_DOMAIN}\r\n")
            f.write(f"To: {self.mail_from}\r\n")
            f.write(f"Subject: Delivery failure notification\r\n")
            f.write(f"Date: {now}\r\n")
            f.write(f"Message-ID: {make_message_id(SERVER_DOMAIN)}\r\n")
            f.write(f"\r\n")
            f.write(f"Su mensaje no pudo ser entregado.\r\nMotivo: {reason}\r\n")
        print(f"  [BOUNCE] → {path}")

    # ──────────────────────────────────────────
    #  Almacenamiento
    # ──────────────────────────────────────────

    def _store_message(self, received_hdr: str, msg_id: str):
        """
        Guarda el mensaje como txt separandolo en cabecera y en el body
        """
        ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(MAILBOX_DIR, f"msg_{ts}.txt")
        lines = dot_unstuff(self.data_lines)
        with open(path, "w", encoding="utf-8") as f:
            # Cabeceras insertadas por el servidor (van al principio)
            f.write(received_hdr.rstrip("\r\n") + "\n")
            f.write(f"Message-ID: {msg_id}\r\n")
            for ln in lines:
                f.write(ln.rstrip("\r\n") + "\n")
        print(f"  [STORE] → {path}")

    # ──────────────────────────────────────────
    #  Cabecera Received
    # ──────────────────────────────────────────

    def _make_received_header(self) -> str:
        """
        Guarda el header con formato
        Received: from <dominio> ([IP])
                    by <servidor> with ESMTP|SMTP
                    id <message-id>;
                    <fecha>
        """
        now      = datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")
        protocol = "ESMTP" if self.used_esmtp else "SMTP"
        client_ip = self.addr[0]
        return (
            f"Received: from {client_ip} ([{client_ip}])\n"
            f"          by {SERVER_DOMAIN} with {protocol};\n"
            f"          {now}"
        )

    # ──────────────────────────────────────────
    #  Handlers de comandos
    # ──────────────────────────────────────────

    def _handle_helo(self, arg: str):
        """
        Handler del comando HELO usada por el cliente para establecer comunicacion basica
        Implementada en SMTP
        """
        if not arg:
            self._send(R_PARAM_ERROR, "Se requiere el dominio del cliente")
            return
        self.greeted    = True
        self.used_esmtp = False
        self._reset_transaction()
        self._send(R_OK, f"{SERVER_DOMAIN} Hola {arg}")

    def _handle_ehlo(self, arg: str):
        """
        Handler del comando EHLO usada por el cliente para establecer comunicacion con extenciones
        Implementada en ESMTP
        """
        if not arg:
            self._send(R_PARAM_ERROR, "Se requiere el dominio del cliente")
            return
        self.greeted    = True
        self.used_esmtp = True
        self._reset_transaction()
        extensions = [
            f"250-{SERVER_DOMAIN} Saluda a {arg}",
            f"250-SIZE {MAX_MESSAGE_SIZE}",
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
        """
        Handler del comando MAIL FROM usado para enviar y guardar la informacion del destinatario
        """
        if not self.greeted:
            self._send(R_BAD_SEQUENCE, "Primero enviá EHLO/HELO")
            return
        if self.mail_from is not None:
            self._send(R_BAD_SEQUENCE, "Transacción ya iniciada, usá RSET")
            return

        addr, params = parse_mail_from(arg)

        if not is_null_address(addr) and not mail_regex_validator(addr):
            self._send(R_PARAM_ERROR, "Formato de dirección inválido")
            return

        announced_size = 0
        if "SIZE" in params:
            try:
                announced_size = int(params["SIZE"])
            except ValueError:
                self._send(R_PARAM_ERROR, "Valor SIZE inválido")
                return
            if announced_size > MAX_MESSAGE_SIZE:
                self._send(
                    R_EXCEEDED_STORAGE,
                    f"Mensaje demasiado grande: máximo {MAX_MESSAGE_SIZE} bytes"
                )
                return

        self.mail_from      = addr if addr else "<>"
        self.mail_from_size = announced_size
        size_info = f" (SIZE anunciado: {announced_size}B)" if announced_size else ""
        self._send(R_OK, f"Remitente aceptado: {self.mail_from}{size_info}")

    def _handle_rcpt(self, arg: str):
        """
        Handler del comando RCPT TO utilizado para enviar y guardar los datos de el/los recipients (RCPT)
        Limite de 100 RCPTs
        Requiere un haber hecho MAIL FROM previamente
        """
        if not self.greeted:
            self._send(R_BAD_SEQUENCE, "Primero enviá EHLO/HELO")
            return
        if self.mail_from is None:
            self._send(R_BAD_SEQUENCE, "Primero enviá MAIL FROM")
            return

        # Valida la cantidad de Recipients
        if len(self.rcpt_list) >= MAX_RECIPIENTS:
            self._send(
                R_INSUFF_STORAGE,
                f"Demasiados destinatarios: máximo {MAX_RECIPIENTS}"
            )
            return

        addr = parse_address(arg)
        if not addr:
            self._send(R_PARAM_ERROR, "Dirección de destinatario inválida")
            return
        if not mail_regex_validator(addr):
            self._send(R_PARAM_ERROR, "Formato de dirección inválido")
            return

        local = addr.split("@")[0].lower() if "@" in addr else addr.lower()

        # Postmaster/buzones reservados → siempre aceptar
        if local in RESERVED_MAILBOXES:
            self.rcpt_list.append(addr)
            self._send(R_OK, f"Destinatario aceptado: {addr}")
            return

        if self._ask_operator(f"¿Aceptar destinatario '{addr}'?"):
            self.rcpt_list.append(addr)
            self._send(R_OK, f"Destinatario aceptado: {addr}")
        else:
            self._send(R_MBOX_UNAVAIL, f"Buzón no disponible: {addr}")

    def _handle_data(self):
        """
        Handler del comando DATA utilizado para escribir y guardar la data del mail
        Requiere haber hecho MAIL FROM y RCPT TO previamente
        Maximo de 998 lineas y maximo de 10 MB de tamaño
        """
        if not self.greeted:
            self._send(R_BAD_SEQUENCE, "Primero enviá EHLO/HELO")
            return
        if self.mail_from is None:
            self._send(R_BAD_SEQUENCE, "Primero enviá MAIL FROM")
            return
        if not self.rcpt_list:
            self._send(R_BAD_SEQUENCE, "Se requiere al menos un RCPT TO")
            return

        self._send(R_START_MAIL, "Inicio de datos; terminá con <CRLF>.<CRLF>")
        self.in_data    = True
        self.data_lines = []
        total_bytes     = 0

        while True:
            line = self._recv_line(timeout=TIMEOUT_DATA_BLOCK)
            if line is None:
                self._send(R_SERVICE_UNAVAIL, "Timeout durante recepción de datos")
                self.running = False
                return
            if line == ".":
                break

            if len(line) > MAX_LINE_LENGTH:
                self.data_lines.append(line)
                total_bytes += len(line)
                if not hasattr(self, "_line_too_long"):
                    self._line_too_long = True
                continue

            self.data_lines.append(line)
            total_bytes += len(line)

            if total_bytes > MAX_MESSAGE_SIZE:
                while True:
                    drain = self._recv_line(timeout=TIMEOUT_DATA_BLOCK)
                    if drain is None or drain == ".":
                        break
                self.in_data = False
                self._reset_transaction()
                self._send(
                    R_EXCEEDED_STORAGE,
                    f"Mensaje demasiado grande: máximo {MAX_MESSAGE_SIZE} bytes"
                )
                return

        self.in_data = False

        if getattr(self, "_line_too_long", False):
            del self._line_too_long
            self._reset_transaction()
            self._send(
                R_PARAM_ERROR,
                f"Línea demasiado larga: máximo {MAX_LINE_LENGTH} caracteres"
            )
            return

        received_hdr = self._make_received_header()
        msg_id       = make_message_id(SERVER_DOMAIN)

        if self._ask_operator(
            f"¿Aceptar mensaje de '{self.mail_from}' "
            f"para {self.rcpt_list} ({len(self.data_lines)} líneas, {total_bytes}B)?"
        ):
            try:
                self._store_message(received_hdr, msg_id)
                self._send(R_OK, f"Mensaje aceptado. ID: {msg_id}")
            except OSError as e:
                self._send_bounce(f"Error al almacenar: {e}")
                self._send(R_INSUFF_STORAGE, "Error de almacenamiento")
        else:
            self._send_bounce("Mensaje rechazado manualmente por el operador")
            self._send(R_TRANSACTION_FAILED, "Transacción rechazada")

        self._reset_transaction()

    def _handle_rset(self):
        """
        Handler del comando RSET utilizado para reiniciar los datos previamente cargados
        """
        self._reset_transaction()
        self._send(R_OK, "Estado de transacción reiniciado")

    def _handle_vrfy(self, arg: str):
        """
        Handler del comando VRFY utilizada para intentar validar un mail ingresado
        """
        if not arg:
            self._send(R_PARAM_ERROR, "Se requiere un argumento")
            return
        if not mail_regex_validator(arg):
            self._send(R_PARAM_ERROR, "Formato de dirección inválido")
            return
        local = arg.split("@")[0].lower() if "@" in arg else arg.lower()
        if local in RESERVED_MAILBOXES:
            self._send(R_OK, f"{arg} <{arg}@{SERVER_DOMAIN}>")
        else:
            self._send(R_VRFY_CANNOT, f"No se puede verificar {arg}, pero se intentará la entrega")

    def _handle_noop(self):
        """
        Handler del comando NOOP utilizado para validar el estado de la conexión
        """
        self._send(R_OK, "OK")

    def _handle_quit(self):
        """
        Handler del comando QUIT utilizado para terminar la conexion entre Cliente-Servidor
        """
        self._send(R_GOODBYE, f"{SERVER_DOMAIN} Cerrando conexión. Hasta luego.")
        self.running = False

    def _handle_help(self):
        """
        Handler del comando HELP utilizado para enviar informacion util sobre comandos y codigos numericos por el servidor
        """
        for i, text in enumerate(HELP_LINES):
            sep  = " " if i == len(HELP_LINES) - 1 else "-"
            line = f"{R_HELP_MESSAGE}{sep}{text}"
            print(f"  S: {line}")
            try:
                self.conn.sendall(encode_line(line))
            except OSError:
                self.running = False
                return

    # ──────────────────────────────────────────
    #  Bucle principal
    # ──────────────────────────────────────────

    def run(self):
        print(f"\n[SERVER] Nueva conexión de {self.addr[0]}:{self.addr[1]}")
        self._send(R_SERVICE_READY, f"{SERVER_DOMAIN} Servicio SMTP listo (RFC 5321)")

        while self.running:
            raw = self._recv_line(timeout=TIMEOUT_SERVER_WAIT)

            if raw is None:
                print(f"  [SERVER] Timeout o conexión cerrada — {self.addr}")
                try:
                    self._send(R_SERVICE_UNAVAIL, "Timeout de inactividad, cerrando")
                except Exception:
                    pass
                break

            if not raw.strip():
                continue

            print(f"  C: {raw}")
            parts   = raw.strip().split(None, 1)
            command = parts[0].upper()
            arg     = parts[1] if len(parts) > 1 else ""
            
            # Handlers de los comandos
            if   command == "HELO": self._handle_helo(arg)
            elif command == "EHLO": self._handle_ehlo(arg)
            elif command == "MAIL": self._handle_mail(arg)
            elif command == "RCPT": self._handle_rcpt(arg)
            elif command == "DATA": self._handle_data()
            elif command == "RSET": self._handle_rset()
            elif command == "VRFY": self._handle_vrfy(arg)
            elif command == "NOOP": self._handle_noop()
            elif command == "QUIT": self._handle_quit()
            elif command == "HELP": self._handle_help()
            else:
                self._send(R_SYNTAX_ERROR, f"Comando desconocido: {command}")

        try:
            self.conn.close()
        except OSError:
            pass
        print(f"  [SERVER] Sesión cerrada — {self.addr[0]}:{self.addr[1]}")


# ══════════════════════════════════════════════
#  Servidor principal
# ══════════════════════════════════════════════

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((SMTP_HOST, SMTP_PORT))
    srv.listen(5)
    print(f"[SERVER] Escuchando en {SMTP_HOST}:{SMTP_PORT}")
    print(f"[SERVER] Mensajes en: {MAILBOX_DIR}/")
    print(f"[SERVER] Límites: {MAX_RECIPIENTS} destinatarios, {MAX_MESSAGE_SIZE // 1024 // 1024}MB por mensaje")
    print(f"[SERVER] Ctrl+C para detener.\n")
    try:
        while True:
            conn, addr = srv.accept()
            SMTPSession(conn, addr).start()
    except KeyboardInterrupt:
        print("\n[SERVER] Deteniendo...")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
