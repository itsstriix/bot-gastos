import os
import sys
import threading
import asyncio
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
from supabase import create_client, Client


# ============================================================
# CONFIGURACIÓN
# ============================================================

print("=== Iniciando bot ===")

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")

print("TELEGRAM_TOKEN existe:", bool(TELEGRAM_TOKEN))
print("GEMINI_API_KEY existe:", bool(GEMINI_API_KEY))
print("SUPABASE_URL existe:", bool(SUPABASE_URL))
print("SUPABASE_SECRET_KEY existe:", bool(SUPABASE_SECRET_KEY))


# ============================================================
# COMPROBAR VARIABLES
# ============================================================

if not TELEGRAM_TOKEN:
    print("ERROR: Falta TELEGRAM_TOKEN")
    sys.exit(1)

if not GEMINI_API_KEY:
    print("ERROR: Falta GEMINI_API_KEY")
    sys.exit(1)

if not SUPABASE_URL:
    print("ERROR: Falta SUPABASE_URL")
    sys.exit(1)

if not SUPABASE_SECRET_KEY:
    print("ERROR: Falta SUPABASE_SECRET_KEY")
    sys.exit(1)


# ============================================================
# CLIENTES
# ============================================================

client = genai.Client(
    api_key=GEMINI_API_KEY
)

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SECRET_KEY
)

# Modelo de Gemini
GEMINI_MODEL = "gemini-3.5-flash-lite"


# ============================================================
# PROMPT DE GEMINI
# ============================================================

SYSTEM_PROMPT = """
Sos un asistente de control de gastos personales en pesos argentinos.

Analizá el mensaje del usuario y respondé SOLO con un JSON válido.
NO agregues explicaciones.
NO uses Markdown.
NO uses ```json.
NO escribas texto fuera del JSON.

Si el usuario está registrando un gasto:

{
  "accion": "registrar",
  "monto": 30000,
  "categoria": "combustible",
  "descripcion": "nafta"
}

Si el usuario está consultando gastos:

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

REGLAS:

1. El monto siempre debe ser un número.
2. Si el usuario dice nafta, combustible, nafta para la moto, etc.,
   usar "combustible".
3. Si dice comida, restaurante, delivery, almuerzo, cena,
   desayuno, etc., usar "comida".
4. Si dice supermercado, usar "supermercado".
5. Si no se puede determinar la categoría, usar "otros".
6. Si pregunta cuánto gastó hoy:
   "periodo": "hoy"
7. Si pregunta cuánto gastó este mes:
   "periodo": "mes"
8. Si pregunta por una categoría específica,
   completar "categoria".
9. Si pregunta por el total sin especificar categoría,
   usar "categoria": null.
10. Nunca inventes un monto.
"""


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

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
# GEMINI
# ============================================================

async def consultar_gemini(texto):

    prompt = (
        SYSTEM_PROMPT
        + "\n\nMensaje del usuario:\n"
        + texto
    )

    ultimo_error = None

    for intento in range(3):

        try:

            response = await asyncio.to_thread(
                client.models.generate_content,
                model=GEMINI_MODEL,
                contents=prompt
            )

            if not response or not response.text:
                raise Exception(
                    "Gemini devolvió una respuesta vacía."
                )

            respuesta_texto = response.text.strip()

            # Eliminar Markdown si Gemini lo agrega
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

            data = json.loads(respuesta_texto)

            return data

        except Exception as e:

            ultimo_error = e

            error_texto = str(e)

            print(
                f"Gemini error "
                f"(intento {intento + 1}/3): "
                f"{error_texto}"
            )

            if (
                "503" in error_texto
                or "UNAVAILABLE" in error_texto
            ):

                if intento < 2:

                    espera = 2 ** intento

                    print(
                        f"Reintentando en "
                        f"{espera} segundos..."
                    )

                    await asyncio.sleep(espera)

                    continue

            break

    raise ultimo_error


# ============================================================
# GUARDAR GASTO EN SUPABASE
# ============================================================

