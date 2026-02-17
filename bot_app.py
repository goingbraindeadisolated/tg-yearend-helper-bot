"""
Конкретный бот, который читает `bot_text.txt`, парсит 9 шагов и собирает
FlowManager с reply-клавиатурами. Файл использует `flow_core.FlowManager`.

Запуск: установите BOT_TOKEN (или впишите прямо в код) и выполните:
    python bot_app.py
"""
import os
import re
import time
from flow_core import FlowManager, Step, logger, escape_md_v2
from typing import Dict, Any
import admins
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram import Router
from aiogram.types import FSInputFile
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import json
import asyncio

# Token (можно задать через переменную окружения BOT_TOKEN)
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

router = Router()

# Импортируем структурированные данные (удобно редактировать)
from bot_text import SCRIPT_STEPS, SYSTEM_TEXTS  # файл bot_text.py должен содержать SCRIPT_STEPS dict and SYSTEM_TEXTS


def normalize_label(s: str) -> str:
    """Normalize labels/messages for comparison: replace NBSP, normalize quotes, collapse whitespace, lower-case."""
    if s is None:
        return ""
    text = str(s)
    # replace non-breaking space and common unicode quotes with ASCII
    text = text.replace("\u00A0", " ")
    text = text.replace("\u2019", "'")
    text = text.replace("\u2018", "'")
    text = text.replace("\u201c", '"')
    text = text.replace("\u201d", '"')
    # collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def build_flow_from_struct(steps_struct: Dict[str, Dict[str, Any]]) -> FlowManager:
    """Создаёт FlowManager на основе готовой структуры `SCRIPT_STEPS`.

    Ожидаем формат:
    SCRIPT_STEPS = {
      "1": {"text": "...", "answers": [ {"label":"Да","action":{"type":"goto","target":"2"}}, ... ]},
      ...
    }
    """
    flow = FlowManager("script_flow")

    for step_id, info in steps_struct.items():
        text = info.get("text", "")
        # normalize text: allow tuple/list of strings in bot_text for convenience
        if isinstance(text, (list, tuple)):
            text = "\n".join(text)
        answers = info.get("answers", [])

        # Если в структуре указан документ для отправки при входе — создаём on_enter
        on_enter = None
        docname = info.get("document")
        if docname:
            def make_on_enter(doc):
                async def on_enter_fn(message: types.Message, state: FSMContext, meta: Dict[str, Any]):
                    doc_path = os.path.join(os.path.dirname(__file__), doc)
                    if os.path.exists(doc_path):
                        await message.answer_document(FSInputFile(doc_path))
                return on_enter_fn
            on_enter = make_on_enter(docname)

        prefmt = bool(info.get("md_v2", False))
        if not answers:
            step = Step(id=step_id, text=text, reply_keyboard_descriptor=None, on_enter=on_enter, preformatted_md=prefmt)
            flow.add_step(step)
            continue

        # build reply keyboard: each answer on its own row
        reply_descr: list[list[str]] = [[ans["label"]] for ans in answers]

        # create map normalized_label -> {orig, action}
        action_map = {normalize_label(ans["label"]): {"orig": ans["label"], "action": ans.get("action", {})} for ans in answers}

        def make_on_message(map_local):
            async def on_msg(message: types.Message, state: FSMContext, meta: Dict[str, Any]):
                # Берём актуальные meta из состояния, т.к. он мог обновиться
                ctx = await state.get_data()
                user_meta = ctx.get("meta", {})

                # Если пользователь присылает фото и ранее указал, что оплатил,
                # пересылаем чек админу и прикрепляем inline-кнопки для подтверждения.
                if (message.photo or message.document) and user_meta.get("pending_payment"):
                    pending = user_meta.get("pending_payment")
                    order_tag = pending.get("order_tag")
                    method = pending.get("method")
                    admin_id = admins.ADMIN_ID
                    # Forward the media to admin (if admin configured)
                    if admin_id:
                        try:
                            # Forward original message so админ увидит отправителя
                            await message.bot.forward_message(admin_id, message.chat.id, message.message_id)
                            # Send control message with inline buttons
                            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

                            kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="Подтвердить", callback_data=f"pay_confirm:{message.from_user.id}:{order_tag}"),
                                 InlineKeyboardButton(text="Отклонить", callback_data=f"pay_decline:{message.from_user.id}:{order_tag}")]
                            ])
                            await message.bot.send_message(admin_id, escape_md_v2(f"Платёж от user_id={message.from_user.id}, order={order_tag}, method={method}"), reply_markup=kb, parse_mode=ParseMode.MARKDOWN_V2)
                            await message.answer(escape_md_v2(SYSTEM_TEXTS.get("receipt_sent")), parse_mode=ParseMode.MARKDOWN_V2)
                        except Exception:
                            await message.answer(escape_md_v2(SYSTEM_TEXTS.get("receipt_send_failed")), parse_mode=ParseMode.MARKDOWN_V2)
                    else:
                        # Admin not configured — уведомим пользователя
                        await message.answer(escape_md_v2(SYSTEM_TEXTS.get("no_admin")), parse_mode=ParseMode.MARKDOWN_V2)
                    # Снимаем pending метку
                    user_meta.pop("pending_payment", None)
                    await state.update_data({"meta": user_meta})
                    return

                # Обычные текстовые ответы обрабатываем по mapping
                txt_raw = (message.text or "")
                txt = normalize_label(txt_raw)
                act_entry = map_local.get(txt)
                if not act_entry:
                    # Log unmatched text and available original labels for debugging
                    try:
                        ctx_now = await state.get_data()
                        current_step = ctx_now.get("step")
                    except Exception:
                        current_step = None
                    available = [v["orig"] for v in map_local.values()]
                    logger.info(f"Unmatched reply from user={message.from_user.id} step={current_step} text_raw='{txt_raw[:200]}' normalized='{txt[:200]}' available_labels={available}")
                    await message.answer(escape_md_v2(SYSTEM_TEXTS.get("use_buttons")), parse_mode=ParseMode.MARKDOWN_V2)
                    return
                act = act_entry.get("action")
                kind = act.get("type")
                # Специальный кейс: пользователь нажал "оплатил ..." — помечаем, ждём чек
                if txt.lower().startswith("оплатил"):
                    order_tag = str(int(time.time()))
                    user_meta["pending_payment"] = {"order_tag": order_tag, "method": txt}
                    await state.update_data({"meta": user_meta})
                    # удаляем reply-клавиатуру, чтобы пользователь мог отправить фото без лишних кнопок
                    await message.answer(escape_md_v2(SYSTEM_TEXTS.get("send_receipt_instr")), reply_markup=types.ReplyKeyboardRemove(), parse_mode=ParseMode.MARKDOWN_V2)
                    return
                if kind == "goto":
                    await flow.start(message, state, act.get("target"))
                elif kind == "screenshot":
                    # Отправляем два изображения по очереди (без медиагруппы) — надёжный вариант
                    img1 = os.path.join(os.path.dirname(__file__), "screenshot1.png")
                    img2 = os.path.join(os.path.dirname(__file__), "screenshot2.png")
                    if os.path.exists(img1):
                        await message.answer_photo(FSInputFile(img1))
                    if os.path.exists(img2):
                        await message.answer_photo(FSInputFile(img2))
                    # optional target: перейти дальше после отправки
                    if act.get("target"):
                        await flow.start(message, state, act.get("target"))
                elif kind == "raw":
                    await message.answer(escape_md_v2(act.get("payload", "")), parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    await message.answer(escape_md_v2(SYSTEM_TEXTS.get("unknown_action")), parse_mode=ParseMode.MARKDOWN_V2)

            return on_msg

        on_message = make_on_message(action_map)
        step = Step(id=step_id, text=text, reply_keyboard_descriptor=reply_descr, on_message=on_message, on_enter=on_enter, preformatted_md=prefmt)
        flow.add_step(step)

    return flow


# Build flow from structured module
script_flow = build_flow_from_struct(SCRIPT_STEPS)


# Simple persistence for list of users who interacted with the bot
USERS_FILE = os.path.join(os.path.dirname(__file__), "users.json")

def load_users() -> set:
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(int(x) for x in data)
    except Exception:
        pass
    return set()

def save_users(users: set):
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(users)), f, ensure_ascii=False)
    except Exception:
        logger.exception("Failed to save users file")

