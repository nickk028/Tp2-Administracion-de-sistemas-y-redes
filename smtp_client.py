"""
smtp_client.py — Cliente SMTP según RFC 5321
Uso: python smtp_client.py

Flujo:
  1. Conecta al servidor y espera el 220.
  2. Envía EHLO y guarda las extensiones del servidor.
     Si el servidor no soporta EHLO, hace fallback automático a HELO.
  3. Menú interactivo: MAIL FROM, RCPT TO, DATA, RSET, VRFY, NOOP, HELP, QUIT.
  4. Timeouts individuales por fase según RFC 5321 §4.5.3.2.
"""

import socket
import datetime

from smtp_common import (
    SMTP_HOST, SMTP_PORT,
    TIMEOUT_GREETING, TIMEOUT_MAIL_RCPT,
    TIMEOUT_DATA_INIT, TIMEOUT_DATA_BLOCK, TIMEOUT_DATA_END,
    MAX_MESSAGE_SIZE, MAX_LINE_LENGTH,
    encode_line, decode_line,
    recv_line_buffered,          # mejora #9: lectura por buffer
    dot_stuff,
)

CLIENT_DOMAIN = "cliente.local"


# ══════════════════════════════════════════════
#  Cliente SMTP
# ══════════════════════════════════════════════

