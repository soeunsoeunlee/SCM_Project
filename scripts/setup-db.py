"""Create a private local PostgreSQL cluster without displaying credentials."""
from pathlib import Path
import os
import secrets
import subprocess

root = Path(__file__).resolve().parents[1]
local = root / '.local'
bin_dir = local / 'postgresql' / 'pgsql' / 'bin'
data = local / 'pgdata'
env_file = root / '.env'
if not bin_dir.joinpath('initdb.exe').exists():
    raise SystemExit('PostgreSQL binaries missing. See README.md.')
if not data.joinpath('PG_VERSION').exists():
    if env_file.exists():
        raise SystemExit('Existing .env preserved. Configure database manually; see README.md.')
    password = secrets.token_urlsafe(32)
    pwfile = local / 'init-password'
    pwfile.write_text(password, encoding='utf-8')
    try:
        subprocess.run([str(bin_dir / 'initdb.exe'), '-D', str(data), '-U', 'scm',
                        '--auth=scram-sha-256', '--encoding=UTF8', '--locale=C',
                        '--pwfile', str(pwfile)], check=True)
        env_file.write_text(f'DATABASE_URL=postgresql+psycopg://scm:{password}@127.0.0.1:5433/scm_demo\n', encoding='utf-8')
    finally:
        pwfile.unlink(missing_ok=True)
status = subprocess.run([str(bin_dir / 'pg_ctl.exe'), '-D', str(data), 'status'], capture_output=True)
if status.returncode:
    subprocess.run([str(bin_dir / 'pg_ctl.exe'), '-D', str(data), '-l', str(local / 'postgres.log'),
                    '-o', '-h 127.0.0.1 -p 5433', '-w', 'start'], check=True)
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
url = dotenv_values(env_file)['DATABASE_URL']
engine = create_engine(url.rsplit('/', 1)[0] + '/postgres', isolation_level='AUTOCOMMIT')
with engine.connect() as conn:
    if not conn.scalar(text("SELECT 1 FROM pg_database WHERE datname='scm_demo'")):
        conn.execute(text('CREATE DATABASE scm_demo'))
print('Local PostgreSQL ready at 127.0.0.1:5433. Credentials stored only in ignored .env.')
