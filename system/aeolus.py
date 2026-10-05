#!/usr/bin/python3
"""Aeolus: quiet, temperature-driven fan control for motherboard fan headers.

One file, two sides:

  aeolus daemon     the root service: reads the CPU (and optionally GPU)
                    temperature every second and drives the configured PWM
                    headers along the curve of the current mode.
  aeolus mode M     ask the service for another mode: auto, silent,
                    performance, or "manual N" (fans at N%); any member of
                    the control group may.
  aeolus status     what the service is doing right now (--json for raw).
  aeolus setup      (root) find the headers, measure them, write the config.
  aeolus restore    (root) hand every header back to the firmware.

Modes:
  auto          the quietest that keeps the CPU at its target (70 °C): fans
                stay at their floor, and only a load that pushes the CPU past
                the target brings them up, as far as it takes to hold it.
  silent        a fixed low curve; lets the CPU run warmer before speeding up.
  performance   a fixed high curve.
  manual N      fans at N%, but never under what silent would ask for.

Safety rails, in every mode:
  - each header has a floor it never goes under, measured at setup (a pump
    that stops is the one failure that really hurts);
  - CPU or GPU at the emergency temperature: every header at 100%, now;
  - no temperature for a few seconds, or a header that stops turning:
    every header at 100%;
  - the service exiting for any reason (stop, crash, kill, watchdog) gives
    the headers back to the firmware's own curves (systemd ExecStopPost).
"""

import json
import os
import signal
import socket
import stat
import subprocess
import sys
import time

VERSION = "1.0.0"
MODES = ("auto", "silent", "performance", "manual")
CURVE_MODES = ("silent", "performance")
ROLES = ("pump", "fan")

SYSFS = os.environ.get("AEOLUS_SYSFS", "/sys/class/hwmon")
CONFIG = os.environ.get("AEOLUS_CONFIG", "/etc/aeolus/config.json")
RUN_DIR = os.environ.get("AEOLUS_RUN", "/run/aeolus")
STATE_DIR = os.environ.get("AEOLUS_STATE", "/var/lib/aeolus")
NVIDIA_SMI = os.environ.get("AEOLUS_NVIDIA_SMI", "/usr/bin/nvidia-smi")

STATUS_FILE = "status.json"
REQUEST_FILE = "mode"
ORIGINAL_FILE = "original.json"

# Curves: (temperature °C, duty %) points, interpolated linearly and held flat
# past both ends. "pump" curves start high because a pump is quiet and is what
# moves the heat to the radiator; "fan" curves carry the noise.
CURVES = {
    "silent": {
        "pump": [(0, 40), (65, 40), (75, 60), (85, 100)],
        "fan": [(0, 20), (55, 20), (65, 35), (75, 65), (85, 100)],
    },
    "performance": {
        "pump": [(0, 65), (50, 75), (65, 100)],
        "fan": [(0, 45), (45, 55), (60, 85), (70, 100)],
    },
}
# The GPU heats the case, not the CPU loop: it only lifts the "fan" headers,
# along this curve, and only when it asks for more than the CPU does.
GPU_CURVE = [(0, 0), (60, 0), (70, 40), (80, 70), (87, 100)]

DEFAULTS = {
    "mode": "auto",
    "target": 70,             # °C, CPU temperature auto mode holds at most
    "emergency": 90,          # °C, CPU (Tctl) — every header to 100%
    "emergencyClear": 80,     # °C, back to the curve below this
    "gpuEmergency": 87,       # °C
    "gpu": True,              # follow an NVIDIA GPU if nvidia-smi is there
    "group": "wheel",         # who may change the mode
    "interval": 1.0,          # seconds between readings
}
STALL_SECONDS = 6             # a header reading 0 rpm this long has stopped
SENSOR_GRACE = 5              # readings in a row lost before going to 100%
GPU_EVERY = 3                 # read the GPU every N loops (nvidia-smi is slow)


