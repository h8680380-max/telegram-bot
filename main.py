from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction
from google import genai
from groq import Groq
import httpx
import os

# =============================================
# КЛЮЧИ — на Railway вставим через переменные
# =============================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "ВСТАВЬ_СЮДА")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "ВСТАВЬ_СЮДА")
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY",   "ВСТАВЬ_СЮДА")

# =============================================
# ИНИЦИАЛИЗАЦИЯ
# =============================================
gemini_client = genai.Client(api_key=GEMINI_API_KEY)
_http = httpx.Client(transport=httpx.HTTPTransport(proxy=None))
groq_client = Groq(api_key=GROQ_API_KEY, http_client=_http)

user_ai      = {}
user_modes   = {}
user_history = {}

# =============================================
# СИСТЕМНЫЕ ПРОМПТЫ
# =============================================
PROMPTS = {
    "default":      "Ты — умный универсальный ассистент. Отвечай на языке пользователя. Будь дружелюбным и полезным.",
    "code":         "Ты — опытный программист. Пиши чистый код с комментариями. Объясняй каждый шаг. Всегда указывай язык в блоке кода. Предлагай лучшие практики.",
    "translate":    "Ты — профессиональный переводчик. Русский↔Английский и любые другие языки. Перевод естественный, не дословный.",
    "write":        "Ты — талантливый копирайтер. Пишешь тексты для соцсетей, статьи, письма, истории. Живой язык, эмоции, конкретика.",
    "analyze":      "Ты — аналитик-эксперт. Анализируй глубоко. Структура: проблема → анализ → выводы → рекомендации.",
    "image":        "Ты — эксперт по промптам для Midjourney/DALL-E/Stable Diffusion. Детальные промпты на английском + описание на русском.",
    "presentation": "Ты — эксперт по презентациям. Чёткая структура слайдов: заголовок + 3-4 тезиса + идея для визуала.",
    "excel":        "Ты — эксперт по Excel и Google Sheets. Формулы с примерами, макросы на VBA, сводные таблицы.",
    "summarize":    "Ты — эксперт по сжатию информации. Выдели главное, убери воду. Структура: суть → ключевые тезисы.",
    "explain":      "Ты — учитель. Объясняй как 12-летнему: без жаргона, с аналогиями из жизни, примерами.",
}

MODE_NAMES = {
    "default": "🤖 Обычный", "code": "💻 Программист",
    "translate": "🌍 Переводчик", "write": "✍️ Писатель",
    "analyze": "📊 Аналитик", "image": "🖼️ Промпты",
    "presentation": "📋 Презентации", "excel": "📗 Excel",
    "summarize": "📝 Суммаризатор", "explain": "🧠 Объяснятор",
}

# =============================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================

def get_ai(uid):   return user_ai.get(uid, "gemini")
def get_mode(uid): return user_modes.get(uid, "default")
def get_history(uid):
    if uid not in user_history: user_history[uid] = []
    return user_history[uid]

async def send(update, text):
    for i in range(0, len(text), 4096):
        await update.message.reply_text(text[i:i+4096])

