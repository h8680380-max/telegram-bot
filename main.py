from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction
from groq import Groq
import urllib.request
import json
import os

# =============================================
# КЛЮЧИ
# =============================================
TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN",     "ВСТАВЬ_СЮДА")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "ВСТАВЬ_СЮДА")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY",       "ВСТАВЬ_СЮДА")

groq_client = Groq(api_key=GROQ_API_KEY)

# =============================================
# СОСТОЯНИЕ ПОЛЬЗОВАТЕЛЕЙ
# =============================================
user_modes   = {}
user_history = {}
user_model   = {}

# =============================================
# МОДЕЛИ
# source: "openrouter" или "groq"
# =============================================
OR_MODELS = {
    "groq":     ("groq",                                          "⚡ Groq Llama 3.3 70B",     "groq"),
    "nvidia":   ("nvidia/nemotron-3-super-120b-a12b:free",        "🟢 NVIDIA Nemotron 120B",   "openrouter"),
    "deepseek": ("deepseek/deepseek-r1:free",                     "🔍 DeepSeek R1",            "openrouter"),
    "qwen":     ("qwen/qwen3-235b-a22b:free",                     "🌸 Qwen3 235B",             "openrouter"),
    "mistral":  ("mistralai/mistral-small-3.1-24b-instruct:free", "🌬️ Mistral Small 3.1",     "openrouter"),
    "llama":    ("meta-llama/llama-3.3-70b-instruct:free",        "🦙 Llama 3.3 70B",         "openrouter"),
    "gemma":    ("google/gemma-3-27b-it:free",                    "💎 Gemma 3 27B",            "openrouter"),
    "ring":     ("inclusionai/ring-2.6-1t:free",                  "💍 Ring 2.6 1T",            "openrouter"),
    "owl":      ("openrouter/owl-alpha",                          "🦉 Owl Alpha",              "openrouter"),
}

DEFAULT_MODEL = "groq"

