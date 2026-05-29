# Implementación SMTP

Implementación de un servidor y cliente SMTP en Python, basada en RFC 5321 y tomando en cuenta los RFCs anteriores (788, 821/822, 1869 y 2821).
Soporta ESMTP con fallback a SMTP básico y manejo de sesiones concurrentes mediante threads.

---

## Estructura del proyecto

```
.
├── implementacion/
│   ├── mailbox/
│   ├── smtp_common.py
│   ├── smtp_server.py
│   └── smtp_client.py
```
En donde `mailbox/` contiene todos los mensajes o bounces recibidos en formato `.txt`, `smtp_common.py` contiene constantes, utilidades y funciones compartidas, `smtp_server.py` con el servicor principal SMTP que recibe y responde todas las requests y `smtp_client.py` el cual contiene la consola interactiva con algunos comandos SMTP.

---

## Requisitos

- Python 3.10 o superior
- Sin dependencias externas

---

## Ejecución

### Servidor

Para ejecutar el servidor:

```bash
python -m implementacion.smtp_server
```

o desde /implementacion

```bash
python -m smtp_server
```

El servidor queda escuchando en `127.0.0.1:2525`.
Los mensajes aceptados se guardan en `mailbox/` como archivos `.txt`.
Importante abrir una consola Servidor antes de abrir cualquier consola Cliente

### Cliente

Para ejecutar el Cliente (en otra terminal):

```bash
python -m implementacion.smtp_client
```

o desde /implementacion

```bash
python -m smtp_client
```

Se abre una sesión interactiva con el menú de comandos disponibles.

---

## Comandos disponibles (cliente)

Los comandos principales del protocolo SMTP incluyen `MAIL FROM`, que le permite al cliente definir el remitente. `RCPT TO`, que permite al cliente definir uno o mas recipients / destinatarios (hasta un máximo de 100). `DATA`, que le permite al cliente escribir y enviar el cuerpo del correo. `RSET`, que reinicia la información de la transacción en curso; `VRFY`, que consulta si una dirección de correo existe en la lista de buzones reservados o si es un formato valido de correo. `NOOP`, que verifica del lado del cliente que la conexión siga activa sin realizar ningún cambio. `QUIT`, que termina la sesión con el servidor (Cierra solo el Cliente). y `HELP`, que muestra información de ayuda disponible sobre codigos numéricos y comandos. Ademas de `HELO` y `EHLO` que se utilizan para establecer la conexion entre Cliente - Servidor, sus diferencias se explican en el informe.

### Dot-Stuffing 
Durante el envío del cuerpo del mensaje, el cliente aplica dot-stuffing sobre las líneas del contenido antes de transmitirlas. El servidor revierte este proceso al momento de almacenar el mensaje.

---

## Límites configurados

El sistema utiliza automaticamente el puerto 2525 para la comunicación y permite tener un máximo de 100 destinatarios/recipients por mensaje. El tamaño máximo de cada correo es de 10MB y la longitud máxima permitida por línea es de 998 caracteres. De superar alguno de estos limites, se cancelara el envio del mail y se guardara un bounce en `mailbox/`.

---

## Timeouts

El sistema cuenta con un sistema de timeouts según la etapa de la sesión. Para los comandos `HELO` y `EHLO` y para el saludo incial se cuenta con un timeout de 300 segundos, al igual que para el comando `RCPT TO` y `MAIL FROM`. Para el comando `DATA` se cuentan con tres tipos de timeouts, al iniciarlo se esperan 120 segundos para recibir el código numerico 354, al comenzar a escribir el cuerpo se cuentan con 180 segundos y, por ultimo, se esperan 600 segundos para recibir el 250 final.

---

## Buzones reservados

Los siguientes buzones son aceptados automáticamente sin intervención del operador:

`ipineda`, `ifalcone`, `rpodazza`, `postmaster`, `abuse`

Estos pueden ser agregadoso modificados en el archivo `smtp_common.py`

---

## Comportamiento del operador (servidor)

Durante la ejecución, el servidor solicita confirmación por consola para:

- Aceptar o rechazar cada destinatario (excepto buzones reservados)
- Aceptar o rechazar el mensaje recibido tras `DATA`

Los mensajes aceptados por el operador generan un archivo `msg_*.txt` en `mailbox/`. Mientras que, los mensajes rechazados generan un archivo `bounce_*.txt`. Este tendria informacion similar aun mail aceptado pero informando que fue rechazado.

---

## Ejemplo de comunicación

```bash
  [SERVER] Nueva conexión de 127.0.0.1:56836
  S: 220 localhost Servicio SMTP listo
  C: EHLO cliente.local
  S: 250-localhost Saluda a cliente.local
  S: 250-SIZE 10485760
  S: 250-8BITMIME
  S: 250-VRFY
  S: 250 HELP
  C: MAIL FROM:<ifalcone@alumno.huergo.edu.ar> SIZE=10485760
  S: 250 Remitente aceptado: ifalcone@alumno.huergo.edu.ar (SIZE anunciado: 10485760B)
  C: RCPT TO:<rpodazza@alumno.huergo.edu.ar>
  S: 250 Destinatario aceptado: rpodazza@alumno.huergo.edu.ar
  C: RCPT TO:<ipineda@alumno.huergo.edu.ar>
  S: 250 Destinatario aceptado: ipineda@alumno.huergo.edu.ar
  C: DATA
  S: 354 Inicio de datos; terminá con <CRLF>.<CRLF>
  [OPERADOR] ¿Aceptar mensaje de 'ifalcone@alumno.huergo.edu.ar' para ['rpodazza@alumno.huergo.edu.ar', 'ipineda@alumno.huergo.edu.ar'] (8 líneas, 178B)? (y/n): y
  [STORE] → C:\Users\Usuario\Tp2-Administracion-de-sistemas-y-redes\implementacion\mailbox\msg_20260529_024820_111035.txt
  S: 250 Mensaje aceptado. ID: <20260529024815.c2d1629ad0eb@localhost>
  C: QUIT
  S: 221 localhost Cerrando conexión. Hasta luego.
  [SERVER] Sesión cerrada — 127.0.0.1:56836
```