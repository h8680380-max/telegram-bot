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
user_profiles  = {}   # профиль { uid: {...} }
user_diary     = {}   # дневник { uid: { "дата": [...] } }
user_custom    = {}   # свои продукты { uid: { "название": {...} } }
user_favorites = {}   # избранное { uid: { "название": {...} } }
user_state     = {}   # состояние диалога { uid: "state" }
pending_photo  = {}   # фото в ожидании { uid: photo_b64 }

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

def set_state(uid, state):
    user_state[uid] = state

def get_state(uid):
    return user_state.get(uid)

def clear_state(uid):
    user_state.pop(uid, None)

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
    '{"dish":"название","calories":число,"protein":число,"fat":число,"carbs":число,'
    '"weight":число,"comment":"1-2 предложения о блюде"} '
    "Все числа целые."
)

ANALYZE_TEXT_PROMPT = (
    "Ты - эксперт диетолог. Пользователь написал что съел. "
    "Рассчитай калории и КЖБУ максимально точно. "
    "Если указан вес - используй его. Если нет - возьми стандартную порцию. "
    "Отвечай СТРОГО в формате JSON без лишнего текста: "
    '{"dish":"название","calories":число,"protein":число,"fat":число,"carbs":число,'
    '"weight":число,"comment":"1-2 предложения"} '
    "Все числа целые."
)

NUTRITION_PROMPT = (
    "Ты - опытный диетолог. Даешь практичные советы по питанию. "
    "Отвечай на языке пользователя, кратко и по делу."
)

# =============================================
# ЗАПРОСЫ К ИИ
# =============================================

async def call_vision(photo_b64, description):
    body = json.dumps({
        "model": "nvidia/nemotron-nano-12b-v2-vl:free",
        "messages": [
            {"role": "system", "content": ANALYZE_PHOTO_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{photo_b64}"}},
                {"type": "text", "text": f"Описание: {description}"}
            ]}
        ],
        "max_tokens": 400,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json; charset=utf-8",
            "HTTP-Referer": "https://t.me",
            "X-Title": "CalorieSnap",
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"].strip()
    if "```" in content:
        for part in content.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                content = part
                break
    return json.loads(content)

async def call_groq_json(prompt, user_text):
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_text}
    ]
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=400
    )
    content = response.choices[0].message.content.strip()
    if "```" in content:
        for part in content.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("{"):
                content = part
                break
    return json.loads(content)

async def ask_nutrition(uid, question):
    profile = get_profile(uid)
    total, _ = get_daily_total(uid)
    ctx = ""
    if profile["goal"]: ctx += f"Цель: {profile['goal']}. "
    if profile["weight"]: ctx += f"Вес: {profile['weight']}кг. "
    if total["calories"] > 0: ctx += f"Сегодня: {total['calories']} ккал."
    system = NUTRITION_PROMPT + (f"\nКонтекст: {ctx}" if ctx else "")
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
        f"Добавлено: {result['dish']}\n\n"
        f"Калории: {result['calories']} ккал\n"
        f"Белки: {result['protein']} г\n"
        f"Жиры: {result['fat']} г\n"
        f"Углеводы: {result['carbs']} г\n"
        f"Порция: ~{result['weight']} г\n"
    )
    if result.get("comment"):
        text += f"\n{result['comment']}\n"
    text += f"\nЗа сегодня: {total['calories']} ккал"
    if target:
        remains = target - total["calories"]
        text += f" (осталось {max(0,remains)} из {target})"
    return text

# =============================================
# ГЛАВНОЕ МЕНЮ ДОБАВЛЕНИЯ ЕДЫ
# =============================================

def add_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Фото блюда",        callback_data="add_photo")],
        [InlineKeyboardButton("✏️ Написать название", callback_data="add_text")],
        [InlineKeyboardButton("🔍 Поиск в базе",      callback_data="add_search")],
        [InlineKeyboardButton("⭐ Избранное",          callback_data="add_favorite")],
        [InlineKeyboardButton("🍎 Свои продукты",      callback_data="add_custom")],
    ])