def add_user(uid: int):
    users = load_users()
    if uid not in users:
        users.add(int(uid))
        save_users(users)



@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    add_user(message.from_user.id)
    await script_flow.start(message, state, "1")


# admin_privileged removed — admin broadcast input is handled in `all_messages` so admin
# messages are not intercepted during normal flow processing.


@router.message(lambda message: not (message.text and message.text.startswith("/")))
async def all_messages(message: types.Message, state: FSMContext):
    # Verbose logging for debugging: record raw incoming message and FSM meta
    try:
        ctx = await state.get_data()
        user = message.from_user
        uid = user.id if user else None
        username = user.username if user else None
        chat_id = message.chat.id if message.chat else None
        text_repr = repr(message.text) if hasattr(message, "text") else "<no-text>"
        logger.debug(f"INCOMING message uid={uid} username={username} chat_id={chat_id} text={text_repr} ctx={ctx}")
    except Exception:
        logger.exception("Failed to log incoming message or fetch FSM meta")

    # Don't intercept commands here — let command handlers run (still log them)
    try:
        if message.text and message.text.startswith("/"):
            logger.debug(f"Ignoring command in all_messages: {message.text}")
            return
    except Exception:
        pass

    # record active user for broadcasts
    try:
        if message.from_user:
            add_user(message.from_user.id)
    except Exception:
        logger.exception("Failed to add user from incoming message")

    # Support immediate '/broadcast <text>' (one-line) from admin
    try:
        text = (message.text or "").strip()
        if text.startswith("/broadcast "):
            # command with payload in same message
            if message.from_user and message.from_user.id == admins.ADMIN_ID:
                payload = text.split(" ", 1)[1].strip()
                if payload:
                    logger.info(f"Admin requested immediate broadcast: len={len(payload)}")
                    users = load_users()
                    sent = 0
                    for uid in sorted(users):
                        try:
                            await message.bot.send_message(int(uid), escape_md_v2(payload), parse_mode=ParseMode.MARKDOWN_V2)
                            sent += 1
                        except Exception:
                            logger.exception(f"Failed to send broadcast to user={uid}")
                    await message.answer(f"Рассылка выполнена. Отправлено: {sent} пользователям.")
                else:
                    await message.answer("Пустое сообщение рассылки, отмена.")
            else:
                await message.answer("Только администратор может запускать рассылку.")
            return
    except Exception:
        logger.exception("Error while handling immediate /broadcast command")

    # If admin previously started /broadcast and we're awaiting their payload, handle it here
    try:
        ctx = await state.get_data()
        if ctx.get("awaiting_broadcast") and message.from_user and message.from_user.id == admins.ADMIN_ID:
            payload = (message.text or "").strip()
            if not payload:
                await message.answer("Пустое сообщение рассылки. Отправьте текст или отмените.")
            else:
                logger.info(f"Processing admin broadcast payload (len={len(payload)})")
                users = load_users()
                sent = 0
                for uid in sorted(users):
                    try:
                        await message.bot.send_message(int(uid), escape_md_v2(payload), parse_mode=ParseMode.MARKDOWN_V2)
                        sent += 1
                    except Exception:
                        logger.exception(f"Failed to send broadcast to user={uid}")
                await message.answer(f"Рассылка выполнена. Отправлено: {sent} пользователям.")
                # clear awaiting flag
                try:
                    await state.update_data({"awaiting_broadcast": False})
                except Exception:
                    logger.exception("Failed to clear awaiting_broadcast flag in state")
            return
    except Exception:
        logger.exception("Error while handling awaiting_broadcast payload")

    try:
        ctx = await state.get_data()
        if ctx.get("flow") == script_flow.name:
            await script_flow.handle_message(message, state)
    except Exception:
        logger.exception("Error while delegating message to script_flow.handle_message")


