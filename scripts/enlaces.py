#!/usr/bin/env python3
"""Cruza los discos del blog con Encarta y Grapa y escribe data/enlaces.json.

Los ids se calculan igual que en encarta/build.py (blake2b de 48 bits sobre
«tipo|clave normalizada»), y solo se enlaza lo que de verdad está publicado:
la ficha exportada en la versión estática de Encarta o la página en Grapa.

Uso:  python3 -I scripts/enlaces.py [raíz del blog] [salida.json]
Solo biblioteca estándar. Si una de las webs no responde, se omiten sus enlaces.
"""
import hashlib
import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

ENCARTA = 'https://volteret4.github.io/encarta/'
GRAPA = 'https://volteret4.github.io/grapa/'
SECTIONS = ('posts', 'coleccion')


# ── misma normalización que encarta/build.py ──
def norm(s):
    if not s:
        return ''
    s = unicodedata.normalize('NFKD', str(s))
    s = ''.join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace('&', ' and ').replace('’', "'")
    s = re.sub(r"[^\w\s]", ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def strip_the(s):
    s = norm(s)
    return s[4:] if s.startswith('the ') else s


def eid(type_, key):
    return int.from_bytes(hashlib.blake2b(f'{type_}|{key}'.encode(), digest_size=6).digest(), 'big') or 1


def fetch_json(url):
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001 — sin red o sin web: seguimos sin esos enlaces
        print(f'aviso: no se pudo leer {url}: {e}', file=sys.stderr)
        return None


# ── artista y álbum de cada post (misma lógica que layouts/partials/meta.html) ──
FM = re.compile(r'^---\n(.*?)\n---\n', re.S)
COVER = re.compile(r'^!\[cover\]\(image\.jpeg \((.+?) - (.*)\)\)\s*$', re.M)
TITLE = re.compile(r'^(.+?)\s*-\s+(.+)$')


def fm_value(fm, key):
    m = re.search(rf'^{key}:\s*"?(.*?)"?\s*$', fm, re.M)
    return m.group(1) if m else ''


def artist_album(text):
    m = FM.match(text)
    fm = m.group(1) if m else ''
    artist, album = fm_value(fm, 'artist'), fm_value(fm, 'album')
    if album:
        return artist, album
    m = COVER.search(text)
    if m:
        return m.group(1), m.group(2)
    m = TITLE.match(fm_value(fm, 'title'))
    return (m.group(1), m.group(2)) if m else ('', '')


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else '.')
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else root / 'data' / 'enlaces.json'

    index = fetch_json(ENCARTA + 'data/index.json')
    exported = {row[0] for row in index} if index else set()
    grapa = fetch_json(GRAPA + 'data/artistas.json') or {}
    g_artists = {int(k): v for k, v in (grapa.get('artists') or {}).items()}
    g_albums = {int(k): v for k, v in (grapa.get('albums') or {}).items()}

    links, stats = {}, {'posts': 0, 'encarta_artista': 0, 'encarta_disco': 0, 'grapa_artista': 0, 'grapa_disco': 0}
    for sec in SECTIONS:
        for f in sorted((root / 'content' / sec).glob('*/index.md')):
            artist, album = artist_album(f.read_text(encoding='utf-8', errors='replace'))
            artist = artist.strip(' -')
            if not artist:
                continue
            stats['posts'] += 1
            a_id = eid('artist', strip_the(artist))
            key = f'{strip_the(artist)}|{strip_the(album)}'
            d_ids = [eid('album', key), eid('work', key)]
            e = {}
            if a_id in exported:
                e['encarta_artista'] = f'{ENCARTA}#/e/{a_id}'
            d = next((i for i in d_ids if i in exported), None)
            if d:
                e['encarta_disco'] = f'{ENCARTA}#/e/{d}'
            if a_id in g_artists:
                e['grapa_artista'] = f'{GRAPA}artista/{a_id}.html'
                e['grapa_n'] = sum(g_artists[a_id].values())
            d = next((i for i in d_ids if i in g_albums), None)
            if d:
                g_art, n = g_albums[d]
                e['grapa_disco'] = f'{GRAPA}artista/{g_art}.html#d{d}'
                e['grapa_disco_n'] = n
            for k in list(stats)[1:]:
                stats[k] += k in e
            if e:
                links[f'{sec}/{f.parent.name}'] = e

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(links, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')
    print(f'{out}: ' + ', '.join(f'{k} {v}' for k, v in stats.items()))


if __name__ == '__main__':
    main()
