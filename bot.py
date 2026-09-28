import os
import sqlite3
from datetime import datetime, date
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
import json
import re

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Configurar Gemini
client = genai.Client(api_key=GEMINI_API_KEY)

# Base de datos simple
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

init_db()

# Prompt para Gemini
SYSTEM_PROMPT = """
Sos un asistente de control de gastos personales en pesos argentinos.
Analizá el mensaje del usuario y respondé SOLO con un JSON válido, sin texto extra.

Si el usuario está registrando un gasto, respondé exactamente así:
{
  "accion": "registrar",
  "monto": 30000,
  "categoria": "combustible",
  "descripcion": "nafta"
}

Si está consultando, respondé así:
{
  "accion": "consultar",
  "tipo": "mes" o "dia" o "categoria" o "total",
  "categoria": "combustible" (si aplica),
  "periodo": "2026-09" o "hoy"
}

Categorías posibles: combustible, comida, supermercado, transporte, ocio, salud, servicios, ropa, otros.
Si no estás seguro de la categoría usá "otros".
Monto siempre en número (sin puntos ni comas).
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 ¡Hola! Soy tu bot de gastos.\n\n"
        "Escribime cosas como:\n"
        "• Gasté 30000 en combustible\n"
        "• 15000 supermercado\n"
        "• ¿Cuánto gasté este mes?\n"
        "• Gastos de hoy en comida\n\n"
        "También podés preguntar por categoría o por mes."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    texto = update.message.text

    try:
        # Llamar a Gemini
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=f"{SYSTEM_PROMPT}\n\nMensaje del usuario: {texto}"
        )
        
        # Extraer el JSON de la respuesta
        respuesta_texto = response.text.strip()
        # Limpiar por si Gemini agrega markdown
        respuesta_texto = re.sub(r'```json|```', '', respuesta_texto).strip()
        data = json.loads(respuesta_texto)

        if data.get("accion") == "registrar":
            monto = float(data["monto"])
            categoria = data.get("categoria", "otros").lower()
            descripcion = data.get("descripcion", "")
            fecha = date.today().isoformat()

            conn = sqlite3.connect("gastos.db")
            c = conn.cursor()
            c.execute("INSERT INTO gastos (user_id, monto, categoria, descripcion, fecha) VALUES (?, ?, ?, ?, ?)",
                      (user_id, monto, categoria, descripcion, fecha))
            conn.commit()
            conn.close()

            await update.message.reply_text(
                f"✅ Registrado:\n"
                f"💰 ${monto:,.0f}\n"
                f"📁 {categoria.capitalize()}\n"
                f"📝 {descripcion}"
            )

        elif data.get("accion") == "consultar":
            conn = sqlite3.connect("gastos.db")
            c = conn.cursor()

            tipo = data.get("tipo", "total")
            categoria = data.get("categoria")
            periodo = data.get("periodo", "hoy")

            if tipo == "dia" or periodo == "hoy":
                hoy = date.today().isoformat()
                if categoria:
                    c.execute("SELECT SUM(monto), COUNT(*) FROM gastos WHERE user_id=? AND fecha=? AND categoria=?",
                              (user_id, hoy, categoria))
                else:
                    c.execute("SELECT SUM(monto), COUNT(*) FROM gastos WHERE user_id=? AND fecha=?",
                              (user_id, hoy))
            elif tipo == "mes" or "2026" in str(periodo):
                mes = periodo if len(periodo) == 7 else date.today().strftime("%Y-%m")
                if categoria:
                    c.execute("SELECT SUM(monto), COUNT(*) FROM gastos WHERE user_id=? AND fecha LIKE ? AND categoria=?",
                              (user_id, f"{mes}%", categoria))
                else:
                    c.execute("SELECT SUM(monto), COUNT(*) FROM gastos WHERE user_id=? AND fecha LIKE ?",
                              (user_id, f"{mes}%"))
            else:
                if categoria:
                    c.execute("SELECT SUM(monto), COUNT(*) FROM gastos WHERE user_id=? AND categoria=?",
                              (user_id, categoria))
                else:
                    c.execute("SELECT SUM(monto), COUNT(*) FROM gastos WHERE user_id=?", (user_id,))

            resultado = c.fetchone()
            conn.close()

            total = resultado[0] or 0
            cantidad = resultado[1] or 0

            texto_respuesta = f"📊 Resultado:\nTotal: ${total:,.0f}\nCantidad de gastos: {cantidad}"
            if categoria:
                texto_respuesta += f"\nCategoría: {categoria}"
            await update.message.reply_text(texto_respuesta)

        else:
            await update.message.reply_text("No entendí. Probá de nuevo.")

    except Exception as e:
        await update.message.reply_text(f"Hubo un error: {str(e)}\nIntentá de nuevo con otro mensaje.")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot iniciado...")
    app.run_polling()

if __name__ == "__main__":
    main()