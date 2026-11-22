from dataclasses import dataclass
import os
from pathlib import Path
import re
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    telegram_token: str
    google_key: str
    allowed_ids: frozenset[int]
    data_dir: Path
    text_model: str = 'gemini-3.8-flash'
    tts_model: str = 'gemini-3.8-flash-tts'
    daily_user: int = 25
    daily_total: int = 100
    max_voices: int = 3
    max_text: int = 3000
    max_audio_seconds: int = 180
    max_upload_mb: int = 15
    ffmpeg: str = ''

    @classmethod
    def load(cls):
        load_dotenv(ROOT / '.env', encoding='utf-8-sig', override=True)
        token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{20,}', token):
            raise ValueError('Укажи настоящий TELEGRAM_BOT_TOKEN в .env рядом с telegram_bot.py.')
        ids = os.getenv('ALLOWED_USER_IDS', '').strip()
        try:
            allowed = frozenset(int(x.strip()) for x in ids.split(',') if x.strip())
        except ValueError:
            raise ValueError('ALLOWED_USER_IDS: только числовые Telegram ID через запятую.') from None
        def number(name, default, ceiling):
            value = int(os.getenv(name, str(default)))
            if not 1 <= value <= ceiling:
                raise ValueError(f'{name}: допустимо от 1 до {ceiling}.')
            return value
        models = [os.getenv('GEMINI_TEXT_MODEL', 'gemini-3.8-flash'),
                  os.getenv('GEMINI_TTS_MODEL', 'gemini-3.8-flash-tts')]
        if not all(re.fullmatch(r'[a-zA-Z0-9._-]+', m) for m in models):
            raise ValueError('Недопустимое имя модели Gemini.')
        return cls(token, os.getenv('GEMINI_API_KEY', '').strip(), allowed,
                   (ROOT / os.getenv('DATA_DIR', 'data')).resolve(), *models,
                   number('MAX_DAILY_REQUESTS_PER_USER', 25, 10000),
                   number('MAX_DAILY_REQUESTS_TOTAL', 100, 100000),
                   number('MAX_VOICES_PER_USER', 3, 100),
                   number('MAX_TEXT_LENGTH', 3000, 3500),
                   number('MAX_AUDIO_SECONDS', 180, 600),
                   number('MAX_UPLOAD_MB', 15, 19), os.getenv('FFMPEG_EXE', '').strip())
