#!/usr/bin/env python3
"""Research NOTUP conferences with Codex or Claude and write an updated copy."""

from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple
from urllib.parse import urlparse

import yaml


Conference = Dict[str, Any]
EDITABLE_FIELDS = (
    "date",
    "description",
    "link",
    "dblp",
    "deadline",
    "comment",
    "place",
    "timezone",
)
SOURCE_FIELDS = ("name", "year", *EDITABLE_FIELDS)
ACTIONS = ("no_change", "update_existing", "new_edition", "uncertain")
PROMPT_VERSION = 1
LOGGER = logging.getLogger("conference_research")


RESULT_SCHEMA: Dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "reason", "conference", "sources"],
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "reason": {"type": "string"},
        "conference": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "year", *EDITABLE_FIELDS],
            "properties": {
                "name": {"type": ["string", "null"]},
                "year": {"type": ["integer", "null"]},
                "date": {"type": ["string", "null"]},
                "description": {"type": ["string", "null"]},
                "link": {"type": ["string", "null"]},
                "dblp": {"type": ["string", "null"]},
                "deadline": {
                    "anyOf": [
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "null"},
                    ]
                },
                "comment": {"type": ["string", "null"]},
                "place": {"type": ["string", "null"]},
                "timezone": {"type": ["string", "null"]},
            },
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["url", "title", "supports"],
                "properties": {
                    "url": {"type": "string"},
                    "title": {"type": "string"},
                    "supports": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(SOURCE_FIELDS)},
                    },
                },
            },
        },
    },
}


class ResearchError(RuntimeError):
    """Raised when a provider fails or returns an unsafe result."""


def configure_logging(log_path: Path) -> None:
    if not log_path.parent.resolve().is_dir():
        raise ResearchError(f"log directory does not exist: {log_path.parent}")
    for handler in LOGGER.handlers:
        handler.close()
    LOGGER.handlers.clear()
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False

    try:
        file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    except OSError as error:
        raise ResearchError(f"cannot open log file {log_path}: {error}") from error
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(console_handler)


def load_conferences(path: Path) -> List[Conference]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as error:
        raise ResearchError(f"cannot read {path}: {error}") from error
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ResearchError(f"{path}: expected a top-level list of mappings")
    return value


def validate_input(conferences: Sequence[Conference], source: Path) -> None:
    seen: set[Tuple[Any, Any]] = set()
    for entry in conferences:
        if "name" not in entry or "year" not in entry:
            raise ResearchError(f"{source}: every entry must contain name and year")
        key = (entry["name"], entry["year"])
        if key in seen:
            raise ResearchError(f"{source}: duplicate conference {key!r}")
        seen.add(key)


def is_notup(entry: Mapping[str, Any]) -> bool:
    tags = entry.get("tags", [])
    return isinstance(tags, list) and "NOTUP" in tags


def build_prompt(entry: Mapping[str, Any], today: date) -> str:
    entry_yaml = yaml.safe_dump(dict(entry), sort_keys=False, allow_unicode=True).rstrip()
    fields = ", ".join(SOURCE_FIELDS)
    return f"""Research the event below using live web search.

Today is {today.isoformat()}. Find authoritative, current information, prioritizing the
official event site. Search for both corrections to this edition and a newer
announced edition. Do not infer dates or deadlines from recurring schedules.

Return exactly the JSON object required by the supplied schema.

Choose one action:
- no_change: this edition is already accurate and no newer edition is announced.
- update_existing: authoritative sources establish corrections for this same name/year.
- new_edition: a later-year edition of the same event is officially announced. Return the
  later edition as a separate complete record; do not rewrite the supplied edition.
- uncertain: reliable sources are missing, conflicting, inaccessible, or insufficient.

Rules:
- Keep the conference name stable. A new edition must have a strictly greater integer year.
- For update_existing, return the current full record with only verified corrections applied.
- For new_edition, fill every known field for the new edition. Use null only when a field is
  genuinely unknown or inapplicable. Never invent information.
- deadline must be a list of strings formatted as YYYY-MM-DD HH:MM. If the source specifies a
  timezone other than AoE, set timezone to its IANA tz database name when possible.
- Sources must be direct HTTP(S) pages, not search-result URLs. Each source's supports array
  must name exactly the fields it substantiates, chosen from: {fields}.
- update_existing must source every changed field. new_edition must at minimum source year and
  link. For no_change or uncertain, sources may be empty.
- Do not return tags; the caller always preserves them.

Current YAML entry:
```yaml
{entry_yaml}
```"""


def _run(command: Sequence[str], prompt: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        input=prompt,
        text=True,
        capture_output=True,
        cwd=cwd,
        check=False,
    )


