"""
smtp_client.py — Cliente SMTP según RFC 5321
Uso: python smtp_client.py

Flujo interactivo:
  1. Conecta al servidor y espera el 220.
  2. Envía HELO automáticamente.
  3. Menú de comandos: MAIL FROM, RCPT TO, DATA, RSET, VRFY, NOOP, QUIT.
  4. Timeouts individuales por comando según RFC 5321 §4.5.3.2.
"""

import socket
import datetime

from smtp_common import (
    SMTP_HOST, SMTP_PORT,
    TIMEOUT_GREETING,
    TIMEOUT_MAIL_RCPT,
    TIMEOUT_DATA_INIT,
    TIMEOUT_DATA_BLOCK,
    TIMEOUT_DATA_END,
    encode_line, decode_line, dot_stuff,
)

CLIENT_DOMAIN = "cliente.local"


# ══════════════════════════════════════════════
#  Cliente SMTP
# ══════════════════════════════════════════════

class SMTPClient:
    """Cliente SMTP interactivo con timeouts RFC 5321."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None
        # Estado de la transacción actual
        self._mail_from: str | None = None
        self._rcpt_list: list[str]  = []

    # ──────────────────────────────────────────
    #  Conexión y desconexión
    # ──────────────────────────────────────────

    def connect(self) -> bool:
        print(f"[CLIENT] Conectando a {self.host}:{self.port}...")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.sock.connect((self.host, self.port))
        except ConnectionRefusedError:
            print("[CLIENT] ERROR: Conexión rechazada. ¿Está el servidor corriendo?")
            return False

        # Esperar 220 con timeout de saludo (5 min)
        code, msg = self._recv_response(TIMEOUT_GREETING)
        if code is None:
            print("[CLIENT] TIMEOUT: No se recibió el saludo 220.")
            self.sock.close()
            return False
        if not code.startswith("2"):
            print(f"[CLIENT] Servidor no disponible: {code} {msg}")
            self.sock.close()
            return False

        self._mail_from = None
        self._rcpt_list = []
        print(f"[CLIENT] Conectado.")
        return True

    def disconnect(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    # ──────────────────────────────────────────
    #  E/S con timeout
    # ──────────────────────────────────────────

    def _send_cmd(self, line: str):
        """Envía un comando al servidor."""
        print(f"  C: {line}")
        self.sock.sendall(encode_line(line))

    def _recv_response(self, timeout: float) -> tuple[str | None, str]:
        """
        Lee una respuesta (posiblemente multi-línea) con timeout.
        Retorna (código, texto_completo) o (None, "") en timeout/error.
        """
        self.sock.settimeout(timeout)
        lines   = []
        code    = None
        try:
            while True:
                buf = b""
                while not buf.endswith(b"\n"):
                    ch = self.sock.recv(1)
                    if not ch:
                        return None, ""
                    buf += ch
                line = decode_line(buf)
                lines.append(line)
                # Formato: "XYZ-texto" → continúa; "XYZ texto" → última línea
                if len(line) >= 3:
                    code = line[:3]
                    if len(line) == 3 or line[3] == " ":
                        break
                    # Si el 4º carácter es '-', sigue leyendo
        except socket.timeout:
            print(f"  [TIMEOUT] ({timeout}s) sin respuesta del servidor.")
            return None, ""
        except OSError as e:
            print(f"  [ERROR] Red: {e}")
            return None, ""

        full_text = " | ".join(lines)
        print(f"  S: {full_text}")
        return code, full_text

    # ──────────────────────────────────────────
    #  Comandos SMTP
    # ──────────────────────────────────────────

    def cmd_helo(self) -> bool:
        self._send_cmd(f"HELO {CLIENT_DOMAIN}")
        code, _ = self._recv_response(TIMEOUT_MAIL_RCPT)
        if code is None:
            print("  [CLIENT] Timeout en HELO.")
            return False
        ok = code.startswith("2")
        if not ok:
            print(f"  [CLIENT] HELO rechazado: {code}")
        return ok

    def cmd_mail_from(self, addr: str) -> bool:
        if self._mail_from:
            print(f"  [INFO] Ya hay un remitente definido: {self._mail_from}")
            print(f"         Usá RSET (opción 4) para reiniciar la transacción.")
            return False
        self._send_cmd(f"MAIL FROM:<{addr}>")
        code, _ = self._recv_response(TIMEOUT_MAIL_RCPT)
        if code is None:
            print("  [TIMEOUT] MAIL FROM sin respuesta.")
            return False
        ok = code.startswith("2")
        if ok:
            self._mail_from = addr
        else:
            print(f"  [RECHAZADO] MAIL FROM: {code}")
        return ok

    def cmd_rcpt_to(self, addr: str) -> bool:
        self._send_cmd(f"RCPT TO:<{addr}>")
        code, _ = self._recv_response(TIMEOUT_MAIL_RCPT)
        if code is None:
            print("  [TIMEOUT] RCPT TO sin respuesta.")
            return False
        ok = code.startswith("2")
        if ok:
            self._rcpt_list.append(addr)
            print(f"  [INFO] Destinatarios aceptados hasta ahora: {self._rcpt_list}")
        else:
            print(f"  [RECHAZADO] RCPT TO: {code}")
        return ok

    def cmd_data(self, body_lines: list[str]) -> bool:
        """
        Envía el comando DATA y luego el cuerpo del mensaje.
        Aplica dot-stuffing automáticamente.
        """
        # Paso 1: enviar DATA, esperar 354
        
        code, _ = self._recv_response(TIMEOUT_DATA_INIT)
        if code is None:
            print("  [TIMEOUT] Esperando 354.")
            return False
        if not code.startswith("3"):
            print(f"  [RECHAZADO] DATA: {code}")
            return False

        # Paso 2: enviar cuerpo con dot-stuffing, timeout de bloque
        self.sock.settimeout(TIMEOUT_DATA_BLOCK)
        stuffed = dot_stuff(body_lines)
        try:
            for line in stuffed:
                self.sock.sendall(encode_line(line))
            # Línea terminadora
            self.sock.sendall(encode_line("."))
            print("  C: .")
        except socket.timeout:
            print("  [TIMEOUT] Enviando datos.")
            return False
        except OSError as e:
            print(f"  [ERROR] Enviando datos: {e}")
            return False

        # Paso 3: esperar 250 final con timeout largo (10 min)
        code, _ = self._recv_response(TIMEOUT_DATA_END)
        if code is None:
            print("  [TIMEOUT] Esperando confirmación final del DATA.")
            return False
        ok = code.startswith("2")
        if ok:
            # Limpiar estado tras transacción exitosa
            self._mail_from = None
            self._rcpt_list = []
        else:
            print(f"  [RECHAZADO] Mensaje: {code}")
        return ok

    def cmd_rset(self) -> bool:
        self._send_cmd("RSET")
        code, _ = self._recv_response(TIMEOUT_MAIL_RCPT)
        if code is None:
            print("  [TIMEOUT] RSET sin respuesta.")
            return False
        if code.startswith("2"):
            self._mail_from = None
            self._rcpt_list = []
        return code.startswith("2")

    def cmd_vrfy(self, arg: str) -> bool:
        self._send_cmd(f"VRFY {arg}")
        code, _ = self._recv_response(TIMEOUT_MAIL_RCPT)
        if code is None:
            print("  [TIMEOUT] VRFY sin respuesta.")
            return False
        return True   # 250/252/550 son respuestas válidas

    def cmd_noop(self) -> bool:
        self._send_cmd("NOOP")
        code, _ = self._recv_response(TIMEOUT_MAIL_RCPT)
        if code is None:
            print("  [TIMEOUT] NOOP sin respuesta.")
            return False
        return code.startswith("2")

    def cmd_quit(self):
        self._send_cmd("QUIT")
        self._recv_response(TIMEOUT_MAIL_RCPT)
        self.disconnect()
    
    def cmd_help(self):
        self._send_cmd("HELP")
        self._print_menu()

    # ──────────────────────────────────────────
    #  Helpers de presentación
    # ──────────────────────────────────────────

    def _print_menu(self):
        from_str = self._mail_from or "(no definido)"
        rcpt_str = ", ".join(self._rcpt_list) if self._rcpt_list else "(ninguno)"
        print("\n" + "═" * 60)
        print("  COMANDOS SMTP DISPONIBLES")
        print("─" * 60)
        print("  1) MAIL FROM  — Define el remitente del mensaje (uno solo).")
        print("                  Requerido antes de RCPT TO y DATA.")
        print("  2) RCPT TO    — Agrega un destinatario. Puede usarse varias")
        print("                  veces para enviar a múltiples direcciones.")
        print("  3) DATA       — Envía el cuerpo del mensaje. Requiere que")
        print("                  MAIL FROM y al menos un RCPT TO estén listos.")
        print("                  Escribí el texto y terminá con una línea '.'")
        print("  4) RSET       — Cancela la transacción actual y limpia el")
        print("                  remitente y todos los destinatarios.")
        print("  5) VRFY       — Consulta al servidor si una dirección existe.")
        print("  6) NOOP       — Envía un ping al servidor para verificar que")
        print("                  la conexión sigue activa.")
        print("  7) QUIT       — Cierra la sesión correctamente.")
        print("  8) HELP       — Vuelve a mostar el menú.")
        print("─" * 60)
        print(f"  Estado actual  →  From: {from_str}")
        print(f"                    To:   {rcpt_str}")
        print("═" * 60)

    # ──────────────────────────────────────────
    #  Menú interactivo
    # ──────────────────────────────────────────

    def interactive_session(self):
        if not self.connect():
            return

        # HELO automático al iniciar
        print("[CLIENT] Enviando HELO...")
        if not self.cmd_helo():
            print("[CLIENT] HELO falló. Cerrando.")
            self.disconnect()
            return

        self._print_menu()

        while True:
            try:
                choice = input("\n> Comando [1-7]: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[CLIENT] Interrumpido. Enviando QUIT...")
                self.cmd_quit()
                return

            if choice == "1":  # MAIL FROM
                addr = input("  Remitente (ej: usuario@dominio.com): ").strip()
                if addr:
                    self.cmd_mail_from(addr)

            elif choice == "2":  # RCPT TO
                if not self._mail_from:
                    print("  [ERROR] Primero definí el remitente con MAIL FROM (opción 1).")
                    continue
                addr = input("  Destinatario (ej: destino@dominio.com): ").strip()
                if addr:
                    self.cmd_rcpt_to(addr)

            elif choice == "3":  # DATA
                # Validar precondiciones
                if not self._mail_from:
                    print("  [ERROR] Falta MAIL FROM. Usá la opción 1 primero.")
                    continue
                if not self._rcpt_list:
                    print("  [ERROR] Falta al menos un RCPT TO. Usá la opción 2 primero.")
                    continue
                
                self._send_cmd("DATA")
                
                # Cabeceras automáticas con fecha real
                now     = datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")
                to_hdr  = ", ".join(self._rcpt_list)
                subj    = input("  Asunto del mensaje: ").strip()
                lines   = [
                    f"Date: {now}",
                    f"From: {self._mail_from}",
                    f"To: {to_hdr}",
                    f"Subject: {subj}",
                    "",   # línea en blanco separa cabeceras del cuerpo
                ]

                print("  Escribí el cuerpo. Ingresá '.' en una línea vacía para terminar:\n")
                while True:
                    try:
                        ln = input()
                    except EOFError:
                        break
                    if ln == ".":
                        break
                    lines.append(ln)

                if self.cmd_data(lines):
                    print("  [OK] Mensaje enviado correctamente.")
                    self._print_menu()   # Refrescar estado (se limpió)

            elif choice == "4":  # RSET
                if self.cmd_rset():
                    print("  [OK] Transacción reiniciada. Remitente y destinatarios borrados.")

            elif choice == "5":  # VRFY
                arg = input("  Usuario o dirección a verificar: ").strip()
                if arg:
                    self.cmd_vrfy(arg)

            elif choice == "6":  # NOOP
                if self.cmd_noop():
                    print("  [OK] Conexión activa.")

            elif choice == "7":  # QUIT
                print("[CLIENT] Enviando QUIT...")
                self.cmd_quit()
                print("[CLIENT] Sesión cerrada.")
                return
            
            elif choice == "8":  # HELP
                self.cmd_help()

            else:
                print("  Opción inválida. Ingresá un número del 1 al 7.")


# ══════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════

def main():
    client = SMTPClient(SMTP_HOST, SMTP_PORT)
    client.interactive_session()


if __name__ == "__main__":
    main()