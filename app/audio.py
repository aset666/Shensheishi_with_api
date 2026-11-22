import asyncio
from pathlib import Path
import wave
import imageio_ffmpeg


class AudioError(Exception):
    pass


class Audio:
    def __init__(self, executable=''):
        self.executable = executable or imageio_ffmpeg.get_ffmpeg_exe()

    async def run(self, *args):
        try:
            process = await asyncio.create_subprocess_exec(
                self.executable, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', *args,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        except OSError:
            raise AudioError('Не удалось запустить FFmpeg. Проверь установку зависимостей или FFMPEG_EXE.') from None
        try:
            _, error = await asyncio.wait_for(process.communicate(), timeout=45)
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise AudioError('Обработка аудио заняла слишком много времени. Отправь более короткую запись.') from None
        if process.returncode:
            # Do not return command lines, file paths or untrusted decoder text to Telegram.
            raise AudioError('Не удалось прочитать аудио. Отправь голосовое Telegram или WAV/MP3/OGG.')

    async def normalize(self, source: Path, destination: Path, max_seconds: int):
        await self.run('-protocol_whitelist', 'file,pipe', '-i', str(source),
                       '-map', '0:a:0', '-vn', '-t', str(max_seconds + 1),
                       '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le', str(destination))
        with wave.open(str(destination), 'rb') as f:
            duration = f.getnframes() / f.getframerate()
        if duration < 0.5:
            raise AudioError('Запись слишком короткая.')
        if duration > max_seconds + 0.05:
            raise AudioError(f'Запись длиннее {max_seconds} секунд. Отправь более короткую.')
        return duration

    async def mp3(self, source: Path, destination: Path):
        await self.run('-protocol_whitelist', 'file,pipe', '-i', str(source),
                       '-vn', '-ac', '1', '-ar', '24000', '-c:a', 'libmp3lame',
                       '-b:a', '96k', str(destination))