@router.message(Command("whoami"))
async def whoami(message: types.Message):
    # Показываем user_id и логируем — вы сможете добавить этого пользователя в admins.py
    uid = message.from_user.id
    await message.answer(escape_md_v2(f"Ваш user_id: {uid}"), parse_mode=ParseMode.MARKDOWN_V2)
    # Log to central bot.log (configured in flow_core.logger)
    try:
        logger.info(f"whoami user_id={uid} username={message.from_user.username}")
    except Exception:
        pass
    # you can copy this user_id into admins.py


@router.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message, state: FSMContext):
    # Only admin allowed
    if message.from_user.id != admins.ADMIN_ID:
        await message.answer("Только администратор может использовать эту команду.")
        return
    # If command includes payload like '/broadcast текст', send immediately
    text = (message.text or "").strip()
    if text.startswith("/broadcast "):
        payload = text.split(" ", 1)[1].strip()
        if not payload:
            await message.answer("Пустое сообщение рассылки, отмена.")
            return
        users = load_users()
        sent = 0
        for uid in sorted(users):
            try:
                await message.bot.send_message(int(uid), escape_md_v2(payload), parse_mode=ParseMode.MARKDOWN_V2)
                sent += 1
            except Exception:
                logger.exception(f"Failed to send broadcast to user={uid}")
        await message.answer(f"Рассылка выполнена. Отправлено: {sent} пользователям.")
        return

    # Set awaiting state and instruct admin about format
    await state.update_data({"awaiting_broadcast": True})
    instr = (
        "Отправьте сообщение рассылки в следующем формате:\n\n"
        "<текст рассылки>\n\n"
        "Примечание: сейчас рассылка отправляет только текстовые сообщения без inline‑кнопок.\n"
        "Кнопки можно вернуть позже — тогда потребуется реализовать обработчики callback'ов.\n\n"
        "Пример:\nПривет! У нас обновление.\n"
        "(отправьте простой текст, он будет разослан всем пользователям)"
    )
    await message.answer(instr)


