from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction
from groq import Groq
import httpx
import base64
import os

# =============================================
# КЛЮЧИ — вставь через Railway Variables
# =============================================
TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN",     "ВСТАВЬ_СЮДА")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "ВСТАВЬ_СЮДА")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY",       "ВСТАВЬ_СЮДА")

groq_client = Groq(api_key=GROQ_API_KEY)

# =============================================
# МОДЕЛИ
# (source, model_id, label, company, vision, description)
# =============================================
MODELS = {
    "gemini": (
        "openrouter",
        "google/gemini-2.0-flash-exp:free",
        "✨ Gemini 2.0 Flash",
        "Google",
        True,
        "Быстрый и умный. Уровень GPT-4o mini. Поддерживает фото 📸"
    ),
    "deepseek": (
        "openrouter",
        "deepseek/deepseek-chat-v3-0324:free",
        "🔍 DeepSeek V3",
        "DeepSeek",
        False,
        "Уровень GPT-4o. Отлично для текстов и анализа"
    ),
    "deepseek_r1": (
        "openrouter",
        "deepseek/deepseek-r1:free",
        "🔬 DeepSeek R1",
        "DeepSeek",
        False,
        "Аналог ChatGPT o1 — думает перед ответом. Лучший для логики и математики"
    ),
    "groq": (
        "groq",
        "llama-3.3-70b-versatile",
        "🦙 Llama 3.3 70B",
        "Meta (Groq)",
        False,
        "Уровень GPT-4. Быстрый через Groq. Лучший для кода"
    ),
    "qwen": (
        "openrouter",
        "qwen/qwen2.5-vl-72b-instruct:free",
        "👁️ Qwen2.5 VL 72B",
        "Alibaba",
        True,
        "Мощная модель с анализом фото 📸. Хороша для визуальных задач"
    ),
    "mistral": (
        "openrouter",
        "mistralai/mistral-small-3.1-24b-instruct:free",
        "💨 Mistral Small 24B",
        "Mistral",
        True,
        "Умная и быстрая. Поддерживает фото 📸"
    ),
}

def src(k):    return MODELS[k][0]
def mid(k):    return MODELS[k][1]
def lbl(k):    return MODELS[k][2]
def comp(k):   return MODELS[k][3]
def vis(k):    return MODELS[k][4]
def desc(k):   return MODELS[k][5]

user_ai      = {}
user_modes   = {}
user_history = {}

def get_ai(uid):   return user_ai.get(uid, "gemini")
def get_mode(uid): return user_modes.get(uid, "default")
def get_history(uid):
    if uid not in user_history: user_history[uid] = []
    return user_history[uid]

# =============================================
# СИСТЕМНЫЕ ПРОМПТЫ
# =============================================
PROMPTS = {
    "default":      "Ты — умный универсальный ассистент. Отвечай на языке пользователя. Будь конкретным и полезным.",
    "code":         "Ты — Senior разработчик. Пиши чистый код с комментариями. Объясняй почему, а не только как. Указывай язык в блоках кода. Предлагай лучшие практики.",
    "translate":    "Ты — профессиональный переводчик. Переводи естественно, не дословно. Сохраняй стиль оригинала.",
    "write":        "Ты — талантливый копирайтер. Пиши живо, с эмоциями и конкретикой. Избегай клише и канцелярита.",
    "analyze":      "Ты — аналитик уровня McKinsey. Структура: суть → анализ → факты → выводы → рекомендации.",
    "image":        "Ты — эксперт по промптам для Midjourney/DALL-E/Stable Diffusion. Детальные промпты на английском + описание на русском.",
    "presentation": "Ты — эксперт по презентациям. Одна идея — один слайд. Заголовок до 7 слов + 3-4 тезиса + идея для визуала.",
    "excel":        "Ты — эксперт по Excel и Google Sheets. Формулы с примерами, объясняй каждую часть.",
    "summarize":    "Ты — эксперт по сжатию информации. Суть в 1-2 предложениях → ключевые тезисы → вывод.",
    "explain":      "Ты — гениальный учитель. Объясняй через аналогии из жизни. Простое определение → аналогия → пример → почему важно.",
}

MODE_NAMES = {
    "default": "🤖 Универсальный", "code": "💻 Программист",
    "translate": "🌍 Переводчик",  "write": "✍️ Писатель",
    "analyze": "📊 Аналитик",      "image": "🖼️ Промпты",
    "presentation": "📋 Презентации", "excel": "📗 Excel",
    "summarize": "📝 Суммаризатор", "explain": "🧠 Объяснятор",
}

