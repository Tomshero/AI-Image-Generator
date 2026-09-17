import base64
import datetime
import io
import json
import os
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import requests
from PIL import Image, ImageTk

# 源码中不内置任何密钥：优先读取本地 config.json，其次环境变量 IMAGE_API_KEY
# 使用前请在界面填写密钥，或执行：setx IMAGE_API_KEY "sk-xxx"
DEFAULT_API_KEY = os.getenv("IMAGE_API_KEY", "")
DEFAULT_BASE_URL = "https://api.mcgfdata.com/v1/images/generations"
# 打包成 exe 后也要把配置和图片放在 exe 同级目录，而不是临时解压目录
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "images")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
MODELS = ["gpt-image-2.5", "gpt-image-2"]

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
    cfg = {"api_key": DEFAULT_API_KEY, "model": MODELS[0], "base_url": DEFAULT_BASE_URL}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg["api_key"] = data.get("api_key") or cfg["api_key"]
                cfg["base_url"] = data.get("base_url") or cfg["base_url"]
                if data.get("model") in MODELS:
                    cfg["model"] = data["model"]
        except Exception:
            pass
    return cfg


def save_config(api_key, model, base_url):
    """把 API Key、模型与接口地址写入配置文件，下次启动自动加载。"""
    os.makedirs(BASE_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"api_key": api_key, "model": model, "base_url": base_url},
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


def fetch_image(prompt, api_key, model, base_url):
    """调用接口返回 PIL.Image，出错时抛出可读的异常信息。"""
    payload = {
        "model": model,
        "prompt": prompt,
        "size": "1024x1024",
        "quality": "auto",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
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
def card(parent, padx=14, pady=10):
    """带白色卡片底和浅边框的容器。"""
    outer = tk.Frame(parent, bg=BORDER)
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
    return api_key, model, base_url


def save_only():
    """只保存配置，不触发生成。"""
    values = read_settings()
    if not values:
        return
    api_key, model, base_url = values
    try:
        save_config(api_key, model, base_url)
        set_status(f"配置已保存 · {model} · {base_url}", "#0F9D58")
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
    api_key, model, base_url = values

    try:
        save_config(api_key, model, base_url)
    except Exception as exc:
        set_status("配置保存失败", "#D93025")
        messagebox.showerror("错误", f"配置保存失败：{exc}")
        return

    set_running(True)
    placeholder_label.config(text="")

    def worker():
        try:
            img = fetch_image(prompt, api_key, model, base_url)
            path = save_without_icc(img, next_save_path())
            preview = img.copy()
            preview.thumbnail((460, 460))
            photo = ImageTk.PhotoImage(preview)
            root.after(0, lambda: on_success(photo, path))
        except Exception as exc:  # 网络/解析/保存等任何异常都反馈到界面
            root.after(0, lambda: on_error(exc))

    threading.Thread(target=worker, daemon=True).start()


def on_success(photo, path):
    img_label.config(image=photo)
    img_label.image = photo  # 保持引用，防止被 GC 后图片不显示
    set_running(False)
    set_status(f"已保存：{path}", "#0F9D58")
    messagebox.showinfo("完成", f"图片已保存：\n{path}")


def on_error(exc):
    set_running(False)
    set_status("生成失败", "#D93025")
    placeholder_label.config(text="生成失败，请检查提示词 / API Key / 网络")
    messagebox.showerror("错误", f"生成失败：{exc}")


cfg = load_config()
animating = {"on": False}

# ---------- 窗口 ----------
root = tk.Tk()
root.title("GPT Image 生图工具")
root.configure(bg=BG)
root.geometry("620x760")
root.minsize(560, 620)

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
        font=FONT_BOLD,
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

# 底部状态栏
status_var = tk.StringVar(value="就绪")
status_bar = tk.Frame(root, bg=SOFT, height=34)
status_bar.pack(fill=tk.X, side=tk.BOTTOM)
status_bar.pack_propagate(False)
status_label = tk.Label(status_bar, textvariable=status_var, bg=SOFT, fg=MUTED, font=FONT_SMALL, anchor="w")
status_label.pack(fill=tk.X, padx=14)

root.mainloop()