@router.callback_query(lambda c: c.data and c.data.startswith("pay_confirm:"))
async def cb_pay_confirm(callback: types.CallbackQuery, state: FSMContext):
    # callback_data format: pay_confirm:<user_id>:<order_tag>
    data = callback.data.split(":")
    if len(data) < 3:
        await callback.answer()
        return
    _, user_id_str, order_tag = data[0], data[1], data[2]
    try:
        admin_id = admins.ADMIN_ID
        if callback.from_user.id != admin_id:
            await callback.answer("Only admin can confirm payments.")
            return
        user_id = int(user_id_str)
        # send document to user if exists
        pdf_path = os.path.join(os.path.dirname(__file__), "practice.pdf")
        if os.path.exists(pdf_path):
            await callback.bot.send_document(user_id, FSInputFile(pdf_path))
            await callback.message.answer(escape_md_v2(SYSTEM_TEXTS.get("payment_confirmed_admin_notify").format(user_id=user_id, order_tag=order_tag)), parse_mode=ParseMode.MARKDOWN_V2)
            try:
                    # send the official step-9 text to the user (preserve markdown if step marked md_v2)
                    text9 = SCRIPT_STEPS.get("9", {}).get("text", "")
                    # If the step is intended to keep MarkdownV2 formatting, allow it when escaping
                    allow_md = bool(SCRIPT_STEPS.get("9", {}).get("md_v2", False))
                    await callback.bot.send_message(user_id, escape_md_v2(text9, allow_markdown=allow_md), parse_mode=ParseMode.MARKDOWN_V2)
            except Exception:
                # user might have blocked bot; ignore
                pass
        else:
            await callback.answer(SYSTEM_TEXTS.get("payment_pdf_missing"))
    finally:
        await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("pay_decline:"))
async def cb_pay_decline(callback: types.CallbackQuery, state: FSMContext):
    # callback_data format: pay_decline:<user_id>:<order_tag>
    data = callback.data.split(":")
    if len(data) < 3:
        await callback.answer()
        return
    _, user_id_str, order_tag = data[0], data[1], data[2]
    admin_id = admins.ADMIN_ID
    if callback.from_user.id != admin_id:
        await callback.answer("Only admin can decline payments.")
        return
    user_id = int(user_id_str)
    try:
        await callback.bot.send_message(user_id, escape_md_v2(SYSTEM_TEXTS.get("payment_declined_user")), parse_mode=ParseMode.MARKDOWN_V2)
        await callback.message.answer(escape_md_v2(f"Отклонено администратором для user={user_id} order={order_tag}"), parse_mode=ParseMode.MARKDOWN_V2)
    finally:
        await callback.answer()

def create_dispatcher() -> Dispatcher:
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)
    return dp


async def main():
    bot = Bot(token=BOT_TOKEN)
    # Ensure no webhook is set (prevents TelegramConflictError when using getUpdates)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Deleted existing webhook (if any) to enable polling via getUpdates.")
    except Exception as e:
        logger.warning(f"Could not delete webhook before polling: {e}")
    dp = create_dispatcher()
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
