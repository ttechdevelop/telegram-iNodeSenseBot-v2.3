import asyncio
import json
import logging
import os
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, InputFile, FSInputFile
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from aiohttp import ClientSession, BasicAuth
import pytz
import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
import shutil
import configparser

# --- Load Config from config.ini ---
logger = logging.getLogger(__name__)

config = configparser.ConfigParser()
config.read('config.ini')
logger.info("Config file loaded successfully.")

# Загрузка общих настроек из config.ini
BOT_TOKEN = config.get('bot', 'token').strip('"')
logger.info(f"BOT_TOKEN loaded: {BOT_TOKEN}")
TELEGRAM_GROUP_ID = int(config.get('bot', 'group_id'))
logger.info(f"TELEGRAM_GROUP_ID loaded: {TELEGRAM_GROUP_ID}")
BOT_NAME = config.get('bot', 'bot_name').strip('"')
logger.info(f"BOT_NAME loaded: {BOT_NAME}")
INDEX_JSON_URL = config.get('api', 'index_url').strip('"')
logger.info(f"INDEX_JSON_URL loaded: {INDEX_JSON_URL}")
ROUTPUT_SET_URL = config.get('api', 'routput_url').strip('"')
logger.info(f"ROUTPUT_SET_URL loaded: {ROUTPUT_SET_URL}")
AUTH_USER = config.get('api', 'auth_user').strip('"')
logger.info(f"AUTH_USER loaded: {AUTH_USER}")
AUTH_PASS = config.get('api', 'auth_pass').strip('"')
logger.info(f"AUTH_PASS loaded: {AUTH_PASS}")
AUTH_CREDENTIALS = (AUTH_USER, AUTH_PASS)
POLL_INTERVAL = int(config.get('settings', 'poll_interval', fallback='3'))
logger.info(f"POLL_INTERVAL loaded: {POLL_INTERVAL}")
DAILY_REPORT_TIMES = json.loads(config.get('settings', 'daily_report_times', fallback='["17:30"]'))
logger.info(f"DAILY_REPORT_TIMES loaded: {DAILY_REPORT_TIMES}")
ROUTPUT_NAME = config.get('settings', 'routput_name', fallback='Вентиляция').strip('"')
logger.info(f"ROUTPUT_NAME loaded: {ROUTPUT_NAME}")
MOSCOW_TZ = pytz.timezone(config.get('settings', 'timezone', fallback='Europe/Moscow').strip('"'))
logger.info(f"MOSCOW_TZ loaded: {MOSCOW_TZ}")

# Загрузка настроек webhook
WEBHOOK_HOST = config.get('webhook', 'host').strip('"')
logger.info(f"WEBHOOK_HOST loaded: {WEBHOOK_HOST}")
WEBHOOK_PATH = config.get('webhook', 'path', fallback="/").strip('"')
logger.info(f"WEBHOOK_PATH loaded: {WEBHOOK_PATH}")
WEBHOOK_URL = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}"
logger.info(f"WEBHOOK_URL loaded: {WEBHOOK_URL}")

# Загрузка настроек web app
WEBAPP_HOST = config.get('webapp', 'host', fallback="0.0.0.0").strip('"')
logger.info(f"WEBAPP_HOST loaded: {WEBAPP_HOST}")
WEBAPP_PORT = int(config.get('webapp', 'port', fallback='8777'))
logger.info(f"WEBAPP_PORT loaded: {WEBAPP_PORT}")
DEFAULT_CAMERA_CHANNEL = config.get('default','default',fallback='17').strip('"')
logger.info(f"DEFAULT_CAMERA_CHANNEL loaded: {DEFAULT_CAMERA_CHANNEL}")
# --- End load from config.ini ---


# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
logger.info("Bot initialized.")
dp = Dispatcher()
logger.info("Dispatcher initialized.")

