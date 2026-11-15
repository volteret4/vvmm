#!/usr/bin/env python3
"""Genera la sección «escuchas»: lo escuchado cada semana, agrupado por álbum.

Los scrobbles salen de repeat-one en pepecono, que ya los extrae a diario de Last.fm
(contenedor lastfm-scrobbles, /data/lastfm_stats.db). Sello, fecha y géneros de cada
álbum, y su MBID, salen de explorer.db (servicio lastfm-explorer). Ambas bases se
copian por ssh a .cache/repeat-one/ al empezar y se consultan en local.

Cada semana (ISO, de lunes a domingo, hora de Madrid) es una página
content/escuchas/AAAA-semana-SS/ y cada álbum escuchado esa semana es un post dentro
de ella, ordenados por número de escuchas. Las portadas (500 px) se guardan en
assets/escuchas-portadas/, una sola vez por disco aunque se repita en varias semanas:
primero Cover Art Archive con el MBID y, si no hay, album.getInfo de Last.fm (solo si
está LASTFM_API_KEY). Los discos sin portada se recuerdan en .cache/ y no se vuelven a
buscar (--rebuscar para repetirlos).

Regenerar una semana borra y vuelve a escribir solo la carpeta de esa semana.

Uso:
  python3 -I scripts/escuchas.py --anio 2026          todas las semanas cerradas de 2026
  python3 -I scripts/escuchas.py --ultima             la última semana cerrada
  python3 -I scripts/escuchas.py --semana 2026-W45    una semana concreta (repetible)
Opciones:
  --min N            escuchas mínimas para que un álbum entre (por defecto 3)
  --max N            como mucho N álbumes por semana (0 = sin límite, por defecto)
  --incluir-actual   con --anio, incluye también la semana en curso (incompleta)
  --host HOST        máquina de repeat-one (por defecto pepecono, vía ~/.ssh/config;
                     «local» si se ejecuta en el propio pepecono)
  --sin-copiar       usa las bases ya copiadas en .cache/repeat-one/ sin pedirlas
  --rebuscar         vuelve a buscar portadas que antes no se encontraron
  --raiz DIR         raíz del blog (por defecto, la carpeta padre de scripts/)
Solo biblioteca estándar más ssh.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Europe/Madrid')
UA = 'vvmm-escuchas/1.0'
MESES = 'ene feb mar abr may jun jul ago sep oct nov dic'.split()
# en pepecono
STATS_CMD = 'docker exec lastfm-scrobbles cat /data/lastfm_stats.db'
EXPLORER_CMD = 'cat contenedores/repeat-one/services/lastfm_explorer/explorer.db'
# estrella gris que Last.fm devuelve cuando no tiene portada
LASTFM_SIN_PORTADA = '2a96cbd8b46e442fc41c2b86b821562f'
PORTADAS = 'escuchas-portadas'


# ── bases de datos ───────────────────────────────────────────────────────────

def copiar(host, cmd, destino):
    tmp = destino.with_suffix('.tmp')
    # --host local: se ejecuta en el propio pepecono (cron semanal), sin ssh
    orden = ['bash', '-c', cmd] if host == 'local' else ['ssh', '-o', 'BatchMode=yes', host, cmd]
    with open(tmp, 'wb') as f:
        r = subprocess.run(orden, stdout=f, stderr=subprocess.PIPE, cwd=Path.home())
    if r.returncode or tmp.stat().st_size < 4096:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f'No se pudo copiar de {host}: {cmd}\n{r.stderr.decode().strip()}')
    tmp.replace(destino)


def bases(raiz, host, sin_copiar):
    d = raiz / '.cache' / 'repeat-one'
    d.mkdir(parents=True, exist_ok=True)
    stats, explorer = d / 'lastfm_stats.db', d / 'explorer.db'
    if not sin_copiar:
        copiar(host, STATS_CMD, stats)
        copiar(host, EXPLORER_CMD, explorer)
    elif not stats.exists():
        raise SystemExit(f'No hay copia en {stats}; quita --sin-copiar')
    db = sqlite3.connect(f'file:{stats}?mode=ro', uri=True)
    if explorer.exists():
        db.execute(f"ATTACH DATABASE 'file:{explorer}?mode=ro' AS ex")
    else:
        db.execute("ATTACH DATABASE ':memory:' AS ex")
        db.execute('CREATE TABLE ex.album_enrichment (lastfm_album_id INTEGER PRIMARY KEY, release_date TEXT,'
                   ' label TEXT, genres_json TEXT, mb_release_id TEXT)')
    ultimo = db.execute('SELECT max(ts) FROM scrobbles').fetchone()[0]
    print(f'scrobbles hasta {datetime.fromtimestamp(ultimo, TZ):%Y-%m-%d %H:%M}')
    return db, ultimo


EDICION = re.compile(r'\s*[\(\[][^\)\]]*[\)\]]'
                     r'|\s+-\s+.*\b(remaster\w*|deluxe|edition|version|expanded|anniversary|mono|stereo)\b.*$', re.I)


def disco_base(nombre):
    """Nombre del disco sin edición: «Chorus (2020 Deluxe Edition)» → «chorus»."""
    return norm(EDICION.sub('', nombre or '')) or norm(nombre or '')


def primeras(db):
    """Primer scrobble de cada artista y de cada disco en todo el historial (Last.fm + ListenBrainz).

    Las ediciones del mismo disco («Deluxe», «Remastered»…) cuentan como el mismo disco."""
    art = dict(db.execute('SELECT artist_id, min(ts) FROM scrobbles GROUP BY artist_id'))
    alb = {}
    q = ('SELECT s.artist_id, al.name, min(s.ts) FROM scrobbles s JOIN albums al USING (album_id)'
         ' GROUP BY s.album_id')
    for artist_id, nombre, ts in db.execute(q):
        k = (artist_id, disco_base(nombre))
        alb[k] = min(ts, alb.get(k, ts))
    return art, alb


# ── Mix: canciones guardadas (son también los corazones de Last.fm) ──────────

# library.db de mixxx-info: una fila por canción de Mix (artista, disco, título, ruta)
MIX_CMD = 'cat contenedores/repeat-one/services/mixxx_info/library.db'


def mix(raiz, host, sin_copiar):
    """{(artista, disco base): {títulos base}} de las canciones de Mix (sin Mix/.old)."""
    f = raiz / '.cache' / 'repeat-one' / 'mix.db'
    if not (sin_copiar and f.exists()):
        try:
            copiar(host, MIX_CMD, f)
        except SystemExit as e:
            print(f'  · Mix no disponible: {e}', file=sys.stderr)
            if not f.exists():
                return {}
    db = sqlite3.connect(f'file:{f}?mode=ro', uri=True)
    canciones, n = {}, 0
    for artist, album, title, ruta in db.execute('SELECT artist, album, title, file_path FROM songs'):
        if '/.' in (ruta or '') or not album:
            continue
        n += 1
        canciones.setdefault((norm(artist or ''), disco_base(album)), set()).add(disco_base(title))
    print(f'Mix: {n} canciones')
    return canciones


# un disco es «novedad» si se publicó como mucho estos días antes del final de la semana
DIAS_NOVEDAD = 90
REEDICION = re.compile(r'remaster|deluxe|anniversary|expanded|reissue|edition|aniversario', re.I)


def novedad(fecha, album, fin):
    """Días desde la publicación hasta el final de la semana, o None si no es novedad.

    Solo con fecha de día o de mes (con solo el año no se sabe) y nunca en reediciones,
    cuya fecha es la de la reedición."""
    m = re.fullmatch(r'(\d{4})-(\d{2})(?:-(\d{2}))?', fecha or '')
    if not m or REEDICION.search(album or ''):
        return None
    try:
        pub = date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
    except ValueError:
        return None
    dias = (fin.date() - pub).days
    return dias if 0 < dias <= DIAS_NOVEDAD + 7 else None


def clave_disco(artist, album):
    """Clave estable de un disco (data/reproductor_escuchas.json): no cambia con la edición."""
    return f'{norm(artist)}|{disco_base(album)}'


# desde este día mover_mix marca como favorita en Last.fm cada canción que pasa a Mix;
# antes de él había favoritas por otros motivos y su fecha no dice cuándo entró en Mix
MIX_FAVORITAS_DESDE = date(2025, 11, 3)


def lastfm_key(host):
    """API key de Last.fm: la del entorno o, si no, la del contenedor lastfm-scrobbles de pepecono."""
    if os.environ.get('LASTFM_API_KEY'):
        return os.environ['LASTFM_API_KEY']
    cmd = 'docker exec lastfm-scrobbles printenv LASTFM_API_KEY'
    orden = ['bash', '-c', cmd] if host == 'local' else ['ssh', '-o', 'BatchMode=yes', host, cmd]
    r = subprocess.run(orden, capture_output=True, text=True)
    return r.stdout.strip()


def favoritas(raiz, host, sin_copiar, usuario='paqueradejere'):
    """[(artista, título, ts)] de las canciones favoritas (loved) en Last.fm."""
    f = raiz / '.cache' / 'repeat-one' / 'favoritas.json'
    if sin_copiar and f.exists():
        return json.loads(f.read_text())
    key = lastfm_key(host)
    if not key:
        print('  · sin API key de Last.fm: las altas en Mix usan solo la primera escucha', file=sys.stderr)
        return json.loads(f.read_text()) if f.exists() else []
    out, page, pages = [], 1, 1
    while page <= pages:
        q = urllib.parse.urlencode({'method': 'user.getlovedtracks', 'user': usuario, 'limit': 1000,
                                    'page': page, 'api_key': key, 'format': 'json'})
        req = urllib.request.Request('https://ws.audioscrobbler.com/2.0/?' + q, headers={'User-Agent': UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.load(r)['lovedtracks']
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
            print(f'  · favoritas de Last.fm: {e}', file=sys.stderr)
            return json.loads(f.read_text()) if f.exists() else []
        pages = int(d['@attr']['totalPages'])
        out += [(t['artist']['name'], t['name'], int(t['date']['uts'])) for t in d['track'] if t.get('date')]
        page += 1
    f.write_text(json.dumps(out, ensure_ascii=False))
    return out


def mix_altas(raiz, db, host, sin_copiar):
    """Fecha de alta en Mix de cada canción: favorita en Last.fm (desde MIX_FAVORITAS_DESDE) o primera escucha.

    Devuelve ({'AAAA-MM-DD': [por favorita, por primera escucha]}, sin fecha)."""
    f = raiz / '.cache' / 'repeat-one' / 'mix.db'
    if not f.exists():
        return {}, 0
    desde = datetime.combine(MIX_FAVORITAS_DESDE, datetime.min.time(), TZ).timestamp()
    fav = {}
    for artist, title, ts in favoritas(raiz, host, sin_copiar):
        if ts >= desde:
            k = (norm(artist), disco_base(title))
            fav[k] = min(ts, fav.get(k, ts))
    primera = {}
    q = 'SELECT ar.name, s.track, min(s.ts) FROM scrobbles s JOIN artists ar USING (artist_id) GROUP BY s.artist_id, s.track_normalized'
    for artist, track, ts in db.execute(q):
        k = (norm(artist), disco_base(track))
        primera[k] = min(ts, primera.get(k, ts))
    dias, sin = {}, 0
    mdb = sqlite3.connect(f'file:{f}?mode=ro', uri=True)
    for artist, title, ruta in mdb.execute('SELECT artist, title, file_path FROM songs'):
        if '/.' in (ruta or ''):
            continue
        k = (norm(artist or ''), disco_base(title))
        if k in fav:
            ts, i = fav[k], 0
        elif k in primera:
            ts, i = primera[k], 1
        else:
            sin += 1
            continue
        d = dias.setdefault(datetime.fromtimestamp(ts, TZ).strftime('%Y-%m-%d'), [0, 0])
        d[i] += 1
    return dias, sin


def limites(anio, semana):
    """Lunes 00:00 y lunes siguiente 00:00 (hora de Madrid) de una semana ISO."""
    lunes = date.fromisocalendar(anio, semana, 1)
    ini = datetime.combine(lunes, datetime.min.time(), TZ)
    fin = datetime.combine(lunes + timedelta(days=7), datetime.min.time(), TZ)
    return ini, fin


def scrobbles(db, ini, fin):
    filas = db.execute('''
        SELECT s.artist_id, s.album_id, s.track, s.track_normalized, s.ts, s.source,
               ar.name, al.name
        FROM scrobbles s JOIN artists ar USING (artist_id) LEFT JOIN albums al USING (album_id)
        WHERE s.ts >= ? AND s.ts < ? ORDER BY s.ts''', (int(ini.timestamp()), int(fin.timestamp()))).fetchall()
    # Last.fm y ListenBrainz se solapan a veces: la misma canción con segundos de diferencia
    vistas, out = {}, []
    for f in sorted(filas, key=lambda f: f[5] != 'lastfm'):
        k = (f[0], f[3])
        if any(abs(f[4] - t) < 300 for t in vistas.get(k, ())):
            continue
        vistas.setdefault(k, []).append(f[4])
        out.append({'artist_id': f[0], 'album_id': f[1], 'track': f[2], 'artist': f[6], 'album': f[7]})
    return out


def ficha(db, album_id, artist_id):
    r = db.execute('SELECT release_date, label, genres_json, mb_release_id FROM ex.album_enrichment'
                   ' WHERE lastfm_album_id = ?', (album_id,)).fetchone()
    fecha, sello, generos, mbid = r or (None, None, None, None)
    try:
        generos = json.loads(generos or '[]')
    except ValueError:
        generos = []
    if not generos:
        generos = [g for g, in db.execute('SELECT g.name FROM artist_genres JOIN genres g USING (genre_id)'
                                          ' WHERE artist_id = ? LIMIT 6', (artist_id,))]
    return {'fecha': fecha or '', 'sello': sello or '', 'generos': generos[:6], 'mbid': mbid or ''}


# ── portadas ─────────────────────────────────────────────────────────────────

class Temporal(Exception):
    """Error pasajero (5xx, red): no se recuerda como «sin portada»."""


def bajar(url):
    for intento in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:
                return b''
            err = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            err = e
        time.sleep(3 * (intento + 1))
    print(f'  · {url}: {err}', file=sys.stderr)
    raise Temporal(url)


def tipo(data):
    if data.startswith(b'\xff\xd8'):
        return 'jpg'
    if data.startswith(b'\x89PNG'):
        return 'png'
    return ''


def lastfm_portada(artist, album):
    key = os.environ.get('LASTFM_API_KEY')
    if not key:
        return b''
    q = urllib.parse.urlencode({'method': 'album.getinfo', 'artist': artist, 'album': album,
                                'autocorrect': 1, 'api_key': key, 'format': 'json'})
    try:
        req = urllib.request.Request('https://ws.audioscrobbler.com/2.0/?' + q, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        d = {}
        if e.code != 404:
            print(f'  · album.getinfo {artist} - {album}: {e}', file=sys.stderr)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return b''
    url = next((i['#text'] for i in d.get('album', {}).get('image', []) if i.get('size') == 'extralarge'), '')
    m = re.search(r'/([0-9a-f]{32})\.(\w+)$', url)
    if not m or m.group(1) == LASTFM_SIN_PORTADA:
        return b''
    for ext in (m.group(2), 'jpg', 'png'):
        data = bajar(f'https://lastfm.freetls.fastly.net/i/u/500x500/{m.group(1)}.{ext}')
        if tipo(data):
            return data
    return b''


class Portadas:
    def __init__(self, raiz, rebuscar):
        self.dir = raiz / 'assets' / PORTADAS
        self.memo_f = raiz / '.cache' / 'escuchas-sin-portada.json'
        self.memo = set() if rebuscar or not self.memo_f.exists() else set(json.loads(self.memo_f.read_text()))

    def get(self, artist, album, mbid):
        """Nombre del fichero de portada en assets/escuchas-portadas/, o ''."""
        clave = hashlib.md5(f'{norm(artist)}|{norm(album)}'.encode()).hexdigest()[:16]
        for ext in ('jpg', 'png'):
            if (self.dir / f'{clave}.{ext}').exists():
                return f'{clave}.{ext}'
        if clave in self.memo:
            return ''
        try:
            data = bajar(f'https://coverartarchive.org/release/{mbid}/front-500') if mbid else b''
            if not tipo(data):
                data = lastfm_portada(artist, album)
        except Temporal:
            return ''  # se reintentará en la próxima ejecución
        if not tipo(data):
            self.memo.add(clave)
            self.memo_f.write_text(json.dumps(sorted(self.memo)))
            return ''
        self.dir.mkdir(parents=True, exist_ok=True)
        nombre = f'{clave}.{tipo(data)}'
        (self.dir / nombre).write_bytes(data)
        time.sleep(0.2)
        return nombre


# ── escritura ────────────────────────────────────────────────────────────────

def norm(s):
    s = unicodedata.normalize('NFKD', s.casefold())
    return re.sub(r'\W+', ' ', ''.join(c for c in s if not unicodedata.combining(c))).strip()


def slug(s):
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'[^A-Za-z0-9]+', '-', s).strip('-')[:70] or 'x'


def q(s):
    return json.dumps(s, ensure_ascii=False)


def rango(ini, fin):
    a, b = ini.date(), (fin - timedelta(days=1)).date()
    corta = lambda d: f'{d.day} {MESES[d.month - 1]}'  # noqa: E731
    if a.month == b.month:
        return f'{a.day}–{b.day} {MESES[b.month - 1]} {b.year}'
    if a.year == b.year:
        return f'{corta(a)} – {corta(b)} {b.year}'
    return f'{corta(a)} {a.year} – {corta(b)} {b.year}'


def lastfm_url(artist, album):
    return ('https://www.last.fm/music/' + urllib.parse.quote_plus(artist)
            + '/' + urllib.parse.quote_plus(album))


def semana(raiz, db, primera, enmix, anio, sem, minimo, maximo, portadas):
    ini, fin = limites(anio, sem)
    completa = fin <= datetime.now(TZ)
    tracks = scrobbles(db, ini, fin)
    grupos = {}
    for t in tracks:
        if t['album_id'] and (t['album'] or '').strip():
            grupos.setdefault(t['album_id'], []).append(t)
    sin_album = sum(1 for t in tracks if not (t['album_id'] and (t['album'] or '').strip()))

    albumes = []
    for album_id, ts in grupos.items():
        if len(ts) >= minimo:
            albumes.append({'id': album_id, 'artist_id': ts[0]['artist_id'], 'artist': ts[0]['artist'],
                            'album': ts[0]['album'], 'n': len(ts),
                            'canciones': Counter(t['track'] for t in ts),
                            # primera vez: el primer scrobble de su historia cae en esta semana
                            'nuevo_artista': primera[0].get(ts[0]['artist_id'], 0) >= ini.timestamp(),
                            'nuevo_album': primera[1].get((ts[0]['artist_id'], disco_base(ts[0]['album'])), 0)
                            >= ini.timestamp(),
                            'mix': enmix.get((norm(ts[0]['artist']), disco_base(ts[0]['album'])), set())})
    albumes.sort(key=lambda a: (-a['n'], -len(a['canciones']), norm(a['artist'])))
    if maximo:
        albumes = albumes[:maximo]

    carpeta = raiz / 'content' / 'escuchas' / f'{anio}-semana-{sem:02d}'
    if carpeta.exists():
        shutil.rmtree(carpeta)
    if not albumes:
        print(f'{anio}-W{sem:02d}: {len(tracks)} scrobbles, ningún álbum con ≥{minimo} escuchas; sin página')
        return
    carpeta.mkdir(parents=True)
    domingo = datetime.combine((fin - timedelta(days=1)).date(), datetime.min.time(), TZ).replace(hour=23, minute=59)
    vacias = 0
    for i, a in enumerate(albumes, 1):
        f = ficha(db, a['id'], a['artist_id'])
        portada = portadas.get(a['artist'], a['album'], f['mbid'])
        vacias += not portada
        a['novedad'] = novedad(f['fecha'], a['album'], fin)
        lineas = [
            '---',
            f'title: {q(a["artist"] + " - " + a["album"])}',
            # el puesto resta segundos para que el orden por fecha sea también el de escuchas
            f'date: {(domingo - timedelta(seconds=i)).isoformat()}',
            f'artist: {q(a["artist"])}',
            f'album: {q(a["album"])}',
            f'escuchas: {a["n"]}',
            f'puesto: {i}',
            f'semana: "{anio}-W{sem:02d}"',
            f'clave: {q(clave_disco(a["artist"], a["album"]))}',
        ]
        if a['nuevo_artista']:
            lineas.append('nuevo_artista: true')
        if a['nuevo_album']:
            lineas.append('nuevo_album: true')
        if a['mix']:
            lineas.append(f'mix: {len(a["mix"])}')
        if a['novedad'] is not None:
            lineas.append('novedad: true')
        if portada:
            lineas.append(f'portada: "{portada}"')
        if f['mbid']:
            lineas.append(f'mbid: "{f["mbid"]}"')
        lineas += ['---', f'[![lastfm](../links/svg/lastfm.png (lastfm))]({lastfm_url(a["artist"], a["album"])})']
        if f['mbid']:
            lineas.append(f'[![musicbrainz](../links/svg/musicbrainz.png (musicbrainz))]'
                          f'(https://musicbrainz.org/release/{f["mbid"]})')
        if a['nuevo_artista']:
            lineas += ['', '**Primera vez:** primera semana escuchando a este artista']
        elif a['nuevo_album']:
            lineas += ['', '**Primera vez:** primera semana escuchando este disco']
        if a['novedad'] is not None:
            lineas += ['', f'**Novedad:** publicado el {f["fecha"]}, {a["novedad"]} días antes de acabar la semana']
        lineas += ['', f'**Escuchas:** {a["n"]}', '', f'**Puesto:** {i} de {len(albumes)}', '',
                   f'**Canciones distintas:** {len(a["canciones"])}', '']
        if a['mix']:
            n = len(a['mix'])
            lineas += [f'**En Mix:** {"♥" * min(n, 10)} {n} {"canción" if n == 1 else "canciones"}', '']
        if f['generos']:
            lineas += [f'**Género:** {", ".join(f["generos"])}', '']
        if f['sello']:
            lineas += [f'**Sello:** {f["sello"]}', '']
        if f['fecha']:
            lineas += [f'**Año:** {f["fecha"]}', '']
        lineas += ['## Canciones escuchadas', '', '| Canción | Escuchas |', '|---------|---------:|']
        for c, n in sorted(a['canciones'].items(), key=lambda x: (-x[1], x[0].casefold())):
            corazon = ' ♥' if disco_base(c) in a['mix'] else ''
            lineas.append(f'| {c.replace("|", "∣")}{corazon} | {n} |')
        d = carpeta / f'{i:03d}-{slug(a["artist"])}---{slug(a["album"])}'
        d.mkdir()
        (d / 'index.md').write_text('\n'.join(lineas) + '\n')

    (carpeta / '_index.md').write_text('\n'.join([
        '---',
        f'title: "Semana {sem} de {anio}"',
        f'date: {domingo.isoformat()}',
        f'anio: {anio}',
        f'semana: {sem}',
        f'desde: {ini.date().isoformat()}',
        f'hasta: {(fin - timedelta(days=1)).date().isoformat()}',
        f'rango: {q(rango(ini, fin))}',
        f'scrobbles: {len(tracks)}',
        f'nuevos_artistas: {sum(a["nuevo_artista"] for a in albumes)}',
        f'nuevos_albumes: {sum(a["nuevo_album"] for a in albumes)}',
        f'mix: {sum(len(a["mix"]) for a in albumes)}',
        f'novedades: {sum(a["novedad"] is not None for a in albumes)}',
        f'completa: {str(completa).lower()}',
        '---',
    ]) + '\n')
    aviso = '' if completa else ' (en curso)'
    print(f'{anio}-W{sem:02d} ({rango(ini, fin)}){aviso}: {len(tracks)} scrobbles, {len(albumes)} álbumes'
          f' con ≥{minimo} escuchas, {sin_album} sin álbum, {vacias} sin portada')


# ── estadísticas (todo el historial) ─────────────────────────────────────────

# una escucha es «nueva» si cae dentro de estos días desde la primera escucha
# de ese artista / disco / canción
DIAS_NUEVO = 30
TOP_ETIQUETAS = 150


def estadisticas(raiz, db, host='pepecono', sin_copiar=False):
    """data/escuchas_stats.json: series diarias y sellos/géneros por mes, para la pestaña de estadísticas."""
    filas = db.execute('''
        SELECT s.artist_id, s.album_id, al.name, s.track_normalized, s.ts, s.source, ex.label, ex.genres_json
        FROM scrobbles s LEFT JOIN albums al USING (album_id)
        LEFT JOIN ex.album_enrichment ex ON ex.lastfm_album_id = s.album_id
        ORDER BY s.ts''').fetchall()
    # mismo criterio que scrobbles(): fuera los duplicados Last.fm / ListenBrainz
    vistas, unicas = {}, []
    for f in sorted(filas, key=lambda f: f[5] != 'lastfm'):
        k = (f[0], f[3])
        if any(abs(f[4] - t) < 300 for t in vistas.get(k, ())):
            continue
        vistas.setdefault(k, []).append(f[4])
        unicas.append(f)
    unicas.sort(key=lambda f: f[4])
    gen_artista = {}
    for artist_id, g in db.execute('SELECT artist_id, g.name FROM artist_genres JOIN genres g USING (genre_id)'):
        gen_artista.setdefault(artist_id, []).append(g)

    ventana = DIAS_NUEVO * 86400
    primera = {}  # (tipo, clave) → ts
    dias = {}     # 'AAAA-MM-DD' → [escuchas, nuevasArt, nuevasAlb, nuevasCan, descArt, descAlb, descCan]
    sellos, generos = {}, {}  # nombre → {'AAAA-MM': escuchas}
    for artist_id, album_id, album, track, ts, _, sello, gjson in unicas:
        dia = datetime.fromtimestamp(ts, TZ).strftime('%Y-%m-%d')
        d = dias.setdefault(dia, [0] * 7)
        d[0] += 1
        claves = [('a', artist_id), ('d', (artist_id, disco_base(album))) if album else None, ('c', (artist_id, track))]
        for i, k in enumerate(claves):
            if k is None:
                continue
            if k not in primera:
                primera[k] = ts
                d[4 + i] += 1
            if ts - primera[k] < ventana:
                d[1 + i] += 1
        mes = dia[:7]
        if sello and sello.strip() and sello.lower() not in ('[no label]', 'not on label', 'self-released'):
            m = sellos.setdefault(sello.strip(), {})
            m[mes] = m.get(mes, 0) + 1
        try:
            gs = json.loads(gjson or '[]')
        except ValueError:
            gs = []
        for g in {x.strip().lower() for x in (gs or gen_artista.get(artist_id, []))[:4]} - {'', 'unknown'}:
            m = generos.setdefault(g, {})
            m[mes] = m.get(mes, 0) + 1

    def top(tabla):
        orden = sorted(tabla, key=lambda n: -sum(tabla[n].values()))[:TOP_ETIQUETAS]
        return {'nombres': orden, 'meses': [tabla[n] for n in orden]}

    out = {
        'generado': datetime.now(TZ).strftime('%Y-%m-%d'),
        'dias_nuevo': DIAS_NUEVO,
        'campos': ['escuchas', 'nuevas_artista', 'nuevas_disco', 'nuevas_cancion',
                   'artistas_descubiertos', 'discos_descubiertos', 'canciones_descubiertas'],
        'dias': [[k] + v for k, v in sorted(dias.items())],
        'sellos': top(sellos),
        'mix_desde_favoritas': MIX_FAVORITAS_DESDE.isoformat(),
        'generos': top(generos),
    }
    altas, sin = mix_altas(raiz, db, host, sin_copiar)
    out['mix'] = [[k] + v for k, v in sorted(altas.items())]
    out['mix_sin_fecha'] = sin
    f = raiz / 'data' / 'escuchas_stats.json'
    f.write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')) + '\n')
    print(f'estadísticas: {len(unicas)} escuchas, {len(dias)} días → {f.relative_to(raiz)} ({f.stat().st_size // 1024} KB)')


def limpiar(raiz):
    """Borra de assets/escuchas-portadas/ las portadas que ya no usa ninguna semana."""
    usadas = set()
    for f in (raiz / 'content' / 'escuchas').glob('*/*/index.md'):
        m = re.search(r'^portada: "([^"]+)"', f.read_text(), re.M)
        if m:
            usadas.add(m.group(1))
    sobran = [f for f in (raiz / 'assets' / PORTADAS).glob('*') if f.is_file() and f.name not in usadas]
    for f in sobran:
        f.unlink()
    if sobran:
        print(f'{len(sobran)} portadas sin uso borradas')


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--anio', type=int)
    g.add_argument('--ultima', action='store_true')
    g.add_argument('--semana', action='append', metavar='AAAA-WSS')
    p.add_argument('--min', type=int, default=3)
    p.add_argument('--max', type=int, default=0)
    p.add_argument('--incluir-actual', action='store_true')
    p.add_argument('--host', default='pepecono')
    p.add_argument('--sin-copiar', action='store_true')
    p.add_argument('--rebuscar', action='store_true')
    p.add_argument('--raiz', type=Path, default=Path(__file__).resolve().parent.parent)
    a = p.parse_args()

    hoy = datetime.now(TZ).date()
    if a.ultima:
        semanas = [(hoy - timedelta(days=7)).isocalendar()[:2]]
    elif a.semana:
        semanas = []
        for s in a.semana:
            m = re.fullmatch(r'(\d{4})-?W?(\d{1,2})', s, re.I)
            if not m:
                raise SystemExit(f'Semana no válida: {s} (usa AAAA-WSS, p. ej. 2026-W45)')
            semanas.append((int(m.group(1)), int(m.group(2))))
    else:
        semanas = []
        for s in range(1, date(a.anio, 12, 28).isocalendar()[1] + 1):
            ini, fin = limites(a.anio, s)
            if ini.date() > hoy or (fin.date() > hoy and not a.incluir_actual):
                break
            semanas.append((a.anio, s))

    raiz = a.raiz.resolve()
    db, ultimo = bases(raiz, a.host, a.sin_copiar)
    primera = primeras(db)
    enmix = mix(raiz, a.host, a.sin_copiar)
    sec = raiz / 'content' / 'escuchas' / '_index.md'
    if not sec.exists():
        sec.parent.mkdir(parents=True, exist_ok=True)
        sec.write_text('---\ntitle: "escuchas"\n---\n')
    portadas = Portadas(raiz, a.rebuscar)
    for anio, s in semanas:
        semana(raiz, db, primera, enmix, anio, s, a.min, a.max, portadas)
    limpiar(raiz)
    estadisticas(raiz, db, a.host, a.sin_copiar)


if __name__ == '__main__':
    main()
