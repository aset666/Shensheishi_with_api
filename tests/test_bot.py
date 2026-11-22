from datetime import datetime, timezone
from pathlib import Path
import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Update, Message, Chat, User, Voice, CallbackQuery
from app.bot import VoiceBot
from app.config import Config
from app.gemini import GeminiError


TOKEN = '123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi'


class TelegramMock(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b''

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if method.__api_method__ == 'answerCallbackQuery':
            return True
        return Message(message_id=len(self.sent), date=datetime.now(timezone.utc),
            chat=Chat(id=method.chat_id, type='private'),
            from_user=User(id=123456789, is_bot=True, first_name='Bot'),
            text=getattr(method, 'text', None),
            voice=Voice(file_id='generated-audio', file_unique_id='generated-unique', duration=1)
                  if method.__api_method__ == 'sendVoice' else None)


class GoogleMock:
    def __init__(self):
        self.speeches = []
        self.clones = []
        self.texts = []
        self.fail_clone = False
        self.fail_delete = False
        self.fail_speech = False

    async def text(self, prompt, **kwargs):
        self.texts.append((prompt, kwargs))
        return 'Hello world'

    async def speech(self, text, voice, language):
        self.speeches.append((text, voice, language))
        if self.fail_speech:
            raise GeminiError('Тестовая ошибка синтеза')
        return b'mocked-wav'

    async def clone(self, name, reference, consent):
        self.clones.append((name, reference, consent))
        if self.fail_clone:
            raise GeminiError('Тестовая ошибка согласия')
        return {'id': 'voice_created'}

    async def delete_voice(self, voice):
        if self.fail_delete:
            raise GeminiError('Тестовая ошибка удаления')

    async def close(self):
        pass


class AudioMock:
    async def mp3(self, source, destination):
        destination.write_bytes(b'mocked-mp3')


@pytest.fixture
def rig(tmp_path):
    config = Config(TOKEN, 'fake-key', frozenset({1,2}), tmp_path)
    google = GoogleMock()
    service = VoiceBot(config, gemini=google, audio=AudioMock())
    session = TelegramMock()
    bot = Bot(TOKEN, session=session)
    dispatcher = service.dispatcher()
    async def event(uid=1, text=None, callback=None, voice=False):
        chat = Chat(id=uid, type='private')
        user = User(id=uid, is_bot=False, first_name='Tester')
        message = Message(message_id=1, date=datetime.now(timezone.utc), chat=chat,
                          from_user=user, text=text,
                          voice=Voice(file_id='input', file_unique_id='input', duration=12) if voice else None)
        if callback:
            update = Update(update_id=1, callback_query=CallbackQuery(id='q', from_user=user,
                            chat_instance='test', message=message, data=callback))
        else:
            update = Update(update_id=1, message=message)
        await dispatcher.feed_update(bot, update)
    yield service, google, session, event
    service.store.close()


async def test_translation_requires_confirmation_and_stale_buttons_do_not_repeat(rig):
    service, google, telegram, event = rig
    await event(text='🌍 Перевести и озвучить')
    await event(callback='mode_language:en')
    await event(text='Привет мир')
    assert len(google.texts) == 1
    assert not google.speeches
    state, data = service.store.session(1)
    assert state == 'translation_review'
    action = 'translate:' + data['nonce']
    await event(callback=action)
    assert google.speeches == [('Hello world', 'Kore', 'en')]
    await event(callback=action)
    assert len(google.speeches) == 1


async def test_other_user_cannot_select_preview_or_delete_voice(rig):
    service, google, telegram, event = rig
    vid = service.store.add_voice(1, 'voice_private', 'Private', 'ru')
    for action in ('select:', 'preview:', 'confirm_delete:'):
        await event(uid=2, callback=action + f'v:{vid}')
    assert service.store.voice(1, vid)
    assert not google.speeches
    assert service.store.user(2)['selected_voice'] == 'b:Kore'


async def test_unapproved_user_cannot_reach_google(rig):
    service, google, telegram, event = rig
    await event(uid=999, text='💬 Поговорить с ИИ')
    await event(uid=999, voice=True)
    await event(uid=999, callback='preview:b:Kore')
    assert not google.texts and not google.speeches and not google.clones
    assert service.store.session(999) == ('', {})


async def complete_clone_steps(service, event):
    async def download(message, destination, max_seconds):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b'reference' if destination.name == 'reference.wav' else b'consent')
        return 12
    service.download_audio = download
    await event(text='🎙 Добавить голос')
    await event(callback='permission:' + service.store.session(1)[1]['nonce'])
    await event(text='My voice')
    await event(callback='sample_language:kk')
    await event(voice=True)
    assert service.store.session(1)[0] == 'clone_consent'
    assert service.store.voices(1) == []
    await event(voice=True)
    assert service.store.session(1)[0] == 'clone_review'