# Хранение предыдущих состояний датчиков и вытяжки
previous_sensors_state = {}
previous_routput_state = None
bot_initialized = False  # Флаг для отслеживания инициализации бота
report_sent_status = {time: False for time in DAILY_REPORT_TIMES}  # Флаг для отслеживания отправки отчета для каждого времени
dinput_alarm_start_times = {}  # Время начала alarm для dinputs
logger.info("Variables initialized.")

# --- Logging Setup ---
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "bot.log")

# Configure logger
# logger = logging.getLogger(__name__) # already created
logger.setLevel(logging.DEBUG)  # Set the root logger level to DEBUG

# Create file handler with rotation
log_handler = TimedRotatingFileHandler(LOG_FILE, when="midnight", interval=1, backupCount=7, encoding="utf-8")
log_handler.setLevel(logging.DEBUG)

# Create console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Create formatter
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
log_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Add handlers to the logger
logger.addHandler(log_handler)
logger.addHandler(console_handler)
logger.info("Logging setup complete.")

# --- End Logging Setup ---


async def fetch_json(url):
    """Получение JSON с сервера."""
    logger.info(f"Fetching JSON from URL: {url}")
    try:
        async with ClientSession() as session:
            auth = BasicAuth(*AUTH_CREDENTIALS)
            try:
                async with session.get(url, auth=auth) as response:
                    logger.info(f"Response status: {response.status}")
                    if response.status == 200:
                        data =  await response.json()
                        logger.debug(f"JSON data received: {data}")
                        return data
                    else:
                        logger.error(f"Не удалось получить JSON с {url}. Код статуса: {response.status}")
                        return None
            except Exception as e:
                logger.error(f"Ошибка получения JSON с {url}. Ошибка: {e}")
                return None
    except Exception as e:
        logger.error(f"Ошибка при создании сессии для fetch_json: {e}")
        return None


def parse_sensors_data(data):
    """Разбор и форматирование данных датчиков из JSON."""
    logger.info("Parsing sensor data.")
    try:
        parsed_sensors = {}
        if "dinputs" in data:
            for dinput in data["dinputs"]:
                parsed_sensors[dinput['name']] = {'status': dinput['status']}
                logger.debug(f"Parsed dinput: {dinput['name']}")

        if 'sensors' in data:
            for sensor in data["sensors"]:
                if sensor['name'] != '':
                    parsed_sensors[sensor['name']] = {'status': sensor['status'], 'value': sensor.get('value', None),
                                                    'dim': sensor.get('dim', None)}
                    logger.debug(f"Parsed sensor: {sensor['name']}")
        logger.debug(f"Parsed sensors: {parsed_sensors}")
        return parsed_sensors
    except Exception as e:
        logger.error(f"Ошибка при парсинге данных датчиков: {e}")
        return {}


def parse_routput_status(data):
    """Разбор состояния вытяжки из JSON."""
    logger.info("Parsing routput status.")
    try:
        routput = data.get("routput", {})
        parsed_routput = {'name': routput.get('name', None), 'state': routput.get('state', 'off')} if routput else None
        logger.debug(f"Parsed routput: {parsed_routput}")
        return parsed_routput
    except Exception as e:
        logger.error(f"Ошибка при парсинге данных вытяжки: {e}")
        return None


async def send_daily_report():
    """Отправка ежедневного отчета о состоянии датчиков."""
    logger.info("Sending daily report.")
    try:
        json_data = await fetch_json(INDEX_JSON_URL)
        if not json_data:
            return

        sensors_data = parse_sensors_data(json_data)
        routput_info = parse_routput_status(json_data)

        report = "Ежедневный отчет:\n\n"
        for sensor_name, sensor_info in sensors_data.items():
            report += f"{sensor_name}, Состояние: <b>{sensor_info.get('status')}</b>"
            if sensor_info.get('value'):
                report += f", Значение: <b>{sensor_info.get('value')}{sensor_info.get('dim', '')}</b>"
            report += "\n"
        report += f"\nВытяжка '{routput_info.get('name', ROUTPUT_NAME)}' Состояние: <b>{routput_info.get('state')}</b>"
        logger.debug(f"Daily report message: {report}")
        await bot.send_message(TELEGRAM_GROUP_ID, report, parse_mode="HTML")
        logger.info("Daily report sent successfully.")
    except Exception as e:
        logger.error(f"Не удалось отправить ежедневный отчет. Ошибка: {e}")