# =============================================
# КОМАНДЫ
# =============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"Привет, {name}! 👋\n\n"
        "Я считаю калории и помогаю следить за питанием!\n\n"
        "Используй /add чтобы добавить еду в дневник.\n"
        "Или просто напиши вопрос про питание!\n\n"
        "/help — все команды",
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 ВСЕ КОМАНДЫ:\n\n"
        "━━━ 🍽️ ДОБАВИТЬ ЕДУ ━━━\n"
        "/add — меню добавления еды\n"
        "  📸 Фото — сфоткай блюдо\n"
        "  ✏️ Текст — напиши что съел\n"
        "  🔍 Поиск — найди продукт\n"
        "  ⭐ Избранное — быстрый доступ\n"
        "  🍎 Свои продукты — своя база\n\n"
        "━━━ 📔 ДНЕВНИК ━━━\n"
        "/diary — питание за сегодня\n"
        "/week — питание за неделю\n"
        "/clear_diary — очистить дневник\n\n"
        "━━━ ⭐ ИЗБРАННОЕ ━━━\n"
        "/favorites — список избранного\n"
        "/add_fav — добавить в избранное\n\n"
        "━━━ 🍎 СВОИ ПРОДУКТЫ ━━━\n"
        "/my_foods — мои продукты\n"
        "/new_food — добавить продукт\n\n"
        "━━━ 👤 ПРОФИЛЬ ━━━\n"
        "/setup — настроить профиль\n"
        "/profile — показать профиль\n\n"
        "━━━ 💬 СОВЕТЫ ━━━\n"
        "Просто напиши вопрос!\n"
        "Например: что съесть перед тренировкой?\n"
    )

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_state(update.effective_user.id)
    await update.message.reply_text(
        "Как добавить еду?",
        reply_markup=add_menu_keyboard()
    )

# =============================================
# ОБРАБОТКА ИНЛАЙН КНОПОК
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

    elif data == "add_text":
        set_state(uid, "wait_text_food")
        await query.message.reply_text(
            "✏️ Напиши что ты съел:\n\n"
            "Примеры:\n"
            "• 100г гречки варёной\n"
            "• борщ со сметаной 300г\n"
            "• 2 яйца варёных\n"
            "• куриная грудка с рисом\n"
            "• стакан молока 3.2%"
        )

    elif data == "add_search":
        set_state(uid, "wait_search")
        await query.message.reply_text(
            "🔍 Напиши название продукта для поиска:\n\n"
            "Примеры:\n"
            "• гречка\n"
            "• куриная грудка\n"
            "• творог 5%\n"
            "• банан"
        )

    elif data == "add_favorite":
        favorites = get_favorites(uid)
        if not favorites:
            await query.message.reply_text(
                "Избранное пусто!\n\n"
                "Добавь блюда через /add_fav\n"
                "или после добавления еды нажми 'В избранное'"
            )
            return
        keyboard = []
        for name in list(favorites.keys())[:10]:
            keyboard.append([InlineKeyboardButton(name, callback_data=f"fav_{name}")])
        await query.message.reply_text(
            "⭐ Избранное — выбери блюдо:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data == "add_custom":
        customs = get_custom_foods(uid)
        if not customs:
            await query.message.reply_text(
                "Своих продуктов нет!\n\n"
                "Добавь через /new_food"
            )
            return
        keyboard = []
        for name in list(customs.keys())[:10]:
            keyboard.append([InlineKeyboardButton(name, callback_data=f"custom_{name}")])
        await query.message.reply_text(
            "🍎 Свои продукты — выбери:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("fav_"):
        name     = data[4:]
        favorites = get_favorites(uid)
        if name in favorites:
            meal = dict(favorites[name])
            add_meal(uid, meal)
            await query.message.reply_text(format_meal_added(meal, uid))

    elif data.startswith("custom_"):
        name    = data[7:]
        customs = get_custom_foods(uid)
        if name in customs:
            set_state(uid, f"wait_weight_custom_{name}")
            food = customs[name]
            await query.message.reply_text(
                f"{name}\n"
                f"На 100г: {food['calories']} ккал | "
                f"Б:{food['protein']}г Ж:{food['fat']}г У:{food['carbs']}г\n\n"
                "Сколько граммов?"
            )

    elif data.startswith("search_add_"):
        name = data[11:]
        set_state(uid, f"wait_weight_search_{name}")
        await query.message.reply_text(f"Сколько граммов {name}?")

    elif data.startswith("save_fav_"):
        # Сохранить последнее блюдо в избранное
        meal_json = data[9:]
        try:
            meal = json.loads(meal_json)
            get_favorites(uid)[meal["dish"]] = meal
            await query.message.reply_text(f"Добавлено в избранное: {meal['dish']}")
        except:
            await query.message.reply_text("Ошибка сохранения.")

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
            "Опиши кратко:\n"
            "Например: борщ со сметаной"
        )

async def do_analyze_photo(update, context, uid, photo_b64, description):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        result = await call_vision(photo_b64, description)
        add_meal(uid, dict(result))
        text = format_meal_added(result, uid)

        # Кнопка "в избранное"
        meal_str = json.dumps({"dish": result["dish"], "calories": result["calories"],
                               "protein": result["protein"], "fat": result["fat"],
                               "carbs": result["carbs"], "weight": result["weight"]},
                              ensure_ascii=False)
        if len(meal_str) < 60:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("⭐ В избранное", callback_data=f"save_fav_{meal_str}")
            ]])
            await update.message.reply_text(text, reply_markup=keyboard)
        else:
            await update.message.reply_text(text)
        clear_state(uid)
    except Exception as e:
        await update.message.reply_text(f"Не удалось проанализировать: {e}\nПопробуй ещё раз.")

