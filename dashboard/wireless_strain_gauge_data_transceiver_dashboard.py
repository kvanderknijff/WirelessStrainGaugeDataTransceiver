"""Huisman-branded dashboard for the Wireless Strain Gauge Data Transceiver.

Receiver firmware protocol:
  LC,2,<sequence>,<raw_value>,<flags>,<tx_sample_ms>

All other lines are retained verbatim as firmware/debug output.  The program
uses only tkinter (bundled with normal Windows Python) plus pyserial.
"""

from __future__ import annotations

import csv
import queue
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


APP_TITLE = "Huisman | Wireless Strain Gauge Data Transceiver"
BAUD_RATE = 115200
PLOT_POINTS = 900
REFRESH_MS = 100
PLOT_REFRESH_SECONDS = 0.20
LOG_FLUSH_SECONDS = 1.0
SERIAL_QUEUE_CAPACITY = 4096

# Huisman Equipment-inspired UI palette.
# The supplied JPEG is blank/solid white, so the dashboard also supports
# an external "huisman_logo.png" next to this script. If it is not present,
# a clean text fallback is shown instead of displaying a blank image.
BRAND_NAVY = "#071B33"
BRAND_NAVY_2 = "#0B2748"
BRAND_BLUE = "#0093D0"
BRAND_BLUE_LIGHT = "#76C8EA"
BRAND_SKY = "#E7F6FC"
BG = "#071B33"
PANEL = "#0D2A49"
FIELD = "#061625"
TEXT = "#F3F8FC"
MUTED = "#A9C0D3"
GRID = "#244563"
PLOT_BG = "#061625"
ACCENT = "#0093D0"
ACCENT_ACTIVE = "#18A4DE"
SUCCESS = "#37B77A"
WARNING = "#F0A33A"
DANGER = "#D65B68"
LOGO_FILE = Path(__file__).with_name("huisman_logo.png")


@dataclass
class DataPoint:
    """One validated load-cell packet with PC receipt and TX-originated timing."""
    received_at: datetime
    monotonic_s: float
    tx_sample_ms: int
    device_elapsed_s: float
    sequence: int
    value: int
    flags: int