# =============================================
# ЗАПРОСЫ К ИИ
# =============================================
async def call_openrouter(messages, model_id):
    import json
    body = json.dumps(
        {"model": model_id, "messages": messages, "max_tokens": 2048},
        ensure_ascii=False
    ).encode("utf-8")
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json; charset=utf-8",
                "HTTP-Referer": "https://t.me",
                "X-Title": "Telegram AI Bot",
            },
            content=body,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
        
async def ask(uid, message, mode_override=None):
    history = get_history(uid)
    system  = PROMPTS.get(mode_override or get_mode(uid), PROMPTS["default"])
    ai      = get_ai(uid)

    history.append({"role": "user", "content": message})
    if len(history) > 20:
        user_history[uid] = history[-20:]
        history = user_history[uid]

    messages = [{"role": "system", "content": system}] + history

    if src(ai) == "groq":
        response = groq_client.chat.completions.create(
            model=mid(ai), messages=messages, max_tokens=2048
        )
        reply = response.choices[0].message.content
    else:
        reply = await call_openrouter(messages, mid(ai))

    history.append({"role": "assistant", "content": reply})
    return reply

async def ask_with_photo(uid, caption, photo_b64):
    ai      = get_ai(uid)
    system  = PROMPTS.get(get_mode(uid), PROMPTS["default"])
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{photo_b64}"}},
            {"type": "text", "text": caption or "Опиши подробно что на этом изображении."}
        ]}
    ]
    reply = await call_openrouter(messages, mid(ai))
    get_history(uid).append({"role": "user",      "content": caption or "[фото]"})
    get_history(uid).append({"role": "assistant", "content": reply})
    return reply

async def send(update, text):
    for i in range(0, len(text), 4096):
        await update.message.reply_text(text[i:i+4096])

# =============================================
# КОМАНДЫ
# =============================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    name = update.effective_user.first_name
    ai   = get_ai(uid)
    v    = "📸 Понимает фото" if vis(ai) else "🚫 Фото не поддерживает"
    await update.message.reply_text(
        f"Привет, {name}! 👋\n\n"
        f"🧠 Модель: {lbl(ai)}\n"
        f"🏢 {comp(ai)}\n"
        f"{v}\n\n"
        "Просто напиши что-нибудь!\n"
        "/models — выбрать модель\n"
        "/help — все команды"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 ВСЕ КОМАНДЫ:\n\n"
        "━━━ 🤖 МОДЕЛИ ━━━\n"
        "/models — список и выбор\n"
        "/gemini — ✨ Gemini 2.0 Flash 📸\n"
        "/deepseek — 🔍 DeepSeek V3\n"
        "/deepseek_r1 — 🔬 DeepSeek R1 (логика)\n"
        "/groq — 🦙 Llama 3.3 70B (код)\n"
        "/qwen — 👁️ Qwen2.5 VL 72B 📸\n"
        "/mistral — 💨 Mistral Small 24B 📸\n\n"
        "━━━ 📸 АНАЛИЗ ФОТО ━━━\n"
        "Просто отправь фото с подписью или без!\n"
        "Поддерживают: Gemini, Qwen, Mistral\n\n"
        "━━━ 🎭 РЕЖИМЫ ━━━\n"
        "/mode — меню режимов\n"
        "/default — 🤖 Универсальный\n"
        "/code — 💻 Программист\n"
        "/translate — 🌍 Переводчик\n"
        "/write — ✍️ Писатель\n"
        "/analyze — 📊 Аналитик\n"
        "/presentation — 📋 Презентации\n"
        "/excel — 📗 Excel\n\n"
        "━━━ 🛠️ ИНСТРУМЕНТЫ ━━━\n"
        "/sum <текст> — сжать текст\n"
        "/fix <текст> — исправить ошибки\n"
        "/explain <тема> — объяснить просто\n"
        "/ideas <тема> — генерация идей\n"
        "/image <идея> — промпт для картинки\n"
        "/story <тема> — написать историю\n"
        "/gif <идея> — описание GIF\n"
        "/pptx <тема> — структура презентации\n"
        "/table <описание> — создать таблицу\n\n"
        "━━━ ⚙️ ПРОЧЕЕ ━━━\n"
        "/status — текущий статус\n"
        "/clear — очистить историю\n"
    )

