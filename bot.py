import os
import sys
import threading
import asyncio
import json
import re
from datetime import date, datetime, timedelta
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

print("=== INICIANDO BOT DE GASTOS ===")

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")

# ID del único usuario autorizado
TELEGRAM_USER_ID = os.getenv("TELEGRAM_USER_ID")


print("TELEGRAM_TOKEN existe:", bool(TELEGRAM_TOKEN))
print("GEMINI_API_KEY existe:", bool(GEMINI_API_KEY))
print("SUPABASE_URL existe:", bool(SUPABASE_URL))
print("SUPABASE_SECRET_KEY existe:", bool(SUPABASE_SECRET_KEY))
print("TELEGRAM_USER_ID configurado:", bool(TELEGRAM_USER_ID))


# ============================================================
# COMPROBACIONES
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

gemini = genai.Client(
    api_key=GEMINI_API_KEY
)

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SECRET_KEY
)

GEMINI_MODEL = "gemini-3.5-flash-lite"


# ============================================================
# CONFIRMACIONES PENDIENTES
# ============================================================

# Guarda temporalmente una operación que necesita confirmación.
# Ejemplo:
#
# pending_action[user_id] = {
#     "tipo": "eliminar",
#     "gasto_id": 15
# }

pending_actions = {}


# ============================================================
# PROMPT DE GEMINI
# ============================================================

SYSTEM_PROMPT = """
Sos un asistente de control de gastos personales.

El usuario utiliza pesos argentinos.

Tu trabajo es interpretar el mensaje y devolver ÚNICAMENTE
un JSON válido.

NO uses Markdown.
NO uses ```json.
NO agregues explicaciones.
NO escribas texto fuera del JSON.

============================================================
REGISTRAR UN GASTO
============================================================

Si el usuario quiere registrar un gasto:

{
  "accion": "registrar",
  "monto": 30000,
  "categoria": "combustible",
  "descripcion": "nafta"
}

============================================================
CONSULTAR GASTOS
============================================================

Si pregunta cuánto gastó:

{
  "accion": "consultar",
  "periodo": "mes",
  "categoria": null
}

Los periodos permitidos son:

"hoy"
"mes"

Si pregunta por una categoría:

{
  "accion": "consultar",
  "periodo": "mes",
  "categoria": "combustible"
}

============================================================
VER ÚLTIMOS GASTOS
============================================================

Si pide ver sus últimos gastos:

{
  "accion": "listar",
  "cantidad": 5
}

============================================================
BORRAR UN GASTO
============================================================

Si quiere borrar un gasto:

{
  "accion": "eliminar",
  "objetivo": "ultimo"
}

Si menciona un monto:

{
  "accion": "eliminar",
  "objetivo": "monto",
  "monto": 30000
}

Si dice una categoría:

{
  "accion": "eliminar",
  "objetivo": "categoria",
  "categoria": "comida"
}

Si dice "el número 3" después de haber visto una lista,
usar:

{
  "accion": "eliminar",
  "objetivo": "lista",
  "indice": 3
}

============================================================
CORREGIR UN GASTO
============================================================

Si quiere modificar un gasto:

{
  "accion": "corregir",
  "objetivo": "ultimo",
  "nuevo_monto": 25000,
  "nueva_categoria": null,
  "nueva_descripcion": null
}

Si solamente quiere cambiar el monto:

{
  "accion": "corregir",
  "objetivo": "ultimo",
  "nuevo_monto": 25000
}

Si quiere cambiar la categoría:

{
  "accion": "corregir",
  "objetivo": "ultimo",
  "nuevo_monto": null,
  "nueva_categoria": "supermercado"
}

============================================================
REINICIAR TODO
============================================================

Si quiere borrar absolutamente todos los gastos:

{
  "accion": "reiniciar"
}

============================================================
CATEGORÍAS
============================================================

Categorías permitidas:

combustible
comida
supermercado
transporte
ocio
salud
servicios
ropa
otros

Reglas:

- nafta, nafta moto, combustible -> combustible
- restaurante, delivery, almuerzo, cena, desayuno -> comida
- supermercado, compras del súper -> supermercado
- colectivo, tren, taxi, Uber -> transporte
- cine, juegos, entretenimiento -> ocio
- médico, farmacia -> salud
- luz, gas, internet, teléfono -> servicios
- ropa, zapatillas, calzado -> ropa

Si no se puede determinar la categoría:
"otros"

============================================================
MONTOS
============================================================

El monto debe ser un número.

"30 mil" = 30000
"30.000" = 30000
"$30.000" = 30000
"300 lucas" = 300000

Nunca inventes un monto.

============================================================
IMPORTANTE
============================================================

Si el usuario dice "sí", "si", "confirmo",
"confirmado" o "dale", NO intentes interpretar
eso con Gemini.

La aplicación se encargará de las confirmaciones.
"""