class ReceiverDashboard(tk.Tk):
    """Tkinter application that monitors, controls and logs the STM32 receiver."""

    def __init__(self) -> None:
        """Create bounded application state, UI variables and recurring UI work."""
        super().__init__()
        self.title(APP_TITLE)
        self.minsize(1120, 720)
        self.geometry("1380x850")
        self.configure(bg=BG)

        self.serial_port = None
        self.serial_thread: threading.Thread | None = None
        self.stop_reader = threading.Event()
        self.incoming: queue.Queue[tuple[str, str, datetime, int]] = queue.Queue(maxsize=SERIAL_QUEUE_CAPACITY)
        self.connection_generation = 0
        self.serial_queue_drops = 0
        self.reported_queue_drops = 0
        self.port_labels: dict[str, str] = {}
        self.points: deque[DataPoint] = deque(maxlen=PLOT_POINTS)
        self.program_started = time.monotonic()
        self.last_sequence: int | None = None
        self.last_tx_sample_ms: int | None = None
        self.tx_epoch_ms = 0
        self.tx_origin_ms: int | None = None
        self.data_writer = None
        self.data_file = None
        self.debug_writer = None
        self.debug_file = None
        self.last_port_scan = 0.0
        self.last_plot_draw = 0.0
        self.last_log_flush = 0.0
        self.plot_dirty = True
        self.data_terminal_buffer: list[str] = []
        self.debug_terminal_buffer: list[str] = []
        self.x_window_var = tk.StringVar(value="60")
        self.raw_auto_var = tk.BooleanVar(value=True)
        self.raw_y_min_var = tk.StringVar(value="0")
        self.raw_y_max_var = tk.StringVar(value="100000")

        self._configure_style()
        self._build_ui()
        for variable in (self.x_window_var, self.raw_y_min_var, self.raw_y_max_var):
            variable.trace_add("write", lambda *_args: self.mark_plot_dirty())
        self.refresh_ports(force=True)
        self.after(REFRESH_MS, self.process_incoming)
        self.after(1000, self.periodic_port_scan)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _configure_style(self) -> None:
        """Define the dark ttk theme shared by controls, panels and notebook tabs."""
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 20))
        style.configure("Sub.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 10))
        style.configure("TButton", padding=(10, 6), font=("Segoe UI Semibold", 9))
        style.configure("Accent.TButton", background=ACCENT, foreground="white")
        style.map("Accent.TButton", background=[("active", ACCENT_ACTIVE)])
        style.configure("Danger.TButton", background=DANGER, foreground="white")
        style.map("Danger.TButton", background=[("active", "#E16F7A")])
        style.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=TEXT, arrowcolor=TEXT)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)])
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", PANEL)], foreground=[("active", TEXT)])
        style.configure("TNotebook", background=PANEL, borderwidth=0)
        style.configure("TNotebook.Tab", background=BRAND_NAVY_2, foreground=MUTED, padding=(14, 7))
        style.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", TEXT)])
        style.configure("TLabelframe", background=PANEL, foreground=MUTED)
        style.configure("TLabelframe.Label", background=PANEL, foreground=BRAND_SKY,
                        font=("Segoe UI Semibold", 9))
        self.option_add("*tearOff", False)

    def _build_ui(self) -> None:
        """Build connection controls, raw chart, log controls and terminal tabs."""
        header = ttk.Frame(self, padding=(22, 14, 22, 8))
        header.pack(fill="x")

        # Huisman branding block. Prefer a real PNG logo supplied next to the
        # script; otherwise show a clean text fallback so the UI never renders
        # a blank white rectangle from the supplied JPEG.
        brand = tk.Frame(header, bg=BG)
        brand.pack(side="left", anchor="w")
        self.logo_image = None
        if LOGO_FILE.exists():
            try:
                self.logo_image = tk.PhotoImage(file=str(LOGO_FILE))
                max_w, max_h = 210, 48
                if self.logo_image.width() > max_w or self.logo_image.height() > max_h:
                    # PhotoImage has no high-quality arbitrary scaling, so use
                    # subsample only when a very large PNG is supplied.
                    sx = max(1, (self.logo_image.width() + max_w - 1) // max_w)
                    sy = max(1, (self.logo_image.height() + max_h - 1) // max_h)
                    self.logo_image = self.logo_image.subsample(sx, sy)
                tk.Label(brand, image=self.logo_image, bg=BG, bd=0).pack(anchor="w")
            except tk.TclError:
                self.logo_image = None

        if self.logo_image is None:
            tk.Label(
                brand, text="HUISMAN", bg=BG, fg="white",
                font=("Segoe UI Semibold", 24), padx=0, pady=0
            ).pack(anchor="w")
            tk.Frame(brand, bg=BRAND_BLUE, height=3, width=128).pack(anchor="w", pady=(2, 3))

        tk.Label(
            brand, text="WIRELESS STRAIN GAUGE DATA TRANSCEIVER", bg=BG, fg=BRAND_SKY,
            font=("Segoe UI Semibold", 9), padx=0
        ).pack(anchor="w")

        self.status_var = tk.StringVar(value="Disconnected - choose a receiver COM port")
        self.status_ball = tk.Canvas(header, width=18, height=18, bg=BG, highlightthickness=0)
        self.status_ball.pack(side="right", padx=(8, 0))
        self.status_label = ttk.Label(header, textvariable=self.status_var, style="Sub.TLabel")
        self.status_label.pack(side="right", padx=4)
        self.set_connection_status("disconnected", self.status_var.get())

        tk.Frame(self, bg=BRAND_BLUE, height=3).pack(fill="x", padx=22, pady=(0, 10))

        connection = ttk.Frame(self, style="Panel.TFrame", padding=12)
        connection.pack(fill="x", padx=22, pady=(0, 12))
        ttk.Label(connection, text="Receiver port", style="Panel.TLabel").pack(side="left", padx=(0, 8))
        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(connection, textvariable=self.port_var, state="readonly", width=48)
        self.port_box.pack(side="left", padx=(0, 8))
        ttk.Button(connection, text="↻ Scan", command=lambda: self.refresh_ports(force=True)).pack(side="left", padx=3)
        self.connect_button = ttk.Button(connection, text="Connect", style="Accent.TButton", command=self.toggle_connection)
        self.connect_button.pack(side="left", padx=3)
        ttk.Label(connection, text="115200 baud", style="Panel.TLabel").pack(side="left", padx=(15, 0))

        body = ttk.Frame(self, padding=(22, 0, 22, 18))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=4)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, style="Panel.TFrame", padding=14)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(2, weight=1)
        left.columnconfigure(0, weight=1)
        summary = ttk.Frame(left, style="Panel.TFrame")
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.value_var = tk.StringVar(value="—")
        self.seq_var = tk.StringVar(value="Sequence —")
        self.rate_var = tk.StringVar(value="0 packets")
        ttk.Label(summary, text="RAW LOAD CELL", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        tk.Label(summary, textvariable=self.value_var, bg=PANEL, fg=BRAND_SKY, font=("Segoe UI Semibold", 28)).grid(row=1, column=0, sticky="w")
        ttk.Label(summary, textvariable=self.seq_var, style="Panel.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Label(summary, textvariable=self.rate_var, style="Panel.TLabel").grid(row=2, column=1, sticky="e", padx=24)
        ttk.Button(summary, text="Clear graph", command=self.clear_graph).grid(row=1, column=1, rowspan=1, sticky="e", padx=24)

        chart_tabs = ttk.Notebook(left)
        chart_tabs.grid(row=2, column=0, sticky="nsew")
        raw_tab = ttk.Frame(chart_tabs, style="Panel.TFrame")
        chart_tabs.add(raw_tab, text="Raw counts")
        self.raw_plot = tk.Canvas(raw_tab, bg=PLOT_BG, highlightthickness=0)
        self.raw_plot.pack(fill="both", expand=True)
        self.raw_plot.bind("<Configure>", lambda _event: self.mark_plot_dirty())
        ttk.Label(left, text="X-axis uses transmitter sample time. Set chart span and Y ranges in Graph settings.", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=(9, 0))

        right = ttk.Frame(body, style="Panel.TFrame", padding=14)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        ttk.Label(right, text="DEVICE CONTROLS", style="Panel.TLabel", font=("Segoe UI Semibold", 11)).grid(row=0, column=0, sticky="w")
        buttons = ttk.Frame(right, style="Panel.TFrame")
        buttons.grid(row=1, column=0, sticky="ew", pady=(8, 14))
        ttk.Button(buttons, text="Start node", style="Accent.TButton", command=lambda: self.send_command("start")).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(buttons, text="Stop node", style="Danger.TButton", command=lambda: self.send_command("stop")).pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.notice_var = tk.StringVar(value="Ready. Connect to a receiver serial port.")
        notice = tk.Label(right, textvariable=self.notice_var, justify="left", anchor="w", wraplength=340,
                          bg=BRAND_NAVY_2, fg=BRAND_SKY, font=("Segoe UI", 10), padx=12, pady=10)
        notice.grid(row=2, column=0, sticky="ew", pady=(0, 14))

        settings = ttk.LabelFrame(right, text="GRAPH AND CALIBRATION", padding=8)
        settings.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(settings, text="X window (s)", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.x_window_var, width=7).grid(row=0, column=1, sticky="w", padx=(6, 12))
        ttk.Button(settings, text="Apply", command=self.mark_plot_dirty).grid(row=0, column=2, sticky="w")
        self._axis_controls(settings, 1, "Raw Y", self.raw_auto_var, self.raw_y_min_var, self.raw_y_max_var)

        ttk.Label(right, text="CSV LOGGING", style="Panel.TLabel", font=("Segoe UI Semibold", 11)).grid(row=4, column=0, sticky="w")
        log_buttons = ttk.Frame(right, style="Panel.TFrame")
        log_buttons.grid(row=5, column=0, sticky="ew", pady=(8, 8))
        self.data_log_button = ttk.Button(log_buttons, text="Start data CSV", command=self.toggle_data_logging)
        self.data_log_button.pack(fill="x", pady=(0, 5))
        self.debug_log_button = ttk.Button(log_buttons, text="Start debug CSV", command=self.toggle_debug_logging)
        self.debug_log_button.pack(fill="x")
        ttk.Label(right, text="Data and debug logs are deliberately separate. Each row is stamped by this PC in UTC.", style="Panel.TLabel", wraplength=340).grid(row=6, column=0, sticky="w", pady=(0, 14))

        output_title = ttk.Frame(right, style="Panel.TFrame")
        output_title.grid(row=7, column=0, sticky="ew")
        ttk.Label(output_title, text="LIVE OUTPUT", style="Panel.TLabel", font=("Segoe UI Semibold", 11)).pack(side="left")
        ttk.Button(output_title, text="Clear data", command=lambda: self.clear_terminal(self.data_text)).pack(side="right", padx=(4, 0))
        ttk.Button(output_title, text="Clear debug", command=lambda: self.clear_terminal(self.debug_text)).pack(side="right")
        notebook = ttk.Notebook(right)
        notebook.grid(row=8, column=0, sticky="nsew")
        right.rowconfigure(8, weight=1)
        self.data_text = self._make_text_tab(notebook, "Data")
        self.debug_text = self._make_text_tab(notebook, "Debug")

    def _axis_controls(self, parent: ttk.LabelFrame, row: int, label: str,
                       auto_var: tk.BooleanVar, min_var: tk.StringVar, max_var: tk.StringVar) -> None:
        """Add one auto/manual Y-axis control row to the graph settings panel."""
        ttk.Checkbutton(parent, text=f"{label} auto", variable=auto_var, command=self.mark_plot_dirty).grid(row=row, column=0, sticky="w", pady=(5, 0))
        ttk.Entry(parent, textvariable=min_var, width=7).grid(row=row, column=1, sticky="w", padx=(6, 3), pady=(5, 0))
        ttk.Entry(parent, textvariable=max_var, width=7).grid(row=row, column=2, sticky="w", pady=(5, 0))

    def set_connection_status(self, state: str, text: str) -> None:
        """Show a colored state ball and matching human-readable connection status."""
        colors = {"connected": SUCCESS, "waiting": WARNING, "disconnected": DANGER}
        color = colors.get(state, colors["disconnected"])
        self.status_ball.delete("all")
        self.status_ball.create_oval(3, 3, 15, 15, fill=color, outline="")
        self.status_var.set(text)

    def mark_plot_dirty(self) -> None:
        """Request a deferred redraw; the timed UI loop limits actual draw rate."""
        self.plot_dirty = True

    def _make_text_tab(self, notebook: ttk.Notebook, label: str) -> tk.Text:
        """Create one bounded, scrollable read-only terminal tab."""
        frame = ttk.Frame(notebook, style="Panel.TFrame")
        notebook.add(frame, text=label)
        text = tk.Text(frame, height=12, bg=PLOT_BG, fg="#d6e4f3", insertbackground="white",
                       relief="flat", wrap="word", font=("Cascadia Mono", 9), state="disabled")
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return text

    def refresh_ports(self, force: bool = False) -> None:
        """Enumerate available serial ports without opening or probing any device."""
        if list_ports is None:
            self.notice("pyserial is not installed. Run: py -m pip install -r requirements.txt", error=True)
            return
        now = time.monotonic()
        if not force and now - self.last_port_scan < 0.8:
            return
        self.last_port_scan = now
        old_port = self.port_labels.get(self.port_var.get(), self.port_var.get())
        ports = list(list_ports.comports())  # Fast OS enumeration; does not open/probe ports.
        self.port_labels = {f"{p.device} — {p.description or 'Serial device'}": p.device for p in ports}
        # TX_simulator.py creates PTY links here; OS port enumeration omits them.
        known_devices = set(self.port_labels.values())
        for port in sorted(Path(tempfile.gettempdir()).glob("strain_gauge_serial_*/dashboard")):
            if port.is_char_device() and str(port) not in known_devices:
                self.port_labels[f"TX simulator — {port}"] = str(port)
        labels = list(self.port_labels)
        self.port_box["values"] = labels
        if old_port:
            for label, device in self.port_labels.items():
                if device == old_port:
                    self.port_var.set(label)
                    break
        elif labels:
            self.port_var.set(labels[0])

    def periodic_port_scan(self) -> None:
        """Refresh the port list once per second only while disconnected."""
        if self.serial_port is None:
            self.refresh_ports()
        self.after(1000, self.periodic_port_scan)

    def toggle_connection(self) -> None:
        """Open the selected receiver port or close the currently active connection."""
        if self.serial_port is not None:
            self.disconnect()
            return
        if serial is None:
            self.notice("pyserial is required before connecting.", error=True)
            return
        device = self.port_labels.get(self.port_var.get())
        if not device:
            self.notice("Choose a COM port, then connect.", error=True)
            return
        self.set_connection_status("waiting", f"Connecting to {device}...")
        # A generation token prevents late bytes from an old COM port session
        # from being interpreted after the user reconnects to another device.
        self.connection_generation += 1
        generation = self.connection_generation
        try:
            self.serial_port = serial.Serial(device, BAUD_RATE, timeout=0.2, write_timeout=0.5)
        except serial.SerialException as error:
            self.set_connection_status("disconnected", "Connection failed")
            self.notice(f"Cannot open {device}: {error}", error=True)
            return
        self.stop_reader.clear()
        self.serial_thread = threading.Thread(target=self.reader_loop, args=(self.serial_port, generation),
                                              name="serial-reader", daemon=True)
        self.serial_thread.start()
        self.connect_button.configure(text="Disconnect", style="Danger.TButton")
        self.set_connection_status("connected", f"Connected and receiving: {device}")
        self.notice(f"Connected to {device}. Receiver output is live.")

    def disconnect(self) -> None:
        """Invalidate the active reader session, close its port and update the UI."""
        self.stop_reader.set()
        self.connection_generation += 1
        port, self.serial_port = self.serial_port, None
        if port is not None:
            try:
                port.close()
            except Exception:
                pass
        self.connect_button.configure(text="Connect", style="Accent.TButton")
        self.set_connection_status("disconnected", "Disconnected - choose a receiver COM port")
        self.notice("Receiver disconnected.")

    def queue_reader_event(self, kind: str, content: str, generation: int) -> None:
        """Pass a reader-thread event to Tk without allowing unbounded RAM growth."""
        try:
            self.incoming.put_nowait((kind, content, datetime.now(timezone.utc), generation))
        except queue.Full:
            # Bounded queue prevents an unattended UI from consuming RAM.
            self.serial_queue_drops += 1

    def reader_loop(self, port, generation: int) -> None:
        """Read newline-delimited UART output on a worker thread for one session."""
        while not self.stop_reader.is_set():
            try:
                raw = port.readline()
                if raw:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line:
                        self.queue_reader_event("line", line, generation)
            except (OSError, serial.SerialException) as error:
                self.queue_reader_event("error", str(error), generation)
                break

    def process_incoming(self) -> None:
        """Consume serial events, batch UI work and schedule the next UI iteration."""
        processed = 0
        while processed < 300:
            try:
                kind, content, received_at, generation = self.incoming.get_nowait()
            except queue.Empty:
                break
            processed += 1
            if generation != self.connection_generation:
                continue  # A late line from a port that has since been closed.
            if kind == "error":
                self.notice(f"Serial error: {content}", error=True)
                self.disconnect()
                continue
            self.handle_line(content, received_at)
        if processed:
            self.plot_dirty = True
        self.flush_terminals()
        now = time.monotonic()
        if self.plot_dirty and now - self.last_plot_draw >= PLOT_REFRESH_SECONDS:
            self.draw_plots()
            self.last_plot_draw = now
            self.plot_dirty = False
        if now - self.last_log_flush >= LOG_FLUSH_SECONDS:
            self.flush_logs()
            self.last_log_flush = now
        if self.serial_queue_drops != self.reported_queue_drops:
            self.reported_queue_drops = self.serial_queue_drops
            self.notice(f"UI input backlog: {self.serial_queue_drops} serial lines discarded.", error=True)
        self.after(REFRESH_MS, self.process_incoming)

    def handle_line(self, line: str, received_at: datetime) -> None:
        """Parse one receiver line as protocol data or retain it as debug output."""
        if line.startswith("LC,"):
            fields = line.split(",")
            try:
                if len(fields) != 6 or fields[0] != "LC" or fields[1] != "2":
                    raise ValueError("unsupported data format")
                tx_sample_ms = int(fields[5])
                if not 0 <= tx_sample_ms <= 0xFFFFFFFF:
                    raise ValueError("invalid transmitter timestamp")
                if self.last_tx_sample_ms is not None and tx_sample_ms < self.last_tx_sample_ms:
                    if (self.last_tx_sample_ms - tx_sample_ms) > 0x80000000:
                        self.tx_epoch_ms += 0x100000000  # Normal 49.7-day uint32 wrap.
                    else:
                        # TX reset: make the next data sample a fresh time origin.
                        self.points.clear()
                        self.tx_epoch_ms = 0
                        self.tx_origin_ms = None
                        self.notice("Transmitter timestamp restarted; graph reset.")
                absolute_tx_ms = self.tx_epoch_ms + tx_sample_ms
                if self.tx_origin_ms is None:
                    self.tx_origin_ms = absolute_tx_ms
                point = DataPoint(received_at, time.monotonic() - self.program_started,
                                  tx_sample_ms, (absolute_tx_ms - self.tx_origin_ms) / 1000.0,
                                  int(fields[2]), int(fields[3]), int(fields[4]))
            except ValueError:
                self.queue_terminal("debug", f"Malformed data: {line}\n")
                self.write_debug(received_at, "malformed_data", line)
                return
            self.points.append(point)
            self.value_var.set(f"{point.value:,}")
            self.seq_var.set(f"Sequence {point.sequence}   •   flags 0x{point.flags:02X}")
            if self.last_sequence is not None and point.sequence != ((self.last_sequence + 1) & 0xFFFF):
                self.notice(f"Packet sequence gap: expected {(self.last_sequence + 1) & 0xFFFF}, received {point.sequence}.", error=True)
            self.last_sequence = point.sequence
            self.last_tx_sample_ms = tx_sample_ms
            self.rate_var.set(self.packet_rate_text())
            self.queue_terminal("data", line + "\n")
            self.write_data(point)
        else:
            self.queue_terminal("debug", line + "\n")
            self.write_debug(received_at, "debug", line)
            if "AWAKE confirmed" in line:
                self.set_connection_status("connected", "Connected - node awake and streaming")
                self.notice("Node is awake and the receiver is back in data mode.")
            elif "wake probing started" in line:
                self.set_connection_status("waiting", "Connected - waiting for sleeping TX to wake")
                self.notice("Wake request is being sent; TX responds during its next listen window.")
            elif "queued=STOP" in line or "STOP:" in line:
                self.set_connection_status("waiting", "Connected - stop requested; waiting for TX sleep")
                self.notice("Stop command queued; TX powers its load switch off itself.")
            elif line.startswith("INIT RX"):
                self.notice("Receiver radio initialized successfully.")

    def send_command(self, command: str) -> None:
        """Write one newline-terminated START or STOP command to the receiver UART."""
        if self.serial_port is None:
            self.notice("Connect to the receiver before sending a command.", error=True)
            return
        try:
            self.serial_port.write((command + "\r\n").encode("ascii"))
            self.serial_port.flush()
            if command == "start":
                self.set_connection_status("waiting", "Connected - requesting TX wake-up")
            else:
                self.set_connection_status("waiting", "Connected - requesting TX sleep")
            self.notice(f"Sent {command.upper()} command to receiver.")
        except (OSError, serial.SerialException) as error:
            self.notice(f"Could not send command: {error}", error=True)

    def toggle_data_logging(self) -> None:
        """Start or stop the CSV writer that stores validated load-cell packets."""
        if self.data_file is not None:
            self.data_file.close()
            self.data_file = self.data_writer = None
            self.data_log_button.configure(text="Start data CSV")
            self.notice("Data CSV logging stopped.")
            return
        path = self.choose_log_path("loadcell_data")
        if path:
            self.data_file = path.open("w", newline="", encoding="utf-8")
            self.data_writer = csv.writer(self.data_file)
            self.data_writer.writerow(["pc_received_utc", "host_elapsed_s", "tx_sample_ms", "tx_elapsed_s", "sequence", "raw_value", "flags"])
            self.data_log_button.configure(text="Stop data CSV")
            self.notice(f"Writing data CSV: {path.name}")

    def toggle_debug_logging(self) -> None:
        """Start or stop the separate CSV writer for firmware/debug lines."""
        if self.debug_file is not None:
            self.debug_file.close()
            self.debug_file = self.debug_writer = None
            self.debug_log_button.configure(text="Start debug CSV")
            self.notice("Debug CSV logging stopped.")
            return
        path = self.choose_log_path("receiver_debug")
        if path:
            self.debug_file = path.open("w", newline="", encoding="utf-8")
            self.debug_writer = csv.writer(self.debug_file)
            self.debug_writer.writerow(["pc_received_utc", "category", "message"])
            self.debug_log_button.configure(text="Stop debug CSV")
            self.notice(f"Writing debug CSV: {path.name}")

    def choose_log_path(self, prefix: str) -> Path | None:
        """Ask the user where to save a timestamped CSV log, or return no path."""
        default = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        selected = filedialog.asksaveasfilename(title="Choose CSV log location", defaultextension=".csv",
                                                initialfile=default, filetypes=[("CSV files", "*.csv")])
        return Path(selected) if selected else None

    def write_data(self, point: DataPoint) -> None:
        """Append one decoded packet to the enabled data CSV without flushing per row."""
        if self.data_writer:
            self.data_writer.writerow([point.received_at.isoformat(), f"{point.monotonic_s:.6f}", point.tx_sample_ms,
                                       f"{point.device_elapsed_s:.3f}", point.sequence, point.value, point.flags])

    def write_debug(self, timestamp: datetime, category: str, message: str) -> None:
        """Append one non-data UART line to the enabled debug CSV."""
        if self.debug_writer:
            self.debug_writer.writerow([timestamp.isoformat(), category, message])

    def queue_terminal(self, target: str, value: str) -> None:
        """Accumulate terminal text until the next batched Tk UI update."""
        (self.data_terminal_buffer if target == "data" else self.debug_terminal_buffer).append(value)

    def flush_terminals(self) -> None:
        """Render queued terminal text and trim history to keep Tk responsive."""
        for target, buffer in ((self.data_text, self.data_terminal_buffer),
                               (self.debug_text, self.debug_terminal_buffer)):
            if buffer:
                target.configure(state="normal")
                target.insert("end", "".join(buffer))
                buffer.clear()
                if int(target.index("end-1c").split(".")[0]) > 600:
                    target.delete("1.0", "150.0")
                target.see("end")
                target.configure(state="disabled")

    def clear_terminal(self, target: tk.Text) -> None:
        """Clear the selected terminal widget and its not-yet-rendered text buffer."""
        target.configure(state="normal")
        target.delete("1.0", "end")
        target.configure(state="disabled")
        (self.data_terminal_buffer if target is self.data_text else self.debug_terminal_buffer).clear()

    def flush_logs(self) -> None:
        """Commit buffered CSV data periodically, balancing safety and UI latency."""
        for file in (self.data_file, self.debug_file):
            if file:
                file.flush()

    def packet_rate_text(self) -> str:
        """Calculate a stable five-second packet rate using TX sample timestamps."""
        if len(self.points) < 2:
            return "Rate warming up"
        newest = self.points[-1].device_elapsed_s
        recent = [point for point in self.points if point.device_elapsed_s >= newest - 5.0]
        interval = recent[-1].device_elapsed_s - recent[0].device_elapsed_s
        return "Rate warming up" if interval <= 0.0 else f"{(len(recent) - 1) / interval:.1f} pkt/s (5 s TX average)"

    def clear_graph(self) -> None:
        """Discard displayed history and reset sequence/timestamp tracking state."""
        self.points.clear()
        self.last_sequence = None
        self.last_tx_sample_ms = None
        self.tx_epoch_ms = 0
        self.tx_origin_ms = None
        self.value_var.set("—")
        self.seq_var.set("Sequence —")
        self.rate_var.set("0 packets")
        self.mark_plot_dirty()

    def draw_plots(self) -> None:
        """Render the raw-count graph using the active X and Y axis settings."""
        self.draw_graph(self.raw_plot, [float(point.value) for point in self.points], "RAW COUNTS",
                        self.raw_auto_var.get(), self.raw_y_min_var.get(), self.raw_y_max_var.get(), "")

    def axis_window_seconds(self) -> float:
        """Return the validated visible TX-time span, constrained to 2..3600 seconds."""
        try:
            return min(3600.0, max(2.0, float(self.x_window_var.get())))
        except ValueError:
            return 60.0

    def draw_graph(self, canvas: tk.Canvas, all_values: list[float], title: str,
                   auto_y: bool, y_min_text: str, y_max_text: str, unit: str) -> None:
        """Draw one bounded, auto/manual-scaled time-series chart on a Canvas."""
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 20 or height < 20:
            return
        canvas.delete("all")
        left, top, right, bottom = 58, 16, width - 18, height - 34
        canvas.create_rectangle(left, top, right, bottom, outline=GRID)
        if not self.points:
            canvas.create_text(width / 2, height / 2, text="Waiting for load-cell data", fill=MUTED, font=("Segoe UI", 12))
            return
        window, newest = self.axis_window_seconds(), self.points[-1].device_elapsed_s
        visible = [(point, value) for point, value in zip(self.points, all_values) if point.device_elapsed_s >= newest - window]
        values = [value for _point, value in visible]
        if auto_y:
            low, high = min(values), max(values)
            pad = max(1.0, abs(low) * 0.02) if low == high else max(1.0, (high - low) * 0.08)
            low, high = low - pad, high + pad
        else:
            try:
                low, high = float(y_min_text), float(y_max_text)
                if high <= low:
                    raise ValueError
            except ValueError:
                low, high = min(values) - 1.0, max(values) + 1.0
        for step in range(5):
            y, value = top + (bottom - top) * step / 4, high - (high - low) * step / 4
            canvas.create_line(left, y, right, y, fill=GRID)
            canvas.create_text(left - 7, y, text=f"{value:,.3g}", fill=MUTED, anchor="e", font=("Segoe UI", 8))
        tick_step = max(1, int(window / 4))
        for seconds_ago in range(0, int(window) + 1, tick_step):
            x = right - (right - left) * seconds_ago / window
            canvas.create_line(x, top, x, bottom, fill=GRID)
            canvas.create_text(x, bottom + 16, text=f"-{seconds_ago}s" if seconds_ago else "now", fill=MUTED, font=("Segoe UI", 8))
        coordinates: list[float] = []
        for point, value in visible:
            coordinates.extend((right - (right - left) * (newest - point.device_elapsed_s) / window,
                                bottom - (bottom - top) * (value - low) / (high - low)))
        if len(coordinates) >= 4:
            canvas.create_line(*coordinates, fill=BRAND_BLUE_LIGHT, width=2, smooth=True)
        if coordinates:
            canvas.create_oval(coordinates[-2] - 3, coordinates[-1] - 3, coordinates[-2] + 3, coordinates[-1] + 3, fill=BRAND_SKY, outline="")
        canvas.create_text(left, top - 8, text=f"{title} {unit}".strip(), fill=MUTED, anchor="sw", font=("Segoe UI Semibold", 8))

    # def draw_plot(self) -> None:
    #     """Compatibility entry point retained for older callbacks; delegates to draw_plots."""
    #     """Compatibility wrapper for older callbacks."""
    #     self.draw_plots()
    #     return
        
    def notice(self, text: str, error: bool = False) -> None:
        """Display a concise informational or warning notification in the side panel."""
        self.notice_var.set(("⚠ " if error else "● ") + text)

    def on_close(self) -> None:
        """Close serial/log resources cleanly before destroying the Tk application."""
        self.disconnect()
        for file in (self.data_file, self.debug_file):
            if file:
                file.close()
        self.destroy()


if __name__ == "__main__":
    if serial is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_TITLE, "pyserial is not installed.\n\nRun: py -m pip install -r requirements.txt")
        root.destroy()
    else:
        ReceiverDashboard().mainloop()
