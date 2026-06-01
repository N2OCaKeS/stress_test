import os
import psycopg2
import psycopg2.extras
from flask import Flask, render_template, g, request, abort

app = Flask(__name__)

DB_HOST = os.environ.get("DB_HOST", "pgpool.balance.rbt")
DB_PORT = int(os.environ.get("DB_PORT", "5440"))
DB_NAME = "protopack"
DB_USER = os.environ.get("DB_USER", "protopack_web")


def get_db():
    if "db" not in g:
        # Соединение открывается из процесса с MAC-меткой пользователя (Apache AstraMode).
        g.db = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
    return g.db


@app.teardown_appcontext
def close_db(error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.before_request
def load_user():
    # Apache GSSAPI кладёт принципал в REMOTE_USER в виде user@REALM
    raw = request.environ.get("REMOTE_USER", "")
    g.user = raw.split("@")[0] if raw else "anonymous"


@app.route("/")
def index():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT build, build_type, rel, create_utc, count_packages
            FROM main.build_info
            ORDER BY create_utc DESC NULLS LAST
            LIMIT 100
        """)
        builds = cur.fetchall()
    return render_template("index.html", builds=builds)


@app.route("/build/<path:build_id>")
def build_detail(build_id):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT build, build_type, rel, create_utc, count_packages, repo, mount_point
            FROM main.build_info
            WHERE build = %s
        """, (build_id,))
        build_info = cur.fetchone()

    if build_info is None:
        abort(404)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT component, binary, binary_version, source, source_version,
                   section, department, responsible, licence
            FROM main.build_packages
            WHERE build = %s
            ORDER BY component, binary
        """, (build_id,))
        packages = cur.fetchall()

    return render_template("build.html", build_info=build_info, packages=packages)


@app.route("/sources/<path:build_id>")
def build_sources(build_id):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT source, source_version, description, repository,
                   binary_packages, department, sdk
            FROM main.build_sources
            WHERE build = %s
            ORDER BY source
        """, (build_id,))
        sources = cur.fetchall()

    if not sources:
        abort(404)

    return render_template("sources.html", build_id=build_id, sources=sources)


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404
