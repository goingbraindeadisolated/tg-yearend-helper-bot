"""
flow_core.py
Абстрактный модуль — содержит минимальные классы и функции для описания
потока (flow) и шагов (step). Этот файл не зависит от конкретного скрипта и
может переиспользоваться в разных ботах.

Содержит:
- Step: dataclass, описывает один шаг
- FlowManager: управляет переходами между шагами и делегирует сообщения/нажатия
- build_reply_keyboard: утилита для создания ReplyKeyboardMarkup

Принцип: кнопки reply отправляют текст как сообщение. FlowManager ожидает,
что on_message каждого шага будет обрабатывать текстовые ответы (например,
сопоставлять текст кнопки с действием и переходитьна нужный шаг).
"""
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import logging
import os
import re

from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.enums import ParseMode

# Configure module-level logger writing to bot.log in the project directory.
# Ensure a FileHandler exists that writes to bot.log even if other handlers were added.
logger = logging.getLogger("bot")
logger.setLevel(logging.DEBUG)
log_path = os.path.join(os.path.dirname(__file__), "bot.log")
try:
    # Check whether a FileHandler for our log_path already exists
    has_file_handler = False
    for h in logger.handlers:
        try:
            # FileHandler has attribute baseFilename
            if getattr(h, "baseFilename", None) == os.path.abspath(log_path):
                has_file_handler = True
                break
        except Exception:
            continue
    if not has_file_handler:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    logger.propagate = False
except Exception:
    # If anything fails during logger setup, fallback to basicConfig to ensure logging works
    logging.basicConfig(level=logging.DEBUG)

HandlerCallable = Callable[[types.Message, FSMContext, Dict[str, Any]], Any]


@dataclass
class Step:
    """Описывает шаг потока.

    Поля:
    - id: уникальный идентификатор (строка)
    - text: текст сообщения (строка или Callable(meta)->строка)
    - reply_keyboard_descriptor: список рядов, каждый ряд — список текстов кнопок
    - on_enter: вызывается при входе в шаг (может отправлять дополнительные сообщения)
    - on_message: обработчик входящих текстовых сообщений на этом шаге
    """
    id: str
    text: Optional[Callable[[Dict[str, Any]], str]] | Optional[str] = None
    reply_keyboard_descriptor: Optional[List[List[str]]] = None
    on_enter: Optional[HandlerCallable] = None
    on_message: Optional[HandlerCallable] = None
    preformatted_md: bool = False


