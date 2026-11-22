"""Run: python parse_voices.py --download (no Telegram/Google keys needed)."""
import argparse
import asyncio
import json
from pathlib import Path
import re
import sys
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

import httpx

from app.audio import AudioError
from app.sources import CommonsParser, SourceError, write_catalog

ROOT = Path(__file__).resolve().parent


async def download_with_pause(commons, row, output):
    for attempt in range(3):
        try:
            return await commons.download(row, output)
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 429 or attempt == 2:
                raise
            retry_after = error.response.headers.get('Retry-After', '')
            delay = 15 * (attempt + 1)
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    delay = max(delay, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds())
            if delay > 60:
                raise SourceError(f'Wikimedia просит подождать {round(delay)} секунд. Запусти парсер позже.') from None
            print(f'Ограничение Wikimedia: пауза {round(delay)} секунд. Скачанные файлы сохранены.', flush=True)
            await asyncio.sleep(delay)


async def main():
    parser = argparse.ArgumentParser(description='Парсинг открытых записей Wikimedia Commons.')
    parser.add_argument('--download', action='store_true', help='Скачать записи и сделать MP3 для Telegram.')
    parser.add_argument('--seed', type=Path, default=ROOT / 'catalog' / 'people_seed.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'catalog')
    args = parser.parse_args()
    seeds = json.loads(args.seed.read_text(encoding='utf-8'))
    if not 1 <= len(seeds) <= 100:
        raise SourceError('В списке должно быть от 1 до 100 записей.')
    if len({s['id'] for s in seeds}) != len(seeds):
        raise SourceError('Идентификаторы записей должны быть уникальными.')
    for seed in seeds:
        if not re.fullmatch(r'[a-z0-9_]{1,45}', seed['id']) or not seed['file_title'].startswith('File:'):
            raise SourceError('Проверь id и file_title в исходном списке.')
    async with httpx.AsyncClient(timeout=60, follow_redirects=False,
            headers={'User-Agent': 'Shensheishi-Educational-Voice-Catalog/1.1 (Python httpx; Wikimedia Commons samples)'}) as client:
        commons = CommonsParser(client)
        rows = await commons.metadata(seeds)
        for index, row in enumerate(rows, 1):
            if args.download:
                await download_with_pause(commons, row, args.output)
                await asyncio.sleep(1)
            else:
                commons.use_cached(row, args.output)
            print(f'{index}/{len(rows)}: {row["name"]} — {row["license"]}', flush=True)
        # Keep the previous catalog if any request or download fails before this point.
        write_catalog(args.output / 'people.json', rows)
        print(f'Готово: {len(rows)} исходных записей. Каталог: {args.output / "people.json"}')
        print(f'Локальное прослушивание: {sum(bool(r.get("preview_file")) for r in rows)}/{len(rows)}. '
              'Остальные доступны по ссылке на источник.')
        print('Клоны не создавались. Парсинг не заменяет процедуру создания голосового профиля.')


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (SourceError, AudioError, httpx.HTTPError, OSError, ValueError, KeyError) as error:
        print(f'Парсинг не завершён ({type(error).__name__}): {error}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('Парсинг остановлен.')
