from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction
from groq import Groq
import httpx
import base64
import os

# =============================================
# КЛЮЧИ
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
    "groq": (
        "groq",
        "llama-3.3-70b-versatile",
        "🦙 Llama 3.3 70B",
        "Meta (Groq)",
        False,
        "Уровень GPT-4. Быстрый через Groq. Лучший для кода"
    ),
    "gemma": (
        "openrouter",
        "google/gemma-4-27b-it:free",
        "💎 Gemma 4 27B",
        "Google",
        True,
        "Новая модель Google. Поддерживает фото"
    ),
    "qwen": (
        "openrouter",
        "nvidia/nemotron-nano-12b-v2-vl:free",
        "👁️ Nemotron VL 12B",
        "NVIDIA",
        True,
        "Визуальная модель NVIDIA. Поддерживает фото"
    ),
    "gpt": (
        "openrouter",
        "openai/gpt-oss-20b:free",
        "💬 GPT OSS 20B",
        "OpenAI",
        False,
        "Открытая модель OpenAI. Универсальная"
    ),
    "nvidia": (
        "openrouter",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "⚡ Nemotron Super 120B",
        "NVIDIA",
        False,
        "Огромная модель 120B. Для сложных задач"
    ),
}

def src(k):  return MODELS[k][0]
def mid(k):  return MODELS[k][1]
def lbl(k):  return MODELS[k][2]
def comp(k): return MODELS[k][3]
def vis(k):  return MODELS[k][4]
def desc(k): return MODELS[k][5]

user_ai      = {}
user_modes   = {}
user_history = {}

def get_ai(uid):   return user_ai.get(uid, "groq")
def get_mode(uid): return user_modes.get(uid, "default")
def get_history(uid):
    if uid not in user_history: user_history[uid] = []
    return user_history[uid]

# =============================================
# ПРОМПТЫ
# =============================================
PROMPTS = {
    "default":      "You are a smart universal assistant. Always reply in the user's language. Be concrete and helpful.",
    "code":         "You are a Senior developer. Write clean code with comments. Explain why, not just how. Always specify language in code blocks. Suggest best practices.",
    "translate":    "You are a professional translator. Translate naturally, not word-for-word. Preserve the original style.",
    "write":        "You are a talented copywriter. Write vividly with emotions and specifics. Avoid cliches.",
    "analyze":      "You are an expert analyst. Structure: essence -> analysis -> facts -> conclusions -> recommendations.",
    "image":        "You are an expert in prompts for Midjourney/DALL-E/Stable Diffusion. Detailed prompts in English + description in the user's language.",
    "presentation": "You are a presentation expert. One idea per slide. Title up to 7 words + 3-4 points + visual idea.",
    "excel":        "You are an Excel and Google Sheets expert. Formulas with examples, explain each part.",
    "summarize":    "You are an expert at compressing information. Structure: essence in 1-2 sentences -> key points -> conclusion.",
    "explain":      "You are a brilliant teacher. Explain through life analogies. Simple definition -> analogy -> example -> why it matters.",
}

MODE_NAMES = {
    "default": "Универсальный", "code": "Программист",
    "translate": "Переводчик",  "write": "Писатель",
    "analyze": "Аналитик",      "image": "Промпты",
    "presentation": "Презентации", "excel": "Excel",
    "summarize": "Суммаризатор", "explain": "Объяснятор",
}

# =============================================
# ЗАПРОСЫ К ИИ
# =============================================
async def call_openrouter(messages, model_id):
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://t.me",
                "X-Title": "Telegram AI Bot",
            },
            json={"model": model_id, "messages": messages, "max_tokens": 2048},
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
    ai     = get_ai(uid)
    system = PROMPTS.get(get_mode(uid), PROMPTS["default"])
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{photo_b64}"}},
            {"type": "text", "text": caption or "Describe this image in detail."}
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
        "/groq — 🦙 Llama 70B Groq (быстро)\n"
        "/gemma — 💎 Gemma 4 27B Google 📸\n"
        "/qwen — 👁️ Nemotron VL NVIDIA 📸\n"
        "/gpt — 💬 GPT OSS 20B OpenAI\n"
        "/nvidia — ⚡ Nemotron Super 120B\n\n"
        "━━━ 📸 АНАЛИЗ ФОТО ━━━\n"
        "Просто отправь фото с подписью или без!\n"
        "Поддерживают: /gemma и /qwen\n\n"
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
        vis_icon = "📸" if m[4] else ""
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
    v = "📸 Поддерживает фото!" if vis(key) else "🚫 Фото не поддерживает\n\nДля фото: /gemma или /qwen"
    await update.message.reply_text(
        f"{lbl(key)}\n🏢 {comp(key)}\n{v}\n\nℹ️ {desc(key)}\n\nИстория очищена."
    )

async def set_groq(u,c):   await switch_ai(u, u.effective_user.id, "groq")
async def set_gemma(u,c):  await switch_ai(u, u.effective_user.id, "gemma")
async def set_qwen(u,c):   await switch_ai(u, u.effective_user.id, "qwen")
async def set_gpt(u,c):    await switch_ai(u, u.effective_user.id, "gpt")
async def set_nvidia(u,c): await switch_ai(u, u.effective_user.id, "nvidia")

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
            f"Write an engaging story (400-600 words) with vivid characters and unexpected ending: {args}",
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
            f"Describe a GIF animation frame by frame: {args}. Style, colors, English prompt.",
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
            "Для анализа фото переключись:\n"
            "/gemma — 💎 Gemma 4 27B\n"
            "/qwen — 👁️ Nemotron VL 12B"
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
        BotCommand("start",        "Начать"),
        BotCommand("help",         "Все команды"),
        BotCommand("models",       "Выбрать модель"),
        BotCommand("status",       "Текущий статус"),
        BotCommand("clear",        "Очистить историю"),
        BotCommand("groq",         "Llama 70B через Groq (быстро)"),
        BotCommand("gemma",        "Gemma 4 27B Google (фото)"),
        BotCommand("qwen",         "Nemotron VL NVIDIA (фото)"),
        BotCommand("gpt",          "GPT OSS 20B OpenAI"),
        BotCommand("nvidia",       "Nemotron Super 120B"),
        BotCommand("mode",         "Сменить режим"),
        BotCommand("code",         "Программист"),
        BotCommand("translate",    "Переводчик"),
        BotCommand("write",        "Писатель"),
        BotCommand("analyze",      "Аналитик"),
        BotCommand("sum",          "Сжать текст"),
        BotCommand("fix",          "Исправить текст"),
        BotCommand("explain",      "Объяснить просто"),
        BotCommand("ideas",        "Генерация идей"),
        BotCommand("image",        "Промпт для картинки"),
        BotCommand("story",        "Написать историю"),
        BotCommand("gif",          "Описание GIF"),
        BotCommand("pptx",         "Структура презентации"),
        BotCommand("table",        "Создать таблицу"),
    ])

def main():
    print("Бот запускается...")
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start",        start))
    app.add_handler(CommandHandler("help",         help_cmd))
    app.add_handler(CommandHandler("models",       models_cmd))
    app.add_handler(CommandHandler("status",       status))
    app.add_handler(CommandHandler("clear",        clear))
    app.add_handler(CommandHandler("groq",         set_groq))
    app.add_handler(CommandHandler("gemma",        set_gemma))
    app.add_handler(CommandHandler("qwen",         set_qwen))
    app.add_handler(CommandHandler("gpt",          set_gpt))
    app.add_handler(CommandHandler("nvidia",       set_nvidia))
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

    print("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
