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

### Estadísticas

`/escuchas/estadisticas/` (pestaña dentro de escuchas): conocido vs nuevo para artistas, discos y canciones; artistas y discos descubiertos frente a escuchas; sellos y géneros más escuchados. Filtro por año y mes. Los datos (`data/escuchas_stats.json`, todo el historial) los regenera `scripts/escuchas.py` en cada ejecución; «nuevo» = dentro de los `DIAS_NUEVO` (30) días siguientes a la primera escucha.

### Reproductor, novedades y altas en Mix

- `scripts/reproductor_escuchas.py`: reproductor de cada disco de escuchas en `data/reproductor_escuchas.json` (por la `clave:` del post). Bandcamp de la base de datos de music-fuzzy en pepecono, o de los enlaces de MusicBrainz comprobando la página del disco; si no, YouTube con yt-dlp. Lo lanza también el cron semanal.
- ◆ novedad: disco publicado como mucho 90 días antes de esa semana (fecha de explorer.db; no cuenta reediciones ni discos con solo el año).
- Estadísticas, altas en Mix: fecha en que la canción se marcó como favorita en Last.fm (exacta desde el 3/11/2025, cuando mover_mix empezó a marcarlas al pasarlas a Mix) o, si no, la de su primera escucha.
