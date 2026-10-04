import json
from unittest.mock import MagicMock

import pytest

from scripts import verify_restore_identities as identities


def url(host="ep-source.example.neon.tech", database="fixture", role="owner"):
    return f"postgresql://{role}:synthetic-secret@{host}:5432/{database}?sslmode=verify-full"


def environment():
    return {
        "CONFIRM_ISOLATED_TARGET": "I_UNDERSTAND_ISOLATED_TARGET",
        "SOURCE_DATABASE_URL": url(),
        "TARGET_DATABASE_URL": url("ep-target.example.neon.tech"),
        "SOURCE_AUDITOR_DATABASE_URL": url(role="auditor"),
        "TARGET_AUDITOR_DATABASE_URL": url("ep-target.example.neon.tech", role="reader"),
    }


@pytest.mark.parametrize(
    "database_url",
    [
        "",
        "host=example dbname=fixture user=owner password=synthetic",
        url() + "&host=override.example",
        url() + "&hostaddr=127.0.0.1",
        url() + "&dbname=other",
        url() + "&user=other",
        url() + "&service=other",
        url() + "&options=-crole=other",
        url() + "&sslmode=require",
        url() + "#ignored",
        url().replace("verify-full", "disable"),
        url().replace(":synthetic-secret", ""),
        url().replace("owner", ""),
        url(database=""),
        url(host="first.example,second.example"),
        url(database="fixture%0Aname"),
        url(role="owner%0Aname"),
        url().replace(":5432", ":0"),
        url().replace(":5432", ":65536"),
    ],
)
def test_endpoint_rejects_ambiguous_or_unsafe_urls(database_url):
    with pytest.raises(identities.RestoreIdentityError, match="^invalid_database_url$"):
        identities.endpoint(database_url)


def test_only_documented_neon_pooler_alias_is_normalized():
    assert identities.endpoint(url("ep-source-pooler.example.neon.tech")) == identities.endpoint(
        url()
    )
    assert identities.endpoint(url("EP-SOURCE.EXAMPLE.NEON.TECH")) == identities.endpoint(url())
    assert identities.endpoint(url("ep-source-pooler.example.invalid"))[0].endswith(".invalid")
    assert (
        identities.endpoint(url("ep-source-pooler.example.invalid"))[0]
        != identities.endpoint(url("ep-source.example.invalid"))[0]
    )


@pytest.mark.parametrize(
    "variable,value,reason",
    [
        ("CONFIRM_ISOLATED_TARGET", "CANCEL", "explicit_confirmation_required"),
        ("SOURCE_DATABASE_URL", "", "source_invalid_database_url"),
        ("TARGET_DATABASE_URL", url(database="different"), "source_target_endpoint_not_distinct"),
        (
            "TARGET_DATABASE_URL",
            url().replace(":5432", ":5433"),
            "source_target_endpoint_not_distinct",
        ),
        (
            "TARGET_DATABASE_URL",
            url("ep-source-pooler.example.neon.tech"),
            "source_target_endpoint_not_distinct",
        ),
        (
            "SOURCE_AUDITOR_DATABASE_URL",
            url(database="other", role="auditor"),
            "source_auditor_endpoint_mismatch",
        ),
        (
            "SOURCE_AUDITOR_DATABASE_URL",
            url("wrong.example", role="auditor"),
            "source_auditor_endpoint_mismatch",
        ),
        (
            "SOURCE_AUDITOR_DATABASE_URL",
            url(role="auditor").replace(":5432", ":5433"),
            "source_auditor_endpoint_mismatch",
        ),
        ("TARGET_AUDITOR_DATABASE_URL", url(role="reader"), "target_auditor_endpoint_mismatch"),
        ("SOURCE_AUDITOR_DATABASE_URL", url(), "source_writer_auditor_role_conflict"),
        (
            "TARGET_AUDITOR_DATABASE_URL",
            url("ep-target.example.neon.tech"),
            "target_writer_auditor_role_conflict",
        ),
    ],
)
def test_static_failures_never_connect(monkeypatch, variable, value, reason):
    observe = MagicMock()
    monkeypatch.setattr(identities, "observe", observe)
    configured = environment()
    configured[variable] = value
    report = identities.verify(configured)
    assert report["status"] == "not_ready"
    assert report["reason"] == reason
    observe.assert_not_called()


