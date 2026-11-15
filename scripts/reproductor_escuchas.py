#!/usr/bin/env python3
"""Busca un reproductor para cada disco de escuchas y lo guarda en data/reproductor_escuchas.json.

Clave: la de cada post (`clave:` en su front matter, «artista|disco» normalizados), así el
reproductor vale para todas las semanas en que salga el disco. Orden de preferencia:
  1. Bandcamp, de la base de datos de music-fuzzy en pepecono (albums.bandcamp_album_id,
     ya comprobado por music-fuzzy).
  2. Bandcamp, desde los enlaces de MusicBrainz (MBID del post o búsqueda, como
     bandcamp_buscar.py): se lee la página del disco y solo se acepta si artista y título
     coinciden y hay pistas que se pueden escuchar (el mismo criterio que music-fuzzy).
  3. YouTube con yt-dlp, sin API: playlist del álbum o vídeo de álbum completo
     (search() de reproductor.py).

Lo ya resuelto no se vuelve a buscar; los «sin reproductor» se reintentan pasados
--reintentar-dias (por defecto 60: los discos nuevos tardan en subirse).

Uso:  python3 -I scripts/reproductor_escuchas.py [--host pepecono|local] [--max N] [--sin-youtube]
"""
import argparse
import html
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bandcamp_buscar import find as mb_bandcamp  # noqa: E402
from bandcamp_verificar import same  # noqa: E402
from enlaces import norm  # noqa: E402
from reproductor import search as youtube  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
MFUZZ_DB = '/home/pepe/contenedores/repeat-one/data/mfuzz-db/musica_local.sqlite'
MFUZZ_SQL = f'''
import json, sqlite3
c = sqlite3.connect("file:{MFUZZ_DB}?mode=ro", uri=True)
q = "SELECT ar.name, al.name, al.bandcamp_album_id FROM albums al JOIN artists ar ON ar.id = al.artist_id WHERE al.bandcamp_album_id > 0"
print(json.dumps(c.execute(q).fetchall()))
'''
UA_BANDCAMP = 'vvmm-blog/1.0 (+https://github.com/volteret4/vvmm)'
TRALBUM = re.compile(r'data-tralbum="([^"]+)"')
PAGE_PROPS = re.compile(r'name="bc-page-properties"\s+content="([^"]+)"')
PAUSA_BANDCAMP = 3
_ultima = [0.0]

from escuchas import EDICION, clave_disco  # noqa: E402


def posts():
    """{clave: (artista, disco, mbid)} de todos los posts de escuchas."""
    out = {}
    for f in sorted((RAIZ / 'content' / 'escuchas').glob('*/*/index.md')):
        fm = f.read_text().split('---')[1]
        g = lambda k: (re.search(rf'^{k}: (.+)$', fm, re.M) or [None, None])[1]  # noqa: E731
        artist, album = json.loads(g('artist') or '""'), json.loads(g('album') or '""')
        mbid = (g('mbid') or '').strip('"')
        out.setdefault(clave_disco(artist, album), (artist, album, mbid))
    return out


def mfuzz(host):
    """{clave: bandcamp_album_id} de la base de datos de music-fuzzy en pepecono."""
    orden = ['python3', '-'] if host == 'local' else ['ssh', '-o', 'BatchMode=yes', host, 'python3', '-']
    r = subprocess.run(orden, input=MFUZZ_SQL, capture_output=True, text=True, timeout=120)
    if r.returncode:
        print(f'  · music-fuzzy no disponible: {r.stderr.strip()[-200:]}', file=sys.stderr)
        return {}
    return {clave_disco(a, d): int(i) for a, d, i in json.loads(r.stdout)}


def pagina_bandcamp(url):
    """{id, artista, titulo, tipo, pistas} de la página de un álbum (como leer_pagina_bandcamp de music-fuzzy)."""
    espera = PAUSA_BANDCAMP - (time.monotonic() - _ultima[0])
    if espera > 0:
        time.sleep(espera)
    _ultima[0] = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA_BANDCAMP})
        with urllib.request.urlopen(req, timeout=30) as r:
            texto = r.read().decode('utf-8', 'replace')
    except (urllib.error.URLError, TimeoutError) as e:
        print(f'  · {url}: {e}', file=sys.stderr)
        return None
    m = TRALBUM.search(texto)
    if not m:
        return None
    try:
        d = json.loads(html.unescape(m.group(1)))
    except ValueError:
        return None
    album_id = d.get('id')
    props = PAGE_PROPS.search(texto)
    if props:
        try:
            p = json.loads(html.unescape(props.group(1)))
            if p.get('item_type') == 'a' and p.get('item_id'):
                album_id = p['item_id']
        except ValueError:
            pass
    return {'id': int(album_id) if album_id else None, 'artista': d.get('artist') or '',
            'titulo': (d.get('current') or {}).get('title') or '', 'tipo': d.get('item_type'),
            'pistas': sum(1 for t in d.get('trackinfo') or [] if t.get('file'))}