async def models_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ai  = get_ai(uid)
    text = f"🤖 Сейчас: {lbl(ai)}\n\n━━━ Выбери модель ━━━\n\n"
    for key, m in MODELS.items():
        active   = "✅" if key == ai else "○"
        vis_icon = "📸" if m[4] else "🚫📸"
        text += f"{active} /{key} — {m[2]} {vis_icon}\n"
        text += f"   └ {m[3]} · {m[5]}\n\n"
    await send(update, text)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ai  = get_ai(uid)
    v   = "📸 Да" if vis(ai) else "🚫 Нет"
    await update.message.reply_text(
        f"🧠 Модель: {lbl(ai)}\n"
        f"🏢 Компания: {comp(ai)}\n"
        f"📸 Анализ фото: {v}\n"
        f"🎭 Режим: {MODE_NAMES.get(get_mode(uid))}\n"
        f"💬 Сообщений в памяти: {len(get_history(uid))}"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_history[update.effective_user.id] = []
    await update.message.reply_text("🗑️ История очищена!")

async def switch_ai(update, uid, key):
    user_ai[uid] = key
    user_history[uid] = []
    v = "📸 Поддерживает фото!" if vis(key) else f"🚫 Фото не поддерживает\n\nДля фото: /gemini /qwen /mistral"
    await update.message.reply_text(
        f"{lbl(key)}\n🏢 {comp(key)}\n{v}\n\nℹ️ {desc(key)}\n\nИстория очищена."
    )

async def set_gemini(u,c):      await switch_ai(u, u.effective_user.id, "gemini")
async def set_deepseek(u,c):    await switch_ai(u, u.effective_user.id, "deepseek")
async def set_deepseek_r1(u,c): await switch_ai(u, u.effective_user.id, "deepseek_r1")
async def set_groq(u,c):        await switch_ai(u, u.effective_user.id, "groq")
async def set_qwen(u,c):        await switch_ai(u, u.effective_user.id, "qwen")
async def set_mistral(u,c):     await switch_ai(u, u.effective_user.id, "mistral")

async def mode_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎭 Выбери режим:\n\n"
        "/default — 🤖 Универсальный\n"
        "/code — 💻 Программист\n"
        "/translate — 🌍 Переводчик\n"
        "/write — ✍️ Писатель\n"
        "/analyze — 📊 Аналитик\n"
        "/presentation — 📋 Презентации\n"
        "/excel — 📗 Excel\n"
    )

async def set_m(u, c, mode, text):
    user_modes[u.effective_user.id] = mode
    await u.message.reply_text(text)

async def m_default(u,c):      await set_m(u,c,"default",      "🤖 Универсальный режим!")
async def m_code(u,c):         await set_m(u,c,"code",          "💻 Режим: Программист!")
async def m_translate(u,c):    await set_m(u,c,"translate",     "🌍 Режим: Переводчик!")
async def m_write(u,c):        await set_m(u,c,"write",         "✍️ Режим: Писатель!")
async def m_analyze(u,c):      await set_m(u,c,"analyze",       "📊 Режим: Аналитик!")
async def m_presentation(u,c): await set_m(u,c,"presentation",  "📋 Режим: Презентации!")
async def m_excel(u,c):        await set_m(u,c,"excel",         "📗 Режим: Excel!")

async def quick_cmd(update, context, mode, label, prefix=""):
    args = " ".join(context.args) if context.args else None
    if not args:
        await update.message.reply_text(f"Использование: /{label} <текст>"); return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(update.effective_user.id, prefix + args, mode_override=mode)
        await send(update, reply)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def cmd_image(u,c):   await quick_cmd(u,c,"image","image")
async def cmd_sum(u,c):     await quick_cmd(u,c,"summarize","sum",    "Сожми этот текст: ")
async def cmd_explain(u,c): await quick_cmd(u,c,"explain","explain",  "Объясни просто: ")
async def cmd_fix(u,c):     await quick_cmd(u,c,"write","fix",        "Исправь грамматику и объясни ошибки: ")
async def cmd_ideas(u,c):   await quick_cmd(u,c,"write","ideas",      "Придумай 10 идей с пояснениями: ")
async def cmd_pptx(u,c):    await quick_cmd(u,c,"presentation","pptx","Структура презентации 8-10 слайдов: ")
async def cmd_table(u,c):   await quick_cmd(u,c,"excel","table",      "Создай таблицу в Markdown: ")