async def monitor_changes():
    """Мониторинг изменений в данных датчиков и отправка уведомлений."""
    global previous_sensors_state, previous_routput_state, bot_initialized, dinput_alarm_start_times
    logger.info("Starting monitor_changes loop.")
    while True:
        try:
            if not bot_initialized:
                logger.debug("Bot not initialized, sleeping.")
                await asyncio.sleep(POLL_INTERVAL)
                continue
            
            logger.debug("Fetching JSON data for monitoring.")
            json_data = await fetch_json(INDEX_JSON_URL)
            if not json_data:
                logger.debug("Failed to fetch JSON, sleeping.")
                await asyncio.sleep(POLL_INTERVAL)
                continue        
            
            logger.debug("Parsing sensor and routput data.")
            current_sensors_state = parse_sensors_data(json_data)
            current_routput_info = parse_routput_status(json_data)
            current_routput_state = current_routput_info['state']
            current_routput_name = current_routput_info.get('name', ROUTPUT_NAME)
            
            logger.debug("Checking for changes in sensor states.")
            # Проверка изменений в состоянии отдельных датчиков
            for sensor_name, current_sensor_info in current_sensors_state.items():
                previous_sensor_info = previous_sensors_state.get(sensor_name)
                if previous_sensor_info is None or previous_sensor_info.get('status') != current_sensor_info.get('status'):
                    alert = f"⚠️ Внимание! Изменение состояния '{sensor_name}': Новое состояние: <b>{current_sensor_info.get('status')}</b>"
                    if current_sensor_info.get('value'):
                        alert += f" , Значение: <b>{current_sensor_info.get('value')}{current_sensor_info.get('dim', '')}</b>"
                    try:
                        await bot.send_message(TELEGRAM_GROUP_ID, alert, parse_mode="HTML")
                        logger.info(f"Sent sensor state change alert for: {sensor_name}")
                    except Exception as e:
                        logger.error(
                            f"Не удалось отправить уведомление об изменении состояния датчика: {sensor_name}. Ошибка: {e}")
                        
                # Проверка dinputs на 'alarm' состояние
                if 'dinputs' in json_data:
                    for dinput in json_data['dinputs']:
                        dinput_name = dinput['name']
                        dinput_status = dinput['status']
                        
                        if dinput_status == 'alarm':
                            if dinput_name not in dinput_alarm_start_times:
                                dinput_alarm_start_times[dinput_name] = datetime.now(MOSCOW_TZ)
                                logger.debug(f"Alarm start for dinput: {dinput_name}")
                            else:
                                alarm_duration = datetime.now(MOSCOW_TZ) - dinput_alarm_start_times[dinput_name]
                                if alarm_duration >= timedelta(minutes=5) and alarm_duration.seconds % 300 <= POLL_INTERVAL:
                                    warning = f"⚠️ WARNING !!\n{dinput_name} — <b>{dinput_status}</b> дольше 5-ти минут."
                                    try:
                                        await bot.send_message(TELEGRAM_GROUP_ID, warning, parse_mode="HTML")
                                        logger.info(f"Sent dinput alarm warning for: {dinput_name}")
                                    except Exception as e:
                                        logger.error(f"Не удалось отправить уведомление об alarm для dinput: {dinput_name}. Ошибка: {e}")
                                        
                        elif dinput_name in dinput_alarm_start_times:
                            del dinput_alarm_start_times[dinput_name] # Reset alarm time if status changed
                            logger.debug(f"Alarm stopped for dinput: {dinput_name}")

            # Проверка изменений состояния вытяжки
            if previous_routput_state is None or previous_routput_state != current_routput_state:
                alert = f"⚠️ Внимание! Изменение состояния вытяжки '{current_routput_name}': Новое состояние: <b>{current_routput_state}</b>"
                try:
                    await bot.send_message(TELEGRAM_GROUP_ID, alert, parse_mode="HTML")
                    logger.info(f"Sent routput state change alert for: {current_routput_name}")
                except Exception as e:
                    logger.error(f"Не удалось отправить уведомление об изменении состояния вытяжки. Ошибка: {e}")

            previous_sensors_state = current_sensors_state
            previous_routput_state = current_routput_state
            logger.debug("Finished checking for changes, sleeping.")
            await asyncio.sleep(POLL_INTERVAL)
        except Exception as e:
            logger.error(f"Произошла ошибка в основном цикле мониторинга: {e}")
            await asyncio.sleep(POLL_INTERVAL)


