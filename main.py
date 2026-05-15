from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
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
user_profiles  = {}
user_diary     = {}
user_custom    = {}
user_favorites = {}
user_state     = {}
pending_photo  = {}

def get_profile(uid):
    if uid not in user_profiles:
        user_profiles[uid] = {
            "goal": None, "weight": None, "height": None,
            "age": None, "gender": None, "target_calories": None
        }
    return user_profiles[uid]

def get_today_meals(uid):
    today = datetime.now().strftime("%Y-%m-%d")
    if uid not in user_diary: user_diary[uid] = {}
    if today not in user_diary[uid]: user_diary[uid][today] = []
    return user_diary[uid][today], today

def add_meal(uid, meal):
    meals, _ = get_today_meals(uid)
    meal["time"] = datetime.now().strftime("%H:%M")
    meals.append(meal)

def get_daily_total(uid):
    meals, _ = get_today_meals(uid)
    t = {"calories": 0, "protein": 0, "fat": 0, "carbs": 0}
    for m in meals:
        t["calories"] += m.get("calories", 0)
        t["protein"]  += m.get("protein", 0)
        t["fat"]      += m.get("fat", 0)
        t["carbs"]    += m.get("carbs", 0)
    return t, meals

def get_custom_foods(uid):
    if uid not in user_custom: user_custom[uid] = {}
    return user_custom[uid]

def get_favorites(uid):
    if uid not in user_favorites: user_favorites[uid] = {}
    return user_favorites[uid]

def set_state(uid, state): user_state[uid] = state
def get_state(uid): return user_state.get(uid)
def clear_state(uid): user_state.pop(uid, None)

async def send(update, text):
    for i in range(0, len(text), 4096):
        await update.message.reply_text(text[i:i+4096])

# =============================================
# ПРОМПТЫ
# =============================================
ANALYZE_PHOTO_PROMPT = (
    "Ты - эксперт диетолог. Пользователь прислал фото еды и описание. "
    "Рассчитай калории и КЖБУ. "
    "Отвечай СТРОГО в формате JSON без лишнего текста: "
    "{\"dish\":\"название\",\"calories\":число,\"protein\":число,\"fat\":число,\"carbs\":число,"
    "\"weight\":число,\"comment\":\"1-2 предложения о блюде\"} "
    "Все числа целые."
)

ANALYZE_TEXT_PROMPT = (
    "Ты - эксперт диетолог. Пользователь написал что съел. "
    "Рассчитай калории и КЖБУ максимально точно. "
    "Если указан вес - используй его. Если нет - возьми стандартную порцию. "
    "Отвечай СТРОГО в формате JSON без лишнего текста: "
    "{\"dish\":\"название\",\"calories\":число,\"protein\":число,\"fat\":число,\"carbs\":число,"
    "\"weight\":число,\"comment\":\"1-2 предложения\"} "
    "Все числа целые."
)

NUTRITION_PROMPT = (
    "Ты - опытный диетолог. Даешь практичные советы по питанию. "
    "Отвечай на языке пользователя, кратко и по делу."
)

# =============================================
# ЗАПРОСЫ К ИИ
# =============================================

def parse_json_response(content):
    content = content.strip()
    if "```" in content:
        for part in content.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                content = part
                break
    return json.loads(content)

async def call_vision(photo_b64, description):
    body = json.dumps({
        "model": "google/gemma-4-27b-it:free",
        "messages": [
            {"role": "system", "content": ANALYZE_PHOTO_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + photo_b64}},
                {"type": "text", "text": "Описание: " + description}
            ]}
        ],
        "max_tokens": 400,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": "Bearer " + OPENROUTER_API_KEY,
            "Content-Type": "application/json; charset=utf-8",
            "HTTP-Referer": "https://t.me",
            "X-Title": "CalorieSnap",
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return parse_json_response(data["choices"][0]["message"]["content"])

