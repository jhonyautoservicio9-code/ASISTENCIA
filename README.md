# Autoservicio JHONY — asistencia por QR

Aplicación Flask para administrar trabajadores, registrar entradas y salidas con códigos QR y descargar reportes CSV.

## Ejecutar localmente

Necesitas Python 3.10 o posterior.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
```

Configura una contraseña y una clave secreta antes de iniciar. En Linux/macOS:

```bash
export ADMIN_PASSWORD='una-contrasena-segura'
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python app.py
```

En Windows PowerShell, usa `$env:ADMIN_PASSWORD='una-contrasena-segura'` y `$env:SECRET_KEY='una-clave-larga-y-aleatoria'`. Abre <http://127.0.0.1:5000> y entra con la contraseña configurada. La base SQLite se crea como `asistencias.db` junto a `app.py` por defecto. Puedes cambiar su ubicación con `DATA_DIR` y la zona horaria con `APP_TIMEZONE`.

## Publicar en Render

El archivo `render.yaml` configura el servicio web, Gunicorn y el disco persistente para SQLite. Al crear el servicio, define `ADMIN_PASSWORD` en el panel de Render. `SECRET_KEY` se genera automáticamente. Para una URL pública estable de los QR, puedes configurar `BASE_URL` con la URL HTTPS del servicio (sin `/` al final); si no se configura, se usa el host de la solicitud que generó el QR.

## Uso

1. Inicia sesión y agrega trabajadores.
2. Abre el QR de cada trabajador e imprímelo o guárdalo.
3. El trabajador escanea el QR, revisa el movimiento propuesto y lo confirma.
4. Descarga el reporte general o el reporte individual en CSV desde el panel.

El sistema registra hora local en `APP_TIMEZONE` (por defecto `America/Mexico_City`) y evita registros repetidos durante 45 segundos. El QR identifica al trabajador, pero no comprueba su ubicación física. Mantén privados los códigos.
