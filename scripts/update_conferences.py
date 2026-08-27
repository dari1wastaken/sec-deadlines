#!/usr/bin/env python3
"""Merge updates into an existing conference list.

Conferences already present in OLD_FILE are updated by (name, year).  A newer
year for a known conference name is added.  In both cases, ``tags`` comes from
the relevant old entry rather than the new file.
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
    """Update matches and add newer editions of conference names already known."""
    old_by_key = index_conferences(old_conferences, "old file")
    new_by_key = index_conferences(new_conferences, "new file")

    merged: List[Conference] = []
    for old_conference in old_conferences:
        result = dict(old_conference)
        update = new_by_key.get(conference_key(old_conference, "old file"))
        if update is not None:
            result.update({key: value for key, value in update.items() if key != "tags"})
        merged.append(result)

    # Add unmatched new editions in their new-file order.  When multiple old
    # editions share a name, inherit tags from the latest edition older than
    # the one being added.
    for new_conference in new_conferences:
        new_name, new_year = conference_key(new_conference, "new file")
        if (new_name, new_year) in old_by_key:
            continue
        try:
            older_editions = [
                old
                for old in old_conferences
                if old["name"] == new_name and old["year"] < new_year
            ]
            source = max(older_editions, key=lambda conference: conference["year"])
        except TypeError as error:
            raise ValueError(
                f"cannot compare years for conference {new_name!r}: {new_year!r}"
            ) from error
        except ValueError:
            # An unknown name, or an edition older than all known editions, is
            # not introduced into the maintained file.
            continue

        result = dict(new_conference)
        if "tags" in source:
            result["tags"] = source["tags"]
        else:
            result.pop("tags", None)
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
