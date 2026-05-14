from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatAction
from groq import Groq
import urllib.request
import json
import base64
import os
from datetime import datetime

# =============================================
# КЛЮЧИ
# =============================================
TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN",     "ВСТАВЬ_СЮДА")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "ВСТАВЬ_СЮДА")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY",       "ВСТАВЬ_СЮДА")

groq_client = Groq(api_key=GROQ_API_KEY)

# =============================================
# ДАННЫЕ ПОЛЬЗОВАТЕЛЕЙ
# =============================================
user_data    = {}  # профиль: цель, вес, рост, возраст
user_diary   = {}  # дневник питания { uid: { "2024-01-01": [...] } }
pending_photo = {} # ожидаем описание к фото { uid: photo_b64 }

def get_profile(uid):
    if uid not in user_data:
        user_data[uid] = {"goal": None, "weight": None, "height": None, "age": None, "gender": None}
    return user_data[uid]

def get_today(uid):
    today = datetime.now().strftime("%Y-%m-%d")
    if uid not in user_diary: user_diary[uid] = {}
    if today not in user_diary[uid]: user_diary[uid][today] = []
    return user_diary[uid][today], today

def add_meal(uid, meal_data):
    meals, today = get_today(uid)
    meal_data["time"] = datetime.now().strftime("%H:%M")
    meals.append(meal_data)

def get_daily_total(uid):
    meals, _ = get_today(uid)
    total = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0}
    for m in meals:
        total["calories"] += m.get("calories", 0)
        total["protein"]  += m.get("protein", 0)
        total["fat"]      += m.get("fat", 0)
        total["carbs"]    += m.get("carbs", 0)
    return total, meals

# =============================================
# ПРОМПТЫ
# =============================================
ANALYZE_PROMPT = """Ты - эксперт по питанию и диетолог.
Пользователь прислал фото еды и её описание.
Твоя задача - рассчитать калории и КЖБУ максимально точно.

Отвечай СТРОГО в формате JSON (ничего лишнего, только JSON):
{
  "dish": "название блюда",
  "calories": число,
  "protein": число,
  "fat": число,
  "carbs": число,
  "weight": число (примерный вес порции в граммах),
  "comment": "короткий комментарий о блюде (1-2 предложения)"
}

Все числа - целые, без единиц измерения."""

NUTRITION_PROMPT = """Ты - опытный диетолог и нутрициолог.
Даешь практичные советы по питанию, основанные на науке.
Отвечай на языке пользователя, кратко и по делу.
Учитывай цель пользователя если она указана."""

# =============================================
# ЗАПРОСЫ К ИИ
# =============================================

async def analyze_food_photo(photo_b64, description):
    body = json.dumps({
        "model": "google/gemini-2.0-flash-exp:free",
        "messages": [
            {"role": "system", "content": ANALYZE_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{photo_b64}"}},
                {"type": "text", "text": f"Описание от пользователя: {description}"}
            ]}
        ],
        "max_tokens": 500,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json; charset=utf-8",
            "HTTP-Referer": "https://t.me",
            "X-Title": "Calorie Bot",
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    content = data["choices"][0]["message"]["content"]
    # Чистим от markdown если есть
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    return json.loads(content.strip())

async def ask_nutrition(uid, question):
    profile = get_profile(uid)
    total, _ = get_daily_total(uid)

    context = ""
    if profile["goal"]:
        context += f"Цель пользователя: {profile['goal']}. "
    if profile["weight"] and profile["height"]:
        context += f"Вес: {profile['weight']}кг, рост: {profile['height']}см. "
    if total["calories"] > 0:
        context += f"Сегодня съедено: {total['calories']} ккал, белки: {total['protein']}г, жиры: {total['fat']}г, углеводы: {total['carbs']}г."

    messages = [
        {"role": "system", "content": NUTRITION_PROMPT + ("\n\nКонтекст: " + context if context else "")},
        {"role": "user", "content": question}
    ]
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=1024
    )
    return response.choices[0].message.content

async def send(update, text):
    for i in range(0, len(text), 4096):
        await update.message.reply_text(text[i:i+4096])

# =============================================
# КОМАНДЫ
# =============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"Привет, {name}! 👋\n\n"
        "Я помогу считать калории и следить за питанием!\n\n"
        "📸 Как пользоваться:\n"
        "1. Отправь фото еды\n"
        "2. Напиши описание что это\n"
        "3. Получи калории и КЖБУ\n\n"
        "💬 Или просто спроси что-нибудь про питание!\n\n"
        "/setup — настроить профиль\n"
        "/diary — дневник питания за сегодня\n"
        "/help — все команды"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 ВСЕ КОМАНДЫ:\n\n"
        "━━━ 📸 АНАЛИЗ ЕДЫ ━━━\n"
        "Отправь фото + описание — получи КЖБУ\n\n"
        "━━━ 📊 ДНЕВНИК ━━━\n"
        "/diary — питание за сегодня\n"
        "/week — питание за неделю\n"
        "/clear_diary — очистить дневник\n\n"
        "━━━ 👤 ПРОФИЛЬ ━━━\n"
        "/setup — настроить профиль\n"
        "/profile — показать профиль\n"
        "/goal — изменить цель\n\n"
        "━━━ 💡 СОВЕТЫ ━━━\n"
        "Просто напиши вопрос про питание!\n"
        "Примеры:\n"
        "- Что лучше есть перед тренировкой?\n"
        "- Сколько воды пить в день?\n"
        "- Как похудеть без голодовки?\n\n"
        "━━━ ⚙️ ПРОЧЕЕ ━━━\n"
        "/start — начало\n"
        "/help — эта справка\n"
    )

