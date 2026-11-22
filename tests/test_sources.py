import hashlib
import json

import httpx
import pytest

from app.sources import CommonsParser, SourceError, parse_metadata, Recordings, trusted_url

SEED = {'id': 'speaker', 'name': 'Speaker', 'file_title': 'File:Example.flac', 'language': 'en'}
RAW = b'original recording'


def page():
    return {'pageid': 12, 'title': SEED['file_title'], 'imageinfo': [{
        'mime': 'audio/x-flac', 'size': len(RAW), 'duration': 20,
        'url': 'https://upload.wikimedia.org/wikipedia/commons/a/ab/Example.flac',
        'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Example.flac',
        'sha1': hashlib.sha1(RAW).hexdigest(), 'extmetadata': {
            'Artist': {'value': '<a href="/wiki/Author">Author &amp; Co</a>'},
            'LicenseShortName': {'value': 'CC BY 3.0'},
            'LicenseUrl': {'value': 'https://creativecommons.org/licenses/by/3.0'},
            'AttributionRequired': {'value': 'true'}}}]}


def test_source_metadata_keeps_attribution_without_claiming_clone():
    row = parse_metadata(SEED, page())
    assert row['author'] == 'Author & Co'
    assert row['source_language'] == 'en' and row['attribution_required']
    assert not row['clone_available'] and row['status'] == 'source_recording'


def test_missing_license_and_missing_file_are_rejected():
    data = page()
    del data['imageinfo'][0]['extmetadata']['LicenseUrl']
    with pytest.raises(SourceError):
        parse_metadata(SEED, data)
    with pytest.raises(SourceError):
        parse_metadata(SEED, {'missing': True})


def test_catalog_paths_and_download_hosts_are_restricted(tmp_path):
    (tmp_path / 'people.json').write_text(json.dumps({'recordings': []}))
    recordings = Recordings(tmp_path)
    with pytest.raises(SourceError):
        recordings.preview({'preview_file': '../private.mp3'})
    with pytest.raises(SourceError):
        trusted_url('https://upload.wikimedia.org.evil.test/a', 'upload.wikimedia.org')


async def test_download_checksum_mismatch_leaves_existing_preview(tmp_path):
    row = parse_metadata(SEED, page())
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b'wrong bytes'))) as client:
        with pytest.raises(SourceError):
            await CommonsParser(client).download(row, tmp_path)
    assert not list((tmp_path / 'samples').glob('*.mp3'))


async def test_metadata_uses_api_and_download_records_transformation(tmp_path):
    class Audio:
        async def mp3(self, source, destination):
            assert source.read_bytes() == RAW
            destination.write_bytes(b'mp3 recording')
    def respond(request):
        if request.url.host == 'commons.wikimedia.org':
            assert request.url.params['prop'] == 'imageinfo'
            return httpx.Response(200, json={'query': {'pages': [page()]}})
        return httpx.Response(200, content=RAW)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        parser = CommonsParser(client, audio=Audio())
        rows = await parser.metadata([SEED])
        await parser.download(rows[0], tmp_path)
    assert (tmp_path / rows[0]['preview_file']).read_bytes() == b'mp3 recording'
    assert rows[0]['modifications'] and rows[0]['preview_sha256']
    # A second run validates and uses the existing file without an HTTP request.
    async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: pytest.fail('Unnecessary second download'))) as client:
        await CommonsParser(client).download(rows[0], tmp_path)


async def test_long_retry_after_is_respected_without_immediate_retry(tmp_path):
    from parse_voices import download_with_pause
    calls = []
    class Commons:
        async def download(self, row, folder):
            calls.append(1)
            response = httpx.Response(429, headers={'Retry-After': '600'},
                request=httpx.Request('GET', 'https://upload.wikimedia.org/example'))
            response.raise_for_status()
    with pytest.raises(SourceError, match='600'):
        await download_with_pause(Commons(), {}, tmp_path)
    assert len(calls) == 1
