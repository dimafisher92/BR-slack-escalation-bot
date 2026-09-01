# Braverave Escalation Guard

Slack-бот для воркспейса braverave. Отвечает в тред, когда эскалация дублирует
предыдущую, и напоминает про payments-эскалации, висящие 7 дней без решения.

| Канал         | Дубликат по    | Окно     | Напоминание о 7 днях |
|---------------|----------------|----------|----------------------|
| `#2-payments` | Transaction ID | 7 дней   | да                   |
| `#7-bonuses`  | Player ID      | 12 часов | нет                  |
| `#3-kyc`      | Player ID      | 7 дней   | нет                  |

Окно в bonuses уже остальных не случайно. Совпадение по одному player id
означает лишь «тот же человек», а не то же обращение. На реальной истории за
30 дней окно в 7 дней давало 24 срабатывания на 74 сообщения, почти все — про
уже закрытые обращения того же игрока по другому поводу; 12 часов оставляют 3,
и все они укладываются в одну смену. В payments окно шире, потому что
совпадение transaction id означает буквально одну и ту же операцию: 7 дней и
24 часа дали там одинаковые 2 срабатывания, то есть лишняя ширина не добавляет
ложных. Пороги лежат в `guard/config.py`, окно задаётся отдельно для каждого
канала.

Решением считается реакция `:white_check_mark:`. Реакция `:eyes:` показывает,
что запрос взяли в работу, но напоминание не отменяет. Если дубликатов нет,
бот молчит.

Если в сообщении есть permalink на ту самую эскалацию, которую бот нашёл как
дубликат, он молчит: саппорт прикладывает ссылку, когда просит статус по своей
же заявке, и пересказывать ему его же сообщение незачем. Совпадение, на которое
ссылки нет, показывается как обычно.

Дубликаты, посланные пока бот был выключен, при рестарте попадают в индекс
бэкфиллом, но о них никто не узнаёт — сверка на дубль запускается только для
сообщений, пришедших в реальном времени. Это осознанный компромисс: тишина
при отсутствии триггера важнее полноты покрытия задним числом.

## Установка приложения в Slack

1. Открыть https://api.slack.com/apps, нажать Create New App, выбрать
   From an app manifest, выбрать воркспейс braverave.
2. Вставить содержимое `deploy/manifest.yaml`, создать приложение.
3. Basic Information -> App-Level Tokens -> Generate Token and Scopes:
   имя любое, скоуп `connections:write`. Полученный `xapp-...` это `SLACK_APP_TOKEN`.
4. Install App -> Install to Workspace. Полученный `xoxb-...` это `SLACK_BOT_TOKEN`.
5. Пригласить бота в каналы: в каждом из трёх набрать `/invite @Escalation Guard`.
6. Взять channel ID: правый клик по каналу -> Copy link, ID это последний
   сегмент URL, вида `C0123ABCDEF`.

## Развёртывание на VPS

```bash
sudo apt-get update && sudo apt-get install -y sqlite3

sudo useradd --system --home /opt/escalation-guard escalation
sudo mkdir -p /opt/escalation-guard /var/lib/escalation-guard
sudo chown -R escalation:escalation /opt/escalation-guard /var/lib/escalation-guard

sudo -u escalation git clone <repo-url> /opt/escalation-guard
cd /opt/escalation-guard
sudo -u escalation python3 -m venv .venv
sudo -u escalation .venv/bin/pip install -r requirements.txt

sudo -u escalation cp .env.example .env
sudo -u escalation nano .env   # вписать токены и channel ID
sudo chmod 600 .env

sudo cp deploy/escalation-guard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now escalation-guard
journalctl -u escalation-guard -f
```

## Первый запуск: сухой прогон

Первые сутки прогнать без отправки сообщений, на отдельной базе:

```bash
sudo -u escalation DB_PATH=/tmp/dry.db .venv/bin/python -m guard.main --dry-run
```

**Внимание**: `--dry-run` НЕ ДОЛЖЕН работать с production базой. В режиме
dry-run poster сообщает об успехе, но не отправляет сообщение. Sweeper видит
успех и помечает старые эскалации как обработанные. Если запустить dry-run на
production.db, все нынешние stale payments-эскалации навсегда займут место
среди обработанных — напоминания о них никогда не будут отправлены.

В логе будут строки `DRY-RUN would reply ...` с полным текстом. Убедиться, что
бот срабатывает там, где ожидается, и молчит там, где дубликатов нет.
Затем удалить `/tmp/dry.db` и запустить сервис обычным образом.

## Бэкап

```bash
sudo crontab -e
# 0 4 * * * DB_PATH=/var/lib/escalation-guard/guard.db bash /opt/escalation-guard/deploy/backup.sh
```

## Разработка

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -v
```

## Настройка

Пороги и эмодзи лежат в `guard/config.py`. Добавить четвёртый канал — это
правка кода: новая многострочная запись в словаре, который возвращает
`load_channels()` в `guard/config.py`, плюс переменная окружения для её
channel ID.
