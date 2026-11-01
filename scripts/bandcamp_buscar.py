#!/usr/bin/env python3
"""Busca en MusicBrainz la URL de Bandcamp de los discos que no la tienen.

Para cada post sin URL de álbum de Bandcamp (ni en el post ni ya encontrada):
  1. edición de MusicBrainz enlazada en el post → sus enlaces y los de las demás
     ediciones de su release group;
  2. si no hay enlace, búsqueda del release group por artista y título (puntuación ≥ 90
     y nombres que coinciden).
Se quedan solo URLs …bandcamp.com/album/…; luego bandcamp_ids.py saca el id y
bandcamp_verificar.py comprueba que es el disco.

API pública de MusicBrainz: una petición por segundo, con User-Agent identificativo.
Resultado en data/bandcamp_urls.json: {post: url | ""} ("" = buscado sin éxito).

Uso:  python3 -I scripts/bandcamp_buscar.py [raíz del blog] [--out f.json] [--max N] [--reintentar]
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enlaces import SECTIONS, artist_album  # noqa: E402
from bandcamp_ids import ALBUM_URL, db_urls, key, DB  # noqa: E402
from bandcamp_verificar import same  # noqa: E402

WS = 'https://musicbrainz.org/ws/2/'
UA = 'vvmm-blog/1.0 ( https://github.com/volteret4/vvmm )'
MB_RELEASE = re.compile(r'musicbrainz\.org/release/([0-9a-f-]{36})')
BC_ALBUM = re.compile(r'^https?://[\w.-]+\.bandcamp\.com/album/[\w-]+')
_last = [0.0]


def mb(path, **params):
    url = f'{WS}{path}?{urllib.parse.urlencode({**params, "fmt": "json"})}'
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    for intento in range(5):
        wait = 1.1 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 503 or intento == 4:  # 503 = MusicBrainz pide ir más despacio
                raise
            time.sleep(2 ** intento * 2)


def bandcamp_of(entity):
    for rel in entity.get('relations') or []:
        m = BC_ALBUM.match((rel.get('url') or {}).get('resource', ''))
        if m:
            return m.group(0)
    return None


def from_group(rgid):
    data = mb('release', **{'release-group': rgid, 'inc': 'url-rels', 'limit': 50})
    for rel in data.get('releases', []):
        url = bandcamp_of(rel)
        if url:
            return url
    return None


def find(text, artist, album):
    m = MB_RELEASE.search(text)
    if m:
        rel = mb(f'release/{m.group(1)}', inc='url-rels+release-groups')
        url = bandcamp_of(rel)
        if url:
            return url, 'edición del post'
        rg = (rel.get('release-group') or {}).get('id')
        if rg and same(rel.get('title'), album):
            url = from_group(rg)
            if url:
                return url, 'otra edición'
    q = f'artist:"{artist}" AND releasegroup:"{album}"'
    for g in mb('release-group', query=q, limit=5).get('release-groups', []):
        credit = ''.join(c.get('name', '') + c.get('joinphrase', '') for c in g.get('artist-credit', []))
        if g.get('score', 0) >= 90 and same(g.get('title'), album) and same(credit, artist):
            url = from_group(g['id'])
            if url:
                return url, 'búsqueda'
    return None, None


def main():
    argv = sys.argv[1:]
    opt = lambda name, default: argv[argv.index(name) + 1] if name in argv else default
    pos = [a for i, a in enumerate(argv) if not a.startswith('--') and (i == 0 or argv[i - 1] not in ('--out', '--max'))]
    root = Path(pos[0] if pos else '.')
    out = Path(opt('--out', root / 'data' / 'bandcamp_urls.json'))
    limit = int(opt('--max', 1000))
    retry = '--reintentar' in argv
    found = json.loads(out.read_text(encoding='utf-8')) if out.exists() else {}
    from_db = db_urls(DB)
    hechos = 0

    for sec in SECTIONS:
        for f in sorted((root / 'content' / sec).glob('*/index.md')):
            post = f'{sec}/{f.parent.name}'
            if post in found and (found[post] or not retry):
                continue
            text = f.read_text(encoding='utf-8', errors='replace')
            artist, album = artist_album(text)
            artist = artist.strip(' -')
            if not (artist and album) or ALBUM_URL.search(text):
                continue
            if hechos >= limit:
                break
            hechos += 1
            try:
                url, how = find(text, artist, album)
            except Exception as e:  # noqa: BLE001 — sin red o MusicBrainz ocupado: otra vez será
                print(f'{post}: error {e}', file=sys.stderr)
                continue
            found[post] = url or ''
            note = '(también en music-fuzzy, puede ser otra)' if url and from_db.get(key(artist, album)) not in (None, url) else ''
            print(f'{post}: {url + "  [" + how + "]" if url else "sin Bandcamp en MusicBrainz"} {note}', flush=True)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(found, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')

    print(f'{out}: {hechos} buscados; {sum(1 for v in found.values() if v)} con URL de Bandcamp')


if __name__ == '__main__':
    main()
