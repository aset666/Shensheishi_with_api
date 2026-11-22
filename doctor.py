"""Read-only connection checks; --tts additionally generates three short samples."""
import argparse
import asyncio
from pathlib import Path
from aiogram import Bot
from app.config import Config
from app.gemini import Gemini, GeminiError
from app.audio import Audio


async def check(tts=False):
    c = Config.load()
    print('Telegram token: present (not printed)')
    print('Google API key:', 'present' if c.google_key else 'MISSING')
    print('Allowed users:', len(c.allowed_ids))
    converter = Audio(c.ffmpeg)
    await converter.run('-version')
    print('FFmpeg: OK')
    async with Bot(c.telegram_token) as bot:
        me = await bot.get_me()
        print('Telegram: @' + (me.username or 'bot'))
    google = Gemini(c.google_key, c.text_model, c.tts_model)
    try:
        for model in await google.check_models():
            print('Model found:', model)
        if tts:
            out = c.data_dir / 'checks'
            out.mkdir(parents=True, exist_ok=True)
            for lang, text in [('ru', 'Привет! Проверка русского языка.'),
                               ('kk', 'Сәлем! Қазақ тілін тексеріп жатырмыз.'),
                               ('en', 'Hello! This is an English voice test.')]:
                (out / f'{lang}.wav').write_bytes(await google.speech(text, 'Kore', lang))
                await converter.mp3(out / f'{lang}.wav', out / f'{lang}.mp3')
                print('Created:', out / f'{lang}.mp3')
            print('These 3 generation requests consume Google quota/billing.')
        print('Read-only checks passed. Voice cloning still needs a real reference and consent test.')
    finally:
        await google.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--tts', action='store_true', help='Generate 3 short samples; consumes Google quota/billing.')
    args = parser.parse_args()
    try:
        asyncio.run(check(args.tts))
    except Exception as exc:
        # Never print library exceptions that could include tokens in request URLs.
        if isinstance(exc, (GeminiError, ValueError)):
            print('Check failed:', str(exc))
        else:
            print('Check failed:', type(exc).__name__, '(check connection, dependencies and credentials)')
        raise SystemExit(1)
