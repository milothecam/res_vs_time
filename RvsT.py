import time
import pathlib
import pyvisa
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import csv
from datetime import datetime, timedelta

############################
# USER CONFIGURATION
############################

MEASURE_INTERVAL_S  = 60          # seconds — time between resistance measurements
STOP_TEMPERATURE_MK = 4500.0        # mK    — stop when T drops below this value
HOURLY_MARKER_INTERVAL_H = 1.0    # hours — interval between vertical time markers on plot
                                  #         (set to e.g. 0.5 for every 30 min)

NPLC                = 10          # DMM integration time (10 NPLC = ~200 ms, good noise rejection)

FILE_PREFIX = "RvsT_cooldown_Delft1b"

TIMEOUT = 60_000   # ms

# Lakeshore (same as RvsI script)
LAKESHORE_LOG_ROOT = pathlib.Path("C:/Users/bluefors/Documents/logging/temperature")
LAKESHORE_CHANNEL  = 6

# Output
SAVE_DIR = pathlib.Path(r"C:\data\Camilo\Wafer Delft 1st run 2023\1b\R vs T Delft 1b")
SAVE_DIR.mkdir(parents=True, exist_ok=True)
PLOT_SAVE_DIR = SAVE_DIR / "plots"
PLOT_SAVE_DIR.mkdir(parents=True, exist_ok=True)

############################
# WIRING NOTE
#
#  34461A 2-wire resistance measurement (RES):
#    HI input  →  one end of DUT
#    LO input  →  other end of DUT
#
#  2-wire is appropriate here because DUT resistance (100 Ω – 1 MΩ)
#  is >> typical lead resistance (~1 Ω), so lead error is negligible.
#  The DMM sources a small AC-reversed current internally and measures
#  the resulting voltage — no external SMU needed.
#
#  Auto-range covers the full 4-decade span (1 MΩ normal → ~100 Ω SC).
#  Expect noisy readings at the high end (normal state) — that's fine.
#  Precision improves greatly as the device transitions to low resistance.
############################

############################
# OPEN INSTRUMENTS
############################

rm  = pyvisa.ResourceManager()

DMM = rm.open_resource("USB0::0x2A8D::0x1301::MY60036848::0::INSTR")   # 34461A
DMM.read_termination  = "\n"
DMM.write_termination = "\n"
DMM.timeout = 30_000   # ms

print("DMM ID:", DMM.query("*IDN?").strip())
print()
print("=" * 65)
print("  WIRING CHECK")
print("  34461A: 2-wire resistance (RES), auto range")
print(f"  Measure every {MEASURE_INTERVAL_S} s")
print(f"  Stop when T < {STOP_TEMPERATURE_MK} mK")
print("=" * 65)
input("Press ENTER to begin, Ctrl+C to abort...")
print()

############################
# INSTRUMENT FUNCTIONS
############################

def read_lakeshore_temperature():
    """Read latest temperature (mK) from Lakeshore log file."""
    latest_folder = sorted([x for x in LAKESHORE_LOG_ROOT.iterdir()])[-1]
    log_file = latest_folder / f"CH{LAKESHORE_CHANNEL} T {latest_folder.stem}.log"
    with log_file.open() as f:
        last = f.readlines()[-1]
        temp_K = float(last.split(",")[-1])
    return temp_K * 1e3   # → mK


def configure_dmm():
    """Configure 34461A for 2-wire resistance (RES), auto range."""
    DMM.write("*RST")
    DMM.query("*OPC?")
    DMM.write(":SENS:FUNC 'RES'")           # 2-wire resistance
    DMM.write(":SENS:RES:RANG:AUTO ON")     # auto range — spans 1 MΩ → 100 Ω
    DMM.write(f":SENS:RES:NPLC {NPLC}")    # integration time
    DMM.write(":SENS:RES:ZERO:AUTO OFF")   # disable autozero for speed
    DMM.write(":TRIG:SOUR IMM")            # immediate trigger
    DMM.write(":TRIG:COUN 1")
    DMM.write(":SAMP:COUN 1")
    DMM.query("*OPC?")
    print(f"DMM configured: RES (2-wire), {NPLC} NPLC, auto range.")


def measure_resistance():
    """Trigger a single 4-wire resistance reading. Returns R in Ohm (nan on overflow)."""
    raw = DMM.query(":READ?").strip()
    try:
        val = float(raw)
        return np.nan if abs(val) > 9.0e37 * 0.99 else val
    except ValueError:
        return np.nan   # "OVERLOAD" etc.

############################
# CONFIGURE
############################

configure_dmm()

############################
# SANITY CHECK
############################

print("\nSanity check — single resistance reading...")
R_check = measure_resistance()
T_check = read_lakeshore_temperature()
if np.isnan(R_check):
    print("  WARNING: DMM returned NaN — check 4-wire connections before proceeding.")
else:
    print(f"  R = {R_check:.4f} Ω   T = {T_check:.1f} mK")
    print("  Readings look valid.")

input("\nPress ENTER to start monitoring, Ctrl+C to abort...")
print()

############################
# MONITORING LOOP
############################

