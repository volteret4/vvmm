# vvmm

## escuchas

Sección `/escuchas/`: una página por semana (ISO, lunes a domingo, hora de Madrid) con los discos más escuchados en Last.fm (paqueradejere), un post por disco ordenado por número de escuchas.

Los scrobbles no se piden a Last.fm: se copian por ssh las bases de repeat-one en pepecono (`lastfm_stats.db` del contenedor `lastfm-scrobbles`, que se actualiza a diario a las 00:00, y `explorer.db` de `lastfm-explorer` para sello, fecha, géneros y MBID). Las portadas salen de Cover Art Archive y, si no hay, de Last.fm (con `LASTFM_API_KEY`), y se guardan una vez por disco en `assets/escuchas-portadas/`.

```bash
python3 -I scripts/escuchas.py --anio 2026
```

```bash
python3 -I scripts/escuchas.py --ultima
```

```bash
python3 -I scripts/escuchas.py --semana 2026-W45 --semana 2026-W46
```

Opciones: `--min N` (escuchas mínimas por disco, 3 por defecto), `--max N` (discos por semana), `--incluir-actual`, `--sin-copiar`, `--rebuscar`. Ver `python3 scripts/escuchas.py -h`.
