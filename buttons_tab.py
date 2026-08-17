import os
import json
import math
import threading
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Optional, Tuple, Dict

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageOps, ImageTk

DPI = 300
CACHE_PREVIEW_WIDTH = 780
MANUAL_PREVIEW_SIZE = 420
THUMB_SIZE = 64

DEFAULT_BUTTON_SIZES_MM = {
    "25 mm / 2,5 cm": 25.0,
    "32 mm / 3,2 cm": 32.0,
    "58 mm / 5,8 cm": 58.0,
}

DEFAULT_CONFIG = {
    "photo_bleed_mm": 1.0,
    "page_margin_mm": 10.0,
    "outer_ring_extra_mm": 10.0,
    "border_width_px": 3,
    "border_color": "black",
    "button_sizes_mm": DEFAULT_BUTTON_SIZES_MM,
    "selected_button_label": "32 mm / 3,2 cm",
    "export_format": "JPG",
}

A4_WIDTH_MM = 210
A4_HEIGHT_MM = 297
SAVE_DATE_FORMAT = "%d-%m-%Y %H-%M-%S"


def save_timestamp():
    return datetime.now().strftime(SAVE_DATE_FORMAT)


def mm_to_px(mm, dpi=DPI):
    return max(1, int(round((mm / 25.4) * dpi)))


def a4_size_px():
    return mm_to_px(A4_WIDTH_MM), mm_to_px(A4_HEIGHT_MM)


def clamp(value, min_value, max_value):
    return max(min_value, min(value, max_value))


def ensure_rgb(img_rgba):
    rgb = Image.new("RGB", img_rgba.size, (255, 255, 255))
    if img_rgba.mode == "RGBA":
        rgb.paste(img_rgba, mask=img_rgba.split()[-1])
    else:
        rgb.paste(img_rgba)
    return rgb


def make_button_label(mm_value):
    cm = mm_value / 10.0
    cm_str = f"{cm:.1f}".replace(".", ",")
    mm_txt = str(int(mm_value)) if float(mm_value).is_integer() else str(mm_value).replace(".", ",")
    return f"{mm_txt} mm / {cm_str} cm"


def safe_float(value, default):
    try:
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def safe_int(value, default):
    try:
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


@dataclass
class ImageItem:
    path: str
    zoom: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    enabled: bool = True


def build_center_square(img):
    img = ImageOps.exif_transpose(img).convert("RGB")
    crop_size = min(img.width, img.height)
    left = (img.width - crop_size) // 2
    top = (img.height - crop_size) // 2
    return img.crop((left, top, left + crop_size, top + crop_size))


