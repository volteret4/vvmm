#!/usr/bin/env python3
"""Elige el reproductor de cada disco del blog y lo guarda en data/reproductor.json.

Orden de preferencia:
  1. Bandcamp: id numérico del álbum comprobado por bandcamp_verificar.py
     (data/bandcamp_verificados.json, solo los ok).
  2. Playlist de YouTube enlazada en el propio post.
  3. Búsqueda en YouTube con yt-dlp (sin API): playlist del álbum y,
     si no hay, un vídeo de álbum completo (> 20 min). Solo se acepta si el título
     (o el canal) contiene artista y álbum.

Lo ya resuelto, también los «no encontrado», queda en el JSON y no se vuelve a buscar
(--rebuscar para repetir los no encontrados). Solo biblioteca estándar y el comando yt-dlp.

Uso:  python3 -I scripts/reproductor.py [raíz del blog] [--rebuscar] [--max N] [--out fichero.json]
"""
import json
import re
import sys
import subprocess
import urllib.parse
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enlaces import SECTIONS, artist_album, norm  # noqa: E402

PLAYLIST = re.compile(r'youtube\.com/playlist\?list=([\w-]+)')


def ytdlp(url):
    """Resultados de una búsqueda de YouTube con yt-dlp (sin API ni cuota)."""
    r = subprocess.run(['yt-dlp', '--flat-playlist', '--playlist-end', '10', '-J', '--no-warnings', url],
                       capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise RuntimeError(r.stderr.strip()[-300:])
    return json.loads(r.stdout).get('entries') or []


def matches(item, artist, album):
    title, channel = norm(item.get('title')), norm(item.get('channel') or item.get('uploader'))
    a, d = norm(artist), norm(album)
    a = a[4:] if a.startswith('the ') else a
    has = lambda needle, hay: f' {needle} ' in f' {hay} '  # palabras completas: «I» no casa con «cigarettes»
    return bool(d) and has(d, title) and (has(a, title) or has(a, channel))


def search(artist, album):
    q = urllib.parse.quote_plus(f'{artist} {album} album')
    for it in ytdlp(f'https://www.youtube.com/results?search_query={q}&sp=EgIQAw%3D%3D'):  # solo playlists
        if it.get('id', '').startswith(('PL', 'OLAK')) and matches(it, artist, album):
            return {'tipo': 'yt_playlist', 'id': it['id'], 'fuente': 'busqueda'}
    for it in ytdlp(f'ytsearch10:{artist} {album} full album'):
        if (it.get('duration') or 0) >= 1200 and matches(it, artist, album):
            return {'tipo': 'yt_video', 'id': it['id'], 'fuente': 'busqueda'}
    return None


def main():
    argv = sys.argv[1:]
    opt = lambda name, default: argv[argv.index(name) + 1] if name in argv else default
    args = [a for i, a in enumerate(argv) if not a.startswith('--') and (i == 0 or argv[i - 1] not in ('--max', '--out'))]
    rebuscar = '--rebuscar' in sys.argv
    max_busquedas = int(opt('--max', 80))
    root = Path(args[0] if args else '.')
    out = Path(opt('--out', root / 'data' / 'reproductor.json'))
    cache = json.loads(out.read_text(encoding='utf-8')) if out.exists() else {}
    bc_file = out.parent / 'bandcamp_verificados.json'
    bandcamp = {k: v['id'] for k, v in json.loads(bc_file.read_text(encoding='utf-8')).items() if v.get('ok')} if bc_file.exists() else {}
    busquedas = 0
    nuevo = {}

    for sec in SECTIONS:
        for f in sorted((root / 'content' / sec).glob('*/index.md')):
            post = f'{sec}/{f.parent.name}'
            text = f.read_text(encoding='utf-8', errors='replace')
            artist, album = artist_album(text)
            artist = artist.strip(' -')
            prev = cache.get(post)

            if bandcamp.get(post):
                nuevo[post] = {'tipo': 'bandcamp', 'id': int(bandcamp[post])}
                continue
            if prev and prev.get('tipo') == 'bandcamp':  # ya no está verificado: se vuelve a decidir
                prev = None
            m = PLAYLIST.search(text)
            if m:
                nuevo[post] = {'tipo': 'yt_playlist', 'id': m.group(1), 'fuente': 'post'}
                continue
            if prev and prev.get('fuente') == 'busqueda' and (prev.get('id') or not rebuscar):
                nuevo[post] = prev
                continue
            if not (artist and album) or busquedas >= max_busquedas:
                if prev:
                    nuevo[post] = prev
                continue
            busquedas += 1
            try:
                found = search(artist, album)
            except Exception as e:  # noqa: BLE001 — sin red o YouTube bloquea: se deja para la próxima
                print(f'aviso: búsqueda fallida para {post}: {e}', file=sys.stderr)
                break
            nuevo[post] = found or {'tipo': None, 'fuente': 'busqueda', 'buscado': date.today().isoformat()}
            print(f'{post}: {found["tipo"] + " " + found["id"] if found else "no encontrado"}')

    for post, v in cache.items():  # lo no recorrido (búsqueda cortada) se conserva
        if v.get('tipo') != 'bandcamp' or bandcamp.get(post):  # salvo un Bandcamp ya no verificado
            nuevo.setdefault(post, v)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(nuevo, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')
    tipos = {}
    for v in nuevo.values():
        tipos[v.get('tipo')] = tipos.get(v.get('tipo'), 0) + 1
    print(f'{out}: {busquedas} búsquedas; ' + ', '.join(f'{k or "sin reproductor"} {n}' for k, n in sorted(tipos.items(), key=str)))


if __name__ == '__main__':
    main()
