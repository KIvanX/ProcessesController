import logging
import signal
from datetime import datetime

import dotenv
import asyncio
import subprocess
import psutil
import os

import requests
from aiogram import types, F, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

dotenv.load_dotenv()

NUM_PROCESSES = 2
bot = Bot(token=os.environ['TOKEN'], default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
ipv4 = requests.get("https://ifconfig.me").text.strip()
pause = True

logging.root.handlers.clear()
logging.basicConfig(level=logging.WARNING, filename="logs.log", filemode="a", format="%(asctime)s %(levelname)s %(message)s\n" + '_' * 100)


def get_processes():
    processes = []
    for process in psutil.process_iter():
        try:
            if process.name() == 'python' and process.cwd().startswith(os.environ['WORKER_PATH']):
                processes.append(process.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return processes


async def restorer():
    while True:
        processes = get_processes()
        if not pause and len(processes) < NUM_PROCESSES:
            path = os.environ["WORKER_PATH"]
            subprocess.Popen(f'nohup {path}/.venv/bin/python {path}/main.py &', shell=True, cwd=path)
        await asyncio.sleep(10)


@dp.callback_query(F.data == 'start')
async def start(data):
    message: types.Message = data.message if isinstance(data, types.CallbackQuery) else data

    processes = get_processes()
    ssd, ram = psutil.disk_usage('/'), psutil.virtual_memory()
    text = f'⚙️ <b>Общая информация о системе</b>\n\n' \
           f'<b>Public IP:</b> <code>{ipv4}</code>\n' \
           f'<b>CPU:</b> <b>{psutil.cpu_percent()}</b>%\n' \
           f'<b>RAM:</b> <b>{ram.used / (1024 ** 3):.2f}</b>GB / <b>{ram.total / (1024 ** 3):.2f}</b>GB (<b>{ram.percent}</b>%)\n' \
           f'<b>SSD:</b> <b>{ssd.used / (1024 ** 3):.2f}</b>GB / <b>{ssd.total / (1024 ** 3):.2f}</b>GB (<b>{ssd.percent}</b>%)\n' \
           f'<b>Запущена:</b> <b>{datetime.fromtimestamp(psutil.boot_time()).strftime("%d.%m.%Y, %H:%M")}</b>\n\n' \
           f'🧑‍💻 <b>Работающие процессы</b> (<b>{len(processes)}</b> / <b>{NUM_PROCESSES}</b>)'

    keyboard = InlineKeyboardBuilder()
    for pid in processes:
        keyboard.add(types.InlineKeyboardButton(text=str(pid), callback_data=f'process_{pid}'))
    keyboard.row(types.InlineKeyboardButton(text='🔄 Обновить', callback_data='start'))
    if processes:
        keyboard.row(types.InlineKeyboardButton(text='🔴 Завершить процессы', callback_data='all_stop'))
    else:
        keyboard.row(types.InlineKeyboardButton(text='🟢 Запустить процессы', callback_data='all_start'))
    # keyboard.row(types.InlineKeyboardButton(text='🆕 Запустить новый', callback_data='new_process'))

    send_message = message.answer if isinstance(data, types.Message) else message.edit_text
    await send_message(text, reply_markup=keyboard.as_markup())


@dp.callback_query(F.data.startswith('process_'))
async def process_menu(call: types.CallbackQuery):
    pid = call.data.split('_')[1]
    with open(os.environ['WORKER_PATH'] + '/logs.log') as f:
        logs = f.read().split('\n')

    keyboard = InlineKeyboardBuilder()
    keyboard.row(types.InlineKeyboardButton(text='🏁 Завершить', callback_data=f'stop_{pid}'))
    keyboard.row(types.InlineKeyboardButton(text='☠️ Убить', callback_data=f'kill_{pid}'))
    keyboard.row(types.InlineKeyboardButton(text='⬅️ Назад', callback_data='start'))

    done = len([log for log in logs if log.startswith(f'[{pid}]') and 'DONE' in log])
    await call.message.edit_text(f'Процесс <code>{pid}</code>: {done}', reply_markup=keyboard.as_markup())


@dp.callback_query(F.data == 'new_process')
async def new_process(call: types.CallbackQuery):
    path = os.environ["WORKER_PATH"]
    subprocess.Popen(f'nohup {path}/.venv/bin/python {path}/main.py &', shell=True, cwd=path)
    await call.answer('✅ Процесс запущен')
    await start(call)


@dp.callback_query(F.data.startswith('stop_') | F.data.startswith('kill_'))
async def stop_process(call: types.CallbackQuery):
    try:
        os.kill(int(call.data.split('_')[1]), signal.SIGTERM if call.data.startswith('stop') else signal.SIGKILL)
        await call.answer(f'✅ Процесс {"завершен" if call.data.startswith("stop") else "убит"}')
    except OSError:
        await call.answer('❗️ Не удалось завершить процесс')

    await start(call)


@dp.callback_query(F.data == 'all_stop')
async def kill_all_process(call: types.CallbackQuery):
    global pause
    try:
        for process in get_processes():
            os.kill(process, signal.SIGTERM)
        await call.answer(f'💤 Завершение процессов...')
        await asyncio.sleep(3)
        for process in get_processes():
            os.kill(process, signal.SIGKILL)
    except OSError:
        pass

    pause = True
    await call.answer(f'✅ Все процессы убиты')
    await start(call)


@dp.callback_query(F.data == 'all_start')
async def start_all_processes(call: types.CallbackQuery):
    global pause
    pause = False
    await call.answer('✅ Процессы запускаются...')
    await asyncio.sleep(10)
    await start(call)


@dp.message(F.text.isdigit())
async def set_num_processes(message: types.Message):
    global NUM_PROCESSES
    NUM_PROCESSES = int(message.text)
    await message.delete()


@dp.message(Command('logs'))
async def get_logs(message: types.Message):
    if str(message.chat.id) == os.environ['ADMIN_ID']:
        await message.delete()
        keyboard = InlineKeyboardBuilder().row(types.InlineKeyboardButton(text='ОК', callback_data='del'))
        await bot.send_document(message.chat.id, FSInputFile('logs.log'), reply_markup=keyboard.as_markup())

        with open('logs.log', 'w') as file:
            file.write(str(datetime.now()) + '\n')


@dp.startup()
async def on_start():
    await bot.set_my_commands([types.BotCommand(command='start', description='Старт')])


async def main():
    dp.message.register(start, Command('start'))
    asyncio.create_task(restorer())

    logging.warning('Controller is running...')
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