###################### POST request (not working) ######################

# async def control_routput(action: str):
#     """Отправка POST запроса для включения/выключения вытяжки."""
#     async with ClientSession() as session:
#         auth = BasicAuth(*AUTH_CREDENTIALS)
#         payload = {
#             "routput_config": {
#                 "maction": action,
#                 "timer_set": "0"
#              }
#         }
#         headers = {'Content-Type': 'application/json'}
#         try:
#             async with session.post(ROUTPUT_SET_URL, auth=auth, json=payload, headers=headers) as response:
#                  logger.info(f"Response status: {response.status}")
#                  if response.status == 200:
#                     json_data = await response.json()
#                     logger.info(f"Response json data: {json_data}")
#                     if json_data.get('error'):
#                          return json_data['error']
#                     else:
#                          return None
#                  else:
#                     logger.error(f"Ошибка при управлении вытяжкой. Код статуса: {response.status}")
#                     return f'Ошибка управления вытяжкой. Код:{response.status}'
#         except Exception as e:
#             logger.error(f"Ошибка при отправке POST запроса для управления вытяжкой. Ошибка: {e}")
#             return f'Ошибка при отправке запроса. Ошибка: {e}'


@dp.message(F.text == f"/start@{BOT_NAME}")
async def cmd_start(message: Message):
    """Обработка команды /start."""
    logger.info(f"Received /start command from chat: {message.chat.id}")
    try:
        if message.chat.id != TELEGRAM_GROUP_ID:
            logger.info(f"Command /start from unauthorized chat: {message.chat.id}")
            return
        global previous_sensors_state, previous_routput_state, bot_initialized

        # Получение начальных состояний датчиков и вытяжки
        logger.debug("Fetching initial data for /start command.")
        json_data = await fetch_json(INDEX_JSON_URL)
        logger.debug(f"Fetched data for /start command: {json_data}")
        if json_data:
            previous_sensors_state = parse_sensors_data(json_data)
            previous_routput_state = parse_routput_status(json_data)['state']
            logger.debug(f"parsed data  for /start command: sensors={previous_sensors_state}, routput= {previous_routput_state}")
            bot_initialized = True
            logger.info(f"bot_initialized is {bot_initialized}")
            await message.reply("Бот запущен и готов к работе!")
            logger.info("Bot initialized and /start command executed successfully.")
        else:
            await message.reply("Не удалось получить данные для инициализации бота.")
            logger.warning("Failed to get initial data for /start command.")
    except Exception as e:
        logger.error(f"Ошибка при обработке команды /start: {e}")
        await message.reply("Произошла ошибка при выполнении команды.")


