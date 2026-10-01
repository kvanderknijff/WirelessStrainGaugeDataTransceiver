#!/usr/bin/env python3
"""
Interactive test receiver for the Wireless Strain Gauge Data Transceiver.

Goal:
    Start this script and follow the prompts. On macOS/Linux it tries to create
    a temporary virtual serial connection automatically using 'socat'.

    One side is opened by this simulator. The other side is shown to the user
    and can be selected in the dashboard.

Protocol:
    LC,2,<sequence>,<raw_value>,<flags>,<tx_sample_ms>

Dashboard commands:
    start\r\n
    stop\r\n

Dependency:
    Python package: pyserial

macOS/Linux virtual-port dependency:
    socat
    macOS installation if missing:
        brew install socat
"""

from __future__ import annotations

import math
import os
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("\nPySerial is nog niet geïnstalleerd.")
    print("Installeer het met:")
    print("  python3 -m pip install pyserial")
    input("\nDruk op Enter om af te sluiten...")
    raise SystemExit(1)


BAUD_RATE = 115200


def heading(text: str) -> None:
    print("\n" + "=" * 68)
    print(text)
    print("=" * 68)


def ask_float(prompt: str, default: float, minimum: float = 0.001) -> float:
    while True:
        value = input(f"{prompt} [{default}]: ").strip()
        if not value:
            return default
        try:
            number = float(value)
            if number >= minimum:
                return number
        except ValueError:
            pass
        print(f"Voer een getal >= {minimum} in.")


def ask_int(prompt: str, default: int, minimum: int | None = None) -> int:
    while True:
        value = input(f"{prompt} [{default}]: ").strip()
        if not value:
            return default
        try:
            number = int(value)
            if minimum is None or number >= minimum:
                return number
        except ValueError:
            pass
        print("Ongeldige waarde.")


class VirtualSerialPair:
    """Create a linked PTY pair using socat on macOS/Linux."""

    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.tempdir: tempfile.TemporaryDirectory | None = None
        self.sim_port: str | None = None
        self.dashboard_port: str | None = None

    def create(self) -> tuple[str, str]:
        socat = shutil.which("socat")
        if not socat:
            raise RuntimeError("SOCAT_MISSING")

        self.tempdir = tempfile.TemporaryDirectory(prefix="strain_gauge_serial_")
        root = Path(self.tempdir.name)
        sim_link = root / "simulator"
        dashboard_link = root / "dashboard"

        cmd = [
            socat,
            "-d", "-d",
            f"pty,raw,echo=0,link={sim_link}",
            f"pty,raw,echo=0,link={dashboard_link}",
        ]

        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        deadline = time.time() + 5.0
        while time.time() < deadline:
            if sim_link.exists() and dashboard_link.exists():
                self.sim_port = str(sim_link)
                self.dashboard_port = str(dashboard_link)
                return self.sim_port, self.dashboard_port

            if self.process.poll() is not None:
                err = self.process.stderr.read() if self.process.stderr else ""
                raise RuntimeError(f"socat stopte onverwacht:\n{err}")

            time.sleep(0.05)

        self.close()
        raise RuntimeError("Timeout tijdens het maken van virtuele seriële poorten.")

    def close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None

        if self.tempdir is not None:
            self.tempdir.cleanup()
            self.tempdir = None


class ReceiverSimulator:
    def __init__(
        self,
        port: str,
        rate_hz: float,
        base_value: int,
        amplitude: int,
        noise: int,
        auto_start: bool,
    ) -> None:
        self.port = port
        self.rate_hz = rate_hz
        self.base = base_value
        self.amplitude = amplitude
        self.noise = noise
        self.streaming = auto_start
        self.running = True
        self.sequence = 0
        self.t0 = time.monotonic()
        self.ser: serial.Serial | None = None
        self.lock = threading.Lock()

    def send(self, line: str) -> None:
        if not self.ser:
            return
        with self.lock:
            self.ser.write((line + "\r\n").encode("utf-8"))
            self.ser.flush()

    def command_loop(self) -> None:
        assert self.ser is not None
        while self.running:
            try:
                raw = self.ser.readline()
            except serial.SerialException:
                self.running = False
                return

            if not raw:
                continue

            cmd = raw.decode("ascii", errors="replace").strip().lower()
            if not cmd:
                continue

            print(f"\nDashboard -> simulator: {cmd}")

            if cmd == "start":
                self.streaming = True
                self.send("AWAKE confirmed")
                print("Streaming gestart.")
            elif cmd == "stop":
                self.streaming = False
                self.send("STOP: streaming disabled")
                print("Streaming gestopt; wacht op START.")
            else:
                self.send(f"DEBUG: unknown command '{cmd}'")

    def measurement(self, elapsed: float) -> int:
        return int(
            self.base
            + self.amplitude * math.sin(2 * math.pi * 0.10 * elapsed)
            + self.amplitude * 0.18 * math.sin(2 * math.pi * 0.73 * elapsed)
            + random.randint(-self.noise, self.noise)
        )

    def run(self) -> None:
        self.ser = serial.Serial(
            self.port,
            BAUD_RATE,
            timeout=0.1,
            write_timeout=0.5,
        )

        self.send("INIT RX - simulator ready")

        threading.Thread(
            target=self.command_loop,
            name="dashboard-command-reader",
            daemon=True,
        ).start()

        interval = 1.0 / self.rate_hz
        next_sample = time.monotonic()

        try:
            while self.running:
                if not self.streaming:
                    time.sleep(0.05)
                    next_sample = time.monotonic()
                    continue

                now = time.monotonic()
                if now < next_sample:
                    time.sleep(min(next_sample - now, 0.01))
                    continue

                elapsed = now - self.t0
                tx_ms = int(elapsed * 1000) & 0xFFFFFFFF
                raw_value = self.measurement(elapsed)

                line = (
                    f"LC,2,{self.sequence},"
                    f"{raw_value},0,{tx_ms}"
                )

                self.send(line)
                print(f"\rTX  {line:<55}", end="", flush=True)

                self.sequence = (self.sequence + 1) & 0xFFFF
                next_sample += interval

                if next_sample < time.monotonic() - interval:
                    next_sample = time.monotonic() + interval

        except KeyboardInterrupt:
            print("\n\nSimulator gestopt.")
        finally:
            self.running = False
            if self.ser and self.ser.is_open:
                self.ser.close()