def test_success_observes_all_four_and_only_target_emptiness(monkeypatch):
    observe = MagicMock(return_value=18)
    monkeypatch.setattr(identities, "observe", observe)
    configured = environment()
    configured["SOURCE_AUDITOR_DATABASE_URL"] = url(
        "ep-source-pooler.example.neon.tech", role="auditor"
    )
    report = identities.verify(configured)
    assert report["status"] == "ready"
    assert report["reason"] is None
    assert report["target_table_count"] == 0
    assert report["server_majors"] == dict.fromkeys(identities.DATABASE_VARIABLES, 18)
    assert [call.kwargs["empty"] for call in observe.call_args_list] == [False, True, False, False]
    encoded = json.dumps(report)
    for private in ("synthetic-secret", "neon.tech", "fixture", "owner", "reader"):
        assert private not in encoded


@pytest.mark.parametrize("label", identities.DATABASE_VARIABLES)
def test_connection_errors_are_redacted_and_stop_observations(monkeypatch, label):
    index = list(identities.DATABASE_VARIABLES).index(label)
    observe = MagicMock(side_effect=[*[18] * index, RuntimeError(url())])
    monkeypatch.setattr(identities, "observe", observe)
    report = identities.verify(environment())
    assert report["reason"] == f"{label}_connection_or_observation_failed"
    assert report["status"] == "not_ready"
    assert observe.call_count == index + 1
    assert "synthetic-secret" not in json.dumps(report)
    assert "neon.tech" not in json.dumps(report)


def connection_fixture(monkeypatch, row=("fixture", "owner", "owner", 180006), count=(0,)):
    connection = MagicMock()
    connection.execute.return_value.fetchone.side_effect = [row, count]
    connect = MagicMock()
    connect.return_value.__enter__.return_value = connection
    monkeypatch.setattr(identities.psycopg, "connect", connect)
    return connection, connect


def test_observations_are_read_only_bounded_and_include_nonpublic_schemas(monkeypatch):
    connection, connect = connection_fixture(monkeypatch)
    assert identities.observe(url(), identities.endpoint(url()), empty=True) == 18
    connect.assert_called_once_with(url(), connect_timeout=10, autocommit=True)
    queries = [call.args[0] for call in connection.execute.call_args_list]
    assert queries[0] == "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"
    assert queries[1:3] == ["SET LOCAL statement_timeout='10s'", "SET LOCAL lock_timeout='1s'"]
    assert "n.nspname !~ '^pg_'" in queries[4]
    assert "inet_server_addr" not in " ".join(queries)
    assert queries[-1] == "ROLLBACK"


@pytest.mark.parametrize(
    "row,count,reason",
    [
        (None, (0,), "observed_database_or_role_mismatch"),
        (("other", "owner", "owner", 180006), (0,), "observed_database_or_role_mismatch"),
        (("fixture", "other", "owner", 180006), (0,), "observed_database_or_role_mismatch"),
        (("fixture", "owner", "other", 180006), (0,), "observed_database_or_role_mismatch"),
        (("fixture", "owner", "owner", 180006), (1,), "isolated_target_not_empty"),
        (("fixture", "owner", "owner", 180006), None, "isolated_target_not_empty"),
        (("fixture", "owner", "owner", 999), (0,), "invalid_server_version"),
    ],
)
def test_observed_identity_and_emptiness_fail_closed(monkeypatch, row, count, reason):
    connection_fixture(monkeypatch, row, count)
    with pytest.raises(identities.RestoreIdentityError, match=f"^{reason}$"):
        identities.observe(url(), identities.endpoint(url()), empty=True)


def test_failed_observed_identity_retains_specific_code(monkeypatch):
    monkeypatch.setattr(
        identities,
        "observe",
        MagicMock(
            side_effect=identities.RestoreIdentityError("observed_database_or_role_mismatch")
        ),
    )
    report = identities.verify(environment())
    assert report["reason"] == "source_observed_database_or_role_mismatch"


def test_cli_retains_failure_and_refuses_report_overwrite(tmp_path, monkeypatch, capsys):
    output = tmp_path / "preflight.json"
    monkeypatch.setattr("sys.argv", ["verify_restore_identities.py", "--output", str(output)])
    monkeypatch.setenv("CONFIRM_ISOLATED_TARGET", "CANCEL")
    assert identities.main() == 1
    original = output.read_bytes()
    assert json.loads(original)["reason"] == "explicit_confirmation_required"
    assert identities.main() == 2
    assert output.read_bytes() == original
    assert "report_write_failed" in capsys.readouterr().out