results = []
t_start = datetime.now()
next_marker_time = t_start + timedelta(hours=HOURLY_MARKER_INTERVAL_H)
marker_times = []   # list of datetime objects where markers will be drawn

print(f"  {'#':<5} {'Timestamp':<22} {'R (Ω)':<16} {'T (mK)'}")
print("  " + "-" * 60)

count = 0
while True:
    count += 1
    now = datetime.now()
    R   = measure_resistance()
    T   = read_lakeshore_temperature()

    R_str = f"{R:.6f}" if not np.isnan(R) else "nan"
    print(f"  [{count:>4}] {now.strftime('%Y-%m-%d %H:%M:%S')}   {R_str:<16} {T:.2f}")

    results.append({
        "timestamp":      now.isoformat(timespec="seconds"),
        "elapsed_min":    (now - t_start).total_seconds() / 60,
        "R_ohm":          R,
        "Temperature_mK": T,
    })

    # Record marker time if interval crossed
    if now >= next_marker_time:
        marker_times.append(next_marker_time)
        # Advance to next marker (handles gaps if measurement took longer than interval)
        while next_marker_time <= now:
            next_marker_time += timedelta(hours=HOURLY_MARKER_INTERVAL_H)

    # Stop condition
    if T < STOP_TEMPERATURE_MK:
        print(f"\n  Stop condition reached: T = {T:.2f} mK < {STOP_TEMPERATURE_MK} mK")
        break

    # Wait for next interval (account for measurement time)
    elapsed_since_meas = (datetime.now() - now).total_seconds()
    sleep_time = max(0.0, MEASURE_INTERVAL_S - elapsed_since_meas)
    time.sleep(sleep_time)

############################
# SAVE CSV
############################

csv_path = SAVE_DIR / f"{FILE_PREFIX}_{t_start.strftime('%Y%m%d_%H%M%S')}.csv"
fieldnames = ["timestamp", "elapsed_min", "R_ohm", "Temperature_mK"]
with csv_path.open("w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(results)
print(f"\nSaved CSV: {csv_path}")

############################
# PLOT
############################

timestamps = [datetime.fromisoformat(r["timestamp"]) for r in results]
R_arr      = np.array([r["R_ohm"]          for r in results])
T_arr      = np.array([r["Temperature_mK"] for r in results])
valid      = ~np.isnan(R_arr)

fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=False)

# ── Panel 1: R vs Temperature ─────────────────────────────────────────────
ax1 = axes[0]
sc = ax1.scatter(T_arr[valid], R_arr[valid],
                 c=mdates.date2num(np.array(timestamps)[valid]),
                 cmap="viridis", s=18, zorder=3)
ax1.plot(T_arr[valid], R_arr[valid], linewidth=0.8, alpha=0.4, color="gray")
cbar = fig.colorbar(sc, ax=ax1, pad=0.02)
cbar.set_label("Time", fontsize=9)
# Format colorbar ticks as HH:MM
cbar_ticks = cbar.get_ticks()
cbar.set_ticklabels([
    mdates.num2date(t).strftime("%H:%M") for t in cbar_ticks
], fontsize=7)
ax1.set_xlabel("Temperature [mK]", fontsize=12)
ax1.set_ylabel(r"R [$\Omega$]", fontsize=12)
ax1.set_title(f"Resistance vs Temperature\n{FILE_PREFIX}", fontsize=12)
ax1.set_yscale("log")   # log scale — resolves transition across 4 decades
ax1.grid(True, linestyle="--", alpha=0.5, which="both")
ax1.invert_xaxis()   # cooldown goes right→left (high T to low T)

# ── Panel 2: R vs wall-clock time, with hourly markers ───────────────────
ax2 = axes[1]
ax2.plot(np.array(timestamps)[valid], R_arr[valid],
         linewidth=1.2, color="steelblue", marker=".", markersize=3)

# Vertical markers at requested intervals
for mt in marker_times:
    ax2.axvline(mt, color="darkred", linestyle=":", linewidth=1.2, alpha=0.8)
    ax2.text(mt, 1.0, mt.strftime("%H:%M"), rotation=90,
             va="top", ha="right", fontsize=7, color="darkred",
             transform=ax2.get_xaxis_transform())   # x=data, y=axes fraction → always at top

ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
ax2.xaxis.set_major_locator(mdates.AutoDateLocator())
fig.autofmt_xdate(rotation=30)
ax2.set_xlabel("Wall-clock time", fontsize=12)
ax2.set_ylabel(r"R [$\Omega$]", fontsize=12)
ax2.set_title("Resistance vs Time (with interval markers)", fontsize=12)
ax2.set_yscale("log")   # log scale — consistent with R vs T panel
ax2.grid(True, linestyle="--", alpha=0.5, which="both")

fig.tight_layout()
plot_path = PLOT_SAVE_DIR / f"{FILE_PREFIX}_{t_start.strftime('%Y%m%d_%H%M%S')}.png"
fig.savefig(plot_path, dpi=150)
print(f"Saved plot: {plot_path}")
plt.show()

############################
# CLEANUP
############################

print("\nClosing connections...")
DMM.close()
rm.close()
print("Done.")