async def lookup_barcode(barcode):
    url = "https://world.openfoodfacts.org/api/v0/product/" + barcode + ".json"
    req = urllib.request.Request(url, headers={"User-Agent": "CalorieBot/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("status") != 1:
        return None
    p = data["product"]
    n = p.get("nutriments", {})
    name = p.get("product_name_ru") or p.get("product_name") or "Неизвестный продукт"
    return {
        "dish":     name,
        "calories": int(n.get("energy-kcal_100g", n.get("energy_100g", 0)) or 0),
        "protein":  round(float(n.get("proteins_100g", 0) or 0), 1),
        "fat":      round(float(n.get("fat_100g", 0) or 0), 1),
        "carbs":    round(float(n.get("carbohydrates_100g", 0) or 0), 1),
        "weight":   100,
        "comment":  "Данные с упаковки (на 100г)"
    }

async def call_groq_json(prompt, user_text):
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_text}
        ],
        max_tokens=400
    )
    return parse_json_response(response.choices[0].message.content)

async def ask_nutrition(uid, question):
    profile = get_profile(uid)
    total, _ = get_daily_total(uid)
    ctx = ""
    if profile["goal"]: ctx += "Цель: " + profile["goal"] + ". "
    if profile["weight"]: ctx += "Вес: " + str(profile["weight"]) + "кг. "
    if total["calories"] > 0: ctx += "Сегодня: " + str(total["calories"]) + " ккал."
    system = NUTRITION_PROMPT + ("\nКонтекст: " + ctx if ctx else "")
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": question}],
        max_tokens=800
    )
    return response.choices[0].message.content

def format_meal_added(result, uid):
    total, _ = get_daily_total(uid)
    profile  = get_profile(uid)
    target   = profile.get("target_calories", 0)
    text = (
        "Добавлено: " + result["dish"] + "\n\n"
        "Калории: " + str(result["calories"]) + " ккал\n"
        "Белки: " + str(result["protein"]) + " г\n"
        "Жиры: " + str(result["fat"]) + " г\n"
        "Углеводы: " + str(result["carbs"]) + " г\n"
        "Порция: ~" + str(result["weight"]) + " г\n"
    )
    if result.get("comment"):
        text += "\n" + result["comment"] + "\n"
    text += "\nЗа сегодня: " + str(total["calories"]) + " ккал"
    if target:
        remains = target - total["calories"]
        text += " (осталось " + str(max(0, remains)) + " из " + str(target) + ")"
    return text

def add_food_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ В избранное",     callback_data="save_last_fav")],
        [InlineKeyboardButton("✏️ Исправить ккал", callback_data="edit_calories")],
    ])

# =============================================
# МЕНЮ ДОБАВЛЕНИЯ ЕДЫ
# =============================================

def add_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Фото блюда",        callback_data="add_photo")],
        [InlineKeyboardButton("🔢 Штрихкод",           callback_data="add_barcode")],
        [InlineKeyboardButton("✏️ Написать название", callback_data="add_text")],
        [InlineKeyboardButton("🔍 Поиск продукта",    callback_data="add_search")],
        [InlineKeyboardButton("⭐ Избранное",          callback_data="add_favorite")],
        [InlineKeyboardButton("🍎 Свои продукты",     callback_data="add_custom")],
    ])

# =============================================
# КОМАНДЫ
# =============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        "Привет, " + name + "! 👋\n\n"
        "Я считаю калории и помогаю следить за питанием!\n\n"
        "/add — добавить еду в дневник\n"
        "/diary — дневник питания\n"
        "/help — все команды\n\n"
        "Или просто напиши вопрос про питание!"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 ВСЕ КОМАНДЫ:\n\n"
        "🍽️ ДОБАВИТЬ ЕДУ:\n"
        "/add — меню добавления\n"
        "  📸 Фото блюда\n"
        "  🔢 Штрихкод с упаковки\n"
        "  ✏️ Написать что съел\n"
        "  🔍 Поиск продукта\n"
        "  ⭐ Избранное\n"
        "  🍎 Свои продукты\n\n"
        "📔 ДНЕВНИК:\n"
        "/diary — питание за сегодня\n"
        "/week — питание за неделю\n"
        "/clear_diary — очистить дневник\n\n"
        "⭐ ИЗБРАННОЕ:\n"
        "/favorites — список избранного\n"
        "/add_fav — добавить в избранное\n\n"
        "🍎 СВОИ ПРОДУКТЫ:\n"
        "/my_foods — мои продукты\n"
        "/new_food — создать продукт\n\n"
        "👤 ПРОФИЛЬ:\n"
        "/setup — как настроить\n"
        "/profile — мой профиль\n\n"
        "💬 СОВЕТЫ:\n"
        "Просто напиши вопрос про питание!"
    )

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(update.effective_user.id)
    await update.message.reply_text(
        "Как добавить еду?",
        reply_markup=add_menu_keyboard()
    )

