from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ChatAction
from groq import Groq
import urllib.request
import json
import base64
import os
from datetime import datetime, timedelta

TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN",     "ВСТАВЬ_СЮДА")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "ВСТАВЬ_СЮДА")
GROQ_API_KEY       = os.environ.get("GROQ_API_KEY",       "ВСТАВЬ_СЮДА")

groq_client = Groq(api_key=GROQ_API_KEY)

# =============================================
# ДАННЫЕ
# =============================================
user_profiles  = {}
user_diary     = {}   # { uid: { "2024-01-01": { "meals": [], "water": 0, "activity": [] } } }
user_custom    = {}
user_favorites = {}
user_state     = {}
pending_photo  = {}

def get_profile(uid):
    if uid not in user_profiles:
        user_profiles[uid] = {
            "goal": None, "weight": None, "height": None,
            "age": None, "gender": None,
            "target_calories": None,
            "target_water": 2000,
            "target_steps": 10000,
        }
    return user_profiles[uid]

def get_day(uid, date=None):
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    if uid not in user_diary: user_diary[uid] = {}
    if date not in user_diary[uid]:
        user_diary[uid][date] = {"meals": [], "water": 0, "activity": []}
    return user_diary[uid][date], date

def today_str():
    return datetime.now().strftime("%Y-%m-%d")

def get_today_meals(uid):
    day, date = get_day(uid)
    return day["meals"], date

def add_meal(uid, meal, date=None):
    day, _ = get_day(uid, date)
    meal["time"] = datetime.now().strftime("%H:%M")
    day["meals"].append(meal)

def get_daily_total(uid, date=None):
    day, _ = get_day(uid, date)
    meals = day["meals"]
    t = {"calories": 0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}
    for m in meals:
        t["calories"] += int(m.get("calories", 0))
        t["protein"]  += float(m.get("protein", 0))
        t["fat"]      += float(m.get("fat", 0))
        t["carbs"]    += float(m.get("carbs", 0))
    t["protein"] = round(t["protein"], 1)
    t["fat"]     = round(t["fat"], 1)
    t["carbs"]   = round(t["carbs"], 1)
    return t, meals

def add_water(uid, ml):
    day, _ = get_day(uid)
    day["water"] = day.get("water", 0) + ml

def get_water(uid, date=None):
    day, _ = get_day(uid, date)
    return day.get("water", 0)

def add_activity(uid, activity):
    day, _ = get_day(uid)
    day["activity"].append(activity)

def get_activity(uid, date=None):
    day, _ = get_day(uid, date)
    return day.get("activity", [])

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
    "Ты - профессиональный диетолог с 20 годами опыта. "
    "Пользователь прислал фото еды с описанием. "
    "Максимально точно рассчитай КЖБУ. "
    "Учитывай способ приготовления, ингредиенты, размер порции. "
    "Отвечай СТРОГО только JSON без лишнего текста: "
    "{\"dish\":\"название\",\"calories\":число,\"protein\":число,\"fat\":число,\"carbs\":число,\"weight\":число,\"comment\":\"совет 1-2 предложения\"} "
    "Все числа целые. calories обычная порция 200-800 ккал."
)

ANALYZE_TEXT_PROMPT = (
    "Ты - профессиональный диетолог с 20 годами опыта. "
    "Пользователь написал что съел. Максимально точно рассчитай КЖБУ. "
    "Если указан вес - используй его точно. Если нет - стандартная порция. "
    "Примеры: 100г куриной грудки = 165 ккал, 31г белка, 3г жира, 0г углеводов. "
    "100г гречки вареной = 92 ккал, 3г белка, 0.6г жира, 20г углеводов. "
    "Отвечай СТРОГО только JSON без лишнего текста: "
    "{\"dish\":\"название\",\"calories\":число,\"protein\":число,\"fat\":число,\"carbs\":число,\"weight\":число,\"comment\":\"совет 1-2 предложения\"} "
    "Все числа целые."
)

NUTRITION_PROMPT = (
    "Ты - опытный диетолог и нутрициолог. "
    "Отвечай кратко, конкретно и на языке пользователя. "
    "Давай практичные советы основанные на науке."
)

# =============================================
# ИИ ЗАПРОСЫ
# =============================================

def parse_json(content):
    content = content.strip()
    if "```" in content:
        for part in content.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                content = part
                break
    start = content.find("{")
    end   = content.rfind("}") + 1
    if start != -1 and end > start:
        content = content[start:end]
    return json.loads(content)

def validate_meal(data):
    for k in ["dish", "calories", "protein", "fat", "carbs", "weight"]:
        if k not in data:
            raise ValueError("Missing: " + k)
    cal = int(data["calories"])
    if cal < 0 or cal > 5000:
        raise ValueError("Unrealistic calories: " + str(cal))
    return {
        "dish":     str(data["dish"]),
        "calories": int(data["calories"]),
        "protein":  round(float(data["protein"]), 1),
        "fat":      round(float(data["fat"]), 1),
        "carbs":    round(float(data["carbs"]), 1),
        "weight":   int(data["weight"]),
        "comment":  str(data.get("comment", ""))
    }

async def call_openrouter(messages, model="google/gemma-4-27b-it:free"):
    body = json.dumps({"model": model, "messages": messages, "max_tokens": 400}, ensure_ascii=False).encode("utf-8")
    req  = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": "Bearer " + OPENROUTER_API_KEY, "Content-Type": "application/json; charset=utf-8", "HTTP-Referer": "https://t.me", "X-Title": "CalorieSnap"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]

FALLBACK_MODELS = ["google/gemma-4-27b-it:free", "nvidia/nemotron-nano-12b-v2-vl:free"]

async def call_vision(photo_b64, description):
    messages = [
        {"role": "system", "content": ANALYZE_PHOTO_PROMPT},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + photo_b64}},
            {"type": "text", "text": "Описание: " + description}
        ]}
    ]
    for model in FALLBACK_MODELS:
        for _ in range(2):
            try:
                content = await call_openrouter(messages, model)
                return validate_meal(parse_json(content))
            except Exception:
                pass
    raise Exception("Все модели недоступны. Попробуй позже.")

