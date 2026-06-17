import os
from datetime import date, datetime
from decimal import Decimal

import pymysql
from flask import g, current_app
from werkzeug.security import generate_password_hash, check_password_hash


class Row(dict):
    """Строка результата: доступ и по имени, и по индексу."""
    def __init__(self, columns, values):
        converted = [self._convert(v) for v in values]
        super().__init__(zip(columns, converted))
        self._values = converted

    @staticmethod
    def _convert(value):
        if isinstance(value, (date, datetime)):
            return value.isoformat(sep=' ') if isinstance(value, datetime) else value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        return value

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


class CursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor
        self.columns = [d[0] for d in cursor.description] if cursor.description else []
        self.lastrowid = cursor.lastrowid
        self.rowcount = cursor.rowcount

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is None:
            return None
        return Row(self.columns, row)

    def fetchall(self):
        return [Row(self.columns, row) for row in self.cursor.fetchall()]


class MySQLConnectionWrapper:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        sql = sql.strip()
        upper = sql.upper()
        if upper in ('BEGIN', 'BEGIN EXCLUSIVE'):
            sql = 'START TRANSACTION'
        cur = self.conn.cursor()
        cur.execute(sql, params or ())
        return CursorWrapper(cur)

    def executescript(self, script):
        for statement in split_sql_script(script):
            if not statement:
                continue
            try:
                self.execute(statement)
            except pymysql.err.OperationalError as exc:
                # Повторный запуск проекта не должен падать на уже созданных индексах.
                if exc.args and exc.args[0] == 1061:
                    continue
                raise

    def close(self):
        self.conn.close()


def split_sql_script(script):
    """Простое разбиение SQL-файла на команды по ; вне строк."""
    statements = []
    buf = []
    quote = None
    i = 0
    while i < len(script):
        ch = script[i]
        nxt = script[i + 1] if i + 1 < len(script) else ''

        if quote is None and ch == '-' and nxt == '-':
            while i < len(script) and script[i] not in '\r\n':
                i += 1
            continue

        if ch in ("'", '"'):
            if quote is None:
                quote = ch
            elif quote == ch:
                # SQL escaping by doubled quotes: ''
                if i + 1 < len(script) and script[i + 1] == ch:
                    buf.append(ch)
                    i += 1
                else:
                    quote = None

        if ch == ';' and quote is None:
            statement = ''.join(buf).strip()
            if statement:
                statements.append(statement)
            buf = []
        else:
            buf.append(ch)
        i += 1

    tail = ''.join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _mysql_config(with_database=True):
    cfg = {
        'host': os.environ.get('MYSQL_HOST', '127.0.0.1'),
        'port': int(os.environ.get('MYSQL_PORT', '3306')),
        'user': os.environ.get('MYSQL_USER', 'root'),
        'password': os.environ.get('MYSQL_PASSWORD', ''),
        'charset': 'utf8mb4',
        'autocommit': True,
    }
    if with_database:
        cfg['database'] = os.environ.get('MYSQL_DATABASE', 'hotel_db')
    return cfg


def _ensure_database_exists():
    database = os.environ.get('MYSQL_DATABASE', 'hotel_db')
    cfg = _mysql_config(with_database=False)
    conn = pymysql.connect(**cfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        conn.close()


def get_db():
    if 'db' not in g:
        _ensure_database_exists()
        conn = pymysql.connect(**_mysql_config(with_database=True))
        g.db = MySQLConnectionWrapper(conn)
    return g.db


def close_db(exc=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    schema = os.path.join(os.path.dirname(current_app.root_path), 'schema.sql')
    with open(schema, encoding='utf-8') as f:
        db.executescript(f.read())

    seed = os.path.join(os.path.dirname(current_app.root_path), 'seed.sql')
    if os.path.exists(seed):
        cats = db.execute("SELECT COUNT(*) FROM room_categories").fetchone()[0]
        if cats == 0:
            with open(seed, encoding='utf-8') as f:
                db.executescript(f.read())

    admin = db.execute("SELECT id, password_hash FROM users WHERE username='admin'").fetchone()
    if not admin:
        db.execute(
            "INSERT INTO users(username,password_hash,full_name,role) VALUES(%s,%s,%s,%s)",
            ('admin', generate_password_hash('admin123'), 'Администратор системы', 'admin')
        )
    elif not check_password_hash(admin['password_hash'], 'admin123') and str(admin['password_hash']).startswith('pbkdf2:sha256:260000$rHv5Q2Zk$'):
        db.execute(
            "UPDATE users SET password_hash=%s, full_name=%s, role=%s WHERE username=%s",
            (generate_password_hash('admin123'), 'Администратор системы', 'admin', 'admin')
        )


def audit(action, entity=None, entity_id=None, details=None):
    """Записать действие в журнал."""
    try:
        from flask_login import current_user
        uid = current_user.id if current_user.is_authenticated else None
    except Exception:
        uid = None
    get_db().execute(
        "INSERT INTO audit_log(user_id,action,entity,entity_id,details) VALUES(%s,%s,%s,%s,%s)",
        (uid, action, entity, entity_id, details)
    )