@dp.message(F.text == f"/get_info@{BOT_NAME}")
async def cmd_get_info(message: Message):
    """Обработка команды /get_info."""
    logger.info(f"Received /get_info command from chat: {message.chat.id}")
    try:
        if message.chat.id != TELEGRAM_GROUP_ID:
            logger.info(f"Command /get_info from unauthorized chat: {message.chat.id}")
            return
        logger.debug("Fetching data for /get_info command.")
        json_data = await fetch_json(INDEX_JSON_URL)
        if not json_data:
            await message.reply("Не удалось получить данные.")
            logger.warning("Failed to fetch data for /get_info command.")
            return

        sensors_data = parse_sensors_data(json_data)
        routput_info = parse_routput_status(json_data)
        logger.debug(f"Parsed data for /get_info: sensors={sensors_data}, routput={routput_info}")

        response = "📍Текущее состояние датчиков:\n\n"
        for sensor_name, sensor_info in sensors_data.items():
            response += f"{sensor_name} — <b>{sensor_info.get('status')}</b>"
            if sensor_info.get('value'):
                response += f" — <b>{sensor_info.get('value')}{sensor_info.get('dim', '')}</b>"
            response += "\n"
        response += f"\n'{routput_info.get('name', ROUTPUT_NAME)}' — <b>{routput_info.get('state')}</b>"

        logger.debug(f"Response message for /get_info: {response}")
        await message.reply(response, parse_mode="HTML")
        logger.info("Sent /get_info response successfully.")
    except Exception as e:
        logger.error(f"Ошибка при обработке команды /get_info: {e}")
        await message.reply("Произошла ошибка при выполнении команды.")


@dp.message(F.text.startswith(f"/get_cam@{BOT_NAME}"))
async def cmd_get_camera(message: Message):
    """Обработчик команд /get_cam и /get_cam[channel]."""
    logger.info(f"Received /get_cam command from chat: {message.chat.id}")
    try:
        if message.chat.id != TELEGRAM_GROUP_ID:
            logger.info(f"Command /get_cam from unauthorized chat: {message.chat.id}")
            return
        text = message.text
        if text == f"/get_cam@{BOT_NAME}":
           await cmd_camera(message)
           logger.info(f"Executed /get_cam command successfully, default channel")
        elif text.startswith(f"/get_cam@{BOT_NAME}"):
            try:
                 channel = text[len(f"/get_cam@{BOT_NAME}"):].strip() # Extract the channel number from the command
                 channel = int(channel)
                 await cmd_camera(message, channel)
                 logger.info(f"Executed /get_cam command successfully, channel: {channel}")
            except ValueError:
                await message.reply("Неправильный формат команды. Используйте /get_cam или /get_cam[номер канала].")
                logger.warning("Invalid /get_cam command format.")
    except Exception as e:
        logger.error(f"Ошибка при обработке команды /get_cam: {e}")
        await message.reply("Произошла ошибка при выполнении команды.")


async def cmd_camera(update: Message, channel: str = DEFAULT_CAMERA_CHANNEL):
    """Обработка запроса на получение снимка с камеры."""
    logger.info(f"Starting cmd_camera function with channel: {channel}")
    try:
      logger.debug(str(update.chat))
      logger.debug(str(update.from_user.first_name) + ' ' + str(update.from_user.last_name) + ' ' + str(
          update.from_user.id) + ' : ' + update.text)
      try:
          chat_id = str(update.chat.id) if config.has_section(
              str(update.chat.id)) else '-1001432069292'
          url = f"http://{config[chat_id]['nvr']}/cgi-bin/snapshot.cgi?channel={channel}"
          logger.debug(f"Camera URL: {url}")
          # Delete old image if it exists
          if os.path.exists('img/img.jpeg'):
            os.remove('img/img.jpeg')
            logger.debug(f"Old image removed")

          auth = HTTPDigestAuth(config[chat_id]['login'], config[chat_id]['password'])
          logger.debug(f"Authentication set for camera: user = {config[chat_id]['login']}")
          response = requests.get(url, auth=auth, stream=True)
          response.raise_for_status()
          logger.debug(f"Response code: {response.status_code}")
          if response.status_code == 200:
              with open('img/img.jpeg', 'wb') as out_file:
                  shutil.copyfileobj(response.raw, out_file)
                  logger.debug(f"Image downloaded to img/img.jpeg")
              
              try:
                   photo = FSInputFile('img/img.jpeg')
                   await bot.send_photo(chat_id=update.chat.id, photo=photo)
                   logger.info(f"Sent photo to chat: {update.chat.id}")
              except Exception as e:
                  logger.error(f"Error sending photo to Telegram: {e}")
                  await update.reply(f"Ошибка при отправке фото в телеграмм: {e}")

              del response
              logger.debug(f"response deleted.")

          else:
            await update.reply(f"{response.status_code} - {response.reason}")
            logger.error(f"Error during camera snapshot request: {response.status_code} - {response.reason}")
              
      except requests.exceptions.RequestException as e:
          logger.error(f"Error during camera snapshot request: {e}")
          await update.reply(f"Ошибка при запросе снимка с камеры: {e}")
      except Exception as e:
          logger.error(f"Error during camera snapshot: {e}")
          await update.reply(f"Ошибка при получении снимка с камеры: {e}")
    except Exception as e:
        logger.error(f"Ошибка при вызове функции cmd_camera: {e}")
        await update.reply(f"Произошла ошибка при выполнении запроса к камере {e}")


