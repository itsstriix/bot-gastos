import os
import sys
import threading
import asyncio
import sqlite3
import json
import re
from datetime import date
from http.server import HTTPServer, BaseHTTPRequestHandler

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from google import genai


# ============================================================
# CONFIGURACIÓN
# ============================================================

print("=== Iniciando bot ===")

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

print("TELEGRAM_TOKEN existe:", bool(TELEGRAM_TOKEN))
print("GEMINI_API_KEY existe:", bool(GEMINI_API_KEY))

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    print("ERROR: Faltan las variables de entorno")
    sys.exit(1)


# ============================================================
# GEMINI
# ============================================================

client = genai.Client(api_key=GEMINI_API_KEY)

# Modelo actual y estable para este tipo de tarea
GEMINI_MODEL = "gemini-3.5-flash-lite"


# ============================================================
# BASE DE DATOS
# ============================================================

def init_db():
    conn = sqlite3.connect("gastos.db")
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS gastos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            monto REAL,
            categoria TEXT,
            descripcion TEXT,
            fecha TEXT
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# INSTRUCCIONES PARA GEMINI
# ============================================================

SYSTEM_PROMPT = """
Sos un asistente de control de gastos personales en pesos argentinos.

Analizá el mensaje del usuario y respondé SOLO con un JSON válido.
NO agregues explicaciones, Markdown, ```json ni texto fuera del JSON.

Si el usuario está registrando un gasto, respondé exactamente con esta estructura:

{
  "accion": "registrar",
  "monto": 30000,
  "categoria": "combustible",
  "descripcion": "nafta"
}

Si el usuario está consultando gastos, respondé:

{
  "accion": "consultar",
  "tipo": "mes",
  "categoria": "combustible",
  "periodo": "mes"
}

Categorías posibles:
- combustible
- comida
- supermercado
- transporte
- ocio
- salud
- servicios
- ropa
- otros

Reglas:

1. El monto siempre debe ser un número.
2. Si el usuario dice "nafta", "combustible", "combustible para la moto", etc., usar "combustible".
3. Si dice comida, restaurante, delivery, almuerzo, cena, desayuno, etc., usar "comida".
4. Si dice supermercado, usar "supermercado".
5. Si no se puede determinar la categoría, usar "otros".
6. Si el usuario pregunta cuánto gastó hoy, usar:
   "periodo": "hoy"
7. Si pregunta cuánto gastó este mes, usar:
   "periodo": "mes"
8. Si pregunta por una categoría específica, completar "categoria".
9. Si pregunta por el total sin especificar categoría, usar "categoria": null.
10. Nunca inventes un monto.
"""


# ============================================================
# TELEGRAM / START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "Hola! Soy tu bot de gastos. 💰\n\n"
        "Podés escribirme cosas como:\n\n"
        "• Gasté 30000 en combustible\n"
        "• Gasté 15000 en supermercado\n"
        "• Gasté 8000 en comida\n"
        "• Cuánto gasté hoy?\n"
        "• Cuánto gasté este mes?\n"
        "• Cuánto gasté en combustible este mes?"
    )


# ============================================================
# CONSULTA A GEMINI
# ============================================================

async def consultar_gemini(texto):

    prompt = (
        SYSTEM_PROMPT
        + "\n\nMensaje del usuario:\n"
        + texto
    )

    ultimo_error = None

    # Reintentos para errores temporales como 503
    for intento in range(3):

        try:

            response = await asyncio.to_thread(
                client.models.generate_content,
                model=GEMINI_MODEL,
                contents=prompt
            )

            if not response or not response.text:
                raise Exception("Gemini devolvió una respuesta vacía.")

            respuesta_texto = response.text.strip()

            # Eliminar posibles bloques Markdown
            respuesta_texto = re.sub(
                r"^```json\s*",
                "",
                respuesta_texto,
                flags=re.IGNORECASE
            )

            respuesta_texto = re.sub(
                r"^```\s*",
                "",
                respuesta_texto
            )

            respuesta_texto = re.sub(
                r"\s*```$",
                "",
                respuesta_texto
            )

            respuesta_texto = respuesta_texto.strip()

            # Convertir a JSON
            data = json.loads(respuesta_texto)

            return data

        except Exception as e:

            ultimo_error = e

            error_texto = str(e)

            print(
                f"Error Gemini - intento {intento + 1}/3: "
                f"{error_texto}"
            )

            # Si es un error temporal, esperar antes de reintentar
            if "503" in error_texto or "UNAVAILABLE" in error_texto:

                if intento < 2:
                    espera = 2 ** intento

                    print(
                        f"Gemini no disponible. "
                        f"Reintentando en {espera} segundos..."
                    )

                    await asyncio.sleep(espera)

                    continue

            # Otros errores no necesitan 3 intentos
            break

    raise ultimo_error