def ensure_provider_available(provider: str) -> None:
    executable = shutil.which(provider)
    if executable is None:
        if provider == "codex":
            hint = "install it from https://developers.openai.com/codex/cli"
        else:
            hint = "install Claude Code and sign in with your Pro account"
        raise ResearchError(f"{provider} command not found; {hint}")

    if provider == "codex":
        status_result = subprocess.run(
            [executable, "login", "status"],
            text=True,
            capture_output=True,
            check=False,
        )
        status_text = f"{status_result.stdout}\n{status_result.stderr}"
        if status_result.returncode != 0 or "Logged in" not in status_text:
            raise ResearchError("Codex is not authenticated; run `codex` and sign in")


def run_codex(prompt: str, cwd: Path, model: str | None) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="conference-research-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        schema_path = temp_dir / "schema.json"
        result_path = temp_dir / "result.json"
        schema_path.write_text(json.dumps(RESULT_SCHEMA), encoding="utf-8")
        command = [
            "codex",
            "--search",
            "exec",
            "--ephemeral",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(result_path),
        ]
        if model:
            command.extend(["--model", model])
        command.append("-")
        completed = _run(command, prompt, cwd)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ResearchError(f"Codex failed: {detail}")
        try:
            return json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ResearchError(f"Codex returned invalid structured output: {error}") from error


def run_claude(prompt: str, cwd: Path, model: str | None) -> Dict[str, Any]:
    command = [
        "claude",
        "--print",
        "--output-format",
        "json",
        "--allowedTools",
        "WebSearch",
        "WebFetch",
        "--disallowedTools",
        "Bash",
        "Edit",
        "Write",
        "Read",
        "--max-turns",
        "15",
    ]
    if model:
        command.extend(["--model", model])
    completed = _run(command, prompt, cwd)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ResearchError(f"Claude failed: {detail}")
    try:
        wrapper = json.loads(completed.stdout)
        raw_result = wrapper.get("result") if isinstance(wrapper, dict) else wrapper
        return json.loads(raw_result) if isinstance(raw_result, str) else raw_result
    except (AttributeError, TypeError, json.JSONDecodeError) as error:
        raise ResearchError(f"Claude returned invalid JSON output: {error}") from error


def run_research(
    provider: str, prompt: str, cwd: Path, model: str | None
) -> Dict[str, Any]:
    if provider == "codex":
        return run_codex(prompt, cwd, model)
    return run_claude(prompt, cwd, model)


def _valid_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def validate_result(result: Any, current: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        raise ResearchError("provider result is not an object")
    if set(result) != {"action", "reason", "conference", "sources"}:
        raise ResearchError("provider result has missing or unexpected top-level fields")
    action = result["action"]
    if action not in ACTIONS:
        raise ResearchError(f"invalid action: {action!r}")
    if not isinstance(result["reason"], str):
        raise ResearchError("result reason must be a string")
    proposed = result["conference"]
    if not isinstance(proposed, dict) or set(proposed) != {"name", "year", *EDITABLE_FIELDS}:
        raise ResearchError("result conference does not match the required field set")
    if proposed["name"] is not None and not isinstance(proposed["name"], str):
        raise ResearchError("conference name must be a string or null")
    if proposed["year"] is not None and not isinstance(proposed["year"], int):
        raise ResearchError("conference year must be an integer or null")
    for field in EDITABLE_FIELDS:
        value = proposed[field]
        if field == "deadline":
            if value is not None and (
                not isinstance(value, list)
                or any(not isinstance(deadline, str) for deadline in value)
            ):
                raise ResearchError("conference deadline must be a list of strings or null")
        elif value is not None and not isinstance(value, str):
            raise ResearchError(f"conference {field} must be a string or null")
    sources = result["sources"]
    if not isinstance(sources, list):
        raise ResearchError("result sources must be a list")

    supported: set[str] = set()
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"url", "title", "supports"}:
            raise ResearchError("a source has missing or unexpected fields")
        if not _valid_url(source["url"]) or not isinstance(source["title"], str):
            raise ResearchError("every source needs a valid HTTP(S) URL and title")
        if not isinstance(source["supports"], list) or any(
            field not in SOURCE_FIELDS for field in source["supports"]
        ):
            raise ResearchError("a source contains invalid supported fields")
        supported.update(source["supports"])

    if action in ("no_change", "uncertain"):
        return result
    if proposed["name"] != current["name"]:
        raise ResearchError("provider attempted to rename a conference")
    if action == "update_existing" and proposed["year"] != current["year"]:
        raise ResearchError("update_existing must retain the current year")
    if action == "new_edition":
        if not isinstance(proposed["year"], int) or proposed["year"] <= current["year"]:
            raise ResearchError("new_edition must use a later integer year")
        if not {"year", "link"}.issubset(supported):
            raise ResearchError("new editions require sources supporting year and link")

    changed_fields = {
        field
        for field in EDITABLE_FIELDS
        if proposed[field] is not None and proposed[field] != current.get(field)
    }
    if action == "update_existing" and not changed_fields:
        return {**result, "action": "no_change"}
    if not changed_fields.issubset(supported):
        missing = ", ".join(sorted(changed_fields - supported))
        raise ResearchError(f"changed fields lack source support: {missing}")
    return result


