from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction
import google.generativeai as genai
from groq import Groq
import httpx
import os
import asyncio

# =============================================
# КЛЮЧИ — через переменные окружения Railway
# =============================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "ВСТАВЬ_СЮДА")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "ВСТАВЬ_СЮДА")
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY",   "ВСТАВЬ_СЮДА")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "ВСТАВЬ_СЮДА")

# =============================================
# ИНИЦИАЛИЗАЦИЯ (старые сервисы)
# =============================================
genai.configure(api_key=GEMINI_API_KEY)
_http = httpx.Client(transport=httpx.HTTPTransport(proxy=None))
groq_client = Groq(api_key=GROQ_API_KEY, http_client=_http)

user_ai      = {}      # gemini / groq
user_modes   = {}
user_history = {}

# =============================================
# OpenRouter (новые модели)
# =============================================
OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS = {
    "deepseek": "deepseek/deepseek-chat-v3-0324:free",
    "flash":    "google/gemini-2.0-flash-lite-preview-02-05:free",
    "gptoss":   "openai/gpt-oss-120b:free"
}
MODEL_NAMES = {
    "deepseek": "DeepSeek V3",
    "flash":    "Gemini 2.0 Flash Lite",
    "gptoss":   "GPT-OSS 120B"
}
user_ai_openrouter = {}   # deepseek / flash / gptoss

def get_openrouter_model(uid):
    return user_ai_openrouter.get(uid, "deepseek")

# =============================================
# СИСТЕМНЫЕ ПРОМПТЫ (старые)
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
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (старые)
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
            gemini_history.append({"role": role, "parts": [m["content"]]})
        model = genai.GenerativeModel("gemini-2.0-flash", system_instruction=system)
        chat  = model.start_chat(history=gemini_history)
        reply = chat.send_message(message).text
    else:  # groq
        messages = [{"role": "system", "content": system}] + history
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=2048,
        )
        reply = response.choices[0].message.content

    history.append({"role": "assistant", "content": reply})
    return reply

# =============================================
# OpenRouter ФУНКЦИЯ
# =============================================
async def ask_openrouter(uid, message, system_prompt, image_url=None):
    model_id = OPENROUTER_MODELS[get_openrouter_model(uid)]
    content = []
    if message:
        content.append({"type": "text", "text": message})
    if image_url:
        content.append({"type": "image_url", "image_url": {"url": image_url}})
    
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content if len(content) > 1 else message}
        ],
        "temperature": 0.7,
        "max_tokens": 4096
    }
    if not image_url:
        payload["messages"][1]["content"] = message
    
    try:
        response = httpx.post(
            f"{OPENROUTER_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=120.0
        )
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content']
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            return "❌ Лимит запросов (20/мин). Подождите."
        return f"❌ Ошибка API: {e}"
    except Exception as e:
        return f"❌ Ошибка: {e}"