# =============================================
# ОБРАБОТКА ТЕКСТА
# =============================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
    text  = update.message.text.strip()
    state = get_state(uid)

    # Ждём описание к фото
    if state == "wait_photo_desc":
        photo_b64 = pending_photo.pop(uid, None)
        if photo_b64:
            clear_state(uid)
            await do_analyze_photo(update, context, uid, photo_b64, text)
        return

    # Ждём текстовое описание еды
    if state == "wait_text_food":
        clear_state(uid)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        try:
            result = await call_groq_json(ANALYZE_TEXT_PROMPT, text)
            add_meal(uid, dict(result))
            await update.message.reply_text(format_meal_added(result, uid))
        except Exception as e:
            await update.message.reply_text(f"Не удалось распознать: {e}\nПопробуй написать точнее.")
        return

    # Ждём поисковый запрос
    if state == "wait_search":
        clear_state(uid)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        try:
            search_prompt = (
                "Ты - база данных продуктов питания. "
                "Пользователь ищет продукт. Дай данные на 100г. "
                "Отвечай СТРОГО в JSON без лишнего текста: "
                '{"results": [{"name":"название","calories":число,"protein":число,'
                '"fat":число,"carbs":число}]} '
                "Дай 3-5 наиболее подходящих вариантов. Все числа целые."
            )
            result = await call_groq_json(search_prompt, f"Найди продукт: {text}")
            items = result.get("results", [])
            if not items:
                await update.message.reply_text("Ничего не найдено. Попробуй написать иначе.")
                return

            keyboard = []
            resp_text = f"Найдено по запросу '{text}':\n\n"
            for item in items[:5]:
                resp_text += (
                    f"• {item['name']}\n"
                    f"  100г: {item['calories']} ккал | "
                    f"Б:{item['protein']}г Ж:{item['fat']}г У:{item['carbs']}г\n\n"
                )
                keyboard.append([InlineKeyboardButton(
                    item['name'],
                    callback_data=f"search_add_{item['name'][:30]}"
                )])
                # Сохраняем данные для последующего добавления
                if uid not in user_custom: user_custom[uid] = {}
                user_custom[uid][f"_search_{item['name'][:30]}"] = item

            resp_text += "Нажми на продукт чтобы добавить:"
            await update.message.reply_text(resp_text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            await update.message.reply_text(f"Ошибка поиска: {e}")
        return

    # Ждём вес для продукта из поиска
    if state and state.startswith("wait_weight_search_"):
        name = state[19:]
        clear_state(uid)
        try:
            grams = int(text.replace("г", "").replace("g", "").strip())
            food  = get_custom_foods(uid).get(f"_search_{name}")
            if not food:
                await update.message.reply_text("Продукт не найден.")
                return
            factor = grams / 100
            meal = {
                "dish":     f"{food['name']} {grams}г",
                "calories": int(food["calories"] * factor),
                "protein":  round(food["protein"] * factor, 1),
                "fat":      round(food["fat"] * factor, 1),
                "carbs":    round(food["carbs"] * factor, 1),
                "weight":   grams,
                "comment":  ""
            }
            add_meal(uid, meal)
            await update.message.reply_text(format_meal_added(meal, uid))
        except:
            await update.message.reply_text("Напиши количество граммов цифрой, например: 150")
        return

    # Ждём вес для своего продукта
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
                "dish":     f"{name} {grams}г",
                "calories": int(food["calories"] * factor),
                "protein":  round(food["protein"] * factor, 1),
                "fat":      round(food["fat"] * factor, 1),
                "carbs":    round(food["carbs"] * factor, 1),
                "weight":   grams,
                "comment":  ""
            }
            add_meal(uid, meal)
            await update.message.reply_text(format_meal_added(meal, uid))
        except:
            await update.message.reply_text("Напиши количество граммов цифрой, например: 150")
        return

    # Ждём данные нового продукта
    if state == "wait_new_food":
        clear_state(uid)
        try:
            # Формат: Название | калории | белки | жиры | углеводы
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
                f"Продукт сохранён!\n\n"
                f"{name}\n"
                f"На 100г: {food['calories']} ккал | "
                f"Б:{food['protein']}г Ж:{food['fat']}г У:{food['carbs']}г\n\n"
                "Теперь доступен в /add -> Свои продукты"
            )
        except:
            await update.message.reply_text("Ошибка! Проверь формат и попробуй снова.")
        return

    # Ждём данные для избранного
    if state == "wait_add_fav":
        clear_state(uid)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        try:
            result = await call_groq_json(ANALYZE_TEXT_PROMPT, text)
            get_favorites(uid)[result["dish"]] = result
            await update.message.reply_text(
                f"Добавлено в избранное!\n\n"
                f"{result['dish']}\n"
                f"{result['calories']} ккал | "
                f"Б:{result['protein']}г Ж:{result['fat']}г У:{result['carbs']}г"
            )
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")
        return

    # Обычный вопрос про питание
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    try:
        reply = await ask_nutrition(uid, text)
        await send(update, reply)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

