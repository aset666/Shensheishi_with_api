import base64
import io
import json
import wave
import httpx
import pytest
from app.gemini import Gemini, GeminiError, audio_from_response


def wav_bytes():
    result = io.BytesIO()
    with wave.open(result, 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(24000)
        f.writeframes(b'\0\0' * 2400)
    return result.getvalue()


async def test_speech_uses_current_rest_shape_and_never_url_key():
    wav = wav_bytes()
    def handler(request):
        assert request.url.path == '/v1beta/interactions'
        assert 'key' not in request.url.params
        assert request.headers['x-goog-api-key'] == 'test-key'
        body = json.loads(request.content)
        assert body['response_format'] == {'type': 'audio'}
        assert body['store'] is False
        assert body['generation_config']['speech_config'] == [{'voice': 'voice_example'}]
        assert body['input'][0]['content'][0]['text'] == 'Сәлем'
        return httpx.Response(200, json={'steps': [{'type': 'model_output', 'content': [
            {'type': 'audio', 'mime_type': 'audio/wav', 'data': base64.b64encode(wav).decode()}]}]})
    client = Gemini('test-key', 'text-model', 'tts-model', httpx.MockTransport(handler))
    assert await client.speech('Сәлем', 'voice_example', 'kk') == wav
    await client.close()


@pytest.mark.parametrize('status', [400, 401, 403, 404, 429, 500])
async def test_api_errors_are_actionable_and_do_not_expose_response(status):
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(status, json={'error': {'message': 'SECRET-provider-payload'}})
    client = Gemini('test-key', 'text', 'tts', httpx.MockTransport(handler))
    with pytest.raises(GeminiError) as info:
        await client.clone('name', wav_bytes(), wav_bytes())
    assert str(status) in str(info.value)
    assert 'SECRET' not in str(info.value)
    assert count == 1  # Creating a voice must not automatically retry and create duplicates.
    await client.close()


async def test_clone_requires_both_recordings_and_returns_persistent_id():
    def handler(request):
        body = json.loads(request.content)
        assert body['store'] is True
        assert body['voice']['type'] == 'replicated'
        assert set(body['voice']['replicated']) == {'source_audio', 'consent_audio'}
        return httpx.Response(200, json={'id': 'voice_example', 'type': 'replicated'})
    client = Gemini('test-key', 'text', 'tts', httpx.MockTransport(handler))
    assert (await client.clone('name', wav_bytes(), wav_bytes()))['id'] == 'voice_example'
    await client.close()


async def test_text_does_not_return_internal_thoughts():
    def handler(request):
        body = json.loads(request.content)
        assert body['contents'][-1]['parts'][0]['text'] == 'hello'
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
            {'thought': True, 'text': 'private reasoning'}, {'text': 'answer'}]}}]})
    client = Gemini('key', 'text', 'tts', httpx.MockTransport(handler))
    assert await client.text('hello', system='system') == 'answer'
    await client.close()


async def test_timeout_does_not_repeat_paid_post():
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout('timeout')
    client = Gemini('key', 'text', 'tts', httpx.MockTransport(handler))
    with pytest.raises(GeminiError, match='вовремя'):
        await client.clone('name', wav_bytes(), wav_bytes())
    assert calls == 1
    await client.close()


def test_input_audio_never_mistaken_for_generated_output():
    with pytest.raises(GeminiError):
        audio_from_response({'steps': [{'type': 'user_input', 'content': [
            {'type': 'audio', 'data': base64.b64encode(wav_bytes()).decode()}]}]})
