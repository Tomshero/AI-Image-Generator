import base64
import datetime
import io
import json
import mimetypes
import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import requests
from PIL import Image, ImageTk

# 默认不内置任何接口地址和密钥：优先读取本地 config.json，其次环境变量 IMAGE_API_KEY
# 首次使用请在界面填写接口地址和 API Key，或执行：setx IMAGE_API_KEY "sk-xxx"
DEFAULT_API_KEY = os.getenv("IMAGE_API_KEY", "")
DEFAULT_BASE_URL = ""
# 打包成 exe 后也要把配置和图片放在 exe 同级目录，而不是临时解压目录
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "images")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
MODELS = ["gpt-image-2.5", "gpt-image-2", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"]
# 接口实测支持的尺寸：auto 由模型自行决定比例
SIZES = ["auto", "1024x1024", "1024x1536", "1536x1024"]
SIZE_LABELS = {"auto": "自动", "1024x1024": "方图 1:1", "1024x1536": "竖版 2:3", "1536x1024": "横版 3:2"}
UPLOAD_MAX_SIDE = 1024  # 上传给接口的底图最大边长

# ---------- 主题配色 ----------
BG = "#F4F6FA"
CARD = "#FFFFFF"
BORDER = "#E3E8F0"
TEXT = "#1F2937"
MUTED = "#6B7280"
ACCENT = "#2F6FED"
ACCENT_DARK = "#2258C4"
SOFT = "#EEF2F8"

FONT = ("Microsoft YaHei UI", 10)
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 15, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 9)


def load_config():
    """读取本地配置，文件缺失或损坏时回退到默认值。"""
    cfg = {"api_key": DEFAULT_API_KEY, "model": MODELS[0], "base_url": DEFAULT_BASE_URL,
           "size": SIZES[0]}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg["api_key"] = data.get("api_key") or cfg["api_key"]
                cfg["base_url"] = data.get("base_url") or cfg["base_url"]
                if data.get("model") in MODELS:
                    cfg["model"] = data["model"]
                if data.get("size") in SIZES:
                    cfg["size"] = data["size"]
        except Exception:
            pass
    return cfg


def save_config(api_key, model, base_url, size):
    """把 API Key、模型、尺寸与接口地址写入配置文件，下次启动自动加载。"""
    os.makedirs(BASE_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"api_key": api_key, "model": model, "base_url": base_url, "size": size},
            f,
            ensure_ascii=False,
            indent=2,
        )