async def setup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👤 Настройка профиля\n\n"
        "Напиши свои данные в таком формате:\n"
        "/setprofile 25 70 175 м похудеть\n\n"
        "Параметры по порядку:\n"
        "• возраст (лет)\n"
        "• вес (кг)\n"
        "• рост (см)\n"
        "• пол (м/ж)\n"
        "• цель: похудеть / набрать / поддержать\n\n"
        "Пример:\n"
        "/setprofile 28 80 180 м похудеть"
    )

async def setprofile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    args = context.args
    if len(args) < 5:
        await update.message.reply_text(
            "Укажи все параметры:\n"
            "/setprofile возраст вес рост пол цель\n\n"
            "Пример: /setprofile 28 80 180 м похудеть"
        )
        return
    try:
        profile = get_profile(uid)
        profile["age"]    = int(args[0])
        profile["weight"] = int(args[1])
        profile["height"] = int(args[2])
        profile["gender"] = args[3].lower()
        profile["goal"]   = " ".join(args[4:])

        # Считаем норму калорий по формуле Миффлина-Сан Жеора
        w, h, a = profile["weight"], profile["height"], profile["age"]
        if profile["gender"] in ["м", "m"]:
            bmr = 10 * w + 6.25 * h - 5 * a + 5
        else:
            bmr = 10 * w + 6.25 * h - 5 * a - 161
        tdee = int(bmr * 1.55)  # умеренная активность

        if "похудеть" in profile["goal"]:
            target = tdee - 500
            goal_text = "похудение (-500 ккал)"
        elif "набрать" in profile["goal"]:
            target = tdee + 300
            goal_text = "набор массы (+300 ккал)"
        else:
            target = tdee
            goal_text = "поддержание веса"

        profile["target_calories"] = target

        await update.message.reply_text(
            f"✅ Профиль сохранён!\n\n"
            f"👤 Возраст: {profile['age']} лет\n"
            f"⚖️ Вес: {profile['weight']} кг\n"
            f"📏 Рост: {profile['height']} см\n"
            f"🎯 Цель: {goal_text}\n\n"
            f"🔥 Твоя норма калорий: {target} ккал/день\n\n"
            "Теперь отправляй фото еды и я буду считать!"
        )
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}\nПроверь формат данных.")

async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    profile = get_profile(uid)
    total, _ = get_daily_total(uid)

    if not profile["weight"]:
        await update.message.reply_text(
            "Профиль не настроен.\n"
            "Используй /setup чтобы настроить."
        )
        return

    target  = profile.get("target_calories", 0)
    remains = target - total["calories"] if target else 0

    await update.message.reply_text(
        f"👤 Твой профиль:\n\n"
        f"🎂 Возраст: {profile['age']} лет\n"
        f"⚖️ Вес: {profile['weight']} кг\n"
        f"📏 Рост: {profile['height']} см\n"
        f"🎯 Цель: {profile.get('goal', 'не указана')}\n"
        f"🔥 Норма: {target} ккал/день\n\n"
        f"📊 Сегодня:\n"
        f"Съедено: {total['calories']} ккал\n"
        f"Осталось: {max(0, remains)} ккал"
    )

async def goal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎯 Укажи цель:\n\n"
        "/setgoal похудеть\n"
        "/setgoal набрать\n"
        "/setgoal поддержать"
    )

async def setgoal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    args = " ".join(context.args)
    if not args:
        await update.message.reply_text("Укажи цель: /setgoal похудеть")
        return
    get_profile(uid)["goal"] = args
    await update.message.reply_text(f"✅ Цель обновлена: {args}")

async def diary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    total, meals = get_daily_total(uid)
    profile = get_profile(uid)
    target  = profile.get("target_calories", 0)

    if not meals:
        await update.message.reply_text(
            "📔 Дневник пуст\n\n"
            "Отправь фото еды чтобы начать отслеживать!"
        )
        return

    text = f"📔 Дневник питания — сегодня:\n\n"
    for i, m in enumerate(meals, 1):
        text += (
            f"{i}. {m['time']} — {m['dish']}\n"
            f"   🔥 {m['calories']} ккал | "
            f"Б:{m['protein']}г Ж:{m['fat']}г У:{m['carbs']}г\n\n"
        )

    text += f"━━━━━━━━━━━━━━━\n"
    text += f"📊 ИТОГО ЗА ДЕНЬ:\n"
    text += f"🔥 Калории: {total['calories']} ккал\n"
    text += f"🥩 Белки: {total['protein']} г\n"
    text += f"🧈 Жиры: {total['fat']} г\n"
    text += f"🍞 Углеводы: {total['carbs']} г\n"

    if target:
        remains = target - total["calories"]
        if remains > 0:
            text += f"\n✅ Осталось: {remains} ккал из {target}"
        else:
            text += f"\n⚠️ Превышение нормы на {abs(remains)} ккал!"

    await send(update, text)

