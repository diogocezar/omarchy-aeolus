"""Tests for system/aeolus.py, against a fake /sys/class/hwmon tree.

    python3 -m unittest discover -s tests -v
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "system", "aeolus.py")
sys.path.insert(0, os.path.join(HERE, "..", "system"))
import aeolus  # noqa: E402


def base_cfg(**over):
    cfg = dict(aeolus.DEFAULTS)
    cfg["curves"] = {m: dict(aeolus.CURVES[m]) for m in aeolus.CURVE_MODES}
    cfg["channels"] = [
        {"chip": "nct6799", "device": "", "pwm": 2, "role": "pump", "floor": 40.0, "label": "Pump", "tach": True},
        {"chip": "nct6799", "device": "", "pwm": 7, "role": "fan", "floor": 20.0, "label": "Fans", "tach": True},
    ]
    cfg.update(over)
    return cfg


def settle(ctl, temp, seconds=60, gpu=None):
    for _ in range(seconds):
        out, reason, _ = ctl.step(temp, gpu)
    return out, reason


class Curves(unittest.TestCase):
    def test_interpolate(self):
        pts = [(0, 20), (50, 20), (70, 60), (80, 100)]
        self.assertEqual(aeolus.interpolate(pts, -5), 20)
        self.assertEqual(aeolus.interpolate(pts, 40), 20)
        self.assertEqual(aeolus.interpolate(pts, 60), 40)
        self.assertEqual(aeolus.interpolate(pts, 80), 100)
        self.assertEqual(aeolus.interpolate(pts, 200), 100)

    def test_valid_curve(self):
        self.assertTrue(aeolus.valid_curve([[0, 20], [80, 100]]))
        self.assertFalse(aeolus.valid_curve([[0, 20]]))
        self.assertFalse(aeolus.valid_curve([[50, 20], [40, 100]]))
        self.assertFalse(aeolus.valid_curve([[0, 120], [80, 100]]))
        self.assertFalse(aeolus.valid_curve([[0, float("nan")], [80, 100]]))
        self.assertFalse(aeolus.valid_curve("nope"))

    def test_builtin_curves_end_at_full_speed(self):
        for mode, roles in aeolus.CURVES.items():
            for role, pts in roles.items():
                self.assertTrue(aeolus.valid_curve([list(p) for p in pts]), (mode, role))
                self.assertEqual(pts[-1][1], 100, (mode, role))
                self.assertLessEqual(pts[-1][0], 85, (mode, role))

    def test_modes_are_ordered(self):
        # at any temperature: silent <= performance
        for role in aeolus.ROLES:
            for t in range(0, 101):
                s = aeolus.interpolate(aeolus.CURVES["silent"][role], t)
                p = aeolus.interpolate(aeolus.CURVES["performance"][role], t)
                self.assertLessEqual(s, p, (role, t))

    def test_parse_mode(self):
        self.assertEqual(aeolus.parse_mode("auto"), ("auto", None))
        self.assertEqual(aeolus.parse_mode("manual 45"), ("manual", 45))
        self.assertEqual(aeolus.parse_mode("manual 0"), ("manual", 0))
        for bad in ("manual", "manual 101", "manual -1", "manual 4.5", "turbo", "",
                    "auto 3", "manual 0045", None, "manual  45 1"):
            self.assertIsNone(aeolus.parse_mode(bad), bad)


class Gpu(unittest.TestCase):
    def fake(self, body):
        fd, path = tempfile.mkstemp()
        os.write(fd, ("#!/bin/sh\n" + body + "\n").encode())
        os.close(fd)
        os.chmod(path, 0o755)
        self.addCleanup(os.unlink, path)
        return path

    def test_reads_hottest_gpu(self):
        aeolus.NVIDIA_SMI = self.fake("echo 41; echo 63")
        self.assertEqual(aeolus.read_gpu_temp(), 63)

    def test_garbage_and_failures(self):
        for body in ("echo N/A", "exit 3", "echo 999", "yes 50", "sleep 10"):
            aeolus.NVIDIA_SMI = self.fake(body)
            started = time.monotonic()
            r = aeolus.read_gpu_temp()
            self.assertLess(time.monotonic() - started, 4, body)
            if body == "yes 50":
                self.assertIsNone(r)          # endless output: capped, not trusted
            else:
                self.assertIsNone(r, body)

    def tearDown(self):
        aeolus.NVIDIA_SMI = "/usr/bin/nvidia-smi"


class Config(unittest.TestCase):
    def write(self, data):
        fd, path = tempfile.mkstemp()
        os.write(fd, json.dumps(data).encode())
        os.close(fd)
        self.addCleanup(os.unlink, path)
        return path

    def test_target_validation(self):
        with self.assertRaises(aeolus.ConfigError):
            aeolus.load_config(self.write({"channels": [{"chip": "x", "pwm": 1, "role": "fan"}], "target": 82}))
        with self.assertRaises(aeolus.ConfigError):
            aeolus.load_config(self.write({"channels": [{"chip": "x", "pwm": 1, "role": "fan"}], "target": 40}))

    def test_minimal(self):
        cfg = aeolus.load_config(self.write({"channels": [{"chip": "nct6799", "pwm": 2, "role": "pump"}]}))
        self.assertEqual(cfg["channels"][0]["floor"], 40)
        self.assertEqual(cfg["mode"], "auto")

    def test_rejects_low_pump_floor(self):
        with self.assertRaises(aeolus.ConfigError):
            aeolus.load_config(self.write({"channels": [{"chip": "x", "pwm": 1, "role": "pump", "floor": 20}]}))

    def test_rejects_bad_entries(self):
        bad = [
            {},
            {"channels": []},
            {"channels": [{"chip": "x", "pwm": 1, "role": "heater"}]},
            {"channels": [{"chip": "x", "pwm": 99, "role": "fan"}]},
            {"channels": [{"chip": "x", "pwm": 1, "role": "fan"}, {"chip": "x", "pwm": 1, "role": "fan"}]},
            {"channels": [{"chip": "x", "pwm": 1, "role": "fan"}], "emergency": 200},
            {"channels": [{"chip": "x", "pwm": 1, "role": "fan"}], "emergency": 80, "emergencyClear": 85},
            {"channels": [{"chip": "x", "pwm": 1, "role": "fan"}], "curves": {"silent": {"fan": [[0, 1]]}}},
            [1, 2],
        ]
        for data in bad:
            with self.assertRaises(aeolus.ConfigError, msg=data):
                aeolus.load_config(self.write(data))

    def test_missing(self):
        with self.assertRaises(aeolus.ConfigError):
            aeolus.load_config("/nonexistent/aeolus.json")


class Control(unittest.TestCase):
    def test_floors_hold_in_every_mode(self):
        for mode in aeolus.MODES:
            ctl = aeolus.Controller(base_cfg(mode=mode))
            out, _ = settle(ctl, 20)
            self.assertGreaterEqual(out[0], 40, mode)
            self.assertGreaterEqual(out[1], 20, mode)

    def test_short_spike_is_ignored(self):
        ctl = aeolus.Controller(base_cfg(mode="silent"))
        before, _ = settle(ctl, 42)
        peak = 0
        for t in (75, 76) + (42,) * 20:   # two-second Ryzen spike
            out, _, _ = ctl.step(t)
            peak = max(peak, out[1])
        self.assertLessEqual(peak - before[1], 1)

    def test_auto_stays_at_floor_when_cool(self):
        ctl = aeolus.Controller(base_cfg(mode="auto"))
        out, _ = settle(ctl, 54, 300)
        self.assertEqual(out, {0: 40.0, 1: 20.0})

    def test_auto_holds_the_target(self):
        # a crude thermal model: the CPU settles where heat in = heat out,
        # and more fan takes more heat out
        ctl = aeolus.Controller(base_cfg(mode="auto"))
        temp = 45.0
        for _ in range(900):
            out, reason, _ = ctl.step(temp)
            fan = out[1]
            equilibrium = 95 - 0.45 * fan       # 20% fan -> 86 °C, 100% -> 50 °C
            temp += (equilibrium - temp) * 0.05
        self.assertEqual(reason, "")
        self.assertLess(abs(temp - 70), 2.5, temp)
        self.assertLess(fan, 70)                # just what it takes, not maximum
        self.assertGreater(fan, 45)

    def test_auto_winds_down_after_load(self):
        ctl = aeolus.Controller(base_cfg(mode="auto"))
        settle(ctl, 78, 120)
        self.assertGreater(ctl.boost, 10)
        out, _ = settle(ctl, 45, 300)
        self.assertEqual(ctl.boost, 0.0)
        self.assertEqual(out[1], 20.0)

    def test_auto_never_below_silent(self):
        for t in range(30, 90, 5):
            a = aeolus.Controller(base_cfg(mode="auto"))
            q = aeolus.Controller(base_cfg(mode="silent"))
            oa, _ = settle(a, t, 5)
            oq, _ = settle(q, t, 5)
            self.assertGreaterEqual(oa[1], oq[1], t)
            self.assertGreaterEqual(oa[0], oq[0], t)

    def test_manual(self):
        ctl = aeolus.Controller(base_cfg(mode="manual 55"))
        out, _, _ = ctl.step(42)
        self.assertEqual(out[1], 55)
        self.assertEqual(out[0], 40)            # pump keeps the silent curve
        ctl.set_mode("manual 5")
        out, _, _ = ctl.step(42)
        self.assertEqual(out[1], 20)            # floor wins
        out, _ = settle(ctl, 80, 60)
        self.assertGreater(out[1], 70)          # heat wins over the slider
        self.assertEqual(ctl.mode_text, "manual 5")

    def test_sustained_load_ramps_up(self):
        ctl = aeolus.Controller(base_cfg(mode="silent"))
        settle(ctl, 42)
        out, reason = settle(ctl, 78, 30)
        self.assertEqual(reason, "")
        self.assertGreater(out[1], 70)
        self.assertGreater(out[0], 70)

    def test_ramp_rates(self):
        ctl = aeolus.Controller(base_cfg(mode="silent"))
        settle(ctl, 80)
        hot = ctl.duty[1]
        out, _, _ = ctl.step(40)
        self.assertGreaterEqual(out[1], hot - 1.5)   # winds down gently

    def test_emergency_is_immediate_and_sticky(self):
        ctl = aeolus.Controller(base_cfg(mode="silent"))
        settle(ctl, 42)
        out, reason, _ = ctl.step(91)
        self.assertEqual(reason, "emergency")
        self.assertEqual(out, {0: 100.0, 1: 100.0})
        out, reason, _ = ctl.step(85)        # between clear and trigger
        self.assertEqual(reason, "emergency")
        out, reason, _ = ctl.step(79)
        self.assertEqual(reason, "")
        self.assertGreater(out[1], 95)       # still winding down from 100

    def test_gpu_emergency(self):
        ctl = aeolus.Controller(base_cfg())
        settle(ctl, 42)
        out, reason, _ = ctl.step(42, gpu=88)
        self.assertEqual(reason, "emergency")
        out, reason, _ = ctl.step(42, gpu=84)
        self.assertEqual(reason, "emergency")
        out, reason, _ = ctl.step(42, gpu=80)
        self.assertEqual(reason, "")

    def test_gpu_lifts_fans_not_pump(self):
        ctl = aeolus.Controller(base_cfg(mode="silent"))
        out, _ = settle(ctl, 42, gpu=80)
        self.assertGreaterEqual(out[1], 69)
        self.assertEqual(out[0], 40)

    def test_gpu_ignored_when_disabled(self):
        ctl = aeolus.Controller(base_cfg(mode="silent", gpu=False))
        out, _ = settle(ctl, 42, gpu=80)
        self.assertEqual(out[1], 20)

    def test_lost_sensor_goes_full_speed(self):
        ctl = aeolus.Controller(base_cfg(mode="silent"))
        settle(ctl, 42)
        for i in range(aeolus.SENSOR_GRACE - 1):
            out, reason, _ = ctl.step(None)
            self.assertEqual(reason, "")
        out, reason, _ = ctl.step(None)
        self.assertEqual(reason, "no-sensor")
        self.assertEqual(out, {0: 100.0, 1: 100.0})

    def test_never_had_sensor(self):
        ctl = aeolus.Controller(base_cfg())
        out, reason, _ = ctl.step(None)
        self.assertEqual(reason, "no-sensor")
        self.assertEqual(out[0], 100.0)

    def test_stall_goes_full_speed(self):
        ctl = aeolus.Controller(base_cfg(mode="silent"))
        settle(ctl, 42)
        out, reason, _ = ctl.step(42, None, stalled=True)
        self.assertEqual(reason, "stalled")
        self.assertEqual(out, {0: 100.0, 1: 100.0})

    def test_mode_change_lands_at_once(self):
        ctl = aeolus.Controller(base_cfg(mode="manual 100"))
        settle(ctl, 47, 5)
        ctl.set_mode("silent")
        out, _, _ = ctl.step(47)
        self.assertEqual(out[1], 20)

    def test_mode_switch(self):
        ctl = aeolus.Controller(base_cfg(mode="silent"))
        quiet, _ = settle(ctl, 60)
        ctl.set_mode("performance")
        loud, _ = settle(ctl, 60)
        self.assertGreater(loud[1], quiet[1])
        ctl.set_mode("bogus")
        self.assertEqual(ctl.mode, "performance")


class FakeHardware:
    """A /sys/class/hwmon with an nct6799 (pwm2 pump, pwm7 fans) and k10temp."""

    def __init__(self, root):
        self.root = root
        self.sys = os.path.join(root, "hwmon")
        self.nct = os.path.join(self.sys, "hwmon7")
        self.cpu = os.path.join(self.sys, "hwmon2")
        os.makedirs(self.nct)
        os.makedirs(self.cpu)
        self.put(self.nct, "name", "nct6799")
        for i in (2, 7):
            self.put(self.nct, "pwm%d" % i, 163)
            self.put(self.nct, "pwm%d_enable" % i, 5)
            self.put(self.nct, "fan%d_input" % i, 2000)
        self.put(self.cpu, "name", "k10temp")
        self.put(self.cpu, "temp1_label", "Tctl")
        self.temp(42)
        self.run = os.path.join(root, "run")
        self.state = os.path.join(root, "state")
        self.config = os.path.join(root, "config.json")
        with open(self.config, "w") as f:
            json.dump({"gpu": False, "interval": 0.2, "channels": [
                {"chip": "nct6799", "pwm": 2, "role": "pump", "label": "Pump"},
                {"chip": "nct6799", "pwm": 7, "role": "fan", "label": "Fans"}]}, f)

    @staticmethod
    def put(d, name, value):
        with open(os.path.join(d, name), "w") as f:
            f.write("%s\n" % value)

    def get(self, name):
        with open(os.path.join(self.nct, name)) as f:
            return int(f.read())

    def temp(self, c):
        self.put(self.cpu, "temp1_input", int(c * 1000))

    def env(self):
        e = dict(os.environ)
        e.update(AEOLUS_SYSFS=self.sys, AEOLUS_RUN=self.run, AEOLUS_STATE=self.state,
                 AEOLUS_CONFIG=self.config, AEOLUS_NVIDIA_SMI="/nonexistent")
        e.pop("NOTIFY_SOCKET", None)
        return e

    def cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT] + list(args), env=self.env(),
                              capture_output=True, text=True, timeout=10)

    def status(self):
        try:
            with open(os.path.join(self.run, "status.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None


def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("condition not met in %.0fs" % timeout)


class Daemon(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hw = FakeHardware(self.tmp.name)
        self.proc = None

    def tearDown(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.tmp.cleanup()

    def start(self):
        try:
            os.unlink(os.path.join(self.hw.run, "status.json"))  # left by a kill
        except OSError:
            pass
        self.proc = subprocess.Popen([sys.executable, SCRIPT, "daemon"], env=self.hw.env(),
                                     stderr=subprocess.DEVNULL)
        wait_for(self.hw.status)

    def test_takes_control_and_follows_temperature(self):
        self.start()
        wait_for(lambda: self.hw.get("pwm2_enable") == 1 and self.hw.get("pwm7_enable") == 1)
        st = self.hw.status()
        self.assertEqual(st["mode"], "auto")
        self.assertEqual(self.hw.get("pwm2"), aeolus.pct_to_pwm(40))
        self.assertEqual(self.hw.get("pwm7"), aeolus.pct_to_pwm(20))
        self.hw.temp(95)
        wait_for(lambda: self.hw.get("pwm7") == 255 and self.hw.get("pwm2") == 255)
        self.assertEqual(self.hw.status()["reason"], "emergency")

    def test_sigterm_restores_firmware(self):
        self.start()
        wait_for(lambda: self.hw.get("pwm2_enable") == 1)
        self.proc.send_signal(signal.SIGTERM)
        self.proc.wait(timeout=5)
        self.assertEqual(self.hw.get("pwm2_enable"), 5)
        self.assertEqual(self.hw.get("pwm7_enable"), 5)
        self.assertIsNone(self.hw.status())

    def test_kill_then_restore_command(self):
        # what systemd's ExecStopPost does after a crash or a watchdog kill
        self.start()
        wait_for(lambda: self.hw.get("pwm2_enable") == 1)
        self.proc.kill()
        self.proc.wait()
        self.assertEqual(self.hw.get("pwm2_enable"), 1)
        r = self.hw.cli("restore")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.hw.get("pwm2_enable"), 5)
        self.assertEqual(self.hw.get("pwm7_enable"), 5)

    def test_restart_keeps_true_firmware_state(self):
        self.start()
        wait_for(lambda: self.hw.get("pwm2_enable") == 1)
        self.proc.kill()
        self.proc.wait()
        self.start()                       # sees enable=1, must not save it
        self.proc.send_signal(signal.SIGTERM)
        self.proc.wait(timeout=5)
        self.assertEqual(self.hw.get("pwm2_enable"), 5)

    def test_restore_without_saved_state_goes_full_speed(self):
        r = self.hw.cli("restore")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.hw.get("pwm2"), 255)
        self.assertEqual(self.hw.get("pwm7"), 255)

    def test_firmware_taking_back_control_is_undone(self):
        self.start()
        wait_for(lambda: self.hw.get("pwm7_enable") == 1)
        FakeHardware.put(self.hw.nct, "pwm7_enable", 5)   # e.g. after resume
        FakeHardware.put(self.hw.nct, "pwm7", 163)
        wait_for(lambda: self.hw.get("pwm7_enable") == 1 and self.hw.get("pwm7") != 163)

    def test_full_speed_reported_as_zero_is_left_alone(self):
        self.start()
        self.hw.temp(95)
        wait_for(lambda: self.hw.get("pwm7") == 255)
        FakeHardware.put(self.hw.nct, "pwm7_enable", 0)    # nct6775 quirk
        time.sleep(0.8)
        self.assertEqual(self.hw.get("pwm7_enable"), 0)
        FakeHardware.put(self.hw.nct, "pwm2_enable", 0)
        FakeHardware.put(self.hw.nct, "pwm2", 100)          # not what we wrote
        wait_for(lambda: self.hw.get("pwm2_enable") == 1 and self.hw.get("pwm2") == 255)

    def test_mode_request_and_persistence(self):
        self.start()
        r = self.hw.cli("mode", "performance")
        self.assertEqual(r.returncode, 0, r.stderr)
        wait_for(lambda: self.hw.status()["mode"] == "performance")
        wait_for(lambda: self.hw.get("pwm2") >= aeolus.pct_to_pwm(65))
        self.assertEqual(self.hw.cli("mode", "turbo").returncode, 1)
        self.assertEqual(self.hw.cli("mode", "manual", "120").returncode, 1)
        with open(os.path.join(self.hw.run, "mode"), "w") as f:
            f.write("x" * 5000)            # garbage is ignored
        time.sleep(0.6)
        self.assertEqual(self.hw.status()["mode"], "performance")
        self.proc.send_signal(signal.SIGTERM)
        self.proc.wait(timeout=5)
        self.start()
        self.assertEqual(self.hw.status()["mode"], "performance")
        self.assertEqual(self.hw.cli("mode", "manual", "60").returncode, 0)
        wait_for(lambda: self.hw.get("pwm7") == aeolus.pct_to_pwm(60))
        st = self.hw.status()
        self.assertEqual((st["mode"], st["manual"]), ("manual", 60))

    def test_stalled_pump_goes_full_speed(self):
        self.start()
        wait_for(lambda: self.hw.get("pwm2_enable") == 1)
        FakeHardware.put(self.hw.nct, "fan2_input", 0)
        st = wait_for(lambda: self.hw.status()["reason"] == "stalled" and self.hw.status(),
                      timeout=aeolus.STALL_SECONDS + 4)
        self.assertTrue(st["headers"][0]["stalled"])
        wait_for(lambda: self.hw.get("pwm7") == 255)
        FakeHardware.put(self.hw.nct, "fan2_input", 2100)
        wait_for(lambda: self.hw.status()["reason"] == "")

    def test_lost_sensor_goes_full_speed(self):
        self.start()
        wait_for(lambda: self.hw.get("pwm2_enable") == 1)
        os.unlink(os.path.join(self.hw.cpu, "temp1_input"))
        wait_for(lambda: self.hw.get("pwm7") == 255 and self.hw.get("pwm2") == 255)
        self.assertEqual(self.hw.status()["reason"], "no-sensor")

    def test_missing_chip_refuses_to_start(self):
        FakeHardware.put(self.hw.nct, "name", "other")
        r = subprocess.run([sys.executable, SCRIPT, "daemon"], env=self.hw.env(),
                           capture_output=True, text=True, timeout=10)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not found", r.stderr)
        self.assertEqual(self.hw.get("pwm2_enable"), 5)

    def test_status_cli(self):
        self.start()
        r = self.hw.cli("status")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Pump", r.stdout)
        r = self.hw.cli("status", "--json")
        self.assertEqual(json.loads(r.stdout)["mode"], "auto")


class Release(unittest.TestCase):
    def test_versions_match(self):
        root = os.path.join(HERE, "..")
        with open(os.path.join(root, "manifest.json")) as f:
            manifest = json.load(f)["version"]
        with open(os.path.join(root, "src", "Widget.qml")) as f:
            widget = f.read()
        self.assertEqual(aeolus.VERSION, manifest)
        self.assertIn('pluginVersion: "%s"' % manifest, widget)


if __name__ == "__main__":
    unittest.main()
