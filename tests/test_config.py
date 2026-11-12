import json
import os

from clickity import config
from clickity.config import PRESETS, Settings, Store

from .helpers import IsolatedConfig


class SettingsClampTest(IsolatedConfig):
    def test_defaults_are_already_valid(self):
        s = Settings()
        self.assertEqual(s.to_dict(), Settings().clamp().to_dict())

    def test_numeric_limits(self):
        s = Settings(cps=9999, jitter=-5, hold_ms=10**6, burst=-1, burst_pause_ms=10**9,
                     max_clicks=-3, max_seconds=-3, start_delay=999, wiggle_px=100,
                     scroll_step=0).clamp()
        self.assertEqual(s.cps, 500.0)
        self.assertEqual(s.jitter, 0)
        self.assertEqual(s.hold_ms, 5000)
        self.assertEqual(s.burst, 0)
        self.assertEqual(s.burst_pause_ms, 600000)
        self.assertEqual(s.max_clicks, 0)
        self.assertEqual(s.max_seconds, 0)
        self.assertEqual(s.start_delay, 60.0)
        self.assertEqual(s.wiggle_px, 20)
        self.assertEqual(s.scroll_step, 1)

    def test_cps_floor(self):
        self.assertEqual(Settings(cps=0).clamp().cps, 0.1)

    def test_strings_coerced_to_numbers(self):
        s = Settings(cps="12.5", jitter="7").clamp()
        self.assertEqual(s.cps, 12.5)
        self.assertEqual(s.jitter, 7)

    def test_invalid_choices_reset(self):
        s = Settings(action="nuke", button="thumb", click_type="quad",
                     mode="sometimes", theme="beige").clamp()
        self.assertEqual((s.action, s.button, s.click_type, s.mode, s.theme),
                         ("click", "left", "single", "toggle", "neon"))

    def test_from_dict_ignores_unknown_keys_and_clamps(self):
        s = Settings.from_dict({"cps": 1000, "bogus": 1, "button": "right"})
        self.assertEqual(s.cps, 500.0)
        self.assertEqual(s.button, "right")

    def test_round_trip(self):
        s = Settings(cps=33, jitter=12, action="scroll-left", theme="ice")
        self.assertEqual(Settings.from_dict(s.to_dict()), s)

    def test_presets_are_valid(self):
        for name, p in PRESETS.items():
            with self.subTest(preset=name):
                self.assertEqual(Settings.from_dict(p.to_dict()), p)


class StoreTest(IsolatedConfig):
    def test_fresh_store_has_presets(self):
        st = Store.load()
        self.assertEqual(st.active, "default")
        self.assertEqual(set(st.profiles), set(PRESETS))
        self.assertEqual(st.lifetime_clicks, 0)

    def test_save_load_round_trip(self):
        st = Store.load()
        st.profiles["mine"] = Settings(cps=42, button="middle")
        st.active = "mine"
        st.lifetime_clicks = 1234
        st.save()
        self.assertTrue(os.path.exists(self.cfg_file))
        self.assertFalse(os.path.exists(self.cfg_file + ".tmp"))

        again = Store.load()
        self.assertEqual(again.active, "mine")
        self.assertEqual(again.lifetime_clicks, 1234)
        self.assertEqual(again.profiles["mine"], Settings(cps=42, button="middle"))

    def test_save_creates_missing_dir(self):
        nested = os.path.join(self.cfg_dir, "a", "b")
        config.CONFIG_DIR = nested  # undone by IsolatedConfig's patch cleanup
        config.CONFIG_FILE = os.path.join(nested, "config.json")
        Store.load().save()
        self.assertTrue(os.path.exists(config.CONFIG_FILE))

    def test_corrupt_file_falls_back_to_defaults(self):
        with open(self.cfg_file, "w") as f:
            f.write("{not json")
        st = Store.load()
        self.assertEqual(set(st.profiles), set(PRESETS))

    def test_wrong_types_fall_back(self):
        with open(self.cfg_file, "w") as f:
            json.dump({"lifetime_clicks": "lots", "profiles": []}, f)
        st = Store.load()
        self.assertIn("default", st.profiles)

    def test_stored_profile_values_are_clamped(self):
        with open(self.cfg_file, "w") as f:
            json.dump({"profiles": {"hacked": {"cps": 1e9, "action": "rm -rf"}}}, f)
        p = Store.load().profiles["hacked"]
        self.assertEqual(p.cps, 500.0)
        self.assertEqual(p.action, "click")

    def test_unknown_active_resets_to_default(self):
        with open(self.cfg_file, "w") as f:
            json.dump({"active": "ghost"}, f)
        self.assertEqual(Store.load().active, "default")

    def test_deleted_presets_stay_deleted_except_default(self):
        with open(self.cfg_file, "w") as f:
            json.dump({"deleted_presets": ["turbo", "default"]}, f)
        st = Store.load()
        self.assertNotIn("turbo", st.profiles)
        self.assertIn("default", st.profiles)
        self.assertIn("human", st.profiles)

    def test_user_edit_of_preset_wins(self):
        with open(self.cfg_file, "w") as f:
            json.dump({"profiles": {"turbo": {"cps": 77}}}, f)
        self.assertEqual(Store.load().profiles["turbo"].cps, 77)

    def test_current_returns_a_copy(self):
        st = Store.load()
        cur = st.current()
        cur.cps = 321
        self.assertNotEqual(st.profiles[st.active].cps, 321)

    def test_presets_not_shared_between_loads(self):
        st = Store.load()
        st.profiles["default"].cps = 99
        self.assertEqual(PRESETS["default"].cps, Settings().cps)
        self.assertEqual(Store.load().profiles["default"].cps, Settings().cps)