# ============================================================
# MANEJAR MENSAJES
# ============================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id
    texto = update.message.text

    if not texto:
        await update.message.reply_text(
            "No pude leer el mensaje."
        )
        return

    print(
        f"Mensaje recibido de {user_id}: {texto}"
    )

    try:

        # ----------------------------------------------------
        # Consultar Gemini
        # ----------------------------------------------------

        data = await consultar_gemini(texto)

        print("Respuesta de Gemini:", data)

        accion = data.get("accion")

        # ----------------------------------------------------
        # REGISTRAR GASTO
        # ----------------------------------------------------

        if accion == "registrar":

            monto = float(data["monto"])

            categoria = (
                data.get("categoria", "otros")
                or "otros"
            ).lower()

            descripcion = (
                data.get("descripcion", "")
                or ""
            )

            fecha = date.today().isoformat()

            conn = sqlite3.connect("gastos.db")
            c = conn.cursor()

            c.execute(
                """
                INSERT INTO gastos
                (
                    user_id,
                    monto,
                    categoria,
                    descripcion,
                    fecha
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    monto,
                    categoria,
                    descripcion,
                    fecha
                )
            )

            conn.commit()
            conn.close()

            await update.message.reply_text(
                "✅ Gasto registrado\n\n"
                f"💰 Monto: ${monto:,.0f}\n"
                f"📂 Categoría: {categoria}\n"
                f"📝 Descripción: {descripcion}"
            )

        # ----------------------------------------------------
        # CONSULTAR GASTOS
        # ----------------------------------------------------

        elif accion == "consultar":

            categoria = data.get("categoria")
            periodo = data.get("periodo", "mes")

            conn = sqlite3.connect("gastos.db")
            c = conn.cursor()

            # -----------------------------------------------
            # HOY
            # -----------------------------------------------

            if periodo == "hoy":

                hoy = date.today().isoformat()

                if categoria:

                    c.execute(
                        """
                        SELECT SUM(monto), COUNT(*)
                        FROM gastos
                        WHERE user_id = ?
                        AND fecha = ?
                        AND categoria = ?
                        """,
                        (
                            user_id,
                            hoy,
                            categoria
                        )
                    )

                else:

                    c.execute(
                        """
                        SELECT SUM(monto), COUNT(*)
                        FROM gastos
                        WHERE user_id = ?
                        AND fecha = ?
                        """,
                        (
                            user_id,
                            hoy
                        )
                    )

            # -----------------------------------------------
            # MES
            # -----------------------------------------------

            else:

                mes = date.today().strftime("%Y-%m")

                if categoria:

                    c.execute(
                        """
                        SELECT SUM(monto), COUNT(*)
                        FROM gastos
                        WHERE user_id = ?
                        AND fecha LIKE ?
                        AND categoria = ?
                        """,
                        (
                            user_id,
                            mes + "%",
                            categoria
                        )
                    )

                else:

                    c.execute(
                        """
                        SELECT SUM(monto), COUNT(*)
                        FROM gastos
                        WHERE user_id = ?
                        AND fecha LIKE ?
                        """,
                        (
                            user_id,
                            mes + "%"
                        )
                    )

            resultado = c.fetchone()

            conn.close()

            total = resultado[0] or 0
            cantidad = resultado[1] or 0

            # -----------------------------------------------
            # RESPUESTA
            # -----------------------------------------------

            if periodo == "hoy":
                titulo = "📊 Gastos de hoy"
            else:
                titulo = "📊 Gastos de este mes"

            texto_respuesta = (
                f"{titulo}\n\n"
                f"💰 Total: ${total:,.0f}\n"
                f"🧾 Cantidad de gastos: {cantidad}"
            )

            if categoria:
                texto_respuesta += (
                    f"\n📂 Categoría: {categoria}"
                )

            await update.message.reply_text(
                texto_respuesta
            )

        # ----------------------------------------------------
        # ACCIÓN DESCONOCIDA
        # ----------------------------------------------------

        else:

            await update.message.reply_text(
                "No pude interpretar lo que querés hacer."
            )

    except json.JSONDecodeError:

        print("ERROR: Gemini no devolvió JSON válido.")

        await update.message.reply_text(
            "No pude interpretar la respuesta de la IA. "
            "Probá escribirlo de otra manera."
        )

    except Exception as e:

        print(
            f"ERROR procesando mensaje: {str(e)}"
        )

        await update.message.reply_text(
            f"Error: {str(e)}"
        )


# ============================================================
# SERVIDOR WEB PARA RENDER
# ============================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)
        self.send_header(
            "Content-type",
            "text/plain"
        )
        self.end_headers()

        self.wfile.write(
            b"Bot is running"
        )

    def log_message(self, format, *args):
        return


def run_web_server():

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(
        f"Servidor web escuchando en puerto {port}"
    )

    server.serve_forever()


# ============================================================
# MAIN
# ============================================================

def main():

    # Servidor web para mantener Render satisfecho
    threading.Thread(
        target=run_web_server,
        daemon=True
    ).start()

    print(
        "Creando Application de Telegram..."
    )

    app = (
        Application
        .builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    print(
        "Bot listo. Iniciando polling..."
    )

    app.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# EJECUTAR
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.get_event_loop()

    except RuntimeError:

        loop = asyncio.new_event_loop()

        asyncio.set_event_loop(loop)

    main()
