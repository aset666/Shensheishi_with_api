"""Public recording catalog and Wikimedia Commons metadata/download client."""
import asyncio
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit, urlunsplit

import httpx

from .audio import Audio

API = 'https://commons.wikimedia.org/w/api.php'
MAX_SOURCE_BYTES = 20 * 1024 * 1024


class SourceError(Exception):
    pass


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain(value):
    parser = PlainText()
    parser.feed(str(value))
    return ' '.join(''.join(parser.parts).split())


def trusted_url(url, host):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname != host or parts.username or parts.password or parts.port:
        raise SourceError('Источник вернул неожиданный адрес файла.')
    return urlunsplit(parts._replace(query='', fragment=''))


def parse_metadata(seed, page):
    info = (page.get('imageinfo') or [{}])[0]
    meta = info.get('extmetadata', {})
    field = lambda key: plain(meta.get(key, {}).get('value', ''))
    if page.get('missing') or not info:
        raise SourceError(f'Запись не найдена: {seed["name"]}')
    if not info.get('mime', '').startswith('audio/') and info.get('mime') != 'application/ogg':
        raise SourceError(f'Нужен аудиофайл: {seed["name"]}')
    if not 0 < info.get('size', 0) <= MAX_SOURCE_BYTES or not 0 < info.get('duration', 0) <= 120:
        raise SourceError(f'Размер или длительность записи не подходят: {seed["name"]}')
    if not field('LicenseShortName') or not field('LicenseUrl') or not field('Artist'):
        raise SourceError(f'Нет полных сведений об авторе и лицензии: {seed["name"]}')
    sha1 = info.get('sha1', '')
    if not re.fullmatch(r'[0-9a-f]{40}', sha1):
        raise SourceError('Источник не вернул контрольную сумму аудио.')
    return dict(seed, page_id=page['pageid'],
        source_url=trusted_url(info['descriptionurl'], 'commons.wikimedia.org'),
        audio_url=trusted_url(info['url'], 'upload.wikimedia.org'),
        author=field('Artist'), credit=field('Credit'), description=field('ImageDescription'),
        license=field('LicenseShortName'), license_url=field('LicenseUrl'),
        attribution_required=field('AttributionRequired') == 'true',
        source_sha1=sha1, source_bytes=info['size'], duration=round(info['duration'], 2),
        mime_type=info['mime'], source_language=seed.get('language', 'en'),
        status='source_recording', clone_available=False,
        retrieved_at=datetime.now(timezone.utc).isoformat())


class CommonsParser:
    def __init__(self, client, audio=None):
        self.client = client
        self.audio = audio

    async def metadata(self, seeds):
        rows = []
        for offset in range(0, len(seeds), 5):
            batch = seeds[offset:offset + 5]
            response = await self.client.get(API, params={
                'action': 'query', 'format': 'json', 'formatversion': 2,
                'prop': 'imageinfo', 'iiprop': 'url|mime|size|sha1|extmetadata',
                'titles': '|'.join(item['file_title'] for item in batch)})
            response.raise_for_status()
            result = response.json()
            if 'error' in result:
                raise SourceError('Wikimedia отклонила запрос метаданных.')
            pages = {p['title']: p for p in result.get('query', {}).get('pages', [])}
            for seed in batch:
                if seed['file_title'] not in pages:
                    raise SourceError('Не найдено точное имя файла: ' + seed['file_title'])
                rows.append(parse_metadata(seed, pages[seed['file_title']]))
            await asyncio.sleep(0.2)
        return rows

    async def download(self, row, folder):
        folder = Path(folder)
        samples = folder / 'samples'
        samples.mkdir(parents=True, exist_ok=True)
        target = samples / (row['id'] + '_' + row['source_sha1'][:12] + '.mp3')
        checksum_file = target.with_suffix('.sha256')
        if self.use_cached(row, folder):
            return
        with tempfile.TemporaryDirectory(prefix='commons_') as tmp:
            raw = Path(tmp) / 'source.audio'
            size, digest = 0, hashlib.sha1()
            async with self.client.stream('GET', trusted_url(row['audio_url'], 'upload.wikimedia.org')) as response:
                response.raise_for_status()
                with raw.open('wb') as output:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_SOURCE_BYTES:
                            raise SourceError('Аудиофайл превысил лимит 20 МБ.')
                        digest.update(chunk)
                        output.write(chunk)
            if size != row['source_bytes'] or digest.hexdigest() != row['source_sha1']:
                raise SourceError('Запись изменилась или скачалась не полностью. Запусти парсер заново.')
            converted = Path(tmp) / 'preview.mp3'
            audio = self.audio or Audio()
            await audio.mp3(raw, converted)
            temporary = target.with_suffix('.tmp')
            temporary.write_bytes(converted.read_bytes())
            temporary.replace(target)
            checksum_file.write_text(hashlib.sha256(target.read_bytes()).hexdigest() + '\n')
        self.preview_metadata(row, target, folder)

    @classmethod
    def use_cached(cls, row, folder):
        folder = Path(folder)
        target = folder / 'samples' / (row['id'] + '_' + row['source_sha1'][:12] + '.mp3')
        checksum_file = target.with_suffix('.sha256')
        if target.is_file() and checksum_file.is_file():
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual == checksum_file.read_text().strip():
                cls.preview_metadata(row, target, folder)
                return True
        return False

    @staticmethod
    def preview_metadata(row, target, folder):
        row['preview_file'] = target.relative_to(folder).as_posix()
        row['preview_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
        row['modifications'] = 'Полная запись перекодирована в MP3, mono 24 кГц, 96 кбит/с; без обрезки.'


class Recordings:
    def __init__(self, root):
        self.root = Path(root)
        path = self.root / 'people.json'
        self.rows = json.loads(path.read_text(encoding='utf-8'))['recordings'] if path.is_file() else []
        self.by_id = {row['id']: row for row in self.rows}

    def preview(self, row):
        relative = row.get('preview_file')
        if not relative:
            return None
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root.resolve()) or target.suffix != '.mp3':
            raise SourceError('Некорректный путь записи в каталоге.')
        return target if target.is_file() else None


def write_catalog(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'schema_version': 1,
        'purpose': 'Открытые исходные записи для прослушивания и анализа. Голосовые клоны не созданы.',
        'recordings': rows}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
