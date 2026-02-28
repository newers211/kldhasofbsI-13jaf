#!/usr/bin/env python3
import os
import sys
import asyncio
import logging
import signal
from typing import Optional, Set

import aiosqlite
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    InlineKeyboardButton,
    WebAppInfo,
    MenuButtonWebApp,
    CallbackQuery,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiocryptopay import AioCryptoPay, Networks

from dotenv import load_dotenv

# Load local .env if present (for local dev)
load_dotenv()

# --- CONFIG FROM ENV ---
API_TOKEN = os.environ.get("API_TOKEN")
CRYPTO_TOKEN = os.environ.get("CRYPTO_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@zen_finance_app")
MINI_APP_URL = os.environ.get("MINI_APP_URL", "https://betatestappzenfinance.netlify.app")
BANNER_URL = os.environ.get("BANNER_URL", "https://img.freepik.com/free-vector/gradient-stock-market-concept_23-2149166910.jpg")
CEO_USERNAME = os.environ.get("CEO_USERNAME", "newers77")
LEGENDA_USERNAME = os.environ.get("LEGENDA_USERNAME", "vveivi_flow")
DATABASE_PATH = os.environ.get("DATABASE_PATH", "users.db")

REQUIRED = {
    "API_TOKEN": API_TOKEN,
    "CRYPTO_TOKEN": CRYPTO_TOKEN,
}
missing = [k for k, v in REQUIRED.items() if not v]
if missing:
    logging.basicConfig(level=logging.INFO)
    logging.error("Missing required environment variables: %s", ", ".join(missing))
    logging.error("Set them in your environment or create a .env file.")
    sys.exit(1)

# --- Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --- Bot / Dispatcher / Crypto client ---
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
crypto = AioCryptoPay(token=CRYPTO_TOKEN, network=Networks.MAIN_NET)

# Track background tasks so we can cancel them gracefully on shutdown
PAYMENT_TASKS: Set[asyncio.Task] = set()

class DonateState(StatesGroup):
    waiting_for_custom_amount = State()

# --- TEXTS ---
TEXTS = {
    'ru': {
        'start': "<b>Выберите язык / Choose language</b>",
        'sub_req': "<b>⚠️ Доступ ограничен</b>\n\nПодпишитесь на канал, чтобы пользоваться <b>Zen Finance</b>!",
        'sub_button': "💎 Подписаться на канал",
        'check_button': "🔄 Проверить подписку",
        'welcome': "<b>📊 Главное меню</b>\n\nПривет, {name}!\nСтатус: <code>{status}</code>",
        'open_app': "🚀 Запустить Zen Finance",
        'settings': "⚙️ Настройки",
        'donate_btn': "☕ Поддержать проект",
        'not_subbed': "❌ Вы всё еще не подписаны!",
        'donate_choice': "<b>☕ Донат</b>\nВыберите сумму или введите свою:",
        'pay_button': "💳 Оплатить {amount} USDT",
        'success_pay': "✅ Оплата получена! Статус обновлен. Спасибо! ❤️",
        'custom_amount': "⌨️ Своя сумма",
        'enter_amount': "Введите сумму доната (целое число USDT):",
        'bad_amount': "❌ Ошибка! Введите число (например: 20)",
        'back': "⬅️ Назад"
    },
    'en': {
        'start': "<b>Choose Language</b>",
        'sub_req': "<b>⚠️ Access Restricted</b>\n\nSubscribe to the channel to use <b>Zen Finance</b>!",
        'sub_button': "💎 Subscribe",
        'check_button': "🔄 Check Subscription",
        'welcome': "<b>📊 Main Menu</b>\n\nHi, {name}!\nStatus: <code>{status}</code>",
        'open_app': "🚀 Launch Zen Finance",
        'settings': "⚙️ Settings",
        'donate_btn': "☕ Donate",
        'not_subbed': "❌ Still not subscribed!",
        'donate_choice': "<b>☕ Support us</b>\nSelect amount or enter custom:",
        'pay_button': "💳 Pay {amount} USDT",
        'success_pay': "✅ Payment received! Status updated. Thanks! ❤️",
        'custom_amount': "⌨️ Custom Amount",
        'enter_amount': "Enter donation amount (USDT integer):",
        'bad_amount': "❌ Error! Enter a number (e.g. 20)",
        'back': "⬅️ Back"
    }
}

