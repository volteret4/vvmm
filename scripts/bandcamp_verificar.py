#!/usr/bin/env python3
"""Comprueba que cada id de data/bandcamp_ids.json es el disco del post.

Carga el reproductor oficial (bandcamp.com/EmbeddedPlayer) en chromium sin interfaz,
lee su JSON (album_title, artist, tracks) y lo da por bueno solo si artista y álbum
coinciden con los del post y hay pistas que se pueden escuchar.
Las URLs de music-fuzzy a veces son de otro artista, y esto las descarta.

Resultado en data/bandcamp_verificados.json: {post: {id, ok, artista, album, pistas}}.
reproductor.py solo usa los que tienen ok = true.

Uso:  python3 -I scripts/bandcamp_verificar.py [raíz del blog] [--dir carpeta de data] [--todo] [--revaluar]
"""
import html
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enlaces import artist_album, norm  # noqa: E402

DATA = re.compile(r'data-player-data="([^"]+)"|data-embed="([^"]+)"')


def embed_info(album_id):
    url = f'https://bandcamp.com/EmbeddedPlayer/album={album_id}/size=large/tracklist=true/'
    r = subprocess.run(['chromium', '--headless=new', '--disable-gpu', '--virtual-time-budget=6000', '--dump-dom', url],
                       capture_output=True, text=True, timeout=90)
    page = html.unescape(r.stdout)
    titles = re.findall(r'"album_title":"((?:[^"\\]|\\.)*)"', page)
    artists = re.findall(r'"artist":"((?:[^"\\]|\\.)*)"', page)
    tracks = len(re.findall(r'"track_streaming":true|"streaming":true', page)) or page.count('class="tracktitle')
    dec = lambda s: json.loads(f'"{s}"')
    return (dec(titles[0]) if titles else None), (dec(artists[0]) if artists else None), tracks


def same(a, b):
    a, b = norm(a), norm(b)
    a, b = re.sub(r'^the ', '', a), re.sub(r'^the ', '', b)
    if not (a and b):
        return False
    # «E L S I A N E» = «Elsiane»; «Struggler» ⊂ «Struggler EP»
    return a == b or a.replace(' ', '') == b.replace(' ', '') or f' {a} ' in f' {b} ' or f' {b} ' in f' {a} '


def is_match(bc_title, bc_artist, tracks, artist, album):
    if not tracks:
        return False
    if same(bc_title, album) and same(bc_artist, artist):
        return True
    # página de un sello: «Heavenly Recordings – Saint Etienne - International»
    return norm(bc_title) == norm(f'{artist} {album}')


def main():
    argv = sys.argv[1:]
    data = Path(argv[argv.index('--dir') + 1]) if '--dir' in argv else None
    pos = [a for i, a in enumerate(argv) if not a.startswith('--') and (i == 0 or argv[i - 1] != '--dir')]
    root = Path(pos[0] if pos else '.')
    data = data or root / 'data'
    ids = json.loads((data / 'bandcamp_ids.json').read_text(encoding='utf-8'))
    out = data / 'bandcamp_verificados.json'
    done = json.loads(out.read_text(encoding='utf-8')) if out.exists() else {}

    if '--revaluar' in argv:  # recalcula ok con lo ya descargado, sin pedir nada
        for post, v in done.items():
            artist, album = artist_album((root / 'content' / post / 'index.md').read_text(encoding='utf-8', errors='replace'))
            v['ok'] = is_match(v['album'], v['artista'], v['pistas'], artist.strip(' -'), album)
        out.write_text(json.dumps(done, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')
        ids = {}
    for post, album_id in sorted(ids.items()):
        if not album_id or (post in done and done[post]['id'] == album_id and '--todo' not in argv):
            continue
        artist, album = artist_album((root / 'content' / post / 'index.md').read_text(encoding='utf-8', errors='replace'))
        try:
            t, a, n = embed_info(album_id)
        except subprocess.TimeoutExpired:
            print(f'{post}: tiempo agotado', file=sys.stderr)
            continue
        ok = is_match(t, a, n, artist.strip(' -'), album)
        done[post] = {'id': album_id, 'ok': ok, 'artista': a, 'album': t, 'pistas': n}
        print(f'{"ok " if ok else "NO "} {post}: bandcamp dice «{a} – {t}», {n} pistas', flush=True)
        out.write_text(json.dumps(done, ensure_ascii=False, indent=1, sort_keys=True), encoding='utf-8')

    print(f'{out}: {sum(v["ok"] for v in done.values())} buenos de {len(done)}')


if __name__ == '__main__':
    main()