async def test_clone_requires_two_recordings_and_provider_success(rig):
    service, google, telegram, event = rig
    await complete_clone_steps(service, event)
    assert not google.clones
    await event(callback='clone:' + service.store.session(1)[1]['nonce'])
    assert google.clones == [('My voice', b'reference', b'consent')]
    assert service.store.voices(1)[0]['sample_language'] == 'kk'
    assert service.store.user(1)['selected_voice'].startswith('v:')
    assert not (service.pending / '1').exists()
    assert service.store.session(1) == ('', {})


async def test_clone_failure_does_not_claim_success_or_erase_retry_inputs(rig):
    service, google, telegram, event = rig
    await complete_clone_steps(service, event)
    google.fail_clone = True
    await event(callback='clone:' + service.store.session(1)[1]['nonce'])
    assert not service.store.voices(1)
    assert (service.pending / '1' / 'reference.wav').exists()
    assert service.store.session(1)[0] == 'clone_review'
    await event(text='/cancel')
    assert not (service.pending / '1').exists()


async def test_delete_failure_keeps_local_voice_for_retry(rig):
    service, google, telegram, event = rig
    vid = service.store.add_voice(1, 'voice_private', 'Private', 'ru')
    google.fail_delete = True
    await event(callback=f'confirm_delete:v:{vid}')
    assert service.store.voice(1, vid) is not None
    google.fail_delete = False
    await event(callback=f'confirm_delete:v:{vid}')
    assert service.store.voice(1, vid) is None


async def test_failed_preview_can_retry_without_corrupting_next_mode(rig):
    service, google, telegram, event = rig
    google.fail_speech = True
    await event(callback='preview:b:Kore')
    state, data = service.store.session(1)
    assert state == 'preview_retry'
    google.fail_speech = False
    await event(callback='retry:' + data['speech_retry']['nonce'])
    assert service.store.session(1) == ('', {})
    await event(text='🔊 Озвучить текст')
    await event(callback='mode_language:kk')
    await event(text='Сәлем')
    assert google.speeches[-1] == ('Сәлем', 'Kore', 'kk')


async def prepare_multilingual(service, event, choice='all'):
    await event(text='🌐 Текст на 3 языках')
    await event(text='Сегодня я расскажу о моём проекте.')
    data = service.store.session(1)[1]
    await event(callback='multi_choose:' + data['nonce'] + ':' + choice)
    state, data = service.store.session(1)
    assert state == 'multi_review'
    return 'multi_speak:' + data['nonce']


async def test_three_languages_review_fixed_saved_voice_and_stale_buttons(rig):
    service, google, telegram, event = rig
    vid = service.store.add_voice(1, 'voice_kazakh_sample', 'My voice', 'kk')
    service.store.select(1, f'v:{vid}')
    action = await prepare_multilingual(service, event)
    assert len(google.texts) == 3 and not google.speeches
    await event(callback='select:b:Puck')
    await event(callback=action)
    assert [(voice, lang) for _, voice, lang in google.speeches] == [
        ('voice_kazakh_sample', 'kk'), ('voice_kazakh_sample', 'ru'), ('voice_kazakh_sample', 'en')]
    assert len([m for m in telegram.sent if m.__api_method__ == 'sendVoice']) == 3
    assert service.store.session(1)[0] == 'multi_text'
    assert not (service.pending / '1').exists()
    await event(callback=action)
    assert len(google.speeches) == 3


