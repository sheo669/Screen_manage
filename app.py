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
import shutil
import traceback

INTERVALS = {
    "10 секунд": 10,
    "30 секунд": 30,
    "1 минута": 60,
    "5 минут": 300,
    "10 минут": 600,
    "30 минут": 1800,
    "1 час": 3600,
    "2 часа": 7200,
    "3 часа": 10800,
    "4 часа": 14400,
}

RESOLUTIONS = {
    "640x480 (VGA)": (640, 480),
    "800x600 (SVGA)": (800, 600),
    "1280x720 (HD)": (1280, 720),
    "1600x900": (1600, 900),
    "1920x1080 (Full HD)": (1920, 1080),
    "2560x1440 (2K)": (2560, 1440),
}

JPEG_QUALITIES = ["70", "80", "85", "90", "95", "100"]

PREVIEW_W = 640
PREVIEW_H = 480
PREVIEW_FPS_MS = 33
RECONNECT_DELAY_MS = 5000
DISK_FREE_MIN_MB = 200
CAMERA_SCAN_RANGE = 10


def get_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Camera Screenshot Capture")
        self.root.resizable(False, False)

        self.cap = None
        self.running = False
        self.preview_active = False
        self.shot_count = 0
        self.save_folder = ""
        self.log_file_path = os.path.join(get_base_dir(), "camera_capture.log")

        self._preview_job = None
        self._capture_job = None
        self._reconnect_job = None
        self._clock_job = None
        self._current_cam_index = -1
        self._current_frame = None
        self._photo_ref = None
        self._reconnect_attempts = 0
        self._closing = False
        self._next_capture_time = None
        self._last_saved_file = ""

        self._build_ui()
        self._write_log("Приложение запущено")
        self.scan_cameras()
        self._start_clock_updater()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        pad = 8
        ctrl = tk.Frame(self.root, padx=pad, pady=pad)
        ctrl.pack(side=tk.TOP, fill=tk.X)
        ctrl.grid_columnconfigure(4, weight=1)

        tk.Label(ctrl, text="Камера:").grid(row=0, column=0, sticky="w", padx=(0, 4))
        self.cam_var = tk.StringVar()
        self.cam_combo = ttk.Combobox(ctrl, textvariable=self.cam_var, state="readonly", width=18)
        self.cam_combo.grid(row=0, column=1, sticky="w")
        self.cam_combo.bind("<<ComboboxSelected>>", self._on_cam_selected)

        self.btn_refresh = tk.Button(ctrl, text="Обновить камеры", command=self.scan_cameras)
        self.btn_refresh.grid(row=0, column=2, padx=(8, 0), sticky="w")

        tk.Label(ctrl, text="Интервал:").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=(6, 0))
        self.interval_var = tk.StringVar(value="30 минут")
        self.interval_combo = ttk.Combobox(
            ctrl, textvariable=self.interval_var, values=list(INTERVALS.keys()), state="readonly", width=18
        )
        self.interval_combo.grid(row=1, column=1, sticky="w", pady=(6, 0))

        tk.Label(ctrl, text="Разрешение:").grid(row=1, column=2, sticky="w", padx=(12, 4), pady=(6, 0))
        self.resolution_var = tk.StringVar(value="1280x720 (HD)")
        self.resolution_combo = ttk.Combobox(
            ctrl, textvariable=self.resolution_var, values=list(RESOLUTIONS.keys()), state="readonly", width=20
        )
        self.resolution_combo.grid(row=1, column=3, sticky="w", pady=(6, 0))
        self.resolution_combo.bind("<<ComboboxSelected>>", self._on_resolution_selected)

        tk.Label(ctrl, text="JPEG quality:").grid(row=2, column=2, sticky="w", padx=(12, 4), pady=(6, 0))
        self.quality_var = tk.StringVar(value="95")
        self.quality_combo = ttk.Combobox(
            ctrl, textvariable=self.quality_var, values=JPEG_QUALITIES, state="readonly", width=20
        )
        self.quality_combo.grid(row=2, column=3, sticky="w", pady=(6, 0))

        self.btn_start = tk.Button(ctrl, text="▶ Start", width=10, bg="#2ecc71", fg="white", command=self.start_capture)
        self.btn_start.grid(row=0, column=5, padx=(16, 0), sticky="e")

        self.btn_stop = tk.Button(
            ctrl, text="■ Stop", width=10, bg="#e74c3c", fg="white", command=self.stop_capture, state=tk.DISABLED
        )
        self.btn_stop.grid(row=1, column=5, padx=(16, 0), pady=(6, 0), sticky="e")

        tk.Label(ctrl, text="Папка:").grid(row=2, column=0, sticky="w", padx=(0, 4), pady=(6, 0))
        self.folder_var = tk.StringVar(value="—")
        tk.Label(ctrl, textvariable=self.folder_var, anchor="w", width=52, relief="sunken", font=("Consolas", 8)).grid(
            row=2, column=1, columnspan=3, sticky="w", pady=(6, 0)
        )

        self.counter_var = tk.StringVar(value="Снимков: 0")
        tk.Label(ctrl, textvariable=self.counter_var, font=("Arial", 9, "bold")).grid(row=2, column=4, padx=(12, 0), pady=(6, 0))

        self.next_shot_var = tk.StringVar(value="Следующий снимок: —")
        tk.Label(ctrl, textvariable=self.next_shot_var, font=("Arial", 9)).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

        self.disk_var = tk.StringVar(value="Свободно на диске: —")
        tk.Label(ctrl, textvariable=self.disk_var, font=("Arial", 9)).grid(row=3, column=3, columnspan=2, sticky="w", pady=(8, 0))

        self.last_file_var = tk.StringVar(value="Последний файл: —")
        tk.Label(ctrl, textvariable=self.last_file_var, font=("Arial", 9)).grid(row=4, column=0, columnspan=5, sticky="w", pady=(4, 0))

        self.canvas = tk.Canvas(self.root, width=PREVIEW_W, height=PREVIEW_H, bg="black")
        self.canvas.pack(side=tk.TOP, padx=pad, pady=(0, pad))
        self.canvas.create_text(
            PREVIEW_W // 2, PREVIEW_H // 2, text="Выберите камеру", fill="gray", font=("Arial", 16), tags="placeholder"
        )

        self.status_var = tk.StringVar(value="Готово. Выберите камеру.")
        tk.Label(self.root, textvariable=self.status_var, anchor="w", relief="sunken", font=("Arial", 9)).pack(
            side=tk.BOTTOM, fill=tk.X, padx=pad, pady=(0, pad)
        )

    def scan_cameras(self):
        self._set_status("Поиск камер…")
        self.btn_refresh.config(state=tk.DISABLED)
        self.root.update_idletasks()

        found = []
        for idx in range(CAMERA_SCAN_RANGE):
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            try:
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret:
                        found.append(f"Camera {idx}")
            finally:
                cap.release()

        self.cam_combo["values"] = found
        self.btn_refresh.config(state=tk.NORMAL)

        if found:
            current = self.cam_var.get()
            if current in found:
                self.cam_combo.set(current)
                idx = int(current.split()[-1])
            else:
                self.cam_combo.current(0)
                idx = int(found[0].split()[-1])
            self._set_status(f"Найдено камер: {len(found)}")
            self.btn_start.config(state=tk.NORMAL)
            if not self.running:
                self.open_camera(idx)
        else:
            self.cam_var.set("")
            self._set_status("Камеры не найдены.")
            self.btn_start.config(state=tk.DISABLED)
            self._draw_placeholder("Камеры не найдены")

    def open_camera(self, index: int, show_message: bool = True) -> bool:
        if self._current_cam_index == index and self.cap and self.cap.isOpened():
            return True

        self._stop_preview()
        self._release_camera()

        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF]
        cap = None
        last_error = None

        for backend in backends:
            try:
                candidate = cv2.VideoCapture(index, backend)
                if not candidate.isOpened():
                    candidate.release()
                    continue

                candidate.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                width, height = RESOLUTIONS.get(self.resolution_var.get(), (1280, 720))
                candidate.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                candidate.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                candidate.set(cv2.CAP_PROP_FPS, 30)

                ret, frame = candidate.read()
                if ret and frame is not None:
                    cap = candidate
                    break

                candidate.release()
            except Exception as exc:
                last_error = exc

        if cap is None:
            self._current_cam_index = index
            msg = f"Не удалось открыть Camera {index}"
            if last_error:
                msg += f": {last_error}"
            self._write_log(msg)
            self._set_status(msg)
            self._draw_placeholder("Камера недоступна")
            if show_message:
                messagebox.showerror("Ошибка", msg)
            return False

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        _, frame = cap.read()
        self.cap = cap
        self._current_cam_index = index
        if frame is not None:
            self._current_frame = frame.copy()
            self._draw_frame(frame)
        self._set_status(
            f"Camera {index} активна | Запрошено: {RESOLUTIONS.get(self.resolution_var.get(), (1280, 720))[0]}x{RESOLUTIONS.get(self.resolution_var.get(), (1280, 720))[1]} | Фактически: {actual_w}x{actual_h}"
        )
        self._write_log(f"Камера {index} открыта. Фактическое разрешение: {actual_w}x{actual_h}")
        self._start_preview()
        return True

    def _on_cam_selected(self, _event=None):
        sel = self.cam_var.get()
        if not sel:
            return
        idx = int(sel.split()[-1])
        if self.running:
            self.stop_capture()
        self.open_camera(idx)

    def _on_resolution_selected(self, _event=None):
        if self.running:
            messagebox.showinfo("Информация", "Сначала остановите съёмку.")
            return
        if self.cam_var.get():
            idx = int(self.cam_var.get().split()[-1])
            self.open_camera(idx, show_message=False)

    def _start_preview(self):
        if self.preview_active:
            return
        self.preview_active = True
        self._preview_tick()

    def _stop_preview(self):
        self.preview_active = False
        if self._preview_job is not None:
            try:
                self.root.after_cancel(self._preview_job)
            except Exception:
                pass
            self._preview_job = None

    def _preview_tick(self):
        if not self.preview_active:
            return

        if self.cap and self.cap.isOpened():
            try:
                ret, frame = self.cap.read()
            except Exception as exc:
                self._write_log(f"Ошибка чтения кадра: {exc}")
                ret, frame = False, None

            if ret and frame is not None:
                self._current_frame = frame.copy()
                self._draw_frame(frame)
            else:
                self._write_log("Кадр получить не удалось, запускается переподключение камеры")
                self._handle_camera_failure()
                return

        self._preview_job = self.root.after(PREVIEW_FPS_MS, self._preview_tick)

    def _draw_frame(self, bgr_frame):
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        img = img.resize((PREVIEW_W, PREVIEW_H), Image.BILINEAR)
        photo = ImageTk.PhotoImage(image=img)
        self.canvas.delete("placeholder")
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=photo)
        self._photo_ref = photo

    def _draw_placeholder(self, text: str):
        self.canvas.delete("all")
        self.canvas.create_text(PREVIEW_W // 2, PREVIEW_H // 2, text=text, fill="gray", font=("Arial", 16))
        self._photo_ref = None

    def start_capture(self):
        if self.running:
            return
        if not self.cam_var.get():
            messagebox.showwarning("Нет камеры", "Сначала выберите камеру.")
            return
        if not self.cap or not self.cap.isOpened():
            idx = int(self.cam_var.get().split()[-1])
            if not self.open_camera(idx):
                return

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
        self.last_file_var.set("Последний файл: —")
        self._last_saved_file = ""

        self.running = True
        self._reconnect_attempts = 0
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.cam_combo.config(state=tk.DISABLED)
        self.interval_combo.config(state=tk.DISABLED)
        self.resolution_combo.config(state=tk.DISABLED)
        self.quality_combo.config(state=tk.DISABLED)
        self.btn_refresh.config(state=tk.DISABLED)

        interval_sec = INTERVALS[self.interval_var.get()]
        self._next_capture_time = datetime.datetime.now() + datetime.timedelta(seconds=interval_sec)
        self._write_log(f"Старт съёмки. Интервал: {interval_sec} сек. Папка: {self.save_folder}")
        self._update_next_shot_label()
        self._update_disk_space_label()
        self._set_status(f"Запись запущена. Интервал: {self.interval_var.get()}.")
        self._schedule_next_capture()

    def _schedule_next_capture(self):
        if not self.running or self._next_capture_time is None:
            return
        if self._capture_job is not None:
            try:
                self.root.after_cancel(self._capture_job)
            except Exception:
                pass
            self._capture_job = None

        delay_ms = max(200, int((self._next_capture_time - datetime.datetime.now()).total_seconds() * 1000))
        self._capture_job = self.root.after(delay_ms, self._capture_tick)
        self._update_next_shot_label()

    def _capture_tick(self):
        self._capture_job = None
        if not self.running:
            return

        now = datetime.datetime.now()
        interval_sec = INTERVALS[self.interval_var.get()]
        success = self.save_screenshot()

        if self._next_capture_time is None:
            self._next_capture_time = now + datetime.timedelta(seconds=interval_sec)
        else:
            self._next_capture_time += datetime.timedelta(seconds=interval_sec)
            while self._next_capture_time <= datetime.datetime.now():
                self._next_capture_time += datetime.timedelta(seconds=interval_sec)

        if not success:
            self._write_log("Снимок не сохранён в запланированный момент")
        self._schedule_next_capture()

    def stop_capture(self):
        if self._capture_job is not None:
            try:
                self.root.after_cancel(self._capture_job)
            except Exception:
                pass
            self._capture_job = None

        was_running = self.running
        self.running = False
        self._next_capture_time = None
        self.next_shot_var.set("Следующий снимок: —")

        self.btn_start.config(state=tk.NORMAL if self.cam_var.get() else tk.DISABLED)
        self.btn_stop.config(state=tk.DISABLED)
        self.cam_combo.config(state="readonly")
        self.interval_combo.config(state="readonly")
        self.resolution_combo.config(state="readonly")
        self.quality_combo.config(state="readonly")
        self.btn_refresh.config(state=tk.NORMAL)

        if was_running:
            self._write_log(f"Съёмка остановлена. Сохранено снимков: {self.shot_count}")
            self._set_status(f"Остановлено. Сохранено снимков: {self.shot_count}. Папка: {self.save_folder}")
        else:
            self._set_status("Запись не была запущена.")

    def save_screenshot(self) -> bool:
        if self._current_frame is None:
            self._set_status("Нет текущего кадра для сохранения")
            return False

        if not self.save_folder:
            self._set_status("Не выбрана папка для сохранения")
            return False

        if not self._check_disk_space():
            self._write_log("Недостаточно свободного места на диске")
            self._set_status("Съёмка остановлена: мало места на диске")
            self.stop_capture()
            messagebox.showerror("Ошибка", f"Свободного места меньше {DISK_FREE_MIN_MB} МБ. Съёмка остановлена.")
            return False

        if not os.path.isdir(self.save_folder):
            try:
                os.makedirs(self.save_folder, exist_ok=True)
                self._write_log("Папка сохранения была восстановлена")
            except Exception as exc:
                self._write_log(f"Не удалось восстановить папку сохранения: {exc}")
                self._set_status(f"Ошибка папки сохранения: {exc}")
                return False

        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"shot_{ts}.jpg"
        filepath = os.path.join(self.save_folder, filename)
        jpeg_quality = int(self.quality_var.get())

        frame_to_save = self._current_frame.copy()

        try:
            ok = cv2.imwrite(filepath, frame_to_save, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
            if not ok:
                raise IOError("cv2.imwrite вернул False")
            if not os.path.exists(filepath) or os.path.getsize(filepath) <= 0:
                raise IOError("Файл не был записан корректно")

            self.shot_count += 1
            self._last_saved_file = filepath
            self.counter_var.set(f"Снимков: {self.shot_count}")
            self.last_file_var.set(f"Последний файл: {filename}")
            self._update_disk_space_label()
            self._set_status(f"Сохранён: {filename} | quality={jpeg_quality} | всего: {self.shot_count}")
            self._write_log(f"Сохранён файл: {filepath}")
            return True
        except Exception as exc:
            self._write_log(f"Ошибка сохранения снимка: {exc}\n{traceback.format_exc()}")
            self._set_status(f"Ошибка сохранения: {exc}")
            return False

    def _handle_camera_failure(self):
        self._stop_preview()
        self._release_camera()
        self._draw_placeholder("Камера отключена. Попытка переподключения...")
        if self._reconnect_job is None and not self._closing:
            self._set_status("Потеряна камера. Пытаюсь переподключить...")
            self._schedule_reconnect()

    def _schedule_reconnect(self):
        if self._reconnect_job is not None or self._closing:
            return
        self._reconnect_job = self.root.after(RECONNECT_DELAY_MS, self._attempt_reconnect)

    def _attempt_reconnect(self):
        self._reconnect_job = None
        if self._closing:
            return
        if self._current_cam_index < 0:
            return

        self._reconnect_attempts += 1
        self._set_status(f"Переподключение камеры, попытка {self._reconnect_attempts}...")
        self._write_log(f"Попытка переподключения камеры #{self._reconnect_attempts}")

        ok = self.open_camera(self._current_cam_index, show_message=False)
        if ok:
            self._reconnect_attempts = 0
            self._set_status("Камера переподключена")
            self._write_log("Камера успешно переподключена")
            if self.running and self._next_capture_time is not None:
                self._schedule_next_capture()
        else:
            self._schedule_reconnect()

    def _release_camera(self):
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None

    def _check_disk_space(self) -> bool:
        try:
            path = self.save_folder or get_base_dir()
            usage = shutil.disk_usage(path)
            free_mb = usage.free / (1024 * 1024)
            self.disk_var.set(f"Свободно на диске: {free_mb:.1f} МБ")
            return free_mb >= DISK_FREE_MIN_MB
        except Exception as exc:
            self._write_log(f"Не удалось проверить место на диске: {exc}")
            return True

    def _update_disk_space_label(self):
        self._check_disk_space()

    def _update_next_shot_label(self):
        if self.running and self._next_capture_time is not None:
            self.next_shot_var.set("Следующий снимок: " + self._next_capture_time.strftime("%Y-%m-%d %H:%M:%S"))
        else:
            self.next_shot_var.set("Следующий снимок: —")

    def _start_clock_updater(self):
        self._clock_tick()

    def _clock_tick(self):
        self._update_next_shot_label()
        self._update_disk_space_label()
        self._clock_job = self.root.after(1000, self._clock_tick)

    def _write_log(self, message: str):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        try:
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

    def _set_status(self, msg: str):
        self.status_var.set(msg)
        self._write_log(f"STATUS: {msg}")

    def on_close(self):
        self._closing = True
        self.stop_capture()
        self._stop_preview()

        for job_name in ("_reconnect_job", "_clock_job"):
            job = getattr(self, job_name)
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
                setattr(self, job_name, None)

        self._release_camera()
        self._write_log("Приложение закрыто")
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    root.geometry("900x690")
    app = App(root)
    root.mainloop()
