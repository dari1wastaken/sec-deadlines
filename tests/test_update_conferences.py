import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "update_conferences.py"
SPEC = importlib.util.spec_from_file_location("update_conferences", SCRIPT)
assert SPEC and SPEC.loader
update_conferences = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update_conferences)


class MergeConferencesTest(unittest.TestCase):
    def test_updates_matches_and_preserves_old_only_data(self):
        old = [
            {
                "name": "S&P",
                "year": 2027,
                "date": "TBA",
                "comment": "keep when absent from update",
                "tags": ["SEC", "TOP4"],
            },
            {"name": "Old only", "year": 2026, "tags": ["SEC"]},
        ]
        new = [
            {
                "name": "S&P",
                "year": 2027,
                "date": "May 17-20",
                "place": "Montreal, Canada",
                "tags": ["CHANGED"],
            },
            {"name": "New only", "year": 2028, "tags": ["PRIV"]},
        ]

        merged = update_conferences.merge_conferences(old, new)

        self.assertEqual([entry["name"] for entry in merged], ["S&P", "Old only"])
        self.assertEqual(merged[0]["date"], "May 17-20")
        self.assertEqual(merged[0]["place"], "Montreal, Canada")
        self.assertEqual(merged[0]["comment"], "keep when absent from update")
        self.assertEqual(merged[0]["tags"], ["SEC", "TOP4"])
        self.assertEqual(merged[1], old[1])

    def test_rejects_duplicate_keys(self):
        duplicate = [
            {"name": "S&P", "year": 2027},
            {"name": "S&P", "year": 2027},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate conference"):
            update_conferences.merge_conferences([], duplicate)

    def test_adds_newer_edition_and_copies_tags_from_latest_old_edition(self):
        old = [
            {"name": "S&P", "year": 2025, "tags": ["OLD"]},
            {"name": "S&P", "year": 2026, "tags": ["SEC", "TOP4"]},
            {"name": "Other", "year": 2028, "tags": ["OTHER"]},
        ]
        new = [
            {"name": "S&P", "year": 2027, "date": "May", "tags": ["WRONG"]},
            {"name": "Unknown", "year": 2029, "tags": ["NEW"]},
            {"name": "Other", "year": 2027, "tags": ["OLDER"]},
        ]

        merged = update_conferences.merge_conferences(old, new)

        self.assertEqual(len(merged), 4)
        self.assertEqual(
            merged[-1],
            {
                "name": "S&P",
                "year": 2027,
                "date": "May",
                "tags": ["SEC", "TOP4"],
            },
        )


if __name__ == "__main__":
    unittest.main()