# ============================================================
# UTILIDADES
# ============================================================

def usuario_autorizado(user_id):
    """
    Comprueba que el mensaje venga del único usuario permitido.
    """

    if not TELEGRAM_USER_ID:
        return False

    return str(user_id) == str(TELEGRAM_USER_ID)


def limpiar_json(texto):
    """
    Limpia posibles bloques Markdown de Gemini.
    """

    texto = texto.strip()

    texto = re.sub(
        r"^```json\s*",
        "",
        texto,
        flags=re.IGNORECASE
    )

    texto = re.sub(
        r"^```\s*",
        "",
        texto
    )

    texto = re.sub(
        r"\s*```$",
        "",
        texto
    )

    return texto.strip()


def dinero(valor):
    """
    Formatea un número como pesos argentinos.
    """

    return f"${float(valor):,.0f}".replace(
        ",",
        "."
    )


# ============================================================
# OBTENER ID DE TELEGRAM
# ============================================================

async def mostrar_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    await update.message.reply_text(
        "Tu Telegram User ID es:\n\n"
        f"{user_id}"
    )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    if not usuario_autorizado(user_id):

        await update.message.reply_text(
            "❌ No estás autorizado para utilizar este bot."
        )

        return

    await update.message.reply_text(
        "💰 Hola! Soy tu bot de gastos.\n\n"

        "Podés escribirme normalmente, por ejemplo:\n\n"

        "➕ Gasté 30000 en nafta\n"
        "➕ Gasté 15000 en comida\n\n"

        "📊 Cuánto gasté este mes?\n"
        "📊 Cuánto gasté en combustible este mes?\n\n"

        "📋 Mostrame los últimos 5 gastos\n\n"

        "✏️ Corregí el último gasto, eran 25000\n"
        "🗑️ Borrá el último gasto\n\n"

        "También puedo reiniciar todos los gastos."
    )


# ============================================================
# CONSULTAR GEMINI
# ============================================================

async def consultar_gemini(texto):

    prompt = (
        SYSTEM_PROMPT
        + "\n\nMENSAJE DEL USUARIO:\n"
        + texto
    )

    ultimo_error = None

    for intento in range(3):

        try:

            response = await asyncio.to_thread(
                gemini.models.generate_content,
                model=GEMINI_MODEL,
                contents=prompt
            )

            if not response:
                raise Exception(
                    "Gemini no devolvió respuesta."
                )

            if not response.text:
                raise Exception(
                    "Gemini devolvió una respuesta vacía."
                )

            respuesta = limpiar_json(
                response.text
            )

            data = json.loads(
                respuesta
            )

            print(
                "Gemini respondió:",
                data
            )

            return data

        except Exception as e:

            ultimo_error = e

            error = str(e)

            print(
                f"Error Gemini "
                f"(intento {intento + 1}/3): "
                f"{error}"
            )

            # Error temporal de Gemini
            if (
                "503" in error
                or "UNAVAILABLE" in error
            ):

                if intento < 2:

                    espera = 2 ** intento

                    print(
                        f"Esperando {espera} segundos..."
                    )

                    await asyncio.sleep(
                        espera
                    )

                    continue

            break

    raise ultimo_error


