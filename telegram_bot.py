"""Start with: python telegram_bot.py"""
import asyncio
from contextlib import contextmanager, suppress
import logging
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.types import BotCommand

from app.config import Config
from app.bot import VoiceBot


@contextmanager
def instance_lock(path: Path):
    """Prevent two local polling processes from using this database."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as file:
        file.seek(0)
        if file.read(1) == b'':
            file.write(b'0')
            file.flush()
        file.seek(0)
        try:
            import msvcrt
        except ImportError:
            import fcntl
            try:
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Эта версия бота уже запущена. Закрой второе окно.') from None
            try:
                yield
            finally:
                fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        else:
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError('Эта версия бота уже запущена. Закрой второе окно.') from None
            try:
                yield
            finally:
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets):
        super().__init__('%(asctime)s %(levelname)s %(name)s: %(message)s')
        self.secrets = [x for x in secrets if x]

    def format(self, record):
        result = super().format(record)
        for secret in self.secrets:
            result = result.replace(secret, '[REDACTED]')
        return result


async def run(config):
    service = VoiceBot(config)
    cleanup_task = None
    try:
        async with Bot(config.telegram_token) as bot:
            # Retry only read-only connection checks. Generation requests are never auto-repeated.
            for attempt in range(5):
                try:
                    me = await bot.get_me()
                    break
                except TelegramNetworkError:
                    if attempt == 4:
                        raise
                    logging.warning('Нет связи с Telegram. Повтор подключения…')
                    await asyncio.sleep(min(2 ** attempt, 10))
            webhook = await bot.get_webhook_info()
            if webhook.url:
                raise RuntimeError('Для этого бота настроен webhook. Отключи старый webhook перед запуском polling.')
            await bot.set_my_commands([
                BotCommand(command='start', description='Главное меню'),
                BotCommand(command='cancel', description='Отменить текущий режим'),
                BotCommand(command='continue', description='Продолжить озвучку на трёх языках'),
                BotCommand(command='voices', description='Мои голоса'),
                BotCommand(command='newchat', description='Очистить историю диалога'),
                BotCommand(command='settings', description='Настройки'),
                BotCommand(command='id', description='Мой Telegram ID'),
                BotCommand(command='check', description='Проверка Google'),
                BotCommand(command='privacy', description='Обработка данных'),
                BotCommand(command='help', description='Помощь'),
            ])
            print(f'Бот @{me.username} подключён. Остановка: Ctrl+C', flush=True)
            if not config.allowed_ids:
                print('Отправь боту /id. Добавь свой ID в ALLOWED_USER_IDS в .env и перезапусти.', flush=True)
            if not config.google_key:
                print('Для голосовых функций добавь GEMINI_API_KEY в .env.', flush=True)
            service.sweep()
            async def periodic_cleanup():
                while True:
                    await asyncio.sleep(3600)
                    service.sweep()
            cleanup_task = asyncio.create_task(periodic_cleanup())
            dp = service.dispatcher()
            await dp.start_polling(bot, polling_timeout=25, allowed_updates=['message', 'callback_query'],
                                   close_bot_session=False)
    finally:
        if cleanup_task:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
        await service.gemini.close()
        service.store.close()


def main():
    try:
        config = Config.load()
        handler = logging.StreamHandler()
        handler.setFormatter(RedactingFormatter([config.telegram_token, config.google_key]))
        logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
        logging.getLogger('httpx').setLevel(logging.WARNING)
        with instance_lock(config.data_dir / 'bot.lock'):
            asyncio.run(run(config))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print('\nБот остановлен.')
    except TelegramUnauthorizedError:
        print('Telegram отклонил токен. Проверь новый токен BotFather в .env.')
        raise SystemExit(1)
    except TelegramNetworkError:
        print('Не удалось подключиться к Telegram. Проверь интернет и повтори запуск.')
        raise SystemExit(1)
    except (ValueError, RuntimeError) as exc:
        print(str(exc))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
