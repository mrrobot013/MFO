# Обратка по лидам

Это MVP без подключения к MAX/SMS. Скрипт работает только с Excel: готовит первое сообщение, читает вручную внесённый ответ, классифицирует его и готовит сообщение с персональной ссылкой.

## Excel-файл

По умолчанию используется:

`data/leads.xlsx`

Основные колонки:

- `name` — имя лида;
- `phone` — телефон;
- `max_id` — будущий ID в MAX;
- `personal_url` — персональная ссылка для этого лида;
- `status` — статус обработки;
- `first_message` — первое сообщение;
- `last_reply_text` — ответ лида;
- `reply_class` — positive / negative / unclear;
- `link_message` — сообщение с персональной ссылкой.

## Создать шаблон

```bash
source .venv/bin/activate
python outreach.py init --file data/leads.xlsx --with-sample
```

## Подготовить первые сообщения

Заполни строки лидов со статусом `new`, например:

```text
name | phone | max_id | personal_url | status
Дима | +7999... |       | https://... | new
```

Потом запусти:

```bash
python outreach.py process --file data/leads.xlsx
```

Скрипт заполнит:

- `first_message`;
- `first_message_ready_at`;
- `status = first_ready`.

Пример первого сообщения:

```text
Привет, Дим, ты хотел занять денег? Могу помочь с этим.
```

## Обработать ответ

Пока MAX/SMS не подключены, ответ вносится руками в колонку `last_reply_text`.

Пример:

```text
Да давайте
```

После этого снова запусти:

```bash
python outreach.py process --file data/leads.xlsx
```

Если ответ положительный, скрипт возьмёт `personal_url` из этой же строки и подготовит:

```text
Да, конечно. Вот твоя ссылка для оформления: https://...
```

Статус станет `link_ready`.

## Статусы

- `new` — новый лид;
- `first_ready` — первое сообщение подготовлено;
- `first_sent` — первое сообщение реально отправлено будущей интеграцией;
- `link_ready` — сообщение с персональной ссылкой подготовлено;
- `replied_negative` — отказ;
- `replied_unclear` — непонятный ответ;
- `error` — не хватает данных, например `personal_url`.

## Как подключится MAX/SMS позже

Текущая логика останется такой же. Вместо ручной отправки будущий коннектор будет:

1. Брать строки `first_ready` и отправлять `first_message` в MAX/SMS.
2. Менять статус на `first_sent`.
3. Записывать входящий ответ в `last_reply_text`.
4. Запускать классификацию.
5. Брать строки `link_ready` и отправлять `link_message`.