async def call_groq_json(prompt, user_text):
    for _ in range(3):
        try:
            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "system", "content": prompt}, {"role": "user", "content": user_text}],
                max_tokens=400
            )
            data = parse_json(response.choices[0].message.content)
            if "dish" in data:
                return validate_meal(data)
            return data
        except Exception:
            pass
    raise Exception("Не удалось распознать после 3 попыток")

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
        "dish": name, "calories": int(n.get("energy-kcal_100g", 0) or 0),
        "protein": round(float(n.get("proteins_100g", 0) or 0), 1),
        "fat": round(float(n.get("fat_100g", 0) or 0), 1),
        "carbs": round(float(n.get("carbohydrates_100g", 0) or 0), 1),
        "weight": 100, "comment": "Данные с упаковки (на 100г)"
    }

async def ask_nutrition(uid, question):
    profile = get_profile(uid)
    total, _ = get_daily_total(uid)
    ctx = ""
    if profile["goal"]:   ctx += "Цель: " + profile["goal"] + ". "
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
        "Белки:   " + str(result["protein"]) + " г\n"
        "Жиры:    " + str(result["fat"]) + " г\n"
        "Углеводы:" + str(result["carbs"]) + " г\n"
        "Порция:  " + str(result["weight"]) + " г\n"
    )
    if result.get("comment"):
        text += "\n" + result["comment"] + "\n"
    text += "\nЗа сегодня: " + str(total["calories"]) + " ккал"
    if target:
        remains = target - total["calories"]
        if remains > 0:
            text += " (осталось " + str(remains) + " из " + str(target) + ")"
        else:
            text += " — превышение на " + str(abs(remains)) + " ккал!"
    return text

def action_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ В избранное",     callback_data="save_last_fav")],
        [InlineKeyboardButton("✏️ Исправить ккал", callback_data="edit_calories")],
    ])

def add_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Фото блюда",        callback_data="add_photo")],
        [InlineKeyboardButton("🔢 Штрихкод",           callback_data="add_barcode")],
        [InlineKeyboardButton("✏️ Написать что съел", callback_data="add_text")],
        [InlineKeyboardButton("🔍 Поиск продукта",    callback_data="add_search")],
        [InlineKeyboardButton("🍳 Своё блюдо",        callback_data="add_own")],
        [InlineKeyboardButton("⭐ Избранное",          callback_data="add_favorite")],
        [InlineKeyboardButton("🍎 Свои продукты",     callback_data="add_custom")],
    ])

def water_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("150 мл", callback_data="water_150"),
         InlineKeyboardButton("200 мл", callback_data="water_200"),
         InlineKeyboardButton("250 мл", callback_data="water_250")],
        [InlineKeyboardButton("300 мл", callback_data="water_300"),
         InlineKeyboardButton("500 мл", callback_data="water_500"),
         InlineKeyboardButton("✏️ Другое", callback_data="water_custom")],
    ])

def calendar_keyboard(offset=0):
    today = datetime.now()
    rows  = []
    row   = []
    for i in range(7):
        day = today - timedelta(days=6-i+offset*7)
        label = day.strftime("%d.%m")
        if i == 6 - offset*7 and offset == 0:
            label = "Сегодня"
        row.append(InlineKeyboardButton(label, callback_data="cal_" + day.strftime("%Y-%m-%d")))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if offset < 3:
        nav.append(InlineKeyboardButton("◀ Раньше", callback_data="cal_prev_" + str(offset+1)))
    if offset > 0:
        nav.append(InlineKeyboardButton("Позже ▶", callback_data="cal_next_" + str(offset-1)))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(rows)

# =============================================
# КОМАНДЫ
# =============================================

def goal_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Похудеть",       callback_data="onb_goal_похудеть")],
        [InlineKeyboardButton("💪 Набрать массу",  callback_data="onb_goal_набрать")],
        [InlineKeyboardButton("⚖️ Поддержать вес", callback_data="onb_goal_поддержать")],
    ])

def activity_level_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛋️ Сидячий (офис, мало движения)",    callback_data="onb_act_1.2")],
        [InlineKeyboardButton("🚶 Лёгкая (прогулки 1-2 раза/нед)",   callback_data="onb_act_1.375")],
        [InlineKeyboardButton("🏃 Умеренная (тренировки 3-4 раза)",  callback_data="onb_act_1.55")],
        [InlineKeyboardButton("🔥 Активная (тренировки 5-6 раз)",    callback_data="onb_act_1.725")],
        [InlineKeyboardButton("⚡ Очень активная (спорт каждый день)", callback_data="onb_act_1.9")],
    ])

def gender_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👨 Мужской", callback_data="onb_gender_м")],
        [InlineKeyboardButton("👩 Женский", callback_data="onb_gender_ж")],
    ])

def calculate_plan(profile):
    w = profile["weight"]
    h = profile["height"]
    a = profile["age"]
    g = profile["gender"]
    act = float(profile.get("activity_level", 1.55))
    bmr = 10*w + 6.25*h - 5*a + (5 if g in ["м","m"] else -161)
    tdee = int(bmr * act)
    goal = profile.get("goal", "поддержать")
    if "похудеть" in goal:
        calories = tdee - 500
        protein  = int(w * 2.0)
        fat      = int(w * 0.8)
    elif "набрать" in goal:
        calories = tdee + 300
        protein  = int(w * 2.2)
        fat      = int(w * 1.0)
    else:
        calories = tdee
        protein  = int(w * 1.8)
        fat      = int(w * 0.9)
    carbs = int((calories - protein*4 - fat*9) / 4)
    carbs = max(50, carbs)
    water = int(w * 35)
    return {
        "calories": calories,
        "protein":  protein,
        "fat":      fat,
        "carbs":    carbs,
        "water":    water,
        "tdee":     tdee,
    }

def goal_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Похудеть",       callback_data="onb_goal_худеть")],
        [InlineKeyboardButton("Набрать массу",  callback_data="onb_goal_набрать")],
        [InlineKeyboardButton("Поддержать вес", callback_data="onb_goal_поддержать")],
    ])

def activity_level_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Сидячий (офис)",          callback_data="onb_act_1.2")],
        [InlineKeyboardButton("Легкая активность",        callback_data="onb_act_1.375")],
        [InlineKeyboardButton("Умеренная (3-4 трен/нед)", callback_data="onb_act_1.55")],
        [InlineKeyboardButton("Активная (5-6 трен/нед)",  callback_data="onb_act_1.725")],
        [InlineKeyboardButton("Очень активная",           callback_data="onb_act_1.9")],
    ])

