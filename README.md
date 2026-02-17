Шаблон Telegram‑бота на aiogram (пошаговый)

Файлы
- [bot.py](bot.py): Основной шаблон — здесь редактируйте тексты и клавиатуры.
- [requirements.txt](requirements.txt): зависимости.

Как запустить
1. Установите зависимости:

```powershell
pip install -r requirements.txt
```

2. Установите токен бота (один из вариантов):
- Экспорт через переменную окружения `BOT_TOKEN`, например в PowerShell:

```powershell
$env:BOT_TOKEN="<ваш_токен>"
python bot.py
```

- Или замените значение `BOT_TOKEN` в начале `bot.py`.

Редактирование
- Тексты находятся в словаре `TEXTS` в файле [bot.py](bot.py).
- Inline‑клавиатуры и reply‑клавиатуры собираются в функциях `inline_kb_start()`, `inline_kb_nav()` и `reply_kb_cancel()` — редактируйте там метки, callback_data и структуру.
- Состояния пошагового процесса описаны в `class Form(StatesGroup)`; добавляйте новые `State()` при необходимости и обработчики в `router`.

Если хотите, могу добавить пример сохранения ответов в файл/БД, поддержку callback data с payload или расширенный менеджер состояний.