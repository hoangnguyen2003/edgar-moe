"""Validate the bounded artifact selection for the diagnostic-history workflow."""

from __future__ import annotations

import argparse
import re
import sys

MAX_SELECTIONS = 8
_RUN_ID = re.compile(r"[1-9][0-9]*")
_ARTIFACT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def validate_selection(
    *, run_ids: str, artifact_names: str, minimum_reports: str
) -> tuple[str, ...]:
    """Return stable, value-free validation codes for a workflow selection."""

    errors: list[str] = []
    parsed_runs = _split_csv(run_ids)
    parsed_artifacts = _split_csv(artifact_names)

    if not parsed_runs:
        errors.append("run_ids_missing")
    elif len(parsed_runs) > MAX_SELECTIONS:
        errors.append("selection_count_exceeded")
    elif any(_RUN_ID.fullmatch(value) is None for value in parsed_runs):
        errors.append("run_id_invalid")
    elif len(set(parsed_runs)) != len(parsed_runs):
        errors.append("run_id_duplicate")

    if not parsed_artifacts:
        errors.append("artifact_names_missing")
    elif len(parsed_artifacts) > MAX_SELECTIONS:
        errors.append("selection_count_exceeded")
    elif any(_ARTIFACT_NAME.fullmatch(value) is None for value in parsed_artifacts):
        errors.append("artifact_name_invalid")
    elif len(set(parsed_artifacts)) != len(parsed_artifacts):
        errors.append("artifact_name_duplicate")

    if parsed_runs and parsed_artifacts and len(parsed_runs) != len(parsed_artifacts):
        errors.append("selection_lengths_mismatch")

    if not re.fullmatch(r"[1-9][0-9]*", minimum_reports):
        errors.append("minimum_reports_invalid")
    elif not 1 <= int(minimum_reports) <= MAX_SELECTIONS:
        errors.append("minimum_reports_out_of_range")
    elif parsed_runs and int(minimum_reports) > len(parsed_runs):
        errors.append("minimum_reports_exceeds_selection")

    return tuple(dict.fromkeys(errors))


def _split_csv(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value:
        return ()
    values = tuple(value.split(","))
    if any(not item or item != item.strip() for item in values):
        return ()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-ids", required=True)
    parser.add_argument("--artifact-names", required=True)
    parser.add_argument("--minimum-reports", required=True)
    args = parser.parse_args()
    errors = validate_selection(
        run_ids=args.run_ids,
        artifact_names=args.artifact_names,
        minimum_reports=args.minimum_reports,
    )
    if errors:
        print("forward diagnostic history selection is invalid", file=sys.stderr)
        print("reason_codes=" + ",".join(errors), file=sys.stderr)
        return 1
    print("forward diagnostic history selection is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