def gender_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Мужской", callback_data="onb_gender_м")],
        [InlineKeyboardButton("Женский", callback_data="onb_gender_ж")],
    ])

def calculate_plan(profile):
    w   = profile["weight"]
    h   = profile["height"]
    a   = profile["age"]
    g   = profile["gender"]
    act = float(profile.get("activity_level", 1.55))
    bmr  = 10*w + 6.25*h - 5*a + (5 if g in ["м","m"] else -161)
    tdee = int(bmr * act)
    goal = profile.get("goal", "поддержать")
    if "худеть" in goal:
        calories = tdee - 500
        protein  = int(w * 2.0)
        fat      = int(w * 0.8)
    elif "набрать" in goal:
        calories = tdee + 300
        protein  = int(w * 2.2)
        fat      = int(w * 1.0)
    else:
        calories = tdee
        protein  = int(w * 1.8)
        fat      = int(w * 0.9)
    carbs = max(50, int((calories - protein*4 - fat*9) / 4))
    water = int(w * 35)
    return {"calories": calories, "protein": protein, "fat": fat, "carbs": carbs, "water": water, "tdee": tdee}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    name = update.effective_user.first_name
    profile = get_profile(uid)

    if profile.get("weight") and profile.get("height"):
        total, _ = get_daily_total(uid)
        target   = profile.get("target_calories", 0)
        water    = get_water(uid)
        target_w = profile.get("target_water", 2000)
        remains  = target - total["calories"] if target else 0
        await update.message.reply_text(
            "Привет, " + name + "! 👋\n\n"
            "Сегодня:\n"
            "Калории: " + str(total["calories"]) + " / " + str(target) + " ккал\n"
            "Осталось: " + str(max(0, remains)) + " ккал\n"
            "Вода: " + str(water) + " / " + str(target_w) + " мл\n\n"
            "/add — добавить еду\n"
            "/water — отметить воду\n"
            "/diary — дневник сегодня\n"
            "/calendar — календарь\n"
            "/stats — статистика\n"
            "/help — все команды"
        )
        return

    set_state(uid, "onb_goal")
    await update.message.reply_text(
        "Привет, " + name + "! 👋\n\n"
        "Я помогу считать калории, следить за питанием, водой и активностью!\n\n"
        "Давай настроим твой персональный план.\n"
        "Это займёт 1 минуту!\n\n"
        "Шаг 1 из 6: Какая у тебя цель?",
        reply_markup=goal_keyboard()
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ВСЕ КОМАНДЫ:\n\n"
        "ЕДА:\n"
        "/add — добавить еду (7 способов)\n"
        "/diary — дневник сегодня\n"
        "/clear_diary — очистить дневник\n\n"
        "ВОДА:\n"
        "/water — добавить воду\n"
        "/water_goal — изменить норму воды\n\n"
        "АКТИВНОСТЬ:\n"
        "/activity — записать тренировку\n\n"
        "КАЛЕНДАРЬ И СТАТИСТИКА:\n"
        "/calendar — просмотр любого дня\n"
        "/week — питание за неделю\n"
        "/stats — подробная статистика\n\n"
        "ИЗБРАННОЕ И ПРОДУКТЫ:\n"
        "/favorites — избранное\n"
        "/add_fav — добавить в избранное\n"
        "/my_foods — свои продукты\n"
        "/new_food — создать продукт\n\n"
        "ПРОФИЛЬ:\n"
        "/setup — как настроить\n"
        "/profile — мой профиль\n"
        "/edit_profile — изменить параметры\n\n"
        "Просто напиши вопрос про питание!"
    )

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(update.effective_user.id)
    await update.message.reply_text("Как добавить еду?", reply_markup=add_menu_keyboard())

# =============================================
# ВОДА
# =============================================

async def water_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    current = get_water(uid)
    target  = get_profile(uid).get("target_water", 2000)
    pct     = min(100, int(current / target * 100)) if target else 0
    filled  = int(pct / 10)
    bar     = "💧" * filled + "⬜" * (10 - filled)
    await update.message.reply_text(
        "Сколько воды добавить?\n\n"
        "Сегодня выпито: " + str(current) + " мл из " + str(target) + " мл\n"
        + bar + " " + str(pct) + "%",
        reply_markup=water_keyboard()
    )

async def water_goal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_state(update.effective_user.id, "wait_water_goal")
    await update.message.reply_text(
        "Введи дневную норму воды в мл:\n\n"
        "Рекомендации:\n"
        "Минимум: 1500 мл\n"
        "Норма: 2000 мл\n"
        "Активный образ жизни: 2500-3000 мл\n\n"
        "Просто напиши число, например: 2000"
    )

# =============================================
# АКТИВНОСТЬ
# =============================================

async def activity_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_state(update.effective_user.id, "wait_activity")
    await update.message.reply_text(
        "Запись тренировки\n\n"
        "Напиши что делал и сколько минут:\n\n"
        "Примеры:\n"
        "бег 30 минут\n"
        "силовая тренировка 60 минут\n"
        "плавание 45 минут\n"
        "велосипед 90 минут\n"
        "ходьба 60 минут"
    )

# =============================================
# КАЛЕНДАРЬ
# =============================================

async def calendar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Выбери день для просмотра:",
        reply_markup=calendar_keyboard(0)
    )

