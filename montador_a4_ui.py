
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import base64
import ctypes
import io
import json
import os
import threading
import urllib.error
import urllib.request
import uuid
from ctypes import wintypes
from datetime import datetime
from PIL import Image, ImageTk, ImageOps, ImageDraw, ImageWin, ImageFont, ImageFilter
from buttons_tab import ButtonsTab

DPI = 300
A4_W, A4_H = 2480, 3508
CRICUT_MAX_CM = 13.9
PHOTO_W = int(round(60 / 25.4 * DPI))
PHOTO_H = int(round(90 / 25.4 * DPI))
COLS, ROWS = 3, 3
GAP_X = GAP_Y = int(round(5 / 25.4 * DPI))
GRID_W = COLS * PHOTO_W + (COLS - 1) * GAP_X
GRID_H = ROWS * PHOTO_H + (ROWS - 1) * GAP_Y
MARGIN_X = (A4_W - GRID_W) // 2
MARGIN_Y = (A4_H - GRID_H) // 2

BG = "#F4F1EA"
PANEL = "#FFFFFF"
ACCENT = "#C58A3A"
TEXT = "#222222"
MUTED = "#6E6E6E"
BORDER = "#D9D3C7"
API_KEYS_FILE = Path(__file__).with_name(".api_keys.dat")
WHEEL_SCROLL_LINES = 3
SAVE_DATE_FORMAT = "%d-%m-%Y %H-%M-%S"


def save_timestamp():
    return datetime.now().strftime(SAVE_DATE_FORMAT)


class DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


class DOCINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_int),
        ("lpszDocName", wintypes.LPCWSTR),
        ("lpszOutput", wintypes.LPCWSTR),
        ("lpszDatatype", wintypes.LPCWSTR),
        ("fwType", wintypes.DWORD),
    ]


class PRINTDLGW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hDevMode", wintypes.HGLOBAL),
        ("hDevNames", wintypes.HGLOBAL),
        ("hDC", wintypes.HDC),
        ("Flags", wintypes.DWORD),
        ("nFromPage", wintypes.WORD),
        ("nToPage", wintypes.WORD),
        ("nMinPage", wintypes.WORD),
        ("nMaxPage", wintypes.WORD),
        ("nCopies", wintypes.WORD),
        ("hInstance", wintypes.HINSTANCE),
        ("lCustData", wintypes.LPARAM),
        ("lpfnPrintHook", ctypes.c_void_p),
        ("lpfnSetupHook", ctypes.c_void_p),
        ("lpPrintTemplateName", wintypes.LPCWSTR),
        ("lpSetupTemplateName", wintypes.LPCWSTR),
        ("hPrintTemplate", wintypes.HGLOBAL),
        ("hSetupTemplate", wintypes.HGLOBAL),
    ]