# ============================================================
# GUARDAR GASTO
# ============================================================

async def guardar_gasto(
    user_id,
    monto,
    categoria,
    descripcion
):

    datos = {
        "user_id": int(user_id),
        "monto": float(monto),
        "categoria": categoria,
        "descripcion": descripcion,
        "fecha": date.today().isoformat()
    }

    print(
        "Insertando gasto:",
        datos
    )

    response = await asyncio.to_thread(
        lambda:
        supabase
        .table("gastos")
        .insert(datos)
        .execute()
    )

    return response.data


# ============================================================
# OBTENER ÚLTIMOS GASTOS
# ============================================================

async def obtener_ultimos_gastos(
    user_id,
    cantidad=10
):

    cantidad = max(
        1,
        min(
            int(cantidad),
            20
        )
    )

    response = await asyncio.to_thread(
        lambda:
        supabase
        .table("gastos")
        .select(
            "id,user_id,monto,categoria,descripcion,fecha"
        )
        .eq(
            "user_id",
            int(user_id)
        )
        .order(
            "id",
            desc=True
        )
        .limit(
            cantidad
        )
        .execute()
    )

    return response.data or []


# ============================================================
# OBTENER GASTOS DEL PERIODO
# ============================================================

async def obtener_gastos_periodo(
    user_id,
    periodo,
    categoria=None
):

    hoy = date.today()

    if periodo == "hoy":

        fecha_desde = hoy
        fecha_hasta = hoy + timedelta(days=1)

    else:

        # Primer día del mes
        fecha_desde = hoy.replace(
            day=1
        )

        # Primer día del mes siguiente
        if hoy.month == 12:

            fecha_hasta = hoy.replace(
                year=hoy.year + 1,
                month=1,
                day=1
            )

        else:

            fecha_hasta = hoy.replace(
                month=hoy.month + 1,
                day=1
            )

    def consulta():

        query = (
            supabase
            .table("gastos")
            .select(
                "id,user_id,monto,categoria,descripcion,fecha"
            )
            .eq(
                "user_id",
                int(user_id)
            )
            .gte(
                "fecha",
                fecha_desde.isoformat()
            )
            .lt(
                "fecha",
                fecha_hasta.isoformat()
            )
        )

        if categoria:

            query = query.eq(
                "categoria",
                categoria
            )

        return query.order(
            "id",
            desc=True
        ).execute()

    response = await asyncio.to_thread(
        consulta
    )

    return response.data or []


# ============================================================
# BUSCAR GASTOS POR MONTO
# ============================================================

async def buscar_por_monto(
    user_id,
    monto
):

    gastos = await obtener_ultimos_gastos(
        user_id,
        20
    )

    coincidencias = []

    for gasto in gastos:

        if float(gasto["monto"]) == float(monto):

            coincidencias.append(
                gasto
            )

    return coincidencias


# ============================================================
# BUSCAR GASTOS POR CATEGORÍA
# ============================================================

async def buscar_por_categoria(
    user_id,
    categoria
):

    gastos = await obtener_ultimos_gastos(
        user_id,
        20
    )

    return [
        gasto
        for gasto in gastos
        if gasto["categoria"] == categoria
    ]


# ============================================================
# ACTUALIZAR GASTO
# ============================================================

async def actualizar_gasto(
    gasto_id,
    nuevo_monto=None,
    nueva_categoria=None,
    nueva_descripcion=None
):

    cambios = {}

    if nuevo_monto is not None:

        cambios["monto"] = float(
            nuevo_monto
        )

    if nueva_categoria:

        cambios["categoria"] = (
            nueva_categoria
        )

    if nueva_descripcion:

        cambios["descripcion"] = (
            nueva_descripcion
        )

    if not cambios:

        raise Exception(
            "No se indicó ningún cambio."
        )

    response = await asyncio.to_thread(
        lambda:
        supabase
        .table("gastos")
        .update(cambios)
        .eq(
            "id",
            int(gasto_id)
        )
        .execute()
    )

    return response.data


