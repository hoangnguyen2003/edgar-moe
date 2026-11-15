import json
import subprocess

import pytest

from scripts import verify_restore_schema as schema

URL = "postgresql://reader:synthetic%40password@example.test/isolated?sslrootcert=%2Fca.pem"


@pytest.mark.parametrize("returncode", [0, 1, 2])
def test_schema_report_never_retains_raw_success_or_failure_output(monkeypatch, returncode):
    def run(command, **kwargs):
        assert command == [schema.sys.executable, "-m", "alembic", "check"]
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] == 60
        assert kwargs["env"]["EDGAR_MOE_REGISTRY_DATABASE_URL"] == URL
        assert "default_transaction_read_only=on" in kwargs["env"]["PGOPTIONS"]
        assert "statement_timeout=10000" in kwargs["env"]["PGOPTIONS"]
        return subprocess.CompletedProcess(command, returncode, stdout=URL, stderr=URL)

    monkeypatch.setattr(schema.subprocess, "run", run)
    report = schema.verify_schema(URL)
    assert report["status"] == ("passed" if returncode == 0 else "failed")
    assert URL not in json.dumps(report)
    assert "synthetic" not in json.dumps(report)
    assert report["raw_diagnostics_retained"] is False


@pytest.mark.parametrize(
    "error", [subprocess.TimeoutExpired("check", 60, output=URL), OSError(URL)]
)
def test_schema_exception_diagnostics_remain_private(monkeypatch, error):
    def run(*args, **kwargs):
        raise error

    monkeypatch.setattr(schema.subprocess, "run", run)
    report = schema.verify_schema(URL)
    assert report["status"] == "failed"
    assert URL not in json.dumps(report)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not-a-url",
        "sqlite:///file",
        URL + "&options=-c%20default_transaction_read_only%3Doff",
        URL + "&dbname=other",
        URL + "&hostaddr=127.0.0.1",
        URL + "&service=override",
    ],
)
def test_schema_rejects_missing_or_non_postgres_configuration(monkeypatch, url):
    def run(*args, **kwargs):
        pytest.fail("invalid configuration must not start a subprocess")

    monkeypatch.setattr(schema.subprocess, "run", run)
    assert schema.verify_schema(url)["reason"] == "postgresql_target_required"


def test_schema_cli_failure_is_json_and_nonzero(monkeypatch, capsys):
    monkeypatch.setenv("EDGAR_MOE_REGISTRY_DATABASE_URL", "")
    assert schema.main() == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