# --- DB ---
async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users 
                          (user_id INTEGER PRIMARY KEY, lang TEXT, total_donated REAL DEFAULT 0)''')
        await db.commit()
    logging.info("DB initialized at %s", DATABASE_PATH)

async def get_user_data(user_id: int) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

# --- STATUS LOGIC ---
def determine_status(username: Optional[str], total_donated: float):
    if username == CEO_USERNAME:
        return "CEO💀"
    if username == LEGENDA_USERNAME:
        return "LEGENDA 🏆"
    if total_donated and total_donated > 0:
        return "Donator 💰"
    return "Standard 👤"

async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False

# --- KEYBOARDS ---
def get_main_kb(lang):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=TEXTS[lang]['open_app'], web_app=WebAppInfo(url=MINI_APP_URL)))
    builder.row(InlineKeyboardButton(text=TEXTS[lang]['settings'], callback_data="open_settings"))
    builder.row(InlineKeyboardButton(text=TEXTS[lang]['donate_btn'], callback_data="open_donate"))
    return builder.as_markup()

def get_lang_kb(lang, show_back=False):
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🇷🇺 RU", callback_data="setlang_ru"))
    builder.add(InlineKeyboardButton(text="🇺🇸 EN", callback_data="setlang_en"))
    if show_back:
        builder.row(InlineKeyboardButton(text=TEXTS[lang]['back'], callback_data="back_main"))
    return builder.as_markup()

def get_donate_kb(lang):
    builder = InlineKeyboardBuilder()
    for amount in [1, 5, 10, 50]:
        builder.add(InlineKeyboardButton(text=f"💎 {amount}$", callback_data=f"buy_{amount}"))
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text=TEXTS[lang]['custom_amount'], callback_data="buy_custom"))
    builder.row(InlineKeyboardButton(text=TEXTS[lang]['back'], callback_data="back_main"))
    return builder.as_markup()

# --- HANDLERS ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_db = await get_user_data(message.from_user.id)
    if not user_db:
        # show language selection
        await message.answer(TEXTS['ru']['start'], reply_markup=get_lang_kb('ru'))
    else:
        await show_main_menu(message.chat.id, message.from_user, user_db['lang'])

async def show_main_menu(chat_id: int, user: types.User, lang: str):
    db_data = await get_user_data(user.id)
    donated = db_data['total_donated'] if db_data else 0
    status = determine_status(user.username, donated)

    if await is_subscribed(user.id):
        try:
            await bot.set_chat_menu_button(chat_id=chat_id, menu_button=MenuButtonWebApp(text="Zen Finance", web_app=WebAppInfo(url=MINI_APP_URL)))
        except Exception:
            logging.debug("Failed to set chat menu button (might be unsupported).")
        try:
            await bot.send_photo(chat_id, photo=BANNER_URL, caption=TEXTS[lang]['welcome'].format(name=user.first_name, status=status), reply_markup=get_main_kb(lang))
        except Exception as e:
            logging.exception("Failed to send main menu photo: %s", e)
            # fallback to text
            await bot.send_message(chat_id, TEXTS[lang]['welcome'].format(name=user.first_name, status=status), reply_markup=get_main_kb(lang))
    else:
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text=TEXTS[lang]['sub_button'], url=f"https://t.me/{CHANNEL_ID.lstrip('@')}"))
        builder.row(InlineKeyboardButton(text=TEXTS[lang]['check_button'], callback_data="check_sub"))
        try:
            await bot.send_message(chat_id, TEXTS[lang]['sub_req'], reply_markup=builder.as_markup())
        except Exception:
            logging.exception("Failed to send subscription request")

@dp.callback_query(F.data.startswith("setlang_"))
async def callbacks_lang(callback: CallbackQuery):
    lang = callback.data.split("_", 1)[1]
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (user_id, lang, total_donated) VALUES (?, ?, COALESCE((SELECT total_donated FROM users WHERE user_id = ?), 0))",
            (callback.from_user.id, lang, callback.from_user.id)
        )
        await db.commit()
    # remove the language selection message (if possible)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_main_menu(callback.message.chat.id, callback.from_user, lang)

@dp.callback_query(F.data == "open_settings")
async def settings_menu(callback: CallbackQuery):
    user = await get_user_data(callback.from_user.id)
    lang = user['lang'] if user else 'ru'
    # Try to edit caption, fallback to edit_text
    try:
        await callback.message.edit_caption(caption=TEXTS[lang]['start'], reply_markup=get_lang_kb(lang, True))
    except Exception:
        try:
            await callback.message.edit_text(TEXTS[lang]['start'], reply_markup=get_lang_kb(lang, True))
        except Exception:
            await callback.answer(TEXTS[lang]['start'], show_alert=True)

@dp.callback_query(F.data == "open_donate")
async def donate_menu(callback: CallbackQuery):
    user = await get_user_data(callback.from_user.id)
    lang = user['lang'] if user else 'ru'
    try:
        await callback.message.edit_caption(caption=TEXTS[lang]['donate_choice'], reply_markup=get_donate_kb(lang))
    except Exception:
        try:
            await callback.message.edit_text(TEXTS[lang]['donate_choice'], reply_markup=get_donate_kb(lang))
        except Exception:
            await callback.answer(TEXTS[lang]['donate_choice'], show_alert=True)

@dp.callback_query(F.data == "buy_custom")
async def buy_custom(callback: CallbackQuery, state: FSMContext):
    user = await get_user_data(callback.from_user.id)
    lang = user['lang'] if user else 'ru'
    await callback.answer()
    try:
        await callback.message.answer(TEXTS[lang]['enter_amount'])
    except Exception:
        await callback.answer(TEXTS[lang]['enter_amount'], show_alert=True)
    await state.set_state(DonateState.waiting_for_custom_amount)

@dp.message(DonateState.waiting_for_custom_amount)
async def process_custom_amount(message: Message, state: FSMContext):
    user_db = await get_user_data(message.from_user.id)
    lang = user_db['lang'] if user_db else 'ru'
    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer(TEXTS[lang]['bad_amount'])
        return
    amount = int(message.text)
    await state.clear()
    await create_invoice_logic(message, amount, lang, is_cb=False)

@dp.callback_query(F.data.startswith("buy_"))
async def create_invoice_cb(callback: CallbackQuery):
    amount = int(callback.data.split("_", 1)[1])
    user_db = await get_user_data(callback.from_user.id)
    lang = user_db['lang'] if user_db else 'ru'
    await create_invoice_logic(callback.message, amount, lang, is_cb=True)

async def create_invoice_logic(target: Message, amount: int, lang: str, is_cb: bool):
    try:
        invoice = await crypto.create_invoice(asset='USDT', amount=amount)
    except Exception as e:
        logging.exception("Failed to create invoice: %s", e)
        try:
            await target.answer("❌ Не удалось создать счёт. Попробуйте позже.")
        except Exception:
            pass
        return

    url = getattr(invoice, 'bot_invoice_url', None) or getattr(invoice, 'pay_url', None) or 'https://t.me/CryptoBot'

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text=TEXTS[lang]['pay_button'].format(amount=amount), url=url))
    builder.row(InlineKeyboardButton(text=TEXTS[lang]['back'], callback_data="open_donate"))

    msg_text = f"💳 Счет на <b>{amount} USDT</b> создан!"
    try:
        if is_cb:
            # attempts to edit the message (works if origin was a photo with caption)
            try:
                await target.edit_caption(caption=msg_text, reply_markup=builder.as_markup())
            except Exception:
                await target.edit_text(msg_text, reply_markup=builder.as_markup())
        else:
            await target.answer(msg_text, reply_markup=builder.as_markup())
    except Exception:
        logging.exception("Failed to present invoice buttons to user")

    # start background payment checker
    invoice_id = getattr(invoice, "invoice_id", None) or getattr(invoice, "id", None)
    if invoice_id is None:
        logging.warning("Invoice has no invoice_id; cannot start check loop.")
        return

    task = asyncio.create_task(check_payment_loop(target.chat.id, invoice_id, amount, lang))
    PAYMENT_TASKS.add(task)
    task.add_done_callback(lambda t: PAYMENT_TASKS.discard(t))

async def check_payment_loop(user_id: int, invoice_id: str, amount: int, lang: str):
    try:
        for _ in range(180):  # ~30 minutes (180 * 10s)
            await asyncio.sleep(10)
            try:
                invoices = await crypto.get_invoices(invoice_ids=invoice_id)
            except Exception:
                # try single-invoice getter if library differs
                try:
                    invoices = await crypto.get_invoice(invoice_id)
                except Exception as e:
                    logging.debug("Failed to fetch invoice status: %s", e)
                    continue

            inv_list = invoices if isinstance(invoices, list) else [invoices]
            if any(getattr(inv, "status", None) == "paid" for inv in inv_list):
                # update DB
                try:
                    async with aiosqlite.connect(DATABASE_PATH) as db:
                        await db.execute("UPDATE users SET total_donated = total_donated + ? WHERE user_id = ?", (amount, user_id))
                        await db.commit()
                except Exception:
                    logging.exception("Failed to update donation in DB")

                # notify user
                try:
                    await bot.send_message(user_id, TEXTS[lang]['success_pay'])
                except Exception:
                    logging.debug("Failed to send thank-you message to user %s", user_id)
                break
    except asyncio.CancelledError:
        logging.info("Payment check task for invoice %s was cancelled", invoice_id)
    except Exception:
        logging.exception("Unexpected error in payment check loop for invoice %s", invoice_id)

@dp.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery):
    user = await get_user_data(callback.from_user.id)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await show_main_menu(callback.message.chat.id, callback.from_user, user['lang'] if user else 'ru')

@dp.callback_query(F.data == "check_sub")
async def callback_check(callback: CallbackQuery):
    user = await get_user_data(callback.from_user.id)
    lang = user['lang'] if user else 'ru'
    if await is_subscribed(callback.from_user.id):
        try:
            await callback.message.delete()
        except Exception:
            pass
        await show_main_menu(callback.message.chat.id, callback.from_user, lang)
    else:
        await callback.answer(TEXTS[lang]['not_subbed'], show_alert=True)

# --- RUN / SHUTDOWN HELPERS ---
should_exit = asyncio.Event()

def _signal_handler(sig):
    logging.info("Received signal %s, initiating shutdown...", sig)
    should_exit.set()

async def shutdown_cleanup():
    # cancel payment tasks
    if PAYMENT_TASKS:
        logging.info("Cancelling %d payment tasks...", len(PAYMENT_TASKS))
        for t in list(PAYMENT_TASKS):
            t.cancel()
        await asyncio.gather(*list(PAYMENT_TASKS), return_exceptions=True)
    # close crypto client
    try:
        await crypto.close()
    except Exception:
        logging.debug("crypto.close() failed during shutdown")
    # close bot session
    try:
        await bot.session.close()
    except Exception:
        pass
    logging.info("Shutdown cleanup done.")

async def main():
    # register signals
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, lambda sig=s: _signal_handler(sig))

    await init_db()
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception:
        logging.debug("No webhook to delete or delete failed.")

    polling_task = asyncio.create_task(dp.start_polling(bot))
    try:
        # Wait until a signal triggers should_exit
        await should_exit.wait()
    finally:
        logging.info("Stopping polling...")
        polling_task.cancel()
        try:
            await polling_task
        except Exception:
            pass
        await shutdown_cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Exited by user")