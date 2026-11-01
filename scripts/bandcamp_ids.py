#!/usr/bin/env python3
"""Saca el id numérico de álbum de Bandcamp de cada disco del blog (para su reproductor).

URL del álbum, por orden: la enlazada en el post (…bandcamp.com/album/…), la encontrada
en MusicBrainz por bandcamp_buscar.py (data/bandcamp_urls.json) o la de la base de datos
de music-fuzzy (tabla albums, por artista y título normalizados; a veces es de otro artista).
La página se descarga con yt-dlp y el id sale de su metadato bc-page-properties.

Resultado en data/bandcamp_ids.json: {post: id}; 0 = no se pudo (no se reintenta
salvo con --reintentar). Pausa entre peticiones para no cargar a Bandcamp.

Uso:  python3 -I scripts/bandcamp_ids.py [raíz del blog] [--out f.json] [--max N] [--reintentar] [--db musica.sqlite]
"""
import html
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enlaces import SECTIONS, artist_album, norm  # noqa: E402

DB = '/home/huan/gits/pollo/music-fuzzy/db/sqlite/musica_local.sqlite'
ALBUM_URL = re.compile(r'\((https://[\w.-]+\.bandcamp\.com/album/[\w-]+)[^)]*\)')
PAGE_PROPS = re.compile(r'name="bc-page-properties"\s+content="([^"]+)"')
PAUSA = 3  # segundos entre descargas


def key(artist, album):
    a = norm(artist)
    return (a[4:] if a.startswith('the ') else a), norm(album)


def db_urls(path):
    try:
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        rows = con.execute("SELECT ar.name, a.name, a.bandcamp_url FROM albums a JOIN artists ar ON ar.id = a.artist_id "
                           "WHERE a.bandcamp_url LIKE '%/album/%'").fetchall()
    except sqlite3.Error as e:
        print(f'aviso: sin base de datos de music-fuzzy ({e})', file=sys.stderr)
        return {}
    return {key(ar, al): url for ar, al, url in rows}


def album_id(url):
    """Descarga la página con yt-dlp y devuelve el id del álbum, o None."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(['yt-dlp', '--flat-playlist', '--skip-download', '--write-pages', '--no-warnings', '-q', url],
                       cwd=tmp, capture_output=True, text=True, timeout=120)
        for dump in Path(tmp).glob('*.dump'):
            page = dump.read_text(encoding='utf-8', errors='replace')
            m = PAGE_PROPS.search(page)
            if m:
                props = json.loads(html.unescape(m.group(1)))
                if props.get('item_type') == 'a' and props.get('item_id'):
                    return int(props['item_id'])
            m = re.search(r'EmbeddedPlayer/album=(\d+)|album=(\d+)', page)
            if m:
                return int(m.group(1) or m.group(2))
    return None


def main():
    argv = sys.argv[1:]
    opt = lambda name, default: argv[argv.index(name) + 1] if name in argv else default
    pos = [a for i, a in enumerate(argv) if not a.startswith('--') and (i == 0 or argv[i - 1] not in ('--out', '--max', '--db'))]
    root = Path(pos[0] if pos else '.')
    out = Path(opt('--out', root / 'data' / 'bandcamp_ids.json'))
    limit = int(opt('--max', 1000))
    retry = '--reintentar' in argv
    ids = json.loads(out.read_text(encoding='utf-8')) if out.exists() else {}
    from_db = db_urls(opt('--db', DB))
    load = lambda name: json.loads((out.parent / name).read_text(encoding='utf-8')) if (out.parent / name).exists() else {}
    from_mb = {k: v for k, v in load('bandcamp_urls.json').items() if v}
    checked = load('bandcamp_verificados.json')
    src_file = out.parent / 'bandcamp_fuentes.json'
    sources = load('bandcamp_fuentes.json')  # {post: url de la que salió el id}

    hechos = encontrados = 0
    for sec in SECTIONS:
        for f in sorted((root / 'content' / sec).glob('*/index.md')):
            post = f'{sec}/{f.parent.name}'
            # un id que no pasó la verificación se repite si MusicBrainz da una URL aún no probada
            bad = post in checked and not checked[post].get('ok') and checked[post].get('id') == ids.get(post)
            new_mb = post in from_mb and sources.get(post) != from_mb[post]
            if post in ids and (ids[post] or not retry) and not (bad and new_mb):
                continue
            text = f.read_text(encoding='utf-8', errors='replace')
            artist, album = artist_album(text)
            m = ALBUM_URL.search(text)
            url = m.group(1) if m else from_mb.get(post) or from_db.get(key(artist.strip(' -'), album))
            if post in ids and sources.get(post) == url and not (retry and not ids[post]):  # esa URL ya se probó
                continue
            if not url:
                continue
            if hechos >= limit:
                break
            if hechos:
                time.sleep(PAUSA)
            hechos += 1
            try:
                i = album_id(url)
            except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
                print(f'{post}: error {e}', file=sys.stderr)
                i = None
            ids[post] = i or 0
            sources[post] = url
            encontrados += bool(i)
            print(f'{post}: {i or "sin id"}  ({url})', flush=True)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(ids, indent=1, sort_keys=True), encoding='utf-8')
            src_file.write_text(json.dumps(sources, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')

    print(f'{out}: {hechos} consultadas, {encontrados} con id; total con id {sum(1 for v in ids.values() if v)}')


if __name__ == '__main__':
    main()