# ============================================================
# ELIMINAR GASTO
# ============================================================

async def eliminar_gasto(
    gasto_id
):

    response = await asyncio.to_thread(
        lambda:
        supabase
        .table("gastos")
        .delete()
        .eq(
            "id",
            int(gasto_id)
        )
        .execute()
    )

    return response.data


# ============================================================
# ELIMINAR TODO
# ============================================================

async def eliminar_todos_los_gastos(
    user_id
):

    response = await asyncio.to_thread(
        lambda:
        supabase
        .table("gastos")
        .delete()
        .eq(
            "user_id",
            int(user_id)
        )
        .execute()
    )

    return response.data


# ============================================================
# MOSTRAR GASTO
# ============================================================

def formato_gasto(
    gasto,
    numero=None
):

    prefijo = ""

    if numero is not None:

        prefijo = f"{numero}. "

    texto = (
        f"{prefijo}"
        f"💰 {dinero(gasto['monto'])}\n"
        f"📂 {gasto['categoria']}\n"
        f"📝 {gasto.get('descripcion') or '-'}\n"
        f"📅 {gasto['fecha']}\n"
        f"🆔 #{gasto['id']}"
    )

    return texto


# ============================================================
# PEDIR CONFIRMACIÓN PARA ELIMINAR
# ============================================================

async def pedir_confirmacion_eliminar(
    update,
    user_id,
    gasto
):

    pending_actions[user_id] = {
        "tipo": "eliminar",
        "gasto_id": gasto["id"]
    }

    await update.message.reply_text(
        "🗑️ ENCONTRÉ ESTE GASTO:\n\n"
        + formato_gasto(gasto)
        + "\n\n"
        "¿Querés eliminarlo?\n\n"
        "Respondé **SI** o **NO**."
    )


# ============================================================
# PEDIR CONFIRMACIÓN PARA CORREGIR
# ============================================================

async def pedir_confirmacion_corregir(
    update,
    user_id,
    gasto,
    cambios
):

    pending_actions[user_id] = {
        "tipo": "corregir",
        "gasto_id": gasto["id"],
        "cambios": cambios
    }

    nuevo_monto = cambios.get(
        "monto",
        gasto["monto"]
    )

    nueva_categoria = cambios.get(
        "categoria",
        gasto["categoria"]
    )

    nueva_descripcion = cambios.get(
        "descripcion",
        gasto.get("descripcion")
    )

    await update.message.reply_text(
        "✏️ VOY A CORREGIR ESTE GASTO:\n\n"

        "ANTES:\n"
        + formato_gasto(gasto)
        + "\n\n"

        "DESPUÉS:\n"
        f"💰 {dinero(nuevo_monto)}\n"
        f"📂 {nueva_categoria}\n"
        f"📝 {nueva_descripcion or '-'}\n"
        f"🆔 #{gasto['id']}\n\n"

        "¿Confirmás?\n\n"
        "Respondé **SI** o **NO**."
    )


# ============================================================
# PEDIR CONFIRMACIÓN PARA REINICIAR
# ============================================================

async def pedir_confirmacion_reinicio(
    update,
    user_id
):

    gastos = await obtener_ultimos_gastos(
        user_id,
        20
    )

    # Obtener el total real del mes para mostrar referencia
    gastos_mes = await obtener_gastos_periodo(
        user_id,
        "mes"
    )

    total_mes = sum(
        float(g["monto"])
        for g in gastos_mes
    )

    pending_actions[user_id] = {
        "tipo": "reiniciar"
    }

    await update.message.reply_text(
        "⚠️ ATENCIÓN ⚠️\n\n"

        "Vas a eliminar TODOS los gastos "
        "guardados en el bot.\n\n"

        f"📊 Gastos del mes: {len(gastos_mes)}\n"
        f"💰 Total del mes: {dinero(total_mes)}\n\n"

        "Esta operación no se puede deshacer.\n\n"

        "Para confirmar escribí exactamente:\n\n"

        "CONFIRMAR"
    )