# =============================================
# ПРОМПТЫ
# =============================================
PROMPTS = {
    "default": (
        "Ты — умный универсальный ИИ-ассистент. "
        "Всегда отвечай на языке пользователя. "
        "Будь конкретным, полезным и дружелюбным. "
        "Если вопрос неоднозначный — уточни. "
        "Структурируй длинные ответы, используй списки и заголовки где уместно."
    ),
    "code": (
        "Ты — Senior Software Engineer с 15+ годами опыта. "
        "Правила: 1) Всегда указывай язык в блоке кода. "
        "2) Пиши чистый, читаемый код с комментариями на русском. "
        "3) Объясняй ПОЧЕМУ, а не только КАК. "
        "4) Указывай возможные edge cases и ошибки. "
        "5) Предлагай оптимизации и лучшие практики. "
        "6) Если видишь баг — сразу исправляй и объясняй что было не так."
    ),
    "translate": (
        "Ты — профессиональный переводчик-лингвист. "
        "Переводи между русским, английским и любыми другими языками. "
        "Правила: перевод должен звучать естественно, не дословно. "
        "Сохраняй стиль и тон оригинала. "
        "Если есть идиомы — переводи смысл, не слова. "
        "Для сложных терминов давай пояснение в скобках."
    ),
    "write": (
        "Ты — талантливый копирайтер и редактор с опытом в SMM, журналистике и маркетинге. "
        "Пиши живо, с эмоциями, конкретикой и примерами. "
        "Избегай клише и канцелярита. "
        "Структура: цепляющее начало — суть — призыв к действию. "
        "Адаптируй стиль под задачу: пост в соцсети, статья, письмо, история — всё разное."
    ),
    "analyze": (
        "Ты — аналитик-эксперт уровня McKinsey. "
        "Анализируй системно и глубоко. "
        "Структура ответа: 1) Суть проблемы 2) Анализ факторов 3) Данные и факты "
        "4) Альтернативные точки зрения 5) Выводы 6) Конкретные рекомендации. "
        "Используй цифры и факты там где возможно. "
        "Не бойся давать прямые оценки."
    ),
    "image": (
        "Ты — эксперт по генерации изображений с глубоким знанием Midjourney, DALL-E 3, Stable Diffusion, Flux. "
        "Для каждого запроса создавай детальный промпт на английском: "
        "субъект, стиль, освещение, цвета, камера/угол, настроение, качество. "
        "Добавляй технические параметры: --ar, --style, --v для Midjourney. "
        "Дополнительно давай краткое описание на русском что получится."
    ),
    "presentation": (
        "Ты — эксперт по публичным выступлениям и бизнес-презентациям. "
        "Создавай структуры по принципу 'одна идея — один слайд'. "
        "Для каждого слайда: заголовок (до 7 слов) + 3-4 коротких тезиса + идея для визуала. "
        "Начинай с проблемы аудитории, заканчивай четким призывом к действию. "
        "Используй принцип пирамиды Минто: главная мысль сначала."
    ),
    "excel": (
        "Ты — эксперт по Excel, Google Sheets и анализу данных. "
        "Объясняй формулы пошагово с примерами реальных данных. "
        "Всегда показывай: формулу, что делает каждая часть, пример использования. "
        "Для сложных задач предлагай несколько решений (формула / VBA / Power Query). "
        "Предупреждай о частых ошибках и как их избежать."
    ),
    "summarize": (
        "Ты — эксперт по сжатию информации. "
        "Структура: 1) Суть в 1-2 предложениях 2) Ключевые тезисы (3-5 пунктов) "
        "3) Важные детали которые нельзя упустить 4) Вывод. "
        "Убирай воду, оставляй только ценное. "
        "Сохраняй важные цифры, имена, даты."
    ),
    "explain": (
        "Ты — гениальный учитель который умеет объяснить любую сложную вещь просто. "
        "Объясняй как будто человек слышит это впервые. "
        "Используй: аналогии из повседневной жизни, конкретные примеры, сравнения. "
        "Структура: простое определение, аналогия, пример, почему это важно. "
        "Проверяй понимание в конце."
    ),
    "business": (
        "Ты — опытный бизнес-консультант и предприниматель. "
        "Мыслишь стратегически, ориентируешься на результат. "
        "Для любой бизнес-задачи: анализируй рынок, конкурентов, риски. "
        "Давай конкретные шаги с временными рамками. "
        "Фокусируйся на ROI и практической реализации."
    ),
    "legal": (
        "Ты — юридический консультант с широкими знаниями права. "
        "Объясняй законы и нормы понятным языком. "
        "Всегда указывай конкретные статьи и законы. "
        "Предупреждай о рисках и подводных камнях. "
        "Рекомендуй обратиться к профессиональному юристу для важных решений."
    ),
    "health": (
        "Ты — медицинский консультант с обширными знаниями в области здоровья. "
        "Давай информацию основанную на доказательной медицине. "
        "Объясняй симптомы, причины, методы лечения доступным языком. "
        "Всегда рекомендуй консультацию с врачом для серьезных вопросов. "
        "Не ставь диагнозы — информируй."
    ),
}

MODE_NAMES = {
    "default":      "Универсальный",
    "code":         "Программист",
    "translate":    "Переводчик",
    "write":        "Писатель",
    "analyze":      "Аналитик",
    "image":        "Промпты для картинок",
    "presentation": "Презентации",
    "excel":        "Excel/Таблицы",
    "summarize":    "Суммаризатор",
    "explain":      "Объяснятор",
    "business":     "Бизнес",
    "legal":        "Юрист",
    "health":       "Здоровье",
}

# =============================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================

def get_mode(uid):  return user_modes.get(uid, "default")
def get_model(uid): return user_model.get(uid, DEFAULT_MODEL)
def get_history(uid):
    if uid not in user_history: user_history[uid] = []
    return user_history[uid]

def get_model_label(uid):
    return OR_MODELS.get(get_model(uid), OR_MODELS[DEFAULT_MODEL])[1]

async def send(update, text):
    for i in range(0, len(text), 4096):
        await update.message.reply_text(text[i:i+4096])

