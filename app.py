"""
Camera Screenshot Capture App
Dependencies: pip install opencv-python pillow
Build .exe:   pyinstaller --onefile --windowed app.py
"""

import tkinter as tk
from tkinter import ttk, messagebox
import cv2
from PIL import Image, ImageTk
import os
import sys
import datetime

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

INTERVALS = {
    "10 секунд":  10,
    "30 секунд":  30,
    "1 минута":   60,
    "5 минут":    300,
    "10 минут":   600,
    "30 минут":   1800,
    "1 час":      3600,
    "2 часа":     7200,
    "3 часа":     10800,
    "4 часа":     14400,
}

PREVIEW_W = 640
PREVIEW_H = 480
PREVIEW_FPS_MS = 33  # ~30 fps preview refresh


def get_base_dir() -> str:
    """Return directory next to .exe or next to script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# ──────────────────────────────────────────────
# Main Application
# ──────────────────────────────────────────────

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Camera Screenshot Capture")
        self.root.resizable(False, False)

        self.cap: cv2.VideoCapture | None = None
        self.running = False          # capture is active
        self.preview_active = False   # preview loop is active
        self.shot_count = 0
        self.save_folder = ""

        self._preview_job = None      # after() job id for preview
        self._capture_job = None      # after() job id for screenshot timer
        self._current_cam_index = -1
        self._current_frame = None    # latest BGR frame from camera

        self._build_ui()
        self.scan_cameras()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ── UI construction ────────────────────────

    def _build_ui(self):
        PAD = 8

        # ── Top control panel ──
        ctrl = tk.Frame(self.root, padx=PAD, pady=PAD)
        ctrl.pack(side=tk.TOP, fill=tk.X)

        # Camera row
        tk.Label(ctrl, text="Камера:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.cam_var = tk.StringVar()
        self.cam_combo = ttk.Combobox(ctrl, textvariable=self.cam_var,
                                      state="readonly", width=18)
        self.cam_combo.grid(row=0, column=1, sticky="w")
        self.cam_combo.bind("<<ComboboxSelected>>", self._on_cam_selected)

        self.btn_refresh = tk.Button(ctrl, text="🔄 Refresh cameras",
                                     command=self.scan_cameras)
        self.btn_refresh.grid(row=0, column=2, padx=(8, 0))

        # Interval row
        tk.Label(ctrl, text="Интервал:").grid(row=1, column=0, sticky="w",
                                               padx=(0, 4), pady=(6, 0))
        self.interval_var = tk.StringVar(value=list(INTERVALS.keys())[0])
        self.interval_combo = ttk.Combobox(ctrl,
                                            textvariable=self.interval_var,
                                            values=list(INTERVALS.keys()),
                                            state="readonly", width=18)
        self.interval_combo.grid(row=1, column=1, sticky="w", pady=(6, 0))

        # Start / Stop
        self.btn_start = tk.Button(ctrl, text="▶ Start", width=10,
                                   bg="#2ecc71", fg="white",
                                   command=self.start_capture)
        self.btn_start.grid(row=0, column=3, padx=(16, 4), rowspan=1)

        self.btn_stop = tk.Button(ctrl, text="■ Stop", width=10,
                                  bg="#e74c3c", fg="white",
                                  command=self.stop_capture,
                                  state=tk.DISABLED)
        self.btn_stop.grid(row=1, column=3, padx=(16, 4), pady=(6, 0))

        # Save folder path
        tk.Label(ctrl, text="Папка:").grid(row=2, column=0, sticky="w",
                                            padx=(0, 4), pady=(6, 0))
        self.folder_var = tk.StringVar(value="—")
        tk.Label(ctrl, textvariable=self.folder_var,
                 anchor="w", width=52, relief="sunken",
                 font=("Consolas", 8)).grid(row=2, column=1, columnspan=3,
                                             sticky="w", pady=(6, 0))

        # Counter
        self.counter_var = tk.StringVar(value="Снимков: 0")
        tk.Label(ctrl, textvariable=self.counter_var,
                 font=("Arial", 9, "bold")).grid(row=2, column=4,
                                                  padx=(12, 0), pady=(6, 0))

        # ── Preview canvas ──
        self.canvas = tk.Canvas(self.root, width=PREVIEW_W, height=PREVIEW_H,
                                bg="black")
        self.canvas.pack(side=tk.TOP, padx=PAD, pady=(0, PAD))

        # Placeholder text on canvas
        self.canvas.create_text(PREVIEW_W // 2, PREVIEW_H // 2,
                                 text="Выберите камеру",
                                 fill="gray", font=("Arial", 16),
                                 tags="placeholder")

        # ── Status bar ──
        self.status_var = tk.StringVar(value="Готово. Выберите камеру.")
        tk.Label(self.root, textvariable=self.status_var,
                 anchor="w", relief="sunken",
                 font=("Arial", 9)).pack(side=tk.BOTTOM, fill=tk.X,
                                          padx=PAD, pady=(0, PAD))

    # ── Camera scanning ────────────────────────

    def scan_cameras(self):
        """Probe camera indices 0–9 and populate combobox."""
        self._set_status("Поиск камер…")
        self.btn_refresh.config(state=tk.DISABLED)
        self.root.update_idletasks()

        found = []
        for idx in range(10):
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if cap.isOpened():
                ret, _ = cap.read()
                if ret:
                    found.append(f"Camera {idx}")
            cap.release()

        self.cam_combo["values"] = found
        self.btn_refresh.config(state=tk.NORMAL)

        if found:
            self.cam_combo.current(0)
            self._set_status(f"Найдено камер: {len(found)}")
            self.btn_start.config(state=tk.NORMAL)
            self.open_camera(int(found[0].split()[-1]))
        else:
            self._set_status("Камеры не найдены.")
            self.btn_start.config(state=tk.DISABLED)

    # ── Camera open / close ────────────────────

    def open_camera(self, index: int):
        """Open a camera by index, start preview."""
        if self._current_cam_index == index and self.cap and self.cap.isOpened():
            return

        self._stop_preview()
        if self.cap:
            self.cap.release()

        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            self._set_status(f"Не удалось открыть Camera {index}")
            messagebox.showerror("Ошибка", f"Не удалось открыть Camera {index}")
            return

        self.cap = cap
        self._current_cam_index = index
        self._set_status(f"Camera {index} активна")
        self._start_preview()

    def _on_cam_selected(self, _event=None):
        sel = self.cam_var.get()
        if sel:
            idx = int(sel.split()[-1])
            # Stop capture if running with old camera
            if self.running:
                self.stop_capture()
            self.open_camera(idx)

    # ── Preview loop ───────────────────────────

    def _start_preview(self):
        self.preview_active = True
        self._preview_tick()

    def _stop_preview(self):
        self.preview_active = False
        if self._preview_job is not None:
            self.root.after_cancel(self._preview_job)
            self._preview_job = None

    def _preview_tick(self):
        """Called repeatedly via after() to update canvas with camera frame."""
        if not self.preview_active:
            return
        if self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            if ret:
                self._current_frame = frame
                self._draw_frame(frame)
            else:
                self._handle_cam_disconnect()
                return
        self._preview_job = self.root.after(PREVIEW_FPS_MS, self._preview_tick)

    def _draw_frame(self, bgr_frame):
        """Convert BGR frame to PhotoImage and render on canvas."""
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        img = img.resize((PREVIEW_W, PREVIEW_H), Image.BILINEAR)
        photo = ImageTk.PhotoImage(image=img)
        self.canvas.delete("placeholder")
        self.canvas.create_image(0, 0, anchor="nw", image=photo)
        self.canvas._photo_ref = photo  # prevent GC

    def _handle_cam_disconnect(self):
        self._stop_preview()
        if self.running:
            self.stop_capture()
        self._set_status("Камера отключилась во время работы.")
        messagebox.showwarning("Предупреждение", "Камера отключилась.")

    # ── Capture control ────────────────────────

    def start_capture(self):
        if not self.cam_var.get():
            messagebox.showwarning("Нет камеры", "Сначала выберите камеру.")
            return
        if not self.cap or not self.cap.isOpened():
            messagebox.showerror("Ошибка", "Камера недоступна.")
            return

        # Create save folder
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        folder_name = f"screenshots_{ts}"
        self.save_folder = os.path.join(get_base_dir(), folder_name)
        try:
            os.makedirs(self.save_folder, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Ошибка", f"Не удалось создать папку: {e}")
            return

        self.folder_var.set(self.save_folder)
        self.shot_count = 0
        self.counter_var.set("Снимков: 0")

        interval_sec = INTERVALS[self.interval_var.get()]
        self.running = True
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.cam_combo.config(state=tk.DISABLED)
        self.interval_combo.config(state=tk.DISABLED)

        self._set_status(
            f"Запись. Интервал: {self.interval_var.get()}. "
            f"Папка: {folder_name}"
        )

        # Schedule first screenshot after one interval
        self._capture_job = self.root.after(
            interval_sec * 1000, self._capture_tick
        )

    def _capture_tick(self):
        """Called by after() at each interval to save a screenshot."""
        if not self.running:
            return
        self.save_screenshot()
        interval_sec = INTERVALS[self.interval_var.get()]
        self._capture_job = self.root.after(
            interval_sec * 1000, self._capture_tick
        )

    def stop_capture(self):
        if not self.running:
            self._set_status("Запись не была запущена.")
            return
        if self._capture_job is not None:
            self.root.after_cancel(self._capture_job)
            self._capture_job = None
        self.running = False
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.cam_combo.config(state="readonly")
        self.interval_combo.config(state="readonly")
        self._set_status(
            f"Остановлено. Сохранено снимков: {self.shot_count}. "
            f"Папка: {self.save_folder}"
        )

    # ── Screenshot saving ──────────────────────

    def save_screenshot(self):
        if self._current_frame is None:
            return
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"shot_{ts}.jpg"
        filepath = os.path.join(self.save_folder, filename)
        try:
            cv2.imwrite(filepath, self._current_frame)
            self.shot_count += 1
            self.counter_var.set(f"Снимков: {self.shot_count}")
            self._set_status(
                f"Сохранён: {filename}  (всего: {self.shot_count})"
            )
        except Exception as e:
            self._set_status(f"Ошибка сохранения: {e}")

    # ── Cleanup ────────────────────────────────

    def on_close(self):
        self.stop_capture()
        self._stop_preview()
        if self.cap:
            self.cap.release()
        self.root.destroy()

    # ── Helpers ───────────────────────────────

    def _set_status(self, msg: str):
        self.status_var.set(msg)


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    root.geometry("900x650")
    app = App(root)
    root.mainloop()