@pytest.mark.parametrize('language', ['kk', 'ru', 'en'])
async def test_single_language_option(rig, language):
    service, google, telegram, event = rig
    action = await prepare_multilingual(service, event, language)
    await event(callback=action)
    assert len(google.texts) == 1
    assert google.speeches == [('Hello world', 'Kore', language)]


async def test_multilingual_partial_speech_failure_resumes_remaining(rig):
    service, google, telegram, event = rig
    original = google.speech
    failed = False
    async def fail_once(text, voice, language):
        nonlocal failed
        if language == 'ru' and not failed:
            failed = True
            raise GeminiError('Временная ошибка')
        return await original(text, voice, language)
    google.speech = fail_once
    action = await prepare_multilingual(service, event)
    await event(callback=action)
    state, data = service.store.session(1)
    assert state == 'multi_render' and data['sent'] == ['kk']
    await event(text='/continue')
    assert [x[2] for x in google.speeches] == ['kk', 'ru', 'en']
    assert len(google.texts) == 3
    assert service.store.session(1)[0] == 'multi_text'


async def test_multilingual_translation_failure_preserves_completed_text(rig):
    service, google, telegram, event = rig
    original = google.text
    failed = False
    async def fail_once(text, **kwargs):
        nonlocal failed
        if 'русском' in kwargs['system'] and not failed:
            failed = True
            raise GeminiError('Временная ошибка')
        return await original(text, **kwargs)
    google.text = fail_once
    await event(text='🌐 Текст на 3 языках')
    await event(text='Мой проект')
    data = service.store.session(1)[1]
    await event(callback='multi_choose:' + data['nonce'] + ':all')
    state, data = service.store.session(1)
    assert state == 'multi_prepare' and list(data['translations']) == ['kk']
    await event(text='/continue')
    assert len(google.texts) == 3 and not google.speeches
    assert service.store.session(1)[0] == 'multi_review'


async def test_failed_telegram_upload_reuses_generated_file(rig):
    from aiogram.exceptions import TelegramNetworkError
    service, google, telegram, event = rig
    original = telegram.make_request
    failed = False
    async def fail_once(bot, method, timeout=None):
        nonlocal failed
        if method.__api_method__ == 'sendVoice' and not failed:
            failed = True
            raise TelegramNetworkError(method=method, message='Disconnected')
        return await original(bot, method, timeout)
    telegram.make_request = fail_once
    action = await prepare_multilingual(service, event)
    await event(callback=action)
    state, data = service.store.session(1)
    assert state == 'multi_render' and data['ready']['kk'] and not data['sent']
    # Model a restart: flow reads the persisted database and MP3 on /continue.
    from app.storage import Store
    service.store.close()
    service.store = Store(service.config.data_dir / 'shensheishi.sqlite3')
    await event(text='/continue')
    assert [x[2] for x in google.speeches] == ['kk', 'ru', 'en']


async def test_multilingual_cancel_and_unauthorized_callback(rig):
    service, google, telegram, event = rig
    action = await prepare_multilingual(service, event)
    await event(uid=2, callback=action)
    assert not google.speeches
    await event(text='/cancel')
    await event(callback=action)
    assert not google.speeches


async def test_recording_preview_never_becomes_synthesis_voice(rig, tmp_path):
    import json
    from app.sources import Recordings
    service, google, telegram, event = rig
    (tmp_path / 'original.mp3').write_bytes(b'mock original')
    row = dict(id='actor', name='Actor', source_language='en', duration=20, author='Author',
               license='CC BY 3.0', source_url='https://commons.wikimedia.org/wiki/File:Example.flac',
               preview_file='original.mp3')
    (tmp_path / 'people.json').write_text(json.dumps({'recordings': [row]}))
    service.recordings = Recordings(tmp_path)
    await event(text='🎧 Известные люди: записи')
    await event(callback='source:actor')
    await event(callback='source_play:actor')
    await event(callback='select:source:actor')
    assert any(m.__api_method__ == 'sendAudio' for m in telegram.sent)
    assert service.store.user(1)['selected_voice'] == 'b:Kore'
    assert not google.speeches and not google.clones and not google.texts
