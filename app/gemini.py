"""Gemini Developer API over HTTPS. No credentials in URLs or logs."""
import base64
import io
import re
import wave
from urllib.parse import quote
import httpx


class GeminiError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def audio_from_response(payload):
    blocks = [block for step in payload.get('steps', []) if step.get('type') == 'model_output'
              for block in step.get('content', []) if block.get('type') == 'audio']
    if not blocks:
        raise GeminiError('Google не вернул аудио. Попробуй более короткий текст.')
    # Current API convenience property output_audio also refers to the last audio block.
    block = blocks[-1]
    try:
        data = base64.b64decode(block['data'], validate=True)
    except (KeyError, ValueError):
        raise GeminiError('Google вернул некорректное аудио.') from None
    if not data or len(data) > 35 * 1024 * 1024:
        raise GeminiError('Аудио пустое или слишком большое. Сократи текст.')
    mime = block.get('mime_type', 'audio/wav')
    if data[:4] == b'RIFF' and data[8:12] == b'WAVE':
        return data
    if 'pcm' in mime or 'L16' in mime:
        match = re.search(r'rate=(\d+)', mime)
        rate = int(match[1]) if match else 24000
        if not 8000 <= rate <= 48000 or len(data) % 2:
            raise GeminiError('Неподдерживаемый формат PCM.')
        out = io.BytesIO()
        with wave.open(out, 'wb') as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(rate)
            f.writeframes(data)
        return out.getvalue()
    raise GeminiError('Google вернул неожиданный формат звука. Проверь выбранную TTS-модель.')


class Gemini:
    def __init__(self, key, text_model, tts_model, transport=None):
        self.key = key
        self.text_model = text_model
        self.tts_model = tts_model
        self.client = httpx.AsyncClient(
            base_url='https://generativelanguage.googleapis.com/v1beta/',
            headers={'x-goog-api-key': key},
            timeout=httpx.Timeout(180, connect=20), transport=transport,
            follow_redirects=False)

    async def close(self):
        await self.client.aclose()

    async def request(self, method, endpoint, *, body=None, params=None):
        if not self.key:
            raise GeminiError('В .env ещё не указан GEMINI_API_KEY. Добавь ключ из Google AI Studio и перезапусти бота.')
        try:
            response = await self.client.request(method, endpoint, json=body, params=params)
        except httpx.TimeoutException:
            raise GeminiError('Google не ответил вовремя. Автоматический повтор отключён, чтобы не дублировать платный запрос. Попробуй позже. При создании голоса сначала проверь список голосов в AI Studio.') from None
        except httpx.RequestError:
            raise GeminiError('Нет соединения с Google. Проверь интернет и повтори позже.') from None
        if response.status_code >= 400:
            try:
                detail = str(response.json().get('error', {}).get('message', '')).lower()
            except ValueError:
                detail = ''
            code = response.status_code
            if 'consent' in detail or 'speaker' in detail and 'match' in detail:
                message = 'Google не подтвердил согласие или совпадение голосов. Запиши образец и точную фразу согласия одним и тем же взрослым человеком в тихом месте.'
            elif code in (401, 403):
                message = 'Google отклонил доступ. Проверь API-ключ, ограничения проекта и оплату. Для создания клона нужен Paid Tier.'
            elif code == 429:
                message = 'Лимит Google исчерпан. Подожди или проверь квоты и баланс проекта в AI Studio.'
            elif code == 404:
                message = 'Google не нашёл модель или голос. Проверь доступ к указанной модели. Сохранённый голос мог быть удалён или просрочен.'
            elif code >= 500:
                message = 'Сервис Google временно недоступен. Повтори позже. Если создавался голос, проверь AI Studio перед повтором: запрос мог завершиться на сервере.'
            elif code == 400:
                message = 'Google отклонил запрос. Проверь формат записи, фразу согласия, длину текста и доступность выбранной модели.'
            else:
                message = 'Google не выполнил запрос.'
            raise GeminiError(f'{message} (HTTP {code})', code)
        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            raise GeminiError('Google вернул ответ в неожиданном формате.') from None

    async def text(self, prompt, *, system, history=None, audio=None):
        parts = [{'text': prompt}]
        if audio is not None:
            parts.append({'inlineData': {'mimeType': 'audio/wav',
                                        'data': base64.b64encode(audio).decode('ascii')}})
        contents = [{'role': h['role'], 'parts': [{'text': h['text']}]} for h in (history or [])]
        contents.append({'role': 'user', 'parts': parts})
        result = await self.request('POST', f'models/{self.text_model}:generateContent', body={
            'contents': contents,
            'systemInstruction': {'parts': [{'text': system}]},
            'generationConfig': {'maxOutputTokens': 8192},
        })
        candidates = result.get('candidates') or []
        if not candidates:
            raise GeminiError('Google не сформировал ответ. Попробуй переформулировать запрос.')
        candidate = candidates[0]
        if candidate.get('finishReason') == 'MAX_TOKENS':
            raise GeminiError('Ответ превысил лимит. Сократи запрос или раздели его на части.')
        text = ''.join(p.get('text', '') for p in candidate.get('content', {}).get('parts', [])
                       if not p.get('thought')).strip()
        if not text:
            raise GeminiError('Google вернул пустой ответ или заблокировал запрос.')
        return text

    async def speech(self, text, voice, language):
        languages = {'ru': 'Russian', 'kk': 'Kazakh', 'en': 'English'}
        response = await self.request('POST', 'interactions', body={
            'model': self.tts_model,
            'store': False,
            'input': [{'type': 'user_input', 'content': [{
                'type': 'text', 'text': text,
                'annotations': [{'type': 'speech_metadata',
                                 'style': f'Natural clear {languages[language]} speech. Read the supplied text verbatim; do not translate or add words.'}]}]}],
            'response_format': {'type': 'audio'},
            'generation_config': {'speech_config': [{'voice': voice}]},
        })
        return audio_from_response(response)

    async def clone(self, name, reference, consent):
        def clip(data):
            return {'mime_type': 'audio/wav', 'data': base64.b64encode(data).decode('ascii')}
        result = await self.request('POST', 'voices', body={
            'store': True,
            'voice': {'type': 'replicated', 'display_name': name,
                      'replicated': {'source_audio': clip(reference), 'consent_audio': clip(consent)}}})
        if not re.fullmatch(r'voice_[A-Za-z0-9_-]+', result.get('id', '')):
            raise GeminiError('Google не вернул идентификатор созданного голоса. Проверь список в AI Studio перед повтором.')
        return result

    async def delete_voice(self, remote):
        try:
            await self.request('DELETE', 'voices/' + quote(remote, safe=''))
        except GeminiError as e:
            if e.status != 404:
                raise

    async def check_models(self):
        report = []
        for model in (self.text_model, self.tts_model):
            await self.request('GET', 'models/' + quote(model, safe=''))
            report.append(model)
        return report
