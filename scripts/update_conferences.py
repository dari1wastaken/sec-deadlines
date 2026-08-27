#!/usr/bin/env python3
"""Merge updates into an existing conference list.

Only conferences already present in OLD_FILE are updated.  Conference identity
is the pair (name, year), and the old value of ``tags`` is always retained.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, TextIO, Tuple

import yaml


Conference = Dict[str, Any]
ConferenceKey = Tuple[Any, Any]


def conference_key(conference: Mapping[str, Any], source: str) -> ConferenceKey:
    """Return a conference's identity, rejecting entries that cannot be matched."""
    missing = [field for field in ("name", "year") if field not in conference]
    if missing:
        raise ValueError(
            f"{source}: conference entry is missing {', '.join(missing)}: "
            f"{conference!r}"
        )
    return conference["name"], conference["year"]


def index_conferences(
    conferences: Sequence[Conference], source: str
) -> Dict[ConferenceKey, Conference]:
    """Index conferences and reject ambiguous duplicate identities."""
    indexed: Dict[ConferenceKey, Conference] = {}
    for conference in conferences:
        if not isinstance(conference, dict):
            raise ValueError(f"{source}: expected a mapping, got {conference!r}")
        key = conference_key(conference, source)
        if key in indexed:
            raise ValueError(
                f"{source}: duplicate conference with name={key[0]!r}, year={key[1]!r}"
            )
        indexed[key] = conference
    return indexed


def merge_conferences(
    old_conferences: Sequence[Conference], new_conferences: Sequence[Conference]
) -> List[Conference]:
    """Update matching old entries while retaining old order, fields, and tags."""
    # Validate both files, including duplicate old keys.  New-only entries are
    # intentionally left unused.
    index_conferences(old_conferences, "old file")
    new_by_key = index_conferences(new_conferences, "new file")

    merged: List[Conference] = []
    for old_conference in old_conferences:
        result = dict(old_conference)
        update = new_by_key.get(conference_key(old_conference, "old file"))
        if update is not None:
            result.update({key: value for key, value in update.items() if key != "tags"})
        merged.append(result)
    return merged


def load_conferences(path: Path) -> List[Conference]:
    with path.open("r", encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    if not isinstance(value, list):
        raise ValueError(f"{path}: expected a top-level YAML list")
    return value


def dump_conferences(conferences: Sequence[Conference], stream: TextIO) -> None:
    yaml.safe_dump(
        list(conferences),
        stream,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=1000,
    )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_file", type=Path, help="currently maintained YAML file")
    parser.add_argument("new_file", type=Path, help="YAML file containing updates")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="write here instead of stdout (may be the same path as OLD_FILE)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        merged = merge_conferences(
            load_conferences(args.old_file), load_conferences(args.new_file)
        )
        if args.output is None:
            dump_conferences(merged, sys.stdout)
        else:
            with args.output.open("w", encoding="utf-8") as stream:
                dump_conferences(merged, stream)
    except (OSError, ValueError, yaml.YAMLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
