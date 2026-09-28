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
  "tipo": "mes",
  "categoria": "combustible",
  "periodo": "hoy"
}

Categorías posibles: combustible, comida, supermercado, transporte, ocio, salud, servicios, ropa, otros.
Si no estás seguro de la categoría usá "otros".
Monto siempre en número (sin puntos ni comas).
"""