async def scheduler():
    """Планировщик для ежедневного отчета."""
    global report_sent_status
    logger.info("Starting scheduler loop.")
    while True:
        try:
            now = datetime.now(MOSCOW_TZ)  # Current time in Moscow
            logger.debug(f"Scheduler current time: {now}")
            for report_time in DAILY_REPORT_TIMES:
                target_time = datetime.strptime(report_time, "%H:%M").time()
                target_datetime = datetime.combine(now.date(), target_time, tzinfo=MOSCOW_TZ)
                logger.debug(f"Scheduler target time: {target_datetime}")

                if now >= target_datetime and not report_sent_status[report_time]:
                    await send_daily_report()
                    report_sent_status[report_time] = True
                    logger.info(f"Daily report has been sent for {report_time}.")
    # Reset all report flags at the start of the next day
            if now.hour == 0 and now.minute == 0:
                report_sent_status = {time: False for time in DAILY_REPORT_TIMES}
                logger.info("Resetting daily report flags.")

            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"Ошибка в планировщике: {e}")
            await asyncio.sleep(60)

async def main():
    """Основная функция запуска бота."""
    logger.info("Starting main function.")
    try:
        # Запуск планировщика и мониторинга в фоновом режиме
        logger.info("Creating background tasks for monitoring and scheduler.")
        asyncio.create_task(monitor_changes())
        asyncio.create_task(scheduler())


        # Set bot commands
        logger.info("Setting bot commands.")
        bot_commands = [
            types.
            BotCommand(command=f"/start", description="Запуск бота"),
            types.BotCommand(command=f"/get_info", description="Получить информацию о датчиках"),
            types.BotCommand(command=f"/get_cam", description="Получить снимок с камеры"),
        ]
        await bot.set_my_commands(commands=bot_commands)
        logger.info("Bot commands set.")

        # Set webhook
        logger.info(f"Setting webhook URL: {WEBHOOK_URL}")
        await bot.set_webhook(url=WEBHOOK_URL)
        logger.info("Webhook set.")

        # Create aiohttp app for webhook
        logger.info("Creating aiohttp app.")
        app = web.Application()
        webhook_requests_handler = SimpleRequestHandler(
            dispatcher=dp,
            bot=bot
        )

        # Mount dispatcher to application
        webhook_requests_handler.register(app, path=WEBHOOK_PATH)

        # Setup application and add to main
        logger.info("Setting up application and adding to main.")
        setup_application(app, dp, bot=bot)
        
        # Start webserver
        logger.info(f"Starting web server on host: {WEBAPP_HOST} and port: {WEBAPP_PORT}")
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=WEBAPP_HOST, port=WEBAPP_PORT)
        await site.start()
        logger.info("Web server started.")

        # keep running
        logger.info("Bot started successfully. Entering main loop.")
        try:
          await asyncio.Future()  # Keep the application running until a future is awaited.
        except Exception as e:
          logger.error(f"Произошла ошибка в main(): {e}")
        finally:
          logger.info("Cleaning up runner.")
          await runner.cleanup()
          logger.info("Runner cleaned up.")
    except Exception as e:
        logger.error(f"Произошла ошибка при запуске main(): {e}")

if __name__ == "__main__":
    asyncio.run(main())