async def call_openrouter(messages, model_id):
    body = json.dumps(
        {"model": model_id, "messages": messages, "max_tokens": 2048},
        ensure_ascii=False
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json; charset=utf-8",
            "HTTP-Referer": "https://t.me",
            "X-Title": "Telegram AI Bot",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()  # Пытаемся получить текст ошибки от OpenRouter
        error_code = e.code
        if error_code == 401:
            return "Ошибка авторизации: проверьте ваш OpenRouter API ключ и настройки приватности (Model Training должен быть включен)."
        elif error_code == 404:
            # Это ключевой момент: модель не найдена
            return f"Модель '{model_id}' не найдена. Возможно, идентификатор устарел. Проверьте список актуальных моделей на OpenRouter."
        elif error_code == 429:
            return "Превышен лимит запросов к OpenRouter. Бесплатные модели имеют ограничение 20 запросов в минуту и 200 в день."
        elif error_code == 502:
            return f"Провайдер модели '{model_id}' временно недоступен. Попробуйте позже или выберите другую модель."
        else:
            # Возвращаем детали ошибки для диагностики, но скрываем чувствительные данные
            return f"Ошибка API OpenRouter (код {error_code}). Подробности в логах для разработчика."
    except Exception as e:
        return f"Сетевая ошибка при обращении к OpenRouter: {e}"
        
async def ask(uid, message, mode_override=None):
    history = get_history(uid)
    mode    = mode_override or get_mode(uid)
    system  = PROMPTS.get(mode, PROMPTS["default"])
    model   = get_model(uid)
    source  = OR_MODELS[model][2]

    history.append({"role": "user", "content": message})
    if len(history) > 30:
        user_history[uid] = history[-30:]
        history = user_history[uid]

    messages = [{"role": "system", "content": system}] + history

    if source == "groq":
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=2048
        )
        reply = response.choices[0].message.content
    else:
        reply = await call_openrouter(messages, OR_MODELS[model][0])

    history.append({"role": "assistant", "content": reply})
    return reply

# =============================================
# КОМАНДЫ
# =============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    uid  = update.effective_user.id
    await update.message.reply_text(
        f"Привет, {name}! 👋\n\n"
        f"🧠 Модель: {get_model_label(uid)}\n"
        f"🎭 Режим: {MODE_NAMES.get(get_mode(uid))}\n\n"
        "Просто напиши что-нибудь или выбери команду:\n"
        "/help — все команды\n"
        "/models — выбрать модель\n"
        "/mode — сменить режим"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 ВСЕ КОМАНДЫ:\n\n"
        "━━━ 🧠 МОДЕЛИ ━━━\n"
        "/models — меню моделей\n"
        "/groq — ⚡ Groq Llama 70B (быстрый, твой ключ)\n"
        "/nvidia — 🟢 NVIDIA Nemotron 120B\n"
        "/deepseek — 🔍 DeepSeek R1\n"
        "/qwen — 🌸 Qwen3 235B\n"
        "/mistral — 🌬️ Mistral Small 3.1\n"
        "/llama — 🦙 Llama 3.3 70B\n"
        "/gemma — 💎 Gemma 3 27B\n"
        "/ring — 💍 Ring 2.6 1T\n"
        "/owl — 🦉 Owl Alpha\n\n"
        "━━━ 🎭 РЕЖИМЫ ━━━\n"
        "/mode — меню режимов\n"
        "/default — 🤖 Универсальный\n"
        "/code — 💻 Программист\n"
        "/translate — 🌍 Переводчик\n"
        "/write — ✍️ Писатель\n"
        "/analyze — 📊 Аналитик\n"
        "/business — 💼 Бизнес\n"
        "/legal — ⚖️ Юрист\n"
        "/health — 🏥 Здоровье\n\n"
        "━━━ 🛠️ ИНСТРУМЕНТЫ ━━━\n"
        "/sum <текст> — сжать текст\n"
        "/fix <текст> — исправить ошибки\n"
        "/explain <тема> — объяснить просто\n"
        "/ideas <тема> — генерация идей\n"
        "/image <идея> — промпт для картинки\n"
        "/story <тема> — написать историю\n"
        "/pptx <тема> — структура презентации\n"
        "/table <описание> — создать таблицу\n\n"
        "━━━ ⚙️ ПРОЧЕЕ ━━━\n"
        "/status — текущий режим и модель\n"
        "/clear — очистить историю\n"
    )