def format_day_summary(uid, date):
    day, _ = get_day(uid, date)
    meals    = day.get("meals", [])
    water    = day.get("water", 0)
    activity = day.get("activity", [])
    profile  = get_profile(uid)
    target   = profile.get("target_calories", 0)
    target_w = profile.get("target_water", 2000)

    dt = datetime.strptime(date, "%Y-%m-%d")
    header = dt.strftime("%d %B %Y")

    total = {"calories": 0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}
    for m in meals:
        total["calories"] += int(m.get("calories", 0))
        total["protein"]  += float(m.get("protein", 0))
        total["fat"]      += float(m.get("fat", 0))
        total["carbs"]    += float(m.get("carbs", 0))

    burned = sum(a.get("calories_burned", 0) for a in activity)
    net    = total["calories"] - burned

    text = header + "\n\n"

    if meals:
        text += "ЕДА:\n"
        for i, m in enumerate(meals, 1):
            text += str(i) + ". " + m.get("time", "") + " — " + m["dish"] + " (" + str(m["calories"]) + " ккал)\n"
        text += "\nКалории: " + str(total["calories"]) + " ккал"
        if target:
            remains = target - total["calories"]
            if remains >= 0:
                text += " (осталось " + str(remains) + ")"
            else:
                text += " (превышение " + str(abs(remains)) + ")"
        text += "\nБелки: " + str(round(total["protein"], 1)) + "г | Жиры: " + str(round(total["fat"], 1)) + "г | Углеводы: " + str(round(total["carbs"], 1)) + "г\n"
    else:
        text += "Еда не записана\n"

    text += "\nВОДА:\n"
    pct = min(100, int(water / target_w * 100)) if target_w else 0
    text += str(water) + " мл из " + str(target_w) + " мл (" + str(pct) + "%)\n"

    if activity:
        text += "\nАКТИВНОСТЬ:\n"
        for a in activity:
            text += "• " + a["name"] + " — " + str(a["duration"]) + " мин"
            if a.get("calories_burned"):
                text += " (-" + str(a["calories_burned"]) + " ккал)"
            text += "\n"
        if burned:
            text += "Сожжено: " + str(burned) + " ккал | Нетто: " + str(net) + " ккал\n"
    else:
        text += "\nАктивность не записана\n"

    return text

# =============================================
# СТАТИСТИКА
# =============================================

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    profile = get_profile(uid)
    target  = profile.get("target_calories", 0)
    target_w = profile.get("target_water", 2000)

    text = "Статистика за 7 дней:\n\n"
    total_cal = 0
    total_water = 0
    days_tracked = 0

    for i in range(7):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        if uid in user_diary and date in user_diary[uid]:
            day = user_diary[uid][date]
            meals  = day.get("meals", [])
            water  = day.get("water", 0)
            activity = day.get("activity", [])
            cal    = sum(m.get("calories", 0) for m in meals)
            burned = sum(a.get("calories_burned", 0) for a in activity)
            if meals or water:
                days_tracked += 1
                total_cal   += cal
                total_water += water
                dt = datetime.strptime(date, "%Y-%m-%d")
                label = "Сегодня" if i == 0 else dt.strftime("%d.%m")
                status_cal = ""
                if target and cal > 0:
                    if cal <= target:
                        status_cal = " ✅"
                    else:
                        status_cal = " ❌"
                water_pct = int(water / target_w * 100) if target_w else 0
                status_water = " 💧✅" if water >= target_w else " 💧" + str(water_pct) + "%"
                text += label + ": " + str(cal) + " ккал" + status_cal
                if burned:
                    text += " (-" + str(burned) + ")"
                text += status_water + "\n"

    if days_tracked > 0:
        avg_cal   = int(total_cal / days_tracked)
        avg_water = int(total_water / days_tracked)
        text += "\nСредние показатели:\n"
        text += "Калории: " + str(avg_cal) + " ккал/день\n"
        text += "Вода: " + str(avg_water) + " мл/день\n"
        if target:
            diff = avg_cal - target
            if diff > 0:
                text += "Превышение нормы: +" + str(diff) + " ккал\n"
            else:
                text += "Дефицит: " + str(abs(diff)) + " ккал\n"
    else:
        text += "Нет данных. Начни записывать питание через /add"

    await send(update, text)

async def week_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_diary or not user_diary[uid]:
        await update.message.reply_text("Нет данных. Начни отслеживать питание!")
        return
    text = "Питание за последние дни:\n\n"
    for date, day in sorted(user_diary[uid].items(), reverse=True)[:7]:
        meals = day.get("meals", [])
        cal   = sum(m.get("calories", 0) for m in meals)
        water = day.get("water", 0)
        text += date + ": " + str(cal) + " ккал | вода: " + str(water) + " мл (" + str(len(meals)) + " приёмов)\n"
    await send(update, text)

# =============================================
# ДНЕВНИК
# =============================================

async def diary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    text = format_day_summary(uid, today_str())
    await send(update, text)

async def clear_diary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in user_diary and today_str() in user_diary[uid]:
        user_diary[uid][today_str()] = {"meals": [], "water": 0, "activity": []}
    await update.message.reply_text("Дневник за сегодня очищен!")

