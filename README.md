# TwiST Ludka Bot

Бот реагирует только на официальный Telegram-слот `🎰` со значением `777`.
После `777` игрок получает поле кейсов. По умолчанию поле `5x5`, то есть `25` кнопок.

В этой версии нет минок, классики и выбора режима. Остаются только кейсы за `777`.

## Файлы

Загрузите на хостинг:

```text
bot.py
requirements.txt
README.md
.env.example
```

`.env` в GitHub загружать не нужно. Переменные окружения задаются в панели хостинга.

## Переменные окружения

```text
TELEGRAM_BOT_TOKEN=токен от BotFather
ALLOWED_CHAT_IDS=-1001234567890
OWNER_USER_IDS=1062658507
DATA_DIR=/app/data
```

Если чатов или owner несколько, пишите через запятую:

```text
ALLOWED_CHAT_IDS=-100111,-100222
OWNER_USER_IDS=111,222
```

## Команды игрока

```text
/start - запустить бота в личке
/help - помощь
/mystats - личная статистика
/chatid - узнать ID чата и свой user ID
```

## Owner-команды

Если команду owner напишет обычный пользователь, бот молча проигнорирует ее.

```text
/owner - панель управления
/panel - то же самое
/stats - статистика текущего чата, только owner
/game - показать настройки игры
/game count 25 - количество кейсов
/game chance 1/25 - шанс NFT
/game price 2 - цена прокрута в Stars для статистики экономики
/game commission 15 - комиссия стороннего сервиса в процентах
/giftbank - gifts с аккаунта owner и блоклист
/gift block https://t.me/nft/name - заблокировать gift owner
/gift unblock https://t.me/nft/name - разблокировать gift owner
/gift add nft https://t.me/nft/name Diamond Ring - fallback, если API не вернул owner gifts
/gift add ordinary Сердце - fallback обычный подарок
/settext - список шаблонов
/texts - панель всех текстов с кнопками
/settext jackpot_start текст - текст после 777
/settext nft_win текст - текст выигрыша NFT
/settext gift_win текст - текст выигрыша обычного/удаленного подарка
/settext empty_win текст - текст пустой клетки
/settext stats текст - оформление статистики
/settext owner_payout текст - уведомление owner о выигрыше
/settext payout_done текст - сообщение игроку после завершения выдачи
/settext help текст - помощь
/resetstats - обнулить статистику текущего игрового чата
```

Любое сообщение, которое бот отправляет в чат, личку или всплывающим уведомлением на кнопке, настраивается через `/texts`.
Откройте `/texts`, нажмите нужный ключ и посмотрите текущий текст.
Чтобы поменять текст, отправьте:

```text
/settext КЛЮЧ новый текст
```

Для custom emoji/готового форматирования отправьте боту готовое сообщение и ответьте на него:

```text
/settext КЛЮЧ
```

В owner-панели кнопка `Статистика` показывает периоды:

```text
последний час
последние 6 часов
последние 24 часа
последняя неделя
все время
```

Бот сам отправляет статистику в игровой чат каждые 6 часов и на каждом 200-м прокруте.

## Placeholders для текстов

```text
{username}
{case_count}
{nft_chance}
{spin_price}
{gift_title}
{gift_url}
{selected_case}
{total_spins}
{jackpots}
{opened_cases}
{nft_wins}
{gift_wins}
{empty_wins}
{gross_stars}
{net_stars}
{top5}
{period}
{period_title}
{result_type}
```

Можно использовать HTML-разметку Telegram:

```text
<b>жирный текст</b>
<i>курсив</i>
<code>моноширинный</code>
```

## Важно про банк подарков

Бот получает gifts аккаунта owner через Telegram Bot API `getUserGifts`.
Уникальные gifts считаются NFT, обычные gifts считаются обычными подарками.

Если Telegram не отдаст gifts owner, можно добавить fallback-подарки через `/gift add ...`.
Когда игрок выигрывает подарок, owner получает уведомление в личку с кнопкой `Выдача завершена`.

## Запуск локально

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```
