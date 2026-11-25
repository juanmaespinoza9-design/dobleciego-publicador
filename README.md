# Publicador de reels · Doble Ciego Latam

Publica en Instagram, a las 18:17 (Argentina), el primer reel pendiente cuya fecha ya llegó. Nunca más de uno por corrida ni dos en 20 horas. TikTok es manual: tras cada publicación el workflow abre un issue «TikTok: <ENSAYO>» con el texto y los enlaces.

- `programa/<ENSAYO>.json`: lo escribe la Mac con `preparar_publicacion.py` (video y portada van a la release `media`).
- `estado/<ENSAYO>.json`: lo escribe solo el workflow. Sin archivo = pendiente.
- `publicar.py`: solo biblioteca estándar de Python.

## Pausar

Crear un archivo vacío llamado `PAUSA` en la raíz (desde la app de GitHub en el teléfono: Add file > Create new file). Borrarlo para reanudar.

## Correr a mano

- Desde GitHub: Actions > Publicar reel > Run workflow (modo `publicar`, `simulacro` o `conciliar`; `ensayo` opcional).
- Local: `python3 publicar.py --listar` (sin red), o con `IG_TOKEN` e `IG_USER_ID` exportados: `--simulacro`, `--conciliar`, `--ensayo NOMBRE`, `--sin-git`.
- Pruebas: `python3 -m unittest discover pruebas`.

## Secretos y variables (Settings > Secrets and variables > Actions)

- Secretos: `IG_TOKEN`, `IG_USER_ID`, `GH_PAT_SECRETS` (token personal con permiso para escribir secretos y variables del repositorio; solo se usa para renovar el token).
- Variable: `IG_TOKEN_FECHA` (AAAA-MM-DD de la última emisión/renovación del token; el script lo renueva si pasaron más de 7 días).
