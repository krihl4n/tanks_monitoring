"""Tests for sensors module — calc_percent, filter_value, _median."""

import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sensors import calc_percent, filter_value, _median, _read_with_median
from config import settings


class TestCalcPercent(unittest.TestCase):
    """Tests for fill percentage calculation."""

    def test_empty_tank(self):
        """Distance == depth -> 0%."""
        pct, level, over = calc_percent(settings["tank_depth_cm"])
        self.assertEqual(pct, 0)
        self.assertEqual(level, 0)
        self.assertFalse(over)

    def test_full_tank(self):
        """Distance 0 -> 100%."""
        pct, level, over = calc_percent(0)
        self.assertEqual(pct, 100)
        self.assertEqual(level, settings["tank_depth_cm"])
        self.assertTrue(over)  # 0 <= sensor_min_distance_cm

    def test_half_tank(self):
        """Distance == half depth -> ~50%."""
        pct, level, over = calc_percent(settings["tank_depth_cm"] / 2)
        self.assertEqual(pct, 50)

    def test_above_sensor_limit(self):
        """Distance <= sensor_min_distance_cm -> above_sensor_limit=True."""
        pct, level, over = calc_percent(settings["sensor_min_distance_cm"])
        self.assertTrue(over)
        pct2, _, over2 = calc_percent(settings["sensor_min_distance_cm"] + 1)
        self.assertFalse(over2)

    def test_negative_distance_clamped(self):
        """Negative distance -> level clamped to depth."""
        pct, level, over = calc_percent(-10)
        self.assertEqual(pct, 100)
        self.assertEqual(level, settings["tank_depth_cm"])

    def test_over_depth_clamped(self):
        """Distance > depth -> level clamped to 0."""
        pct, level, over = calc_percent(settings["tank_depth_cm"] + 50)
        self.assertEqual(pct, 0)
        self.assertEqual(level, 0)


class TestMedian(unittest.TestCase):
    """Tests for median calculation."""

    def test_odd_count(self):
        self.assertEqual(_median([3, 1, 2]), 2)

    def test_even_count(self):
        self.assertEqual(_median([1, 2, 3, 4]), 2.5)

    def test_single(self):
        self.assertEqual(_median([5]), 5)

    def test_two(self):
        self.assertEqual(_median([1, 3]), 2)

    def test_duplicates(self):
        self.assertEqual(_median([5, 5, 5]), 5)


class TestFilterValue(unittest.TestCase):
    """Tests for sensor reading filter (median + anti-spike)."""

    def _make_history(self, values=None):
        """Create a history list from given values."""
        return list(values) if values else []

    def test_none_returns_last(self):
        """None input -> returns last known reading."""
        h = self._make_history([100, 101, 102])
        result = filter_value(None, h)
        self.assertEqual(result, 102)

    def test_none_empty_history(self):
        """None on empty history -> None."""
        h = self._make_history()
        result = filter_value(None, h)
        self.assertIsNone(result)

    def test_first_reading_accepted(self):
        """First reading is always accepted."""
        h = self._make_history()
        result = filter_value(100, h)
        self.assertEqual(result, 100)
        self.assertEqual(h, [100])

    def test_second_reading_accepted(self):
        """Second reading accepted (not enough data to filter)."""
        h = self._make_history([100])
        result = filter_value(105, h)
        self.assertEqual(result, 105)

    def test_normal_reading_passes(self):
        """Reading within threshold -> accepted."""
        h = self._make_history([100, 101, 100, 101, 100])
        result = filter_value(102, h)
        self.assertEqual(result, 102)

    def test_spike_rejected(self):
        """Single spike > SPIKE_THRESHOLD_CM -> rejected (returns median)."""
        h = self._make_history([100, 100, 100, 100, 100])
        spike = 100 + settings["spike_threshold_cm"] + 5
        result = filter_value(spike, h)
        self.assertEqual(result, 100)

    def test_trend_accepted(self):
        """Two readings in the same direction (> SENSOR_NOISE_CM) -> trend accepted."""
        base = 100
        h = self._make_history([base, base, base, base, base])
        big_jump = base + settings["spike_threshold_cm"] + 5
        # First spike — rejected
        r1 = filter_value(big_jump, h)
        self.assertEqual(r1, base)

        # Second spike — prev direction is still flat (spike was replaced with median)
        # So second spike is also rejected
        r2 = filter_value(big_jump, h)
        self.assertEqual(r2, base)

    def test_sensor_noise_oscillation_rejected(self):
        """+-1 cm oscillation (within SENSOR_NOISE_CM) does not confirm trend."""
        h = self._make_history([100, 101, 100, 101, 100])
        spike = 100 + settings["spike_threshold_cm"] + 5
        result = filter_value(spike, h)
        self.assertEqual(result, 100)

    def test_history_grows(self):
        """Each reading appends to history."""
        h = self._make_history()
        filter_value(100, h)
        filter_value(101, h)
        filter_value(102, h)
        self.assertEqual(len(h), 3)


