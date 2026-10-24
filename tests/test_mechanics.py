import random
import unittest

from cocbot.mechanics import dice, generation
from cocbot.mechanics.checks import CheckRules, SuccessLevel as S, success_level
from cocbot.mechanics.derived import (
    compute_derived,
    damage_bonus_and_build,
    eval_derived_formula,
    movement_rate,
)
from tests.helpers import SeqRandom


class SuccessLevelTests(unittest.TestCase):
    def test_tiers(self):
        cases = [
            (1, 50, S.CRITICAL),
            (10, 50, S.EXTREME),
            (11, 50, S.HARD),
            (25, 50, S.HARD),
            (26, 50, S.SUCCESS),
            (50, 50, S.SUCCESS),
            (51, 50, S.FAIL),
            (1, 3, S.CRITICAL),
            (2, 3, S.SUCCESS),
        ]
        for roll, target, expected in cases:
            with self.subTest(roll=roll, target=target):
                self.assertEqual(success_level(roll, target), expected)

    def test_fumble_rules_raw(self):
        # skill < 50: 96-100 fumble
        self.assertEqual(success_level(95, 49), S.FAIL)
        self.assertEqual(success_level(96, 49), S.FUMBLE)
        self.assertEqual(success_level(100, 49), S.FUMBLE)
        # skill >= 50: only 100 fumbles (previously returned FAIL — RAW fix)
        self.assertEqual(success_level(99, 50), S.FAIL)
        self.assertEqual(success_level(100, 50), S.FUMBLE)
        self.assertEqual(success_level(100, 99), S.FUMBLE)
        self.assertEqual(success_level(97, 99), S.SUCCESS)

    def test_house_rule_hook(self):
        wide = CheckRules(critical_max=5)
        self.assertEqual(success_level(5, 60, wide), S.CRITICAL)
        self.assertEqual(success_level(5, 60), S.EXTREME)


class DiceTests(unittest.TestCase):
    def tearDown(self):
        dice.set_rng(random.Random())

    def test_bonus_takes_lowest_penalty_highest(self):
        # ones=3, tens: 7, 1
        dice.set_rng(SeqRandom([3, 7, 1]))
        r = dice.roll_d100_bonus_penalty_candidates(1)
        self.assertEqual(r.candidates, [73, 13])
        self.assertEqual(r.chosen, 13)
        dice.set_rng(SeqRandom([3, 7, 1]))
        self.assertEqual(dice.roll_d100_bonus_penalty_candidates(-1).chosen, 73)

    def test_double_zero_is_100(self):
        dice.set_rng(SeqRandom([0, 0]))
        self.assertEqual(dice.roll_d100_bonus_penalty_candidates(0).chosen, 100)
        # with a bonus die, 00 tens + 0 ones -> 100, but tens 1 -> 10
        dice.set_rng(SeqRandom([0, 0, 1]))
        r = dice.roll_d100_bonus_penalty_candidates(1)
        self.assertEqual(r.candidates, [100, 10])
        self.assertEqual(r.chosen, 10)

    def test_check_details_only_reports_candidates_with_bp(self):
        dice.set_rng(SeqRandom([5, 4]))
        result, cands = dice.d100_check_details(50, 0)
        self.assertEqual((result.roll, result.level, cands), (45, S.SUCCESS, None))
        dice.set_rng(SeqRandom([5, 4, 0]))
        result, cands = dice.d100_check_details(50, 1)
        self.assertEqual(result.roll, 5)
        self.assertEqual(cands, [45, 5])
        self.assertEqual(result.level, S.EXTREME)

    def test_bp_guard(self):
        with self.assertRaises(ValueError):
            dice.roll_d100_bonus_penalty_candidates(dice.MAX_BONUS_PENALTY_DICE + 1)

    def test_parse_and_roll(self):
        dice.set_rng(SeqRandom([4, 6]))
        self.assertEqual(dice.parse_and_roll("2d6+1"), 11)
        dice.set_rng(SeqRandom([20]))
        self.assertEqual(dice.parse_and_roll("d20"), 20)
        dice.set_rng(SeqRandom([50]))
        self.assertEqual(dice.parse_and_roll("1d100 - 10"), 40)
        for bad in ("", "abc", "0d6", "2d0", "1000d6", "1d100000"):
            with self.subTest(expr=bad), self.assertRaises(ValueError):
                dice.parse_and_roll(bad)


