import os
import sys
print("=== Iniciando bot ===")
print("Python version:", sys.version)

from dotenv import load_dotenv
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

print("TELEGRAM_TOKEN existe:", bool(TELEGRAM_TOKEN))
print("GEMINI_API_KEY existe:", bool(GEMINI_API_KEY))

if not TELEGRAM_TOKEN:
    print("ERROR: Falta TELEGRAM_TOKEN")
    sys.exit(1)

if not GEMINI_API_KEY:
    print("ERROR: Falta GEMINI_API_KEY")
    sys.exit(1)

import sqlite3
from datetime import date
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
import json
import re
import asyncio

print("Librerias importadas correctamente")

client = genai.Client(api_key=GEMINI_API_KEY)
print("Cliente de Gemini creado")

def init_db():
    conn = sqlite3.connect("gastos.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS gastos
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  monto REAL,
                  categoria TEXT,
                  descripcion TEXT,
                  fecha TEXT)''')
    conn.commit()
    conn.close()
    print("Base de datos lista")

init_db()

SYSTEM_PROMPT = """
Sos un asistente de control de gastos personales en pesos argentinos.
Analizá el mensaje del usuario y respondé SOLO con un JSON válido, sin texto extra.

Si el usuario está registrando un gasto, respondé exactamente así:
{"accion": "registrar", "monto": 30000, "categoria": "combustible", "descripcion": "nafta"}

Si está consultando, respondé así:
{"accion": "consultar", "tipo": "mes", "categoria": "combustible", "periodo": "hoy"}

Categorías posibles: combustible, comida, supermercado, transporte, ocio, salud, servicios, ropa, otros.
Monto siempre en número.
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hola! Soy tu bot de gastos.\n\n"
        "Escribime:\n"
        "- Gaste 30000 en combustible\n"
        "- 15000 supermercado\n"
        "- Cuanto gaste este mes?"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    texto = update.message.text

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=SYSTEM_PROMPT + "\n\nMensaje del usuario: " + texto
        )
        
        respuesta_texto = response.text.strip()
        respuesta_texto = re.sub(r"```json|```", "", respuesta_texto).strip()
        data = json.loads(respuesta_texto)

        if data.get("accion") == "registrar":
            monto = float(data["monto"])
            categoria = data.get("categoria", "otros").lower()
            descripcion = data.get("descripcion", "")
            fecha = date.today().isoformat()

            conn = sqlite3.connect("gastos.db")
            c = conn.cursor()
            c.execute(
                "INSERT INTO gastos (user_id, monto, categoria, descripcion, fecha) VALUES (?, ?, ?, ?, ?)",
                (user_id, monto, categoria, descripcion, fecha)
            )
            conn.commit()
            conn.close()

            await update.message.reply_text(
                f"Registrado:\nMonto: ${monto:,.0f}\nCategoria: {categoria}\nDescripcion: {descripcion}"
            )

        elif data.get("accion") == "consultar":
            conn = sqlite3.connect("gastos.db")
            c = conn.cursor()

            categoria = data.get("categoria")
            periodo = data.get("periodo", "hoy")

            if periodo == "hoy":
                hoy = date.today().isoformat()
                if categoria:
                    c.execute("SELECT SUM(monto), COUNT(*) FROM gastos WHERE user_id=? AND fecha=? AND categoria=?", (user_id, hoy, categoria))
                else:
                    c.execute("SELECT SUM(monto), COUNT(*) FROM gastos WHERE user_id=? AND fecha=?", (user_id, hoy))
            else:
                mes = date.today().strftime("%Y-%m")
                if categoria:
                    c.execute("SELECT SUM(monto), COUNT(*) FROM gastos WHERE user_id=? AND fecha LIKE ? AND categoria=?", (user_id, mes + "%", categoria))
                else:
                    c.execute("SELECT SUM(monto), COUNT(*) FROM gastos WHERE user_id=? AND fecha LIKE ?", (user_id, mes + "%"))

            resultado = c.fetchone()
            conn.close()

            total = resultado[0] or 0
            cantidad = resultado[1] or 0

            texto_respuesta = f"Resultado:\nTotal: ${total:,.0f}\nCantidad: {cantidad}"
            if categoria:
                texto_respuesta += f"\nCategoria: {categoria}"
            await update.message.reply_text(texto_respuesta)

        else:
            await update.message.reply_text("No entendi.")

    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

def main():
    print("Creando Application...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot listo. Iniciando polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    try:
        main()
    except Exception as e:
        print("ERROR FATAL:", str(e))
        import traceback
        traceback.print_exc()