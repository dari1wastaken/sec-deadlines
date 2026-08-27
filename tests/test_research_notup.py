import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml


SCRIPT = Path(__file__).parents[1] / "scripts" / "research_notup.py"
SPEC = importlib.util.spec_from_file_location("research_notup", SCRIPT)
assert SPEC and SPEC.loader
research = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(research)


def result(action, conference, supports=()):
    full = {field: None for field in ("name", "year", *research.EDITABLE_FIELDS)}
    full.update(conference)
    sources = []
    if supports:
        sources.append(
            {
                "url": "https://conference.example/cfp",
                "title": "Official call for papers",
                "supports": list(supports),
            }
        )
    return {
        "action": action,
        "reason": "Verified on the official site.",
        "conference": full,
        "sources": sources,
    }


class ResearchNotupTest(unittest.TestCase):
    def setUp(self):
        self.old = {
            "name": "ExampleConf",
            "year": 2026,
            "description": "Example Conference",
            "link": "https://conference.example/2026",
            "deadline": ["2026-01-10 23:59"],
            "date": "May 1-2",
            "place": "Old City",
            "tags": ["SEC", "CONF", "NOTUP"],
            "private_note": "must survive",
        }

    def test_validated_update_preserves_tags_and_unknown_fields(self):
        raw = result(
            "update_existing",
            {
                "name": "ExampleConf",
                "year": 2026,
                "place": "New City",
                "link": "https://conference.example/2026/cfp",
            },
            supports=("place", "link"),
        )
        validated = research.validate_result(raw, self.old)
        merged, updated, added = research.apply_results(
            [self.old], {("ExampleConf", 2026): validated}
        )
        self.assertEqual((updated, added), (1, 0))
        self.assertEqual(merged[0]["place"], "New City")
        self.assertEqual(merged[0]["tags"], self.old["tags"])
        self.assertEqual(merged[0]["private_note"], "must survive")

    def test_new_edition_is_inserted_and_inherits_notup_tags(self):
        raw = result(
            "new_edition",
            {
                "name": "ExampleConf",
                "year": 2027,
                "description": "Example Conference",
                "link": "https://conference.example/2027",
                "deadline": ["2027-01-09 23:59"],
                "date": "May 3-4",
                "place": "New City",
            },
            supports=("year", "link", "deadline", "date", "place"),
        )
        validated = research.validate_result(raw, self.old)
        merged, updated, added = research.apply_results(
            [self.old], {("ExampleConf", 2026): validated}
        )
        self.assertEqual((updated, added), (0, 1))
        self.assertEqual(merged[0], self.old)
        self.assertEqual(merged[1]["year"], 2027)
        self.assertEqual(merged[1]["tags"], ["SEC", "CONF", "NOTUP"])
        self.assertNotIn("private_note", merged[1])

    def test_rejects_an_unsourced_change(self):
        raw = result(
            "update_existing",
            {"name": "ExampleConf", "year": 2026, "place": "New City"},
            supports=("link",),
        )
        with self.assertRaisesRegex(research.ResearchError, "lack source support"):
            research.validate_result(raw, self.old)

    def test_rejects_new_edition_without_year_and_link_sources(self):
        raw = result(
            "new_edition",
            {
                "name": "ExampleConf",
                "year": 2027,
                "link": "https://conference.example/2027",
            },
            supports=("year",),
        )
        with self.assertRaisesRegex(research.ResearchError, "year and link"):
            research.validate_result(raw, self.old)

    def test_rejects_unsourced_new_edition_fields(self):
        raw = result(
            "new_edition",
            {
                "name": "ExampleConf",
                "year": 2027,
                "link": "https://conference.example/2027",
                "place": "Unsupported City",
            },
            supports=("year", "link"),
        )
        with self.assertRaisesRegex(research.ResearchError, "place"):
            research.validate_result(raw, self.old)

    def test_rejects_invalid_deadline_shape_from_unstructured_provider(self):
        raw = result(
            "update_existing",
            {
                "name": "ExampleConf",
                "year": 2026,
                "deadline": "2026-01-20 23:59",
            },
            supports=("deadline",),
        )
        with self.assertRaisesRegex(research.ResearchError, "list of strings"):
            research.validate_result(raw, self.old)

    def test_atomic_dump_can_replace_its_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conferences.yml"
            path.write_text(yaml.safe_dump([self.old]), encoding="utf-8")
            os.chmod(path, 0o640)
            changed = [{**self.old, "place": "Atomic City"}]
            research.atomic_dump(changed, path, path)
            with path.open(encoding="utf-8") as stream:
                self.assertEqual(yaml.safe_load(stream)[0]["place"], "Atomic City")
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    @mock.patch.object(research, "ensure_provider_available")
    @mock.patch.object(research, "run_research")
    def test_main_writes_output_without_real_llm_call(self, run_research, ensure_provider):
        run_research.return_value = result(
            "no_change", {"name": "ExampleConf", "year": 2026}
        )
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "input.yml"
            output_path = Path(directory) / "output.yml"
            input_path.write_text(yaml.safe_dump([self.old]), encoding="utf-8")
            exit_code = research.main(
                [str(input_path), "--output", str(output_path), "--provider", "codex"]
            )
            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            log_path = Path(f"{output_path}.log")
            self.assertTrue(log_path.exists())
            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn("Researching ExampleConf 2026", log_text)
            self.assertIn("no_change: Verified on the official site", log_text)
            ensure_provider.assert_called_once_with("codex")
            run_research.assert_called_once()


if __name__ == "__main__":
    unittest.main()
