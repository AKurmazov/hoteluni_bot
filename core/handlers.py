import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.utils.exceptions import TelegramAPIError
from apscheduler.jobstores.redis import RedisJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from typing import Optional
from loguru import logger

from core.utils import decorators
from core.utils.middlewares import (
    update_middleware,
    logger_middleware
)

from core.database.models import user_model
from core.utils.states.mailing_everyone import MailingEveryoneDialog
from core.configs import telegram, database
from core.database import db_worker as db
from core import strings
from core.configs.consts import (
    LOGS_FOLDER, default_timezone
)
from core.reply_markups.inline import available_languages as available_languages_markup
from core.reply_markups.callbacks.language_choice import language_callback
from core.strings.scripts import _

logging.basicConfig(format="[%(asctime)s] %(levelname)s : %(name)s : %(message)s",
                    level=logging.INFO, datefmt="%Y-%m-%d at %H:%M:%S")

logger.remove()
logger.add(LOGS_FOLDER / "debug_logs.log", format="[{time:YYYY-MM-DD at HH:mm:ss}] {level}: {name} : {message}",
           level=logging.DEBUG,
           colorize=False)

logger.add(LOGS_FOLDER / "info_logs.log", format="[{time:YYYY-MM-DD at HH:mm:ss}] {level}: {name} : {message}",
           level=logging.INFO,
           colorize=False)

logger.add(LOGS_FOLDER / "warn_logs.log", format="[{time:YYYY-MM-DD at HH:mm:ss}] {level}: {name} : {message}",
           level=logging.WARNING,
           colorize=False)
logger.add(sys.stderr, format="[{time:YYYY-MM-DD at HH:mm:ss}] {level}: {name} : {message}", level=logging.INFO,
           colorize=False)

logging.getLogger('aiogram').setLevel(logging.INFO)

loop = asyncio.get_event_loop()
bot = Bot(telegram.BOT_TOKEN, loop=loop, parse_mode=types.ParseMode.HTML)

scheduler = AsyncIOScheduler(timezone=default_timezone, coalesce=True, misfire_grace_time=10000)
scheduler.add_jobstore(RedisJobStore(db=1,
                                     host=database.REDIS_HOST,
                                     port=database.REDIS_PORT,
                                     password=database.REDIS_PASSWORD))
scheduler.start()

dp = Dispatcher(bot, storage=MemoryStorage())


@dp.message_handler(state='*', commands=['cancel'])
@dp.message_handler(lambda msg: msg.text.lower() == 'cancel', state='*')
async def cancel_handler(msg: types.Message, state: FSMContext, raw_state: Optional[str] = None):
    if raw_state is None:
        return None
    await state.finish()
    await bot.send_message(msg.from_user.id, _(strings.cancel))


@dp.message_handler(commands=['start'], state='*')
async def start_command_handler(msg: types.Message):
    logging.info("sending message in response for /start command")
    await bot.send_message(msg.chat.id, _(strings.start_cmd))


@dp.message_handler(commands=['help'], state='*')
async def help_command_handler(msg: types.Message):
    user = await db.get_user(chat_id=msg.from_user.id)
    await bot.send_message(msg.chat.id, _(strings.help_cmd).format(name=user.first_name))


@dp.message_handler(commands='language')
async def language_cmd_handler(msg: types.Message):
    await bot.send_message(msg.from_user.id,
                           text=_(strings.language_choice),
                           reply_markup=available_languages_markup)


@dp.callback_query_handler(language_callback.filter())
async def language_choice_handler(query: types.CallbackQuery, callback_data: dict):
    await query.answer()
    await db.update_user(query.from_user.id,
                         locale=callback_data['user_locale'])
    from core.strings.scripts import i18n
    i18n.ctx_locale.set(callback_data['user_locale'])

    await bot.send_message(query.from_user.id,
                           _(strings.language_set))


@decorators.admin
@dp.message_handler(commands=['send_to_everyone'])
async def send_to_everyone_command_handler(msg: types.Message):
    await bot.send_message(msg.chat.id, strings.mailing_everyone)
    await MailingEveryoneDialog.first()


@dp.message_handler(state=MailingEveryoneDialog.enter_message)
async def mailing_everyone_handler(msg: types.Message):
    await bot.send_message(msg.chat.id, strings.got)
    scheduler.add_job(send_to_everyone, args=[msg.text])


async def send_to_everyone(txt):
    for u in user_model.User.objects():
        try:
            await bot.send_message(u.chat_id, txt)
        except TelegramAPIError:
            pass
        await asyncio.sleep(.5)


def main():
    logger.info("Compile .po and .mo before running!")

    update_middleware.on_startup(dp)
    logger_middleware.on_startup(dp)
    strings.on_startup(dp)  # enable i18n
    executor.start_polling(dp)


if __name__ == '__main__':
    main()