async def ask(uid, message, mode_override=None):
    history = get_history(uid)
    system  = PROMPTS.get(mode_override or get_mode(uid), PROMPTS["default"])
    ai      = get_ai(uid)

    history.append({"role": "user", "content": message})
    if len(history) > 20:
        user_history[uid] = history[-20:]
        history = user_history[uid]

    if ai == "gemini":
        gemini_history = []
        for m in history[:-1]:
            role = "user" if m["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [{"text": m["content"]}]})
        gemini_history.append({"role": "user", "parts": [{"text": message}]})
        response = gemini_client.models.generate_content(
            model="gemini-1.5-flash",
            contents=gemini_history,
            config={"system_instruction": system}
        )
        reply = response.text
    else:
        messages = [{"role": "system", "content": system}] + history
        response = groq_client.chat.completions.create(
            model="llama3-70b-8192",
            messages=messages,
            max_tokens=2048,
        )
        reply = response.choices[0].message.content

    history.append({"role": "assistant", "content": reply})
    return reply

# =============================================
# КОМАНДЫ
# =============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    ai   = get_ai(update.effective_user.id)
    await update.message.reply_text(
        f"Привет, {name}! 👋\n\n"
        f"Текущий ИИ: {'✨ Gemini' if ai == 'gemini' else '⚡ Groq'}\n\n"
        "Просто напиши мне что-нибудь!\n"
        "/help — все команды"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 ВСЕ КОМАНДЫ:\n\n"
        "━━━ 🧠 МОДЕЛЬ ━━━\n"
        "/gemini — ✨ Gemini (Google, бесплатно)\n"
        "/groq — ⚡ Groq + Llama (бесплатно, быстро)\n\n"
        "━━━ 🎭 РЕЖИМЫ ━━━\n"
        "/mode — меню режимов\n"
        "/default — 🤖 обычный\n"
        "/code — 💻 программист\n"
        "/translate — 🌍 переводчик\n"
        "/write — ✍️ писатель\n"
        "/analyze — 📊 аналитик\n"
        "/presentation — 📋 презентации\n"
        "/excel — 📗 Excel\n\n"
        "━━━ 🎨 ТВОРЧЕСТВО ━━━\n"
        "/image <идея> — промпт для картинки\n"
        "/story <тема> — написать историю\n"
        "/gif <идея> — описание GIF\n\n"
        "━━━ 🛠️ ИНСТРУМЕНТЫ ━━━\n"
        "/sum <текст> — сжать текст\n"
        "/fix <текст> — исправить ошибки\n"
        "/ideas <тема> — генерация идей\n"
        "/explain <тема> — объяснить просто\n"
        "/pptx <тема> — структура презентации\n"
        "/table <описание> — создать таблицу\n\n"
        "━━━ ⚙️ ПРОЧЕЕ ━━━\n"
        "/status — текущий режим и модель\n"
        "/clear — очистить историю\n"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(
        f"🧠 Модель: {'✨ Gemini' if get_ai(uid) == 'gemini' else '⚡ Groq'}\n"
        f"🎭 Режим: {MODE_NAMES.get(get_mode(uid), get_mode(uid))}\n"
        f"💬 Сообщений в памяти: {len(get_history(uid))}"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_history[update.effective_user.id] = []
    await update.message.reply_text("🗑️ История очищена!")

async def set_gemini(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_ai[uid] = "gemini"
    user_history[uid] = []
    await update.message.reply_text("✨ Переключено на Gemini!\nИстория очищена.")

async def set_groq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_ai[uid] = "groq"
    user_history[uid] = []
    await update.message.reply_text("⚡ Переключено на Groq + Llama!\nИстория очищена.")

async def mode_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎭 Выбери режим:\n\n"
        "/default — 🤖 Обычный\n"
        "/code — 💻 Программист\n"
        "/translate — 🌍 Переводчик\n"
        "/write — ✍️ Писатель\n"
        "/analyze — 📊 Аналитик\n"
        "/presentation — 📋 Презентации\n"
        "/excel — 📗 Excel\n"
    )

async def set_m(update, context, mode, text):
    user_modes[update.effective_user.id] = mode
    await update.message.reply_text(text)

async def m_default(u,c):      await set_m(u,c,"default",     "🤖 Обычный режим!")
async def m_code(u,c):         await set_m(u,c,"code",         "💻 Режим: Программист!")
async def m_translate(u,c):    await set_m(u,c,"translate",    "🌍 Режим: Переводчик!")
async def m_write(u,c):        await set_m(u,c,"write",        "✍️ Режим: Писатель!")
async def m_analyze(u,c):      await set_m(u,c,"analyze",      "📊 Режим: Аналитик!")
async def m_presentation(u,c): await set_m(u,c,"presentation", "📋 Режим: Презентации!")
async def m_excel(u,c):        await set_m(u,c,"excel",        "📗 Режим: Excel!")

async def quick_cmd(update, context, prompt_mode, label):
    args = " ".join(context.args) if context.args else None
    if not args:
        await update.message.reply_text(f"Использование: /{label} <текст>"); return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(update.effective_user.id, args, mode_override=prompt_mode)
        await send(update, reply)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def cmd_image(u,c):   await quick_cmd(u,c,"image","image")
async def cmd_sum(u,c):     await quick_cmd(u,c,"summarize","sum")
async def cmd_explain(u,c): await quick_cmd(u,c,"explain","explain")

async def cmd_story(u,c):
    args = " ".join(c.args) if c.args else None
    if not args:
        await u.message.reply_text("Использование: /story <тема>"); return
    await c.bot.send_chat_action(chat_id=u.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(u.effective_user.id, f"Напиши интересную историю (300-500 слов): {args}", mode_override="write")
        await send(u, reply)
    except Exception as e:
        await u.message.reply_text(f"❌ Ошибка: {e}")

async def cmd_gif(u,c):
    args = " ".join(c.args) if c.args else None
    if not args:
        await u.message.reply_text("Использование: /gif <идея>"); return
    await c.bot.send_chat_action(chat_id=u.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(u.effective_user.id, f"Опиши детально GIF-анимацию: {args}. Покадрово, стиль, цвета. Промпт на английском.", mode_override="image")
        await send(u, reply)
    except Exception as e:
        await u.message.reply_text(f"❌ Ошибка: {e}")

async def cmd_fix(u,c):
    args = " ".join(c.args) if c.args else None
    if not args:
        await u.message.reply_text("Использование: /fix <текст>"); return
    await c.bot.send_chat_action(chat_id=u.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(u.effective_user.id, f"Исправь грамматику и пунктуацию, объясни ошибки:\n\n{args}", mode_override="write")
        await send(u, reply)
    except Exception as e:
        await u.message.reply_text(f"❌ Ошибка: {e}")

async def cmd_ideas(u,c):
    args = " ".join(c.args) if c.args else None
    if not args:
        await u.message.reply_text("Использование: /ideas <тема>"); return
    await c.bot.send_chat_action(chat_id=u.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(u.effective_user.id, f"Придумай 10 творческих идей: {args}. Для каждой — пояснение.", mode_override="write")
        await send(u, reply)
    except Exception as e:
        await u.message.reply_text(f"❌ Ошибка: {e}")

async def cmd_pptx(u,c):
    args = " ".join(c.args) if c.args else None
    if not args:
        await u.message.reply_text("Использование: /pptx <тема>"); return
    await c.bot.send_chat_action(chat_id=u.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(u.effective_user.id, f"Создай структуру презентации 8-10 слайдов: {args}. Заголовок, тезисы, визуал.", mode_override="presentation")
        await send(u, reply)
    except Exception as e:
        await u.message.reply_text(f"❌ Ошибка: {e}")

async def cmd_table(u,c):
    args = " ".join(c.args) if c.args else None
    if not args:
        await u.message.reply_text("Использование: /table <описание>"); return
    await c.bot.send_chat_action(chat_id=u.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(u.effective_user.id, f"Создай таблицу в Markdown: {args}", mode_override="excel")
        await send(u, reply)
    except Exception as e:
        await u.message.reply_text(f"❌ Ошибка: {e}")

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(update.effective_user.id, update.message.text)
        await send(update, reply)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}\nПопробуй /clear")

# =============================================
# ЗАПУСК
# =============================================

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start",        "🚀 Начать"),
        BotCommand("help",         "📋 Все команды"),
        BotCommand("gemini",       "✨ Переключить на Gemini"),
        BotCommand("groq",         "⚡ Переключить на Groq"),
        BotCommand("mode",         "🎭 Сменить режим"),
        BotCommand("status",       "📍 Текущий статус"),
        BotCommand("clear",        "🗑️ Очистить историю"),
        BotCommand("code",         "💻 Режим программиста"),
        BotCommand("translate",    "🌍 Переводчик"),
        BotCommand("write",        "✍️ Режим писателя"),
        BotCommand("analyze",      "📊 Режим аналитика"),
        BotCommand("image",        "🖼️ Промпт для картинки"),
        BotCommand("story",        "📖 Написать историю"),
        BotCommand("gif",          "🎞️ Описание GIF"),
        BotCommand("sum",          "📝 Сжать текст"),
        BotCommand("fix",          "✏️ Исправить текст"),
        BotCommand("ideas",        "💡 Генерация идей"),
        BotCommand("explain",      "🧠 Объяснить просто"),
        BotCommand("pptx",         "📊 Структура презентации"),
        BotCommand("table",        "📗 Создать таблицу"),
    ])

def main():
    print("🤖 Бот запускается...")
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start",        start))
    app.add_handler(CommandHandler("help",         help_cmd))
    app.add_handler(CommandHandler("status",       status))
    app.add_handler(CommandHandler("clear",        clear))
    app.add_handler(CommandHandler("gemini",       set_gemini))
    app.add_handler(CommandHandler("groq",         set_groq))
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    print("✅ Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