async def week_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_diary or not user_diary[uid]:
        await update.message.reply_text("Нет данных за неделю.\nНачни отслеживать питание!")
        return

    text = "📅 Питание за последние дни:\n\n"
    for date, meals in sorted(user_diary[uid].items(), reverse=True)[:7]:
        total_cal = sum(m.get("calories", 0) for m in meals)
        count = len(meals)
        text += f"📆 {date}: {total_cal} ккал ({count} приёмов)\n"

    await send(update, text)

async def clear_diary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    today = datetime.now().strftime("%Y-%m-%d")
    if uid in user_diary and today in user_diary[uid]:
        user_diary[uid][today] = []
    await update.message.reply_text("🗑️ Дневник за сегодня очищен!")

# =============================================
# ОБРАБОТКА ФОТО
# =============================================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    # Скачиваем фото
    photo_file  = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()
    photo_b64   = base64.b64encode(photo_bytes).decode("utf-8")

    caption = update.message.caption or ""

    if caption:
        # Описание уже есть — сразу анализируем
        await process_food(update, context, uid, photo_b64, caption)
    else:
        # Сохраняем фото и просим описание
        pending_photo[uid] = photo_b64
        await update.message.reply_text(
            "📸 Фото получено!\n\n"
            "Напиши что это за блюдо и примерный состав.\n\n"
            "Примеры:\n"
            "• борщ со сметаной и хлебом\n"
            "• куриная грудка с рисом и овощами\n"
            "• овсянка с бананом и мёдом"
        )

async def process_food(update, context, uid, photo_b64, description):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        result = await analyze_food_photo(photo_b64, description)

        # Сохраняем в дневник
        add_meal(uid, {
            "dish":     result["dish"],
            "calories": result["calories"],
            "protein":  result["protein"],
            "fat":      result["fat"],
            "carbs":    result["carbs"],
        })

        total, _ = get_daily_total(uid)
        profile  = get_profile(uid)
        target   = profile.get("target_calories", 0)

        text = (
            f"✅ {result['dish']}\n\n"
            f"🔥 Калории: {result['calories']} ккал\n"
            f"🥩 Белки: {result['protein']} г\n"
            f"🧈 Жиры: {result['fat']} г\n"
            f"🍞 Углеводы: {result['carbs']} г\n"
            f"⚖️ Порция: ~{result['weight']} г\n\n"
            f"💬 {result['comment']}\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 За сегодня: {total['calories']} ккал"
        )

        if target:
            remains = target - total["calories"]
            if remains > 0:
                text += f" (осталось {remains} ккал)"
            else:
                text += f" ⚠️ превышение на {abs(remains)} ккал"

        await update.message.reply_text(text)

    except Exception as e:
        await update.message.reply_text(
            f"❌ Не удалось проанализировать фото: {e}\n\n"
            "Попробуй ещё раз или опиши блюдо подробнее."
        )

# =============================================
# ОБРАБОТКА ТЕКСТА
# =============================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = update.message.text

    # Если ждём описание к фото
    if uid in pending_photo:
        photo_b64 = pending_photo.pop(uid)
        await process_food(update, context, uid, photo_b64, text)
        return

    # Иначе — совет по питанию через Groq
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask_nutrition(uid, text)
        await send(update, reply)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

# =============================================
# ЗАПУСК
# =============================================

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start",        "Начать"),
        BotCommand("help",         "Все команды"),
        BotCommand("diary",        "Дневник питания сегодня"),
        BotCommand("week",         "Питание за неделю"),
        BotCommand("profile",      "Мой профиль"),
        BotCommand("setup",        "Настроить профиль"),
        BotCommand("goal",         "Изменить цель"),
        BotCommand("clear_diary",  "Очистить дневник"),
    ])

def main():
    print("Bot starting...")
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("help",        help_cmd))
    app.add_handler(CommandHandler("setup",       setup_cmd))
    app.add_handler(CommandHandler("setprofile",  setprofile_cmd))
    app.add_handler(CommandHandler("profile",     profile_cmd))
    app.add_handler(CommandHandler("goal",        goal_cmd))
    app.add_handler(CommandHandler("setgoal",     setgoal_cmd))
    app.add_handler(CommandHandler("diary",       diary_cmd))
    app.add_handler(CommandHandler("week",        week_cmd))
    app.add_handler(CommandHandler("clear_diary", clear_diary_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