# =============================================
# ИНЛАЙН КНОПКИ
# =============================================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    data = query.data

    # Онбординг
    if data.startswith("onb_goal_"):
        goal = data[9:]
        get_profile(uid)["goal"] = goal
        set_state(uid, "onb_gender")
        await query.message.reply_text(
            "Шаг 2 из 6: Укажи пол:",
            reply_markup=gender_keyboard()
        )
        return

    if data.startswith("onb_gender_"):
        gender = data[11:]
        get_profile(uid)["gender"] = gender
        set_state(uid, "onb_age")
        await query.message.reply_text("Шаг 3 из 6: Сколько тебе лет?\n\nНапиши цифрой, например: 25")
        return

    if data.startswith("onb_act_"):
        level = float(data[8:])
        profile = get_profile(uid)
        profile["activity_level"] = level
        plan = calculate_plan(profile)
        profile["target_calories"] = plan["calories"]
        profile["target_water"]    = plan["water"]
        goal = profile.get("goal", "")
        goal_labels = {"худеть": "Похудение", "набрать": "Набор массы", "поддержать": "Поддержание"}
        goal_text = next((v for k, v in goal_labels.items() if k in goal), goal)
        clear_state(uid)
        await query.message.reply_text(
            "Готово! Твой план 🎉\n\n"
            "Цель: " + goal_text + "\n\n"
            "КАЛОРИИ:\n"
            "Норма: " + str(plan["calories"]) + " ккал/день\n"
            "Базовый обмен: " + str(plan["tdee"]) + " ккал\n\n"
            "БЖУ В ДЕНЬ:\n"
            "Белки:    " + str(plan["protein"]) + " г\n"
            "Жиры:     " + str(plan["fat"]) + " г\n"
            "Углеводы: " + str(plan["carbs"]) + " г\n\n"
            "ВОДА:\n"
            "Норма: " + str(plan["water"]) + " мл/день\n\n"
            "Всё готово! Начнём:\n"
            "/add — добавить первый приём пищи\n"
            "/help — все команды"
        )
        return


    # Редактирование профиля — кнопки
    if data == "edit_weight":
        set_state(uid, "edit_weight")
        await query.message.reply_text(
            "Введи новый вес в кг:\n"
            "Например: 75"
        )
        return

    if data == "edit_height":
        set_state(uid, "edit_height")
        await query.message.reply_text(
            "Введи новый рост в см:\n"
            "Например: 175"
        )
        return

    if data == "edit_age":
        set_state(uid, "edit_age")
        await query.message.reply_text(
            "Введи новый возраст:\n"
            "Например: 28"
        )
        return

    if data == "edit_goal":
        await query.message.reply_text(
            "Выбери новую цель:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔥 Похудеть",       callback_data="edit_goal_худеть")],
                [InlineKeyboardButton("💪 Набрать массу",  callback_data="edit_goal_набрать")],
                [InlineKeyboardButton("⚖️ Поддержать вес", callback_data="edit_goal_поддержать")],
            ])
        )
        return

    if data.startswith("edit_goal_"):
        goal = data[10:]
        profile = get_profile(uid)
        profile["goal"] = goal
        plan = calculate_plan(profile)
        profile["target_calories"] = plan["calories"]
        profile["target_water"]    = plan["water"]
        await query.message.reply_text(
            "Цель обновлена: " + goal + "\n"
            "Новая норма: " + str(plan["calories"]) + " ккал/день"
        )
        return

    if data == "edit_activity":
        await query.message.reply_text(
            "Выбери уровень активности:",
            reply_markup=activity_level_keyboard()
        )
        return

    if data == "edit_calories_target":
        set_state(uid, "edit_calories_target")
        await query.message.reply_text(
            "Введи норму калорий вручную:\n"
            "Например: 1800\n\n"
            "Текущая норма: " + str(get_profile(uid).get("target_calories", "—")) + " ккал"
        )
        return

    # Вода
    if data.startswith("water_"):
        val = data[6:]
        if val == "custom":
            set_state(uid, "wait_water_custom")
            await query.message.reply_text("Введи количество мл, например: 350")
            return
        ml = int(val)
        add_water(uid, ml)
        current = get_water(uid)
        target  = get_profile(uid).get("target_water", 2000)
        pct     = min(100, int(current / target * 100)) if target else 0
        filled  = int(pct / 10)
        bar     = "💧" * filled + "⬜" * (10 - filled)
        await query.message.reply_text(
            "Добавлено: " + str(ml) + " мл\n\n"
            "Всего сегодня: " + str(current) + " мл из " + str(target) + " мл\n"
            + bar + " " + str(pct) + "%"
        )
        return

    # Календарь
    if data.startswith("cal_prev_") or data.startswith("cal_next_"):
        offset = int(data.split("_")[-1])
        await query.message.edit_reply_markup(reply_markup=calendar_keyboard(offset))
        return

    if data.startswith("cal_"):
        date = data[4:]
        try:
            datetime.strptime(date, "%Y-%m-%d")
            text = format_day_summary(uid, date)
            await query.message.reply_text(text)
        except Exception:
            pass
        return

    # Еда
    if data == "add_photo":
        set_state(uid, "wait_photo")
        await query.message.reply_text("📸 Отправь фото блюда!\nМожно сразу с подписью.")

    elif data == "add_barcode":
        set_state(uid, "wait_barcode")
        await query.message.reply_text(
            "🔢 Введи штрихкод с упаковки\n\n"
            "Как найти:\n"
            "1. Возьми упаковку продукта\n"
            "2. Найди полосатый рисунок (обычно сзади)\n"
            "3. Под полосками есть цифры — введи их\n\n"
            "Обычно 8, 10 или 13 цифр\n"
            "Пример: 4607086563126"
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

    elif data == "add_own":
        set_state(uid, "wait_own_name")
        await query.message.reply_text(
            "🍳 Своё блюдо — ввод вручную\n\n"
            "Шаг 1 из 3: Напиши название блюда\n\n"
            "Например: Борщ домашний"
        )

    elif data == "add_favorite":
        favorites = get_favorites(uid)
        if not favorites:
            await query.message.reply_text("Избранное пусто!\n\nДобавь через /add_fav")
            return
        keyboard = []
        for name in list(favorites.keys())[:10]:
            keyboard.append([InlineKeyboardButton(name, callback_data="fav_" + name[:40])])
        await query.message.reply_text("⭐ Выбери блюдо:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "add_custom":
        customs = get_custom_foods(uid)
        real = {k: v for k, v in customs.items() if not k.startswith("_search_")}
        if not real:
            await query.message.reply_text("Своих продуктов нет!\n\nДобавь через /new_food")
            return
        keyboard = []
        for name in list(real.keys())[:10]:
            keyboard.append([InlineKeyboardButton(name, callback_data="custom_" + name[:40])])
        await query.message.reply_text("🍎 Выбери продукт:", reply_markup=InlineKeyboardMarkup(keyboard))

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
                "Сколько граммов съел?"
            )

    elif data.startswith("search_add_"):
        name = data[11:]
        set_state(uid, "wait_weight_search_" + name)
        await query.message.reply_text("Сколько граммов " + name + " съел?")

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
                "✏️ Исправить: " + meal["dish"] + "\n"
                "Сейчас: " + str(meal["calories"]) + " ккал\n\n"
                "Введи правильное количество:"
            )
        else:
            await query.message.reply_text("Нет блюда для редактирования.")

# =============================================
# ФОТО
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
        await update.message.reply_text("Фото получено! Опиши блюдо:\nНапример: борщ со сметаной")

async def do_analyze_photo(update, context, uid, photo_b64, description):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        result = await call_vision(photo_b64, description)
        add_meal(uid, dict(result))
        context.user_data["last_meal"] = result
        await update.message.reply_text(format_meal_added(result, uid), reply_markup=action_keyboard())
        clear_state(uid)
    except Exception as e:
        await update.message.reply_text(
            "Не удалось проанализировать фото.\n\n"
            "Попробуй:\n"
            "1. Сфоткать покрупнее\n"
            "2. Добавить описание\n"
            "3. /add -> Написать что съел"
        )