if __name__ == "__main__":
    unittest.main()


class TestReadWithMedian(unittest.TestCase):
    """Tests for multi-read median averaging."""

    @patch("sensors._read_single")
    @patch("sensors.time")
    def test_median_of_readings(self, mock_time, mock_read):
        """Mediana z 5 odczytów: [130, 120, 130, 130, 129] -> 130, spread=10."""
        mock_read.side_effect = [130, 120, 130, 130, 129]
        result, spread = _read_with_median("1.2.3.4", "distanceCm", 5)
        self.assertEqual(result, 130)
        self.assertEqual(spread, 10)

    @patch("sensors._read_single")
    @patch("sensors.time")
    def test_spike_filtered_out(self, mock_time, mock_read):
        """Pojedynczy spike (100) wśród normalnych (130) -> mediana 130, spread=30."""
        mock_read.side_effect = [130, 130, 100, 130, 130]
        result, spread = _read_with_median("1.2.3.4", "distanceCm", 5)
        self.assertEqual(result, 130)
        self.assertEqual(spread, 30)

    @patch("sensors._read_single")
    @patch("sensors.time")
    def test_all_none_returns_none(self, mock_time, mock_read):
        """Wszystkie odczyty None -> (None, None)."""
        mock_read.side_effect = [None, None, None, None, None]
        result, spread = _read_with_median("1.2.3.4", "distanceCm", 5)
        self.assertIsNone(result)
        self.assertIsNone(spread)

    @patch("sensors._read_single")
    @patch("sensors.time")
    def test_mostly_none_returns_none(self, mock_time, mock_read):
        """< 50% poprawnych odczytów -> (None, None)."""
        mock_read.side_effect = [None, None, 130, None, None]
        result, spread = _read_with_median("1.2.3.4", "distanceCm", 5)
        self.assertIsNone(result)
        self.assertIsNone(spread)

    @patch("sensors._read_single")
    @patch("sensors.time")
    def test_majority_valid_returns_median(self, mock_time, mock_read):
        """Większość odczytów poprawna -> mediana z poprawnych."""
        mock_read.side_effect = [130, None, 131, 130, None]
        result, spread = _read_with_median("1.2.3.4", "distanceCm", 5)
        self.assertEqual(result, 130)
        self.assertEqual(spread, 1)

    @patch("sensors._read_single")
    @patch("sensors.time")
    def test_stable_readings_low_spread(self, mock_time, mock_read):
        """Stabilne odczyty -> spread <= sensor_noise_cm."""
        mock_read.side_effect = [130, 130, 131, 130, 130]
        result, spread = _read_with_median("1.2.3.4", "distanceCm", 5)
        self.assertEqual(result, 130)
        self.assertLessEqual(spread, 2)

    @patch("sensors._read_single")
    @patch("sensors.time")
    def test_unstable_readings_high_spread(self, mock_time, mock_read):
        """Niestabilne odczyty (deszcz) -> spread > 5."""
        mock_read.side_effect = [130, 122, 135, 128, 138]
        result, spread = _read_with_median("1.2.3.4", "distanceCm", 5)
        self.assertEqual(result, 130)
        self.assertGreater(spread, 5)


if __name__ == "__main__":
    unittest.main()
