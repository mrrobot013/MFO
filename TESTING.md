# Пошаговое тестирование парсера МФО

## Самый простой способ — графический интерфейс

```bash
cd mfo_parser
./gui.sh
```

Откроется браузер `http://localhost:8501` с кнопками:

1. **▶ Запустить Chrome** — поднимает Chrome с CDP.
2. **🌐 Открыть страницу МФО** — партнёрская ссылка в этом же Chrome.
3. **▶ Запустить парсер** — заполняет анкету, отправляет.
4. В режиме **iPhone Forward SMS (авто)** SMS и звонки прилетают в Excel сами.
5. В ручном режиме после прихода SMS — вставь текст в поле, нажми **✉ Отправить**.
6. **⏹ Остановить парсер** → **🛑 Закрыть Chrome**.

Готовый файл `data/sms_log.xlsx` можно скачать прямо из интерфейса.

---

## 0. Подготовка (один раз, для CLI-режима)

```bash
cd mfo_parser
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

В `.env` обязательно:

```ini
TRACKER_URL=https://t.leads.tech/click/8/330/?sub1=bizdev&sub2=Name_vacancy
SMS_PROVIDER=webhook
CALL_PROVIDER=webhook
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8765
MANUAL_DEFAULT_SENDER=web-zaim.ru
BROWSER_MODE=cdp
```

Для ручного режима без Forward SMS:

```ini
SMS_PROVIDER=manual
CALL_PROVIDER=off
```

---

## 1. Проверка окружения

```bash
python main.py check
```

Ожидаемо:

- `✓ телефон`
- `✓ Chrome CDP на порту 9222`
- `Готово к запуску: python main.py run`

Если CDP нет — сначала:

```bash
python main.py chrome
python main.py check
```

---

## 2. Smoke: редирект без формы

```bash
python main.py browser-demo --keep-open 10
```

Проверить в логах:

- `Финальный лендинг: https://web-zaim.ru/installment?...`
- скриншот `data/screenshots/browser_demo_landing.png`

---

## 3. Демо таблицы SMS (офлайн, без браузера)

Проверяет 4 колонки ТЗ + редирект из ссылки в SMS:

```bash
python main.py demo --sender WebZaim \
  --text "WebZaim: Vash kod 4821. https://t.leads.tech/click/8/330/?sub1=bizdev&sub2=Name_vacancy"
```

Потом:

```bash
python main.py show
```

Лист **`sms` (ТЗ)** должен содержать:

| text | received_at | sender_alpha | final_redirect_url |
|------|-------------|--------------|-------------------|
| полный текст SMS | дата/время | WebZaim | https://web-zaim.ru/installment?... |

Лист **`Links`** должен содержать исходную ссылку, финальный URL, домен,
HTTP-статус и цепочку редиректов.

---

## 4. Боевой прогон (заявка + SMS)

### Перед стартом

1. Закрой лишние окна Chrome или используй профиль `~/mfo-chrome-profile`.
2. Если в Web-Zaim уже залогинен — выйди из ЛК или очисти cookies `web-zaim.ru`.
3. **Не закрывай** вкладку, которую откроет парсер.

### Запуск (режим `BROWSER_LANDING=manual` — рекомендуется)

**Важно:** парсер подключается к Chrome от `python main.py chrome`,  
не к обычному окну Chrome. Сайт нужно открыть **в том же окне**.

1. Полностью закрой Chrome (**Cmd+Q**).
2. Терминал:

```bash
python main.py chrome
```

3. В **этом** Chrome вручную открой партнёрскую ссылку (как обычно — у тебя работает).
4. Терминал:

```bash
python main.py run
```

5. Когда парсер попросит — **Enter** (он подхватит уже открытую вкладку).
6. Дождись заполнения формы и «жду SMS-код…».

Дождись в логах:

```
приземление на МФО: https://web-zaim.ru/...
заполнено полей: 7
▶ «Продолжить» — отправка регистрации
жду SMS-код…
```

На телефон придёт SMS. **Скопируй полный текст** (не только цифры) в тот же терминал:

```
web-zaim.ru|Код для регистрации: 5544 в сервисе web-zaim.ru
```

или просто текст SMS (sender подставится из `.env`).

Парсер:

1. Запишет строку в `data/sms_log.xlsx` → лист **`sms`**
2. Разберёт редирект, если в SMS есть ссылка
3. Впишет код в браузер

Завершить раньше: `.stop` + Enter.

### Проверка результата

```bash
python main.py show
open data/sms_log.xlsx
```

Файлы: `data/sms_log.xlsx`, `data/sms_log.csv`, `data/sms_log.db`.

---

## 5. Звонок (бонус, не в ТЗ)

Второй терминал, пока `run` слушает или после:

```bash
python main.py log-call --caller +79837210053 --duration 25 \
  --landing "https://web-zaim.ru/installment"
```

Строка появится в листе `calls`.

---

## 6. Полная автоматизация SMS и звонков через iPhone Forward SMS

В `.env`:

```ini
SMS_PROVIDER=webhook
CALL_PROVIDER=webhook
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8765
```

В Forward SMS настрой:

- SMS → `http://IP_MAC:8765/sms`
- звонки → `http://IP_MAC:8765/call`

Тогда OTP, последующие SMS и события звонков ловятся автоматически.

Если Forward SMS отправляет `sender = SIM 1`, а альфа-имя лежит в тексте
хвостом вида `@web-zaim.ru #1234 (23.05.2026 21:38)`, парсер сам перенесёт
`web-zaim.ru` в колонку `sender_alpha`, а дату — в `received_at`.

---

## 403 Forbidden (самое частое)

На скриншоте «Доступ к сайту web-zaim.ru запрещен» — **IP в чёрном списке**, часто из‑за VPN.

1. **Выключи любой VPN** (или смени сервер на Россию).
2. Лучше раздай **мобильный интернет** с телефона (hotspot).
3. Подожди 15–30 мин, если гонял много тестов подряд.
4. Запусти снова `python main.py run` — парсер покажет инструкцию и **подождёт Enter**:
   - вручную открой партнёрскую ссылку в **том же** Chrome (CDP);
   - дойди до калькулятора без 403;
   - Enter в терминале → парсер продолжит заполнение.

---

## Частые проблемы

| Симптом | Решение |
|---------|---------|
| `403 Forbidden` | VPN off + мобильный интернет; ручное восстановление по Enter в `run` |
| `заполнено полей: 0` | Выйти из ЛК Web-Zaim / очистить cookies |
| `no such window` | Не закрывать вкладку парсера; перезапустить `run` |
| Пустой `final_redirect_url` | В SMS нет ссылки — нормально; для проверки используй `demo` со ссылкой |
| В таблице только «5544» | Вставлять **полный** текст SMS, не только код |