# =============================================
# ДНЕВНИК
# =============================================

async def diary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    total, meals = get_daily_total(uid)
    profile = get_profile(uid)
    target  = profile.get("target_calories", 0)

    if not meals:
        await update.message.reply_text(
            "Дневник пуст!\n\nДобавь еду через /add"
        )
        return

    text = "Дневник питания — сегодня:\n\n"
    for i, m in enumerate(meals, 1):
        text += (
            f"{i}. {m['time']} — {m['dish']}\n"
            f"   {m['calories']} ккал | "
            f"Б:{m['protein']}г Ж:{m['fat']}г У:{m['carbs']}г\n\n"
        )
    text += f"ИТОГО:\n"
    text += f"Калории: {total['calories']} ккал\n"
    text += f"Белки: {total['protein']} г\n"
    text += f"Жиры: {total['fat']} г\n"
    text += f"Углеводы: {total['carbs']} г\n"

    if target:
        remains = target - total["calories"]
        if remains > 0:
            text += f"\nОсталось: {remains} ккал из {target}"
        else:
            text += f"\nПревышение на {abs(remains)} ккал!"

    await send(update, text)

async def week_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_diary or not user_diary[uid]:
        await update.message.reply_text("Нет данных. Начни отслеживать питание!")
        return
    text = "Питание за последние дни:\n\n"
    for date, meals in sorted(user_diary[uid].items(), reverse=True)[:7]:
        total_cal = sum(m.get("calories", 0) for m in meals)
        text += f"{date}: {total_cal} ккал ({len(meals)} приёмов)\n"
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
            "Добавь блюда через /add_fav\n"
            "Формат: /add_fav борщ со сметаной 300г"
        )
        return
    text = "Избранное:\n\n"
    for name, f in favorites.items():
        text += f"⭐ {name} — {f['calories']} ккал\n"
    text += "\nИспользуй /add -> Избранное чтобы быстро добавить"
    await send(update, text)