class DerivedTests(unittest.TestCase):
    def test_damage_bonus_build_table(self):
        cases = {
            2: ("-2", -2), 64: ("-2", -2), 65: ("-1", -1), 84: ("-1", -1), 85: ("0", 0),
            124: ("0", 0), 125: ("+1D4", 1), 164: ("+1D4", 1), 165: ("+1D6", 2),
            204: ("+1D6", 2), 205: ("+2D6", 3), 284: ("+2D6", 3), 285: ("+3D6", 4),
            364: ("+3D6", 4), 365: ("+4D6", 5), 444: ("+4D6", 5), 445: ("+5D6", 6),
            524: ("+5D6", 6), 525: ("+6D6", 7), 604: ("+6D6", 7), 605: ("+7D6", 8),
        }
        for total, expected in cases.items():
            with self.subTest(total=total):
                self.assertEqual(damage_bonus_and_build(total, 0), expected)

    def test_movement(self):
        self.assertEqual(movement_rate(40, 40, 50), 7)
        self.assertEqual(movement_rate(60, 40, 50), 8)
        self.assertEqual(movement_rate(50, 50, 50), 8)
        self.assertEqual(movement_rate(60, 60, 50), 9)
        self.assertEqual(movement_rate(60, 60, 50, age=39), 9)
        self.assertEqual(movement_rate(60, 60, 50, age=40), 8)
        self.assertEqual(movement_rate(60, 60, 50, age=89), 4)
        self.assertEqual(movement_rate(60, 60, 50, age=95), 4)

    def test_compute_derived(self):
        stats = {"STR": 60, "CON": 55, "SIZ": 65, "DEX": 70, "APP": 50, "INT": 80, "POW": 45, "EDU": 75}
        d = compute_derived(stats, age=25, cthulhu_mythos=4)
        self.assertEqual(d.hp_max, 12)       # (55+65)/10
        self.assertEqual(d.mp_max, 9)        # 45/5
        self.assertEqual(d.san_start, 45)
        self.assertEqual(d.san_max, 95)
        self.assertEqual((d.damage_bonus, d.build), ("+1D4", 1))  # 125
        self.assertEqual(d.mov, 8)
        self.assertEqual(d.dodge_base, 35)
        self.assertEqual(d.language_own_base, 75)

    def test_partial_stats(self):
        d = compute_derived({"POW": 50})
        self.assertIsNone(d.hp_max)
        self.assertEqual(d.mp_max, 10)
        self.assertIsNone(d.mov)

    def test_eval_formula(self):
        self.assertEqual(eval_derived_formula("DEX/2", {"DEX": 55})[0], 27)
        self.assertEqual(eval_derived_formula("EDU", {"EDU": 80})[0], 80)
        self.assertIsNone(eval_derived_formula("DEX/2", {})[0])


class GenerationTests(unittest.TestCase):
    def test_ranges(self):
        rng = random.Random(1234)
        for _ in range(500):
            v = generation.roll_all(rng).values
            for k in ("STR", "CON", "DEX", "APP", "POW", "LUCK"):
                self.assertTrue(15 <= v[k] <= 90 and v[k] % 5 == 0, (k, v[k]))
            for k in ("SIZ", "INT", "EDU"):
                self.assertTrue(40 <= v[k] <= 90 and v[k] % 5 == 0, (k, v[k]))

    def test_formulas_exact(self):
        self.assertEqual(generation.roll_characteristic("STR", SeqRandom([1, 2, 3])), 30)
        self.assertEqual(generation.roll_characteristic("SIZ", SeqRandom([1, 1])), 40)


if __name__ == "__main__":
    unittest.main()