async def cmd_story(u,c):
    args = " ".join(c.args) if c.args else None
    if not args: await u.message.reply_text("Использование: /story <тема>"); return
    await c.bot.send_chat_action(chat_id=u.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(u.effective_user.id,
            f"Напиши увлекательную историю (400-600 слов) с живыми персонажами и неожиданным финалом: {args}",
            mode_override="write")
        await send(u, reply)
    except Exception as e:
        await u.message.reply_text(f"❌ Ошибка: {e}")

async def cmd_gif(u,c):
    args = " ".join(c.args) if c.args else None
    if not args: await u.message.reply_text("Использование: /gif <идея>"); return
    await c.bot.send_chat_action(chat_id=u.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(u.effective_user.id,
            f"Опиши детально GIF-анимацию покадрово: {args}. Стиль, цвета, промпт на английском.",
            mode_override="image")
        await send(u, reply)
    except Exception as e:
        await u.message.reply_text(f"❌ Ошибка: {e}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ai  = get_ai(uid)
    if not vis(ai):
        await update.message.reply_text(
            f"🚫 {lbl(ai)} не поддерживает анализ фото.\n\n"
            f"ℹ️ Эта модель работает только с текстом — это особенность её архитектуры.\n\n"
            "Для анализа фото переключись:\n"
            "/gemini — ✨ Gemini 2.0 Flash\n"
            "/qwen — 👁️ Qwen2.5 VL 72B\n"
            "/mistral — 💨 Mistral Small 24B"
        )
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        photo_file  = await update.message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        photo_b64   = base64.b64encode(photo_bytes).decode("utf-8")
        caption     = update.message.caption or ""
        reply       = await ask_with_photo(uid, caption, photo_b64)
        await send(update, reply)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при анализе фото: {e}")

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(update.effective_user.id, update.message.text)
        await send(update, reply)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}\nПопробуй /clear или смени модель через /models")

# =============================================
# ЗАПУСК
# =============================================
async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start",        "🚀 Начать"),
        BotCommand("help",         "📋 Все команды"),
        BotCommand("models",       "🤖 Выбрать модель"),
        BotCommand("status",       "📍 Текущий статус"),
        BotCommand("clear",        "🗑️ Очистить историю"),
        BotCommand("gemini",       "✨ Gemini 2.0 Flash 📸"),
        BotCommand("deepseek",     "🔍 DeepSeek V3"),
        BotCommand("deepseek_r1",  "🔬 DeepSeek R1 (логика)"),
        BotCommand("groq",         "🦙 Llama 3.3 70B (код)"),
        BotCommand("qwen",         "👁️ Qwen2.5 VL 72B 📸"),
        BotCommand("mistral",      "💨 Mistral Small 24B 📸"),
        BotCommand("mode",         "🎭 Сменить режим"),
        BotCommand("code",         "💻 Программист"),
        BotCommand("translate",    "🌍 Переводчик"),
        BotCommand("write",        "✍️ Писатель"),
        BotCommand("analyze",      "📊 Аналитик"),
        BotCommand("sum",          "📝 Сжать текст"),
        BotCommand("fix",          "✏️ Исправить текст"),
        BotCommand("explain",      "🧠 Объяснить просто"),
        BotCommand("ideas",        "💡 Генерация идей"),
        BotCommand("image",        "🖼️ Промпт для картинки"),
        BotCommand("story",        "📖 Написать историю"),
        BotCommand("gif",          "🎞️ Описание GIF"),
        BotCommand("pptx",         "📊 Структура презентации"),
        BotCommand("table",        "📗 Создать таблицу"),
    ])

def main():
    print("🤖 Бот запускается...")
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start",        start))
    app.add_handler(CommandHandler("help",         help_cmd))
    app.add_handler(CommandHandler("models",       models_cmd))
    app.add_handler(CommandHandler("status",       status))
    app.add_handler(CommandHandler("clear",        clear))
    app.add_handler(CommandHandler("gemini",       set_gemini))
    app.add_handler(CommandHandler("deepseek",     set_deepseek))
    app.add_handler(CommandHandler("deepseek_r1",  set_deepseek_r1))
    app.add_handler(CommandHandler("groq",         set_groq))
    app.add_handler(CommandHandler("qwen",         set_qwen))
    app.add_handler(CommandHandler("mistral",      set_mistral))
    app.add_handler(CommandHandler("mode",         mode_menu))
    app.add_handler(CommandHandler("default",      m_default))
    app.add_handler(CommandHandler("code",         m_code))
    app.add_handler(CommandHandler("translate",    m_translate))
    app.add_handler(CommandHandler("write",        m_write))
    app.add_handler(CommandHandler("analyze",      m_analyze))
    app.add_handler(CommandHandler("presentation", m_presentation))
    app.add_handler(CommandHandler("excel",        m_excel))
    app.add_handler(CommandHandler("image",        cmd_image))
    app.add_handler(CommandHandler("story",        cmd_story))
    app.add_handler(CommandHandler("gif",          cmd_gif))
    app.add_handler(CommandHandler("sum",          cmd_sum))
    app.add_handler(CommandHandler("fix",          cmd_fix))
    app.add_handler(CommandHandler("ideas",        cmd_ideas))
    app.add_handler(CommandHandler("explain",      cmd_explain))
    app.add_handler(CommandHandler("pptx",         cmd_pptx))
    app.add_handler(CommandHandler("table",        cmd_table))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    print("✅ Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