def es_el_disco(info, artist, album):
    if not info or info.get('tipo') != 'album' or not info.get('id') or not info.get('pistas'):
        return False
    if same(info['titulo'], album) and same(info['artista'], artist):
        return True
    return norm(info['titulo']) == norm(f'{artist} {album}')  # página de un sello


def buscar(artist, album, mbid, bc_mfuzz, clave, con_youtube):
    """Primero con el título tal cual; si no hay nada, sin la edición: «Covers (Japanese Edition)» → «Covers»."""
    r = buscar_titulo(artist, album, mbid, bc_mfuzz, clave, con_youtube)
    limpio = EDICION.sub('', album).strip()
    if r and r['tipo'] == 'ninguno' and limpio and limpio != album:
        r = buscar_titulo(artist, limpio, '', {}, clave, con_youtube) or r
    return r


def buscar_titulo(artist, album, mbid, bc_mfuzz, clave, con_youtube):
    if clave in bc_mfuzz:
        return {'tipo': 'bandcamp', 'id': bc_mfuzz[clave], 'fuente': 'music-fuzzy'}
    texto = f'https://musicbrainz.org/release/{mbid}' if mbid else ''
    try:
        url, como = mb_bandcamp(texto, artist, album)
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        print(f'  · MusicBrainz: {e}', file=sys.stderr)
        url, como = None, None
    if url:
        info = pagina_bandcamp(url)
        if es_el_disco(info, artist, album):
            return {'tipo': 'bandcamp', 'id': info['id'], 'fuente': f'musicbrainz ({como})'}
    if con_youtube:
        try:
            r = youtube(artist, album)
        except (RuntimeError, subprocess.TimeoutExpired, ValueError) as e:
            print(f'  · yt-dlp: {e}', file=sys.stderr)
            return None  # error pasajero: no se apunta como «sin reproductor»
        if r:
            return r
    return {'tipo': 'ninguno'}


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--host', default='pepecono')
    p.add_argument('--max', type=int, default=0, help='como mucho N búsquedas nuevas')
    p.add_argument('--reintentar-dias', type=int, default=60)
    p.add_argument('--sin-youtube', action='store_true')
    p.add_argument('--solo-ediciones', action='store_true',
                   help='reintenta ya mismo los «sin reproductor» cuyo título lleva edición entre paréntesis')
    a = p.parse_args()

    out_f = RAIZ / 'data' / 'reproductor_escuchas.json'
    datos = json.loads(out_f.read_text()) if out_f.exists() else {}
    hoy = date.today()
    pendientes = []
    for clave, (artist, album, mbid) in posts().items():
        r = datos.get(clave)
        reintento = a.solo_ediciones and r and r['tipo'] == 'ninguno' and EDICION.sub('', album).strip() != album
        if r and not reintento and (r['tipo'] != 'ninguno' or (hoy - date.fromisoformat(r['fecha'])).days < a.reintentar_dias):
            continue
        if a.solo_ediciones and not reintento:
            continue
        pendientes.append((clave, artist, album, mbid))
    if a.max:
        pendientes = pendientes[:a.max]
    print(f'{len(pendientes)} discos por buscar ({len(datos)} ya en {out_f.relative_to(RAIZ)})')
    if not pendientes:
        return
    bc_mfuzz = mfuzz(a.host)
    print(f'music-fuzzy: {len(bc_mfuzz)} discos con Bandcamp')
    cuenta = {}
    for i, (clave, artist, album, mbid) in enumerate(pendientes, 1):
        r = buscar(artist, album, mbid, bc_mfuzz, clave, not a.sin_youtube)
        if r is None:
            continue
        r['fecha'] = hoy.isoformat()
        datos[clave] = r
        cuenta[r['tipo']] = cuenta.get(r['tipo'], 0) + 1
        print(f'[{i}/{len(pendientes)}] {r["tipo"]:11} {artist} - {album}', flush=True)
        if i % 10 == 0:  # guarda a menudo: se puede cortar y seguir después
            out_f.write_text(json.dumps(datos, ensure_ascii=False, indent=1, sort_keys=True) + '\n')
    out_f.write_text(json.dumps(datos, ensure_ascii=False, indent=1, sort_keys=True) + '\n')
    print('resultado:', ', '.join(f'{k} {v}' for k, v in sorted(cuenta.items())))


if __name__ == '__main__':
    main()