# --- Small helpers -------------------------------------------------------------

def log(*parts):
    print(*parts, file=sys.stderr, flush=True)


def read_text(path, limit=4096):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        return os.read(fd, limit).decode("utf-8", "replace")
    finally:
        os.close(fd)


def read_int(path):
    return int(read_text(path, 64).strip())


def write_sysfs(path, value):
    with open(path, "w") as f:
        f.write(str(int(value)))


def write_atomic(path, data, mode=0o644):
    tmp = "%s.%d.tmp" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    try:
        os.write(fd, data.encode())
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def interpolate(points, t):
    if t <= points[0][0]:
        return float(points[0][1])
    for (t0, d0), (t1, d1) in zip(points, points[1:]):
        if t <= t1:
            return d0 + (d1 - d0) * (t - t0) / (t1 - t0) if t1 > t0 else float(d1)
    return float(points[-1][1])


def pct_to_pwm(pct):
    return max(0, min(255, int(round(pct * 255 / 100))))


def pwm_to_pct(pwm):
    return int(round(pwm * 100 / 255))


def valid_curve(points):
    if not isinstance(points, list) or not 2 <= len(points) <= 16:
        return False
    last = -1
    for p in points:
        if (not isinstance(p, (list, tuple)) or len(p) != 2
                or not all(isinstance(v, (int, float)) and v == v for v in p)):
            return False
        t, d = p
        if not (0 <= t <= 120 and 0 <= d <= 100 and t >= last):
            return False
        last = t
    return True


# --- Hardware discovery ----------------------------------------------------------

def hwmon_dirs():
    try:
        names = sorted(os.listdir(SYSFS))
    except OSError:
        return []
    return [os.path.join(SYSFS, n) for n in names if n.startswith("hwmon")]


def chip_name(path):
    try:
        return read_text(os.path.join(path, "name"), 128).strip()
    except OSError:
        return ""


def chip_device(path):
    try:
        return os.path.realpath(os.path.join(path, "device"))
    except OSError:
        return ""


def find_chip(name, device=""):
    """hwmonN numbers move between boots: find the chip by name (+ device)."""
    matches = [d for d in hwmon_dirs() if chip_name(d) == name]
    if device:
        exact = [d for d in matches if chip_device(d) == device]
        if exact:
            return exact[0]
    return matches[0] if len(matches) == 1 else None


def find_cpu_sensor():
    """Path of the CPU temperature: Tctl on AMD, Package on Intel."""
    wanted = {"k10temp": ("Tctl", "Tdie"), "zenpower": ("Tctl", "Tdie"),
              "coretemp": ("Package id 0",)}
    for d in hwmon_dirs():
        labels = wanted.get(chip_name(d))
        if not labels:
            continue
        for i in range(1, 32):
            inp = os.path.join(d, "temp%d_input" % i)
            if not os.path.exists(inp):
                continue
            try:
                label = read_text(os.path.join(d, "temp%d_label" % i), 64).strip()
            except OSError:
                label = ""
            if label in labels:
                return inp
        first = os.path.join(d, "temp1_input")
        if os.path.exists(first):
            return first
    return None


def read_capped(argv, limit=256, timeout=3.0):
    """stdout of argv, at most `limit` bytes and `timeout` seconds; None on
    failure. The cap holds while reading, not after."""
    import select
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin"},
                                close_fds=True)
    except OSError:
        return None
    out = b""
    deadline = time.monotonic() + timeout
    try:
        fd = proc.stdout.fileno()
        while len(out) < limit:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([fd], [], [], left)[0]:
                return None
            chunk = os.read(fd, limit - len(out))
            if not chunk:
                break
            out += chunk
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()
    return out if proc.returncode == 0 else None