# =============================================
# ТЕКСТ
# =============================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    text  = update.message.text.strip()
    state = get_state(uid)

    # Онбординг — возраст
    if state == "onb_age":
        try:
            age = int(text.strip())
            if age < 10 or age > 100:
                await update.message.reply_text("Введи реальный возраст, например: 25")
                return
            get_profile(uid)["age"] = age
            set_state(uid, "onb_weight")
            await update.message.reply_text("Шаг 4 из 6: Какой у тебя вес?\n\nНапиши в кг, например: 75")
        except ValueError:
            await update.message.reply_text("Введи цифру! Например: 25")
        return

    if state == "onb_weight":
        try:
            weight = int(text.replace("кг","").strip())
            if weight < 30 or weight > 300:
                await update.message.reply_text("Введи реальный вес в кг, например: 75")
                return
            get_profile(uid)["weight"] = weight
            set_state(uid, "onb_height")
            await update.message.reply_text("Шаг 5 из 6: Какой у тебя рост?\n\nНапиши в см, например: 175")
        except ValueError:
            await update.message.reply_text("Введи цифру! Например: 75")
        return

    if state == "onb_height":
        try:
            height = int(text.replace("см","").strip())
            if height < 100 or height > 250:
                await update.message.reply_text("Введи реальный рост в см, например: 175")
                return
            get_profile(uid)["height"] = height
            set_state(uid, "onb_activity")
            await update.message.reply_text(
                "Шаг 6 из 6: Какой у тебя уровень активности?",
                reply_markup=activity_level_keyboard()
            )
        except ValueError:
            await update.message.reply_text("Введи цифру! Например: 175")
        return

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
            context.user_data["last_meal"] = result
            await update.message.reply_text(format_meal_added(result, uid), reply_markup=action_keyboard())
        except Exception:
            await update.message.reply_text(
                "Не удалось распознать.\n\n"
                "Пиши точнее:\n"
                "100г гречки\n"
                "куриная грудка 150г\n"
                "2 яйца вареных"
            )
        return

    if state == "wait_search":
        clear_state(uid)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        try:
            search_prompt = (
                "Ты - база данных продуктов питания. "
                "Дай точные данные КЖБУ на 100г. "
                "Отвечай СТРОГО только JSON: "
                "{\"results\": [{\"name\":\"название\",\"calories\":число,\"protein\":число,\"fat\":число,\"carbs\":число}]} "
                "Дай 3-5 вариантов. Все числа целые."
            )
            result = await call_groq_json(search_prompt, "Найди: " + text)
            items  = result.get("results", [])
            if not items:
                await update.message.reply_text("Ничего не найдено.")
                return
            keyboard  = []
            resp_text = "Найдено по запросу '" + text + "':\n\n"
            for item in items[:5]:
                resp_text += (
                    "• " + item["name"] + "\n"
                    "  100г: " + str(item["calories"]) + " ккал | "
                    "Б:" + str(item["protein"]) + "г Ж:" + str(item["fat"]) + "г У:" + str(item["carbs"]) + "г\n\n"
                )
                short = item["name"][:30]
                keyboard.append([InlineKeyboardButton(item["name"], callback_data="search_add_" + short)])
                get_custom_foods(uid)["_search_" + short] = item
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
                    await update.message.reply_text(format_meal_added(result, uid), reply_markup=action_keyboard())
                else:
                    await update.message.reply_text("Продукт не найден.\n\nДобавь вручную через /add")
            except Exception as e:
                await update.message.reply_text("Ошибка: " + str(e))
        else:
            await update.message.reply_text("Штрихкод — только цифры (8-13 знаков).\nПример: 4607086563126")
        return

    if state == "wait_water_custom":
        clear_state(uid)
        try:
            ml = int(text.replace("мл", "").replace("ml", "").strip())
            add_water(uid, ml)
            current = get_water(uid)
            target  = get_profile(uid).get("target_water", 2000)
            pct     = min(100, int(current / target * 100)) if target else 0
            await update.message.reply_text(
                "Добавлено: " + str(ml) + " мл\n\n"
                "Всего сегодня: " + str(current) + " мл из " + str(target) + " мл (" + str(pct) + "%)"
            )
        except Exception:
            await update.message.reply_text("Введи число мл, например: 350")
        return

    if state == "wait_water_goal":
        clear_state(uid)
        try:
            ml = int(text.replace("мл", "").replace("ml", "").strip())
            get_profile(uid)["target_water"] = ml
            await update.message.reply_text("Норма воды установлена: " + str(ml) + " мл/день")
        except Exception:
            await update.message.reply_text("Введи число, например: 2000")
        return

    if state == "wait_activity":
        clear_state(uid)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        try:
            activity_prompt = (
                "Пользователь записал тренировку. Распознай название и длительность. "
                "Рассчитай примерный расход калорий для человека 70кг. "
                "Отвечай СТРОГО только JSON: "
                "{\"name\":\"название\",\"duration\":минуты,\"calories_burned\":число} "
                "Все числа целые."
            )
            result = await call_groq_json(activity_prompt, text)
            activity = {
                "name":           str(result.get("name", text)),
                "duration":       int(result.get("duration", 30)),
                "calories_burned": int(result.get("calories_burned", 0)),
                "time":           datetime.now().strftime("%H:%M")
            }
            add_activity(uid, activity)
            await update.message.reply_text(
                "Тренировка записана!\n\n"
                + activity["name"] + "\n"
                "Длительность: " + str(activity["duration"]) + " мин\n"
                "Сожжено: ~" + str(activity["calories_burned"]) + " ккал"
            )
        except Exception as e:
            await update.message.reply_text("Ошибка: " + str(e))
        return

    if state == "wait_own_name":
        context.user_data["own_dish"] = {"name": text}
        set_state(uid, "wait_own_kbju")
        await update.message.reply_text(
            "Шаг 2 из 3: Введи КЖБУ на 100г\n\n"
            "Формат: калории | белки | жиры | углеводы\n\n"
            "Пример: 120 | 15 | 4 | 8"
        )
        return

    if state == "wait_own_kbju":
        try:
            parts = [p.strip() for p in text.replace(",", ".").split("|")]
            if len(parts) < 4:
                await update.message.reply_text("Формат: калории | белки | жиры | углеводы\nПример: 120 | 15 | 4 | 8")
                return
            context.user_data["own_dish"]["per100"] = {
                "calories": int(float(parts[0])),
                "protein":  round(float(parts[1]), 1),
                "fat":      round(float(parts[2]), 1),
                "carbs":    round(float(parts[3]), 1),
            }
            set_state(uid, "wait_own_weight")
            per = context.user_data["own_dish"]["per100"]
            await update.message.reply_text(
                "Шаг 3 из 3: Сколько граммов съел?\n\n"
                "На 100г: " + str(per["calories"]) + " ккал | "
                "Б:" + str(per["protein"]) + "г Ж:" + str(per["fat"]) + "г У:" + str(per["carbs"]) + "г\n\n"
                "Введи граммы:"
            )
        except Exception:
            await update.message.reply_text("Ошибка формата. Пример: 120 | 15 | 4 | 8")
        return

    if state == "wait_own_weight":
        try:
            grams  = int(text.replace("г", "").replace("g", "").strip())
            own    = context.user_data.get("own_dish", {})
            per    = own.get("per100", {})
            factor = grams / 100
            meal = {
                "dish":     own["name"] + " " + str(grams) + "г",
                "calories": int(per["calories"] * factor),
                "protein":  round(per["protein"] * factor, 1),
                "fat":      round(per["fat"] * factor, 1),
                "carbs":    round(per["carbs"] * factor, 1),
                "weight":   grams,
                "comment":  ""
            }
            add_meal(uid, meal)
            context.user_data["last_meal"] = meal
            clear_state(uid)
            await update.message.reply_text(format_meal_added(meal, uid), reply_markup=action_keyboard())
        except Exception:
            await update.message.reply_text("Введи граммы цифрой, например: 250")
        return

    if state == "wait_edit_calories":
        clear_state(uid)
        try:
            new_cal  = int(text.strip())
            meals, _ = get_today_meals(uid)
            meal     = context.user_data.get("last_meal")
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
                    await update.message.reply_text("Блюдо не найдено.")
        except ValueError:
            await update.message.reply_text("Введи число! Например: 178")
        return

    if state and state.startswith("wait_weight_search_"):
        name = state[19:]
        clear_state(uid)
        try:
            grams  = int(text.replace("г", "").replace("g", "").strip())
            food   = get_custom_foods(uid).get("_search_" + name)
            if not food:
                await update.message.reply_text("Продукт не найден.")
                return
            factor = grams / 100
            meal = {
                "dish": food["name"] + " " + str(grams) + "г",
                "calories": int(food["calories"] * factor),
                "protein":  round(food["protein"] * factor, 1),
                "fat":      round(food["fat"] * factor, 1),
                "carbs":    round(food["carbs"] * factor, 1),
                "weight":   grams, "comment": ""
            }
            add_meal(uid, meal)
            await update.message.reply_text(format_meal_added(meal, uid))
        except Exception:
            await update.message.reply_text("Введи граммы цифрой, например: 150")
        return

    if state and state.startswith("wait_weight_custom_"):
        name = state[19:]
        clear_state(uid)
        try:
            grams  = int(text.replace("г", "").replace("g", "").strip())
            food   = get_custom_foods(uid).get(name)
            if not food:
                await update.message.reply_text("Продукт не найден.")
                return
            factor = grams / 100
            meal = {
                "dish": name + " " + str(grams) + "г",
                "calories": int(food["calories"] * factor),
                "protein":  round(food["protein"] * factor, 1),
                "fat":      round(food["fat"] * factor, 1),
                "carbs":    round(food["carbs"] * factor, 1),
                "weight":   grams, "comment": ""
            }
            add_meal(uid, meal)
            await update.message.reply_text(format_meal_added(meal, uid))
        except Exception:
            await update.message.reply_text("Введи граммы цифрой, например: 150")
        return

    if state == "wait_new_food":
        clear_state(uid)
        try:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) < 5:
                await update.message.reply_text("Формат:\nНазвание | калории | белки | жиры | углеводы\nПример:\nОвсянка | 350 | 13 | 6 | 60")
                return
            name = parts[0]
            food = {"name": name, "calories": int(parts[1]), "protein": float(parts[2]), "fat": float(parts[3]), "carbs": float(parts[4])}
            get_custom_foods(uid)[name] = food
            await update.message.reply_text(
                "Продукт сохранён!\n\n" + name + "\n"
                "На 100г: " + str(food["calories"]) + " ккал | "
                "Б:" + str(food["protein"]) + "г Ж:" + str(food["fat"]) + "г У:" + str(food["carbs"]) + "г\n\n"
                "Доступен в /add -> Свои продукты"
            )
        except Exception:
            await update.message.reply_text("Ошибка! Проверь формат.")
        return

    if state == "wait_add_fav":
        clear_state(uid)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        try:
            result = await call_groq_json(ANALYZE_TEXT_PROMPT, text)
            get_favorites(uid)[result["dish"]] = result
            await update.message.reply_text(
                "Добавлено в избранное!\n\n" + result["dish"] + "\n"
                + str(result["calories"]) + " ккал | "
                "Б:" + str(result["protein"]) + "г Ж:" + str(result["fat"]) + "г У:" + str(result["carbs"]) + "г"
            )
        except Exception as e:
            await update.message.reply_text("Ошибка: " + str(e))
        return

    # Вопрос про питание
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask_nutrition(uid, text)
        await send(update, reply)
    except Exception as e:
        await update.message.reply_text("Ошибка: " + str(e))