# =============================================
# КОМАНДЫ (старые + новые)
# =============================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    ai   = get_ai(update.effective_user.id)
    await update.message.reply_text(
        f"Привет, {name}! 👋\n\n"
        f"Текущий ИИ: {'✨ Gemini' if ai == 'gemini' else '⚡ Groq'}\n"
        "Для новых моделей OpenRouter: /deepseek , /flash , /gptoss\n\n"
        "/help — все команды"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 ВСЕ КОМАНДЫ:\n\n"
        "━━━ 🧠 МОДЕЛИ (старые) ━━━\n"
        "/gemini — ✨ Google Gemini\n"
        "/groq — ⚡ Groq + Llama\n\n"
        "━━━ 🚀 НОВЫЕ МОДЕЛИ (OpenRouter) ━━━\n"
        "/deepseek — DeepSeek V3 (код, логика)\n"
        "/flash — Gemini 2.0 Flash Lite (1M контекст, быстро)\n"
        "/gptoss — GPT-OSS 120B (инструменты, структуры)\n"
        "/orstatus — текущая OpenRouter модель\n\n"
        "━━━ 🎭 РЕЖИМЫ (работают для старых моделей) ━━━\n"
        "/mode — меню режимов\n"
        "/code, /translate, /write, /analyze, /presentation, /excel\n\n"
        "━━━ 🎨 ТВОРЧЕСТВО ━━━\n"
        "/image, /story, /gif, /sum, /fix, /ideas, /explain, /pptx, /table\n\n"
        "━━━ ⚙️ ПРОЧЕЕ ━━━\n"
        "/status — старый статус\n"
        "/clear — очистить историю"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(
        f"🧠 Старая модель: {'✨ Gemini' if get_ai(uid) == 'gemini' else '⚡ Groq'}\n"
        f"🎭 Режим: {MODE_NAMES.get(get_mode(uid), get_mode(uid))}\n"
        f"💬 Сообщений в памяти: {len(get_history(uid))}"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_history[update.effective_user.id] = []
    await update.message.reply_text("🗑️ История старых моделей очищена!")

async def set_gemini(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_ai[uid] = "gemini"
    user_history[uid] = []
    await update.message.reply_text("✨ Переключено на Google Gemini!\nИстория очищена.")

async def set_groq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_ai[uid] = "groq"
    user_history[uid] = []
    await update.message.reply_text("⚡ Переключено на Groq + Llama!\nИстория очищена.")

# --- Режимы (старые) ---
async def mode_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎭 Выбери режим:\n/default, /code, /translate, /write, /analyze, /presentation, /excel")

async def set_m(update, context, mode, text):
    user_modes[update.effective_user.id] = mode
    await update.message.reply_text(text)

async def m_default(u,c):      await set_m(u,c,"default","🤖 Обычный режим!")
async def m_code(u,c):         await set_m(u,c,"code","💻 Режим: Программист!")
async def m_translate(u,c):    await set_m(u,c,"translate","🌍 Режим: Переводчик!")
async def m_write(u,c):        await set_m(u,c,"write","✍️ Режим: Писатель!")
async def m_analyze(u,c):      await set_m(u,c,"analyze","📊 Режим: Аналитик!")
async def m_presentation(u,c): await set_m(u,c,"presentation","📋 Режим: Презентации!")
async def m_excel(u,c):        await set_m(u,c,"excel","📗 Режим: Excel!")

# --- Быстрые команды (старые) ---
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
    if not args: await u.message.reply_text("Использование: /story <тема>"); return
    await c.bot.send_chat_action(chat_id=u.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(u.effective_user.id, f"Напиши интересную историю (300-500 слов): {args}", mode_override="write")
        await send(u, reply)
    except Exception as e: await u.message.reply_text(f"❌ Ошибка: {e}")
async def cmd_gif(u,c):
    args = " ".join(c.args) if c.args else None
    if not args: await u.message.reply_text("Использование: /gif <идея>"); return
    await c.bot.send_chat_action(chat_id=u.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(u.effective_user.id, f"Опиши детально GIF-анимацию: {args}. Покадрово, стиль, цвета. Промпт на английском.", mode_override="image")
        await send(u, reply)
    except Exception as e: await u.message.reply_text(f"❌ Ошибка: {e}")
async def cmd_fix(u,c):
    args = " ".join(c.args) if c.args else None
    if not args: await u.message.reply_text("Использование: /fix <текст>"); return
    await c.bot.send_chat_action(chat_id=u.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(u.effective_user.id, f"Исправь грамматику и пунктуацию, объясни ошибки:\n\n{args}", mode_override="write")
        await send(u, reply)
    except Exception as e: await u.message.reply_text(f"❌ Ошибка: {e}")
async def cmd_ideas(u,c):
    args = " ".join(c.args) if c.args else None
    if not args: await u.message.reply_text("Использование: /ideas <тема>"); return
    await c.bot.send_chat_action(chat_id=u.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(u.effective_user.id, f"Придумай 10 творческих идей: {args}. Для каждой — пояснение.", mode_override="write")
        await send(u, reply)
    except Exception as e: await u.message.reply_text(f"❌ Ошибка: {e}")
async def cmd_pptx(u,c):
    args = " ".join(c.args) if c.args else None
    if not args: await u.message.reply_text("Использование: /pptx <тема>"); return
    await c.bot.send_chat_action(chat_id=u.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(u.effective_user.id, f"Создай структуру презентации 8-10 слайдов: {args}. Заголовок, тезисы, визуал.", mode_override="presentation")
        await send(u, reply)
    except Exception as e: await u.message.reply_text(f"❌ Ошибка: {e}")
async def cmd_table(u,c):
    args = " ".join(c.args) if c.args else None
    if not args: await u.message.reply_text("Использование: /table <описание>"); return
    await c.bot.send_chat_action(chat_id=u.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(u.effective_user.id, f"Создай таблицу в Markdown: {args}", mode_override="excel")
        await send(u, reply)
    except Exception as e: await u.message.reply_text(f"❌ Ошибка: {e}")

# --- Обработчик старых текстовых сообщений ---
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(update.effective_user.id, update.message.text)
        await send(update, reply)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}\nПопробуй /clear")

# =============================================
# НОВЫЕ КОМАНДЫ ДЛЯ OPENROUTER
# =============================================
async def openrouter_deepseek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_ai_openrouter[update.effective_user.id] = "deepseek"
    await update.message.reply_text(f"✨ {MODEL_NAMES['deepseek']}\n💪 Код, математика, сложные задачи")

async def openrouter_flash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_ai_openrouter[update.effective_user.id] = "flash"
    await update.message.reply_text(f"⚡ {MODEL_NAMES['flash']}\n🚀 Быстрая, 1M контекста")

async def openrouter_gptoss(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_ai_openrouter[update.effective_user.id] = "gptoss"
    await update.message.reply_text(f"🤖 {MODEL_NAMES['gptoss']}\n🏆 120B параметров, инструменты")

async def openrouter_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    curr = get_openrouter_model(uid)
    await update.message.reply_text(
        f"🧠 Текущая OpenRouter модель: *{MODEL_NAMES[curr]}*\n\n"
        f"Команды:\n/deepseek\n/flash\n/gptoss\n/orstatus\n\n📸 Отправляйте фото для анализа",
        parse_mode='Markdown'
    )

async def handle_openrouter_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_message = update.message.text
    model_name = MODEL_NAMES[get_openrouter_model(uid)]
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    reply = await ask_openrouter(uid, user_message, "Ты полезный ассистент. Отвечай на языке пользователя.")
    full = f"💬 *{model_name}*\n{reply}"
    for i in range(0, len(full), 4096):
        await update.message.reply_text(full[i:i+4096], parse_mode='Markdown')

async def handle_openrouter_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    model_name = MODEL_NAMES[get_openrouter_model(uid)]
    photo_file = await update.message.photo[-1].get_file()
    photo_url = photo_file.file_path
    caption = update.message.caption or "Опиши что на этом изображении"
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    reply = await ask_openrouter(uid, caption, "Ты ассистент с возможностью видеть. Анализируй изображения детально.", image_url=photo_url)
    full = f"🖼️ *{model_name}*\n{reply}"
    for i in range(0, len(full), 4096):
        await update.message.reply_text(full[i:i+4096], parse_mode='Markdown')

# =============================================
# ЗАПУСК (исправленный)
# =============================================
async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start",        "🚀 Начать"),
        BotCommand("help",         "📋 Все команды"),
        BotCommand("gemini",       "✨ Google Gemini"),
        BotCommand("groq",         "⚡ Groq + Llama"),
        BotCommand("deepseek",     "🐋 DeepSeek V3 (OpenRouter)"),
        BotCommand("flash",        "⚡ Gemini Flash Lite (OpenRouter)"),
        BotCommand("gptoss",       "🤖 GPT-OSS 120B (OpenRouter)"),
        BotCommand("orstatus",     "📊 OpenRouter модель"),
        BotCommand("mode",         "🎭 Сменить режим"),
        BotCommand("status",       "📍 Старый статус"),
        BotCommand("clear",        "🗑️ Очистить историю"),
        BotCommand("code",         "💻 Режим программиста"),
        BotCommand("translate",    "🌍 Переводчик"),
        BotCommand("write",        "✍️ Писатель"),
        BotCommand("analyze",      "📊 Аналитик"),
        BotCommand("image",        "🖼️ Промпт картинки"),
        BotCommand("story",        "📖 История"),
        BotCommand("gif",          "🎞️ GIF описание"),
        BotCommand("sum",          "📝 Сжать текст"),
        BotCommand("fix",          "✏️ Исправить текст"),
        BotCommand("ideas",        "💡 Идеи"),
        BotCommand("explain",      "🧠 Объяснить"),
        BotCommand("pptx",         "📊 Структура презентации"),
        BotCommand("table",        "📗 Таблица"),
    ])

async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    # Старые хендлеры
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

    # Новые хендлеры OpenRouter
    app.add_handler(CommandHandler("deepseek", openrouter_deepseek))
    app.add_handler(CommandHandler("flash",   openrouter_flash))
    app.add_handler(CommandHandler("gptoss",  openrouter_gptoss))
    app.add_handler(CommandHandler("orstatus", openrouter_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_openrouter_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_openrouter_photo))

    # Сбрасываем вебхук
    await app.bot.delete_webhook(drop_pending_updates=True)
    print("✅ Бот запущен, начинаем polling...")
    await app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    asyncio.run(main())
