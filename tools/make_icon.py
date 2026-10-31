"""从源图生成应用图标：抠透明底 + 配色映射 + 多尺寸 ICO。

用法
----
    python tools/make_icon.py --source 源图.jpg
    python tools/make_icon.py --source 源图.jpg --preview    # 只出预览，不覆盖正式图标

为什么需要配色映射
------------------
源图是「深蓝底 + 青绿线条 + 亮蓝芯片」，而软件界面用的是
「近黑底 #0a0c10 + 品牌蓝 #4d7cfe + 青色 #22d3ee」。
直接把原图当图标会显得两张皮，所以在 HSV 空间做分区色调映射 ——
只改色相与饱和度，保留原有的明暗结构（渐变、高光、体积感）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

#: ICO 各尺寸。16/24/32 用于任务栏与资源管理器列表，256 是大图标视图
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

#: 界面主题色（与 ui/theme.py 保持一致）
ACCENT = (0x4D, 0x7C, 0xFE)      # 品牌蓝：芯片
CYAN = (0x22, 0xD3, 0xEE)        # 青色：线条
BG_DARK = (0x0B, 0x10, 0x18)     # 近黑底
BG_MID = (0x16, 0x20, 0x33)      # 背景亮部


# ---------------------------------------------------------------- 色彩空间


def rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """向量化 RGB(0~1) -> HSV，H 单位为度。"""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = rgb.max(axis=-1)
    mn = rgb.min(axis=-1)
    delta = mx - mn

    h = np.zeros_like(mx)
    safe = delta > 1e-9
    with np.errstate(invalid="ignore", divide="ignore"):
        idx = safe & (mx == r)
        h[idx] = ((g[idx] - b[idx]) / delta[idx]) % 6.0
        idx = safe & (mx == g)
        h[idx] = (b[idx] - r[idx]) / delta[idx] + 2.0
        idx = safe & (mx == b)
        h[idx] = (r[idx] - g[idx]) / delta[idx] + 4.0
    h = (h * 60.0) % 360.0
    s = np.where(mx > 1e-9, delta / np.maximum(mx, 1e-9), 0.0)
    return h, s, mx


def hsv_to_rgb(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    """向量化 HSV -> RGB(0~1)。"""
    h6 = (h % 360.0) / 60.0
    i = np.floor(h6).astype(np.int32) % 6
    f = h6 - np.floor(h6)
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))

    out = np.zeros(h.shape + (3,), dtype=np.float32)
    conditions = [i == 0, i == 1, i == 2, i == 3, i == 4, i == 5]
    choices = [
        (v, t, p), (q, v, p), (p, v, t),
        (p, q, v), (t, p, v), (v, p, q),
    ]
    for cond, (a, b, c) in zip(conditions, choices):
        out[cond] = np.stack([a[cond], b[cond], c[cond]], axis=-1)
    return np.clip(out, 0.0, 1.0)


def hue_of(rgb: tuple[int, int, int]) -> float:
    arr = np.array(rgb, dtype=np.float32).reshape(1, 1, 3) / 255.0
    h, _, _ = rgb_to_hsv(arr)
    return float(h[0, 0])


# ---------------------------------------------------------------- 处理步骤


def _measure_corner_radius(mask: np.ndarray, y0: int, y1: int) -> float:
    """测量圆角半径：从上/下往中间扫，找内容宽度首次达到满宽的位置。"""
    widths = mask.sum(axis=1).astype(np.float32)
    full = float(widths.max())
    if full <= 0:
        return 0.0

    top_idx = int(np.argmax(widths >= full * 0.985))
    reversed_idx = int(np.argmax(widths[::-1] >= full * 0.985))
    bottom_idx = len(widths) - reversed_idx
    r_top = max(0, top_idx - y0)
    r_bottom = max(0, y1 - bottom_idx)
    return float((r_top + r_bottom) / 2)


def _rounded_rect_sdf(
    xx: np.ndarray, yy: np.ndarray, cx: float, cy: float,
    half: float, radius: float,
) -> np.ndarray:
    """圆角矩形有向距离场（内部为负）。"""
    qx = np.abs(xx - cx) - (half - radius)
    qy = np.abs(yy - cy) - (half - radius)
    outside = np.sqrt(np.maximum(qx, 0.0) ** 2 + np.maximum(qy, 0.0) ** 2)
    inside = np.minimum(np.maximum(qx, qy), 0.0)
    return outside + inside - radius


def build_alpha(rgb: np.ndarray, white_cut: float = 0.935, feather_px: float = 0.7) -> np.ndarray:
    """从白底图提取透明通道。

    两个难点：
    1. **白色既是背景也是高光** —— 芯片反光、唱片中心孔都接近纯白。
       按颜色一刀切会把它们抠掉（第一版就在芯片上留了两个黑洞）。
       解法：binary_fill_holes 从画面边界填背景，被图标围住的白自动保留。
    2. **圆角外侧的装饰/阴影连着主体** —— JPEG 边缘的浅灰会把这些
       和图标连成一片，只做连通域过滤清不掉。
       解法：量出原图的圆角半径，再用圆角矩形的距离场做几何裁剪。

    注意：Pillow 13 的 ImageDraw.floodfill 实测无效（静默不改图），
    所以填洞与羽化都走 scipy。
    """
    from scipy import ndimage

    h, w = rgb.shape[:2]
    content = rgb.min(axis=-1) < white_cut
    filled = ndimage.binary_fill_holes(content)

    # 开运算切断圆角处「浅灰过渡带」与主体的细连接
    filled = ndimage.binary_opening(filled, structure=np.ones((3, 3), bool), iterations=2)

    labeled, count = ndimage.label(filled)
    if count > 1:
        sizes = ndimage.sum(filled, labeled, range(1, count + 1))
        filled = labeled == (int(np.argmax(sizes)) + 1)

    # 主体边界不能取「所有内容」的外接框 —— 圆角外侧的装饰斑块会把框撑大，
    # 几何裁剪跟着变大，斑块就落在框内被保留下来。
    # 这里只认「宽度达到主体一半」的行列，圆角部分由后面的 SDF 重建。
    row_counts = filled.sum(axis=1)
    col_counts = filled.sum(axis=0)
    if row_counts.max() == 0 or col_counts.max() == 0:
        return np.ones((h, w), dtype=np.float32)
    rows = np.where(row_counts >= row_counts.max() * 0.5)[0]
    cols = np.where(col_counts >= col_counts.max() * 0.5)[0]
    if rows.size == 0 or cols.size == 0:
        return np.ones((h, w), dtype=np.float32)

    y0, y1 = int(rows[0]), int(rows[-1])
    x0, x1 = int(cols[0]), int(cols[-1])
    side = max(y1 - y0, x1 - x0) + 1
    cy, cx = (y0 + y1) / 2.0, (x0 + x1) / 2.0
    half = side / 2.0
    radius = max(4.0, _measure_corner_radius(filled, y0, y1))

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    sdf = _rounded_rect_sdf(xx + 0.5, yy + 0.5, cx, cy, half - 0.5, radius)
    geometric = (sdf <= 0).astype(np.float32)

    alpha = np.minimum(filled.astype(np.float32), geometric)
    if feather_px > 0:
        alpha = ndimage.gaussian_filter(alpha, sigma=feather_px)
    return np.clip(alpha, 0.0, 1.0)


def square_crop(rgb: np.ndarray, alpha: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """裁到内容外接框，再用背景色 pad 成正方形。

    图标必须是正方形（拉伸会变形），但内容外接框往往不是。
    缺的那一侧用背景色补齐，对应 alpha=0，视觉上看不见。
    """
    rows = np.where(alpha.max(axis=1) > 0.02)[0]
    cols = np.where(alpha.max(axis=0) > 0.02)[0]
    if rows.size == 0 or cols.size == 0:
        return rgb, alpha

    y0, y1 = int(rows[0]), int(rows[-1]) + 1
    x0, x1 = int(cols[0]), int(cols[-1]) + 1
    rgb = rgb[y0:y1, x0:x1]
    alpha = alpha[y0:y1, x0:x1]

    h, w = alpha.shape
    side = max(h, w)
    pad_y, pad_x = side - h, side - w
    if not pad_y and not pad_x:
        return rgb, alpha

    top, left = pad_y // 2, pad_x // 2
    pad_width = ((top, pad_y - top), (left, pad_x - left))

    bg = np.array(BG_DARK, dtype=np.float32) / 255.0
    rgb_padded = np.pad(rgb, pad_width + ((0, 0),), mode="constant")
    alpha_padded = np.pad(alpha, pad_width, mode="constant")
    # 补出来的区域不可见，但 RGB 也要填成背景色，
    # 否则缩放时透明区的纯黑会渗出来变成暗边
    rgb_padded = np.where(alpha_padded[..., None] > 0, rgb_padded, bg)
    return rgb_padded, alpha_padded


def remap_palette(rgb: np.ndarray) -> np.ndarray:
    """把源图配色映射到软件主题色，只动色相/饱和度，保留明暗结构。"""
    h, s, v = rgb_to_hsv(rgb)

    target_cyan_h = hue_of(CYAN)
    target_blue_h = hue_of(ACCENT)
    target_bg_h = hue_of(BG_MID)

    # ① 青色线条：源图偏绿（H≈150），拉到 #22d3ee（H≈187）。
    #    亮度略收，避免满饱和青色在深色界面上刺眼
    cyan_zone = (h > 115) & (h < 200) & (s > 0.18) & (v > 0.20)
    h = np.where(cyan_zone, target_cyan_h, h)
    s = np.where(cyan_zone, np.clip(s * 1.02, 0, 1), s)
    v = np.where(cyan_zone, np.clip(v * 0.94, 0, 1), v)

    # ② 芯片与高亮蓝：拉到品牌蓝 #4d7cfe（H≈224），提饱和做出层次
    blue_zone = (h >= 200) & (h <= 252) & (s > 0.28) & (v > 0.26)
    h = np.where(blue_zone, target_blue_h, h)
    s = np.where(blue_zone, np.clip(s * 1.05, 0, 1), s)
    v = np.where(blue_zone, np.clip(v * 1.00, 0, 1), v)

    # ③ 背景：大幅压暗并去饱和，让图标立起来。
    #    原图底板偏灰蓝，不压的话整体像蒙了一层雾，跟近黑界面放一起会发灰
    bg_zone = (v <= 0.34) & (s > 0.06)
    h = np.where(bg_zone, target_bg_h, h)
    s = np.where(bg_zone, np.clip(s * 0.38, 0, 1), s)
    v = np.where(bg_zone, np.clip(v * 0.62, 0, 1), v)

    # ④ 中间调背景（原来那层圆形光晕）：压低并统一色相，只留一点微光
    mid_zone = (v > 0.34) & (v <= 0.52) & (h >= 190) & (h <= 258)
    h = np.where(mid_zone, target_bg_h, h)
    s = np.where(mid_zone, np.clip(s * 0.40, 0, 1), s)
    v = np.where(mid_zone, np.clip(v * 0.72, 0, 1), v)

    # ⑤ 低饱和高光（圆角内侧那圈反光）必须保持中性灰。
    #    它们本来是柔和的灰蓝，若跟着背景一起拉成品牌蓝，
    #    在深底上就变成一块突兀的蓝斑。放在最后覆盖前面的处理。
    neutral_zone = (s < 0.24) & (v > 0.28)
    s = np.where(neutral_zone, np.clip(s * 0.55, 0, 1), s)
    v = np.where(neutral_zone, np.clip(v * 0.80, 0, 1), v)

    return hsv_to_rgb(h, s, v)


def bleed_edge_color(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """把半透明边缘的 RGB 换成邻近内部颜色。

    羽化后的边缘像素 RGB 还是原图的白底灰白色，直接缩放会在图标外圈
    留一道白边。这里用「加权模糊再归一化」把内部颜色向边缘扩散，
    相当于图像处理里的 color bleed / edge extend。
    """
    from scipy import ndimage

    # 用距离变换精确定位「距边界 3px 内」的像素：只看 alpha 值会漏掉
    # 紧贴边界的那些 alpha≈1 但 RGB 仍是白底的像素，缩放后就是一道白边
    solid = alpha > 0.05
    if not np.any(solid):
        return rgb
    distance = ndimage.distance_transform_edt(solid)
    edge = solid & (distance <= 3.0)

    w = alpha[..., None]
    weighted = rgb * w
    radius = 6

    def blur(arr: np.ndarray, radius: int) -> np.ndarray:
        sigma = (radius, radius, 0) if arr.ndim == 3 else (radius, radius)
        return ndimage.gaussian_filter(arr, sigma=sigma)

    num = blur(weighted, radius)
    den = blur(w, radius)
    filled = num / np.maximum(den, 1e-4)

    out = rgb.copy()
    out[edge] = np.clip(filled[edge], 0.0, 1.0)
    return out


def to_image(rgb: np.ndarray, alpha: np.ndarray) -> Image.Image:
    data = np.dstack([(rgb * 255).astype(np.uint8), (alpha * 255).astype(np.uint8)])
    return Image.fromarray(data, mode="RGBA")


#: 内容占画布的比例。Windows 图标规范要求四周留白，贴边会显得拥挤，
#: 小尺寸（16/24）尤其明显 —— 留一圈透明边后视觉上会"松开"。
CONTENT_RATIO = 0.90


def resized(img: Image.Image, size: int, ratio: float = CONTENT_RATIO) -> Image.Image:
    """按目标尺寸缩放，并在四周留出透明边距。"""
    inner = max(1, int(round(size * ratio)))
    thumb = img.resize((inner, inner), Image.LANCZOS)
    if inner == size:
        return thumb
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    offset = (size - inner) // 2
    canvas.alpha_composite(thumb, (offset, offset))
    return canvas


def build(source: Path) -> Image.Image:
    img = Image.open(source).convert("RGB")
    rgb = np.asarray(img, dtype=np.float32) / 255.0
    alpha = build_alpha(rgb)
    rgb, alpha = square_crop(rgb, alpha)
    rgb = remap_palette(rgb)
    rgb = bleed_edge_color(rgb, alpha)
    return to_image(rgb, alpha)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成音序管家应用图标")
    parser.add_argument("--source", required=True, help="源图路径（JPG/PNG）")
    parser.add_argument("--preview", action="store_true", help="只输出预览，不覆盖正式图标")
    parser.add_argument("--out-dir", default=str(ASSETS), help="输出目录")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.exists():
        print(f"源图不存在：{source}")
        return 1

    base = build(source)
    print(f"处理完成：{base.size[0]}×{base.size[1]} RGBA")

    if args.preview:
        preview_dir = ROOT / "tests" / "_tmp" / "icon"
        preview_dir.mkdir(parents=True, exist_ok=True)
        base.save(preview_dir / "icon_512.png", "PNG")
        # 各尺寸横排，深色底，便于检查小尺寸可辨识度
        width = sum(ICO_SIZES) + 8 * len(ICO_SIZES) + 16
        strip = Image.new("RGBA", (width, 260), (10, 12, 16, 255))
        x = 8
        for size in ICO_SIZES:
            strip.alpha_composite(resized(base, size), (x, 8))
            x += size + 8
        strip.save(preview_dir / "sizes.png", "PNG")
        print(f"预览已保存：{preview_dir}")
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / "app.png"
    resized(base, 512).save(png_path, "PNG")
    ico_path = out_dir / "app.ico"
    resized(base, 256).save(ico_path, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    print(f"已生成：{png_path}")
    print(f"已生成：{ico_path}（{ico_path.stat().st_size / 1024:.0f} KB，{len(ICO_SIZES)} 个尺寸）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