# =============================================
# ИЗБРАННОЕ И ПРОДУКТЫ
# =============================================

async def favorites_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    favorites = get_favorites(uid)
    if not favorites:
        await update.message.reply_text("Избранное пусто!\n\nДобавь через /add_fav")
        return
    text = "Избранное:\n\n"
    for name, f in favorites.items():
        text += "⭐ " + name + " — " + str(f["calories"]) + " ккал\n"
    text += "\nИспользуй /add -> Избранное"
    await send(update, text)

async def add_fav_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_state(update.effective_user.id, "wait_add_fav")
    await update.message.reply_text("Напиши что добавить в избранное:\n\nПримеры:\nборщ со сметаной 300г\nкуриная грудка с гречкой")

async def my_foods_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    customs = get_custom_foods(uid)
    real    = {k: v for k, v in customs.items() if not k.startswith("_search_")}
    if not real:
        await update.message.reply_text("Своих продуктов нет!\n\nДобавь через /new_food")
        return
    text = "Мои продукты (на 100г):\n\n"
    for name, f in real.items():
        text += "• " + name + "\n  " + str(f["calories"]) + " ккал | Б:" + str(f["protein"]) + "г Ж:" + str(f["fat"]) + "г У:" + str(f["carbs"]) + "г\n\n"
    text += "Используй /add -> Свои продукты"
    await send(update, text)

