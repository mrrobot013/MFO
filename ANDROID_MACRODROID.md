# Android + MacroDroid

Этот режим не ломает iPhone-сценарий. В интерфейсе выбери:

`Android SMS + звонки (webhook)`

На Android нужно скачать одно приложение:

`MacroDroid - Device Automation`

Оно может отправлять в парсер и SMS, и события входящих звонков.

Парсер слушает два endpoint:

- SMS: `http://IP_MAC:8765/sms`
- Звонки: `http://IP_MAC:8765/call`

Если Android и Mac не в одной Wi-Fi сети, запусти `./tunnel.sh` и используй Cloudflare URL:

- SMS: `https://...trycloudflare.com/sms`
- Звонки: `https://...trycloudflare.com/call`

## MacroDroid: SMS

1. Trigger: `SMS Received`
2. Action: `HTTP Request`
3. Method: `POST`
4. URL: `http://IP_MAC:8765/sms`
5. Content-Type: `application/json`
6. Body:

```json
{
  "sender": "{sms_from}",
  "text": "{sms_message}",
  "timestamp": "{year}-{month_digit}-{dayofmonth}T{hour}:{minute}:{second}+03:00"
}
```

## MacroDroid: звонки

1. Trigger: `Call Incoming` или событие из Call Log
2. Action: `HTTP Request`
3. Method: `POST`
4. URL: `http://IP_MAC:8765/call`
5. Content-Type: `application/json`
6. Body:

```json
{
  "number": "{call_number}",
  "timestamp": "{year}-{month_digit}-{dayofmonth}T{hour}:{minute}:{second}+03:00",
  "type": "incoming"
}
```

Если MacroDroid даёт длительность звонка:

```json
{
  "number": "{call_number}",
  "timestamp": "{year}-{month_digit}-{dayofmonth}T{hour}:{minute}:{second}+03:00",
  "duration": "{call_duration}",
  "type": "incoming"
}
```

Названия переменных в MacroDroid могут отличаться. Парсер понимает разные варианты:

- номер: `number`, `phoneNumber`, `caller`, `caller_id`, `from`
- время: `timestamp`, `dateMillis`, `date`, `time`, `callTime`
- длительность: `duration`, `duration_sec`, `durationMillis`
- тип звонка: `type`, `callType`, `direction`, `status`

Android `type=1/2/3` распознаётся как incoming/outgoing/missed.
