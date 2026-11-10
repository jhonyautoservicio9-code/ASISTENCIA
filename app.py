import csv
import io
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import qrcode
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template_string,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("DATA_DIR", APP_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "asistencias.db")

try:
    TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "America/Mexico_City"))
except ZoneInfoNotFoundError as exc:
    raise RuntimeError("APP_TIMEZONE debe ser una zona horaria IANA válida") from exc

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or "solo-desarrollo-cambia-SECRET_KEY"
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "0").lower() in ("1", "true", "yes"),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "CAMBIAR-ESTA-CONTRASENA")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")


def now():
    return datetime.now(TZ)


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with db() as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("""CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            token TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1))
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            event TEXT NOT NULL CHECK(event IN ('ENTRADA', 'SALIDA')),
            timestamp TEXT NOT NULL,
            FOREIGN KEY(employee_id) REFERENCES employees(id)
        )""")


init_db()


def csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]


def validate_csrf():
    expected = session.get("_csrf_token", "")
    provided = request.form.get("_csrf_token", "")
    if not expected or not secrets.compare_digest(expected, provided):
        abort(400, "Formulario vencido o inválido. Recarga la página e inténtalo de nuevo.")


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrapped


STYLE = """
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
*{box-sizing:border-box}body{font-family:Arial,sans-serif;background:#f3f5f7;color:#17212b;margin:0}
.wrap{max-width:1050px;margin:25px auto;padding:0 15px}.card{background:#fff;border-radius:14px;padding:21px;margin:14px 0;box-shadow:0 3px 14px #0000000c}
h1{font-size:25px;margin:0 0 8px}h2{font-size:19px;margin:0 0 13px}.muted{color:#667085}
input,select,button{font-size:15px;padding:11px;border-radius:8px;border:1px solid #ccd2d8}
button,.btn{background:#17212b;color:white;border:0;cursor:pointer;text-decoration:none;display:inline-block;padding:11px 15px;border-radius:8px}
.btn.secondary{background:#e8edf2;color:#17212b}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.row input{flex:1;min-width:170px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid #e8ebee;font-size:14px}
.notice{padding:12px;border-radius:8px;background:#edf5ff;margin:10px 0}.ok{background:#e8f7ed}.warn{background:#fff4e5}
.big{font-size:22px;font-weight:bold}.center{text-align:center}.small{font-size:13px}
@media(max-width:650px){.wrap{margin:12px auto}.card{padding:15px}table{display:block;overflow-x:auto;white-space:nowrap}h1{font-size:22px}}
</style>
"""


def get_range():
    today = now().date()
    start = request.args.get("start") or (today - timedelta(days=today.weekday())).isoformat()
    end = request.args.get("end") or today.isoformat()
    try:
        date.fromisoformat(start)
        date.fromisoformat(end)
    except ValueError:
        abort(400, "Fechas inválidas")
    if start > end:
        abort(400, "La fecha inicial no puede ser posterior a la final")
    return start, end