async def new_food_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_state(update.effective_user.id, "wait_new_food")
    await update.message.reply_text("Добавление продукта\n\nФормат (на 100г):\nНазвание | калории | белки | жиры | углеводы\n\nПример:\nОвсянка | 350 | 13 | 6 | 60")

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
        bmr  = 10*w + 6.25*h - 5*a + (5 if profile["gender"] in ["м", "m"] else -161)
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
            "Норма калорий: " + str(target) + " ккал/день\n"
            "Норма воды: " + str(profile["target_water"]) + " мл/день\n\n"
            "Добавляй еду через /add"
        )
    except Exception as e:
        await update.message.reply_text("Ошибка: " + str(e))

async def profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    profile = get_profile(uid)
    total, meals = get_daily_total(uid)
    water   = get_water(uid)
    activity = get_activity(uid)
    if not profile["weight"]:
        await update.message.reply_text("Профиль не настроен.\nИспользуй /setup")
        return
    target   = profile.get("target_calories", 0)
    target_w = profile.get("target_water", 2000)
    remains  = target - total["calories"] if target else 0
    burned   = sum(a.get("calories_burned", 0) for a in activity)
    await update.message.reply_text(
        "Профиль:\n\n"
        "Возраст: " + str(profile["age"]) + " лет\n"
        "Вес: " + str(profile["weight"]) + " кг\n"
        "Рост: " + str(profile["height"]) + " см\n"
        "Цель: " + str(profile.get("goal", "не указана")) + "\n"
        "Норма калорий: " + str(target) + " ккал/день\n"
        "Норма воды: " + str(target_w) + " мл/день\n\n"
        "Сегодня:\n"
        "Съедено: " + str(total["calories"]) + " ккал\n"
        "Осталось: " + str(max(0, remains)) + " ккал\n"
        "Вода: " + str(water) + " мл из " + str(target_w) + " мл\n"
        "Тренировок: " + str(len(activity)) + "\n"
        "Сожжено: " + str(burned) + " ккал"
    )


# =============================================
# РЕДАКТИРОВАНИЕ ПРОФИЛЯ
# =============================================

def edit_profile_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚖️ Изменить вес",       callback_data="edit_weight")],
        [InlineKeyboardButton("📏 Изменить рост",       callback_data="edit_height")],
        [InlineKeyboardButton("🎂 Изменить возраст",    callback_data="edit_age")],
        [InlineKeyboardButton("🎯 Изменить цель",       callback_data="edit_goal")],
        [InlineKeyboardButton("🏃 Изменить активность", callback_data="edit_activity")],
        [InlineKeyboardButton("🔥 Изменить норму ккал", callback_data="edit_calories_target")],
    ])

async def edit_profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    profile = get_profile(uid)
    if not profile.get("weight"):
        await update.message.reply_text("Профиль не настроен. Используй /setup")
        return
    await update.message.reply_text(
        "Что хочешь изменить в профиле?\n\n"
        "Текущие данные:\n"
        "Вес: " + str(profile.get("weight", "—")) + " кг\n"
        "Рост: " + str(profile.get("height", "—")) + " см\n"
        "Возраст: " + str(profile.get("age", "—")) + " лет\n"
        "Цель: " + str(profile.get("goal", "—")) + "\n"
        "Норма ккал: " + str(profile.get("target_calories", "—")) + " ккал\n",
        reply_markup=edit_profile_keyboard()
    )

# =============================================
# ЗАПУСК
# =============================================

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start",       "Начать"),
        BotCommand("add",         "Добавить еду"),
        BotCommand("water",       "Добавить воду"),
        BotCommand("activity",    "Записать тренировку"),
        BotCommand("diary",       "Дневник сегодня"),
        BotCommand("calendar",    "Календарь питания"),
        BotCommand("stats",       "Статистика за неделю"),
        BotCommand("week",        "Питание за неделю"),
        BotCommand("favorites",   "Избранное"),
        BotCommand("add_fav",     "Добавить в избранное"),
        BotCommand("my_foods",    "Мои продукты"),
        BotCommand("new_food",    "Создать продукт"),
        BotCommand("profile",     "Мой профиль"),
        BotCommand("setup",       "Настроить профиль"),
        BotCommand("edit_profile", "Изменить параметры профиля"),
        BotCommand("water_goal",  "Норма воды"),
        BotCommand("clear_diary", "Очистить дневник"),
        BotCommand("help",        "Все команды"),
    ])

def main():
    print("Bot starting...")
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",       start))
    app.add_handler(CommandHandler("help",        help_cmd))
    app.add_handler(CommandHandler("add",         add_cmd))
    app.add_handler(CommandHandler("water",       water_cmd))
    app.add_handler(CommandHandler("water_goal",  water_goal_cmd))
    app.add_handler(CommandHandler("activity",    activity_cmd))
    app.add_handler(CommandHandler("diary",       diary_cmd))
    app.add_handler(CommandHandler("calendar",    calendar_cmd))
    app.add_handler(CommandHandler("stats",       stats_cmd))
    app.add_handler(CommandHandler("week",        week_cmd))
    app.add_handler(CommandHandler("clear_diary", clear_diary_cmd))
    app.add_handler(CommandHandler("favorites",   favorites_cmd))
    app.add_handler(CommandHandler("add_fav",     add_fav_cmd))
    app.add_handler(CommandHandler("my_foods",    my_foods_cmd))
    app.add_handler(CommandHandler("new_food",    new_food_cmd))
    app.add_handler(CommandHandler("setup",       setup_cmd))
    app.add_handler(CommandHandler("edit_profile", edit_profile_cmd))
    app.add_handler(CommandHandler("setprofile",  setprofile_cmd))
    app.add_handler(CommandHandler("profile",     profile_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("Bot started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