def next_save_path():
    """按时间戳生成不重复的文件名，避免覆盖历史图片。"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(OUTPUT_DIR, f"output_{stamp}.png")
    index = 1
    while os.path.exists(path):  # 同一秒内重复生成时自动加序号
        path = os.path.join(OUTPUT_DIR, f"output_{stamp}_{index}.png")
        index += 1
    return path


def edits_endpoint(base_url):
    """由文生图地址推导图生图(edits)地址。"""
    url = base_url.rstrip("/")
    if url.endswith("generations"):
        return url[: -len("generations")] + "edits"
    return url.rsplit("/", 1)[0] + "/edits"


def read_upload_bytes(path):
    """读取并压缩底图，控制上传体积。"""
    with Image.open(path) as im:
        im = im.convert("RGB") if im.mode not in ("RGB", "RGBA") else im
        im.thumbnail((UPLOAD_MAX_SIDE, UPLOAD_MAX_SIDE))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
    return buf.getvalue()


def fetch_image(prompt, api_key, model, base_url, size="1024x1024", image_paths=None):
    """调用接口返回 PIL.Image；传入 image_paths 时走图生图，否则走文生图。"""
    headers = {"Authorization": f"Bearer {api_key}"}
    image_paths = image_paths or []

    if image_paths:
        files = []
        for p in image_paths:
            mime = mimetypes.guess_type(p)[0] or "image/png"
            files.append(("image", (os.path.basename(p), read_upload_bytes(p), mime)))
        data = {"model": model, "prompt": prompt, "size": size}
        res = requests.post(
            edits_endpoint(base_url), headers=headers,
            files=files, data=data, timeout=180,
        )
    else:
        payload = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "quality": "auto",
        }
        headers["Content-Type"] = "application/json"
        res = requests.post(base_url, headers=headers, json=payload, timeout=120)

    if res.status_code != 200:
        raise RuntimeError(f"接口返回 {res.status_code}：{res.text[:200]}")

    data = res.json()
    item = (data.get("data") or [{}])[0]

    if item.get("url"):
        img_res = requests.get(item["url"], timeout=120)
        img_res.raise_for_status()
        return Image.open(io.BytesIO(img_res.content))

    if item.get("b64_json"):
        return Image.open(io.BytesIO(base64.b64decode(item["b64_json"])))

    raise RuntimeError(f"返回内容中没有图片数据：{str(data)[:300]}")


def save_without_icc(img, path):
    """保存时剥离不规范的 ICC 色彩配置，避免 libpng 的 iCCP 警告。"""
    out = img.copy()
    out.info.pop("icc_profile", None)
    out.save(path, icc_profile=None)
    return path


# ---------- 界面辅助 ----------
def card(parent=None, padx=14, pady=10):
    """带白色卡片底和浅边框的容器，统一放进可滚动内容区。"""
    target = content if (parent is None or parent is root) else parent
    outer = tk.Frame(target, bg=BORDER)
    outer.pack(fill=tk.X, padx=padx, pady=(pady, 0))
    inner = tk.Frame(outer, bg=CARD)
    inner.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
    return inner


def refresh_model_buttons():
    """根据当前选中的模型高亮分段按钮。"""
    for name, btn_widget in model_buttons.items():
        active = name == model_var.get()
        btn_widget.config(
            bg=ACCENT if active else "#F7F9FC",
            fg="#FFFFFF" if active else MUTED,
            relief=tk.FLAT,
        )


def select_model(name):
    model_var.set(name)
    refresh_model_buttons()


def refresh_size_buttons():
    """根据当前选中的尺寸高亮分段按钮。"""
    for key, btn_widget in size_buttons.items():
        active = key == size_var.get()
        btn_widget.config(
            bg=ACCENT if active else "#F7F9FC",
            fg="#FFFFFF" if active else MUTED,
            relief=tk.FLAT,
        )


def select_size(key):
    size_var.set(key)
    refresh_size_buttons()


def toggle_key():
    """切换 API Key 明文/掩码显示。"""
    if api_entry.cget("show") == "*":
        api_entry.config(show="")
        key_toggle_btn.config(text="隐藏")
    else:
        api_entry.config(show="*")
        key_toggle_btn.config(text="显示")


def set_status(text, color=MUTED):
    status_var.set(text)
    status_label.config(fg=color)


def animate(i=0):
    """生成过程中的状态点动画。"""
    if not animating["on"]:
        return
    set_status("正在生成" + "·" * (i % 4), ACCENT)
    root.after(400, lambda: animate(i + 1))


def set_running(running):
    animating["on"] = running
    if running:
        animate()
    btn.config(state=tk.DISABLED)
    save_btn.config(state=tk.DISABLED if running else tk.NORMAL)
    if not running:
        btn.config(state=tk.NORMAL)


def fill_placeholder(event=None):
    """提示词为空时显示灰色占位文案。"""
    text = entry.get("1.0", tk.END).strip()
    if text == "" and not entry_has_input.get():
        entry.config(fg=MUTED)
    else:
        entry.config(fg=TEXT)


def on_entry_key(event=None):
    entry_has_input.set(True)
    entry.config(fg=TEXT)
    return None


def read_settings():
    """读取并校验界面上的接口地址、模型与密钥。"""
    api_key = api_var.get().strip()
    model = model_var.get().strip()
    base_url = url_var.get().strip()
    size = size_var.get().strip()
    if not base_url:
        messagebox.showwarning("提示", "请填写接口地址（Base URL）！")
        return None
    if not base_url.startswith(("http://", "https://")):
        messagebox.showwarning("提示", "接口地址需以 http:// 或 https:// 开头！")
        return None
    if not api_key:
        messagebox.showwarning("提示", "请填写 API Key！")
        return None
    if model not in MODELS:
        messagebox.showwarning("提示", f"请选择有效的模型：{' / '.join(MODELS)}")
        return None
    if size not in SIZES:
        messagebox.showwarning("提示", f"请选择有效的尺寸：{' / '.join(SIZES)}")
        return None
    return api_key, model, base_url, size


def save_only():
    """只保存配置，不触发生成。"""
    values = read_settings()
    if not values:
        return
    api_key, model, base_url, size = values
    try:
        save_config(api_key, model, base_url, size)
        set_status(f"配置已保存 · {model} · {size} · {base_url}", "#0F9D58")
    except Exception as exc:
        set_status("配置保存失败", "#D93025")
        messagebox.showerror("错误", f"配置保存失败：{exc}")


def generate_img():
    """先保存配置，再立即用保存后的配置发起生成。"""
    prompt = entry.get("1.0", tk.END).strip()
    if not prompt:
        messagebox.showwarning("提示", "请输入提示词！")
        return

    values = read_settings()
    if not values:
        return
    api_key, model, base_url, size = values

    try:
        save_config(api_key, model, base_url, size)
    except Exception as exc:
        set_status("配置保存失败", "#D93025")
        messagebox.showerror("错误", f"配置保存失败：{exc}")
        return

    refs = list(reference_images)
    set_running(True)
    placeholder_label.config(text="")

    def worker():
        try:
            img = fetch_image(prompt, api_key, model, base_url, size=size, image_paths=refs)
            path = save_without_icc(img, next_save_path())
            preview = img.copy()
            preview.thumbnail((460, 460))
            photo = ImageTk.PhotoImage(preview)
            root.after(0, lambda: on_success(photo, path, prompt))
        except Exception as exc:  # 网络/解析/保存等任何异常都反馈到界面
            root.after(0, lambda: on_error(exc))

    threading.Thread(target=worker, daemon=True).start()


def on_success(photo, path, prompt):
    img_label.config(image=photo)
    img_label.image = photo  # 保持引用，防止被 GC 后图片不显示
    set_running(False)
    set_status(f"已保存：{path}", "#0F9D58")

    add_history(path, prompt)
    if chain_var.get():  # 结果自动成为下一轮底图，实现连续改图
        reference_images.clear()
        reference_images.append(path)
        refresh_reference()

    messagebox.showinfo("完成", f"图片已保存：\n{path}")


def on_error(exc):
    set_running(False)
    set_status("生成失败", "#D93025")
    placeholder_label.config(text="生成失败，请检查提示词 / API Key / 网络")
    messagebox.showerror("错误", f"生成失败：{exc}")


cfg = load_config()
animating = {"on": False}

# 迭代改图状态：底图列表 + 历史轮次
reference_images = []   # 当前作为底图/参考图的本地图片路径
history_records = []    # [(图片路径, 提示词)]
reference_photos = []   # 防止缩略图被 GC
history_photos = []


def pick_reference():
    """导入本地图片作为底图/参考图。"""
    paths = filedialog.askopenfilenames(
        title="选择底图 / 参考图",
        filetypes=[("图片文件", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"), ("所有文件", "*.*")],
    )
    for p in paths:
        if p not in reference_images:
            reference_images.append(p)
    refresh_reference()


def clear_reference():
    reference_images.clear()
    refresh_reference()


def use_as_reference(path):
    """把历史中的某一轮结果设为当前底图，从它继续修改。"""
    if path not in reference_images:
        reference_images.append(path)
    refresh_reference()
    set_status(f"已设为底图：{os.path.basename(path)}", ACCENT)


def refresh_reference():
    for w in ref_thumb_frame.winfo_children():
        w.destroy()
    reference_photos.clear()

    if not reference_images:
        tk.Label(ref_thumb_frame, text="未选择（将使用文生图）", bg=CARD, fg=MUTED,
                 font=FONT_SMALL).pack(anchor="w")
    else:
        for path in reference_images:
            try:
                with Image.open(path) as im:
                    im.thumbnail((64, 64))
                    photo = ImageTk.PhotoImage(im)
                reference_photos.append(photo)
                cell = tk.Frame(ref_thumb_frame, bg=CARD)
                cell.pack(side=tk.LEFT, padx=(0, 6))
                tk.Label(cell, image=photo, bg=CARD).pack()
                tk.Label(cell, text=os.path.basename(path), bg=CARD, fg=MUTED,
                         font=FONT_SMALL, wraplength=70).pack()
            except Exception:
                pass

    mode = "图生图（改图）" if reference_images else "文生图"
    ref_mode_label.config(text=f"当前模式：{mode}")


def add_history(path, prompt):
    history_records.append((path, prompt))
    refresh_history()


def refresh_history():
    for w in history_frame.winfo_children():
        w.destroy()
    history_photos.clear()

    if not history_records:
        tk.Label(history_frame, text="暂无记录", bg=CARD, fg=MUTED, font=FONT_SMALL).pack(anchor="w")
        return

    for index, (path, prompt) in enumerate(history_records, start=1):
        try:
            with Image.open(path) as im:
                im.thumbnail((72, 72))
                photo = ImageTk.PhotoImage(im)
            history_photos.append(photo)
            cell = tk.Frame(history_frame, bg=CARD)
            cell.pack(side=tk.LEFT, padx=(0, 8))
            btn_hist = tk.Label(cell, image=photo, bg=CARD, cursor="hand2")
            btn_hist.pack()
            btn_hist.bind("<Button-1>", lambda e, p=path: use_as_reference(p))
            tip = prompt if len(prompt) <= 8 else prompt[:8] + "…"
            tk.Label(cell, text=f"{index}. {tip}", bg=CARD, fg=MUTED, font=FONT_SMALL,
                     wraplength=80).pack()
        except Exception:
            pass

# ---------- 窗口 ----------
root = tk.Tk()
root.title("GPT Image 生图工具")
root.configure(bg=BG)
# 高度按屏幕自适应，避免窗口超出屏幕导致按钮点不到
win_h = max(640, min(980, root.winfo_screenheight() - 90))
root.geometry(f"700x{win_h}")
root.minsize(660, 560)

style = ttk.Style()
try:
    style.theme_use("clam")
except tk.TclError:
    pass

# 顶部标题栏
header = tk.Frame(root, bg=ACCENT, height=58)
header.pack(fill=tk.X)
header.pack_propagate(False)
tk.Label(header, text="GPT Image 生图工具", bg=ACCENT, fg="#FFFFFF", font=FONT_TITLE).pack(side=tk.LEFT, padx=18)
tk.Label(header, text="接口 / 模型 / 密钥 均可自定义", bg=ACCENT, fg="#DCE6FF", font=FONT_SMALL).pack(side=tk.RIGHT, padx=18)

# 可滚动内容区：内容再多也不会把按钮挤出屏幕
mid = tk.Frame(root, bg=BG)
mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

canvas = tk.Canvas(mid, bg=BG, highlightthickness=0)
canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
scrollbar = ttk.Scrollbar(mid, orient=tk.VERTICAL, command=canvas.yview)
scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
canvas.configure(yscrollcommand=scrollbar.set)

content = tk.Frame(canvas, bg=BG)
canvas_window = canvas.create_window((0, 0), window=content, anchor="nw")


def _sync_scroll(event=None):
    canvas.configure(scrollregion=canvas.bbox("all"))


def _sync_width(event):
    canvas.itemconfig(canvas_window, width=event.width)


def _on_mousewheel(event):
    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


content.bind("<Configure>", _sync_scroll)
canvas.bind("<Configure>", _sync_width)
canvas.bind_all("<MouseWheel>", _on_mousewheel)

# 提示词卡片
prompt_card = card(root)
tk.Label(prompt_card, text="提示词", bg=CARD, fg=TEXT, font=FONT_BOLD).pack(anchor="w", padx=14, pady=(12, 6))
text_wrap = tk.Frame(prompt_card, bg=BORDER)
text_wrap.pack(fill=tk.X, padx=14)
entry_has_input = tk.BooleanVar(value=True)
entry = tk.Text(
    text_wrap,
    width=60,
    height=6,
    font=FONT,
    fg=TEXT,
    bg="#FBFCFE",
    bd=0,
    relief=tk.FLAT,
    wrap=tk.WORD,
    insertbackground=ACCENT,
    padx=8,
    pady=8,
)
entry.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
entry.bind("<Key>", on_entry_key)
tk.Label(prompt_card, text="描述越具体，出图越贴合预期（Ctrl + Enter 可直接生成）", bg=CARD, fg=MUTED, font=FONT_SMALL).pack(
    anchor="w", padx=14, pady=(6, 12)
)

# 底图 · 参考图卡片
ref_card = card(root)
tk.Label(ref_card, text="底图 · 参考图（图生图）", bg=CARD, fg=TEXT, font=FONT_BOLD).pack(
    anchor="w", padx=14, pady=(12, 6))
ref_mode_label = tk.Label(ref_card, text="当前模式：文生图", bg=CARD, fg=MUTED, font=FONT_SMALL)
ref_mode_label.pack(anchor="w", padx=14)
ref_thumb_frame = tk.Frame(ref_card, bg=CARD)
ref_thumb_frame.pack(fill=tk.X, padx=14, pady=(6, 8))

ref_btns = tk.Frame(ref_card, bg=CARD)
ref_btns.pack(fill=tk.X, padx=14, pady=(0, 8))
tk.Button(ref_btns, text="导入图片", font=FONT_SMALL, bg=SOFT, fg=TEXT, bd=0, relief=tk.FLAT,
          padx=12, pady=5, cursor="hand2", command=pick_reference).pack(side=tk.LEFT)
tk.Button(ref_btns, text="清除底图", font=FONT_SMALL, bg=SOFT, fg=TEXT, bd=0, relief=tk.FLAT,
          padx=12, pady=5, cursor="hand2", command=clear_reference).pack(side=tk.LEFT, padx=8)
chain_var = tk.BooleanVar(value=True)
tk.Checkbutton(ref_btns, text="结果自动作为下一轮底图", variable=chain_var, bg=CARD, fg=TEXT,
               font=FONT_SMALL, activebackground=CARD, highlightthickness=0).pack(side=tk.LEFT, padx=(4, 0))
tk.Label(ref_card, text="勾选后可连续下指令改图；点击历史缩略图可从任意一轮重新改",
         bg=CARD, fg=MUTED, font=FONT_SMALL).pack(anchor="w", padx=14, pady=(0, 12))

# 设置卡片
set_card = card(root)
tk.Label(set_card, text="接口 · 模型 · 密钥", bg=CARD, fg=TEXT, font=FONT_BOLD).pack(anchor="w", padx=14, pady=(12, 8))

url_row = tk.Frame(set_card, bg=CARD)
url_row.pack(fill=tk.X, padx=14, pady=(0, 8))
tk.Label(url_row, text="接口地址", bg=CARD, fg=MUTED, font=FONT, width=8, anchor="w").pack(side=tk.LEFT)
url_var = tk.StringVar(value=cfg["base_url"])
url_wrap = tk.Frame(url_row, bg=BORDER)
url_wrap.pack(side=tk.LEFT, fill=tk.X, expand=True)
tk.Entry(
    url_wrap,
    textvariable=url_var,
    font=FONT_SMALL,
    fg=TEXT,
    bg="#FBFCFE",
    bd=0,
    relief=tk.FLAT,
    insertbackground=ACCENT,
).pack(fill=tk.X, padx=1, pady=1, ipady=6)

model_row = tk.Frame(set_card, bg=CARD)
model_row.pack(fill=tk.X, padx=14, pady=(0, 8))
tk.Label(model_row, text="模型", bg=CARD, fg=MUTED, font=FONT, width=8, anchor="w").pack(side=tk.LEFT)
model_var = tk.StringVar(value=cfg["model"])
seg = tk.Frame(model_row, bg="#F7F9FC", highlightthickness=1, highlightbackground=BORDER)
seg.pack(side=tk.LEFT)
model_buttons = {}
for name in MODELS:
    b = tk.Button(
        seg,
        text=name,
        font=FONT_SMALL,
        bd=0,
        relief=tk.FLAT,
        padx=14,
        pady=5,
        cursor="hand2",
        command=lambda n=name: select_model(n),
    )
    b.pack(side=tk.LEFT, padx=2, pady=2)
    model_buttons[name] = b
refresh_model_buttons()

size_row = tk.Frame(set_card, bg=CARD)
size_row.pack(fill=tk.X, padx=14, pady=(0, 8))
tk.Label(size_row, text="尺寸", bg=CARD, fg=MUTED, font=FONT, width=8, anchor="w").pack(side=tk.LEFT)
size_var = tk.StringVar(value=cfg["size"])
seg_size = tk.Frame(size_row, bg="#F7F9FC", highlightthickness=1, highlightbackground=BORDER)
seg_size.pack(side=tk.LEFT)
size_buttons = {}
for key in SIZES:
    b = tk.Button(
        seg_size,
        text=SIZE_LABELS[key],
        font=FONT_SMALL,
        bd=0,
        relief=tk.FLAT,
        padx=10,
        pady=5,
        cursor="hand2",
        command=lambda k=key: select_size(k),
    )
    b.pack(side=tk.LEFT, padx=2, pady=2)
    size_buttons[key] = b
refresh_size_buttons()

key_row = tk.Frame(set_card, bg=CARD)
key_row.pack(fill=tk.X, padx=14, pady=(0, 12))
tk.Label(key_row, text="API Key", bg=CARD, fg=MUTED, font=FONT, width=8, anchor="w").pack(side=tk.LEFT)
api_var = tk.StringVar(value=cfg["api_key"])
key_wrap = tk.Frame(key_row, bg=BORDER)
key_wrap.pack(side=tk.LEFT, fill=tk.X, expand=True)
api_entry = tk.Entry(
    key_wrap,
    textvariable=api_var,
    font=FONT,
    fg=TEXT,
    bg="#FBFCFE",
    bd=0,
    relief=tk.FLAT,
    show="*",
    insertbackground=ACCENT,
)
api_entry.pack(fill=tk.X, padx=1, pady=1, ipady=6)


def hover_on(e):
    if str(btn["state"]) == "normal":
        btn.config(bg=ACCENT_DARK)


def hover_off(e):
    if str(btn["state"]) == "normal":
        btn.config(bg=ACCENT)


key_toggle_btn = tk.Button(
    key_row,
    text="显示",
    font=FONT_SMALL,
    bg=SOFT,
    fg=MUTED,
    bd=0,
    relief=tk.FLAT,
    padx=10,
    pady=4,
    cursor="hand2",
    command=toggle_key,
)
key_toggle_btn.pack(side=tk.LEFT, padx=(8, 0))
tk.Label(
    set_card,
    text="密钥仅保存在本地 config.json（已加入 .gitignore，不会随源码外传）",
    bg=CARD,
    fg=MUTED,
    font=FONT_SMALL,
).pack(anchor="w", padx=14, pady=(0, 12))

# 操作按钮
action = tk.Frame(root, bg=BG)
action.pack(fill=tk.X, padx=14, pady=14)
btn = tk.Button(
    action,
    text="保存并生成",
    font=FONT_BOLD,
    bg=ACCENT,
    fg="#FFFFFF",
    bd=0,
    relief=tk.FLAT,
    padx=22,
    pady=9,
    cursor="hand2",
    command=generate_img,
)
btn.pack(side=tk.LEFT)
btn.bind("<Enter>", hover_on)
btn.bind("<Leave>", hover_off)
save_btn = tk.Button(
    action,
    text="仅保存设置",
    font=FONT,
    bg=CARD,
    fg=TEXT,
    bd=0,
    relief=tk.FLAT,
    padx=18,
    pady=9,
    cursor="hand2",
    command=save_only,
)
save_btn.pack(side=tk.LEFT, padx=10)
root.bind_all("<Control-Return>", lambda e: generate_img() if str(btn["state"]) == "normal" else None)

# 预览卡片
preview_card = card(root)
preview_card.pack(fill=tk.BOTH, expand=True, padx=14, pady=0)
tk.Label(preview_card, text="预览", bg=CARD, fg=TEXT, font=FONT_BOLD).pack(anchor="w", padx=14, pady=(12, 6))
stage = tk.Frame(preview_card, bg="#FBFCFE", highlightthickness=1, highlightbackground=BORDER)
stage.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 14))
placeholder_label = tk.Label(stage, text="图片将在这里显示", bg="#FBFCFE", fg=MUTED, font=FONT_SMALL)
placeholder_label.pack(expand=True)
img_label = tk.Label(stage, bg="#FBFCFE")
img_label.pack(expand=True)

# 修改历史卡片
hist_card = card(root)
tk.Label(hist_card, text="修改历史（点击图片可从该轮继续改）", bg=CARD, fg=TEXT, font=FONT_BOLD).pack(
    anchor="w", padx=14, pady=(12, 6))
history_frame = tk.Frame(hist_card, bg=CARD)
history_frame.pack(fill=tk.X, padx=14, pady=(0, 12))

refresh_reference()
refresh_history()

# 底部状态栏
status_var = tk.StringVar(value="就绪")
status_bar = tk.Frame(root, bg=SOFT, height=34)
status_bar.pack(fill=tk.X, side=tk.BOTTOM)
status_bar.pack_propagate(False)
status_label = tk.Label(status_bar, textvariable=status_var, bg=SOFT, fg=MUTED, font=FONT_SMALL, anchor="w")
status_label.pack(fill=tk.X, padx=14)

root.mainloop()
