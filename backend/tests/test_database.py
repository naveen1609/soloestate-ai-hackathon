from unittest.mock import MagicMock

import psycopg
import pytest
from pydantic import ValidationError
from pglast import parse_sql

from app.config import Settings, EnvironmentFileError
from app.database import connection_options, DatabaseConfigurationError
from app.migrations import SQL_ROOT, migration_files, _checksum, _validate_history, MigrationError
from scripts import database


def config(**overrides):
    return Settings(_env_file=None, **overrides)


def test_database_url_missing():
    with pytest.raises(DatabaseConfigurationError, match="Set DATABASE_URL"):
        connection_options(config(database_url=None))


@pytest.mark.parametrize("path", migration_files() + [SQL_ROOT / "seed.sql"])
def test_postgresql_sql_syntax(path):
    # PostgreSQL's parser checks grammar; real DB integration tests check semantics.
    assert parse_sql(path.read_text(encoding="utf-8"))


def test_secret_and_tls_options():
    settings = config(database_url="postgresql://owner:password@example.invalid/postgres?sslmode=disable",
                      database_sslrootcert="C:/certs/root.crt")
    options = connection_options(settings)
    assert options["sslmode"] == "verify-full"
    assert options["sslrootcert"] == "C:/certs/root.crt"
    assert options["connect_timeout"] == 10
    assert options["prepare_threshold"] is None
    assert "password" not in repr(settings)


def test_migration_connection_is_separate():
    settings = config(database_url="postgresql://runtime:secret@runtime.invalid/db",
                      migration_database_url="postgresql://admin:secret@admin.invalid/db")
    assert connection_options(settings)["user"] == "runtime"
    assert connection_options(settings, migration=True)["user"] == "admin"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_local_ssl_disable(host):
    assert connection_options(config(database_url=f"postgresql://test:secret@{host}/test", database_sslmode="disable"))["sslmode"] == "disable"


def test_remote_ssl_disable_rejected():
    with pytest.raises(DatabaseConfigurationError, match="loopback"):
        connection_options(config(database_url="postgresql://test:secret@remote.invalid/test", database_sslmode="disable"))


def test_invalid_connection_string_is_safe():
    with pytest.raises(DatabaseConfigurationError) as exc:
        connection_options(config(database_url="not-a-uri-secret"))
    assert "not-a-uri-secret" not in str(exc.value)


def test_unescaped_password_at_rejected_before_dns():
    with pytest.raises(DatabaseConfigurationError, match="percent-encode") as exc:
        connection_options(config(database_url="postgresql://owner:secret@password@db.example.invalid/postgres"))
    assert "secret@password" not in str(exc.value)


def test_malformed_env_fails_without_printing_secrets(tmp_path, caplog):
    path = tmp_path / ".env"
    path.write_text('MODEL_API_KEY=private-secret\nDATABASE_SSLROOTCERT=""C:/certs/root.crt""\n')
    with pytest.raises(EnvironmentFileError, match="line 2") as exc:
        Settings(_env_file=path)
    assert "private-secret" not in str(exc.value)
    assert "C:/certs" not in str(exc.value)
    assert "could not parse" not in caplog.text


def test_quoted_windows_certificate_path(tmp_path):
    path = tmp_path / ".env"
    path.write_text("DATABASE_SSLROOTCERT='C:/certs/root.crt'\n")
    assert Settings(_env_file=path).database_sslrootcert == "C:/certs/root.crt"


def test_cli_env_syntax_error_is_actionable(monkeypatch, capsys):
    def invalid_settings():
        raise EnvironmentFileError("Invalid .env syntax at line 7; check matching quotes.")
    monkeypatch.setattr(database, "Settings", invalid_settings)
    assert database.run("check") == 1
    output = capsys.readouterr().out
    assert '"code": "configuration"' in output
    assert "line 7" in output


def test_invalid_sslmode():
    with pytest.raises(ValidationError):
        config(database_sslmode="prefer")


def test_migration_files_and_line_endings(tmp_path):
    folder = tmp_path / "migrations"
    folder.mkdir()
    first = folder / "0001_initial.sql"
    first.write_bytes(b"SELECT 1;\n")
    checksum = _checksum(first)
    first.write_bytes(b"SELECT 1;\r\n")
    assert _checksum(first) == checksum
    assert migration_files(tmp_path) == [first]
    (folder / "0001_duplicate.sql").write_text("SELECT 2;")
    with pytest.raises(MigrationError, match="Duplicate"):
        migration_files(tmp_path)


def test_applied_migration_drift_rejected(tmp_path):
    path = tmp_path / "0001_initial.sql"
    path.write_text("SELECT 1;")
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [(path.name, "changed-checksum")]
    with pytest.raises(MigrationError, match="differs"):
        _validate_history(conn, [path])


def test_cli_missing_credentials(monkeypatch, capsys):
    monkeypatch.setattr(database, "Settings", lambda: config(database_url=None, migration_database_url=None))
    assert database.run("check") == 1
    assert '"status": "error"' in capsys.readouterr().out


def test_cli_commit_failure_does_not_report_success(monkeypatch, capsys):
    conn = MagicMock()
    conn.__enter__.return_value.execute.return_value.fetchone.return_value = (1,)
    conn.__exit__.side_effect = psycopg.OperationalError("secret-database-password")
    monkeypatch.setattr(database, "connect", lambda *args, **kwargs: conn)
    assert database.run("check") == 1
    output = capsys.readouterr().out
    assert '"status": "ok"' not in output
    assert "secret-database-password" not in output