def read_gpu_temp():
    if not os.path.exists(NVIDIA_SMI):
        return None
    out = read_capped([NVIDIA_SMI, "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"])
    if out is None:
        return None
    temps = []
    for line in out.decode("ascii", "replace").split():
        try:
            t = float(line)
        except ValueError:
            continue
        if 0 < t < 150:
            temps.append(t)
    return max(temps) if temps else None


# --- Config ----------------------------------------------------------------------

class ConfigError(Exception):
    pass


def load_config(path=None):
    path = path or CONFIG
    try:
        raw = json.loads(read_text(path, 64 * 1024))
    except FileNotFoundError:
        raise ConfigError("no config at %s: run `sudo aeolus setup`" % path)
    except (OSError, ValueError) as e:
        raise ConfigError("unreadable config %s: %s" % (path, e))
    if not isinstance(raw, dict):
        raise ConfigError("config must be a JSON object")
    cfg = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in raw:
            cfg[key] = raw[key]
    if parse_mode(cfg["mode"]) is None:
        cfg["mode"] = "auto"
    if not isinstance(cfg["target"], (int, float)) or not 55 <= cfg["target"] <= 85:
        raise ConfigError("target must be between 55 and 85")
    if cfg["target"] >= cfg["emergencyClear"]:
        raise ConfigError("target must be below emergencyClear")
    for key in ("emergency", "emergencyClear", "gpuEmergency"):
        if not isinstance(cfg[key], (int, float)) or not 50 <= cfg[key] <= 105:
            raise ConfigError("%s must be between 50 and 105" % key)
    if cfg["emergencyClear"] >= cfg["emergency"]:
        raise ConfigError("emergencyClear must be below emergency")
    if not isinstance(cfg["interval"], (int, float)) or not 0.2 <= cfg["interval"] <= 5:
        raise ConfigError("interval must be between 0.2 and 5 seconds")

    curves = {m: dict(CURVES[m]) for m in CURVE_MODES}
    for m, roles in (raw.get("curves") or {}).items():
        if m not in CURVE_MODES or not isinstance(roles, dict):
            raise ConfigError("curves: unknown mode %r" % m)
        for role, pts in roles.items():
            if role not in ROLES or not valid_curve(pts):
                raise ConfigError("curves.%s.%s is not a valid curve" % (m, role))
            curves[m][role] = [tuple(p) for p in pts]
    cfg["curves"] = curves

    channels = raw.get("channels")
    if not isinstance(channels, list) or not channels:
        raise ConfigError("no channels configured: run `sudo aeolus setup`")
    seen = set()
    out = []
    for c in channels:
        if not isinstance(c, dict):
            raise ConfigError("bad channel entry")
        chip, pwm, role = c.get("chip"), c.get("pwm"), c.get("role")
        if not isinstance(chip, str) or not chip or not isinstance(pwm, int) or not 1 <= pwm <= 16:
            raise ConfigError("channel needs chip and pwm (1-16)")
        if role not in ROLES:
            raise ConfigError("channel role must be pump or fan")
        floor = c.get("floor", 40 if role == "pump" else 20)
        if not isinstance(floor, (int, float)) or not 0 <= floor <= 100:
            raise ConfigError("channel floor must be 0-100")
        if role == "pump" and floor < 30:
            raise ConfigError("a pump floor under 30% is not allowed")
        key = (chip, c.get("device", ""), pwm)
        if key in seen:
            raise ConfigError("channel %s pwm%d listed twice" % (chip, pwm))
        seen.add(key)
        label = c.get("label") if isinstance(c.get("label"), str) else ""
        out.append({"chip": chip, "device": c.get("device", "") if isinstance(c.get("device"), str) else "",
                    "pwm": pwm, "role": role, "floor": float(floor),
                    "label": label[:40] or ("Pump" if role == "pump" else "Fans"),
                    "tach": bool(c.get("tach", True))})
    cfg["channels"] = out
    return cfg


# --- Control logic (pure, unit-tested) -------------------------------------------

