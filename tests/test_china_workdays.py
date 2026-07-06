import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from apps.finance_crawler.utils.china_workdays import is_china_workday, previous_china_workday


class ChinaWorkdayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.calendar_path = Path(self.temp_dir.name) / "china-workdays.json"
        self.calendar_path.write_text(
            json.dumps(
                {
                    "2026": {
                        "holidays": ["2026-07-03"],
                        "workdays": ["2026-07-05"],
                    }
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_weekday_holiday_is_not_workday(self) -> None:
        self.assertFalse(is_china_workday(date(2026, 7, 3), calendar_path=self.calendar_path))

    def test_adjusted_weekend_is_workday(self) -> None:
        self.assertTrue(is_china_workday(date(2026, 7, 5), calendar_path=self.calendar_path))

    def test_previous_workday_skips_holiday_and_weekend(self) -> None:
        self.assertEqual(
            previous_china_workday(date(2026, 7, 6), calendar_path=self.calendar_path),
            date(2026, 7, 5),
        )

    def test_previous_workday_for_monday_is_previous_friday(self) -> None:
        self.assertEqual(
            previous_china_workday(date(2026, 7, 13), calendar_path=self.calendar_path),
            date(2026, 7, 10),
        )


if __name__ == "__main__":
    unittest.main()
