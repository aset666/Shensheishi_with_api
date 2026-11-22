import math
import struct
import wave
import pytest
from app.audio import Audio, AudioError


def make_wav(path, seconds=1, rate=16000):
    samples = [int(12000 * math.sin(2 * math.pi * 220 * i / rate)) for i in range(int(seconds * rate))]
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(struct.pack('<' + 'h' * len(samples), *samples))


async def test_real_ffmpeg_resampling_and_mp3(tmp_path):
    source, normalized, mp3 = tmp_path/'input.wav', tmp_path/'out.wav', tmp_path/'out.mp3'
    make_wav(source)
    audio = Audio()
    duration = await audio.normalize(source, normalized, 5)
    assert 0.95 < duration < 1.05
    with wave.open(str(normalized)) as f:
        assert (f.getframerate(), f.getnchannels(), f.getsampwidth()) == (24000, 1, 2)
    await audio.mp3(normalized, mp3)
    assert mp3.stat().st_size > 500


async def test_overlong_audio_is_rejected_not_silently_truncated(tmp_path):
    source = tmp_path/'long.wav'
    make_wav(source, 3)
    with pytest.raises(AudioError, match='длиннее'):
        await Audio().normalize(source, tmp_path/'out.wav', 1)


async def test_invalid_file_is_rejected(tmp_path):
    source = tmp_path/'bad.mp3'
    source.write_text('This is not audio')
    with pytest.raises(AudioError):
        await Audio().normalize(source, tmp_path/'out.wav', 5)