def cover_fit(image, target_w, target_h, zoom=1.0, offset_y=0):
    image = ImageOps.exif_transpose(image).convert("RGB")
    scale = max(target_w / image.width, target_h / image.height) * zoom
    nw = max(1, int(image.width * scale))
    nh = max(1, int(image.height * scale))
    image = image.resize((nw, nh), Image.Resampling.LANCZOS)

    # Centraliza a imagem. Se ela for maior, corta o excesso; se for
    # menor, mantém uma borda branca dentro do espaço de 6 x 9 cm.
    # offset_y: deslocamento vertical em pixels (positivo = pra baixo)
    result = Image.new("RGB", (target_w, target_h), "white")
    source_left = max(0, (nw - target_w) // 2)
    source_top = max(0, (nh - target_h) // 2 - offset_y)
    source_right = min(nw, source_left + target_w)
    source_bottom = min(nh, source_top + target_h)
    visible = image.crop((source_left, source_top, source_right, source_bottom))
    destination_x = max(0, (target_w - visible.width) // 2)
    destination_y = max(0, (target_h - visible.height) // 2)
    result.paste(visible, (destination_x, destination_y))
    return result


class SlotCard:
    def __init__(self, parent, index, callback, app=None):
        self.index = index
        self.callback = callback
        self.app = app
        self.path = None
        self.rotation = 0
        self.zoom = 1.0
        self.offset_y = 0
        self.tk_img = None

        self.frame = tk.Frame(
            parent, bg=PANEL,
            highlightbackground=BORDER, highlightthickness=1
        )

        self.title = tk.Label(
            self.frame, text=f"Foto {index+1:02d}",
            bg=PANEL, fg=TEXT, font=("Segoe UI", 10, "bold")
        )
        self.title.pack(anchor="w", padx=10, pady=(8, 4))

        self.preview = tk.Label(
            self.frame, text="Clique para adicionar",
            bg="#ECE8DF", fg=MUTED, font=("Segoe UI", 9),
            cursor="hand2"
        )
        self.preview.pack(fill="both", expand=True, padx=10)
        self.preview.bind("<Button-1>", lambda e: self.choose())

        controls = tk.Frame(self.frame, bg=PANEL)
        controls.pack(fill="x", padx=8, pady=8)

        self._button(controls, "Adicionar", self.choose).pack(side="left", padx=2)
        self._button(controls, "Clone", self.clone, 8).pack(side="left", padx=2)
        self._button(controls, "↺", lambda: self.rotate(-90), 4).pack(side="left", padx=2)
        self._button(controls, "↻", lambda: self.rotate(90), 4).pack(side="left", padx=2)
        self._button(controls, "✕", self.clear, 4).pack(side="right", padx=2)

        resize_controls = tk.Frame(self.frame, bg=PANEL)
        resize_controls.pack(fill="x", padx=8, pady=(0, 8))
        self._button(resize_controls, "−", lambda: self.resize_photo(-0.05), 3).pack(side="left", padx=2)
        self.zoom_label = tk.Label(
            resize_controls, text="100%", width=6, bg=PANEL, fg=TEXT,
            font=("Segoe UI", 8, "bold")
        )
        self.zoom_label.pack(side="left", padx=2)
        self._button(resize_controls, "+", lambda: self.resize_photo(0.05), 3).pack(side="left", padx=2)
        self._button(resize_controls, "Restaurar", self.reset_zoom, 8).pack(side="right", padx=2)

        offset_controls = tk.Frame(self.frame, bg=PANEL)
        offset_controls.pack(fill="x", padx=8, pady=(0, 8))
        tk.Label(
            offset_controls, text="↕", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 9)
        ).pack(side="left", padx=2)
        self._button(offset_controls, "▲", lambda: self.move_photo(-15), 3).pack(side="left", padx=2)
        self.offset_label = tk.Label(
            offset_controls, text="0", width=4, bg=PANEL, fg=TEXT,
            font=("Segoe UI", 8, "bold")
        )
        self.offset_label.pack(side="left", padx=2)
        self._button(offset_controls, "▼", lambda: self.move_photo(15), 3).pack(side="left", padx=2)

    def _button(self, parent, text, command, width=8):
        return tk.Button(
            parent, text=text, command=command, width=width,
            bg="#EFEAE0", fg=TEXT, activebackground="#E4D8C5",
            relief="flat", font=("Segoe UI", 9), cursor="hand2"
        )

    def choose(self):
        try:
            path = filedialog.askopenfilename(
                title=f"Escolher foto {self.index+1}",
                filetypes=[("Imagens", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff")]
            )
        except KeyboardInterrupt:
            return
        if path:
            self.path = path
            self.rotation = 0
            self.zoom = 1.0
            self.refresh()
            self.callback()

    def rotate(self, angle):
        if not self.path:
            return
        self.rotation = (self.rotation + angle) % 360
        self.refresh()
        self.callback()

    def resize_photo(self, amount):
        if not self.path:
            return
        self.zoom = min(3.0, max(0.25, round(self.zoom + amount, 2)))
        self.refresh()
        self.callback()

    def reset_zoom(self):
        if not self.path:
            return
        self.zoom = 1.0
        self.refresh()
        self.callback()

    def move_photo(self, amount):
        if not self.path:
            return
        self.offset_y = max(-300, min(300, self.offset_y + amount))
        self.offset_label.configure(text=str(self.offset_y))
        self.callback()

    def clear(self):
        self.path = None
        self.rotation = 0
        self.zoom = 1.0
        self.offset_y = 0
        self.tk_img = None
        self.preview.configure(image="", text="Clique para adicionar")
        self.zoom_label.configure(text="100%")
        self.offset_label.configure(text="0")
        self.callback()

    def clone(self):
        if not self.path:
            messagebox.showinfo("Sem foto", "Adicione uma foto antes de clonar.")
            return
        target = self.app.get_empty_slot() if self.app else None
        if target is None:
            messagebox.showinfo("Sem espaço", "Não há slots vazios para receber o clone.")
            return
        target.path = self.path
        target.rotation = self.rotation
        target.zoom = self.zoom
        target.offset_y = self.offset_y
        target.tk_img = None
        target.refresh()
        target.callback()
        self.callback()

    def get_image(self):
        if not self.path:
            return None
        try:
            img = Image.open(self.path)
            img = ImageOps.exif_transpose(img)
            if self.rotation:
                img = img.rotate(-self.rotation, expand=True)
            return img.convert("RGB")
        except Exception as exc:
            messagebox.showerror("Erro", f"Não foi possível abrir a imagem.\n\n{exc}")
            return None

    def refresh(self):
        img = self.get_image()
        if img is None:
            return
        pw = max(50, self.preview.winfo_width() - 10)
        ph = max(50, self.preview.winfo_height() - 10)
        thumb = cover_fit(img, pw, ph, self.zoom)
        thumb.thumbnail((pw, ph), Image.Resampling.LANCZOS)
        self.tk_img = ImageTk.PhotoImage(thumb)
        self.preview.configure(image=self.tk_img, text="")
        self.zoom_label.configure(text=f"{round(self.zoom * 100)}%")


class PolaroidSlotCard:
    def __init__(self, parent, index, callback):
        self.index = index
        self.callback = callback
        self.path = None
        self.rotation = 0
        self.offset_y = 0
        self.tk_img = None

        self.frame = tk.Frame(
            parent, bg=PANEL,
            highlightbackground=BORDER, highlightthickness=1
        )

        top = tk.Frame(self.frame, bg=PANEL)
        top.pack(fill="x", padx=8, pady=(6, 2))

        self.title = tk.Label(
            top, text=f"Polaroid {index+1:02d}",
            bg=PANEL, fg=TEXT, font=("Segoe UI", 9, "bold")
        )
        self.title.pack(side="left")

        self.file_label = tk.Label(
            top, text="Sem foto", bg=PANEL, fg=MUTED,
            font=("Segoe UI", 8)
        )
        self.file_label.pack(side="right")

        self.preview = tk.Label(
            self.frame, text="Clique para adicionar",
            bg="#ECE8DF", fg=MUTED, font=("Segoe UI", 8),
            cursor="hand2"
        )
        self.preview.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.preview.bind("<Button-1>", lambda e: self.choose())

        controls = tk.Frame(self.frame, bg=PANEL)
        controls.pack(fill="x", padx=8, pady=(0, 6))

        self._button(controls, "Adicionar", self.choose, 8).pack(side="left", padx=2)
        self._button(controls, "↺", lambda: self.rotate(-90), 3).pack(side="left", padx=2)
        self._button(controls, "↻", lambda: self.rotate(90), 3).pack(side="left", padx=2)
        self._button(controls, "▲", lambda: self.move_photo(-15), 3).pack(side="left", padx=2)
        self._button(controls, "▼", lambda: self.move_photo(15), 3).pack(side="left", padx=2)
        self._button(controls, "✕", self.clear, 3).pack(side="right", padx=2)

    def _button(self, parent, text, command, width=8):
        return tk.Button(
            parent, text=text, command=command, width=width,
            bg="#EFEAE0", fg=TEXT, activebackground="#E4D8C5",
            relief="flat", font=("Segoe UI", 8), cursor="hand2"
        )

    def choose(self):
        try:
            path = filedialog.askopenfilename(
                title=f"Escolher foto {self.index+1}",
                filetypes=[("Imagens", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff")]
            )
        except KeyboardInterrupt:
            return
        if path:
            self.path = path
            self.rotation = 0
            self.refresh()
            self.callback()

    def rotate(self, angle):
        if not self.path:
            return
        self.rotation = (self.rotation + angle) % 360
        self.refresh()
        self.callback()

    def move_photo(self, amount):
        if not self.path:
            return
        self.offset_y = max(-300, min(300, self.offset_y + amount))
        self.callback()

    def clear(self):
        self.path = None
        self.rotation = 0
        self.offset_y = 0
        self.tk_img = None
        self.preview.configure(image="", text="Clique para adicionar")
        self.file_label.configure(text="Sem foto")
        self.callback()

    def get_image(self):
        if not self.path:
            return None
        try:
            img = Image.open(self.path)
            img = ImageOps.exif_transpose(img)
            if self.rotation:
                img = img.rotate(-self.rotation, expand=True)
            return img.convert("RGB")
        except Exception:
            return None

    def refresh(self):
        img = self.get_image()
        if img is None:
            return
        thumb = img.copy()
        pw = max(40, self.preview.winfo_width() - 4)
        ph = max(40, self.preview.winfo_height() - 4)
        thumb.thumbnail((pw, ph), Image.Resampling.LANCZOS)
        self.tk_img = ImageTk.PhotoImage(thumb)
        self.preview.configure(image=self.tk_img, text="")
        name = Path(self.path).name
        self.file_label.configure(text=name if len(name) <= 22 else name[:19] + "...")


class VinylSlotCard:
    def __init__(self, parent, index, callback):
        self.index = index
        self.callback = callback
        self.path = None
        self.rotation = 0
        self.tk_img = None

        self.frame = tk.Frame(
            parent, bg=PANEL,
            highlightbackground=BORDER, highlightthickness=1
        )

        top = tk.Frame(self.frame, bg=PANEL)
        top.pack(fill="x", padx=8, pady=(6, 2))

        self.title = tk.Label(
            top, text=f"Adesivo {index+1:02d}",
            bg=PANEL, fg=TEXT, font=("Segoe UI", 9, "bold")
        )
        self.title.pack(side="left")

        self.file_label = tk.Label(
            top, text="Sem foto", bg=PANEL, fg=MUTED,
            font=("Segoe UI", 8)
        )
        self.file_label.pack(side="right")

        self.preview = tk.Label(
            self.frame, text="Clique para adicionar",
            bg="#ECE8DF", fg=MUTED, font=("Segoe UI", 8),
            cursor="hand2"
        )
        self.preview.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.preview.bind("<Button-1>", lambda e: self.choose())

        controls = tk.Frame(self.frame, bg=PANEL)
        controls.pack(fill="x", padx=8, pady=(0, 6))

        self._button(controls, "Adicionar", self.choose, 8).pack(side="left", padx=2)
        self._button(controls, "↺", lambda: self.rotate(-90), 3).pack(side="left", padx=2)
        self._button(controls, "↻", lambda: self.rotate(90), 3).pack(side="left", padx=2)
        self._button(controls, "✕", self.clear, 3).pack(side="right", padx=2)

    def _button(self, parent, text, command, width=8):
        return tk.Button(
            parent, text=text, command=command, width=width,
            bg="#EFEAE0", fg=TEXT, activebackground="#E4D8C5",
            relief="flat", font=("Segoe UI", 8), cursor="hand2"
        )

    def choose(self):
        try:
            path = filedialog.askopenfilename(
                title=f"Escolher adesivo {self.index+1}",
                filetypes=[("Imagens", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff")]
            )
        except KeyboardInterrupt:
            return
        if path:
            self.path = path
            self.rotation = 90
            self.refresh()
            self.callback()

    def rotate(self, angle):
        if not self.path:
            return
        self.rotation = (self.rotation + angle) % 360
        self.refresh()
        self.callback()

    def clear(self):
        self.path = None
        self.rotation = 0
        self.tk_img = None
        self.preview.configure(image="", text="Clique para adicionar")
        self.file_label.configure(text="Sem foto")
        self.callback()

    def get_image(self):
        if not self.path:
            return None
        try:
            img = Image.open(self.path)
            img = ImageOps.exif_transpose(img)
            if self.rotation:
                img = img.rotate(-self.rotation, expand=True)
            if img.mode == "RGBA":
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[3])
                return bg
            return img.convert("RGB")
        except Exception:
            return None

    def refresh(self):
        img = self.get_image()
        if img is None:
            return
        thumb = img.copy()
        pw = max(40, self.preview.winfo_width() - 4)
        ph = max(40, self.preview.winfo_height() - 4)
        thumb.thumbnail((pw, ph), Image.Resampling.LANCZOS)
        self.tk_img = ImageTk.PhotoImage(thumb)
        self.preview.configure(image=self.tk_img, text="")
        name = Path(self.path).name
        self.file_label.configure(text=name if len(name) <= 22 else name[:19] + "...")


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Montador A4 • Fotos 6×9 cm")
        self.root.geometry("1280x800")
        self.root.minsize(700, 520)
        self.root.configure(bg=BG)

        self.status_var = tk.StringVar(value="Nenhuma foto adicionada")
        self.border_var = tk.BooleanVar(value=False)
        self.montage_duplicate_var = tk.BooleanVar(value=False)
        self.verso_var = tk.BooleanVar(value=False)
        self.verso_offset_y = tk.StringVar(value="0")
        self.preview_img = None
        self.individual_path = None
        self.individual_rotation = 0
        self.individual_ai_image = None
        self.individual_preview_img = None
        self.individual_width = tk.StringVar(value="9")
        self.individual_height = tk.StringVar(value="6")
        self.ai_provider = tk.StringVar(value="OpenAI")
        self.openai_api_key = tk.StringVar()
        self.gemini_api_key = tk.StringVar()
        self.ai_status = tk.StringVar(value="Escolha uma foto e descreva a melhoria.")
        self.keys_status = tk.StringVar(value="As chaves são protegidas pelo Windows.")
        self.lapel_path = None
        self.lapel_rotation = 0
        self.lapel_preview_img = None
        self.lapel_width = tk.StringVar(value="3")
        self.lapel_height = tk.StringVar(value="3")
        self.lapel_border_top = tk.StringVar(value="1")
        self.lapel_border_bottom = tk.StringVar(value="1")
        self.lapel_gap = tk.StringVar(value="0.5")
        self.lapel_width.trace_add("write", self.update_lapel_sheet_count)
        self.lapel_height.trace_add("write", self.update_lapel_sheet_count)
        self.lapel_border_top.trace_add("write", self.update_lapel_sheet_count)
        self.lapel_border_bottom.trace_add("write", self.update_lapel_sheet_count)
        self.lapel_gap.trace_add("write", self.update_lapel_sheet_count)
        self.polaroid_path = None
        self.polaroid_rotation = 0
        self.polaroid_preview_img = None
        self.polaroid_preview_idx = 0
        self.polaroid_total_w = tk.StringVar(value="5.4")
        self.polaroid_total_h = tk.StringVar(value="8.6")
        self.polaroid_img_w = tk.StringVar(value="4.6")
        self.polaroid_img_h = tk.StringVar(value="6.2")
        self.polaroid_border_var = tk.BooleanVar(value=False)
        self.polaroid_duplicate_var = tk.BooleanVar(value=False)
        self.polaroid_count_var = tk.StringVar(value="")
        self.polaroid_num_var = tk.IntVar(value=9)
        self.polaroid_border_top = tk.StringVar(value="0.5")
        self.polaroid_border_left = tk.StringVar(value="0.4")
        self.polaroid_total_w.trace_add("write", self.update_polaroid_sheet_count)
        self.polaroid_total_h.trace_add("write", self.update_polaroid_sheet_count)
        self.polaroid_img_w.trace_add("write", self.update_polaroid_sheet_count)
        self.polaroid_img_h.trace_add("write", self.update_polaroid_sheet_count)

        self.vinyl_total_w = tk.StringVar(value="5")
        self.vinyl_total_h = tk.StringVar(value="5")
        self.vinyl_img_w = tk.StringVar(value="5")
        self.vinyl_img_h = tk.StringVar(value="5")
        self.vinyl_border_var = tk.BooleanVar(value=False)
        self.vinyl_transparent_var = tk.BooleanVar(value=False)
        self.vinyl_count_var = tk.StringVar(value="")
        self.vinyl_num_top = tk.IntVar(value=6)
        self.vinyl_num_bottom = tk.IntVar(value=6)
        self.vinyl_total_w.trace_add("write", self.update_vinyl_sheet_count)
        self.vinyl_total_h.trace_add("write", self.update_vinyl_sheet_count)
        self.vinyl_img_w.trace_add("write", self.update_vinyl_sheet_count)
        self.vinyl_img_h.trace_add("write", self.update_vinyl_sheet_count)
        self.vinyl_num_top.trace_add("write", self.update_vinyl_sheet_count)
        self.vinyl_num_bottom.trace_add("write", self.update_vinyl_sheet_count)

        self.load_api_keys(show_errors=False)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)
        self.montage_tab = tk.Frame(self.notebook, bg=BG)
        self.individual_tab = tk.Frame(self.notebook, bg=BG)
        self.lapel_tab = tk.Frame(self.notebook, bg=BG)
        self.polaroid_tab = tk.Frame(self.notebook, bg=BG)
        self.vinyl_tab = tk.Frame(self.notebook, bg=BG)
        self.buttons_tab = tk.Frame(self.notebook, bg=BG)
        self.settings_tab = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.montage_tab, text="Montagem A4")
        self.notebook.add(self.individual_tab, text="Foto individual")
        self.notebook.add(self.lapel_tab, text="Lapela")
        self.notebook.add(self.polaroid_tab, text="Polaroid")
        self.notebook.add(self.vinyl_tab, text="Vinil")
        self.notebook.add(self.buttons_tab, text="Botões")
        self.notebook.add(self.settings_tab, text="Chaves de API")

        self.build_header()
        self.build_body()
        self.build_footer()
        self.build_individual_tab()
        self.build_lapel_tab()
        self.build_polaroid_tab()
        self.build_vinyl_tab()
        self.buttons_instance = ButtonsTab(self.buttons_tab, self.root)
        self.build_settings_tab()
        self.refresh_preview()

    def build_header(self):
        self.montage_header = tk.Frame(
            self.montage_tab, bg=PANEL, height=76,
            highlightbackground=BORDER, highlightthickness=1
        )
        self.montage_header.pack(fill="x")
        self.montage_header.pack_propagate(False)

        self.header_title = tk.Frame(self.montage_header, bg=PANEL)
        self.header_title.pack(side="left", padx=24, pady=12)

        tk.Label(self.header_title, text="Montador A4", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(self.header_title, text="9 fotos de 6 × 9 cm • 300 DPI",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w")

        self.header_actions = tk.Frame(self.montage_header, bg=PANEL)
        self.header_actions.pack(side="right", padx=24)

        self.action_button(self.header_actions, "Imprimir", self.print_montage).pack(side="left", padx=5)
        self.action_button(self.header_actions, "Adicionar 9 fotos", self.select_multiple, secondary=True).pack(side="left", padx=5)
        self.action_button(self.header_actions, "Limpar tudo", self.clear_all, secondary=True).pack(side="left", padx=5)
        self.montage_header.bind(
            "<Configure>",
            lambda event: self.root.after_idle(
                lambda: self.apply_header_layout(event.width)
            )
        )

    def apply_header_layout(self, width):
        mode = "compact" if width < 900 else "wide"
        if getattr(self, "header_layout_mode", None) == mode:
            return
        self.header_title.pack_forget()
        self.header_actions.pack_forget()
        if mode == "compact":
            self.montage_header.configure(height=126)
            self.header_title.pack(fill="x", padx=18, pady=(8, 2))
            self.header_actions.pack(anchor="w", padx=13, pady=(2, 8))
        else:
            self.montage_header.configure(height=76)
            self.header_title.pack(side="left", padx=24, pady=12)
            self.header_actions.pack(side="right", padx=24)
        self.header_layout_mode = mode

    def build_body(self):
        body_container = tk.Frame(self.montage_tab, bg=BG)
        body_container.pack(fill="both", expand=True, padx=18, pady=18)

        self.body_canvas = tk.Canvas(body_container, bg=BG, highlightthickness=0)
        self.body_canvas.pack(side="left", fill="both", expand=True)

        scrollbar = tk.Scrollbar(body_container, orient="vertical", command=self.body_canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.body_canvas.configure(yscrollcommand=scrollbar.set)

        self.body_frame = tk.Frame(self.body_canvas, bg=BG)
        self.body_window = self.body_canvas.create_window((0, 0), window=self.body_frame, anchor="nw")

        def _update_scrollregion(event=None):
            self.body_canvas.configure(scrollregion=self.body_canvas.bbox("all"))
        self.body_frame.bind("<Configure>", _update_scrollregion)
        self.body_canvas.bind("<Configure>", self.on_body_resize)
        self.body_canvas.bind("<Enter>", self.bind_body_mousewheel)
        self.body_canvas.bind("<Leave>", self.unbind_body_mousewheel)

        self.photos_panel = tk.Frame(self.body_frame, bg=BG)
        self.photos_panel.pack(side="left", fill="both", expand=True)

        grid_header = tk.Frame(self.photos_panel, bg=BG)
        grid_header.pack(fill="x", pady=(0, 8))

        tk.Label(grid_header, text="Fotos", bg=BG, fg=TEXT,
                 font=("Segoe UI", 14, "bold")).pack(side="left")
        tk.Checkbutton(
            grid_header, text="Borda preta nas fotos", variable=self.border_var,
            command=self.refresh_preview, bg=BG, fg=TEXT, activebackground=BG,
            font=("Segoe UI", 9)
        ).pack(side="right", padx=8)
        tk.Checkbutton(
            grid_header, text="Duplicar fotos para preencher A4", variable=self.montage_duplicate_var,
            bg=BG, fg=TEXT, activebackground=BG,
            font=("Segoe UI", 9)
        ).pack(side="right", padx=8)

        self.photos_grid = tk.Frame(self.photos_panel, bg=BG)
        self.photos_grid.pack(fill="both", expand=True)

        self.slots = []
        for i in range(9):
            card = SlotCard(self.photos_grid, i, self.refresh_preview, app=self)
            card.frame.grid(row=i//3, column=i%3, padx=6, pady=6, sticky="nsew")
            self.slots.append(card)
        self.current_grid_columns = 3
        self.configure_photo_grid(3)

        self.sheet_panel = tk.Frame(
            self.body_frame, bg=PANEL, width=390,
            highlightbackground=BORDER, highlightthickness=1
        )
        self.sheet_panel.pack(side="right", fill="y", padx=(16, 0))
        self.sheet_panel.pack_propagate(False)

        tk.Label(self.sheet_panel, text="Prévia da folha", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=18, pady=(18, 4))
        tk.Label(self.sheet_panel, text="A4 vertical • escala real",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=18)

        self.canvas = tk.Canvas(self.sheet_panel, bg="#E5E0D6", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=18, pady=(16, 8))

        rotate_row = tk.Frame(self.sheet_panel, bg=PANEL)
        rotate_row.pack(fill="x", padx=18, pady=(0, 6))
        tk.Label(rotate_row, text="Foto:", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 9)).pack(side="left")
        self.rotate_slot_var = tk.IntVar(value=1)
        tk.Spinbox(
            rotate_row, from_=1, to=9, width=4, textvariable=self.rotate_slot_var,
            font=("Segoe UI", 9)
        ).pack(side="left", padx=(4, 8))
        self.action_button(
            rotate_row, "Girar ↺", lambda: self.rotate_slot(-90), secondary=True
        ).pack(side="left", padx=(0, 4))
        self.action_button(
            rotate_row, "Girar ↻", lambda: self.rotate_slot(90), secondary=True
        ).pack(side="left")

        export_row = tk.Frame(self.sheet_panel, bg=PANEL)
        export_row.pack(fill="x", padx=18, pady=(0, 10))
        self.action_button(
            export_row, "Exportar A4", self.export_sheet
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.action_button(
            export_row, "Exportar verso", self.export_verso
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        info = tk.Frame(self.sheet_panel, bg="#F7F3EB")
        info.pack(fill="x", padx=18, pady=(0, 12))

        rows = [
            ("Papel", "A4 — 210 × 297 mm"),
            ("Quantidade", "9 fotos"),
            ("Tamanho", "60 × 90 mm"),
            ("Resolução", "300 DPI"),
        ]
        for label, value in rows:
            line = tk.Frame(info, bg="#F7F3EB")
            line.pack(fill="x", padx=10, pady=4)
            tk.Label(line, text=label, bg="#F7F3EB", fg=MUTED,
                     font=("Segoe UI", 9)).pack(side="left")
            tk.Label(line, text=value, bg="#F7F3EB", fg=TEXT,
                     font=("Segoe UI", 9, "bold")).pack(side="right")

        verso_frame = tk.Frame(self.sheet_panel, bg=PANEL)
        verso_frame.pack(fill="x", padx=18, pady=(0, 8))

        tk.Label(
            verso_frame, text="Verso", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 12, "bold")
        ).pack(anchor="w")
        tk.Checkbutton(
            verso_frame, text="Página de verso (espelhar vertical)",
            variable=self.verso_var, command=self.refresh_preview,
            bg=PANEL, fg=TEXT, activebackground=PANEL,
            font=("Segoe UI", 9)
        ).pack(anchor="w")

        verso_offset_frame = tk.Frame(verso_frame, bg=PANEL)
        verso_offset_frame.pack(fill="x", pady=(4, 0))
        tk.Label(
            verso_offset_frame, text="Deslocamento Y (mm):",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9)
        ).pack(side="left")
        tk.Entry(
            verso_offset_frame, textvariable=self.verso_offset_y, width=6,
            bg="#F7F3EB", fg=TEXT, relief="solid", bd=1,
            font=("Segoe UI", 9)
        ).pack(side="left", padx=(4, 0), ipady=2)
        tk.Label(
            verso_offset_frame, text="±3 mm",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 8)
        ).pack(side="left", padx=(4, 0))

        verso_actions = tk.Frame(verso_frame, bg=PANEL)
        verso_actions.pack(fill="x", pady=(8, 0))
        self.action_button(
            verso_actions, "Imprimir verso", self.print_verso, secondary=True
        ).pack(side="left")

        tk.Label(
            self.sheet_panel,
            text="Para manter o tamanho correto na impressão,\nuse escala 100% e desative 'Ajustar à página'.",
            bg=PANEL, fg=MUTED, justify="center", font=("Segoe UI", 9)
        ).pack(pady=(4, 10))

    def on_body_resize(self, event):
        self.body_canvas.itemconfig(self.body_window, width=event.width)
        self.root.after_idle(lambda: self.apply_responsive_layout(event.width))

    def apply_responsive_layout(self, width):
        if not hasattr(self, "sheet_panel"):
            return

        stacked = width < 980
        desired_mode = "stacked" if stacked else "side"
        if getattr(self, "body_layout_mode", None) != desired_mode:
            self.photos_panel.pack_forget()
            self.sheet_panel.pack_forget()
            if stacked:
                self.photos_panel.pack(fill="x")
                self.sheet_panel.configure(width=1)
                self.sheet_panel.pack(fill="x", pady=(16, 0))
                self.sheet_panel.pack_propagate(True)
            else:
                self.photos_panel.pack(side="left", fill="both", expand=True)
                self.sheet_panel.configure(width=390)
                self.sheet_panel.pack(side="right", fill="y", padx=(16, 0))
                self.sheet_panel.pack_propagate(False)
            self.body_layout_mode = desired_mode

        available_for_grid = width if stacked else max(300, width - 406)
        columns = 3 if available_for_grid >= 780 else 2 if available_for_grid >= 520 else 1
        if columns != self.current_grid_columns:
            self.configure_photo_grid(columns)

    def configure_photo_grid(self, columns):
        for column in range(3):
            self.photos_grid.grid_columnconfigure(column, weight=0, uniform="")
        for row in range(9):
            self.photos_grid.grid_rowconfigure(row, weight=0)
        for index, slot in enumerate(self.slots):
            row, column = divmod(index, columns)
            slot.frame.grid(
                row=row, column=column, padx=6, pady=6,
                sticky="nsew"
            )
            self.photos_grid.grid_columnconfigure(column, weight=1, uniform="photos")
            self.photos_grid.grid_rowconfigure(row, weight=0)
        self.current_grid_columns = columns

    def build_footer(self):
        footer = tk.Frame(self.montage_tab, bg=PANEL, height=34, highlightbackground=BORDER, highlightthickness=1)
        footer.pack(fill="x")
        footer.pack_propagate(False)
        tk.Label(footer, textvariable=self.status_var, bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left", padx=18)

    def bind_body_mousewheel(self, event=None):
        self.root.bind_all("<MouseWheel>", self.scroll_body_with_mouse)

    def unbind_body_mousewheel(self, event=None):
        self.root.unbind_all("<MouseWheel>")

    def scroll_body_with_mouse(self, event):
        if event.delta:
            self.body_canvas.yview_scroll(
                -int(event.delta / 120) * WHEEL_SCROLL_LINES, "units"
            )

    def build_individual_tab(self):
        header = tk.Frame(
            self.individual_tab, bg=PANEL, height=76,
            highlightbackground=BORDER, highlightthickness=1
        )
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text="Redimensionar foto individual",
            bg=PANEL, fg=TEXT, font=("Segoe UI", 20, "bold")
        ).pack(anchor="w", padx=24, pady=(12, 0))
        tk.Label(
            header, text="Defina o tamanho em centímetros e exporte em 300 DPI",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 10)
        ).pack(anchor="w", padx=24)

        scroll_container = tk.Frame(self.individual_tab, bg=BG)
        scroll_container.pack(fill="both", expand=True)

        self.individual_canvas = tk.Canvas(
            scroll_container, bg=BG, highlightthickness=0
        )
        self.individual_canvas.pack(side="left", fill="both", expand=True)
        individual_scrollbar = tk.Scrollbar(
            scroll_container, orient="vertical",
            command=self.individual_canvas.yview
        )
        individual_scrollbar.pack(side="right", fill="y")
        self.individual_canvas.configure(
            yscrollcommand=individual_scrollbar.set
        )

        content = tk.Frame(self.individual_canvas, bg=BG)
        self.individual_content = content
        self.individual_min_content_height = 720
        self.individual_window = self.individual_canvas.create_window(
            (0, 0), window=content, anchor="nw"
        )
        content.configure(padx=24, pady=24)
        content.bind("<Configure>", self.update_individual_scrollregion)
        self.individual_canvas.bind("<Configure>", self.resize_individual_content)
        scroll_container.bind("<Enter>", self.bind_individual_mousewheel)
        scroll_container.bind("<Leave>", self.unbind_individual_mousewheel)

        controls = tk.Frame(
            content, bg=PANEL,
            highlightbackground=BORDER, highlightthickness=1
        )
        controls.pack(side="left", fill="y")

        tk.Label(
            controls, text="Configurações", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=20, pady=(20, 14))

        self.action_button(
            controls, "Escolher foto", self.choose_individual, secondary=True
        ).pack(fill="x", padx=20, pady=(0, 14))

        dimensions = tk.Frame(controls, bg=PANEL)
        dimensions.pack(fill="x", padx=20)
        self._dimension_field(dimensions, "Largura (cm)", self.individual_width).pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        self._dimension_field(dimensions, "Altura (cm)", self.individual_height).pack(
            side="left", fill="x", expand=True, padx=(5, 0)
        )

        tk.Label(
            controls, text="A imagem é ajustada sem deformação.\n"
                           "Partes excedentes podem ser recortadas.",
            bg=PANEL, fg=MUTED, justify="left", font=("Segoe UI", 9)
        ).pack(anchor="w", padx=20, pady=14)

        rotation = tk.Frame(controls, bg=PANEL)
        rotation.pack(fill="x", padx=20, pady=(0, 10))
        self.action_button(
            rotation, "Girar ↺", lambda: self.rotate_individual(-90), secondary=True
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.action_button(
            rotation, "Girar ↻", lambda: self.rotate_individual(90), secondary=True
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.action_button(
            controls, "Atualizar prévia", self.refresh_individual_preview, secondary=True
        ).pack(fill="x", padx=20, pady=6)
        self.action_button(
            controls, "Exportar foto", self.export_individual
        ).pack(fill="x", padx=20, pady=6)

        preview_panel = tk.Frame(
            content, bg=PANEL, highlightbackground=BORDER, highlightthickness=1
        )
        preview_panel.pack(side="right", fill="both", expand=True, padx=(20, 0))
        tk.Label(
            preview_panel, text="Prévia", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=20, pady=(20, 4))
        self.individual_info = tk.Label(
            preview_panel, text="Nenhuma foto selecionada",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9)
        )
        self.individual_info.pack(anchor="w", padx=20)

        ai_panel = tk.Frame(preview_panel, bg="#F7F3EB")
        ai_panel.pack(fill="x", padx=20, pady=(12, 0))
        tk.Label(
            ai_panel, text="Melhoria com IA", bg="#F7F3EB", fg=TEXT,
            font=("Segoe UI", 11, "bold")
        ).pack(anchor="w", padx=12, pady=(10, 6))

        provider_row = tk.Frame(ai_panel, bg="#F7F3EB")
        provider_row.pack(fill="x", padx=12)
        ttk.Combobox(
            provider_row, textvariable=self.ai_provider,
            values=("OpenAI", "Gemini"), state="readonly", width=12
        ).pack(side="left", padx=(0, 8))
        tk.Label(
            provider_row, text="Chave configurada na aba “Chaves de API”",
            bg="#F7F3EB", fg=MUTED, font=("Segoe UI", 8)
        ).pack(side="left", fill="x", expand=True)

        self.ai_prompt = tk.Text(
            ai_panel, height=3, wrap="word", bg=PANEL, fg=TEXT,
            relief="solid", bd=1, font=("Segoe UI", 9)
        )
        self.ai_prompt.pack(fill="x", padx=12, pady=8)
        self.ai_prompt.insert(
            "1.0",
            "Melhore a qualidade desta imagem para impressão em foto 9 × 6 cm. "
            "Corrija iluminação, cores, contraste e nitidez. Reduza ruídos e "
            "imperfeições, preservando o rosto, a identidade das pessoas e a "
            "composição original. Não adicione nem remova elementos."
        )

        ai_actions = tk.Frame(ai_panel, bg="#F7F3EB")
        ai_actions.pack(fill="x", padx=12, pady=(0, 6))
        self.ai_button = self.action_button(
            ai_actions, "Melhorar com IA", self.start_ai_enhancement
        )
        self.ai_button.pack(side="left")
        tk.Button(
            ai_actions, text="Desfazer IA", command=self.undo_ai_enhancement,
            bg="#EFEAE0", fg=TEXT, relief="flat", padx=10, pady=8,
            font=("Segoe UI", 9), cursor="hand2"
        ).pack(side="left", padx=8)
        tk.Label(
            ai_panel, textvariable=self.ai_status, bg="#F7F3EB", fg=MUTED,
            justify="left", anchor="w", font=("Segoe UI", 8), wraplength=0
        ).pack(fill="x", padx=12, pady=(0, 10))

        self.individual_preview = tk.Label(
            preview_panel, text="Escolha uma foto para começar",
            bg="#E5E0D6", fg=MUTED, font=("Segoe UI", 11)
        )
        self.individual_preview.pack(fill="both", expand=True, padx=20, pady=20)

    def update_individual_scrollregion(self, event=None):
        self.individual_canvas.configure(
            scrollregion=self.individual_canvas.bbox("all")
        )

    def resize_individual_content(self, event):
        self.individual_canvas.itemconfigure(
            self.individual_window,
            width=event.width,
            height=max(
                event.height,
                self.individual_min_content_height,
                self.individual_content.winfo_reqheight()
            )
        )

    def bind_individual_mousewheel(self, event=None):
        self.root.bind_all("<MouseWheel>", self.scroll_individual_with_mouse)

    def unbind_individual_mousewheel(self, event=None):
        self.root.unbind_all("<MouseWheel>")

    def scroll_individual_with_mouse(self, event):
        if event.delta:
            self.individual_canvas.yview_scroll(
                -int(event.delta / 120) * WHEEL_SCROLL_LINES, "units"
            )

    def build_lapel_tab(self):
        header = tk.Frame(
            self.lapel_tab, bg=PANEL, height=76,
            highlightbackground=BORDER, highlightthickness=1
        )
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text="Criar lapela", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 20, "bold")
        ).pack(anchor="w", padx=24, pady=(12, 0))
        tk.Label(
            header, text="Lapela retangular com a sua foto • 300 DPI",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 10)
        ).pack(anchor="w", padx=24)

        scroll_container = tk.Frame(self.lapel_tab, bg=BG)
        scroll_container.pack(fill="both", expand=True)

        self.lapel_canvas = tk.Canvas(
            scroll_container, bg=BG, highlightthickness=0
        )
        self.lapel_canvas.pack(side="left", fill="both", expand=True)
        lapel_scrollbar = tk.Scrollbar(
            scroll_container, orient="vertical", command=self.lapel_canvas.yview
        )
        lapel_scrollbar.pack(side="right", fill="y")
        self.lapel_canvas.configure(yscrollcommand=lapel_scrollbar.set)

        content = tk.Frame(self.lapel_canvas, bg=BG)
        self.lapel_content = content
        self.lapel_min_content_height = 640
        self.lapel_window = self.lapel_canvas.create_window(
            (0, 0), window=content, anchor="nw"
        )
        content.configure(padx=24, pady=24)
        content.bind("<Configure>", self.update_lapel_scrollregion)
        self.lapel_canvas.bind("<Configure>", self.resize_lapel_content)
        scroll_container.bind("<Enter>", self.bind_lapel_mousewheel)
        scroll_container.bind("<Leave>", self.unbind_lapel_mousewheel)

        controls = tk.Frame(
            content, bg=PANEL,
            highlightbackground=BORDER, highlightthickness=1
        )
        controls.pack(side="left", fill="y")

        tk.Label(
            controls, text="Configurações", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=20, pady=(20, 14))

        self.action_button(
            controls, "Escolher foto", self.choose_lapel, secondary=True
        ).pack(fill="x", padx=20, pady=(0, 14))

        dimensions = tk.Frame(controls, bg=PANEL)
        dimensions.pack(fill="x", padx=20)
        self._dimension_field(dimensions, "Altura (cm)", self.lapel_height).pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        self._dimension_field(dimensions, "Largura (cm)", self.lapel_width).pack(
            side="left", fill="x", expand=True, padx=(5, 0)
        )

        self._dimension_field(controls, "Borda superior (cm)", self.lapel_border_top).pack(
            fill="x", padx=20, pady=(8, 0)
        )
        self._dimension_field(controls, "Borda inferior (cm)", self.lapel_border_bottom).pack(
            fill="x", padx=20, pady=(8, 0)
        )
        self._dimension_field(controls, "Espaço entre fotos (cm)", self.lapel_gap).pack(
            fill="x", padx=20, pady=(8, 0)
        )

        tk.Label(
            controls, text="Lapela retangular com as dimensões\ninformadas acima.",
            bg=PANEL, fg=MUTED, justify="left", font=("Segoe UI", 9)
        ).pack(anchor="w", padx=20, pady=14)

        self.lapel_count_var = tk.StringVar(value="")
        tk.Label(
            controls, textvariable=self.lapel_count_var, bg=PANEL, fg=MUTED,
            justify="left", font=("Segoe UI", 9), wraplength=0
        ).pack(anchor="w", padx=20, pady=(0, 10))

        rotation = tk.Frame(controls, bg=PANEL)
        rotation.pack(fill="x", padx=20, pady=(0, 10))
        self.action_button(
            rotation, "Girar ↺", lambda: self.rotate_lapel(-90), secondary=True
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.action_button(
            rotation, "Girar ↻", lambda: self.rotate_lapel(90), secondary=True
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.action_button(
            controls, "Atualizar prévia", self.refresh_lapel_preview, secondary=True
        ).pack(fill="x", padx=20, pady=6)
        self.action_button(
            controls, "Exportar lapela", self.export_lapel
        ).pack(fill="x", padx=20, pady=6)
        self.action_button(
            controls, "Exportar para A4", self.export_lapel_a4
        ).pack(fill="x", padx=20, pady=6)
        self.action_button(
            controls, "Imprimir no Windows", self.print_lapel_a4
        ).pack(fill="x", padx=20, pady=6)

        preview_panel = tk.Frame(
            content, bg=PANEL, highlightbackground=BORDER, highlightthickness=1
        )
        preview_panel.pack(side="right", fill="both", expand=True, padx=(20, 0))
        tk.Label(
            preview_panel, text="Prévia", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=20, pady=(20, 4))
        self.lapel_info = tk.Label(
            preview_panel, text="Nenhuma foto selecionada",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9)
        )
        self.lapel_info.pack(anchor="w", padx=20)

        self.lapel_preview = tk.Label(
            preview_panel, text="Escolha uma foto para começar",
            bg="#E5E0D6", fg=MUTED, font=("Segoe UI", 11)
        )
        self.lapel_preview.pack(fill="both", expand=True, padx=20, pady=20)

        tk.Label(
            preview_panel, text="Prévia na folha A4", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=20, pady=(0, 4))
        self.lapel_a4_canvas = tk.Canvas(
            preview_panel, bg="#E5E0D6", highlightthickness=0
        )
        self.lapel_a4_canvas.pack(fill="x", padx=20, pady=(0, 20))
        self.update_lapel_sheet_count()

    def update_lapel_scrollregion(self, event=None):
        self.lapel_canvas.configure(scrollregion=self.lapel_canvas.bbox("all"))

    def resize_lapel_content(self, event):
        self.lapel_canvas.itemconfigure(
            self.lapel_window,
            width=event.width,
            height=max(
                event.height,
                self.lapel_min_content_height,
                self.lapel_content.winfo_reqheight()
            )
        )

    def bind_lapel_mousewheel(self, event=None):
        self.root.bind_all("<MouseWheel>", self.scroll_lapel_with_mouse)

    def unbind_lapel_mousewheel(self, event=None):
        self.root.unbind_all("<MouseWheel>")

    def scroll_lapel_with_mouse(self, event):
        if event.delta:
            self.lapel_canvas.yview_scroll(
                -int(event.delta / 120) * WHEEL_SCROLL_LINES, "units"
            )

    def choose_lapel(self):
        try:
            path = filedialog.askopenfilename(
                title="Escolher foto para a lapela",
                filetypes=[("Imagens", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff")]
            )
        except KeyboardInterrupt:
            return
        if path:
            self.lapel_path = path
            self.lapel_rotation = 0
            self.refresh_lapel_preview()

    def rotate_lapel(self, angle):
        if not self.lapel_path:
            return
        self.lapel_rotation = (self.lapel_rotation + angle) % 360
        self.refresh_lapel_preview()

    def get_lapel_dimensions(self):
        try:
            width_cm = float(self.lapel_width.get().replace(",", "."))
            height_cm = float(self.lapel_height.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Tamanho inválido", "Informe largura e altura usando números.")
            return None
        if not (0.5 <= width_cm <= 100 and 0.5 <= height_cm <= 100):
            messagebox.showerror(
                "Tamanho inválido",
                "A largura e a altura devem estar entre 0,5 e 100 cm."
            )
            return None
        width_px = int(round(width_cm / 2.54 * DPI))
        height_px = int(round(height_cm / 2.54 * DPI))
        return width_cm, height_cm, width_px, height_px

    def get_lapel_source_image(self):
        with Image.open(self.lapel_path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
        if self.lapel_rotation:
            image = image.rotate(-self.lapel_rotation, expand=True)
        return image

    def build_lapel_image(self):
        if not self.lapel_path:
            messagebox.showwarning("Sem foto", "Escolha uma foto primeiro.")
            return None
        dimensions = self.get_lapel_dimensions()
        if dimensions is None:
            return None
        width_cm, height_cm, width_px, height_px = dimensions
        try:
            image = self.get_lapel_source_image()
            result = cover_fit(image, width_px, height_px)
            return result, width_cm, height_cm
        except Exception as exc:
            messagebox.showerror("Erro", f"Não foi possível processar a imagem.\n\n{exc}")
            return None

    def refresh_lapel_preview(self):
        built = self.build_lapel_image()
        if built is None:
            return
        image, width_cm, height_cm = built
        preview = image.copy()
        pw = max(100, self.lapel_preview.winfo_width() - 10)
        ph = max(100, self.lapel_preview.winfo_height() - 10)
        preview.thumbnail((pw, ph), Image.Resampling.LANCZOS)
        self.lapel_preview_img = ImageTk.PhotoImage(preview)
        self.lapel_preview.configure(image=self.lapel_preview_img, text="")
        self.lapel_info.configure(
            text=f"{Path(self.lapel_path).name}  •  "
                 f"{width_cm:g} × {height_cm:g} cm  •  {image.width} × {image.height} px"
        )
        self.refresh_lapel_a4_preview()

    def export_lapel(self):
        built = self.build_lapel_image()
        if built is None:
            return
        image, width_cm, height_cm = built
        try:
            path = filedialog.asksaveasfilename(
                title="Salvar lapela",
                defaultextension=".png",
                initialfile=f"lapela_{width_cm:g}x{height_cm:g}cm_{save_timestamp()}",
                filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg")]
            )
        except KeyboardInterrupt:
            return
        if not path:
            return
        try:
            ext = Path(path).suffix.lower()
            if ext in (".jpg", ".jpeg"):
                image.save(path, "JPEG", quality=95, dpi=(DPI, DPI), subsampling=0)
            else:
                image.save(path, "PNG", dpi=(DPI, DPI))
            messagebox.showinfo(
                "Arquivo criado",
                f"Lapela exportada com {width_cm:g} × {height_cm:g} cm em 300 DPI."
            )
        except Exception as exc:
            messagebox.showerror("Erro", str(exc))

    def get_lapel_sheet_grid(self):
        dims = self.get_lapel_dimensions()
        if dims is None:
            return None
        _, _, width_px, height_px = dims
        margin = int(round(1.5 / 2.54 * DPI))
        try:
            gap_cm = float(self.lapel_gap.get().replace(",", "."))
        except (ValueError, AttributeError):
            gap_cm = 0.5
        gap = int(round(gap_cm / 2.54 * DPI))
        try:
            top_border_cm = float(self.lapel_border_top.get().replace(",", "."))
        except (ValueError, AttributeError):
            top_border_cm = 1.0
        try:
            bottom_border_cm = float(self.lapel_border_bottom.get().replace(",", "."))
        except (ValueError, AttributeError):
            bottom_border_cm = 1.0
        top_border = int(round(top_border_cm / 2.54 * DPI))
        bottom_border = int(round(bottom_border_cm / 2.54 * DPI))
        usable_w = A4_W - 2 * margin
        usable_h = A4_H - 2 * margin - top_border - bottom_border
        if width_px > usable_w or height_px > usable_h:
            return None
        cols = (usable_w + gap) // (width_px + gap)
        rows = (usable_h + gap) // (height_px + gap)
        if cols < 1 or rows < 1:
            return None
        total_width = cols * width_px + (cols - 1) * gap
        total_height = rows * height_px + (rows - 1) * gap
        start_x = margin + (usable_w - total_width) // 2
        start_y = margin + top_border
        return margin, gap, width_px, height_px, cols, rows, start_x, start_y

    def update_lapel_sheet_count(self, *args):
        try:
            width_cm = float(self.lapel_width.get().replace(",", "."))
            height_cm = float(self.lapel_height.get().replace(",", "."))
        except ValueError:
            self.lapel_count_var.set("")
            return
        if not (0.5 <= width_cm <= 100 and 0.5 <= height_cm <= 100):
            self.lapel_count_var.set("")
            return
        width_px = int(round(width_cm / 2.54 * DPI))
        height_px = int(round(height_cm / 2.54 * DPI))
        margin = int(round(1.5 / 2.54 * DPI))
        try:
            gap_cm = float(self.lapel_gap.get().replace(",", "."))
        except (ValueError, AttributeError):
            gap_cm = 0.5
        gap = int(round(gap_cm / 2.54 * DPI))
        usable_w = A4_W - 2 * margin
        usable_h = A4_H - 2 * margin
        if width_px > usable_w or height_px > usable_h:
            self.lapel_count_var.set("A lapela é maior que a folha A4.")
            return
        cols = (usable_w + gap) // (width_px + gap)
        rows = (usable_h + gap) // (height_px + gap)
        self.lapel_count_var.set(
            f"Na folha A4 cabem {cols} × {rows} = {cols * rows} lapelas."
        )
        if hasattr(self, "lapel_a4_canvas"):
            if hasattr(self, "_a4_preview_job"):
                self.root.after_cancel(self._a4_preview_job)
            self._a4_preview_job = self.root.after(
                400, self.refresh_lapel_a4_preview
            )

    def _compose_lapel_a4(self, grid):
        lapel = self.get_lapel_source_image()
        lapel = cover_fit(lapel, grid[2], grid[3])
        margin, gap, width_px, height_px, cols, rows, start_x, start_y = grid
        sheet = Image.new("RGB", (A4_W, A4_H), "white")
        for r in range(rows):
            for c in range(cols):
                x = start_x + c * (width_px + gap)
                y = start_y + r * (height_px + gap)
                sheet.paste(lapel, (x, y))
        return sheet

    def refresh_lapel_a4_preview(self):
        canvas = self.lapel_a4_canvas
        canvas.delete("all")
        width = max(100, canvas.winfo_width())
        height = max(100, canvas.winfo_height())
        if not self.lapel_path:
            canvas.create_text(
                width // 2, height // 2,
                text="Escolha uma foto para ver a folha A4",
                fill=MUTED, font=("Segoe UI", 9)
            )
            return
        grid = self.get_lapel_sheet_grid()
        if grid is None:
            canvas.create_text(
                width // 2, height // 2,
                text="A lapela não cabe na folha A4",
                fill="#B00020", font=("Segoe UI", 9)
            )
            return
        try:
            sheet = self._compose_lapel_a4(grid)
        except Exception:
            return
        thumb = sheet.copy()
        thumb.thumbnail((width - 16, height - 16), Image.Resampling.LANCZOS)
        self.lapel_a4_tk = ImageTk.PhotoImage(thumb)
        canvas.create_image(width // 2, height // 2, image=self.lapel_a4_tk)

    def build_lapel_a4_sheet(self):
        grid = self.get_lapel_sheet_grid()
        if grid is None:
            messagebox.showwarning(
                "Lapela muito grande",
                "A lapela não cabe na folha A4. Reduza as dimensões."
            )
            return None
        built = self.build_lapel_image()
        if built is None:
            return None
        lapel, width_cm, height_cm = built
        grid = list(grid)
        grid[2] = lapel.width
        grid[3] = lapel.height
        sheet = self._compose_lapel_a4(tuple(grid))
        return sheet, grid[4], grid[5], width_cm, height_cm

    def export_lapel_a4(self):
        built_sheet = self.build_lapel_a4_sheet()
        if built_sheet is None:
            return
        sheet, cols, rows, width_cm, height_cm = built_sheet
        try:
            path = filedialog.asksaveasfilename(
                title="Salvar folha A4 de lapelas",
                defaultextension=".jpg",
                initialfile=f"folha_A4_lapelas_{width_cm:g}x{height_cm:g}_{save_timestamp()}",
                filetypes=[("JPEG", "*.jpg"), ("PNG", "*.png")]
            )
        except KeyboardInterrupt:
            return
        if not path:
            return
        try:
            ext = Path(path).suffix.lower()
            if ext in (".jpg", ".jpeg"):
                sheet.save(path, "JPEG", quality=95, dpi=(DPI, DPI), subsampling=0)
            else:
                sheet.save(path, "PNG", dpi=(DPI, DPI))
            messagebox.showinfo(
                "Arquivo criado",
                f"Folha A4 exportada com {cols * rows} lapelas "
                f"de {width_cm:g} × {height_cm:g} cm em 300 DPI."
            )
        except Exception as exc:
            messagebox.showerror("Erro", str(exc))

    def _show_windows_print_dialog(self):
        try:
            comdlg = ctypes.WinDLL("C:/Windows/System32/comdlg32.dll")
        except OSError:
            return None, None, None
        comdlg.PrintDlgW.argtypes = [ctypes.POINTER(PRINTDLGW)]
        comdlg.PrintDlgW.restype = wintypes.BOOL
        pd = PRINTDLGW()
        pd.lStructSize = ctypes.sizeof(PRINTDLGW)
        pd.hwndOwner = self.root.winfo_id()
        pd.Flags = 0x00000100
        pd.nMinPage = 1
        pd.nMaxPage = 1
        pd.nFromPage = 1
        pd.nToPage = 1
        pd.nCopies = 1
        if not comdlg.PrintDlgW(ctypes.byref(pd)):
            return None, None, None
        return pd.hDC, pd.hDevMode, pd.hDevNames

    def print_image(self, sheet, doc_name):
        hdc, hdev_mode, hdev_names = self._show_windows_print_dialog()
        if not hdc:
            return False
        try:
            gdi32 = ctypes.windll.gdi32
            gdi32.CreateDCW.argtypes = [
                wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                ctypes.c_void_p
            ]
            gdi32.CreateDCW.restype = wintypes.HDC
            gdi32.StartDocW.argtypes = [
                wintypes.HDC, ctypes.POINTER(DOCINFO)
            ]
            gdi32.StartDocW.restype = ctypes.c_int
            gdi32.StartPage.argtypes = [wintypes.HDC]
            gdi32.StartPage.restype = ctypes.c_int
            gdi32.EndPage.argtypes = [wintypes.HDC]
            gdi32.EndPage.restype = ctypes.c_int
            gdi32.EndDoc.argtypes = [wintypes.HDC]
            gdi32.EndDoc.restype = ctypes.c_int
            gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
            gdi32.GetDeviceCaps.restype = ctypes.c_int
            gdi32.DeleteDC.argtypes = [wintypes.HDC]
            gdi32.DeleteDC.restype = ctypes.c_int
            docinfo = DOCINFO()
            docinfo.cbSize = ctypes.sizeof(DOCINFO)
            docinfo.lpszDocName = doc_name
            docinfo.fwType = 0
            if gdi32.StartDocW(hdc, ctypes.byref(docinfo)) <= 0:
                raise ctypes.WinError()
            gdi32.StartPage(hdc)
            horzres = int(gdi32.GetDeviceCaps(hdc, 8))
            vertres = int(gdi32.GetDeviceCaps(hdc, 10))
            offx = int(gdi32.GetDeviceCaps(hdc, 112))
            offy = int(gdi32.GetDeviceCaps(hdc, 113))
            scale = min(horzres / sheet.width, vertres / sheet.height)
            w = int(sheet.width * scale)
            h = int(sheet.height * scale)
            x = offx + (horzres - w) // 2
            y = offy + (vertres - h) // 2
            ImageWin.Dib(sheet).draw(hdc, (x, y, x + w, y + h))
            gdi32.EndPage(hdc)
            gdi32.EndDoc(hdc)
            messagebox.showinfo(
                "Impressão",
                "Folha enviada para a impressora selecionada."
            )
            return True
        except Exception as exc:
            messagebox.showerror("Erro de impressão", str(exc))
            return False
        finally:
            if hdc:
                ctypes.windll.gdi32.DeleteDC(hdc)
            kernel32 = ctypes.windll.kernel32
            kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
            kernel32.GlobalFree.restype = ctypes.c_void_p
            if hdev_mode:
                kernel32.GlobalFree(hdev_mode)
            if hdev_names:
                kernel32.GlobalFree(hdev_names)

    def print_lapel_a4(self):
        built_sheet = self.build_lapel_a4_sheet()
        if built_sheet is None:
            return
        sheet, cols, rows, width_cm, height_cm = built_sheet
        self.print_image(
            sheet,
            f"Folha A4 - Lapelas {width_cm:g}x{height_cm:g} cm"
        )

    def print_montage(self):
        if not any(s.path for s in self.slots):
            messagebox.showwarning(
                "Sem fotos", "Adicione pelo menos uma foto antes de imprimir."
            )
            return
        self.print_image(self.build_sheet(), "Folha A4 - 9 Fotos 6x9 cm")

    def build_polaroid_tab(self):
        header = tk.Frame(
            self.polaroid_tab, bg=PANEL, height=76,
            highlightbackground=BORDER, highlightthickness=1
        )
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text="Criar Polaroid", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 20, "bold")
        ).pack(anchor="w", padx=24, pady=(12, 0))
        tk.Label(
            header, text="Foto emoldurada em estilo Polaroid • 300 DPI",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 10)
        ).pack(anchor="w", padx=24)

        main_body = tk.Frame(self.polaroid_tab, bg=BG)
        main_body.pack(fill="both", expand=True)

        scroll_container = tk.Frame(main_body, bg=BG)
        scroll_container.pack(side="left", fill="both", expand=True)

        self.polaroid_canvas = tk.Canvas(
            scroll_container, bg=BG, highlightthickness=0
        )
        self.polaroid_canvas.pack(side="left", fill="both", expand=True)
        polaroid_scrollbar = tk.Scrollbar(
            scroll_container, orient="vertical",
            command=self.polaroid_canvas.yview
        )
        polaroid_scrollbar.pack(side="right", fill="y")
        self.polaroid_canvas.configure(yscrollcommand=polaroid_scrollbar.set)

        content = tk.Frame(self.polaroid_canvas, bg=BG)
        self.polaroid_content = content
        self.polaroid_min_content_height = 640
        self.polaroid_window = self.polaroid_canvas.create_window(
            (0, 0), window=content, anchor="nw"
        )
        content.configure(padx=24, pady=24)
        content.bind("<Configure>", self.update_polaroid_scrollregion)
        self.polaroid_canvas.bind("<Configure>", self.resize_polaroid_content)
        scroll_container.bind("<Enter>", self.bind_polaroid_mousewheel)
        scroll_container.bind("<Leave>", self.unbind_polaroid_mousewheel)

        controls = tk.Frame(
            content, bg=PANEL,
            highlightbackground=BORDER, highlightthickness=1
        )
        controls.pack(fill="x")

        tk.Label(
            controls, text="Configurações", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=20, pady=(16, 10))

        profiles_frame = tk.Frame(controls, bg=PANEL)
        profiles_frame.pack(fill="x", padx=20, pady=(0, 10))
        tk.Label(
            profiles_frame, text="Perfil", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack(side="left", padx=(0, 8))

        self.polaroid_profiles = {
            "Perfil 1": {"tw": "5.4", "th": "8.6", "iw": "4.6", "ih": "6.2", "bt": "0.5", "bl": "0.4"},
            "Perfil 2": {"tw": "7.0", "th": "10.0", "iw": "6.0", "ih": "7.5", "bt": "0.8", "bl": "0.5"},
            "Perfil 3": {"tw": "8.0", "th": "12.0", "iw": "7.0", "ih": "9.0", "bt": "1.0", "bl": "0.5"},
        }
        self.polaroid_profile_var = tk.StringVar(value="Perfil 1")
        for name in self.polaroid_profiles:
            tk.Radiobutton(
                profiles_frame, text=name, variable=self.polaroid_profile_var, value=name,
                command=self.apply_polaroid_profile, bg=PANEL, fg=TEXT,
                activebackground=PANEL, font=("Segoe UI", 9)
            ).pack(side="left", padx=(0, 6))

        tk.Checkbutton(
            controls, text="Borda preta na polaroid", variable=self.polaroid_border_var,
            command=self.refresh_polaroid_preview, bg=PANEL, fg=TEXT,
            activebackground=PANEL, font=("Segoe UI", 9)
        ).pack(anchor="w", padx=20, pady=(0, 10))

        config_notebook = ttk.Notebook(controls)
        config_notebook.pack(fill="x", padx=20, pady=(0, 8))

        tab_dims = ttk.Frame(config_notebook)
        tab_borders = ttk.Frame(config_notebook)
        tab_options = ttk.Frame(config_notebook)
        config_notebook.add(tab_dims, text="Dimensões")
        config_notebook.add(tab_borders, text="Bordas")
        config_notebook.add(tab_options, text="Opções")

        tk.Label(
            tab_dims, text="Frame (cm)", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=10, pady=(8, 2))
        total_dims = tk.Frame(tab_dims, bg=PANEL)
        total_dims.pack(fill="x", padx=10, pady=(0, 6))
        self._dimension_field(total_dims, "Largura", self.polaroid_total_w).pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        self._dimension_field(total_dims, "Altura", self.polaroid_total_h).pack(
            side="left", fill="x", expand=True, padx=(5, 0)
        )

        tk.Label(
            tab_dims, text="Foto (cm)", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=10, pady=(4, 2))
        img_dims = tk.Frame(tab_dims, bg=PANEL)
        img_dims.pack(fill="x", padx=10, pady=(0, 8))
        self._dimension_field(img_dims, "Largura", self.polaroid_img_w).pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        self._dimension_field(img_dims, "Altura", self.polaroid_img_h).pack(
            side="left", fill="x", expand=True, padx=(5, 0)
        )

        tk.Label(
            tab_borders, text="Bordas (cm)", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=10, pady=(8, 2))
        border_dims = tk.Frame(tab_borders, bg=PANEL)
        border_dims.pack(fill="x", padx=10, pady=(0, 6))
        self._dimension_field(border_dims, "Superior", self.polaroid_border_top).pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        self._dimension_field(border_dims, "Esquerda", self.polaroid_border_left).pack(
            side="left", fill="x", expand=True, padx=(5, 0)
        )

        tk.Checkbutton(
            tab_options, text="Duplicar fotos para preencher A4", variable=self.polaroid_duplicate_var,
            bg=PANEL, fg=TEXT,
            activebackground=PANEL, font=("Segoe UI", 9)
        ).pack(anchor="w", padx=10, pady=(0, 4))

        tk.Label(
            tab_options, text="Quantidade de slots", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=10, pady=(8, 2))
        num_frame = tk.Frame(tab_options, bg=PANEL)
        num_frame.pack(fill="x", padx=10, pady=(0, 8))
        self.polaroid_num_spinbox = tk.Spinbox(
            num_frame, from_=1, to=24, textvariable=self.polaroid_num_var,
            width=6, command=self.build_polaroid_grid,
            font=("Segoe UI", 10)
        )
        self.polaroid_num_spinbox.pack(side="left")

        tk.Label(
            controls, textvariable=self.polaroid_count_var, bg=PANEL, fg=MUTED,
            justify="left", font=("Segoe UI", 9), wraplength=0
        ).pack(anchor="w", padx=20, pady=(0, 8))

        self.polaroid_grid = tk.Frame(content, bg=BG)
        self.polaroid_grid.pack(fill="both", expand=True, pady=(10, 0))
        self.polaroid_slots = []
        self.build_polaroid_grid()

        preview_panel = tk.Frame(
            main_body, bg=PANEL, highlightbackground=BORDER, highlightthickness=1
        )
        preview_panel.pack(side="right", fill="y", padx=(20, 0))
        tk.Label(
            preview_panel, text="Prévia", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=20, pady=(20, 4))
        self.polaroid_info = tk.Label(
            preview_panel, text="Nenhuma foto selecionada",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9)
        )
        self.polaroid_info.pack(anchor="w", padx=20)

        self.polaroid_preview = tk.Label(
            preview_panel, text="Escolha uma foto para começar",
            bg="#E5E0D6", fg=MUTED, font=("Segoe UI", 11)
        )
        self.polaroid_preview.pack(fill="both", expand=True, padx=20, pady=12, ipady=80)

        nav_row = tk.Frame(preview_panel, bg=PANEL)
        nav_row.pack(fill="x", padx=20, pady=(0, 8))
        self.action_button(
            nav_row, "◀", self.polaroid_prev_preview, secondary=True
        ).pack(side="left", padx=(0, 4))
        self.polaroid_preview_label = tk.Label(
            nav_row, text="", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 9)
        )
        self.polaroid_preview_label.pack(side="left", fill="x", expand=True)
        self.action_button(
            nav_row, "▶", self.polaroid_next_preview, secondary=True
        ).pack(side="left", padx=(4, 0))

        tk.Label(
            preview_panel, text="Prévia na folha A4", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=20, pady=(0, 4))
        self.polaroid_a4_canvas = tk.Canvas(
            preview_panel, bg="#E5E0D6", highlightthickness=0
        )
        self.polaroid_a4_canvas.pack(fill="x", padx=20, pady=(0, 20))

        actions = tk.Frame(preview_panel, bg=PANEL)
        actions.pack(fill="x", padx=20, pady=(0, 16))
        self.action_button(
            actions, "Exportar polaroid", self.export_polaroid
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.action_button(
            actions, "Importar várias fotos", self.import_polaroid_multiple
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.action_button(
            actions, "Exportar para A4", self.export_polaroid_a4
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))
        actions2 = tk.Frame(preview_panel, bg=PANEL)
        actions2.pack(fill="x", padx=20, pady=(0, 16))
        self.action_button(
            actions2, "Imprimir no Windows", self.print_polaroid_a4
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.action_button(
            actions2, "Limpar tudo", self.clear_polaroid_all, secondary=True
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.update_polaroid_sheet_count()

    def build_polaroid_grid(self, *args):
        for slot in self.polaroid_slots:
            slot.frame.destroy()
        self.polaroid_slots.clear()
        try:
            n = self.polaroid_num_var.get()
        except (tk.TclError, AttributeError):
            n = 4
        n = max(1, min(24, n))
        for i in range(n):
            card = PolaroidSlotCard(self.polaroid_grid, i, self.refresh_polaroid_preview)
            card.frame.grid(row=i // 3, column=i % 3, padx=4, pady=4, sticky="nsew")
            self.polaroid_slots.append(card)
        for c in range(3):
            self.polaroid_grid.grid_columnconfigure(c, weight=1, uniform="polaroid")

    def update_polaroid_scrollregion(self, event=None):
        self.polaroid_canvas.configure(scrollregion=self.polaroid_canvas.bbox("all"))

    def resize_polaroid_content(self, event):
        self.polaroid_canvas.itemconfigure(
            self.polaroid_window,
            width=event.width,
            height=max(
                event.height,
                self.polaroid_min_content_height,
                self.polaroid_content.winfo_reqheight()
            )
        )

    def bind_polaroid_mousewheel(self, event=None):
        self.root.bind_all("<MouseWheel>", self.scroll_polaroid_with_mouse)

    def unbind_polaroid_mousewheel(self, event=None):
        self.root.unbind_all("<MouseWheel>")

    def scroll_polaroid_with_mouse(self, event):
        if event.delta:
            self.polaroid_canvas.yview_scroll(
                -int(event.delta / 120) * WHEEL_SCROLL_LINES, "units"
            )

    def _parse_polaroid_dims_silent(self):
        try:
            tw = float(self.polaroid_total_w.get().replace(",", "."))
            th = float(self.polaroid_total_h.get().replace(",", "."))
            iw = float(self.polaroid_img_w.get().replace(",", "."))
            ih = float(self.polaroid_img_h.get().replace(",", "."))
            return tw, th, iw, ih
        except ValueError:
            return None

    def get_polaroid_dimensions(self):
        try:
            tw = float(self.polaroid_total_w.get().replace(",", "."))
            th = float(self.polaroid_total_h.get().replace(",", "."))
            iw = float(self.polaroid_img_w.get().replace(",", "."))
            ih = float(self.polaroid_img_h.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Tamanho inválido", "Informe todas as dimensões usando números.")
            return None
        if not (0.5 <= tw <= 100 and 0.5 <= th <= 100 and 0.5 <= iw <= 100 and 0.5 <= ih <= 100):
            messagebox.showerror("Tamanho inválido", "As dimensões devem estar entre 0,5 e 100 cm.")
            return None
        if iw > tw or ih > th:
            messagebox.showerror(
                "Dimensão inválida",
                "A imagem deve ser menor ou igual ao frame nos dois eixos."
            )
            return None
        tw_px = int(round(tw / 2.54 * DPI))
        th_px = int(round(th / 2.54 * DPI))
        iw_px = int(round(iw / 2.54 * DPI))
        ih_px = int(round(ih / 2.54 * DPI))
        return tw, th, iw, ih, tw_px, th_px, iw_px, ih_px

    def _make_polaroid_frame(self, image, tw_px, th_px, offset_y=0):
        dims = self.get_polaroid_dimensions()
        if dims is None:
            return None
        _, _, iw, ih, _, _, iw_px, ih_px = dims
        img_ratio = image.width / image.height
        area_ratio = iw_px / ih_px
        if (img_ratio > 1 and area_ratio < 1) or (img_ratio < 1 and area_ratio > 1):
            image = image.rotate(90, expand=True)
        photo = cover_fit(image, iw_px, ih_px, offset_y=offset_y)
        frame = Image.new("RGB", (tw_px, th_px), "white")
        x = (tw_px - iw_px) // 2
        border_top_cm = float(self.polaroid_border_top.get().replace(",", "."))
        border_top_px = int(border_top_cm / 2.54 * DPI)
        border_left_cm = float(self.polaroid_border_left.get().replace(",", "."))
        border_left_px = int(border_left_cm / 2.54 * DPI)
        y = border_top_px
        x = border_left_px
        frame.paste(photo, (x, y))
        if self.polaroid_border_var.get():
            draw = ImageDraw.Draw(frame)
            draw.rectangle(
                (0, 0, tw_px - 1, th_px - 1),
                outline=(0, 0, 0), width=2
            )
        return frame

    def build_polaroid_image(self):
        if not self.polaroid_slots:
            return None
        first_with_photo = next((s for s in self.polaroid_slots if s.path), None)
        if not first_with_photo:
            messagebox.showwarning("Sem fotos", "Adicione pelo menos uma foto.")
            return None
        dims = self.get_polaroid_dimensions()
        if dims is None:
            return None
        tw, th, _, _, tw_px, th_px, _, _ = dims
        try:
            image = first_with_photo.get_image()
            frame = self._make_polaroid_frame(image, tw_px, th_px, offset_y=first_with_photo.offset_y)
            return frame, tw, th
        except Exception as exc:
            messagebox.showerror("Erro", f"Não foi possível processar a imagem.\n\n{exc}")
            return None

    def polaroid_prev_preview(self):
        slots_with_photo = [s for s in self.polaroid_slots if s.path]
        if not slots_with_photo:
            return
        self.polaroid_preview_idx = (self.polaroid_preview_idx - 1) % len(slots_with_photo)
        self._update_polaroid_single_preview(slots_with_photo)

    def polaroid_next_preview(self):
        slots_with_photo = [s for s in self.polaroid_slots if s.path]
        if not slots_with_photo:
            return
        self.polaroid_preview_idx = (self.polaroid_preview_idx + 1) % len(slots_with_photo)
        self._update_polaroid_single_preview(slots_with_photo)

    def _update_polaroid_single_preview(self, slots_with_photo):
        dims = self.get_polaroid_dimensions()
        if dims is None:
            return
        tw, th, _, _, tw_px, th_px, _, _ = dims
        idx = min(self.polaroid_preview_idx, len(slots_with_photo) - 1)
        slot = slots_with_photo[idx]
        try:
            image = slot.get_image()
            frame = self._make_polaroid_frame(image, tw_px, th_px, offset_y=slot.offset_y)
            preview = frame.copy()
            pw = max(200, self.polaroid_preview.winfo_width() - 20)
            ph = max(200, self.polaroid_preview.winfo_height() - 20)
            preview.thumbnail((pw, ph), Image.Resampling.LANCZOS)
            self.polaroid_preview_img = ImageTk.PhotoImage(preview)
            self.polaroid_preview.configure(image=self.polaroid_preview_img, text="")
            self.polaroid_preview_label.configure(
                text=f"Polaroid {idx+1} de {len(slots_with_photo)}"
            )
        except Exception:
            pass

    def refresh_polaroid_preview(self):
        dims = self.get_polaroid_dimensions()
        if dims is None:
            return
        tw, th, _, _, tw_px, th_px, _, _ = dims
        slots_with_photo = [s for s in self.polaroid_slots if s.path]
        if slots_with_photo:
            self.polaroid_preview_idx = min(self.polaroid_preview_idx, len(slots_with_photo) - 1)
            self._update_polaroid_single_preview(slots_with_photo)
            count = len(slots_with_photo)
            self.polaroid_info.configure(
                text=f"{count} polaroid(es) • {tw:g} × {th:g} cm"
            )
        else:
            self.polaroid_preview.configure(image="", text="Escolha uma foto para começar")
            self.polaroid_info.configure(text="Nenhuma foto selecionada")
            self.polaroid_preview_label.configure(text="")
        self.refresh_polaroid_a4_preview()

    def export_polaroid(self):
        built = self.build_polaroid_image()
        if built is None:
            return
        image, tw, th = built
        try:
            path = filedialog.asksaveasfilename(
                title="Salvar Polaroid",
                defaultextension=".jpg",
                initialfile=f"polaroid_{tw:g}x{th:g}cm_{save_timestamp()}",
                filetypes=[("JPEG", "*.jpg"), ("PNG", "*.png")]
            )
        except KeyboardInterrupt:
            return
        if not path:
            return
        try:
            ext = Path(path).suffix.lower()
            if ext in (".jpg", ".jpeg"):
                image.save(path, "JPEG", quality=95, dpi=(DPI, DPI), subsampling=0)
            else:
                image.save(path, "PNG", dpi=(DPI, DPI))
            messagebox.showinfo(
                "Arquivo criado",
                f"Polaroid exportada com {tw:g} × {th:g} cm em 300 DPI."
            )
        except Exception as exc:
            messagebox.showerror("Erro", str(exc))

    def apply_polaroid_profile(self):
        name = self.polaroid_profile_var.get()
        profile = self.polaroid_profiles.get(name)
        if not profile:
            return
        self.polaroid_total_w.set(profile["tw"])
        self.polaroid_total_h.set(profile["th"])
        self.polaroid_img_w.set(profile["iw"])
        self.polaroid_img_h.set(profile["ih"])
        self.polaroid_border_top.set(profile["bt"])
        self.polaroid_border_left.set(profile["bl"])
        self.refresh_polaroid_preview()

    def clear_polaroid_all(self):
        for slot in self.polaroid_slots:
            slot.clear()
        self.refresh_polaroid_preview()

    def import_polaroid_multiple(self):
        try:
            files = filedialog.askopenfilenames(
                title="Selecionar fotos",
                filetypes=[("Imagens", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff")]
            )
        except KeyboardInterrupt:
            return
        if not files:
            return
        empty_slots = [s for s in self.polaroid_slots if s.path is None]
        n = min(len(files), len(empty_slots))
        if n == 0:
            messagebox.showwarning("Sem slots", "Todos os slots já estão ocupados.")
            return
        for i in range(n):
            empty_slots[i].path = files[i]
            empty_slots[i].rotation = 0
            empty_slots[i].refresh()
        self.refresh_polaroid_preview()
        if len(files) > len(empty_slots):
            messagebox.showinfo(
                "Importação parcial",
                f"{n} foto(s) importada(s). {len(files) - len(empty_slots)} foto(s) restante(s) — aumente a quantidade de slots."
            )

    def get_polaroid_sheet_grid(self):
        dims = self.get_polaroid_dimensions()
        if dims is None:
            return None
        _, _, _, _, tw_px, th_px, _, _ = dims
        margin = int(round(1.0 / 2.54 * DPI))
        gap = int(round(0.2 / 2.54 * DPI))
        usable_w = A4_W - 2 * margin
        usable_h = A4_H - 2 * margin
        if tw_px > usable_w or th_px > usable_h:
            return None
        cols = (usable_w + gap) // (tw_px + gap)
        rows = (usable_h + gap) // (th_px + gap)
        if cols < 1 or rows < 1:
            return None
        total_width = cols * tw_px + (cols - 1) * gap
        total_height = rows * th_px + (rows - 1) * gap
        start_x = margin + (usable_w - total_width) // 2
        start_y = margin + (usable_h - total_height) // 2
        return margin, gap, tw_px, th_px, cols, rows, start_x, start_y

    def update_polaroid_sheet_count(self, *args):
        dims = self._parse_polaroid_dims_silent()
        if dims is None:
            self.polaroid_count_var.set("")
            return
        tw, th, iw, ih = dims
        if not (0.5 <= tw <= 100 and 0.5 <= th <= 100 and 0.5 <= iw <= 100 and 0.5 <= ih <= 100):
            self.polaroid_count_var.set("")
            return
        if iw > tw or ih > th:
            self.polaroid_count_var.set("Imagem maior que o frame.")
            return
        tw_px = int(round(tw / 2.54 * DPI))
        th_px = int(round(th / 2.54 * DPI))
        margin = int(round(1.5 / 2.54 * DPI))
        gap = int(round(0.5 / 2.54 * DPI))
        usable_w = A4_W - 2 * margin
        usable_h = A4_H - 2 * margin
        if tw_px > usable_w or th_px > usable_h:
            self.polaroid_count_var.set("O frame é maior que a folha A4.")
            return
        cols = (usable_w + gap) // (tw_px + gap)
        rows = (usable_h + gap) // (th_px + gap)
        total_slots = cols * rows
        photos = sum(1 for s in self.polaroid_slots if s.path) if hasattr(self, "polaroid_slots") else 0
        self.polaroid_count_var.set(
            f"Na folha A4 cabem {cols} × {rows} = {total_slots} polaroides."
            + (f"  ({photos} com foto)" if photos else "")
        )
        if hasattr(self, "polaroid_a4_canvas"):
            if hasattr(self, "_polaroid_a4_job"):
                self.root.after_cancel(self._polaroid_a4_job)
            self._polaroid_a4_job = self.root.after(
                400, self.refresh_polaroid_a4_preview
            )

    def _compose_polaroid_a4(self, grid):
        dims = self.get_polaroid_dimensions()
        _, _, _, _, tw_px, th_px, _, _ = dims
        margin, gap, w, h, cols, rows, start_x, start_y = grid
        sheet = Image.new("RGB", (A4_W, A4_H), "white")
        slots_with_photo = [s for s in self.polaroid_slots if s.path]
        duplicate = self.polaroid_duplicate_var.get()
        total_slots = cols * rows
        slot_idx = 0
        for r in range(rows):
            for c in range(cols):
                x = start_x + c * (w + gap)
                y = start_y + r * (h + gap)
                if slots_with_photo:
                    if duplicate:
                        photo = slots_with_photo[slot_idx % len(slots_with_photo)]
                    else:
                        if slot_idx >= len(slots_with_photo):
                            photo = None
                        else:
                            photo = slots_with_photo[slot_idx]
                    if photo:
                        img = photo.get_image()
                        if img:
                            frame = self._make_polaroid_frame(img, tw_px, th_px, offset_y=photo.offset_y)
                            sheet.paste(frame, (x, y))
                            slot_idx += 1
                            continue
                slot_idx += 1
        return sheet

    def refresh_polaroid_a4_preview(self):
        canvas = self.polaroid_a4_canvas
        canvas.delete("all")
        width = max(100, canvas.winfo_width())
        height = max(100, canvas.winfo_height())
        has_photo = any(s.path for s in self.polaroid_slots)
        if not has_photo:
            canvas.create_text(
                width // 2, height // 2,
                text="Escolha uma foto para ver a folha A4",
                fill=MUTED, font=("Segoe UI", 9)
            )
            return
        grid = self.get_polaroid_sheet_grid()
        if grid is None:
            canvas.create_text(
                width // 2, height // 2,
                text="O frame não cabe na folha A4",
                fill="#B00020", font=("Segoe UI", 9)
            )
            return
        try:
            sheet = self._compose_polaroid_a4(grid)
        except Exception:
            return
        thumb = sheet.copy()
        thumb.thumbnail((width - 16, height - 16), Image.Resampling.LANCZOS)
        self.polaroid_a4_tk = ImageTk.PhotoImage(thumb)
        canvas.create_image(width // 2, height // 2, image=self.polaroid_a4_tk)

    def build_polaroid_a4_sheet(self):
        has_photo = any(s.path for s in self.polaroid_slots)
        if not has_photo:
            messagebox.showwarning("Sem fotos", "Adicione pelo menos uma foto.")
            return None
        grid = self.get_polaroid_sheet_grid()
        if grid is None:
            messagebox.showwarning(
                "Frame muito grande",
                "O frame não cabe na folha A4. Reduza as dimensões."
            )
            return None
        sheet = self._compose_polaroid_a4(grid)
        dims = self.get_polaroid_dimensions()
        tw, th = dims[0], dims[1]
        margin, gap, w, h, cols, rows, start_x, start_y = grid
        return sheet, cols, rows, tw, th

    def export_polaroid_a4(self):
        built_sheet = self.build_polaroid_a4_sheet()
        if built_sheet is None:
            return
        sheet, cols, rows, tw, th = built_sheet
        try:
            path = filedialog.asksaveasfilename(
                title="Salvar folha A4 de Polaroides",
                defaultextension=".jpg",
                initialfile=f"folha_A4_polaroides_{tw:g}x{th:g}_{save_timestamp()}",
                filetypes=[("JPEG", "*.jpg"), ("PNG", "*.png")]
            )
        except KeyboardInterrupt:
            return
        if not path:
            return
        try:
            ext = Path(path).suffix.lower()
            if ext in (".jpg", ".jpeg"):
                sheet.save(path, "JPEG", quality=95, dpi=(DPI, DPI), subsampling=0)
            else:
                sheet.save(path, "PNG", dpi=(DPI, DPI))
            messagebox.showinfo(
                "Arquivo criado",
                f"Folha A4 exportada com polaroides de {tw:g} × {th:g} cm em 300 DPI."
            )
        except Exception as exc:
            messagebox.showerror("Erro", str(exc))

    def print_polaroid_a4(self):
        has_photo = any(s.path for s in self.polaroid_slots)
        if not has_photo:
            messagebox.showwarning("Sem fotos", "Adicione pelo menos uma foto antes de imprimir.")
            return
        built_sheet = self.build_polaroid_a4_sheet()
        if built_sheet is None:
            return
        sheet, cols, rows, tw, th = built_sheet
        self.print_image(sheet, f"Folha A4 - Polaroides {tw:g}x{th:g} cm")

    def build_vinyl_tab(self):
        header = tk.Frame(
            self.vinyl_tab, bg=PANEL, height=76,
            highlightbackground=BORDER, highlightthickness=1
        )
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text="Vinil", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 20, "bold")
        ).pack(anchor="w", padx=24, pady=(12, 0))
        tk.Label(
            header,
            text=f"Largura máx. {CRICUT_MAX_CM} cm • Cricut Joy (meia folha A4) • 300 DPI",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 10)
        ).pack(anchor="w", padx=24)

        main_body = tk.Frame(self.vinyl_tab, bg=BG)
        main_body.pack(fill="both", expand=True)

        scroll_container = tk.Frame(main_body, bg=BG)
        scroll_container.pack(side="left", fill="both", expand=True)

        self.vinyl_canvas = tk.Canvas(
            scroll_container, bg=BG, highlightthickness=0
        )
        self.vinyl_canvas.pack(side="left", fill="both", expand=True)
        vinyl_scrollbar = tk.Scrollbar(
            scroll_container, orient="vertical",
            command=self.vinyl_canvas.yview
        )
        vinyl_scrollbar.pack(side="right", fill="y")
        self.vinyl_canvas.configure(yscrollcommand=vinyl_scrollbar.set)

        content = tk.Frame(self.vinyl_canvas, bg=BG)
        self.vinyl_content = content
        self.vinyl_min_content_height = 640
        self.vinyl_window = self.vinyl_canvas.create_window(
            (0, 0), window=content, anchor="nw"
        )
        content.configure(padx=24, pady=24)
        content.bind("<Configure>", self.update_vinyl_scrollregion)
        self.vinyl_canvas.bind("<Configure>", self.resize_vinyl_content)
        scroll_container.bind("<Enter>", self.bind_vinyl_mousewheel)
        scroll_container.bind("<Leave>", self.unbind_vinyl_mousewheel)

        controls = tk.Frame(
            content, bg=PANEL,
            highlightbackground=BORDER, highlightthickness=1
        )
        controls.pack(fill="x")

        tk.Label(
            controls, text="Configurações", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=20, pady=(16, 10))

        tk.Label(
            controls, text=f"Largura máx. do adesivo: {CRICUT_MAX_CM} cm",
            bg=PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold")
        ).pack(anchor="w", padx=20)

        tk.Label(
            controls, text="Dimensões do adesivo (cm)", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=20, pady=(8, 0))
        ad_dims = tk.Frame(controls, bg=PANEL)
        ad_dims.pack(fill="x", padx=20, pady=(4, 8))
        self._dimension_field(ad_dims, "Largura", self.vinyl_total_w).pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        self._dimension_field(ad_dims, "Altura", self.vinyl_total_h).pack(
            side="left", fill="x", expand=True, padx=(5, 0)
        )

        tk.Label(
            controls, text="Dimensões da imagem (cm)", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=20)
        img_dims = tk.Frame(controls, bg=PANEL)
        img_dims.pack(fill="x", padx=20, pady=(4, 8))
        self._dimension_field(img_dims, "Largura imagem", self.vinyl_img_w).pack(
            side="left", fill="x", expand=True, padx=(0, 5)
        )
        self._dimension_field(img_dims, "Altura imagem", self.vinyl_img_h).pack(
            side="left", fill="x", expand=True, padx=(5, 0)
        )

        tk.Checkbutton(
            controls, text="Borda preta no adesivo", variable=self.vinyl_border_var,
            command=self.refresh_vinyl_preview, bg=PANEL, fg=TEXT,
            activebackground=PANEL, font=("Segoe UI", 9)
        ).pack(anchor="w", padx=20, pady=(0, 6))

        tk.Checkbutton(
            controls, text="Exportar sem fundo (transparente)", variable=self.vinyl_transparent_var,
            bg=PANEL, fg=TEXT,
            activebackground=PANEL, font=("Segoe UI", 9)
        ).pack(anchor="w", padx=20, pady=(0, 6))

        tk.Label(
            controls, textvariable=self.vinyl_count_var, bg=PANEL, fg=MUTED,
            justify="left", font=("Segoe UI", 9), wraplength=0
        ).pack(anchor="w", padx=20, pady=(0, 8))

        tk.Label(
            controls, text="Adesivos na folha A4", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=20, pady=(0, 4))

        top_frame = tk.Frame(controls, bg=PANEL)
        top_frame.pack(fill="x", padx=20, pady=(0, 4))
        tk.Label(
            top_frame, text="Parte de cima", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 10)
        ).pack(side="left")
        tk.Spinbox(
            top_frame, from_=0, to=24, width=5,
            textvariable=self.vinyl_num_top,
            command=self.build_vinyl_grid,
            font=("Segoe UI", 10)
        ).pack(side="left", padx=(8, 0))

        bot_frame = tk.Frame(controls, bg=PANEL)
        bot_frame.pack(fill="x", padx=20, pady=(0, 8))
        tk.Label(
            bot_frame, text="Parte de baixo", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 10)
        ).pack(side="left")
        tk.Spinbox(
            bot_frame, from_=0, to=24, width=5,
            textvariable=self.vinyl_num_bottom,
            command=self.build_vinyl_grid,
            font=("Segoe UI", 10)
        ).pack(side="left", padx=(8, 0))

        self.action_button(
            controls, "Atualizar prévia", self.refresh_vinyl_preview
        ).pack(fill="x", padx=20, pady=(0, 10))

        self.action_button(
            controls, "Importar vários (parte de cima)", lambda: self.import_vinyl_batch("top"), secondary=True
        ).pack(fill="x", padx=20, pady=(0, 6))

        self.action_button(
            controls, "Importar vários (parte de baixo)", lambda: self.import_vinyl_batch("bot"), secondary=True
        ).pack(fill="x", padx=20, pady=(0, 10))

        self.vinyl_grid = tk.Frame(content, bg=BG)
        self.vinyl_grid.pack(fill="both", expand=True, pady=(10, 0))
        self.vinyl_slots = []
        self.build_vinyl_grid()

        preview_scroll = tk.Frame(main_body, bg=BG)
        preview_scroll.pack(side="right", fill="y", padx=(20, 0))

        preview_canvas = tk.Canvas(
            preview_scroll, bg=PANEL, highlightthickness=0
        )
        preview_canvas.pack(side="left", fill="both", expand=True)
        preview_scrollbar = tk.Scrollbar(
            preview_scroll, orient="vertical", command=preview_canvas.yview
        )
        preview_scrollbar.pack(side="right", fill="y")
        preview_canvas.configure(yscrollcommand=preview_scrollbar.set)

        preview_panel = tk.Frame(preview_canvas, bg=PANEL)
        self._vinyl_preview_window = preview_canvas.create_window(
            (0, 0), window=preview_panel, anchor="nw"
        )
        preview_panel.configure(padx=0, pady=0)
        preview_panel.bind(
            "<Configure>",
            lambda e: preview_canvas.configure(scrollregion=preview_canvas.bbox("all"))
        )
        preview_canvas.bind(
            "<Configure>",
            lambda e: preview_canvas.itemconfigure(
                self._vinyl_preview_window, width=e.width
            )
        )

        tk.Label(
            preview_panel, text="Prévia", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=20, pady=(0, 4))
        self.vinyl_info = tk.Label(
            preview_panel, text="Nenhum adesivo adicionado",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9)
        )
        self.vinyl_info.pack(anchor="w", padx=20, pady=(0, 8))

        tk.Label(
            preview_panel, text="Prévia na folha A4", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=20, pady=(0, 4))
        self.vinyl_a4_canvas = tk.Canvas(
            preview_panel, bg="#E5E0D6", highlightthickness=0
        )
        self.vinyl_a4_canvas.pack(fill="x", padx=20, pady=(0, 20))

        actions = tk.Frame(preview_panel, bg=PANEL)
        actions.pack(fill="x", padx=20, pady=(0, 16))
        self.action_button(
            actions, "Exportar adesivo", self.export_vinyl
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.action_button(
            actions, "Exportar para A4", self.export_vinyl_a4
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))
        actions2 = tk.Frame(preview_panel, bg=PANEL)
        actions2.pack(fill="x", padx=20, pady=(0, 8))
        self.action_button(
            actions2, "Imprimir no Windows", self.print_vinyl_a4
        ).pack(fill="x", expand=True)

        actions3 = tk.Frame(preview_panel, bg=PANEL)
        actions3.pack(fill="x", padx=20, pady=(0, 16))
        self.action_button(
            actions3, "Exportar imagens sem fundo",
            self.export_vinyl_images_transparent, secondary=True
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.action_button(
            actions3, "SVG Topo (Cricut)",
            lambda: self.export_vinyl_svg("top"), secondary=True
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.action_button(
            actions3, "SVG Baixo (Cricut)",
            lambda: self.export_vinyl_svg("bot"), secondary=True
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        preview_scroll.bind("<Enter>", lambda e: self.root.bind_all(
            "<MouseWheel>", lambda ev: preview_canvas.yview_scroll(
                -int(ev.delta / 120) * WHEEL_SCROLL_LINES, "units"
            )
        ))
        preview_scroll.bind("<Leave>", lambda e: self.root.unbind_all("<MouseWheel>"))

        self.update_vinyl_sheet_count()

    def build_vinyl_grid(self, *args):
        for slot in self.vinyl_slots:
            slot.frame.destroy()
        self.vinyl_slots.clear()
        try:
            n_top = max(0, min(24, self.vinyl_num_top.get()))
        except (tk.TclError, AttributeError):
            n_top = 9
        try:
            n_bot = max(0, min(24, self.vinyl_num_bottom.get()))
        except (tk.TclError, AttributeError):
            n_bot = 9

        if n_top > 0:
            tk.Label(
                self.vinyl_grid, text="Parte de cima", bg=BG, fg=MUTED,
                font=("Segoe UI", 9, "bold")
            ).grid(row=0, column=0, columnspan=3, sticky="w", padx=4, pady=(0, 4))
            for i in range(n_top):
                card = VinylSlotCard(self.vinyl_grid, i, self.refresh_vinyl_preview)
                card.frame.grid(row=1 + i // 3, column=i % 3, padx=4, pady=4, sticky="nsew")
                self.vinyl_slots.append(card)

        if n_top > 0 and n_bot > 0:
            sep_row = 1 + ((n_top + 2) // 3)
            tk.Frame(self.vinyl_grid, bg=ACCENT, height=2).grid(
                row=sep_row, column=0, columnspan=3, sticky="ew", padx=4, pady=8
            )
            sep_label_row = sep_row + 1
        else:
            sep_label_row = 0 if n_top == 0 else 1 + ((n_top + 2) // 3)

        if n_bot > 0:
            tk.Label(
                self.vinyl_grid, text="Parte de baixo", bg=BG, fg=MUTED,
                font=("Segoe UI", 9, "bold")
            ).grid(row=sep_label_row, column=0, columnspan=3, sticky="w", padx=4, pady=(0, 4))
            for i in range(n_bot):
                card = VinylSlotCard(self.vinyl_grid, n_top + i, self.refresh_vinyl_preview)
                card.frame.grid(row=sep_label_row + 1 + i // 3, column=i % 3, padx=4, pady=4, sticky="nsew")
                self.vinyl_slots.append(card)

        for c in range(3):
            self.vinyl_grid.grid_columnconfigure(c, weight=1, uniform="vinyl")

    def update_vinyl_scrollregion(self, event=None):
        self.vinyl_canvas.configure(scrollregion=self.vinyl_canvas.bbox("all"))

    def resize_vinyl_content(self, event):
        self.vinyl_canvas.itemconfigure(
            self.vinyl_window,
            width=event.width,
            height=max(
                event.height,
                self.vinyl_min_content_height,
                self.vinyl_content.winfo_reqheight()
            )
        )

    def bind_vinyl_mousewheel(self, event=None):
        self.root.bind_all("<MouseWheel>", self.scroll_vinyl_with_mouse)

    def unbind_vinyl_mousewheel(self, event=None):
        self.root.unbind_all("<MouseWheel>")

    def scroll_vinyl_with_mouse(self, event):
        if event.delta:
            self.vinyl_canvas.yview_scroll(
                -int(event.delta / 120) * WHEEL_SCROLL_LINES, "units"
            )

    def _parse_vinyl_dims_silent(self):
        try:
            tw = float(self.vinyl_total_w.get().replace(",", "."))
            th = float(self.vinyl_total_h.get().replace(",", "."))
            iw = float(self.vinyl_img_w.get().replace(",", "."))
            ih = float(self.vinyl_img_h.get().replace(",", "."))
            return tw, th, iw, ih
        except ValueError:
            return None

    def get_vinyl_dimensions(self):
        try:
            tw = float(self.vinyl_total_w.get().replace(",", "."))
            th = float(self.vinyl_total_h.get().replace(",", "."))
            iw = float(self.vinyl_img_w.get().replace(",", "."))
            ih = float(self.vinyl_img_h.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Tamanho inválido", "Informe todas as dimensões usando números.")
            return None
        if not (0.5 <= tw <= 100 and 0.5 <= th <= 100 and 0.5 <= iw <= 100 and 0.5 <= ih <= 100):
            messagebox.showerror("Tamanho inválido", "As dimensões devem estar entre 0,5 e 100 cm.")
            return None
        if tw > CRICUT_MAX_CM or th > CRICUT_MAX_CM:
            messagebox.showerror(
                "Largura/Altura excedida",
                f"O adesivo não pode ter mais de {CRICUT_MAX_CM} cm.\n"
                f"Valor informado: {tw:g} × {th:g} cm."
            )
            return None
        if iw > tw or ih > th:
            messagebox.showerror(
                "Dimensão inválida",
                "A imagem deve ser menor ou igual ao adesivo nos dois eixos."
            )
            return None
        tw_px = int(round(tw / 2.54 * DPI))
        th_px = int(round(th / 2.54 * DPI))
        iw_px = int(round(iw / 2.54 * DPI))
        ih_px = int(round(ih / 2.54 * DPI))
        return tw, th, iw, ih, tw_px, th_px, iw_px, ih_px

    def _make_vinyl_sticker(self, image, tw_px, th_px, iw_px, ih_px):
        photo = cover_fit(image, iw_px, ih_px)
        sticker = Image.new("RGB", (tw_px, th_px), "white")
        x = (tw_px - iw_px) // 2
        y = (th_px - ih_px) // 2
        sticker.paste(photo, (x, y))
        if self.vinyl_border_var.get():
            draw = ImageDraw.Draw(sticker)
            draw.rectangle(
                (0, 0, tw_px - 1, th_px - 1),
                outline=(0, 0, 0), width=2
            )
        return sticker

    def build_vinyl_image(self):
        if not self.vinyl_slots:
            return None
        first_with_photo = next((s for s in self.vinyl_slots if s.path), None)
        if not first_with_photo:
            messagebox.showwarning("Sem adesivos", "Adicione pelo menos um adesivo.")
            return None
        dims = self.get_vinyl_dimensions()
        if dims is None:
            return None
        tw, th, iw, ih, tw_px, th_px, iw_px, ih_px = dims
        try:
            image = first_with_photo.get_image()
            sticker = self._make_vinyl_sticker(image, tw_px, th_px, iw_px, ih_px)
            return sticker, tw, th, iw, ih
        except Exception as exc:
            messagebox.showerror("Erro", f"Não foi possível processar a imagem.\n\n{exc}")
            return None

    def import_vinyl_batch(self, zone="top"):
        try:
            n_top = max(0, self.vinyl_num_top.get())
        except (tk.TclError, AttributeError):
            n_top = 0
        try:
            n_bot = max(0, self.vinyl_num_bottom.get())
        except (tk.TclError, AttributeError):
            n_bot = 0
        if zone == "top":
            title = "Escolher adesivos (parte de cima)"
            slots = self.vinyl_slots[:n_top]
        else:
            title = "Escolher adesivos (parte de baixo)"
            slots = self.vinyl_slots[n_top:n_top + n_bot]
        try:
            paths = filedialog.askopenfilenames(
                title=title,
                filetypes=[("Imagens", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff")]
            )
        except KeyboardInterrupt:
            return
        if not paths:
            return
        for i, path in enumerate(paths):
            if i >= len(slots):
                break
            slot = slots[i]
            slot.path = path
            slot.rotation = 90
            slot.refresh()
        self.refresh_vinyl_preview()

    def refresh_vinyl_preview(self):
        dims = self.get_vinyl_dimensions()
        if dims is None:
            return
        tw, th, iw, ih, tw_px, th_px, iw_px, ih_px = dims
        count = sum(1 for s in self.vinyl_slots if s.path)
        if count:
            self.vinyl_info.configure(
                text=f"{count} adesivo(s) • {tw:g} × {th:g} cm • imagem {iw:g} × {ih:g} cm"
            )
        else:
            self.vinyl_info.configure(text="Nenhum adesivo adicionado")
        self.refresh_vinyl_a4_preview()

    def export_vinyl(self):
        built = self.build_vinyl_image()
        if built is None:
            return
        image, tw, th, iw, ih = built
        try:
            path = filedialog.asksaveasfilename(
                title="Salvar adesivo",
                defaultextension=".jpg",
                initialfile=f"adesivo_{tw:g}x{th:g}cm_{save_timestamp()}",
                filetypes=[("JPEG", "*.jpg"), ("PNG", "*.png")]
            )
        except KeyboardInterrupt:
            return
        if not path:
            return
        try:
            ext = Path(path).suffix.lower()
            if ext in (".jpg", ".jpeg"):
                image.save(path, "JPEG", quality=95, dpi=(DPI, DPI), subsampling=0)
            else:
                image.save(path, "PNG", dpi=(DPI, DPI))
            messagebox.showinfo(
                "Arquivo criado",
                f"Adesivo exportado com {tw:g} × {th:g} cm em 300 DPI."
            )
        except Exception as exc:
            messagebox.showerror("Erro", str(exc))

    def get_vinyl_zone_layout(self):
        dims = self.get_vinyl_dimensions()
        if dims is None:
            return None
        _, _, _, _, tw_px, th_px, _, _ = dims
        if tw_px > int(round(CRICUT_MAX_CM / 2.54 * DPI)):
            return None
        margin = int(round(1.5 / 2.54 * DPI))
        gap = int(round(0.5 / 2.54 * DPI))
        zone_w = int(round(16.5 / 2.54 * DPI))
        zone_h = int(round(11.5 / 2.54 * DPI))
        zone_x = (A4_W - zone_w) // 2
        zone_y = margin
        cut_y = int(round(14.0 / 2.54 * DPI))
        zone_top = (zone_x, zone_y, zone_x + zone_w, cut_y - gap)
        zone_bot = (zone_x, cut_y + gap, zone_x + zone_w, cut_y + gap + zone_h)
        cols = (zone_w + gap) // (tw_px + gap)
        if cols < 1:
            return None
        zone_top_h = zone_top[3] - zone_top[1]
        zone_bot_h = zone_bot[3] - zone_bot[1]
        rows_top = max(0, (zone_top_h + gap) // (th_px + gap)) if zone_top_h > 0 else 0
        rows_bot = max(0, (zone_bot_h + gap) // (th_px + gap)) if zone_bot_h > 0 else 0
        cap_top = cols * rows_top
        cap_bot = cols * rows_bot
        try:
            n_top = max(0, self.vinyl_num_top.get())
        except (tk.TclError, AttributeError):
            n_top = 0
        try:
            n_bot = max(0, self.vinyl_num_bottom.get())
        except (tk.TclError, AttributeError):
            n_bot = 0
        n_top = min(n_top, cap_top)
        n_bot = min(n_bot, cap_bot)
        return {
            "margin": margin, "gap": gap, "tw_px": tw_px, "th_px": th_px,
            "cols": cols, "rows_top": rows_top, "rows_bot": rows_bot,
            "zone_top": zone_top, "zone_bot": zone_bot,
            "n_top": n_top, "n_bot": n_bot,
            "cap_top": cap_top, "cap_bot": cap_bot,
            "zone_x": zone_x, "zone_w": zone_w,
        }

    def _get_font(self, size=36):
        font = ImageFont.load_default()
        for font_path in (
            Path(__file__).parent / "arial.ttf",
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/segoeui.ttf"),
        ):
            if font_path.exists():
                try:
                    font = ImageFont.truetype(str(font_path), size)
                    break
                except Exception:
                    pass
        return font

    def update_vinyl_sheet_count(self, *args):
        dims = self._parse_vinyl_dims_silent()
        if dims is None:
            self.vinyl_count_var.set("")
            return
        tw, th, iw, ih = dims
        if not (0.5 <= tw <= 100 and 0.5 <= th <= 100 and 0.5 <= iw <= 100 and 0.5 <= ih <= 100):
            self.vinyl_count_var.set("")
            return
        if tw > CRICUT_MAX_CM or th > CRICUT_MAX_CM:
            self.vinyl_count_var.set(f"Largura ou altura > {CRICUT_MAX_CM} cm.")
            return
        if iw > tw or ih > th:
            self.vinyl_count_var.set("Imagem maior que o adesivo.")
            return
        layout = self.get_vinyl_zone_layout()
        if layout is None:
            self.vinyl_count_var.set("O adesivo não cabe na folha A4.")
            return
        photos = sum(1 for s in self.vinyl_slots if s.path) if hasattr(self, "vinyl_slots") else 0
        n_top, n_bot = layout["n_top"], layout["n_bot"]
        cap_top, cap_bot = layout["cap_top"], layout["cap_bot"]
        self.vinyl_count_var.set(
            f"Cima: {n_top}/{cap_top} • Baixo: {n_bot}/{cap_bot}"
            + (f" • {photos} com foto" if photos else "")
        )
        if hasattr(self, "vinyl_a4_canvas"):
            if hasattr(self, "_vinyl_a4_job"):
                self.root.after_cancel(self._vinyl_a4_job)
            self._vinyl_a4_job = self.root.after(400, self.refresh_vinyl_a4_preview)

    def _compose_vinyl_a4(self, layout):
        dims = self.get_vinyl_dimensions()
        _, _, _, _, tw_px, th_px, iw_px, ih_px = dims
        gap = layout["gap"]
        cols = layout["cols"]
        rows_top = layout["rows_top"]
        rows_bot = layout["rows_bot"]
        zone_top = layout["zone_top"]
        zone_bot = layout["zone_bot"]
        n_top = layout["n_top"]
        n_bot = layout["n_bot"]
        zone_x = layout["zone_x"]
        zone_w = layout["zone_w"]
        transparent = self.vinyl_transparent_var.get()
        if transparent:
            sheet = Image.new("RGBA", (A4_W, A4_H), (0, 0, 0, 0))
        else:
            sheet = Image.new("RGB", (A4_W, A4_H), "white")
        font = self._get_font(36)
        font_sm = self._get_font(28)

        draw_all = ImageDraw.Draw(sheet)
        border_color = (0, 0, 0, 255) if transparent else (0, 0, 0)
        draw_all.rectangle(
            (zone_top[0], zone_top[1], zone_top[2], zone_top[3]),
            outline=border_color, width=8
        )
        if n_bot > 0:
            draw_all.rectangle(
                (zone_bot[0], zone_bot[1], zone_bot[2], zone_bot[3]),
                outline=border_color, width=8
            )

        top_photos = [s for s in self.vinyl_slots[:n_top] if s.path]
        bot_photos = [s for s in self.vinyl_slots[n_top:n_top + n_bot] if s.path]

        def _draw_zone(zone_x0, zone_y0, zone_x1, zone_y1, rows, zone_photos, label):
            zone_h = zone_y1 - zone_y0
            zone_w_local = zone_x1 - zone_x0
            total_grid_w = cols * tw_px + (cols - 1) * gap
            total_grid_h = rows * th_px + (rows - 1) * gap
            bx = zone_x0 + (zone_w_local - total_grid_w) // 2
            by = zone_y0 + (zone_h - total_grid_h) // 2
            slot_i = 0
            for r in range(rows):
                for c in range(cols):
                    if slot_i >= len(zone_photos):
                        return
                    x = bx + c * (tw_px + gap)
                    y = by + r * (th_px + gap)
                    img = zone_photos[slot_i].get_image()
                    sticker = self._make_vinyl_sticker(img, tw_px, th_px, iw_px, ih_px)
                    if transparent:
                        sticker = sticker.convert("RGBA")
                    sheet.paste(sticker, (x, y))
                    slot_i += 1
            draw = ImageDraw.Draw(sheet)
            label_color = (140, 140, 140, 255) if transparent else (140, 140, 140)
            draw.text(
                (zone_x0 + 8, zone_y0 + 6),
                label, fill=label_color, font=font_sm
            )

        if n_top > 0:
            _draw_zone(zone_top[0], zone_top[1], zone_top[2], zone_top[3], rows_top, top_photos, "Parte de cima")
        if n_bot > 0:
            _draw_zone(zone_bot[0], zone_bot[1], zone_bot[2], zone_bot[3], rows_bot, bot_photos, "Parte de baixo")

        return sheet

    def refresh_vinyl_a4_preview(self):
        canvas = self.vinyl_a4_canvas
        canvas.delete("all")
        width = max(100, canvas.winfo_width())
        height = max(100, canvas.winfo_height())
        has_photo = any(s.path for s in self.vinyl_slots)
        if not has_photo:
            canvas.create_text(
                width // 2, height // 2,
                text="Escolha uma foto para ver a folha A4",
                fill=MUTED, font=("Segoe UI", 9)
            )
            return
        layout = self.get_vinyl_zone_layout()
        if layout is None:
            canvas.create_text(
                width // 2, height // 2,
                text="O adesivo não cabe na folha A4",
                fill="#B00020", font=("Segoe UI", 9)
            )
            return
        if layout["n_top"] == 0 and layout["n_bot"] == 0:
            canvas.create_text(
                width // 2, height // 2,
                text="Defina adesivos na parte de cima ou de baixo",
                fill=MUTED, font=("Segoe UI", 9)
            )
            return
        try:
            sheet = self._compose_vinyl_a4(layout)
        except Exception:
            return
        thumb = sheet.copy()
        thumb.thumbnail((width - 16, height - 16), Image.Resampling.LANCZOS)
        self.vinyl_a4_tk = ImageTk.PhotoImage(thumb)
        canvas.create_image(width // 2, height // 2, image=self.vinyl_a4_tk)

    def build_vinyl_a4_sheet(self):
        has_photo = any(s.path for s in self.vinyl_slots)
        if not has_photo:
            messagebox.showwarning("Sem adesivos", "Adicione pelo menos um adesivo.")
            return None
        layout = self.get_vinyl_zone_layout()
        if layout is None:
            messagebox.showwarning(
                "Adesivo muito grande",
                "O adesivo não cabe na folha A4. Reduza as dimensões."
            )
            return None
        if layout["n_top"] == 0 and layout["n_bot"] == 0:
            messagebox.showwarning(
                "Sem adesivos",
                "Defina adesivos na parte de cima ou de baixo."
            )
            return None
        sheet = self._compose_vinyl_a4(layout)
        dims = self.get_vinyl_dimensions()
        tw, th = dims[0], dims[1]
        return sheet, layout, tw, th

    def export_vinyl_a4(self):
        built_sheet = self.build_vinyl_a4_sheet()
        if built_sheet is None:
            return
        sheet, layout, tw, th = built_sheet
        n_top, n_bot = layout["n_top"], layout["n_bot"]
        transparent = self.vinyl_transparent_var.get()
        try:
            if transparent:
                filetypes = [("PNG", "*.png")]
            else:
                filetypes = [("JPEG", "*.jpg"), ("PNG", "*.png")]
            path = filedialog.asksaveasfilename(
                title="Salvar folha A4 de adesivos",
                defaultextension=".png" if transparent else ".jpg",
                initialfile=f"folha_A4_adesivos_{tw:g}x{th:g}_{save_timestamp()}",
                filetypes=filetypes
            )
        except KeyboardInterrupt:
            return
        if not path:
            return
        try:
            ext = Path(path).suffix.lower()
            if transparent or ext in (".png",):
                if sheet.mode != "RGBA":
                    sheet = sheet.convert("RGBA")
                sheet.save(path, "PNG", dpi=(DPI, DPI))
            else:
                sheet.save(path, "JPEG", quality=95, dpi=(DPI, DPI), subsampling=0)
            messagebox.showinfo(
                "Arquivo criado",
                f"Folha A4 exportada com adesivos de {tw:g} × {th:g} cm em 300 DPI.\n"
                f"Cima: {n_top} • Baixo: {n_bot} • Linha de corte no meio."
            )
        except Exception as exc:
            messagebox.showerror("Erro", str(exc))

    def export_vinyl_svg(self, zone="top"):
        layout = self.get_vinyl_zone_layout()
        if layout is None:
            messagebox.showwarning(
                "Adesivo muito grande",
                "O adesivo não cabe na folha A4. Reduza as dimensões."
            )
            return
        dims = self.get_vinyl_dimensions()
        if dims is None:
            return
        _, _, _, _, tw_px, th_px, iw_px, ih_px = dims
        n_top = layout["n_top"]
        n_bot = layout["n_bot"]
        if zone == "top":
            slots = self.vinyl_slots[:n_top]
        else:
            slots = self.vinyl_slots[n_top:n_top + n_bot]
        photos = [s for s in slots if s.path]
        if not photos:
            messagebox.showwarning("Sem adesivo", "Adicione adesivos nessa zona.")
            return
        label = "Parte de cima" if zone == "top" else "Parte de baixo"
        try:
            path = filedialog.asksaveasfilename(
                title=f"Salvar silhueta PNG — {label}",
                defaultextension=".png",
                initialfile=f"silhueta_{zone}_{save_timestamp()}",
                filetypes=[("PNG", "*.png")]
            )
        except KeyboardInterrupt:
            return
        if not path:
            return
        try:
            gap = layout["gap"]
            cols = layout["cols"]
            if zone == "top":
                zone_coords = layout["zone_top"]
                rows = layout["rows_top"]
            else:
                zone_coords = layout["zone_bot"]
                rows = layout["rows_bot"]
            zx, zy, zx2, zy2 = zone_coords
            zw = zx2 - zx
            zh = zy2 - zy
            total_grid_w = cols * tw_px + (cols - 1) * gap
            total_grid_h = rows * th_px + (rows - 1) * gap
            bx = zx + (zw - total_grid_w) // 2
            by = zy + (zh - total_grid_h) // 2
            sheet = Image.new("RGBA", (zw, zh), (0, 0, 0, 0))
            slot_i = 0
            for r in range(rows):
                for c in range(cols):
                    if slot_i >= len(photos):
                        break
                    x = bx + c * (tw_px + gap) - zx
                    y = by + r * (th_px + gap) - zy
                    img = photos[slot_i].get_image()
                    if img is None:
                        slot_i += 1
                        continue
                    fitted = cover_fit(img, iw_px, ih_px)
                    gray = fitted.convert("L")
                    mask = gray.point(lambda p: 255 if p < 240 else 0)
                    mask = mask.filter(ImageFilter.MaxFilter(5))
                    silhouette = Image.new("RGBA", fitted.size, (0, 0, 0, 0))
                    black_layer = Image.new("RGBA", fitted.size, (0, 0, 0, 255))
                    silhouette.paste(black_layer, (0, 0), mask)
                    sticker = Image.new("RGBA", (tw_px, th_px), (0, 0, 0, 0))
                    sx = (tw_px - fitted.width) // 2
                    sy = (th_px - fitted.height) // 2
                    sticker.paste(silhouette, (sx, sy), silhouette)
                    sheet.paste(sticker, (x, y), sticker)
                    slot_i += 1
            sheet.save(path, "PNG", dpi=(DPI, DPI))
            messagebox.showinfo(
                "Silhueta exportada",
                f"Silhueta exportada ({label}).\n"
                f"{len(photos)} adesivo(s) em preto sobre fundo transparente.\n"
                f"Importe em programa de vetorização para gerar SVG."
            )
        except Exception as exc:
            messagebox.showerror("Erro", str(exc))

    def export_vinyl_images_transparent(self):
        layout = self.get_vinyl_zone_layout()
        if layout is None:
            messagebox.showwarning(
                "Adesivo muito grande",
                "O adesivo não cabe na folha A4. Reduza as dimensões."
            )
            return
        dims = self.get_vinyl_dimensions()
        if dims is None:
            return
        _, _, _, _, tw_px, th_px, iw_px, ih_px = dims
        n_top = layout["n_top"]
        n_bot = layout["n_bot"]
        all_photos_top = [s for s in self.vinyl_slots[:n_top] if s.path]
        all_photos_bot = [s for s in self.vinyl_slots[n_top:n_top + n_bot] if s.path]
        all_photos = all_photos_top + all_photos_bot
        if not all_photos:
            messagebox.showwarning("Sem imagens", "Adicione imagens antes de exportar.")
            return
        try:
            path = filedialog.asksaveasfilename(
                title="Salvar imagens sem fundo",
                defaultextension=".png",
                initialfile=f"vinyl_sem_fundo_{save_timestamp()}",
                filetypes=[("PNG", "*.png")]
            )
        except KeyboardInterrupt:
            return
        if not path:
            return
        try:
            gap = layout["gap"]
            cols = layout["cols"]
            zone_top = layout["zone_top"]
            zone_bot = layout["zone_bot"]
            rows_top = layout["rows_top"]
            rows_bot = layout["rows_bot"]

            sheet = Image.new("RGBA", (A4_W, A4_H), (0, 0, 0, 0))

            def _paste_zone(zone_coords, rows, zone_photos):
                zx, zy, zx2, zy2 = zone_coords
                zw = zx2 - zx
                zh = zy2 - zy
                total_grid_w = cols * tw_px + (cols - 1) * gap
                total_grid_h = rows * th_px + (rows - 1) * gap
                bx = zx + (zw - total_grid_w) // 2
                by = zy + (zh - total_grid_h) // 2
                slot_i = 0
                for r in range(rows):
                    for c in range(cols):
                        if slot_i >= len(zone_photos):
                            return
                        x = bx + c * (tw_px + gap)
                        y = by + r * (th_px + gap)
                        img = zone_photos[slot_i].get_image()
                        if img is None:
                            slot_i += 1
                            continue
                        fitted = ImageOps.fit(img, (iw_px, ih_px), Image.Resampling.LANCZOS)
                        fitted_rgba = fitted.convert("RGBA")
                        gray = fitted.convert("L")
                        alpha = gray.point(lambda p: 0 if p > 253 else 255)
                        fitted_rgba.putalpha(alpha)
                        sticker = Image.new("RGBA", (tw_px, th_px), (0, 0, 0, 0))
                        sx = (tw_px - fitted.width) // 2
                        sy = (th_px - fitted.height) // 2
                        sticker.paste(fitted_rgba, (sx, sy), fitted_rgba)
                        sheet.paste(sticker, (x, y), sticker)
                        slot_i += 1

            if n_top > 0:
                _paste_zone(zone_top, rows_top, all_photos_top)
            if n_bot > 0:
                _paste_zone(zone_bot, rows_bot, all_photos_bot)

            sheet.save(path, "PNG", dpi=(DPI, DPI))
            messagebox.showinfo(
                "Imagens exportadas",
                f"Imagens exportadas sem fundo.\n"
                f"{len(all_photos)} imagem(ns) no layout A4."
            )
        except Exception as exc:
            messagebox.showerror("Erro", str(exc))

    def print_vinyl_a4(self):
        has_photo = any(s.path for s in self.vinyl_slots)
        if not has_photo:
            messagebox.showwarning("Sem adesivos", "Adicione pelo menos um adesivo antes de imprimir.")
            return
        built_sheet = self.build_vinyl_a4_sheet()
        if built_sheet is None:
            return
        sheet, layout, tw, th = built_sheet
        if sheet.mode == "RGBA":
            bg = Image.new("RGB", sheet.size, (255, 255, 255))
            bg.paste(sheet, mask=sheet.split()[3])
            sheet = bg
        self.print_image(sheet, f"Folha A4 - Adesivos {tw:g}x{th:g} cm")

    def build_settings_tab(self):
        header = tk.Frame(
            self.settings_tab, bg=PANEL, height=76,
            highlightbackground=BORDER, highlightthickness=1
        )
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text="Chaves de API", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 20, "bold")
        ).pack(anchor="w", padx=24, pady=(12, 0))
        tk.Label(
            header, text="Configure os provedores usados na melhoria de fotos",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 10)
        ).pack(anchor="w", padx=24)

        container = tk.Frame(self.settings_tab, bg=BG)
        container.pack(fill="both", expand=True, padx=24, pady=24)
        card = tk.Frame(
            container, bg=PANEL, highlightbackground=BORDER,
            highlightthickness=1
        )
        card.pack(fill="x", anchor="n")

        tk.Label(
            card, text="Credenciais", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=24, pady=(22, 4))
        tk.Label(
            card,
            text="As chaves são criptografadas pelo Windows e só podem ser "
                 "abertas pelo mesmo usuário neste computador.",
            bg=PANEL, fg=MUTED, justify="left", wraplength=0,
            font=("Segoe UI", 9)
        ).pack(anchor="w", padx=24, pady=(0, 18))

        self.openai_key_entry = self.api_key_field(
            card, "OpenAI API key", self.openai_api_key,
            "Usada com o modelo gpt-image-2."
        )
        self.gemini_key_entry = self.api_key_field(
            card, "Gemini API key", self.gemini_api_key,
            "Usada com o modelo gemini-3.1-flash-image."
        )

        self.show_api_keys = tk.BooleanVar(value=False)
        tk.Checkbutton(
            card, text="Mostrar chaves", variable=self.show_api_keys,
            command=self.toggle_api_key_visibility,
            bg=PANEL, fg=TEXT, activebackground=PANEL,
            font=("Segoe UI", 9)
        ).pack(anchor="w", padx=24, pady=(2, 12))

        actions = tk.Frame(card, bg=PANEL)
        actions.pack(fill="x", padx=24)
        self.action_button(
            actions, "Salvar chaves", self.save_api_keys
        ).pack(side="left")
        self.action_button(
            actions, "Apagar chaves", self.clear_api_keys, secondary=True
        ).pack(side="left", padx=10)
        tk.Label(
            card, textvariable=self.keys_status, bg=PANEL, fg=MUTED,
            font=("Segoe UI", 9)
        ).pack(anchor="w", padx=24, pady=(14, 22))

    def api_key_field(self, parent, label, variable, hint):
        frame = tk.Frame(parent, bg=PANEL)
        frame.pack(fill="x", padx=24, pady=(0, 14))
        tk.Label(
            frame, text=label, bg=PANEL, fg=TEXT,
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w")
        entry = tk.Entry(
            frame, textvariable=variable, show="•",
            bg="#F7F3EB", fg=TEXT, relief="solid", bd=1,
            font=("Segoe UI", 10)
        )
        entry.pack(fill="x", pady=(5, 3), ipady=6)
        tk.Label(
            frame, text=hint, bg=PANEL, fg=MUTED,
            font=("Segoe UI", 8)
        ).pack(anchor="w")
        return entry

    def toggle_api_key_visibility(self):
        mask = "" if self.show_api_keys.get() else "•"
        self.openai_key_entry.configure(show=mask)
        self.gemini_key_entry.configure(show=mask)

    def protect_api_data(self, data):
        raw = data.encode("utf-8")
        buffer = ctypes.create_string_buffer(raw)
        input_blob = DataBlob(
            len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
        )
        output_blob = DataBlob()
        success = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(input_blob), "Montador A4", None, None, None,
            0x1, ctypes.byref(output_blob)
        )
        if not success:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(output_blob.pbData)

    def unprotect_api_data(self, encrypted):
        buffer = ctypes.create_string_buffer(encrypted)
        input_blob = DataBlob(
            len(encrypted), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
        )
        output_blob = DataBlob()
        success = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(input_blob), None, None, None, None,
            0x1, ctypes.byref(output_blob)
        )
        if not success:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(
                output_blob.pbData, output_blob.cbData
            ).decode("utf-8")
        finally:
            ctypes.windll.kernel32.LocalFree(output_blob.pbData)

    def save_api_keys(self):
        try:
            payload = json.dumps({
                "openai": self.openai_api_key.get().strip(),
                "gemini": self.gemini_api_key.get().strip(),
            })
            API_KEYS_FILE.write_bytes(self.protect_api_data(payload))
            self.keys_status.set("Chaves salvas e protegidas com sucesso.")
            messagebox.showinfo("Chaves salvas", "As chaves de API foram salvas com segurança.")
        except Exception as exc:
            messagebox.showerror("Erro", f"Não foi possível salvar as chaves.\n\n{exc}")

    def load_api_keys(self, show_errors=True):
        if not API_KEYS_FILE.exists():
            return
        try:
            payload = json.loads(
                self.unprotect_api_data(API_KEYS_FILE.read_bytes())
            )
            self.openai_api_key.set(payload.get("openai", ""))
            self.gemini_api_key.set(payload.get("gemini", ""))
            self.keys_status.set("Chaves salvas carregadas com sucesso.")
        except Exception as exc:
            if show_errors:
                messagebox.showerror(
                    "Erro",
                    f"Não foi possível carregar as chaves salvas.\n\n{exc}"
                )

    def clear_api_keys(self):
        if not messagebox.askyesno(
            "Apagar chaves",
            "Deseja apagar as chaves salvas deste computador?"
        ):
            return
        self.openai_api_key.set("")
        self.gemini_api_key.set("")
        try:
            if API_KEYS_FILE.exists():
                API_KEYS_FILE.unlink()
            self.keys_status.set("As chaves salvas foram apagadas.")
        except Exception as exc:
            messagebox.showerror("Erro", f"Não foi possível apagar as chaves.\n\n{exc}")

    def _dimension_field(self, parent, label, variable):
        frame = tk.Frame(parent, bg=PANEL)
        tk.Label(
            frame, text=label, bg=PANEL, fg=MUTED, font=("Segoe UI", 9)
        ).pack(anchor="w")
        tk.Entry(
            frame, textvariable=variable, bg="#F7F3EB", fg=TEXT,
            relief="solid", bd=1, font=("Segoe UI", 11)
        ).pack(fill="x", pady=(4, 0), ipady=5)
        return frame

    def choose_individual(self):
        try:
            path = filedialog.askopenfilename(
                title="Escolher foto",
                filetypes=[("Imagens", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff")]
            )
        except KeyboardInterrupt:
            return
        if path:
            self.individual_path = path
            self.individual_rotation = 0
            self.individual_ai_image = None
            self.ai_status.set("Foto carregada. Escolha o provedor e descreva a melhoria.")
            self.refresh_individual_preview()

    def rotate_individual(self, angle):
        if not self.individual_path:
            return
        self.individual_rotation = (self.individual_rotation + angle) % 360
        self.refresh_individual_preview()

    def get_individual_source_image(self):
        if self.individual_ai_image is not None:
            image = self.individual_ai_image.copy()
        else:
            with Image.open(self.individual_path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
        if self.individual_rotation:
            image = image.rotate(-self.individual_rotation, expand=True)
        return image

    def get_individual_dimensions(self):
        try:
            width_cm = float(self.individual_width.get().replace(",", "."))
            height_cm = float(self.individual_height.get().replace(",", "."))
        except ValueError:
            messagebox.showerror("Tamanho inválido", "Informe largura e altura usando números.")
            return None
        if not (0.5 <= width_cm <= 100 and 0.5 <= height_cm <= 100):
            messagebox.showerror(
                "Tamanho inválido",
                "A largura e a altura devem estar entre 0,5 e 100 cm."
            )
            return None
        width_px = int(round(width_cm / 2.54 * DPI))
        height_px = int(round(height_cm / 2.54 * DPI))
        return width_cm, height_cm, width_px, height_px

    def build_individual_image(self):
        if not self.individual_path:
            messagebox.showwarning("Sem foto", "Escolha uma foto primeiro.")
            return None
        dimensions = self.get_individual_dimensions()
        if dimensions is None:
            return None
        width_cm, height_cm, width_px, height_px = dimensions
        try:
            image = self.get_individual_source_image()
            result = cover_fit(image, width_px, height_px)
            return result, width_cm, height_cm
        except Exception as exc:
            messagebox.showerror("Erro", f"Não foi possível processar a imagem.\n\n{exc}")
            return None

    def start_ai_enhancement(self):
        if not self.individual_path:
            messagebox.showwarning("Sem foto", "Escolha uma foto antes de usar a IA.")
            return
        provider = self.ai_provider.get()
        api_key = (
            self.openai_api_key.get().strip()
            if provider == "OpenAI"
            else self.gemini_api_key.get().strip()
        )
        if not api_key:
            messagebox.showwarning(
                "Chave necessária",
                f"Configure e salve a chave de {provider} na aba “Chaves de API”."
            )
            self.notebook.select(self.settings_tab)
            return
        prompt = self.ai_prompt.get("1.0", "end").strip()
        if not prompt:
            messagebox.showwarning("Instrução necessária", "Descreva como a foto deve ser melhorada.")
            return
        try:
            source_image = self.get_individual_source_image()
        except Exception as exc:
            messagebox.showerror("Erro", f"Não foi possível abrir a foto.\n\n{exc}")
            return

        self.ai_button.configure(state="disabled", text="Processando...")
        self.ai_status.set(f"Enviando a foto para {provider}. Isso pode levar alguns minutos.")
        threading.Thread(
            target=self._run_ai_enhancement,
            args=(provider, api_key, prompt, source_image),
            daemon=True
        ).start()

    def _run_ai_enhancement(self, provider, api_key, prompt, source_image):
        try:
            buffer = io.BytesIO()
            source_image.save(buffer, "PNG")
            image_bytes = buffer.getvalue()
            if provider == "OpenAI":
                result_bytes = self._enhance_with_openai(api_key, prompt, image_bytes)
            else:
                result_bytes = self._enhance_with_gemini(api_key, prompt, image_bytes)
            result = Image.open(io.BytesIO(result_bytes)).convert("RGB")
            result.load()
            self.root.after(0, lambda: self._finish_ai_enhancement(provider, result, None))
        except Exception as exc:
            error = str(exc)
            self.root.after(0, lambda: self._finish_ai_enhancement(provider, None, error))

    def _enhance_with_openai(self, api_key, prompt, image_bytes):
        boundary = f"----MontadorA4{uuid.uuid4().hex}"
        parts = []

        def add_field(name, value):
            parts.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ])

        add_field("model", "gpt-image-2")
        add_field("prompt", prompt)
        add_field("quality", "medium")
        add_field("output_format", "png")
        parts.extend([
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="image"; filename="foto.png"\r\n',
            b"Content-Type: image/png\r\n\r\n",
            image_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        request = urllib.request.Request(
            "https://api.openai.com/v1/images/edits",
            data=b"".join(parts),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST"
        )
        response = self._read_json_response(request)
        try:
            return base64.b64decode(response["data"][0]["b64_json"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RuntimeError("A OpenAI não retornou uma imagem válida.") from exc

    def _enhance_with_gemini(self, api_key, prompt, image_bytes):
        payload = {
            "model": "gemini-3.1-flash-image",
            "input": [
                {"type": "text", "text": prompt},
                {
                    "type": "image",
                    "mime_type": "image/png",
                    "data": base64.b64encode(image_bytes).decode("ascii"),
                },
            ],
            "response_format": {"type": "image", "mime_type": "image/png"},
        }
        request = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            method="POST"
        )
        response = self._read_json_response(request)
        image_data = response.get("output_image", {}).get("data")
        if not image_data:
            raise RuntimeError("O Gemini não retornou uma imagem válida.")
        return base64.b64decode(image_data)

    def _read_json_response(self, request):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                details = json.loads(body)
                message = (
                    details.get("error", {}).get("message")
                    or details.get("message")
                    or body
                )
            except json.JSONDecodeError:
                message = body or exc.reason
            raise RuntimeError(f"Erro da API ({exc.code}): {message}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Falha de conexão: {exc.reason}") from exc

    def _finish_ai_enhancement(self, provider, image, error):
        self.ai_button.configure(state="normal", text="Melhorar com IA")
        if error:
            self.ai_status.set(f"Falha no processamento com {provider}.")
            messagebox.showerror("Erro da IA", error)
            return
        self.individual_ai_image = image
        self.individual_rotation = 0
        self.ai_status.set(f"Foto melhorada com {provider}. Use “Desfazer IA” para voltar.")
        self.refresh_individual_preview()

    def undo_ai_enhancement(self):
        if self.individual_ai_image is None:
            return
        self.individual_ai_image = None
        self.individual_rotation = 0
        self.ai_status.set("Melhoria desfeita. A foto original foi restaurada.")
        self.refresh_individual_preview()

    def refresh_individual_preview(self):
        built = self.build_individual_image()
        if built is None:
            return
        image, width_cm, height_cm = built
        preview = image.copy()
        pw = max(100, self.individual_preview.winfo_width() - 10)
        ph = max(100, self.individual_preview.winfo_height() - 10)
        preview.thumbnail((pw, ph), Image.Resampling.LANCZOS)
        self.individual_preview_img = ImageTk.PhotoImage(preview)
        self.individual_preview.configure(image=self.individual_preview_img, text="")
        self.individual_info.configure(
            text=f"{Path(self.individual_path).name}  •  "
                 f"{width_cm:g} × {height_cm:g} cm  •  {image.width} × {image.height} px"
        )

    def export_individual(self):
        built = self.build_individual_image()
        if built is None:
            return
        image, width_cm, height_cm = built
        try:
            path = filedialog.asksaveasfilename(
                title="Salvar foto redimensionada",
                defaultextension=".jpg",
                initialfile=f"foto_{width_cm:g}x{height_cm:g}cm_{save_timestamp()}",
                filetypes=[("JPEG", "*.jpg"), ("PNG", "*.png")]
            )
        except KeyboardInterrupt:
            return
        if not path:
            return
        try:
            ext = Path(path).suffix.lower()
            if ext in (".jpg", ".jpeg"):
                image.save(path, "JPEG", quality=95, dpi=(DPI, DPI), subsampling=0)
            else:
                image.save(path, "PNG", dpi=(DPI, DPI))
            messagebox.showinfo(
                "Arquivo criado",
                f"Foto exportada com {width_cm:g} × {height_cm:g} cm em 300 DPI."
            )
        except Exception as exc:
            messagebox.showerror("Erro", str(exc))

    def action_button(self, parent, text, command, secondary=False):
        bg = "#EFEAE0" if secondary else ACCENT
        fg = TEXT if secondary else "white"
        return tk.Button(
            parent, text=text, command=command,
            bg=bg, fg=fg, activebackground="#B77A2E",
            activeforeground="white", relief="flat",
            padx=16, pady=9, font=("Segoe UI", 10, "bold"),
            cursor="hand2"
        )

    def select_multiple(self):
        try:
            paths = filedialog.askopenfilenames(
                title="Escolha até 9 fotos",
                filetypes=[("Imagens", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff")]
            )
        except KeyboardInterrupt:
            return
        for slot, path in zip(self.slots, paths[:9]):
            slot.path = path
            slot.rotation = 0
            slot.zoom = 1.0
            slot.refresh()
        self.refresh_preview()

    def build_sheet(self):
        sheet = Image.new("RGB", (A4_W, A4_H), "white")
        draw = ImageDraw.Draw(sheet)

        loaded = [slot for slot in self.slots if slot.path]
        if not loaded:
            for i in range(COLS * ROWS):
                row, col = divmod(i, COLS)
                x = MARGIN_X + col * (PHOTO_W + GAP_X)
                y = MARGIN_Y + row * (PHOTO_H + GAP_Y)
                draw.rectangle((x, y, x+PHOTO_W, y+PHOTO_H), outline=(185,185,185), width=3)
                draw.line((x, y, x+PHOTO_W, y+PHOTO_H), fill=(220,220,220), width=2)
                draw.line((x+PHOTO_W, y, x, y+PHOTO_H), fill=(220,220,220), width=2)
            return sheet

        total = COLS * ROWS
        if self.montage_duplicate_var.get():
            filled = []
            while len(filled) < total:
                filled.extend(loaded)
            filled = filled[:total]
        else:
            filled = loaded[:total] + [None] * (total - len(loaded))

        for i, slot in enumerate(filled):
            row, col = divmod(i, COLS)
            x = MARGIN_X + col * (PHOTO_W + GAP_X)
            y = MARGIN_Y + row * (PHOTO_H + GAP_Y)

            if slot is None:
                continue
            img = slot.get_image()
            if img:
                img = cover_fit(img, PHOTO_W, PHOTO_H, slot.zoom, offset_y=slot.offset_y)
                sheet.paste(img, (x, y))
                if self.border_var.get():
                    draw.rectangle(
                        (x, y, x + PHOTO_W - 1, y + PHOTO_H - 1),
                        outline=(0, 0, 0), width=2
                    )

        return sheet

    def refresh_preview(self):
        if self.verso_var.get():
            sheet = self.build_verso_sheet()
            label = "Verso"
        else:
            sheet = self.build_sheet()
            label = "Frente"
        preview = sheet.copy()
        cw = max(100, self.canvas.winfo_width())
        ch = max(100, self.canvas.winfo_height())
        preview.thumbnail((max(1, cw - 10), max(1, ch - 10)), Image.Resampling.LANCZOS)
        self.preview_img = ImageTk.PhotoImage(preview)

        self.canvas.delete("all")
        self.canvas.create_rectangle(5, 5, cw - 5, ch - 5, fill="#D3CEC4", outline="")
        x = (cw - preview.width) // 2
        y = (ch - preview.height) // 2
        self.canvas.create_image(x, y, anchor="nw", image=self.preview_img)

        count = sum(1 for s in self.slots if s.path)
        total = COLS * ROWS
        if count > 0:
            dup = -(-total // count)
            self.status_var.set(f"{count} foto(s) única(s) • duplicadas ×{dup} = {total} na folha • {label}")
        else:
            self.status_var.set(f"0 de {total} fotos adicionadas • {label}")

    def rotate_slot(self, degrees):
        try:
            idx = self.rotate_slot_var.get() - 1
        except (tk.TclError, AttributeError):
            return
        if 0 <= idx < len(self.slots) and self.slots[idx].path:
            self.slots[idx].rotation = (self.slots[idx].rotation + degrees) % 360
            self.slots[idx].refresh()
            self.refresh_preview()

    def clear_all(self):
        for slot in self.slots:
            slot.path = None
            slot.rotation = 0
            slot.zoom = 1.0
            slot.tk_img = None
            slot.preview.configure(image="", text="Clique para adicionar")
            slot.zoom_label.configure(text="100%")
        self.verso_var.set(False)
        self.refresh_preview()

    def get_empty_slot(self):
        for slot in self.slots:
            if not slot.path:
                return slot
        return None

    def export_sheet(self):
        if not any(s.path for s in self.slots):
            messagebox.showwarning("Sem fotos", "Adicione pelo menos uma foto antes de exportar.")
            return

        try:
            path = filedialog.asksaveasfilename(
                title="Salvar folha A4",
                defaultextension=".jpg",
                initialfile=f"folha_A4_9_fotos_6x9_{save_timestamp()}",
                filetypes=[("JPEG", "*.jpg"), ("PNG", "*.png")]
            )
        except KeyboardInterrupt:
            return
        if not path:
            return

        try:
            sheet = self.build_sheet()
            ext = Path(path).suffix.lower()
            if ext in (".jpg", ".jpeg"):
                sheet.save(path, "JPEG", quality=95, dpi=(DPI, DPI), subsampling=0)
            else:
                sheet.save(path, "PNG", dpi=(DPI, DPI))

            messagebox.showinfo(
                "Arquivo criado",
                "A folha A4 foi exportada com sucesso.\n\n"
                "9 fotos de 6 × 9 cm\n300 DPI"
            )
        except Exception as exc:
            messagebox.showerror("Erro", str(exc))

    def build_verso_sheet(self):
        sheet = self.build_sheet()
        verso = ImageOps.flip(sheet)
        try:
            offset_mm = float(self.verso_offset_y.get().replace(",", "."))
        except ValueError:
            offset_mm = 0
        offset_px = int(round(offset_mm / 25.4 * DPI))
        if offset_px == 0:
            return verso
        result = Image.new("RGB", (A4_W, A4_H), "white")
        result.paste(verso, (0, offset_px))
        return result

    def export_verso(self):
        if not any(s.path for s in self.slots):
            messagebox.showwarning("Sem fotos", "Adicione pelo menos uma foto antes de exportar.")
            return
        try:
            path = filedialog.asksaveasfilename(
                title="Salvar verso A4",
                defaultextension=".jpg",
                initialfile=f"verso_A4_9_fotos_6x9_{save_timestamp()}",
                filetypes=[("JPEG", "*.jpg"), ("PNG", "*.png")]
            )
        except KeyboardInterrupt:
            return
        if not path:
            return
        try:
            verso = self.build_verso_sheet()
            ext = Path(path).suffix.lower()
            if ext in (".jpg", ".jpeg"):
                verso.save(path, "JPEG", quality=95, dpi=(DPI, DPI), subsampling=0)
            else:
                verso.save(path, "PNG", dpi=(DPI, DPI))
            messagebox.showinfo(
                "Arquivo criado",
                "A página de verso foi exportada com sucesso.\n\n"
                "Espelhamento vertical • 300 DPI"
            )
        except Exception as exc:
            messagebox.showerror("Erro", str(exc))

    def print_verso(self):
        if not any(s.path for s in self.slots):
            messagebox.showwarning("Sem fotos", "Adicione pelo menos uma foto antes de imprimir.")
            return
        self.print_image(self.build_verso_sheet(), "Verso A4 - 9 Fotos 6x9 cm")


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