async def models_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(
        f"🤖 Текущая модель: {get_model_label(uid)}\n\n"
        "━━━ Выбери модель ━━━\n\n"
        "/groq — ⚡ Groq Llama 3.3 70B\n"
        "  Самый быстрый! Работает через твой Groq ключ\n\n"
        "/nvidia — 🟢 NVIDIA Nemotron 120B\n"
        "  120B параметров, топ для сложных задач\n\n"
        "/deepseek — 🔍 DeepSeek R1\n"
        "  Лучший для рассуждений и анализа\n\n"
        "/qwen — 🌸 Qwen3 235B\n"
        "  Огромная модель, отлично для сложных задач\n\n"
        "/mistral — 🌬️ Mistral Small 3.1 (24B)\n"
        "  Быстрая и точная\n\n"
        "/llama — 🦙 Llama 3.3 70B\n"
        "  Надёжная классика от Meta\n\n"
        "/gemma — 💎 Gemma 3 27B\n"
        "  От Google, хорошо для текстов\n\n"
        "/ring — 💍 Ring 2.6 1T\n"
        "  1 триллион параметров, для сложного кода\n\n"
        "/owl — 🦉 Owl Alpha\n"
        "  Агентные задачи, 1M контекст\n"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(
        f"🧠 Модель: {get_model_label(uid)}\n"
        f"🎭 Режим: {MODE_NAMES.get(get_mode(uid), get_mode(uid))}\n"
        f"💬 Сообщений в памяти: {len(get_history(uid))}"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_history[update.effective_user.id] = []
    await update.message.reply_text("🗑️ История очищена!")

# Переключение моделей
async def set_model_cmd(update, uid, key):
    user_model[uid] = key
    user_history[uid] = []
    name = OR_MODELS[key][1]
    await update.message.reply_text(f"{name} выбрана!\nИстория очищена.")

async def set_groq(u, c):     await set_model_cmd(u, u.effective_user.id, "groq")
async def set_nvidia(u, c):   await set_model_cmd(u, u.effective_user.id, "nvidia")
async def set_deepseek(u, c): await set_model_cmd(u, u.effective_user.id, "deepseek")
async def set_qwen(u, c):     await set_model_cmd(u, u.effective_user.id, "qwen")
async def set_mistral(u, c):  await set_model_cmd(u, u.effective_user.id, "mistral")
async def set_llama(u, c):    await set_model_cmd(u, u.effective_user.id, "llama")
async def set_gemma(u, c):    await set_model_cmd(u, u.effective_user.id, "gemma")
async def set_ring(u, c):     await set_model_cmd(u, u.effective_user.id, "ring")
async def set_owl(u, c):      await set_model_cmd(u, u.effective_user.id, "owl")

# Режимы
async def mode_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎭 Выбери режим:\n\n"
        "/default — 🤖 Универсальный\n"
        "/code — 💻 Программист\n"
        "/translate — 🌍 Переводчик\n"
        "/write — ✍️ Писатель\n"
        "/analyze — 📊 Аналитик\n"
        "/business — 💼 Бизнес\n"
        "/legal — ⚖️ Юрист\n"
        "/health — 🏥 Здоровье\n"
        "/presentation — 📋 Презентации\n"
        "/excel — 📗 Excel\n"
    )

async def set_m(update, context, mode, text):
    user_modes[update.effective_user.id] = mode
    await update.message.reply_text(text)

async def m_default(u, c):      await set_m(u, c, "default",      "🤖 Универсальный режим!")
async def m_code(u, c):         await set_m(u, c, "code",          "💻 Режим: Программист!")
async def m_translate(u, c):    await set_m(u, c, "translate",     "🌍 Режим: Переводчик!")
async def m_write(u, c):        await set_m(u, c, "write",         "✍️ Режим: Писатель!")
async def m_analyze(u, c):      await set_m(u, c, "analyze",       "📊 Режим: Аналитик!")
async def m_presentation(u, c): await set_m(u, c, "presentation",  "📋 Режим: Презентации!")
async def m_excel(u, c):        await set_m(u, c, "excel",         "📗 Режим: Excel!")
async def m_business(u, c):     await set_m(u, c, "business",      "💼 Режим: Бизнес!")
async def m_legal(u, c):        await set_m(u, c, "legal",         "⚖️ Режим: Юрист!")
async def m_health(u, c):       await set_m(u, c, "health",        "🏥 Режим: Здоровье!")

# Инструменты
async def quick_cmd(update, context, prompt_mode, label, prefix=""):
    args = " ".join(context.args) if context.args else None
    if not args:
        await update.message.reply_text(f"Использование: /{label} <текст>")
        return
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(update.effective_user.id, prefix + args, mode_override=prompt_mode)
        await send(update, reply)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def cmd_image(u, c):   await quick_cmd(u, c, "image",        "image")
async def cmd_sum(u, c):     await quick_cmd(u, c, "summarize",    "sum",     "Сожми этот текст: ")
async def cmd_explain(u, c): await quick_cmd(u, c, "explain",      "explain", "Объясни просто: ")
async def cmd_fix(u, c):     await quick_cmd(u, c, "write",        "fix",     "Исправь грамматику и пунктуацию, объясни ошибки: ")
async def cmd_ideas(u, c):   await quick_cmd(u, c, "write",        "ideas",   "Придумай 10 творческих идей с пояснениями: ")
async def cmd_pptx(u, c):    await quick_cmd(u, c, "presentation", "pptx",    "Создай структуру презентации 8-10 слайдов: ")
async def cmd_table(u, c):   await quick_cmd(u, c, "excel",        "table",   "Создай таблицу в Markdown: ")

async def cmd_story(u, c):
    args = " ".join(c.args) if c.args else None
    if not args:
        await u.message.reply_text("Использование: /story <тема>")
        return
    await c.bot.send_chat_action(chat_id=u.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask(
            u.effective_user.id,
            f"Напиши увлекательную историю (400-600 слов) с живыми персонажами, диалогами и неожиданным финалом: {args}",
            mode_override="write"
        )
        await send(u, reply)
    except Exception as e:
        await u.message.reply_text(f"❌ Ошибка: {e}")

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
        BotCommand("mode",         "🎭 Сменить режим"),
        BotCommand("status",       "📍 Текущий статус"),
        BotCommand("clear",        "🗑️ Очистить историю"),
        BotCommand("groq",         "⚡ Groq Llama 70B (быстрый)"),
        BotCommand("nvidia",       "🟢 NVIDIA Nemotron 120B"),
        BotCommand("deepseek",     "🔍 DeepSeek R1"),
        BotCommand("qwen",         "🌸 Qwen3 235B"),
        BotCommand("mistral",      "🌬️ Mistral Small 3.1"),
        BotCommand("llama",        "🦙 Llama 3.3 70B"),
        BotCommand("gemma",        "💎 Gemma 3 27B"),
        BotCommand("ring",         "💍 Ring 2.6 1T"),
        BotCommand("owl",          "🦉 Owl Alpha"),
        BotCommand("code",         "💻 Режим программиста"),
        BotCommand("translate",    "🌍 Переводчик"),
        BotCommand("write",        "✍️ Режим писателя"),
        BotCommand("analyze",      "📊 Режим аналитика"),
        BotCommand("business",     "💼 Бизнес-консультант"),
        BotCommand("legal",        "⚖️ Юридический советник"),
        BotCommand("health",       "🏥 Здоровье"),
        BotCommand("sum",          "📝 Сжать текст"),
        BotCommand("fix",          "✏️ Исправить текст"),
        BotCommand("explain",      "🧠 Объяснить просто"),
        BotCommand("ideas",        "💡 Генерация идей"),
        BotCommand("image",        "🖼️ Промпт для картинки"),
        BotCommand("story",        "📖 Написать историю"),
        BotCommand("pptx",         "📊 Структура презентации"),
        BotCommand("table",        "📗 Создать таблицу"),
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
    app.add_handler(CommandHandler("nvidia",       set_nvidia))
    app.add_handler(CommandHandler("deepseek",     set_deepseek))
    app.add_handler(CommandHandler("qwen",         set_qwen))
    app.add_handler(CommandHandler("mistral",      set_mistral))
    app.add_handler(CommandHandler("llama",        set_llama))
    app.add_handler(CommandHandler("gemma",        set_gemma))
    app.add_handler(CommandHandler("ring",         set_ring))
    app.add_handler(CommandHandler("owl",          set_owl))
    app.add_handler(CommandHandler("mode",         mode_menu))
    app.add_handler(CommandHandler("default",      m_default))
    app.add_handler(CommandHandler("code",         m_code))
    app.add_handler(CommandHandler("translate",    m_translate))
    app.add_handler(CommandHandler("write",        m_write))
    app.add_handler(CommandHandler("analyze",      m_analyze))
    app.add_handler(CommandHandler("presentation", m_presentation))
    app.add_handler(CommandHandler("excel",        m_excel))
    app.add_handler(CommandHandler("business",     m_business))
    app.add_handler(CommandHandler("legal",        m_legal))
    app.add_handler(CommandHandler("health",       m_health))
    app.add_handler(CommandHandler("image",        cmd_image))
    app.add_handler(CommandHandler("story",        cmd_story))
    app.add_handler(CommandHandler("sum",          cmd_sum))
    app.add_handler(CommandHandler("fix",          cmd_fix))
    app.add_handler(CommandHandler("ideas",        cmd_ideas))
    app.add_handler(CommandHandler("explain",      cmd_explain))
    app.add_handler(CommandHandler("pptx",         cmd_pptx))
    app.add_handler(CommandHandler("table",        cmd_table))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    print("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
