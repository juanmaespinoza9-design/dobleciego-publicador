# Publicador de reels · Doble Ciego Latam

Publica en Instagram, a las 18:17 (Argentina), el primer reel pendiente cuya fecha ya llegó. Nunca más de uno por corrida ni dos en 20 horas. TikTok es manual: tras cada publicación el workflow abre un issue «TikTok: <ENSAYO>» con el texto y los enlaces.

- `programa/<ENSAYO>.json`: lo escribe la Mac con `preparar_publicacion.py` (video y portada van a la release `media`).
- `estado/<ENSAYO>.json`: lo escribe solo el workflow. Sin archivo = pendiente.
- `publicar.py`: solo biblioteca estándar de Python.

## Pausar

Crear un archivo vacío llamado `PAUSA` (o cualquier nombre que empiece con «pausa», sin importar mayúsculas) en la raíz (desde la app de GitHub en el teléfono: Add file > Create new file). Se controla al arrancar y de nuevo justo antes de publicar (si aparece en ese intervalo, el reel vuelve a pendiente). Borrarlo para reanudar.

## Correr a mano

- Desde GitHub: Actions > Publicar reel > Run workflow (modo `publicar`, `simulacro` o `conciliar`; `ensayo` opcional).
- Local: `python3 publicar.py --listar` (sin red), o con `IG_TOKEN` e `IG_USER_ID` exportados: `--simulacro`, `--conciliar`, `--ensayo NOMBRE`, `--sin-git`.
- Pruebas: `python3 -m unittest discover pruebas`.

## Secretos y variables (Settings > Secrets and variables > Actions)

- Secretos: `IG_TOKEN`, `IG_USER_ID`, `GH_PAT_SECRETS` (token personal con permiso para escribir secretos y variables del repositorio; solo se usa para renovar el token).
- Variable: `IG_TOKEN_FECHA` (AAAA-MM-DD de la última emisión/renovación del token). Si falta o es ilegible, el paso de renovación falla a propósito.

## Renovación del token e inactividad

El workflow tiene dos pasos. «Publicar» no ve `GH_PAT_SECRETS`. «Renovar token» (`--solo-renovar`, corre siempre salvo en simulacro) renueva el token cuando pasaron más de 7 días, lo prueba contra `/me`, actualiza el secreto `IG_TOKEN` y la variable `IG_TOKEN_FECHA`, y registra la fecha en `ultima_renovacion.txt` con commit y push. Ese commit semanal cuenta como actividad: GitHub desactiva los cron de repositorios públicos tras 60 días sin actividad.

## Fallos y reintentos

Fallas transitorias (red, 5xx, 429) no escriben estado y se reintentan en la próxima corrida. Un reel en `error` no se reintenta solo: borrar `estado/<ENSAYO>.json` (o correr con `--ensayo`). Un `estado/*.json` ilegible bloquea toda la cola hasta corregirlo.
