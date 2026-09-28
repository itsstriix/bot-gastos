import os
import sqlite3
from datetime import date
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
import json
import re
import asyncio

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY)

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
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=f"{SYSTEM_PROMPT}\n\nMensaje del usuario: {texto}"
        )
        
        respuesta_texto = response.text.strip()
        respuesta_texto = re.sub(r'```json|```', '', respuesta_texto).strip()
        data = json.loads(respuesta_texto)

        if data.get("accion") == "registrar":
            monto = float(data["monto"])
            categoria = data.get("categoria", "otros").lower()
            descripcion = data.get("descripcion", "")
            fecha = date.today().isoformat()

            conn = sqlite3.connect("gastos.db")
            c = conn.cursor()
            c.execute("INSERT INTO gastos