# =============================================
# ИНЛАЙН КНОПКИ
# =============================================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    data = query.data

    if data == "add_photo":
        set_state(uid, "wait_photo")
        await query.message.reply_text(
            "📸 Отправь фото блюда!\n"
            "Можно сразу с подписью — напиши что это."
        )

   elif data == "add_barcode":
    set_state(uid, "wait_barcode")
    await query.message.reply_text(
        "🔢 Введи штрихкод с упаковки\n\n"
        "Как найти штрихкод:\n"
        "1. Возьми упаковку продукта\n"
        "2. Найди полосатый рисунок (обычно сзади или снизу)\n"
        "3. Под полосками есть цифры — введи их\n\n"
        "Обычно это 8, 10 или 13 цифр\n"
        "Пример: 4607086563126\n\n"
        "Просто напиши эти цифры!"
    )

    elif data == "add_text":
        set_state(uid, "wait_text_food")
        await query.message.reply_text(
            "✏️ Напиши что ты съел:\n\n"
            "Примеры:\n"
            "100г гречки вареной\n"
            "борщ со сметаной 300г\n"
            "2 яйца вареных\n"
            "куриная грудка с рисом\n"
            "стакан молока 3.2%"
        )

    elif data == "add_search":
        set_state(uid, "wait_search")
        await query.message.reply_text(
            "🔍 Напиши название продукта:\n\n"
            "Примеры:\n"
            "гречка\n"
            "куриная грудка\n"
            "творог 5%\n"
            "банан"
        )

    elif data == "add_favorite":
        favorites = get_favorites(uid)
        if not favorites:
            await query.message.reply_text(
                "Избранное пусто!\n\n"
                "Добавь блюда через /add_fav"
            )
            return
        keyboard = []
        for name in list(favorites.keys())[:10]:
            keyboard.append([InlineKeyboardButton(name, callback_data="fav_" + name[:40])])
        await query.message.reply_text(
            "⭐ Выбери блюдо:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "add_custom":
        customs = get_custom_foods(uid)
        real = {k: v for k, v in customs.items() if not k.startswith("_search_")}
        if not real:
            await query.message.reply_text(
                "Своих продуктов нет!\n\n"
                "Добавь через /new_food"
            )
            return
        keyboard = []
        for name in list(real.keys())[:10]:
            keyboard.append([InlineKeyboardButton(name, callback_data="custom_" + name[:40])])
        await query.message.reply_text(
            "🍎 Выбери продукт:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("fav_"):
        name = data[4:]
        favorites = get_favorites(uid)
        if name in favorites:
            meal = dict(favorites[name])
            add_meal(uid, meal)
            await query.message.reply_text(format_meal_added(meal, uid))

    elif data.startswith("custom_"):
        name = data[7:]
        customs = get_custom_foods(uid)
        if name in customs:
            set_state(uid, "wait_weight_custom_" + name)
            food = customs[name]
            await query.message.reply_text(
                name + "\n"
                "На 100г: " + str(food["calories"]) + " ккал | "
                "Б:" + str(food["protein"]) + "г Ж:" + str(food["fat"]) + "г У:" + str(food["carbs"]) + "г\n\n"
                "Сколько граммов?"
            )

    elif data.startswith("search_add_"):
        name = data[11:]
        set_state(uid, "wait_weight_search_" + name)
        await query.message.reply_text("Сколько граммов " + name + "?")

    elif data == "save_last_fav":
        meal = context.user_data.get("last_meal")
        if meal:
            get_favorites(uid)[meal["dish"]] = meal
            await query.message.reply_text("⭐ Добавлено в избранное: " + meal["dish"])
        else:
            await query.message.reply_text("Нет блюда для сохранения.")

    elif data == "edit_calories":
        meal = context.user_data.get("last_meal")
        if meal:
            set_state(uid, "wait_edit_calories")
            await query.message.reply_text(
                "✏️ Исправить калории: " + meal["dish"] + "\n"
                "Сейчас: " + str(meal["calories"]) + " ккал\n\n"
                "Введи правильное количество:"
            )
        else:
            await query.message.reply_text("Нет блюда для редактирования.")

# =============================================
# ОБРАБОТКА ФОТО
# =============================================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    caption = update.message.caption or ""

    photo_file  = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()
    photo_b64   = base64.b64encode(photo_bytes).decode("utf-8")

    if caption:
        await do_analyze_photo(update, context, uid, photo_b64, caption)
    else:
        pending_photo[uid] = photo_b64
        set_state(uid, "wait_photo_desc")
        await update.message.reply_text(
            "Фото получено! Что это за блюдо?\n\n"
            "Опиши кратко, например: борщ со сметаной"
        )

async def do_analyze_photo(update, context, uid, photo_b64, description):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        result = await call_vision(photo_b64, description)
        add_meal(uid, dict(result))
        context.user_data["last_meal"] = {
            "dish": result["dish"], "calories": result["calories"],
            "protein": result["protein"], "fat": result["fat"],
            "carbs": result["carbs"], "weight": result["weight"]
        }
        await update.message.reply_text(format_meal_added(result, uid), reply_markup=add_food_keyboard())
        clear_state(uid)
    except Exception as e:
        await update.message.reply_text("Не удалось проанализировать: " + str(e) + "\nПопробуй ещё раз.")

# =============================================
# ОБРАБОТКА ТЕКСТА
# =============================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    text  = update.message.text.strip()
    state = get_state(uid)

    if state == "wait_photo_desc":
        photo_b64 = pending_photo.pop(uid, None)
        if photo_b64:
            clear_state(uid)
            await do_analyze_photo(update, context, uid, photo_b64, text)
        return

    if state == "wait_text_food":
        clear_state(uid)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        try:
            result = await call_groq_json(ANALYZE_TEXT_PROMPT, text)
            add_meal(uid, dict(result))
            context.user_data["last_meal"] = {
                "dish": result["dish"], "calories": result["calories"],
                "protein": result["protein"], "fat": result["fat"],
                "carbs": result["carbs"], "weight": result["weight"]
            }
            await update.message.reply_text(format_meal_added(result, uid), reply_markup=add_food_keyboard())
        except Exception as e:
            await update.message.reply_text("Не удалось распознать: " + str(e) + "\nПиши точнее, например: 100г гречки")
        return

    if state == "wait_search":
        clear_state(uid)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        try:
            search_prompt = (
                "Ты - база данных продуктов питания. "
                "Пользователь ищет продукт. Дай данные на 100г. "
                "Отвечай СТРОГО в JSON без лишнего текста: "
                "{\"results\": [{\"name\":\"название\",\"calories\":число,\"protein\":число,"
                "\"fat\":число,\"carbs\":число}]} "
                "Дай 3-5 подходящих вариантов. Все числа целые."
            )
            result = await call_groq_json(search_prompt, "Найди продукт: " + text)
            items = result.get("results", [])
            if not items:
                await update.message.reply_text("Ничего не найдено. Попробуй написать иначе.")
                return
            keyboard = []
            resp_text = "Найдено по запросу '" + text + "':\n\n"
            for item in items[:5]:
                resp_text += (
                    "• " + item["name"] + "\n"
                    "  100г: " + str(item["calories"]) + " ккал | "
                    "Б:" + str(item["protein"]) + "г Ж:" + str(item["fat"]) + "г У:" + str(item["carbs"]) + "г\n\n"
                )
                short_name = item["name"][:30]
                keyboard.append([InlineKeyboardButton(item["name"], callback_data="search_add_" + short_name)])
                get_custom_foods(uid)["_search_" + short_name] = item
            resp_text += "Нажми на продукт чтобы добавить:"
            await update.message.reply_text(resp_text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            await update.message.reply_text("Ошибка поиска: " + str(e))
        return

    if state == "wait_barcode":
        barcode = text.strip().replace(" ", "")
        if barcode.isdigit() and len(barcode) >= 8:
            clear_state(uid)
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
            try:
                result = await lookup_barcode(barcode)
                if result:
                    add_meal(uid, dict(result))
                    context.user_data["last_meal"] = result
                    await update.message.reply_text(format_meal_added(result, uid), reply_markup=add_food_keyboard())
                else:
                    await update.message.reply_text(
                        "Продукт не найден в базе.\n\n"
                        "Попробуй добавить вручную через /add"
                    )
            except Exception as e:
                await update.message.reply_text("Ошибка поиска: " + str(e))
        else:
            await update.message.reply_text("Введи только цифры штрихкода, например: 4607086563126")
        return

    if state == "wait_edit_calories":
        clear_state(uid)
        try:
            new_cal = int(text.strip())
            meals, _ = get_today_meals(uid)
            meal = context.user_data.get("last_meal")
            if meal and meals:
                for m in reversed(meals):
                    if m.get("dish") == meal["dish"]:
                        old_cal = m["calories"]
                        m["calories"] = new_cal
                        total, _ = get_daily_total(uid)
                        await update.message.reply_text(
                            "Исправлено!\n"
                            + meal["dish"] + ": " + str(old_cal) + " -> " + str(new_cal) + " ккал\n\n"
                            "За сегодня: " + str(total["calories"]) + " ккал"
                        )
                        break
                else:
                    await update.message.reply_text("Блюдо не найдено в дневнике.")
            else:
                await update.message.reply_text("Нет блюда для редактирования.")
        except ValueError:
            await update.message.reply_text("Введи число! Например: 178")
        return

    if state and state.startswith("wait_weight_search_"):
        name = state[19:]
        clear_state(uid)
        try:
            grams = int(text.replace("г", "").replace("g", "").strip())
            food  = get_custom_foods(uid).get("_search_" + name)
            if not food:
                await update.message.reply_text("Продукт не найден.")
                return
            factor = grams / 100
            meal = {
                "dish":     food["name"] + " " + str(grams) + "г",
                "calories": int(food["calories"] * factor),
                "protein":  round(food["protein"] * factor, 1),
                "fat":      round(food["fat"] * factor, 1),
                "carbs":    round(food["carbs"] * factor, 1),
                "weight":   grams,
                "comment":  ""
            }
            add_meal(uid, meal)
            await update.message.reply_text(format_meal_added(meal, uid))
        except Exception:
            await update.message.reply_text("Напиши количество граммов цифрой, например: 150")
        return

    if state and state.startswith("wait_weight_custom_"):
        name = state[19:]
        clear_state(uid)
        try:
            grams = int(text.replace("г", "").replace("g", "").strip())
            food  = get_custom_foods(uid).get(name)
            if not food:
                await update.message.reply_text("Продукт не найден.")
                return
            factor = grams / 100
            meal = {
                "dish":     name + " " + str(grams) + "г",
                "calories": int(food["calories"] * factor),
                "protein":  round(food["protein"] * factor, 1),
                "fat":      round(food["fat"] * factor, 1),
                "carbs":    round(food["carbs"] * factor, 1),
                "weight":   grams,
                "comment":  ""
            }
            add_meal(uid, meal)
            await update.message.reply_text(format_meal_added(meal, uid))
        except Exception:
            await update.message.reply_text("Напиши количество граммов цифрой, например: 150")
        return

    if state == "wait_new_food":
        clear_state(uid)
        try:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) < 5:
                await update.message.reply_text(
                    "Неверный формат!\n\n"
                    "Используй:\nНазвание | калории | белки | жиры | углеводы\n\n"
                    "Пример:\nОвсянка | 350 | 13 | 6 | 60"
                )
                return
            name = parts[0]
            food = {
                "name":     name,
                "calories": int(parts[1]),
                "protein":  float(parts[2]),
                "fat":      float(parts[3]),
                "carbs":    float(parts[4]),
            }
            get_custom_foods(uid)[name] = food
            await update.message.reply_text(
                "Продукт сохранён!\n\n"
                + name + "\n"
                "На 100г: " + str(food["calories"]) + " ккал | "
                "Б:" + str(food["protein"]) + "г Ж:" + str(food["fat"]) + "г У:" + str(food["carbs"]) + "г\n\n"
                "Доступен в /add -> Свои продукты"
            )
        except Exception:
            await update.message.reply_text("Ошибка! Проверь формат и попробуй снова.")
        return

    if state == "wait_add_fav":
        clear_state(uid)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        try:
            result = await call_groq_json(ANALYZE_TEXT_PROMPT, text)
            get_favorites(uid)[result["dish"]] = result
            await update.message.reply_text(
                "Добавлено в избранное!\n\n"
                + result["dish"] + "\n"
                + str(result["calories"]) + " ккал | "
                "Б:" + str(result["protein"]) + "г Ж:" + str(result["fat"]) + "г У:" + str(result["carbs"]) + "г"
            )
        except Exception as e:
            await update.message.reply_text("Ошибка: " + str(e))
        return

    # Обычный вопрос про питание
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask_nutrition(uid, text)
        await send(update, reply)
    except Exception as e:
        await update.message.reply_text("Ошибка: " + str(e))

# =============================================
# ДНЕВНИК
# =============================================

async def diary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    total, meals = get_daily_total(uid)
    profile = get_profile(uid)
    target  = profile.get("target_calories", 0)

    if not meals:
        await update.message.reply_text("Дневник пуст!\n\nДобавь еду через /add")
        return

    text = "Дневник питания — сегодня:\n\n"
    for i, m in enumerate(meals, 1):
        text += (
            str(i) + ". " + m["time"] + " — " + m["dish"] + "\n"
            "   " + str(m["calories"]) + " ккал | "
            "Б:" + str(m["protein"]) + "г Ж:" + str(m["fat"]) + "г У:" + str(m["carbs"]) + "г\n\n"
        )
    text += (
        "ИТОГО:\n"
        "Калории: " + str(total["calories"]) + " ккал\n"
        "Белки: " + str(total["protein"]) + " г\n"
        "Жиры: " + str(total["fat"]) + " г\n"
        "Углеводы: " + str(total["carbs"]) + " г\n"
    )
    if target:
        remains = target - total["calories"]
        if remains > 0:
            text += "\nОсталось: " + str(remains) + " ккал из " + str(target)
        else:
            text += "\nПревышение на " + str(abs(remains)) + " ккал!"

    await send(update, text)

async def week_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_diary or not user_diary[uid]:
        await update.message.reply_text("Нет данных. Начни отслеживать питание!")
        return
    text = "Питание за последние дни:\n\n"
    for date, meals in sorted(user_diary[uid].items(), reverse=True)[:7]:
        total_cal = sum(m.get("calories", 0) for m in meals)
        text += date + ": " + str(total_cal) + " ккал (" + str(len(meals)) + " приёмов)\n"
    await send(update, text)

async def clear_diary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    today = datetime.now().strftime("%Y-%m-%d")
    if uid in user_diary and today in user_diary[uid]:
        user_diary[uid][today] = []
    await update.message.reply_text("Дневник за сегодня очищен!")

# =============================================
# ИЗБРАННОЕ
# =============================================

async def favorites_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid       = update.effective_user.id
    favorites = get_favorites(uid)
    if not favorites:
        await update.message.reply_text(
            "Избранное пусто!\n\n"
            "Добавь через /add_fav"
        )
        return
    text = "Избранное:\n\n"
    for name, f in favorites.items():
        text += "⭐ " + name + " — " + str(f["calories"]) + " ккал\n"
    text += "\nИспользуй /add -> Избранное"
    await send(update, text)

async def add_fav_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    set_state(uid, "wait_add_fav")
    await update.message.reply_text(
        "Напиши что добавить в избранное:\n\n"
        "Примеры:\n"
        "борщ со сметаной 300г\n"
        "куриная грудка с гречкой\n"
        "творог 200г"
    )

# =============================================
# СВОИ ПРОДУКТЫ
# =============================================

async def my_foods_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    customs = get_custom_foods(uid)
    real    = {k: v for k, v in customs.items() if not k.startswith("_search_")}
    if not real:
        await update.message.reply_text("Своих продуктов нет!\n\nДобавь через /new_food")
        return
    text = "Мои продукты (на 100г):\n\n"
    for name, f in real.items():
        text += (
            "🍎 " + name + "\n"
            "   " + str(f["calories"]) + " ккал | "
            "Б:" + str(f["protein"]) + "г Ж:" + str(f["fat"]) + "г У:" + str(f["carbs"]) + "г\n\n"
        )
    text += "Используй /add -> Свои продукты"
    await send(update, text)

async def new_food_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_state(update.effective_user.id, "wait_new_food")
    await update.message.reply_text(
        "Добавление своего продукта\n\n"
        "Формат (на 100г):\n"
        "Название | калории | белки | жиры | углеводы\n\n"
        "Пример:\n"
        "Овсянка | 350 | 13 | 6 | 60"
    )

# =============================================
# ПРОФИЛЬ
# =============================================

async def setup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Настройка профиля\n\n"
        "Формат:\n"
        "/setprofile возраст вес рост пол цель\n\n"
        "Пример:\n"
        "/setprofile 28 80 180 м похудеть\n\n"
        "Цель: похудеть / набрать / поддержать"
    )

async def setprofile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    args = context.args
    if len(args) < 5:
        await update.message.reply_text("Пример: /setprofile 28 80 180 м похудеть")
        return
    try:
        profile = get_profile(uid)
        profile["age"]    = int(args[0])
        profile["weight"] = int(args[1])
        profile["height"] = int(args[2])
        profile["gender"] = args[3].lower()
        profile["goal"]   = " ".join(args[4:])

        w, h, a = profile["weight"], profile["height"], profile["age"]
        bmr = 10*w + 6.25*h - 5*a + (5 if profile["gender"] in ["м", "m"] else -161)
        tdee = int(bmr * 1.55)

        if "похудеть" in profile["goal"]:
            target, goal_text = tdee - 500, "похудение"
        elif "набрать" in profile["goal"]:
            target, goal_text = tdee + 300, "набор массы"
        else:
            target, goal_text = tdee, "поддержание"

        profile["target_calories"] = target
        await update.message.reply_text(
            "Профиль сохранён!\n\n"
            "Возраст: " + str(profile["age"]) + " лет\n"
            "Вес: " + str(profile["weight"]) + " кг\n"
            "Рост: " + str(profile["height"]) + " см\n"
            "Цель: " + goal_text + "\n"
            "Норма: " + str(target) + " ккал/день\n\n"
            "Добавляй еду через /add"
        )
    except Exception as e:
        await update.message.reply_text("Ошибка: " + str(e))

async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    profile = get_profile(uid)
    total, meals = get_daily_total(uid)
    if not profile["weight"]:
        await update.message.reply_text("Профиль не настроен.\nИспользуй /setup")
        return
    target  = profile.get("target_calories", 0)
    remains = target - total["calories"] if target else 0
    await update.message.reply_text(
        "Профиль:\n\n"
        "Возраст: " + str(profile["age"]) + " лет\n"
        "Вес: " + str(profile["weight"]) + " кг\n"
        "Рост: " + str(profile["height"]) + " см\n"
        "Цель: " + str(profile.get("goal", "не указана")) + "\n"
        "Норма: " + str(target) + " ккал/день\n\n"
        "Сегодня:\n"
        "Съедено: " + str(total["calories"]) + " ккал\n"
        "Осталось: " + str(max(0, remains)) + " ккал\n"
        "Приёмов пищи: " + str(len(meals))
    )

# =============================================
# ЗАПУСК
# =============================================

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start",       "Начать"),
        BotCommand("add",         "Добавить еду"),
        BotCommand("diary",       "Дневник сегодня"),
        BotCommand("week",        "Питание за неделю"),
        BotCommand("favorites",   "Избранное"),
        BotCommand("add_fav",     "Добавить в избранное"),
        BotCommand("my_foods",    "Мои продукты"),
        BotCommand("new_food",    "Создать продукт"),
        BotCommand("profile",     "Мой профиль"),
        BotCommand("setup",       "Настроить профиль"),
        BotCommand("clear_diary", "Очистить дневник"),
        BotCommand("help",        "Все команды"),
    ])

def main():
    print("Bot starting...")
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("help",        help_cmd))
    app.add_handler(CommandHandler("add",         add_cmd))
    app.add_handler(CommandHandler("diary",       diary_cmd))
    app.add_handler(CommandHandler("week",        week_cmd))
    app.add_handler(CommandHandler("clear_diary", clear_diary_cmd))
    app.add_handler(CommandHandler("favorites",   favorites_cmd))
    app.add_handler(CommandHandler("add_fav",     add_fav_cmd))
    app.add_handler(CommandHandler("my_foods",    my_foods_cmd))
    app.add_handler(CommandHandler("new_food",    new_food_cmd))
    app.add_handler(CommandHandler("setup",       setup_cmd))
    app.add_handler(CommandHandler("setprofile",  setprofile_cmd))
    app.add_handler(CommandHandler("profile",     profile_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