async def add_fav_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    args = " ".join(context.args) if context.args else None
    if args:
        set_state(uid, "wait_add_fav")
        await handle_text(update, context)
    else:
        set_state(uid, "wait_add_fav")
        await update.message.reply_text(
            "Напиши что добавить в избранное:\n\n"
            "Примеры:\n"
            "• борщ со сметаной 300г\n"
            "• куриная грудка с гречкой\n"
            "• творог 200г"
        )

# =============================================
# СВОИ ПРОДУКТЫ
# =============================================

async def my_foods_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    customs = get_custom_foods(uid)
    real    = {k: v for k, v in customs.items() if not k.startswith("_search_")}
    if not real:
        await update.message.reply_text(
            "Своих продуктов нет!\n\n"
            "Добавь через /new_food"
        )
        return
    text = "Мои продукты (на 100г):\n\n"
    for name, f in real.items():
        text += (
            f"🍎 {name}\n"
            f"   {f['calories']} ккал | "
            f"Б:{f['protein']}г Ж:{f['fat']}г У:{f['carbs']}г\n\n"
        )
    text += "Используй /add -> Свои продукты чтобы добавить в дневник"
    await send(update, text)

async def new_food_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    set_state(uid, "wait_new_food")
    await update.message.reply_text(
        "Добавление своего продукта\n\n"
        "Напиши в формате (на 100г):\n"
        "Название | калории | белки | жиры | углеводы\n\n"
        "Пример:\n"
        "Овсянка | 350 | 13 | 6 | 60\n\n"
        "Или:\n"
        "Протеин шоколадный | 380 | 75 | 5 | 12"
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
        bmr = 10*w + 6.25*h - 5*a + (5 if profile["gender"] in ["м","m"] else -161)
        tdee = int(bmr * 1.55)

        if "похудеть" in profile["goal"]:
            target, goal_text = tdee - 500, "похудение"
        elif "набрать" in profile["goal"]:
            target, goal_text = tdee + 300, "набор массы"
        else:
            target, goal_text = tdee, "поддержание"

        profile["target_calories"] = target
        await update.message.reply_text(
            f"Профиль сохранён!\n\n"
            f"Возраст: {profile['age']} лет\n"
            f"Вес: {profile['weight']} кг\n"
            f"Рост: {profile['height']} см\n"
            f"Цель: {goal_text}\n"
            f"Норма: {target} ккал/день\n\n"
            "Добавляй еду через /add"
        )
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

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
        f"Профиль:\n\n"
        f"Возраст: {profile['age']} лет\n"
        f"Вес: {profile['weight']} кг\n"
        f"Рост: {profile['height']} см\n"
        f"Цель: {profile.get('goal','не указана')}\n"
        f"Норма: {target} ккал/день\n\n"
        f"Сегодня:\n"
        f"Съедено: {total['calories']} ккал\n"
        f"Осталось: {max(0,remains)} ккал\n"
        f"Приёмов пищи: {len(meals)}"
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