async def guardar_gasto(
    user_id,
    monto,
    categoria,
    descripcion
):

    fecha = date.today().isoformat()

    datos = {
        "user_id": user_id,
        "monto": monto,
        "categoria": categoria,
        "descripcion": descripcion,
        "fecha": fecha
    }

    print("Guardando gasto en Supabase:", datos)

    response = await asyncio.to_thread(
        lambda: supabase
        .table("gastos")
        .insert(datos)
        .execute()
    )

    print(
        "Gasto guardado correctamente:",
        response.data
    )

    return response.data


# ============================================================
# OBTENER GASTOS
# ============================================================

async def obtener_gastos(
    user_id,
    periodo,
    categoria=None
):

    hoy = date.today()

    if periodo == "hoy":

        fecha_desde = hoy.isoformat()
        fecha_hasta = hoy.isoformat()

    else:

        # Mes actual
        fecha_desde = hoy.replace(
            day=1
        ).isoformat()

        # Primer día del mes siguiente
        if hoy.month == 12:

            siguiente_mes = hoy.replace(
                year=hoy.year + 1,
                month=1,
                day=1
            )

        else:

            siguiente_mes = hoy.replace(
                month=hoy.month + 1,
                day=1
            )

        fecha_hasta = siguiente_mes.isoformat()

    def ejecutar_consulta():

        query = (
            supabase
            .table("gastos")
            .select("monto, categoria, descripcion, fecha")
            .eq("user_id", user_id)
            .gte("fecha", fecha_desde)
            .lt("fecha", fecha_hasta)
        )

        if categoria:
            query = query.eq(
                "categoria",
                categoria
            )

        return query.execute()

    response = await asyncio.to_thread(
        ejecutar_consulta
    )

    return response.data or []


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
        # GEMINI
        # ----------------------------------------------------

        data = await consultar_gemini(texto)

        print(
            "Respuesta de Gemini:",
            data
        )

        accion = data.get("accion")

        # ====================================================
        # REGISTRAR
        # ====================================================

        if accion == "registrar":

            if "monto" not in data:

                await update.message.reply_text(
                    "No pude determinar el monto."
                )

                return

            monto = float(
                data["monto"]
            )

            categoria = (
                data.get(
                    "categoria",
                    "otros"
                )
                or "otros"
            ).lower()

            descripcion = (
                data.get(
                    "descripcion",
                    ""
                )
                or ""
            )

            # Guardar en Supabase
            await guardar_gasto(
                user_id=user_id,
                monto=monto,
                categoria=categoria,
                descripcion=descripcion
            )

            await update.message.reply_text(
                "✅ Gasto registrado\n\n"
                f"💰 Monto: ${monto:,.0f}\n"
                f"📂 Categoría: {categoria}\n"
                f"📝 Descripción: {descripcion}"
            )

        # ====================================================
        # CONSULTAR
        # ====================================================

        elif accion == "consultar":

            categoria = data.get(
                "categoria"
            )

            periodo = data.get(
                "periodo",
                "mes"
            )

            gastos = await obtener_gastos(
                user_id=user_id,
                periodo=periodo,
                categoria=categoria
            )

            total = sum(
                float(gasto["monto"])
                for gasto in gastos
            )

            cantidad = len(gastos)

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

        # ====================================================
        # ACCIÓN DESCONOCIDA
        # ====================================================

        else:

            await update.message.reply_text(
                "No pude interpretar lo que querés hacer."
            )

    except json.JSONDecodeError:

        print(
            "ERROR: Gemini no devolvió JSON válido."
        )

        await update.message.reply_text(
            "No pude interpretar la respuesta "
            "de la IA. Probá escribirlo de otra manera."
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

class HealthHandler(
    BaseHTTPRequestHandler
):

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

    def log_message(
        self,
        format,
        *args
    ):

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
        f"Servidor web escuchando "
        f"en puerto {port}"
    )

    server.serve_forever()


# ============================================================
# MAIN
# ============================================================

def main():

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