# ============================================================
# PROCESAR CONFIRMACIONES
# ============================================================

async def procesar_confirmacion(
    update,
    user_id,
    texto
):

    accion = pending_actions.get(
        user_id
    )

    if not accion:

        return False

    texto_limpio = (
        texto.strip()
        .lower()
    )

    # --------------------------------------------------------
    # CANCELAR
    # --------------------------------------------------------

    if texto_limpio in [
        "no",
        "no quiero",
        "cancelar",
        "cancela"
    ]:

        del pending_actions[user_id]

        await update.message.reply_text(
            "❌ Operación cancelada."
        )

        return True

    # --------------------------------------------------------
    # ELIMINAR
    # --------------------------------------------------------

    if accion["tipo"] == "eliminar":

        if texto_limpio in [
            "si",
            "sí",
            "confirmar",
            "confirmo",
            "dale"
        ]:

            gasto_id = accion["gasto_id"]

            await eliminar_gasto(
                gasto_id
            )

            del pending_actions[user_id]

            await update.message.reply_text(
                "🗑️ Gasto eliminado correctamente."
            )

            return True

        await update.message.reply_text(
            "Respondé **SI** para confirmar "
            "o **NO** para cancelar."
        )

        return True

    # --------------------------------------------------------
    # CORREGIR
    # --------------------------------------------------------

    if accion["tipo"] == "corregir":

        if texto_limpio in [
            "si",
            "sí",
            "confirmar",
            "confirmo",
            "dale"
        ]:

            gasto_id = accion["gasto_id"]
            cambios = accion["cambios"]

            await actualizar_gasto(
                gasto_id,
                nuevo_monto=cambios.get("monto"),
                nueva_categoria=cambios.get("categoria"),
                nueva_descripcion=cambios.get("descripcion")
            )

            del pending_actions[user_id]

            await update.message.reply_text(
                "✏️ Gasto corregido correctamente."
            )

            return True

        await update.message.reply_text(
            "Respondé **SI** para confirmar "
            "o **NO** para cancelar."
        )

        return True

    # --------------------------------------------------------
    # REINICIAR
    # --------------------------------------------------------

    if accion["tipo"] == "reiniciar":

        if texto.strip() == "CONFIRMAR":

            await eliminar_todos_los_gastos(
                user_id
            )

            del pending_actions[user_id]

            await update.message.reply_text(
                "🔄 LISTO.\n\n"
                "Todos los gastos fueron eliminados.\n"
                "El contador volvió a 0."
            )

            return True

        if texto_limpio in [
            "no",
            "cancelar",
            "cancela"
        ]:

            del pending_actions[user_id]

            await update.message.reply_text(
                "❌ Reinicio cancelado."
            )

            return True

        await update.message.reply_text(
            "Para borrar TODO tenés que escribir:\n\n"
            "CONFIRMAR\n\n"
            "O escribí NO para cancelar."
        )

        return True

    return False


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
        return

    # --------------------------------------------------------
    # COMANDO PARA OBTENER ID
    # --------------------------------------------------------

    # Esto funciona aunque TELEGRAM_USER_ID todavía
    # no esté configurado.
    #
    # Sirve para conocer el ID y después colocarlo en Render.

    if texto.strip() == "/id":

        await mostrar_id(
            update,
            context
        )

        return

    # --------------------------------------------------------
    # SEGURIDAD
    # --------------------------------------------------------

    if not usuario_autorizado(user_id):

        await update.message.reply_text(
            "❌ No estás autorizado para utilizar este bot."
        )

        return

    # --------------------------------------------------------
    # CONFIRMACIONES
    # --------------------------------------------------------

    if user_id in pending_actions:

        procesado = await procesar_confirmacion(
            update,
            user_id,
            texto
        )

        if procesado:

            return

    print(
        f"Mensaje recibido: {texto}"
    )

    try:

        # ----------------------------------------------------
        # GEMINI
        # ----------------------------------------------------

        data = await consultar_gemini(
            texto
        )

        accion = data.get(
            "accion"
        )

        # ====================================================
        # REGISTRAR
        # ====================================================

        if accion == "registrar":

            monto = data.get(
                "monto"
            )

            if monto is None:

                await update.message.reply_text(
                    "No pude determinar el monto."
                )

                return

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

            await guardar_gasto(
                user_id,
                float(monto),
                categoria,
                descripcion
            )

            await update.message.reply_text(
                "✅ GASTO REGISTRADO\n\n"
                f"💰 Monto: {dinero(monto)}\n"
                f"📂 Categoría: {categoria}\n"
                f"📝 Descripción: {descripcion}"
            )

        # ====================================================
        # CONSULTAR
        # ====================================================

        elif accion == "consultar":

            periodo = data.get(
                "periodo",
                "mes"
            )

            categoria = data.get(
                "categoria"
            )

            gastos = await obtener_gastos_periodo(
                user_id,
                periodo,
                categoria
            )

            total = sum(
                float(g["monto"])
                for g in gastos
            )

            cantidad = len(
                gastos
            )

            if periodo == "hoy":

                titulo = "📊 GASTOS DE HOY"

            else:

                titulo = "📊 GASTOS DE ESTE MES"

            respuesta = (
                f"{titulo}\n\n"
                f"💰 Total: {dinero(total)}\n"
                f"🧾 Cantidad: {cantidad}"
            )

            if categoria:

                respuesta += (
                    f"\n📂 Categoría: {categoria}"
                )

            await update.message.reply_text(
                respuesta
            )

        # ====================================================
        # LISTAR
        # ====================================================

        elif accion == "listar":

            cantidad = data.get(
                "cantidad",
                5
            )

            gastos = await obtener_ultimos_gastos(
                user_id,
                cantidad
            )

            if not gastos:

                await update.message.reply_text(
                    "📋 No hay gastos registrados."
                )

                return

            respuesta = (
                "📋 ÚLTIMOS GASTOS\n\n"
            )

            for i, gasto in enumerate(
                gastos,
                start=1
            ):

                respuesta += (
                    formato_gasto(
                        gasto,
                        i
                    )
                    + "\n\n"
                )

            await update.message.reply_text(
                respuesta
            )

        # ====================================================
        # ELIMINAR
        # ====================================================

        elif accion == "eliminar":

            objetivo = data.get(
                "objetivo",
                "ultimo"
            )

            gasto = None

            # ------------------------------------------------
            # ÚLTIMO
            # ------------------------------------------------

            if objetivo == "ultimo":

                gastos = await obtener_ultimos_gastos(
                    user_id,
                    1
                )

                if gastos:

                    gasto = gastos[0]

            # ------------------------------------------------
            # POR MONTO
            # ------------------------------------------------

            elif objetivo == "monto":

                monto = data.get(
                    "monto"
                )

                coincidencias = (
                    await buscar_por_monto(
                        user_id,
                        monto
                    )
                )

                if len(coincidencias) == 1:

                    gasto = coincidencias[0]

                elif len(coincidencias) > 1:

                    await update.message.reply_text(
                        "Encontré varios gastos "
                        f"por {dinero(monto)}.\n\n"
                        "Usá 'mostrame los últimos gastos' "
                        "para elegir cuál querés borrar."
                    )

                    return

            # ------------------------------------------------
            # POR CATEGORÍA
            # ------------------------------------------------

            elif objetivo == "categoria":

                categoria = data.get(
                    "categoria"
                )

                coincidencias = (
                    await buscar_por_categoria(
                        user_id,
                        categoria
                    )
                )

                if len(coincidencias) == 1:

                    gasto = coincidencias[0]

                elif len(coincidencias) > 1:

                    await update.message.reply_text(
                        "Encontré varios gastos "
                        f"de {categoria}.\n\n"
                        "Mostrame los últimos gastos "
                        "para elegir cuál borrar."
                    )

                    return

            # ------------------------------------------------
            # POR ÍNDICE DE LISTA
            # ------------------------------------------------

            elif objetivo == "lista":

                indice = int(
                    data.get(
                        "indice",
                        0
                    )
                )

                gastos = await obtener_ultimos_gastos(
                    user_id,
                    20
                )

                if (
                    indice >= 1
                    and indice <= len(gastos)
                ):

                    gasto = gastos[
                        indice - 1
                    ]

            # ------------------------------------------------
            # NO ENCONTRADO
            # ------------------------------------------------

            if not gasto:

                await update.message.reply_text(
                    "No encontré un gasto que "
                    "coincida con lo que pediste."
                )

                return

            await pedir_confirmacion_eliminar(
                update,
                user_id,
                gasto
            )

        # ====================================================
        # CORREGIR
        # ====================================================

        elif accion == "corregir":

            objetivo = data.get(
                "objetivo",
                "ultimo"
            )

            gasto = None

            # ------------------------------------------------
            # ÚLTIMO
            # ------------------------------------------------

            if objetivo == "ultimo":

                gastos = await obtener_ultimos_gastos(
                    user_id,
                    1
                )

                if gastos:

                    gasto = gastos[0]

            # ------------------------------------------------
            # POR MONTO
            # ------------------------------------------------

            elif objetivo == "monto":

                monto = data.get(
                    "monto"
                )

                coincidencias = (
                    await buscar_por_monto(
                        user_id,
                        monto
                    )
                )

                if len(coincidencias) == 1:

                    gasto = coincidencias[0]

                elif len(coincidencias) > 1:

                    await update.message.reply_text(
                        "Encontré varios gastos "
                        f"por {dinero(monto)}.\n\n"
                        "Mostrame los últimos gastos "
                        "para elegir cuál corregir."
                    )

                    return

            # ------------------------------------------------
            # SI NO EXISTE
            # ------------------------------------------------

            if not gasto:

                await update.message.reply_text(
                    "No encontré el gasto que "
                    "querés corregir."
                )

                return

            cambios = {}

            if data.get(
                "nuevo_monto"
            ) is not None:

                cambios["monto"] = float(
                    data["nuevo_monto"]
                )

            if data.get(
                "nueva_categoria"
            ):

                cambios["categoria"] = (
                    data["nueva_categoria"]
                ).lower()

            if data.get(
                "nueva_descripcion"
            ):

                cambios["descripcion"] = (
                    data["nueva_descripcion"]
                )

            if not cambios:

                await update.message.reply_text(
                    "No encontré qué querés corregir."
                )

                return

            await pedir_confirmacion_corregir(
                update,
                user_id,
                gasto,
                cambios
            )

        # ====================================================
        # REINICIAR
        # ====================================================

        elif accion == "reiniciar":

            await pedir_confirmacion_reinicio(
                update,
                user_id
            )

        # ====================================================
        # DESCONOCIDO
        # ====================================================

        else:

            await update.message.reply_text(
                "No entendí qué querés hacer."
            )

    except json.JSONDecodeError:

        print(
            "ERROR: Gemini no devolvió JSON válido."
        )

        await update.message.reply_text(
            "No pude interpretar el mensaje. "
            "Probá escribirlo de otra forma."
        )

    except Exception as e:

        print(
            "ERROR:",
            str(e)
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
        "Creando aplicación de Telegram..."
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

        asyncio.set_event_loop(
            loop
        )

    main()