def safe_csv_cell(value):
    """Prevent spreadsheet formulas in user-controlled CSV fields."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def csv_download(rows, filename):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    for row in rows:
        writer.writerow([safe_csv_cell(value) for value in row])
    data = out.getvalue().encode("utf-8-sig")
    return send_file(
        io.BytesIO(data),
        mimetype="text/csv; charset=utf-8",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/")
def home():
    return redirect(url_for("admin"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        validate_csrf()
        if secrets.compare_digest(request.form.get("password", ""), ADMIN_PASSWORD):
            session.clear()
            session["admin"] = True
            session.permanent = True
            return redirect(url_for("admin"))
        flash("Contraseña incorrecta.")
    return render_template_string(STYLE + """
    <div class="wrap"><div class="card" style="max-width:430px;margin:65px auto">
    <h1>Autoservicio JHONY</h1><h2>Control de asistencia QR</h2>
    {% with ms=get_flashed_messages() %}{% for m in ms %}<div class="notice warn">{{m}}</div>{% endfor %}{% endwith %}
    <form method="post"><input type="hidden" name="_csrf_token" value="{{csrf}}">
    <input name="password" type="password" placeholder="Contraseña de administrador" required autocomplete="current-password" style="width:100%;margin:10px 0">
    <button style="width:100%">Iniciar sesión</button></form></div></div>""", csrf=csrf_token())


@app.post("/logout")
@admin_required
def logout():
    validate_csrf()
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin")
@admin_required
def admin():
    start, end = get_range()
    today = now().date().isoformat()
    with db() as con:
        employees = con.execute("SELECT * FROM employees ORDER BY name COLLATE NOCASE").fetchall()
        today_rows = con.execute("""SELECT a.*,e.name FROM attendance a JOIN employees e ON e.id=a.employee_id
            WHERE substr(a.timestamp,1,10)=? ORDER BY a.timestamp DESC""", (today,)).fetchall()
    return render_template_string(STYLE + """
    <div class="wrap">
      <div class="row" style="justify-content:space-between"><div><h1>Autoservicio JHONY</h1><div class="muted">Panel de asistencia · {{today}}</div></div>
      <form method="post" action="{{url_for('logout')}}"><input type="hidden" name="_csrf_token" value="{{csrf}}"><button class="btn secondary">Cerrar sesión</button></form></div>
      {% with ms=get_flashed_messages() %}{% for m in ms %}<div class="notice">{{m}}</div>{% endfor %}{% endwith %}
      <div class="card"><h2>Registrar trabajador</h2><form class="row" method="post" action="{{url_for('add_employee')}}">
        <input type="hidden" name="_csrf_token" value="{{csrf}}"><input name="name" placeholder="Nombre completo" maxlength="100" required><button>Agregar trabajador</button></form>
        <p class="muted small">Se generará un QR personal. Puedes abrirlo e imprimirlo desde la lista.</p></div>
      <div class="card"><h2>Trabajadores ({{employees|length}})</h2>
      {% if employees %}<table><tr><th>Nombre</th><th>Estado</th><th>QR</th><th>Acción</th></tr>
      {% for e in employees %}<tr><td><b>{{e.name}}</b></td><td>{{'Activo' if e.active else 'Inactivo'}}</td>
      <td><a href="{{url_for('qr_page', employee_id=e.id)}}" target="_blank" rel="noopener">Ver / imprimir QR</a></td><td>
      <form method="post" action="{{url_for('toggle_employee', employee_id=e.id)}}"><input type="hidden" name="_csrf_token" value="{{csrf}}"><button class="btn secondary">{{'Desactivar' if e.active else 'Activar'}}</button></form></td></tr>{% endfor %}</table>
      {% else %}<p class="muted">Aún no hay trabajadores.</p>{% endif %}</div>
      <div class="card"><h2>Movimientos de hoy</h2>
      {% if today_rows %}<table><tr><th>Trabajador</th><th>Movimiento</th><th>Hora local</th></tr>
      {% for r in today_rows %}<tr><td>{{r.name}}</td><td>{{r.event}}</td><td>{{r.timestamp[11:19]}}</td></tr>{% endfor %}</table>
      {% else %}<p class="muted">Todavía no hay movimientos registrados hoy.</p>{% endif %}</div>
      <div class="card"><h2>Reportes de asistencia</h2><p class="muted">Selecciona el periodo. Los archivos CSV se abren directamente con Excel.</p>
        <form class="row" method="get" action="{{url_for('admin')}}"><label>Desde <input type="date" name="start" value="{{start}}" required></label>
          <label>Hasta <input type="date" name="end" value="{{end}}" required></label><button>Aplicar periodo</button></form>
        <div class="row" style="margin-top:14px"><a class="btn" href="{{url_for('export_general', start=start, end=end)}}">Descargar reporte general</a>
          <form class="row" method="get" action="{{url_for('export_employee')}}"><input type="hidden" name="start" value="{{start}}"><input type="hidden" name="end" value="{{end}}">
            <select name="employee_id" required><option value="">Selecciona trabajador</option>{% for e in employees if e.active %}<option value="{{e.id}}">{{e.name}}</option>{% endfor %}</select>
            <button>Descargar reporte individual</button></form></div></div>
      <div class="card"><h2>Uso del sistema</h2><ol><li>Agrega a cada trabajador y abre su QR.</li><li>El trabajador escanea su código con la cámara del celular.</li>
      <li>Confirma el botón de registrar entrada o salida.</li><li>Descarga el reporte general o individual al cierre de semana.</li></ol>
      <div class="notice warn"><b>Nota:</b> el QR identifica al trabajador, pero no demuestra por sí solo que esté físicamente en el negocio. Evita compartir o publicar los códigos. Los registros usan la hora del servidor en zona horaria de Ciudad de México.</div></div>
    </div>""", employees=employees, today_rows=today_rows, today=today, start=start, end=end, csrf=csrf_token())


@app.post("/employee/add")
@admin_required
def add_employee():
    validate_csrf()
    name = request.form.get("name", "").strip()
    if not name or len(name) > 100:
        abort(400, "El nombre debe tener entre 1 y 100 caracteres")
    with db() as con:
        count = con.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        if count >= 100:
            flash("Se alcanzó el límite de 100 trabajadores.")
            return redirect(url_for("admin"))
        con.execute("INSERT INTO employees(name,token) VALUES(?,?)", (name, secrets.token_urlsafe(24)))
    flash("Trabajador agregado. Abre 'Ver / imprimir QR' para obtener su código.")
    return redirect(url_for("admin"))


@app.post("/employee/toggle/<int:employee_id>")
@admin_required
def toggle_employee(employee_id):
    validate_csrf()
    with db() as con:
        con.execute("UPDATE employees SET active=1-active WHERE id=?", (employee_id,))
    return redirect(url_for("admin"))


@app.route("/qr/<int:employee_id>")
@admin_required
def qr_page(employee_id):
    with db() as con:
        employee = con.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
    if not employee:
        abort(404)
    base = BASE_URL or request.url_root.rstrip("/")
    target = f"{base}{url_for('mark', token=employee['token'])}"
    image = qrcode.make(target)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name=f"QR_{employee_id}.png")


def next_move(lastrow, today):
    if lastrow and lastrow["event"] == "ENTRADA" and lastrow["timestamp"][:10] == today:
        return "SALIDA"
    return "ENTRADA"


@app.route("/marcar/<token>", methods=["GET", "POST"])
def mark(token):
    with db() as con:
        employee = con.execute("SELECT * FROM employees WHERE token=? AND active=1", (token,)).fetchone()
    if not employee:
        abort(404)

    if request.method == "POST":
        validate_csrf()
        stamp_dt = now()
        stamp = stamp_dt.isoformat(timespec="seconds")
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            last = con.execute("SELECT event,timestamp FROM attendance WHERE employee_id=? ORDER BY id DESC LIMIT 1", (employee["id"],)).fetchone()
            event = next_move(last, stamp[:10])
            if last:
                previous = datetime.fromisoformat(last["timestamp"])
                if previous.tzinfo is None:
                    previous = previous.replace(tzinfo=TZ)
                if 0 <= (stamp_dt - previous).total_seconds() < 45:
                    return render_template_string(STYLE + """<div class="wrap"><div class="card center"><h1>Registro reciente</h1>
                    <p>Ya se registró un movimiento hace unos segundos. Espera antes de volver a escanear.</p></div></div>""")
            con.execute("INSERT INTO attendance(employee_id,event,timestamp) VALUES(?,?,?)", (employee["id"], event, stamp))
        return render_template_string(STYLE + """
        <div class="wrap"><div class="card center" style="max-width:520px;margin:45px auto"><h1>¡Registro realizado!</h1>
        <div class="notice ok big">{{event}} registrada</div><p><b>{{name}}</b></p><p class="big">{{stamp}}</p><p class="muted">Puedes cerrar esta página.</p></div></div>""",
        event=event, name=employee["name"], stamp=stamp.replace("T", " "))

    today = now().date().isoformat()
    with db() as con:
        last = con.execute("SELECT event,timestamp FROM attendance WHERE employee_id=? ORDER BY id DESC LIMIT 1", (employee["id"],)).fetchone()
    event = next_move(last, today)
    return render_template_string(STYLE + """
    <div class="wrap"><div class="card center" style="max-width:520px;margin:45px auto"><h1>Autoservicio JHONY</h1><h2>Registro de asistencia</h2>
    <p class="muted">Trabajador</p><div class="big">{{name}}</div><div class="notice">Siguiente movimiento: <b>{{event}}</b></div>
    {% if last %}<p class="muted small">Último movimiento: {{last.event}} · {{last.timestamp.replace('T',' ')}}</p>{% endif %}
    <form method="post"><input type="hidden" name="_csrf_token" value="{{csrf}}"><button style="font-size:20px;width:100%;padding:18px">Confirmar {{event}}</button></form>
    <p class="muted small">Confirma solo al llegar o al terminar tu jornada.</p></div></div>""", name=employee["name"], event=event, last=last, csrf=csrf_token())


@app.route("/export/general")
@admin_required
def export_general():
    start, end = get_range()
    with db() as con:
        rows = con.execute("""SELECT e.name,a.event,a.timestamp FROM attendance a JOIN employees e ON e.id=a.employee_id
            WHERE substr(a.timestamp,1,10) BETWEEN ? AND ? ORDER BY e.name,a.timestamp""", (start, end)).fetchall()
        summary = con.execute("""SELECT e.name,
            SUM(CASE WHEN a.event='ENTRADA' THEN 1 ELSE 0 END) entradas,
            SUM(CASE WHEN a.event='SALIDA' THEN 1 ELSE 0 END) salidas
            FROM employees e LEFT JOIN attendance a ON a.employee_id=e.id
            AND substr(a.timestamp,1,10) BETWEEN ? AND ? GROUP BY e.id ORDER BY e.name""", (start, end)).fetchall()
    report = [["REPORTE GENERAL DE ASISTENCIA", f"{start} a {end}"], [], ["RESUMEN POR TRABAJADOR", "Entradas", "Salidas"]]
    report.extend([[r["name"], r["entradas"] or 0, r["salidas"] or 0] for r in summary])
    report.extend([[], ["DETALLE DE MOVIMIENTOS"], ["Trabajador", "Movimiento", "Fecha y hora"]])
    report.extend([[r["name"], r["event"], r["timestamp"].replace("T", " ")] for r in rows])
    return csv_download(report, f"asistencia_general_{start}_a_{end}.csv")


@app.route("/export/employee")
@admin_required
def export_employee():
    start, end = get_range()
    try:
        employee_id = int(request.args.get("employee_id", ""))
    except ValueError:
        abort(400, "Selecciona un trabajador")
    with db() as con:
        employee = con.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
        if not employee:
            abort(404)
        rows = con.execute("""SELECT event,timestamp FROM attendance WHERE employee_id=?
            AND substr(timestamp,1,10) BETWEEN ? AND ? ORDER BY timestamp""", (employee_id, start, end)).fetchall()
    entries = sum(1 for row in rows if row["event"] == "ENTRADA")
    exits = sum(1 for row in rows if row["event"] == "SALIDA")
    report = [["REPORTE INDIVIDUAL DE ASISTENCIA"], ["Trabajador", employee["name"]],
              ["Periodo", f"{start} a {end}"], ["Total de entradas", entries], ["Total de salidas", exits],
              [], ["Movimiento", "Fecha y hora"]]
    report.extend([[row["event"], row["timestamp"].replace("T", " ")] for row in rows])
    return csv_download(report, f"asistencia_{employee_id}_{start}_a_{end}.csv")


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
