#!/usr/bin/env python3
"""Lista las playlists públicas del canal de YouTube y las guarda en data/youtube_playlists.json.

La consulta corre en pepecono con el token de yt_sync (~/.config/yt_sync/token.json) y su
venv, por ssh: solo lectura (playlists.list, 1 unidad de cuota por cada 50 listas).
Las privadas y las ocultas no se publican; las vacías tampoco.

Uso:  python3 -I scripts/youtube_playlists.py [--host pepecono] [--out data/youtube_playlists.json]
"""
import argparse
import json
import subprocess
from pathlib import Path

PYTHON = '/home/pepe/Scripts/python_venv/bin/python'
REMOTO = r'''
import json, os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
f = os.path.expanduser("~/.config/yt_sync/token.json")
c = Credentials.from_authorized_user_file(f, ["https://www.googleapis.com/auth/youtube"])
if not c.valid:
    c.refresh(Request())
    open(f, "w").write(c.to_json())
yt = build("youtube", "v3", credentials=c, cache_discovery=False)
out, tok = [], None
while True:
    r = yt.playlists().list(part="snippet,status,contentDetails", mine=True, maxResults=50, pageToken=tok).execute()
    out += r["items"]
    tok = r.get("nextPageToken")
    if not tok:
        break
print(json.dumps(out))
'''


def miniatura(sn):
    t = sn.get('thumbnails') or {}
    for k in ('high', 'medium', 'standard', 'default'):
        if k in t:
            return t[k]['url']
    return ''


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--host', default='pepecono')
    p.add_argument('--out', type=Path, default=Path(__file__).resolve().parent.parent / 'data' / 'youtube_playlists.json')
    a = p.parse_args()
    r = subprocess.run(['ssh', '-o', 'BatchMode=yes', a.host, PYTHON, '-'], input=REMOTO,
                       capture_output=True, text=True, timeout=180)
    if r.returncode:
        raise SystemExit(r.stderr.strip()[-800:])
    items = json.loads(r.stdout)
    listas = []
    for it in items:
        sn, n = it['snippet'], it['contentDetails']['itemCount']
        if it['status']['privacyStatus'] != 'public' or not n:
            continue
        titulo = sn['title']
        mix = titulo.startswith('Mix - ')
        listas.append({
            'id': it['id'],
            'titulo': titulo[6:] if mix else titulo,
            'grupo': 'mix' if mix else 'otras',
            'videos': n,
            'descripcion': sn.get('description', '').strip(),
            'creada': sn['publishedAt'][:10],
            'miniatura': miniatura(sn),
        })
    listas.sort(key=lambda x: (x['grupo'] != 'mix', -x['videos'], x['titulo'].casefold()))
    a.out.write_text(json.dumps(listas, ensure_ascii=False, indent=1) + '\n')
    privadas = sum(1 for it in items if it['status']['privacyStatus'] != 'public')
    print(f'{len(listas)} playlists públicas en {a.out} ({len(items)} en el canal, {privadas} privadas u ocultas sin publicar)')


if __name__ == '__main__':
    main()