def choose_existing_port() -> str | None:
    ports = list(list_ports.comports())

    if not ports:
        print("Er zijn geen seriële poorten gevonden.")
        return None

    print("\nBeschikbare seriële poorten:")
    for i, p in enumerate(ports, start=1):
        print(f"  {i}. {p.device}  ({p.description})")

    while True:
        choice = input("\nKies het nummer van de simulatorpoort: ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(ports):
                return ports[idx].device
        except ValueError:
            pass
        print("Ongeldige keuze.")


def main() -> int:
    heading("Wireless Strain Gauge Data Transceiver - Test Simulator")

    system = platform.system()
    print(f"Besturingssysteem: {system}")
    print("Baudrate: 115200")
    print("\nDit programma simuleert de receiver voor het dashboard.")

    pair: VirtualSerialPair | None = None
    simulator_port: str | None = None
    dashboard_port: str | None = None

    if system in ("Darwin", "Linux"):
        print("\nVirtuele seriële verbinding wordt automatisch aangemaakt...")

        pair = VirtualSerialPair()
        try:
            simulator_port, dashboard_port = pair.create()
        except RuntimeError as exc:
            if str(exc) == "SOCAT_MISSING":
                print("\n'socat' is niet geïnstalleerd.")
                if system == "Darwin":
                    print("Op macOS kun je dit eenmalig installeren met:")
                    print("  brew install socat")
                    print("\nDaarna hoef je alleen dit Python-script opnieuw te starten.")
                else:
                    print("Installeer socat via de package manager van je Linux-distributie.")
                input("\nDruk op Enter om af te sluiten...")
                return 1
            print(f"\nKon geen virtuele verbinding maken: {exc}")
            input("\nDruk op Enter om af te sluiten...")
            return 1

        heading("Virtuele COM-verbinding gereed")
        print("De simulator gebruikt automatisch:")
        print(f"  {simulator_port}")
        print()
        print("KIES IN HET DASHBOARD DEZE POORT:")
        print()
        print(f"  >>> {dashboard_port} <<<")
        print()
        print("Laat dit Python-programma open terwijl je het dashboard test.")

    elif system == "Windows":
        heading("Windows")
        print(
            "Windows kan zonder geïnstalleerde virtuele COM-driver geen gekoppeld "
            "COM-poortpaar puur vanuit Python maken."
        )
        print(
            "Als er al een gekoppelde virtuele poort bestaat, kun je hieronder "
            "de simulatorzijde kiezen."
        )
        simulator_port = choose_existing_port()
        if not simulator_port:
            input("\nDruk op Enter om af te sluiten...")
            return 1
        print("\nOpen in het dashboard de andere poort van het gekoppelde COM-paar.")

    else:
        print(f"Besturingssysteem '{system}' wordt niet automatisch ondersteund.")
        simulator_port = choose_existing_port()
        if not simulator_port:
            return 1

    heading("Simulatie-instellingen")
    print("Druk op Enter om de standaardwaarden te gebruiken.\n")
    rate = ask_float("Meetfrequentie in Hz", 10.0)
    base = ask_int("Gemiddelde raw_value", 82500)
    amplitude = ask_int("Amplitude van de meetvariatie", 1800, 0)
    noise = ask_int("Maximale willekeurige ruis (+/-)", 60, 0)

    answer = input(
        "Direct data sturen, of wachten op de START-knop van het dashboard? "
        "[wachten/direct] [wachten]: "
    ).strip().lower()
    auto_start = answer in ("direct", "d", "ja", "j", "yes", "y")

    heading("Simulator actief")
    if dashboard_port:
        print(f"Dashboardpoort : {dashboard_port}")
    print(f"Simulatorpoort : {simulator_port}")
    print(f"Meetfrequentie : {rate:g} Hz")
    print(f"Raw basis      : {base}")
    print(f"Amplitude      : {amplitude}")
    print(f"Ruis           : +/-{noise}")
    print()
    if auto_start:
        print("Data wordt direct verzonden.")
    else:
        print("Open het dashboard, maak verbinding en klik op 'Start node'.")
    print("Druk hier op Ctrl+C om de simulator af te sluiten.\n")

    try:
        ReceiverSimulator(
            simulator_port,
            rate,
            base,
            amplitude,
            noise,
            auto_start,
        ).run()
    except serial.SerialException as exc:
        print(f"\nSeriële fout: {exc}")
        return 1
    finally:
        if pair:
            pair.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