def apply_manual_transform(square_img, target_px, zoom, offset_x, offset_y):
    zoom = max(1.0, float(zoom))
    offset_x = float(offset_x)
    offset_y = float(offset_y)
    src = square_img.convert("RGB")
    base_size = src.size[0]
    scaled_size = max(target_px, int(round(base_size * zoom)))
    scaled = src.resize((scaled_size, scaled_size), Image.LANCZOS)
    max_shift = max(0, (scaled_size - target_px) // 2)
    shift_x = int(round(offset_x * max_shift))
    shift_y = int(round(offset_y * max_shift))
    center_x = scaled_size // 2 + shift_x
    center_y = scaled_size // 2 + shift_y
    half = target_px // 2
    left = clamp(center_x - half, 0, scaled_size - target_px)
    top = clamp(center_y - half, 0, scaled_size - target_px)
    return scaled.crop((left, top, left + target_px, top + target_px))


def make_button_piece(photo_square, inner_diameter_px, outer_diameter_px, border_width_px, border_color):
    piece = Image.new("RGBA", (outer_diameter_px, outer_diameter_px), (255, 255, 255, 0))
    center = outer_diameter_px // 2
    outer_mask = Image.new("L", (outer_diameter_px, outer_diameter_px), 0)
    ImageDraw.Draw(outer_mask).ellipse((0, 0, outer_diameter_px - 1, outer_diameter_px - 1), fill=255)
    white_circle = Image.new("RGBA", (outer_diameter_px, outer_diameter_px), (255, 255, 255, 255))
    piece.paste(white_circle, (0, 0), outer_mask)
    inner_mask = Image.new("L", (inner_diameter_px, inner_diameter_px), 0)
    ImageDraw.Draw(inner_mask).ellipse((0, 0, inner_diameter_px - 1, inner_diameter_px - 1), fill=255)
    photo_rgba = photo_square.convert("RGBA")
    photo_circular = Image.new("RGBA", (inner_diameter_px, inner_diameter_px), (255, 255, 255, 0))
    photo_circular.paste(photo_rgba, (0, 0), inner_mask)
    paste_x = center - (inner_diameter_px // 2)
    paste_y = center - (inner_diameter_px // 2)
    piece.alpha_composite(photo_circular, (paste_x, paste_y))
    half = max(1, border_width_px // 2)
    ImageDraw.Draw(piece).ellipse(
        (half, half, outer_diameter_px - half - 1, outer_diameter_px - half - 1),
        outline=border_color, width=border_width_px,
    )
    return piece


class ScrollablePreview(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg="#cfcfcf", highlightthickness=0)
        self.v_scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.h_scroll = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.inner = ttk.Frame(self.canvas)
        self.inner_window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.v_scroll.set, xscrollcommand=self.h_scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.v_scroll.grid(row=0, column=1, sticky="ns")
        self.h_scroll.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.inner.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_frame_configure(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event=None):
        self.canvas.itemconfigure(self.inner_window, width=max(event.width, 200))

    def _on_mousewheel(self, event):
        try:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass


class ButtonsTab:
    def __init__(self, parent_frame, root):
        self.parent = parent_frame
        self.root = root

        self.config_data = json.loads(json.dumps(DEFAULT_CONFIG))
        self.items: List[ImageItem] = []
        self.project_path: Optional[str] = None

        self.preview_pages: List[Image.Image] = []
        self.preview_tk_refs: List[ImageTk.PhotoImage] = []
        self.thumb_refs: Dict[str, ImageTk.PhotoImage] = {}
        self.preview_cache = None
        self.export_thread_running = False

        self.button_size_var = tk.StringVar(value=self.config_data["selected_button_label"])
        self.export_format_var = tk.StringVar(value=self.config_data["export_format"])
        self.outer_ring_extra_var = tk.StringVar(value=str(self.config_data["outer_ring_extra_mm"]))
        self.border_width_var = tk.StringVar(value=str(self.config_data["border_width_px"]))
        self.new_size_var = tk.StringVar()

        self.zoom_var = tk.DoubleVar(value=1.0)
        self.offset_x_var = tk.DoubleVar(value=0.0)
        self.offset_y_var = tk.DoubleVar(value=0.0)

        self.project_status_var = tk.StringVar(value="Sem projeto aberto.")
        self.preview_status_var = tk.StringVar(value="Nenhuma prévia gerada.")
        self.export_status_var = tk.StringVar(value="Pronto para exportar.")

        self.manual_selected_index: Optional[int] = None
        self.manual_preview_photo = None

        self.build_ui()
        self.refresh_controls()

    def build_ui(self):
        BG = "#F4F1EA"
        PANEL = "#FFFFFF"
        BORDER = "#D9D3C7"
        TEXT = "#222222"
        MUTED = "#6E6E6E"

        header = tk.Frame(self.parent, bg=PANEL, height=56, highlightbackground=BORDER, highlightthickness=1)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="Gerador de Buttons A4", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 20, "bold")).pack(anchor="w", padx=24, pady=(8, 0))
        tk.Label(header, textvariable=self.project_status_var, bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 10)).pack(anchor="w", padx=24)

        self.notebook = ttk.Notebook(self.parent)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        self.tab_config = ttk.Frame(self.notebook)
        self.tab_preview = ttk.Frame(self.notebook)
        self.tab_manual = ttk.Frame(self.notebook)
        self.tab_sizes = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_config, text="Projeto e Configuração")
        self.notebook.add(self.tab_preview, text="Pré-visualização")
        self.notebook.add(self.tab_manual, text="Ajuste manual")
        self.notebook.add(self.tab_sizes, text="Tamanhos")

        self.build_config_tab()
        self.build_preview_tab()
        self.build_manual_tab()
        self.build_sizes_tab()

    def build_config_tab(self):
        top = ttk.Frame(self.tab_config)
        top.pack(fill="x", pady=(0, 12))

        for text, cmd in [
            ("Novo projeto", self.new_project),
            ("Abrir projeto", self.load_project),
            ("Salvar projeto", self.save_project),
            ("Salvar como", self.save_project_as),
            ("Adicionar imagens", self.select_images),
            ("Gerar prévia", self.generate_preview_async),
        ]:
            ttk.Button(top, text=text, command=cmd).pack(side="left", padx=(0, 8))

        options = ttk.LabelFrame(self.tab_config, text="Opções principais", padding=10)
        options.pack(fill="x", pady=(0, 10))

        ttk.Label(options, text="Tamanho do button").grid(row=0, column=0, sticky="w")
        self.size_combo = ttk.Combobox(options, textvariable=self.button_size_var, state="readonly", width=26)
        self.size_combo.grid(row=1, column=0, sticky="w", padx=(0, 15), pady=(2, 8))
        self.size_combo.bind("<<ComboboxSelected>>", lambda e: self.clear_preview_cache())

        ttk.Label(options, text="Formato padrão de exportação").grid(row=0, column=1, sticky="w")
        self.export_combo = ttk.Combobox(options, textvariable=self.export_format_var, state="readonly", width=18, values=["PDF", "PNG", "JPG"])
        self.export_combo.grid(row=1, column=1, sticky="w", pady=(2, 8))

        list_frame = ttk.LabelFrame(self.tab_config, text="Imagens do projeto", padding=10)
        list_frame.pack(fill="both", expand=True)

        toolbar = ttk.Frame(list_frame)
        toolbar.pack(fill="x", pady=(0, 8))
        for text, cmd in [
            ("Remover", self.remove_selected_items),
            ("Duplicar", self.duplicate_selected_item),
            ("Mover ↑", self.move_selected_up),
            ("Mover ↓", self.move_selected_down),
            ("Habilitar/Desabilitar", self.toggle_selected_enabled),
            ("Resetar todos ajustes", self.reset_all_adjustments),
        ]:
            ttk.Button(toolbar, text=text, command=cmd).pack(side="left", padx=(0, 8))

        columns = ("idx", "name", "status")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="tree headings", height=18)
        self.tree.heading("#0", text="Thumb")
        self.tree.heading("idx", text="#")
        self.tree.heading("name", text="Arquivo")
        self.tree.heading("status", text="Estado")
        self.tree.column("#0", width=82, stretch=False)
        self.tree.column("idx", width=40, anchor="center", stretch=False)
        self.tree.column("name", width=620)
        self.tree.column("status", width=100, anchor="center", stretch=False)
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Double-1>", lambda e: self.notebook.select(self.tab_manual))

        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

    def build_preview_tab(self):
        top = ttk.Frame(self.tab_preview)
        top.pack(fill="x", pady=(0, 10))
        ttk.Button(top, text="Atualizar prévia", command=self.generate_preview_async).pack(side="left")
        ttk.Button(top, text="Exportar", command=self.export_current_format).pack(side="left", padx=8)
        ttk.Label(top, textvariable=self.preview_status_var).pack(side="left", padx=12)

        self.preview_progress = ttk.Progressbar(top, mode="determinate", length=240)
        self.preview_progress.pack(side="right")

        self.preview_area = ScrollablePreview(self.tab_preview)
        self.preview_area.pack(fill="both", expand=True)

    def build_manual_tab(self):
        wrapper = ttk.Frame(self.tab_manual)
        wrapper.pack(fill="both", expand=True)

        left = ttk.LabelFrame(wrapper, text="Imagens", padding=10)
        left.pack(side="left", fill="y", padx=(0, 10))
        self.manual_list = tk.Listbox(left, width=42, height=30)
        self.manual_list.pack(fill="y", expand=True)
        self.manual_list.bind("<<ListboxSelect>>", self.on_manual_select)

        right = ttk.Frame(wrapper)
        right.pack(side="left", fill="both", expand=True)

        adjust = ttk.LabelFrame(right, text="Ajuste manual", padding=10)
        adjust.pack(fill="x", pady=(0, 10))
        self.manual_status = ttk.Label(adjust, text="Selecione uma imagem para ajustar.")
        self.manual_status.pack(anchor="w", pady=(0, 8))

        controls = ttk.Frame(adjust)
        controls.pack(fill="x")

        ttk.Label(controls, text="Zoom").grid(row=0, column=0, sticky="w")
        ttk.Scale(controls, from_=1.0, to=2.5, variable=self.zoom_var, orient="horizontal", command=self.on_manual_slider_change).grid(row=0, column=1, sticky="ew", padx=8)
        self.zoom_lbl = ttk.Label(controls, text="1.00")
        self.zoom_lbl.grid(row=0, column=2)

        ttk.Label(controls, text="Offset X").grid(row=1, column=0, sticky="w")
        ttk.Scale(controls, from_=-1.0, to=1.0, variable=self.offset_x_var, orient="horizontal", command=self.on_manual_slider_change).grid(row=1, column=1, sticky="ew", padx=8)
        self.offx_lbl = ttk.Label(controls, text="0.00")
        self.offx_lbl.grid(row=1, column=2)

        ttk.Label(controls, text="Offset Y").grid(row=2, column=0, sticky="w")
        ttk.Scale(controls, from_=-1.0, to=1.0, variable=self.offset_y_var, orient="horizontal", command=self.on_manual_slider_change).grid(row=2, column=1, sticky="ew", padx=8)
        self.offy_lbl = ttk.Label(controls, text="0.00")
        self.offy_lbl.grid(row=2, column=2)

        controls.columnconfigure(1, weight=1)

        buttons = ttk.Frame(adjust)
        buttons.pack(fill="x", pady=(10, 0))
        for text, cmd in [
            ("Resetar ajuste", self.reset_manual_adjustment),
            ("Aplicar para selecionadas", self.apply_current_adjust_to_selected),
            ("Gerar prévia", self.generate_preview_async),
        ]:
            ttk.Button(buttons, text=text, command=cmd).pack(side="left", padx=(0, 8))

        preview = ttk.LabelFrame(right, text="Preview da imagem", padding=10)
        preview.pack(fill="both", expand=True)
        self.manual_canvas = tk.Canvas(preview, width=MANUAL_PREVIEW_SIZE, height=MANUAL_PREVIEW_SIZE, bg="#efefef", highlightthickness=1, highlightbackground="#999")
        self.manual_canvas.pack(anchor="center", expand=True)

    def build_sizes_tab(self):
        main = ttk.Frame(self.tab_sizes)
        main.pack(fill="both", expand=True)

        left = ttk.LabelFrame(main, text="Tamanhos cadastrados", padding=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        self.size_list = tk.Listbox(left, height=18)
        self.size_list.pack(fill="both", expand=True)

        right = ttk.LabelFrame(main, text="Editar", padding=10)
        right.pack(side="left", fill="y")
        ttk.Label(right, text="Novo tamanho em mm").pack(anchor="w")
        ttk.Entry(right, textvariable=self.new_size_var, width=20).pack(anchor="w", pady=(2, 8))
        ttk.Button(right, text="Adicionar tamanho", command=self.add_size).pack(anchor="w", pady=(0, 12))
        ttk.Button(right, text="Remover tamanho selecionado", command=self.remove_selected_size).pack(anchor="w", pady=(0, 16))
        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(right, text="Borda branca extra (mm)").pack(anchor="w")
        ttk.Entry(right, textvariable=self.outer_ring_extra_var, width=20).pack(anchor="w", pady=(2, 8))
        ttk.Label(right, text="Espessura da borda preta (px)").pack(anchor="w")
        ttk.Entry(right, textvariable=self.border_width_var, width=20).pack(anchor="w", pady=(2, 8))
        ttk.Button(right, text="Aplicar alterações", command=self.apply_size_settings).pack(anchor="w", pady=(8, 0))

    # --- STATE ---

    def refresh_controls(self):
        self.size_combo["values"] = list(self.config_data["button_sizes_mm"].keys())
        if self.button_size_var.get() not in self.config_data["button_sizes_mm"]:
            self.button_size_var.set(next(iter(self.config_data["button_sizes_mm"])))
        self.size_list.delete(0, "end")
        for lbl, mm in self.config_data["button_sizes_mm"].items():
            self.size_list.insert("end", f"{lbl}  ->  {mm} mm")
        self.rebuild_tree()
        self.rebuild_manual_list()
        self.update_project_status()

    def update_project_status(self):
        name = os.path.basename(self.project_path) if self.project_path else "Projeto sem nome"
        self.project_status_var.set(f"{name} | {len(self.items)} imagem(ns)")

    def clear_preview_cache(self):
        self.preview_cache = None

    # --- PROJECT ---

    def new_project(self):
        self.project_path = None
        self.items = []
        self.thumb_refs.clear()
        self.preview_cache = None
        self.preview_pages = []
        self.config_data = json.loads(json.dumps(DEFAULT_CONFIG))
        self.button_size_var.set(self.config_data["selected_button_label"])
        self.export_format_var.set(self.config_data["export_format"])
        self.outer_ring_extra_var.set(str(self.config_data["outer_ring_extra_mm"]))
        self.border_width_var.set(str(self.config_data["border_width_px"]))
        self.manual_selected_index = None
        self.refresh_controls()
        self.preview_status_var.set("Projeto novo criado.")

    def save_project(self):
        if not self.project_path:
            return self.save_project_as()
        self._save_to_path(self.project_path)

    def save_project_as(self):
        try:
            path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("Projeto JSON", "*.json")], title="Salvar projeto",
                                                initialfile=f"projeto_buttons_{save_timestamp()}")
        except KeyboardInterrupt:
            return
        if not path:
            return
        self._save_to_path(path)

    def _save_to_path(self, path):
        self.config_data["selected_button_label"] = self.button_size_var.get()
        self.config_data["export_format"] = self.export_format_var.get()
        self.config_data["outer_ring_extra_mm"] = safe_float(self.outer_ring_extra_var.get(), 10.0)
        self.config_data["border_width_px"] = safe_int(self.border_width_var.get(), 3)
        payload = {
            "version": 1,
            "config": self.config_data,
            "items": [asdict(item) for item in self.items],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        self.project_path = path
        self.update_project_status()
        self.preview_status_var.set(f"Projeto salvo em {os.path.basename(path)}")

    def load_project(self):
        try:
            path = filedialog.askopenfilename(filetypes=[("Projeto JSON", "*.json")], title="Abrir projeto")
        except KeyboardInterrupt:
            return
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        self.project_path = path
        self.config_data = payload.get("config", json.loads(json.dumps(DEFAULT_CONFIG)))
        self.items = [ImageItem(**item) for item in payload.get("items", []) if os.path.exists(item.get("path", ""))]
        self.button_size_var.set(self.config_data.get("selected_button_label", next(iter(self.config_data["button_sizes_mm"]))))
        self.export_format_var.set(self.config_data.get("export_format", "PDF"))
        self.outer_ring_extra_var.set(str(self.config_data.get("outer_ring_extra_mm", 10.0)))
        self.border_width_var.set(str(self.config_data.get("border_width_px", 3)))
        self.thumb_refs.clear()
        self.preview_cache = None
        self.preview_pages = []
        self.refresh_controls()
        self.preview_status_var.set(f"Projeto carregado: {os.path.basename(path)}")

    # --- ITEMS ---

    def select_images(self):
        try:
            files = filedialog.askopenfilenames(
                title="Selecione as imagens",
                filetypes=[("Imagens", "*.jpg *.jpeg *.png *.bmp *.webp"), ("Todos os arquivos", "*.*")]
            )
        except KeyboardInterrupt:
            return
        if not files:
            return
        added = 0
        for path in files:
            if all(item.path != path for item in self.items):
                self.items.append(ImageItem(path=path))
                added += 1
        self.refresh_controls()
        self.clear_preview_cache()
        if added:
            self.preview_status_var.set(f"{added} imagem(ns) adicionada(s).")

    def get_selected_indices_from_tree(self):
        out = []
        for iid in self.tree.selection():
            try:
                out.append(int(iid))
            except Exception:
                pass
        return sorted(set(out))

    def remove_selected_items(self):
        idxs = self.get_selected_indices_from_tree()
        if not idxs:
            return
        for idx in reversed(idxs):
            if 0 <= idx < len(self.items):
                del self.items[idx]
        self.refresh_controls()
        self.clear_preview_cache()

    def duplicate_selected_item(self):
        idxs = self.get_selected_indices_from_tree()
        if not idxs:
            return
        idx = idxs[0]
        item = self.items[idx]
        self.items.insert(idx + 1, ImageItem(**asdict(item)))
        self.refresh_controls()
        self.clear_preview_cache()

    def move_selected_up(self):
        idxs = self.get_selected_indices_from_tree()
        if len(idxs) != 1 or idxs[0] == 0:
            return
        idx = idxs[0]
        self.items[idx - 1], self.items[idx] = self.items[idx], self.items[idx - 1]
        self.refresh_controls()
        self.tree.selection_set(str(idx - 1))
        self.clear_preview_cache()

    def move_selected_down(self):
        idxs = self.get_selected_indices_from_tree()
        if len(idxs) != 1 or idxs[0] >= len(self.items) - 1:
            return
        idx = idxs[0]
        self.items[idx + 1], self.items[idx] = self.items[idx], self.items[idx + 1]
        self.refresh_controls()
        self.tree.selection_set(str(idx + 1))
        self.clear_preview_cache()

    def toggle_selected_enabled(self):
        idxs = self.get_selected_indices_from_tree()
        if not idxs:
            return
        for idx in idxs:
            self.items[idx].enabled = not self.items[idx].enabled
        self.refresh_controls()
        self.clear_preview_cache()

    def reset_all_adjustments(self):
        for item in self.items:
            item.zoom = 1.0
            item.offset_x = 0.0
            item.offset_y = 0.0
        self.clear_preview_cache()
        self.refresh_controls()
        self.update_manual_preview()

    # --- TREE / LIST / THUMBS ---

    def get_thumbnail(self, path):
        if path in self.thumb_refs:
            return self.thumb_refs[path]
        try:
            img = Image.open(path)
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
        except Exception:
            img = Image.new("RGB", (THUMB_SIZE, THUMB_SIZE), "#dddddd")
            draw = ImageDraw.Draw(img)
            draw.line((0, 0, THUMB_SIZE, THUMB_SIZE), fill="red", width=3)
            draw.line((THUMB_SIZE, 0, 0, THUMB_SIZE), fill="red", width=3)
        tk_img = ImageTk.PhotoImage(img)
        self.thumb_refs[path] = tk_img
        return tk_img

    def rebuild_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.thumb_refs = {}
        for idx, item in enumerate(self.items):
            thumb = self.get_thumbnail(item.path)
            state = "ok" if item.enabled else "off"
            self.tree.insert("", "end", iid=str(idx), text="", image=thumb, values=(idx + 1, os.path.basename(item.path), state))

    def rebuild_manual_list(self):
        self.manual_list.delete(0, "end")
        for i, item in enumerate(self.items, start=1):
            suffix = " [off]" if not item.enabled else ""
            self.manual_list.insert("end", f"{i:02d} - {os.path.basename(item.path)}{suffix}")

    def on_tree_select(self, _event=None):
        idxs = self.get_selected_indices_from_tree()
        if len(idxs) == 1:
            idx = idxs[0]
            self.manual_selected_index = idx
            self.manual_list.selection_clear(0, "end")
            self.manual_list.selection_set(idx)
            self.manual_list.see(idx)
            self.load_manual_item(idx)

    def on_manual_select(self, _event=None):
        sel = self.manual_list.curselection()
        if not sel:
            return
        idx = sel[0]
        self.manual_selected_index = idx
        self.tree.selection_set(str(idx))
        self.tree.see(str(idx))
        self.load_manual_item(idx)

    def load_manual_item(self, idx):
        if not (0 <= idx < len(self.items)):
            return
        item = self.items[idx]
        self.zoom_var.set(item.zoom)
        self.offset_x_var.set(item.offset_x)
        self.offset_y_var.set(item.offset_y)
        self.update_manual_labels()
        self.update_manual_preview()

    # --- MANUAL ---

    def update_manual_labels(self):
        self.zoom_lbl.config(text=f"{self.zoom_var.get():.2f}")
        self.offx_lbl.config(text=f"{self.offset_x_var.get():.2f}")
        self.offy_lbl.config(text=f"{self.offset_y_var.get():.2f}")

    def on_manual_slider_change(self, _event=None):
        self.update_manual_labels()
        if self.manual_selected_index is None or not (0 <= self.manual_selected_index < len(self.items)):
            return
        item = self.items[self.manual_selected_index]
        item.zoom = float(self.zoom_var.get())
        item.offset_x = float(self.offset_x_var.get())
        item.offset_y = float(self.offset_y_var.get())
        self.clear_preview_cache()
        self.update_manual_preview()

    def reset_manual_adjustment(self):
        if self.manual_selected_index is None:
            return
        item = self.items[self.manual_selected_index]
        item.zoom = 1.0
        item.offset_x = 0.0
        item.offset_y = 0.0
        self.load_manual_item(self.manual_selected_index)
        self.clear_preview_cache()

    def apply_current_adjust_to_selected(self):
        idxs = self.get_selected_indices_from_tree()
        if not idxs or self.manual_selected_index is None:
            return
        source = self.items[self.manual_selected_index]
        for idx in idxs:
            self.items[idx].zoom = source.zoom
            self.items[idx].offset_x = source.offset_x
            self.items[idx].offset_y = source.offset_y
        self.clear_preview_cache()
        self.refresh_controls()
        self.manual_list.selection_set(self.manual_selected_index)

    def update_manual_preview(self):
        self.manual_canvas.delete("all")
        if self.manual_selected_index is None or not (0 <= self.manual_selected_index < len(self.items)):
            self.manual_status.config(text="Selecione uma imagem para ajustar.")
            return
        try:
            item = self.items[self.manual_selected_index]
            inner_px = mm_to_px(self.config_data["button_sizes_mm"][self.button_size_var.get()] + self.config_data["photo_bleed_mm"])
            outer_px = mm_to_px(self.config_data["button_sizes_mm"][self.button_size_var.get()] + self.config_data["photo_bleed_mm"] + safe_float(self.outer_ring_extra_var.get(), self.config_data["outer_ring_extra_mm"]))
            img = Image.open(item.path)
            base_square = build_center_square(img)
            square = apply_manual_transform(base_square, inner_px, item.zoom, item.offset_x, item.offset_y)
            piece = make_button_piece(
                square, inner_px, outer_px,
                safe_int(self.border_width_var.get(), self.config_data["border_width_px"]),
                self.config_data["border_color"]
            )
            preview = piece.copy()
            preview.thumbnail((MANUAL_PREVIEW_SIZE, MANUAL_PREVIEW_SIZE), Image.LANCZOS)
            self.manual_preview_photo = ImageTk.PhotoImage(preview)
            x = (MANUAL_PREVIEW_SIZE - preview.width) // 2
            y = (MANUAL_PREVIEW_SIZE - preview.height) // 2
            self.manual_canvas.create_image(x, y, anchor="nw", image=self.manual_preview_photo)
            self.manual_status.config(text=os.path.basename(item.path))
        except Exception as e:
            self.manual_status.config(text=f"Erro no preview manual: {e}")

    # --- PREVIEW ---

    def get_layout_metrics(self):
        page_w_px, page_h_px = a4_size_px()
        button_mm = self.config_data["button_sizes_mm"][self.button_size_var.get()]
        inner_mm = button_mm + self.config_data["photo_bleed_mm"]
        outer_mm = inner_mm + safe_float(self.outer_ring_extra_var.get(), self.config_data["outer_ring_extra_mm"])
        inner_px = mm_to_px(inner_mm)
        outer_px = mm_to_px(outer_mm)
        margin_px = mm_to_px(self.config_data["page_margin_mm"])
        usable_w = page_w_px - margin_px * 2
        usable_h = page_h_px - margin_px * 2
        cols = max(1, usable_w // outer_px)
        rows = max(1, usable_h // outer_px)
        positions = []
        for row in range(rows):
            for col in range(cols):
                positions.append((margin_px + col * outer_px, margin_px + row * outer_px))
        return {
            "page_w_px": page_w_px, "page_h_px": page_h_px,
            "inner_px": inner_px, "outer_px": outer_px,
            "per_page": cols * rows, "positions": positions,
        }

    def build_pages(self, progress_callback=None):
        if self.preview_cache is not None:
            return self.preview_cache
        enabled_items = [item for item in self.items if item.enabled]
        if not enabled_items:
            raise ValueError("Nenhuma imagem habilitada para processar.")
        metrics = self.get_layout_metrics()
        inner_px = metrics["inner_px"]
        outer_px = metrics["outer_px"]
        page_w_px = metrics["page_w_px"]
        page_h_px = metrics["page_h_px"]
        per_page = metrics["per_page"]
        positions = metrics["positions"]
        border_px = safe_int(self.border_width_var.get(), self.config_data["border_width_px"])
        pages = []
        total = len(enabled_items)
        total_pages = math.ceil(total / per_page)
        processed = 0
        for page_idx in range(total_pages):
            page = Image.new("RGBA", (page_w_px, page_h_px), (255, 255, 255, 255))
            batch = enabled_items[page_idx * per_page:(page_idx + 1) * per_page]
            for i, item in enumerate(batch):
                img = Image.open(item.path)
                base_square = build_center_square(img)
                square = apply_manual_transform(base_square, inner_px, item.zoom, item.offset_x, item.offset_y)
                piece = make_button_piece(square, inner_px, outer_px, border_px, self.config_data["border_color"])
                page.alpha_composite(piece, positions[i])
                processed += 1
                if progress_callback:
                    progress_callback(processed, total)
            pages.append(page)
        self.preview_cache = pages
        return pages

    def generate_preview_async(self):
        if self.export_thread_running:
            return
        self.preview_progress["value"] = 0
        self.preview_status_var.set("Gerando prévia...")

        def worker():
            try:
                def progress(done, total):
                    self.root.after(0, lambda: self._update_preview_progress(done, total))
                pages = self.build_pages(progress_callback=progress)
                self.root.after(0, lambda: self._on_preview_ready(pages))
            except Exception as e:
                self.root.after(0, lambda: self._on_preview_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _update_preview_progress(self, done, total):
        self.preview_progress["maximum"] = max(total, 1)
        self.preview_progress["value"] = done
        self.preview_status_var.set(f"Gerando prévia... {done}/{total}")

    def _on_preview_ready(self, pages):
        self.preview_pages = pages
        self.render_preview_pages()
        self.preview_status_var.set(f"Prévia pronta: {len(pages)} página(s)")
        self.preview_progress["value"] = self.preview_progress["maximum"]
        self.notebook.select(self.tab_preview)

    def _on_preview_error(self, error):
        self.preview_status_var.set(f"Erro na prévia: {error}")
        messagebox.showerror("Erro", f"Erro ao gerar pré-visualização:\n{error}")

    def render_preview_pages(self):
        for widget in self.preview_area.inner.winfo_children():
            widget.destroy()
        self.preview_tk_refs = []
        for i, page in enumerate(self.preview_pages, start=1):
            frame = ttk.Frame(self.preview_area.inner, padding=(0, 0, 0, 20))
            frame.pack(fill="x", expand=True)
            ttk.Label(frame, text=f"Página {i}", font=("Arial", 11, "bold")).pack(anchor="center", pady=(0, 8))
            img = page.copy()
            scale = min(CACHE_PREVIEW_WIDTH / img.width, 1.0)
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
            tk_img = ImageTk.PhotoImage(img)
            self.preview_tk_refs.append(tk_img)
            canvas = tk.Canvas(frame, width=img.width, height=img.height, bg="white", highlightthickness=1, highlightbackground="#999")
            canvas.pack(anchor="center")
            canvas.create_image(0, 0, anchor="nw", image=tk_img)
        self.preview_area._on_frame_configure()

    # --- EXPORT ---

    def export_current_format(self):
        fmt = self.export_format_var.get().upper()
        if fmt == "PDF":
            self.export_pdf()
        elif fmt == "PNG":
            self.export_png()
        else:
            self.export_jpg()

    def export_pdf(self):
        try:
            path = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")], title="Salvar PDF",
                                                initialfile=f"buttons_{save_timestamp()}")
        except KeyboardInterrupt:
            return
        if not path:
            return
        self._export_async("PDF", path)

    def export_png(self):
        try:
            path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG", "*.png")], title="Salvar PNG",
                                                initialfile=f"buttons_{save_timestamp()}")
        except KeyboardInterrupt:
            return
        if not path:
            return
        self._export_async("PNG", path)

    def export_jpg(self):
        try:
            path = filedialog.asksaveasfilename(defaultextension=".jpg", filetypes=[("JPG", "*.jpg")], title="Salvar JPG",
                                                initialfile=f"buttons_{save_timestamp()}")
        except KeyboardInterrupt:
            return
        if not path:
            return
        self._export_async("JPG", path)

    def _export_async(self, fmt, output_path):
        if self.export_thread_running:
            return
        self.export_thread_running = True
        self.export_status_var.set(f"Exportando {fmt}...")
        self.preview_progress["value"] = 0
        if not self.preview_pages:
            try:
                self.preview_pages = self.build_pages(progress_callback=lambda d, t: self.root.after(0, lambda: self._update_preview_progress(d, t)))
            except Exception as e:
                self.export_thread_running = False
                messagebox.showerror("Erro", str(e))
                return

        def worker():
            try:
                if fmt == "PDF":
                    rgb_pages = [ensure_rgb(p) for p in self.preview_pages]
                    first, rest = rgb_pages[0], rgb_pages[1:]
                    if rest:
                        first.save(output_path, "PDF", resolution=DPI, save_all=True, append_images=rest)
                    else:
                        first.save(output_path, "PDF", resolution=DPI)
                else:
                    base, _ = os.path.splitext(output_path)
                    for i, page in enumerate(self.preview_pages, start=1):
                        out = f"{base}_{i:02d}.{fmt.lower()}"
                        if fmt == "PNG":
                            page.save(out, "PNG")
                        else:
                            ensure_rgb(page).save(out, "JPEG", quality=95)
                        self.root.after(0, lambda done=i, total=len(self.preview_pages): self._update_export_progress(done, total))
                self.root.after(0, lambda: self._on_export_ready(fmt, output_path))
            except Exception as e:
                self.root.after(0, lambda: self._on_export_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _update_export_progress(self, done, total):
        self.preview_progress["maximum"] = max(total, 1)
        self.preview_progress["value"] = done
        self.export_status_var.set(f"Exportando... {done}/{total}")

    def _on_export_ready(self, fmt, path):
        self.export_thread_running = False
        self.export_status_var.set(f"{fmt} exportado com sucesso.")
        messagebox.showinfo("Sucesso", f"{fmt} exportado com sucesso.\n{path}")

    def _on_export_error(self, error):
        self.export_thread_running = False
        self.export_status_var.set(f"Erro na exportação: {error}")
        messagebox.showerror("Erro", f"Erro ao exportar:\n{error}")

    # --- SIZE EDIT ---

    def add_size(self):
        raw = self.new_size_var.get().strip().replace(",", ".")
        if not raw:
            return
        mm = float(raw)
        if mm <= 0:
            messagebox.showwarning("Atenção", "O tamanho precisa ser maior que zero.")
            return
        label = make_button_label(mm)
        if label in self.config_data["button_sizes_mm"]:
            messagebox.showwarning("Atenção", "Esse tamanho já existe.")
            return
        self.config_data["button_sizes_mm"][label] = mm
        self.button_size_var.set(label)
        self.new_size_var.set("")
        self.clear_preview_cache()
        self.refresh_controls()

    def remove_selected_size(self):
        sel = self.size_list.curselection()
        if not sel:
            return
        if len(self.config_data["button_sizes_mm"]) <= 1:
            messagebox.showwarning("Atenção", "É preciso manter pelo menos um tamanho.")
            return
        label = list(self.config_data["button_sizes_mm"].keys())[sel[0]]
        del self.config_data["button_sizes_mm"][label]
        if self.button_size_var.get() == label:
            self.button_size_var.set(next(iter(self.config_data["button_sizes_mm"])))
        self.clear_preview_cache()
        self.refresh_controls()

    def apply_size_settings(self):
        self.config_data["outer_ring_extra_mm"] = max(0.0, safe_float(self.outer_ring_extra_var.get(), 10.0))
        self.config_data["border_width_px"] = max(1, safe_int(self.border_width_var.get(), 3))
        self.outer_ring_extra_var.set(str(self.config_data["outer_ring_extra_mm"]).replace(".0", ""))
        self.border_width_var.set(str(self.config_data["border_width_px"]))
        self.clear_preview_cache()
        self.update_manual_preview()
        messagebox.showinfo("Sucesso", "Configurações de borda atualizadas.")