class SMTPClient:

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None
        # Estado de transacción
        self._mail_from: str | None  = None
        self._rcpt_list: list[str]   = []
        # Extensiones anunciadas por el servidor tras EHLO
        self.extensions: dict[str, str] = {}
        # ¿Se negoció ESMTP? (False si se cayó a HELO)  — mejora #10
        self._using_esmtp = False
        # Buffer de lectura — mejora #9
        self._recv_buf = bytearray()

    # ──────────────────────────────────────────
    #  Conexión
    # ──────────────────────────────────────────

    def connect(self) -> bool:
        print(f"[CLIENT] Conectando a {self.host}:{self.port}...")
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.sock.connect((self.host, self.port))
        except ConnectionRefusedError:
            print("[CLIENT] ERROR: Conexión rechazada. ¿Está el servidor corriendo?")
            return False

        code, msg = self._recv_response(TIMEOUT_GREETING)
        if code is None:
            print("[CLIENT] TIMEOUT: no se recibió el saludo 220.")
            self.sock.close()
            return False
        if not code.startswith("2"):
            print(f"[CLIENT] Servidor no disponible: {code} {msg}")
            self.sock.close()
            return False

        self._mail_from   = None
        self._rcpt_list   = []
        self._recv_buf    = bytearray()
        print("[CLIENT] Conectado.")
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
        print(f"  C: {line}")
        self.sock.sendall(encode_line(line))

    def _recv_response(self, timeout: float) -> tuple[str | None, str]:
        """
        Lee una respuesta SMTP completa (posiblemente multi-línea).
        Mejora #9: usa recv_line_buffered en vez de recv(1).
        """
        self.sock.settimeout(timeout)
        lines = []
        code  = None
        try:
            while True:
                line, self._recv_buf = recv_line_buffered(self.sock, self._recv_buf)
                if line is None:
                    return None, ""
                lines.append(line)
                if len(line) >= 3:
                    code = line[:3]
                    if len(line) == 3 or line[3] == " ":
                        break
        except socket.timeout:
            print(f"  [TIMEOUT] ({timeout}s) sin respuesta.")
            return None, ""
        except OSError as e:
            print(f"  [ERROR] Red: {e}")
            return None, ""

        full = "\n  S: ".join(lines)
        print(f"  S: {full}")
        return code, "\n".join(lines)

    # ──────────────────────────────────────────
    #  EHLO con fallback a HELO — mejora #10
    # ──────────────────────────────────────────

    def cmd_ehlo(self) -> bool:
        """
        Intenta EHLO primero (RFC 5321 §4.1.1.1).
        Si el servidor responde 500/502 (no lo entiende), cae
        automáticamente a HELO — mejora #10.
        """
        self._send_cmd(f"EHLO {CLIENT_DOMAIN}")
        code, full = self._recv_response(TIMEOUT_MAIL_RCPT)

        if code is None:
            print("  [TIMEOUT] EHLO sin respuesta.")
            return False

        # Mejora #10: fallback automático a HELO si el servidor no soporta EHLO
        if code in ("500", "502"):
            print("  [INFO] Servidor no soporta EHLO, usando HELO como fallback...")
            return self._cmd_helo_fallback()

        if not code.startswith("2"):
            print(f"  [RECHAZADO] EHLO: {code}")
            return False

        # Parsear extensiones de la respuesta multi-línea
        self.extensions   = {}
        self._using_esmtp = True
        for line in full.split("\n"):
            line = line.strip()
            if len(line) > 4 and line[:3] == "250":
                kw_part = line[4:].strip()
                parts   = kw_part.split(None, 1)
                if parts:
                    kw    = parts[0].upper()
                    param = parts[1] if len(parts) > 1 else ""
                    self.extensions[kw] = param

        if self.extensions:
            print(f"  [EHLO] Extensiones: {list(self.extensions.keys())}")

        # Leer límite de tamaño desde la extensión SIZE anunciada — mejora #2
        if "SIZE" in self.extensions:
            try:
                server_max = int(self.extensions["SIZE"])
                print(f"  [EHLO] Tamaño máximo del servidor: {server_max // 1024 // 1024}MB")
            except ValueError:
                pass
        return True

    def _cmd_helo_fallback(self) -> bool:
        """HELO básico usado como fallback si EHLO falla."""
        self._send_cmd(f"HELO {CLIENT_DOMAIN}")
        code, _ = self._recv_response(TIMEOUT_MAIL_RCPT)
        if code is None:
            print("  [TIMEOUT] HELO sin respuesta.")
            return False
        ok = code.startswith("2")
        if ok:
            self._using_esmtp = False
            self.extensions   = {}
            print("  [INFO] Sesión establecida en modo SMTP básico (sin extensiones).")
        else:
            print(f"  [RECHAZADO] HELO: {code}")
        return ok

    # ──────────────────────────────────────────
    #  Comandos SMTP
    # ──────────────────────────────────────────

    def cmd_mail_from(self, addr: str) -> bool:
        if self._mail_from is not None:
            print(f"  [INFO] Remitente ya definido: {self._mail_from}. Usá RSET para reiniciar.")
            return False

        # Mejora #2: incluir SIZE= en MAIL FROM si el servidor soporta la extensión
        size_param = ""
        if self._using_esmtp and "SIZE" in self.extensions:
            size_param = f" SIZE={MAX_MESSAGE_SIZE}"   # estimamos el máximo como cota

        self._send_cmd(f"MAIL FROM:<{addr}>{size_param}")
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
            print(f"  [INFO] Destinatarios confirmados: {self._rcpt_list}")
        else:
            print(f"  [RECHAZADO] RCPT TO: {code}")
        return ok

    def cmd_data(self) -> bool:
        """
        Envía DATA → recibe 354 → pide asunto y cuerpo → envía con dot-stuffing.
        Mejoras aplicadas:
        #4 — valida longitud de línea antes de enviar
        #2 — advierte si el mensaje supera el límite del servidor
        #8 — las cabeceras automáticas incluyen el estado real de la sesión
        """
        self._send_cmd("DATA")
        code, _ = self._recv_response(TIMEOUT_DATA_INIT)
        if code is None:
            print("  [TIMEOUT] Esperando 354.")
            return False
        if not code.startswith("3"):
            print(f"  [RECHAZADO] DATA: {code}")
            return False

        # Cabeceras automáticas con fecha real
        now    = datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000")
        to_hdr = ", ".join(self._rcpt_list)
        subj   = input("  Asunto del mensaje: ").strip()
        lines  = [
            f"Date: {now}",
            f"From: {self._mail_from}",
            f"To: {to_hdr}",
            f"Subject: {subj}",
            "",
        ]
        print("  Escribí el cuerpo. Ingresá '.' en una línea vacía para terminar:\n")
        while True:
            try:
                ln = input()
            except EOFError:
                break
            if ln == ".":
                break

            # Mejora #4: advertir si la línea supera el límite RFC
            if len(ln) > MAX_LINE_LENGTH:
                print(f"  [AVISO] Línea demasiado larga ({len(ln)} chars, máx {MAX_LINE_LENGTH}). "
                      f"El servidor puede rechazarla.")
            lines.append(ln)

        # Mejora #2: calcular tamaño total y advertir si supera el límite
        total = sum(len(l) + 2 for l in lines)   # +2 por CRLF
        server_max = int(self.extensions.get("SIZE", MAX_MESSAGE_SIZE) or MAX_MESSAGE_SIZE)
        if total > server_max:
            print(f"  [AVISO] El mensaje ({total}B) supera el límite del servidor ({server_max}B).")
            confirm = input("  ¿Enviarlo igual? (y/n): ").strip().lower()
            if confirm != "y":
                # Cancelar: drenar con RSET
                self._send_cmd("RSET")
                self._recv_response(TIMEOUT_MAIL_RCPT)
                self._mail_from = None
                self._rcpt_list = []
                print("  [INFO] Envío cancelado, transacción reiniciada.")
                return False

        # Enviar cuerpo con dot-stuffing
        self.sock.settimeout(TIMEOUT_DATA_BLOCK)
        stuffed = dot_stuff(lines)
        try:
            for ln in stuffed:
                self.sock.sendall(encode_line(ln))
            self.sock.sendall(encode_line("."))
            print("  C: .")
        except socket.timeout:
            print("  [TIMEOUT] Enviando datos.")
            return False
        except OSError as e:
            print(f"  [ERROR] Enviando datos: {e}")
            return False

        code, _ = self._recv_response(TIMEOUT_DATA_END)
        if code is None:
            print("  [TIMEOUT] Esperando confirmación final.")
            return False
        ok = code.startswith("2")
        if ok:
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
            # Mejora #6: RSET limpia transacción pero NO el estado EHLO/HELO
            self._mail_from = None
            self._rcpt_list = []
        return code.startswith("2")

    def cmd_vrfy(self, arg: str) -> bool:
        self._send_cmd(f"VRFY {arg}")
        code, _ = self._recv_response(TIMEOUT_MAIL_RCPT)
        if code is None:
            print("  [TIMEOUT] VRFY sin respuesta.")
            return False
        return True

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

    def cmd_help(self) -> bool:
        self._send_cmd("HELP")
        code, _ = self._recv_response(TIMEOUT_MAIL_RCPT)
        if code is None:
            print("  [TIMEOUT] HELP sin respuesta.")
            return False
        return code.startswith("2")

    # ──────────────────────────────────────────
    #  Menú
    # ──────────────────────────────────────────

    def _print_menu(self):
        from_str  = self._mail_from or "(no definido)"
        rcpt_str  = ", ".join(self._rcpt_list) if self._rcpt_list else "(ninguno)"
        mode_str  = "ESMTP (extensiones activas)" if self._using_esmtp else "SMTP básico"
        print("\n" + "═" * 62)
        print("  COMANDOS SMTP DISPONIBLES")
        print("─" * 62)
        print("  1) MAIL FROM  — Define el remitente (uno solo).")
        print("                  Requerido antes de RCPT TO y DATA.")
        print("  2) RCPT TO    — Agrega un destinatario (máximo 100).")
        print("                  Puede repetirse para múltiples destinos.")
        print("  3) DATA       — Envía el mensaje. Requiere MAIL FROM y")
        print("                  al menos un RCPT TO. Terminá con '.'")
        print("  4) RSET       — Cancela la transacción actual. Limpia")
        print("                  remitente y destinatarios (no el saludo).")
        print("  5) VRFY       — Consulta si una dirección existe.")
        print("  6) NOOP       — Verifica que la conexión sigue activa.")
        print("  7) QUIT       — Cierra la sesión correctamente.")
        print("  8) HELP       — Muestra la ayuda del servidor.")
        print("─" * 62)
        print(f"  Modo          →  {mode_str}")
        print(f"  From          →  {from_str}")
        print(f"  To            →  {rcpt_str}")
        print("═" * 62)

    def interactive_session(self):
        if not self.connect():
            return

        print("[CLIENT] Enviando EHLO...")
        if not self.cmd_ehlo():
            print("[CLIENT] No se pudo establecer sesión. Cerrando.")
            self.disconnect()
            return

        self._print_menu()

        while True:
            try:
                choice = input("\n> Comando [1-8]: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[CLIENT] Interrumpido. Enviando QUIT...")
                self.cmd_quit()
                return

            if choice == "1":
                addr = input("  Remitente (ej: usuario@dominio.com): ").strip()
                if addr:
                    self.cmd_mail_from(addr)

            elif choice == "2":
                if not self._mail_from:
                    print("  [ERROR] Primero definí el remitente con MAIL FROM (opción 1).")
                    continue
                addr = input("  Destinatario (ej: destino@dominio.com): ").strip()
                if addr:
                    self.cmd_rcpt_to(addr)

            elif choice == "3":
                if not self._mail_from:
                    print("  [ERROR] Falta MAIL FROM. Usá la opción 1 primero.")
                    continue
                if not self._rcpt_list:
                    print("  [ERROR] Falta al menos un RCPT TO. Usá la opción 2 primero.")
                    continue
                if self.cmd_data():
                    print("  [OK] Mensaje enviado correctamente.")
                    self._print_menu()

            elif choice == "4":
                if self.cmd_rset():
                    print("  [OK] Transacción reiniciada (saludo EHLO/HELO conservado).")

            elif choice == "5":
                arg = input("  Dirección a verificar: ").strip()
                if arg:
                    self.cmd_vrfy(arg)

            elif choice == "6":
                if self.cmd_noop():
                    print("  [OK] Conexión activa.")

            elif choice == "7":
                print("[CLIENT] Enviando QUIT...")
                self.cmd_quit()
                print("[CLIENT] Sesión cerrada.")
                return

            elif choice == "8":
                self.cmd_help()

            else:
                print("  Opción inválida. Ingresá un número del 1 al 8.")


def main():
    SMTPClient(SMTP_HOST, SMTP_PORT).interactive_session()


if __name__ == "__main__":
    main()