def apply_results(
    conferences: Sequence[Conference], results: Mapping[Tuple[Any, Any], Mapping[str, Any]]
) -> Tuple[List[Conference], int, int]:
    existing_keys = {(entry["name"], entry["year"]) for entry in conferences}
    output: List[Conference] = []
    updated = 0
    added = 0
    for original in conferences:
        key = (original["name"], original["year"])
        result = results.get(key)
        current = copy.deepcopy(original)
        if result and result["action"] == "update_existing":
            for field in EDITABLE_FIELDS:
                value = result["conference"][field]
                if value is not None:
                    current[field] = value
            if current != original:
                updated += 1
        output.append(current)

        if result and result["action"] == "new_edition":
            proposed = result["conference"]
            new_key = (proposed["name"], proposed["year"])
            if new_key in existing_keys:
                continue
            new_entry = {
                field: copy.deepcopy(value)
                for field, value in proposed.items()
                if value is not None
            }
            new_entry["tags"] = copy.deepcopy(original.get("tags", []))
            output.append(new_entry)
            existing_keys.add(new_key)
            added += 1
    return output, updated, added


def atomic_dump(conferences: Sequence[Conference], output: Path, input_path: Path) -> None:
    output_parent = output.parent.resolve()
    if not output_parent.is_dir():
        raise ResearchError(f"output directory does not exist: {output.parent}")
    mode_source = output if output.exists() else input_path
    try:
        file_mode = stat.S_IMODE(mode_source.stat().st_mode)
    except OSError:
        file_mode = 0o644

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_name = stream.name
            yaml.safe_dump(
                list(conferences),
                stream,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
                width=1000,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, file_mode)
        os.replace(temporary_name, output)
        temporary_name = None
    except (OSError, yaml.YAMLError) as error:
        raise ResearchError(f"cannot write {output}: {error}") from error
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="source conferences.yml")
    parser.add_argument("--output", type=Path, required=True, help="destination YAML file")
    parser.add_argument(
        "--log",
        type=Path,
        help="log file (default: OUTPUT.log)",
    )
    parser.add_argument(
        "--provider", choices=("codex", "claude"), default="codex", help="subscription CLI"
    )
    parser.add_argument("--model", help="optional provider model name")
    parser.add_argument(
        "--limit", type=int, metavar="N", help="research at most N NOTUP entries"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    log_path = args.log if args.log is not None else Path(f"{args.output}.log")
    for handler in LOGGER.handlers:
        handler.close()
    LOGGER.handlers.clear()
    try:
        if log_path.resolve() in (args.input.resolve(), args.output.resolve()):
            raise ResearchError("--log must differ from the input and output YAML paths")
        configure_logging(log_path)
        LOGGER.info("Research log: %s", log_path)
        if args.limit is not None and args.limit < 1:
            raise ResearchError("--limit must be at least 1")
        conferences = load_conferences(args.input)
        validate_input(conferences, args.input)
        targets = [entry for entry in conferences if is_notup(entry)]
        if args.limit is not None:
            targets = targets[: args.limit]
        if not targets:
            atomic_dump(conferences, args.output, args.input)
            LOGGER.info("No NOTUP entries found; wrote unchanged data to %s", args.output)
            return 0

        ensure_provider_available(args.provider)
        results: Dict[Tuple[Any, Any], Dict[str, Any]] = {}
        for position, entry in enumerate(targets, start=1):
            key = (entry["name"], entry["year"])
            LOGGER.info(
                "[%d/%d] Researching %s %s...",
                position,
                len(targets),
                entry["name"],
                entry["year"],
            )
            raw = run_research(
                args.provider, build_prompt(entry, date.today()), args.input.resolve().parent, args.model
            )
            result = validate_result(raw, entry)
            results[key] = result
            LOGGER.info("  %s: %s", result["action"], result["reason"])
            for source in result["sources"]:
                supported = ", ".join(source["supports"]) or "context only"
                LOGGER.info("  source (%s): %s", supported, source["url"])

        merged, updated, added = apply_results(conferences, results)
        atomic_dump(merged, args.output, args.input)
        LOGGER.info(
            "Wrote %s: %d existing edition(s) updated, %d new edition(s) added",
            args.output,
            updated,
            added,
        )
    except ResearchError as error:
        if LOGGER.handlers:
            LOGGER.error("error: %s", error)
        else:
            print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
