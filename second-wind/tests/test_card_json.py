"""card_json.py: every client's way of writing a reset time becomes one epoch."""
import datetime as dt
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import card_json  # noqa: E402

NOW = dt.datetime(2026, 10, 4, 11, 49).timestamp()


def when(epoch):
    return dt.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


class ResetEpoch(unittest.TestCase):
    def test_an_epoch_is_taken_as_given(self):
        self.assertEqual(card_json.reset_epoch(1791162000, None, NOW), 1791162000)
        self.assertEqual(card_json.reset_epoch("1791162000", None, NOW), 1791162000)

    def test_each_client_wording(self):
        cases = {
            "Oct 8 at 1pm (America/Toronto)": "2026-10-08 13:00",  # Claude
            "18:34 on 9 Oct": "2026-10-09 18:34",                  # Codex
            "October 9, 07:11": "2026-10-09 07:11",                 # Grok
            "Oct 30": "2026-10-30 00:00",                           # Cursor
        }
        for text, expected in cases.items():
            self.assertEqual(when(card_json.reset_epoch(None, text, NOW)), expected, text)

    def test_a_bare_time_is_its_next_occurrence(self):
        self.assertEqual(when(card_json.reset_epoch(None, "1:20pm (America/Toronto)", NOW)),
                         "2026-10-04 13:20")
        self.assertEqual(when(card_json.reset_epoch(None, "9am", NOW)), "2026-10-05 09:00")

    def test_a_date_early_next_year_rolls_over(self):
        late = dt.datetime(2026, 12, 30, 12, 0).timestamp()
        self.assertEqual(when(card_json.reset_epoch(None, "Jan 2", late)), "2027-01-02 00:00")

    def test_nothing_readable_is_none(self):
        self.assertIsNone(card_json.reset_epoch(None, None, NOW))
        self.assertIsNone(card_json.reset_epoch(None, "soon", NOW))


class Windows(unittest.TestCase):
    def test_a_claude_reading_lists_its_model_week(self):
        usage = {"five_hour_pct": 6, "five_hour_resets": "1:20pm", "seven_day_pct": 27,
                 "seven_day_resets": "Oct 8 at 1pm",
                 "extra": {"model_weeks": {"Fable": {"pct": 35, "resets": "Oct 8 at 1pm"}}}}
        names = [w["name"] for w in card_json.windows("secondary", "bars", usage)]
        self.assertEqual(names, ["5-hour", "Week", "Fable week"])

    def test_a_window_with_no_figure_is_left_out(self):
        usage = {"five_hour_pct": None, "seven_day_pct": 2, "seven_day_resets": "18:34 on 9 Oct"}
        self.assertEqual([w["name"] for w in card_json.windows("codex", "bars", usage)], ["Week"])


if __name__ == "__main__":
    unittest.main()
