# Publicador de reels · Doble Ciego Latam

Publica en Instagram, a las 18:17 (Argentina), el primer reel pendiente cuya fecha ya llegó. Nunca más de uno por corrida ni dos en 20 horas. TikTok es manual: tras cada publicación el workflow abre un issue «TikTok: <ENSAYO>» con el texto y los enlaces.

- `programa/<ENSAYO>.json`: lo escribe la Mac con `preparar_publicacion.py` (video y portada van a la release `media`).
- `estado/<ENSAYO>.json`: lo escribe solo el workflow. Sin archivo = pendiente.
- `publicar.py`: solo biblioteca estándar de Python.

## Pausar

Crear un archivo vacío llamado `PAUSA` (o cualquier nombre que empiece con «pausa», sin importar mayúsculas) en la raíz (desde la app de GitHub en el teléfono: Add file > Create new file). Se controla al arrancar y de nuevo justo antes de publicar (si aparece en ese intervalo, el reel vuelve a pendiente). Borrarlo para reanudar.

## Correr a mano

- Desde GitHub: Actions > Publicar reel > Run workflow (modo `publicar`, `simulacro`, `conciliar` o `verificar`; `ensayo` opcional).
- Local: `python3 publicar.py --listar` (sin red), o con `IG_TOKEN` e `IG_USER_ID` exportados: `--simulacro`, `--conciliar`, `--ensayo NOMBRE`, `--sin-git`.
- Pruebas: `python3 -m unittest discover pruebas`.

## Secretos (Settings > Secrets and variables > Actions)

- `IG_TOKEN`: token de usuario del sistema de la aplicación de Meta («inicio de sesión unificado», prefijo `EAA`). No vence, así que no hay renovación automática.
- `IG_USER_ID`: id de la cuenta de Instagram profesional. Todas las llamadas lo usan explícito; nunca `/me`.

Si el token se revoca o Meta responde «código 190», el script avisa «El token de Instagram no es válido» y sale con error: generar uno nuevo en la aplicación de Meta (usuario del sistema > generar token, con permisos de publicación en Instagram) y reemplazar el secreto `IG_TOKEN`. Comprobarlo con Actions > Run workflow > modo `verificar` (solo lectura: usuario, cantidad de publicaciones y cuota de publicación) o localmente con `python3 publicar.py --verificar`.

## Inactividad del cron

GitHub desactiva los cron de un repositorio público tras 60 días sin commits. Mientras hay reels por publicar, los commits de estado cuentan como actividad; con una PAUSA muy larga o la cola vacía, el cron se apaga. Se reactiva desde la pestaña Actions (habilitar el workflow).

## Fallos y reintentos

Fallas transitorias (red, 5xx, 429) no escriben estado y se reintentan en la próxima corrida. Un reel en `error` no se reintenta solo: borrar `estado/<ENSAYO>.json` (o correr con `--ensayo`). Un `estado/*.json` ilegible bloquea toda la cola hasta corregirlo.