class FlowManager:
    """Управляет набором Step'ов и переходами между ними.

    Хранит в FSMContext:
    - 'flow': имя потока
    - 'step': текущий шаг
    - 'meta': словарь с данными пользователя в течение потока
    """

    def __init__(self, name: str):
        self.name = name
        self.steps: Dict[str, Step] = {}
        logger.info(f"FlowManager.__init__ name={name}")

    def add_step(self, step: Step):
        self.steps[step.id] = step
        logger.info(f"add_step id={step.id}")

    def get_step(self, step_id: str) -> Optional[Step]:
        logger.info(f"get_step id={step_id}")
        return self.steps.get(step_id)

    async def start(self, message: types.Message, state: FSMContext, step_id: str):
        """Запускает/переключает пользователя на указанный шаг."""
        step = self.get_step(step_id)
        if not step:
            await message.answer(escape_md_v2("Шаг не найден: " + step_id), parse_mode=ParseMode.MARKDOWN_V2)
            return
        # Сохраняем контекст потока
        await state.update_data({"flow": self.name, "step": step_id, "meta": {}})
        logger.info(f"start user={message.from_user.id} step={step_id}")
        await self._enter_step(message, state, step)

    async def _enter_step(self, message: types.Message, state: FSMContext, step: Step):
        # Получаем meta-данные
        ctx = await state.get_data()
        meta = ctx.get("meta", {})
        # Формируем текст
        text = step.text(meta) if callable(step.text) else (step.text or "")
        logger.info(f"_enter_step user={message.from_user.id} step={step.id}")
        # Формируем reply-клавиатуру
        # build reply keyboard; if none specified, remove any existing keyboard
        if step.reply_keyboard_descriptor:
            kb = [[KeyboardButton(text=c) for c in row] for row in step.reply_keyboard_descriptor]
            reply_kb = ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
            logger.debug(f"reply options for step={step.id}: {step.reply_keyboard_descriptor}")
        else:
            reply_kb = ReplyKeyboardRemove()
        # Вызываем hook on_enter
        if step.on_enter:
            logger.info(f"call on_enter for step={step.id} user={message.from_user.id}")
            await step.on_enter(message, state, meta)
        # Отправляем основной текст (если задан)
        if text:
            logger.info(f"send text for step={step.id} user={message.from_user.id}")
            # escape for MarkdownV2; preserve markdown markers if step is preformatted
            try:
                esc = escape_md_v2(text, allow_markdown=bool(step.preformatted_md))
            except Exception:
                esc = text
            await message.answer(esc, reply_markup=reply_kb, parse_mode=ParseMode.MARKDOWN_V2)

    async def handle_message(self, message: types.Message, state: FSMContext):
        """Делегирует входящее сообщение соответствующему шагу (on_message)."""
        ctx = await state.get_data()
        current_step_id = ctx.get("step")
        if not current_step_id:
            # Пользователь не в потоке
            return
        step = self.get_step(current_step_id)
        if step and step.on_message:
            logger.info(f"handle_message user={message.from_user.id} step={current_step_id} text={message.text}")
            await step.on_message(message, state, ctx.get("meta", {}))
        else:
            # Если обработчика нет — информируем пользователя
            logger.info(f"no handler for step={current_step_id} user={message.from_user.id}")
            await message.answer(escape_md_v2("Пожалуйста, используйте кнопки на клавиатуре."), parse_mode=ParseMode.MARKDOWN_V2)


def build_reply_keyboard(descriptor: List[List[str]], resize: bool = True) -> ReplyKeyboardMarkup:
    """Утилита: строит ReplyKeyboardMarkup по простому descriptor'у."""
    logger.info(f"build_reply_keyboard descriptor={descriptor}")
    kb = [[KeyboardButton(text=cell) for cell in row] for row in descriptor]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=resize)


def escape_md_v2(text: str, allow_markdown: bool = False) -> str:
    """Escape text for Telegram MarkdownV2.

    Escapes characters that MarkdownV2 treats as special. Returns the escaped string.
    """
    if not isinstance(text, str):
        return text

    # Base set of characters to escape in MarkdownV2 (always escaped)
    base_pattern = re.compile(r"([\\\[\]\(\)\~\`\>\#\+\-\=\|\{\}\.\!])")
    escaped = base_pattern.sub(lambda m: "\\" + m.group(1), text)

    # If markdown is not allowed at all, also escape '*' and '_'
    if not allow_markdown:
        escaped = re.sub(r"([_*])", lambda m: "\\" + m.group(1), escaped)
        return escaped

    # If markdown is allowed, attempt to keep '*' and '_' unescaped only when they form pairs.
    # Strategy: find positions of '*' and '_' in the original text, pair them left-to-right.
    # Unpaired trailing marker will be escaped to avoid Telegram parse errors.
    def preserve_pairs(s: str, marker: str) -> str:
        positions = [i for i, ch in enumerate(s) if ch == marker]
        if not positions:
            return s
        # Determine which positions should be left unescaped: pair them (0,1),(2,3)...
        allow_pos = set()
        for i in range(0, len(positions) - 1, 2):
            allow_pos.add(positions[i])
            allow_pos.add(positions[i+1])

        # If odd count, last pos is unpaired and must be escaped
        # Build new string: escape marker if its position not in allow_pos
        out = []
        for idx, ch in enumerate(s):
            if ch == marker:
                if idx in allow_pos:
                    out.append(ch)
                else:
                    out.append("\\" + ch)
            else:
                out.append(ch)
        return "".join(out)

    # Apply pairing logic to '*' and '_'
    result = preserve_pairs(escaped, '*')
    result = preserve_pairs(result, '_')
    return result