def parse_mode(text):
    """"auto" | "silent" | "performance" | "manual N" -> (mode, N) or None."""
    if not isinstance(text, str):
        return None
    parts = text.split()
    if len(parts) == 1 and parts[0] in MODES and parts[0] != "manual":
        return parts[0], None
    if len(parts) == 2 and parts[0] == "manual" and parts[1].isdigit() and len(parts[1]) <= 3:
        n = int(parts[1])
        if 0 <= n <= 100:
            return "manual", n
    return None


def mode_text(mode, manual):
    return "manual %d" % manual if mode == "manual" else mode


class Controller:
    """Turns readings into duty cycles. No I/O here: the daemon feeds it."""

    KI = 0.4      # auto: %/s added per °C over the target
    KP = 3.0      # auto: % added per °C over the target, at once
    PUMP_SHARE = 0.6  # auto: the pump gets this share of the fans' boost

    def __init__(self, cfg):
        self.cfg = cfg
        self.mode, self.manual = parse_mode(cfg["mode"]) or ("auto", None)
        self.window = []          # last raw CPU readings, for the spike filter
        self.smoothed = None
        self.emergency = False
        self.missed = 0
        self.boost = 0.0          # auto: integral of time spent over target
        self.duty = {}            # channel index -> current duty %

    def set_mode(self, text):
        parsed = parse_mode(text)
        if not parsed:
            return False
        if parsed != (self.mode, self.manual):
            # a change the user asked for lands at once; only the
            # temperature-driven drift is eased in
            self.boost = 0.0
            self.duty = {}
        self.mode, self.manual = parsed
        return True

    @property
    def mode_text(self):
        return mode_text(self.mode, self.manual)

    def filtered(self, raw):
        """Median of the last 5 readings, then a fast-up / slow-down average:
        a Ryzen jumping 20 °C for a second does not spin the fans up, a real
        load does within a few seconds, and they wind down gently after."""
        self.window = (self.window + [raw])[-5:]
        median = sorted(self.window)[len(self.window) // 2]
        if self.smoothed is None:
            self.smoothed = median
        else:
            alpha = 0.35 if median > self.smoothed else 0.06
            self.smoothed += (median - self.smoothed) * alpha
        return self.smoothed

    def auto_boost(self, t):
        """How far over its floor auto drives the fans: an integral that
        grows while the CPU is over the target and drains while it's under,
        plus a proportional kick so a jump over the target is met at once."""
        err = t - self.cfg["target"]
        self.boost = max(0.0, min(100.0, self.boost + self.KI * err * self.cfg["interval"]))
        return min(100.0, self.boost + self.KP * max(err, 0.0))

    def step(self, cpu, gpu=None, stalled=False):
        """One tick. cpu/gpu: °C or None. Returns ({index: duty %}, reason, t)."""
        cfg = self.cfg
        reason = ""
        if cpu is None:
            self.missed += 1
        else:
            self.missed = 0
        if cpu is not None:
            if cpu >= cfg["emergency"] or (gpu is not None and gpu >= cfg["gpuEmergency"]):
                self.emergency = True
            elif self.emergency and cpu < cfg["emergencyClear"] and (
                    gpu is None or gpu < cfg["gpuEmergency"] - 5):
                self.emergency = False
            t = self.filtered(cpu)
        else:
            t = self.smoothed

        if self.emergency:
            reason = "emergency"
        elif self.missed >= SENSOR_GRACE or t is None:
            reason = "no-sensor"
        elif stalled:
            reason = "stalled"

        boost = self.auto_boost(t) if self.mode == "auto" and not reason else 0.0
        curves = cfg["curves"]["performance" if self.mode == "performance" else "silent"]
        out = {}
        for i, ch in enumerate(cfg["channels"]):
            if reason:
                target = 100.0
            else:
                target = interpolate(curves[ch["role"]], t)
                if self.mode == "auto":
                    share = 1.0 if ch["role"] == "fan" else self.PUMP_SHARE
                    target = max(target, ch["floor"] + boost * share)
                elif self.mode == "manual" and ch["role"] == "fan":
                    target = max(target, float(self.manual))
                if ch["role"] == "fan" and gpu is not None and cfg["gpu"]:
                    target = max(target, interpolate(GPU_CURVE, gpu))
                target = max(target, ch["floor"])
            prev = self.duty.get(i)
            if prev is None or reason:
                duty = target
            elif self.mode == "manual" and ch["role"] == "fan":
                duty = target                      # the slider answers at once
            elif target > prev:
                duty = min(target, prev + 6)       # up to 6%/s
            else:
                duty = max(target, prev - 1.5)     # down 1.5%/s
            duty = max(min(duty, 100.0), ch["floor"])
            self.duty[i] = duty
            out[i] = duty
        return out, reason, t


# --- Daemon ------------------------------------------------------------------------

def sd_notify(msg):
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as s:
            s.connect(addr)
            s.sendall(msg.encode())
    except OSError:
        pass


def control_gid(name):
    try:
        import grp
        return grp.getgrnam(name).gr_gid
    except (KeyError, ImportError):
        return None


def prepare_run_dir(cfg):
    os.makedirs(RUN_DIR, mode=0o755, exist_ok=True)
    st = os.lstat(RUN_DIR)
    if not stat.S_ISDIR(st.st_mode) or (os.geteuid() == 0 and st.st_uid != 0):
        raise SystemExit("aeolus: %s is not a directory owned by root" % RUN_DIR)
    os.chmod(RUN_DIR, 0o755)
    req = os.path.join(RUN_DIR, REQUEST_FILE)
    gid = control_gid(cfg["group"])
    if os.path.lexists(req) and not stat.S_ISREG(os.lstat(req).st_mode):
        os.unlink(req)
    if not os.path.exists(req):
        fd = os.open(req, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
    if os.geteuid() == 0:
        if gid is not None:
            os.chown(req, 0, gid)
            os.chmod(req, 0o660)
        else:
            os.chmod(req, 0o666)


def read_request():
    try:
        text = read_text(os.path.join(RUN_DIR, REQUEST_FILE), 32).strip()
    except OSError:
        return None
    return text if parse_mode(text) else None


def saved_mode():
    try:
        m = read_text(os.path.join(STATE_DIR, "mode"), 32).strip()
        return m if parse_mode(m) else None
    except OSError:
        return None


def save_mode(mode):
    try:
        os.makedirs(STATE_DIR, mode=0o755, exist_ok=True)
        write_atomic(os.path.join(STATE_DIR, "mode"), mode + "\n")
    except OSError as e:
        log("aeolus: could not save mode:", e)


class Header:
    def __init__(self, ch):
        self.ch = ch
        base = find_chip(ch["chip"], ch["device"])
        if not base:
            raise SystemExit("aeolus: chip %s not found (is its driver loaded?)" % ch["chip"])
        self.pwm = os.path.join(base, "pwm%d" % ch["pwm"])
        self.enable = self.pwm + "_enable"
        self.fan = os.path.join(base, "fan%d_input" % ch["pwm"])
        for p in (self.pwm, self.enable):
            if not os.path.exists(p):
                raise SystemExit("aeolus: %s missing" % p)
        self.has_tach = ch["tach"] and os.path.exists(self.fan)
        self.written = None
        self.zero_since = None
        self.rpm = None

    def apply(self, duty):
        value = pct_to_pwm(duty)
        try:
            en = read_int(self.enable)
            # nct6775 reports a manual header at 255 as 0 ("full speed"):
            # that one is still ours. Anything else means the firmware took
            # it back (e.g. on resume).
            if en != 1 and not (en == 0 and self.written == 255 and read_int(self.pwm) == 255):
                write_sysfs(self.enable, 1)
                self.written = None
            if value != self.written:
                write_sysfs(self.pwm, value)
                self.written = value
        except OSError as e:
            log("aeolus: write failed on", self.pwm, e)
            self.written = None

    def read_rpm(self, now):
        if not self.has_tach:
            self.rpm = None
            return False
        try:
            self.rpm = read_int(self.fan)
        except (OSError, ValueError):
            self.rpm = None
            return False
        # 0 rpm while driven well above the floor it was measured to turn at
        if self.rpm == 0 and (self.written or 0) >= pct_to_pwm(max(self.ch["floor"], 25)):
            self.zero_since = self.zero_since or now
            return now - self.zero_since >= STALL_SECONDS
        self.zero_since = None
        return False


def save_originals(headers):
    """Remember what the firmware had, once per boot: a restarted service must
    not mistake its own manual setting for the firmware's."""
    path = os.path.join(RUN_DIR, ORIGINAL_FILE)
    if os.path.exists(path):
        return
    data = {}
    for h in headers:
        try:
            data[h.pwm] = {"enable": read_int(h.enable), "pwm": read_int(h.pwm)}
        except (OSError, ValueError):
            pass
    write_atomic(path, json.dumps(data), 0o600)


def restore(quiet=False):
    """Give every header we touched back to the firmware. Falls back to full
    speed (still manual) when we never saw what the firmware had."""
    path = os.path.join(RUN_DIR, ORIGINAL_FILE)
    try:
        data = json.loads(read_text(path, 64 * 1024))
    except (OSError, ValueError):
        data = None
    ok = True
    if data:
        for pwm, orig in data.items():
            if not pwm.startswith(SYSFS + "/") or ".." in pwm or not isinstance(orig, dict):
                continue
            try:
                en = int(orig.get("enable", 0))
                if en == 1:
                    write_sysfs(pwm, int(orig.get("pwm", 255)))
                elif en >= 2:
                    write_sysfs(pwm + "_enable", en)
                else:
                    write_sysfs(pwm, 255)
                    write_sysfs(pwm + "_enable", 1)
            except (OSError, ValueError) as e:
                ok = False
                log("aeolus: restore failed on", pwm, e)
        if ok:
            try:
                os.unlink(path)
            except OSError:
                pass
    else:
        try:
            cfg = load_config()
            for ch in cfg["channels"]:
                base = find_chip(ch["chip"], ch["device"])
                if base:
                    write_sysfs(os.path.join(base, "pwm%d" % ch["pwm"]), 255)
        except (ConfigError, OSError):
            pass
    if not quiet:
        log("aeolus: headers handed back to the firmware" if data else
            "aeolus: no firmware state saved; headers left at full speed")
    return ok


def daemon():
    # handlers first: a stop that lands during startup still restores below
    stopping = []

    def on_signal(signum, _frame):
        stopping.append(signum)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGHUP, on_signal)

    cfg = load_config()
    prepare_run_dir(cfg)
    headers = [Header(ch) for ch in cfg["channels"]]

    ctl = Controller(cfg)
    ctl.set_mode(saved_mode() or cfg["mode"])
    write_atomic(os.path.join(RUN_DIR, REQUEST_FILE), ctl.mode_text + "\n",
                 0o660 if control_gid(cfg["group"]) is not None else 0o666)
    prepare_run_dir(cfg)  # re-apply owner/group on the file just written

    sensor = find_cpu_sensor()
    if not sensor:
        log("aeolus: no CPU temperature sensor found; running at full speed")

    sd_notify("READY=1")
    log("aeolus %s: %d header(s), mode %s, sensor %s" % (VERSION, len(headers), ctl.mode_text, sensor))
    loop = 0
    gpu = None
    last_reason = ""
    # nothing above touches a header; from here on, every exit restores
    save_originals(headers)
    try:
        while not stopping:
            started = time.monotonic()
            req = read_request()
            if req and req != ctl.mode_text and ctl.set_mode(req):
                save_mode(ctl.mode_text)
                log("aeolus: mode", ctl.mode_text)

            cpu = None
            if sensor:
                try:
                    cpu = read_int(sensor) / 1000.0
                except (OSError, ValueError):
                    cpu = None
            if cfg["gpu"] and loop % GPU_EVERY == 0:
                gpu = read_gpu_temp()

            stalled = [h for h in headers if h.read_rpm(started)]
            duties, reason, smoothed = ctl.step(cpu, gpu, bool(stalled))
            for i, h in enumerate(headers):
                h.apply(duties[i])
            if reason != last_reason:
                log("aeolus: %s" % (reason or "back to %s" % ctl.mode_text),
                    "cpu=%s gpu=%s" % (cpu, gpu),
                    ", ".join(h.ch["label"] for h in stalled))
                last_reason = reason

            status = {
                "version": VERSION, "mode": ctl.mode, "manual": ctl.manual,
                "target": cfg["target"], "reason": reason,
                "cpu": cpu, "cpuSmoothed": round(smoothed, 1) if smoothed is not None else None,
                "gpu": gpu, "emergency": cfg["emergency"], "updated": time.time(),
                "headers": [{"label": h.ch["label"], "role": h.ch["role"],
                             "duty": round(duties[i]), "rpm": h.rpm,
                             "stalled": h in stalled} for i, h in enumerate(headers)],
            }
            try:
                write_atomic(os.path.join(RUN_DIR, STATUS_FILE), json.dumps(status))
            except OSError as e:
                log("aeolus: status write failed:", e)
            sd_notify("WATCHDOG=1")
            loop += 1
            elapsed = time.monotonic() - started
            # sleep in small slices so a stop signal is honored quickly
            remaining = cfg["interval"] - elapsed
            while remaining > 0 and not stopping:
                time.sleep(min(remaining, 0.2))
                remaining = cfg["interval"] - (time.monotonic() - started)
    finally:
        sd_notify("STOPPING=1")
        restore()
        try:
            os.unlink(os.path.join(RUN_DIR, STATUS_FILE))
        except OSError:
            pass


# --- Setup -------------------------------------------------------------------------

def measure(pwm_path, fan_path, value, settle=4.0):
    write_sysfs(pwm_path, value)
    time.sleep(settle)
    try:
        return read_int(fan_path)
    except (OSError, ValueError):
        return 0


def setup(args):
    if os.geteuid() != 0:
        raise SystemExit("aeolus setup must run as root (sudo aeolus setup)")
    found = []
    for d in hwmon_dirs():
        for i in range(1, 17):
            pwm = os.path.join(d, "pwm%d" % i)
            fan = os.path.join(d, "fan%d_input" % i)
            if not (os.path.exists(pwm) and os.path.exists(pwm + "_enable") and os.path.exists(fan)):
                continue
            try:
                rpm = read_int(fan)
            except (OSError, ValueError):
                continue
            if rpm > 0:
                found.append((d, i, rpm))
    if not found:
        raise SystemExit("no spinning PWM header found. Is the Super I/O driver loaded "
                         "(e.g. `modprobe nct6775` or `modprobe it87`)?")

    # without a terminal (pkexec, scripts) every suggestion is taken as is
    ask = sys.stdin.isatty() and "--yes" not in args
    channels = []
    print("Measuring %d header(s). Each one is run at a few speeds for a few "
          "seconds; it is handed back to the firmware right after.\n" % len(found))
    for d, i, rpm in found:
        pwm = os.path.join(d, "pwm%d" % i)
        fan = os.path.join(d, "fan%d_input" % i)
        orig_en, orig_pwm = read_int(pwm + "_enable"), read_int(pwm)
        try:
            write_sysfs(pwm + "_enable", 1)
            full = measure(pwm, fan, 255, 6)
            half = measure(pwm, fan, pct_to_pwm(50))
            low = measure(pwm, fan, pct_to_pwm(40))
        finally:
            write_sysfs(pwm, orig_pwm)
            write_sysfs(pwm + "_enable", orig_en)
        guess = "pump" if full >= 2800 else "fan"
        print("%s pwm%d: %d rpm now, %d at 100%%, %d at 50%%, %d at 40%%"
              % (chip_name(d), i, rpm, full, half, low))
        print("  looks like a %s (%s). Pumps are usually above ~2800 rpm at full speed."
              % (guess, "liquid cooler pump" if guess == "pump" else "fan or fans on a splitter"))
        role = (input("  role [pump/fan/skip] (%s): " % guess).strip().lower() if ask else "") or guess
        if role == "skip":
            continue
        if role not in ROLES:
            raise SystemExit("unknown role %r" % role)
        label = input("  name (%s): " % ("Pump" if role == "pump" else "Fans")).strip()[:40] if ask else ""
        floor = 40 if role == "pump" else 20
        if low == 0:
            floor = 50 if role == "pump" else 45
            print("  it stops at 40%%: its floor is set to %d%%" % floor)
        channels.append({"chip": chip_name(d), "device": chip_device(d), "pwm": i,
                         "role": role, "label": label or ("Pump" if role == "pump" else "Fans"),
                         "floor": floor})
    if not channels:
        raise SystemExit("nothing selected; no config written")
    cfg = dict(DEFAULTS)
    cfg["gpu"] = os.path.exists(NVIDIA_SMI)
    cfg["channels"] = channels
    os.makedirs(os.path.dirname(CONFIG), mode=0o755, exist_ok=True)
    write_atomic(CONFIG, json.dumps(cfg, indent=2) + "\n")
    load_config()  # validate what we wrote
    print("\nWrote %s. Start it with: sudo systemctl enable --now aeolus" % CONFIG)


# --- Client commands -----------------------------------------------------------------

def read_status():
    try:
        st = json.loads(read_text(os.path.join(RUN_DIR, STATUS_FILE), 64 * 1024))
    except (OSError, ValueError):
        return None
    if not isinstance(st, dict) or time.time() - float(st.get("updated", 0)) > 10:
        return None
    return st


def cmd_mode(args):
    mode = " ".join(args)
    if not parse_mode(mode):
        raise SystemExit("mode must be auto, silent, performance or manual N (0-100)")
    path = os.path.join(RUN_DIR, REQUEST_FILE)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise SystemExit("aeolus is not running (sudo systemctl start aeolus)")
    except PermissionError:
        raise SystemExit("not allowed to change the mode (join the control group)")
    try:
        os.write(fd, (mode + "\n").encode())
    finally:
        os.close(fd)


def cmd_status(as_json):
    st = read_status()
    if as_json:
        print(json.dumps(st))
        return 0 if st else 1
    if not st:
        print("aeolus is not running")
        return 1
    print("mode %s%s · CPU %s °C%s" % (
        mode_text(st["mode"], st.get("manual")), (" · " + st["reason"].upper()) if st["reason"] else "",
        st["cpu"], " · GPU %s °C" % st["gpu"] if st.get("gpu") is not None else ""))
    for h in st["headers"]:
        print("  %-12s %3d%%  %s rpm%s" % (h["label"], h["duty"], h["rpm"],
                                          "  STOPPED" if h["stalled"] else ""))
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "daemon":
        daemon()
    elif cmd == "restore":
        return 0 if restore() else 1
    elif cmd == "setup":
        setup(rest)
    elif cmd == "mode" and 1 <= len(rest) <= 2:
        cmd_mode(rest)
    elif cmd == "status":
        return cmd_status("--json" in rest)
    elif cmd == "check":
        load_config()
        print("config ok")
    elif cmd == "version":
        print(VERSION)
    else:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ConfigError as e:
        raise SystemExit("aeolus: %s" % e)
