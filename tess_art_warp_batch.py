"""
tess_art_warp.py -- TESS-FFI-inspired generative art, single-file edition,
now with stars that gravitationally warp the mesh around them.

This consolidates the tess_art pipeline (background/noise stats -> adaptive
Voronoi mesh -> per-cell shading -> bright-source glow -> render -> GIF
preview) that previously lived across several notebook cells into one
script, and adds one new stage:

    Each detected bright source generates a localized space-warp: a smooth
    function of (r, theta) around the star -- a Gaussian radial envelope
    times an m-fold angular wobble -- that both twirls query points around
    the star (a "twirl"/vortex, the same trick behind a gravitational-
    lensing swirl) and breathes them in and out along that same angular
    pattern. This warped coordinate, not the original one, is what gets
    handed to the *existing* Voronoi mesh's `assign`, so the same cells
    that were already there simply appear to bend around each star --
    nothing about mesh generation or per-cell statistics changes. Stronger
    stars (same rel**flux_power brightness language glow.py already uses)
    get both a larger radius of influence and a stronger twirl/breathe.

    The amount of local warp also tints the cell color by blending toward
    `palette.sample_cyclic(warp_angle)` -- the same hue wheel already used
    for each star's own glow -- so color drift is literally a readout of
    how much the fabric moved at that pixel, using no new colors.

    In the GIF, each star's warp pattern has its own slow precession rate
    (distinct from -- and much slower than -- the glow's twinkle rate), so
    the deformation itself evolves frame to frame instead of only
    brightness pulsing. Because the geometry now changes per frame, the
    mesh layer can no longer be rendered once and held fixed; each frame
    re-labels the mesh under that frame's warp field.

Run this file directly (`python tess_art_warp.py`) or paste it into a
Jupyter cell -- both work the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple
import argparse

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree
from PIL import Image

try:
    from IPython.display import display
except ImportError:  # running as a plain script, not a notebook
    def display(*_args, **_kwargs):
        pass


# ============================================================
# config.py
# ============================================================

@dataclass
class RenderConfig:
    # Mesh (Voronoi only -- no hexagons yet)
    target_cells: int = 3200
    relax_iters: int = 2

    # Bright-source detection
    star_threshold_sigma: float = 8.0
    star_min_separation: int = 9
    star_max_sources: int = 150

    # Shading
    jitter_strength: float = 0.06
    alpha_floor: float = 0.16
    seam_strength: float = 0.30

    # Output
    out_size: int = 1500
    supersample: int = 2

    # Star-driven space warp
    warp_enabled: bool = True
    warp_base_radius: float = 24.0    # source-image px of influence, at scale=1
    warp_swirl: float = 0.45          # max twirl, radians, at scale=1
    warp_radial: float = 0.05         # max radial breathe, fraction of radius
    warp_color_gain: float = 0.32     # how strongly warp amount tints color

    # Animation preview
    animate: bool = True
    anim_size: int = 380
    anim_frames: int = 20
    anim_fps: float = 12.0
    anim_twinkle: float = 0.16

    seed: Optional[int] = 7


# ============================================================
# palette.py
# ============================================================

DEFAULT_STOPS: List[Tuple[float, Tuple[int, int, int]]] = [
    (0.00, (108, 23, 201)),   # ultraviolet
    (0.33, (13, 22, 90)),     # deep blue
    (0.66, (255, 255, 255)),  # white
    (1.00, (64, 224, 240)),   # cyan
]


class Palette:
    def __init__(self, stops: Optional[Sequence[Tuple[float, Tuple[int, int, int]]]] = None):
        stops = list(stops) if stops else DEFAULT_STOPS
        stops = sorted(stops, key=lambda s: s[0])
        self._positions = np.array([p for p, _ in stops], dtype=np.float64)
        self._colors = np.array([c for _, c in stops], dtype=np.float64) / 255.0

    def sample(self, t) -> np.ndarray:
        t = np.clip(np.asarray(t, dtype=np.float64), 0.0, 1.0)
        r = np.interp(t, self._positions, self._colors[:, 0])
        g = np.interp(t, self._positions, self._colors[:, 1])
        b = np.interp(t, self._positions, self._colors[:, 2])
        return np.stack([r, g, b], axis=-1)

    def sample_cyclic(self, theta) -> np.ndarray:
        theta = np.asarray(theta, dtype=np.float64)
        t = (theta / (2.0 * np.pi)) % 1.0
        n = len(self._colors)
        positions = np.concatenate([np.arange(n) / n, [1.0]])
        colors = np.concatenate([self._colors, self._colors[:1]], axis=0)
        r = np.interp(t, positions, colors[:, 0])
        g = np.interp(t, positions, colors[:, 1])
        b = np.interp(t, positions, colors[:, 2])
        return np.stack([r, g, b], axis=-1)


# ============================================================
# imgstats.py
# ============================================================

def sigma_clipped_stats(data, sigma=3.0, max_iters=5):
    values = data[np.isfinite(data)].ravel()
    if values.size == 0:
        return 0.0, 1.0
    for _ in range(max_iters):
        med = np.median(values)
        std = np.std(values)
        if std == 0:
            break
        keep = np.abs(values - med) <= sigma * std
        if keep.all():
            break
        values = values[keep]
        if values.size < 10:
            break
    return float(np.median(values)), float(max(np.std(values), 1e-6))


def normalize_stretch(values, background, noise, scale=4.0, percentiles=(1.0, 99.5)):
    z = (values - background) / max(noise, 1e-6)
    stretched = np.arcsinh(z / scale)
    finite = stretched[np.isfinite(stretched)]
    if finite.size == 0:
        return np.zeros_like(values, dtype=np.float64)
    lo, hi = np.percentile(finite, percentiles)
    if hi <= lo:
        hi = lo + 1.0
    out = (stretched - lo) / (hi - lo)
    return np.clip(np.nan_to_num(out, nan=0.0), 0.0, 1.0)


def detail_field(data, smooth_sigma=2.0):
    safe = np.nan_to_num(data, nan=np.nanmedian(data) if np.isfinite(data).any() else 0.0)
    smoothed = ndimage.gaussian_filter(safe, smooth_sigma)
    gy, gx = np.gradient(smoothed)
    grad_mag = np.hypot(gx, gy)
    med = np.median(grad_mag) + 1e-9
    d = np.log1p(grad_mag / med)
    d -= d.min()
    peak = d.max()
    if peak > 0:
        d = d / peak
    return d, gx, gy


def find_bright_sources(data, background, noise, threshold_sigma=8.0,
                         min_separation=9, max_sources=150):
    safe = np.nan_to_num(data, nan=background)
    footprint = np.ones((min_separation, min_separation), dtype=bool)
    local_max = ndimage.maximum_filter(safe, footprint=footprint, mode="nearest") == safe
    bright_enough = safe > (background + threshold_sigma * noise)
    ys, xs = np.nonzero(local_max & bright_enough)
    if len(xs) == 0:
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64), np.array([])
    peaks = safe[ys, xs]
    order = np.argsort(peaks)[::-1][:max_sources]
    return ys[order], xs[order], peaks[order]


# ============================================================
# mesh.py (Voronoi only -- "no hexagons yet")
# ============================================================

class Mesh:
    @property
    def n_cells(self) -> int:
        raise NotImplementedError

    def assign(self, xs, ys) -> np.ndarray:
        raise NotImplementedError


class VoronoiAdaptiveMesh(Mesh):
    def __init__(self, seeds_xy, image_shape):
        self._tree = cKDTree(seeds_xy)
        self.seeds = seeds_xy
        self.image_shape = image_shape

    @property
    def n_cells(self) -> int:
        return len(self.seeds)

    def assign(self, xs, ys) -> np.ndarray:
        pts = np.stack([np.asarray(xs, dtype=np.float64),
                         np.asarray(ys, dtype=np.float64)], axis=-1)
        _, idx = self._tree.query(pts, k=1)
        return np.asarray(idx, dtype=np.int64)


def generate_voronoi_mesh(detail, target_cells=4000, relax_iters=2, rng=None):
    rng = rng or np.random.default_rng()
    ny, nx = detail.shape
    weights = detail + 0.08
    flat_w = weights.ravel()

    u = np.clip(rng.random(flat_w.size), 1e-12, 1.0)
    keys = -np.log(u) / flat_w
    k = min(target_cells, flat_w.size)
    idx = np.argpartition(keys, k - 1)[:k]

    ys0, xs0 = np.unravel_index(idx, detail.shape)
    seeds = np.stack([xs0.astype(np.float64), ys0.astype(np.float64)], axis=-1)
    seeds += rng.uniform(-0.5, 0.5, seeds.shape)

    step = max(1, int(round(max(nx, ny) / 700)))
    gyy, gxx = np.mgrid[0:ny:step, 0:nx:step].astype(np.float64)
    gw = weights[0:ny:step, 0:nx:step].ravel()
    gpts = np.stack([gxx.ravel(), gyy.ravel()], axis=-1)

    for _ in range(relax_iters):
        tree = cKDTree(seeds)
        _, labels = tree.query(gpts, k=1)
        wsum = np.bincount(labels, weights=gw, minlength=len(seeds))
        xsum = np.bincount(labels, weights=gw * gpts[:, 0], minlength=len(seeds))
        ysum = np.bincount(labels, weights=gw * gpts[:, 1], minlength=len(seeds))
        has_pts = wsum > 1e-12
        new_seeds = seeds.copy()
        new_seeds[has_pts, 0] = xsum[has_pts] / wsum[has_pts]
        new_seeds[has_pts, 1] = ysum[has_pts] / wsum[has_pts]
        seeds = new_seeds

    seeds[:, 0] = np.clip(seeds[:, 0], 0, nx - 1)
    seeds[:, 1] = np.clip(seeds[:, 1], 0, ny - 1)
    return VoronoiAdaptiveMesh(seeds, detail.shape)


def compute_cell_stats(image, mesh, n_cells):
    ny, nx = image.shape
    yy, xx = np.mgrid[0:ny, 0:nx].astype(np.float64)
    labels = mesh.assign(xx.ravel(), yy.ravel())
    flux = image.ravel()
    valid = np.isfinite(flux)
    labels = labels[valid]
    flux = flux[valid]
    counts = np.bincount(labels, minlength=n_cells).astype(np.float64)
    counts_safe = np.where(counts == 0, 1.0, counts)
    sums = np.bincount(labels, weights=flux, minlength=n_cells)
    mean = sums / counts_safe
    sums2 = np.bincount(labels, weights=flux ** 2, minlength=n_cells)
    std = np.sqrt(np.maximum(sums2 / counts_safe - mean ** 2, 0.0))
    return mean, std


# ============================================================
# shading.py
# ============================================================

def cell_colors(mean_flux, std_flux, background, noise, palette, rng,
                 jitter_strength=0.06, alpha_floor=0.16):
    t = normalize_stretch(mean_flux, background, noise)
    shot = np.sqrt(np.clip(mean_flux - background, 0.0, None) + 1.0)
    shot_norm = shot / (np.percentile(shot, 95) + 1e-9)
    jitter = rng.normal(0.0, jitter_strength, size=t.shape) * np.clip(shot_norm, 0.0, 1.5)
    t_j = np.clip(t + jitter, 0.0, 1.0)
    colors = palette.sample(t_j)

    contrast = std_flux / (np.abs(mean_flux) + noise + 1e-6)
    finite = contrast[np.isfinite(contrast)]
    c_lo, c_hi = (np.percentile(finite, [5.0, 95.0]) if finite.size else (0.0, 1.0))
    if c_hi <= c_lo:
        c_hi = c_lo + 1.0
    c_t = np.clip((np.nan_to_num(contrast) - c_lo) / (c_hi - c_lo), 0.0, 1.0)

    combo = 0.55 * c_t + 0.45 * t_j
    alpha = np.clip(alpha_floor + (1.0 - alpha_floor) * combo, 0.0, 1.0)
    rgba = np.concatenate([colors, alpha[:, None]], axis=1)
    return rgba, t_j


# ============================================================
# glow.py
# ============================================================

def build_glow_sources(ys, xs, peaks, background, noise, rng,
                        base_core=1.6, base_glow=14.0, flux_power=0.35):
    sources = []
    if len(xs) == 0:
        return sources
    rel = np.clip((peaks - background) / max(noise, 1e-6), 1.0, None)
    scale = rel ** flux_power
    scale = scale / (np.median(scale) + 1e-9)
    for x, y, s in zip(xs, ys, scale):
        sources.append(dict(
            x=float(x), y=float(y),
            r_core=base_core * float(s),
            r_glow=base_glow * float(s),
            phase=float(rng.uniform(0.0, 2 * np.pi)),
            rate=float(rng.uniform(0.7, 1.3)),
        ))
    return sources


def render_glow_layer(canvas_shape, sources, to_canvas, palette,
                       time_phase=0.0, twinkle=0.0):
    h, w = canvas_shape
    layer = np.zeros((h, w, 4), dtype=np.float32)
    for src in sources:
        cx, cy, cs = to_canvas(src["x"], src["y"])
        r_core = max(src["r_core"] * cs, 1e-3)
        r_glow = max(src["r_glow"] * cs, 1e-3)
        twinkle_mod = 1.0 + twinkle * np.sin(time_phase * src["rate"] + src["phase"])
        half = int(max(6, r_glow * 4))
        x0, x1 = int(max(0, cx - half)), int(min(w, cx + half))
        y0, y1 = int(max(0, cy - half)), int(min(h, cy + half))
        if x1 <= x0 or y1 <= y0:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float64)
        dx, dy = xx - cx, yy - cy
        r = np.hypot(dx, dy)
        theta = np.arctan2(dy, dx)
        core = np.exp(-(r / r_core) ** 2)
        env = np.clip(np.exp(-(r / r_glow) ** 2) * twinkle_mod, 0.0, 1.0)
        hue = palette.sample_cyclic(theta)
        color = hue * (1 - core[..., None]) + np.array([1.0, 1.0, 1.0]) * core[..., None]
        region = layer[y0:y1, x0:x1]
        light = (color * env[..., None]).astype(np.float32)
        region[..., :3] = 1.0 - (1.0 - region[..., :3]) * (1.0 - light)
        region[..., 3] = np.clip(region[..., 3] + env, 0.0, 1.0).astype(np.float32)
        layer[y0:y1, x0:x1] = region
    return layer


# ============================================================
# warp.py -- NEW: stars bend the mesh around them
# ============================================================

def build_warp_sources(ys, xs, peaks, background, noise, rng,
                        base_radius=46.0, swirl_strength=0.95,
                        radial_strength=0.25, flux_power=0.35):
    """One space-warp generator per detected star. `radius`/`swirl`/
    `radial` all scale with the same rel**flux_power brightness measure
    glow.py uses for its own sizing, so brighter stars warp a wider area
    more strongly. `m` (angular fold count), a random spin direction, and
    a slow, per-star `rate`/`phase` give each star's field its own
    character and let it precess independently over an animation."""
    sources = []
    if len(xs) == 0:
        return sources
    rel = np.clip((peaks - background) / max(noise, 1e-6), 1.0, None)
    scale = rel ** flux_power
    scale = scale / (np.median(scale) + 1e-9)
    for x, y, s in zip(xs, ys, scale):
        spin = 1.0 if rng.random() < 0.5 else -1.0
        sources.append(dict(
            x=float(x), y=float(y),
            radius=max(base_radius * float(s), 1.0),
            swirl=spin * swirl_strength * float(s),
            radial=radial_strength * float(s),
            m=int(rng.integers(2, 5)),
            phase=float(rng.uniform(0.0, 2 * np.pi)),
            rate=float(rng.uniform(0.12, 0.35)),  # slow precession, not twinkle-fast
        ))
    return sources


def apply_warp(src_x, src_y, sources, to_canvas, canvas_size,
                time_phase=0.0, pad_factor=4.0):
    """src_x, src_y: 2D arrays (out_h, out_w) of source-image pixel
    coordinates, as built in `_label_image` -- i.e. what each output
    pixel would sample from *without* any warp. `to_canvas(x, y)` is the
    same source->canvas mapping glow.py uses, used here only to find
    which window of the output grid a given star could plausibly reach.

    For each star: r/theta are computed around it in true source-pixel
    space, a Gaussian envelope in r sets how strong the effect is at that
    distance, and an m-fold cosine in theta (slowly precessing with
    `time_phase`) modulates both a twirl (rotate the point around the
    star -- the gravitational-lensing-swirl trick) and a radial breathe
    (push/pull along the same angle). Both are genuine functions of r AND
    theta, not a uniform radial pull. Returns the warped coordinates plus
    a [0,1] warp-amount field and the local displacement angle, both used
    downstream to tint color by how much the fabric moved.
    """
    ss_w, ss_h = canvas_size
    dx_field = np.zeros_like(src_x)
    dy_field = np.zeros_like(src_y)
    mag_field = np.zeros_like(src_x)
    for src in sources:
        cx, cy, cs = to_canvas(src["x"], src["y"])
        half = max(6.0, src["radius"] * pad_factor * cs)
        x0, x1 = int(max(0, cx - half)), int(min(ss_w, cx + half))
        y0, y1 = int(max(0, cy - half)), int(min(ss_h, cy + half))
        if x1 <= x0 or y1 <= y0:
            continue
        sx = src_x[y0:y1, x0:x1]
        sy = src_y[y0:y1, x0:x1]
        rx, ry = sx - src["x"], sy - src["y"]
        r = np.hypot(rx, ry)
        theta = np.arctan2(ry, rx)
        env = np.exp(-(r / src["radius"]) ** 2)
        wobble = np.cos(src["m"] * theta - src["rate"] * time_phase + src["phase"])
        swirl = src["swirl"] * env * (0.82 + 0.18 * wobble)
        radial = src["radial"] * src["radius"] * env * wobble
        new_r = np.clip(r + radial, 0.0, None)
        new_theta = theta + swirl
        ddx = new_r * np.cos(new_theta) - rx
        ddy = new_r * np.sin(new_theta) - ry
        dx_field[y0:y1, x0:x1] += ddx
        dy_field[y0:y1, x0:x1] += ddy
        mag_field[y0:y1, x0:x1] += np.abs(swirl) + np.abs(radial) / max(src["radius"], 1e-6)
    warp_angle = np.arctan2(dy_field, dx_field)
    warp_mag = np.clip(mag_field / 1.1, 0.0, 1.0)
    return src_x + dx_field, src_y + dy_field, warp_mag, warp_angle


# ============================================================
# render.py
# ============================================================

def fit_output_size(image_shape, out_size):
    ny, nx = image_shape
    aspect = nx / ny
    if aspect >= 1:
        return out_size, max(1, int(round(out_size / aspect)))
    return max(1, int(round(out_size * aspect))), out_size


def _make_to_canvas(image_shape, ss_w, ss_h) -> Callable[[float, float], Tuple[float, float, float]]:
    src_ny, src_nx = image_shape

    def to_canvas(x, y):
        return x * (ss_w / src_nx), y * (ss_h / src_ny), (ss_w / src_nx)

    return to_canvas


def _label_image(mesh, out_h, out_w, image_shape, to_canvas=None,
                  warp_sources=None, time_phase=0.0):
    src_ny, src_nx = image_shape
    yy, xx = np.mgrid[0:out_h, 0:out_w].astype(np.float64)
    src_x = (xx + 0.5) * (src_nx / out_w)
    src_y = (yy + 0.5) * (src_ny / out_h)
    warp_mag = np.zeros((out_h, out_w), dtype=np.float64)
    warp_angle = np.zeros((out_h, out_w), dtype=np.float64)
    if warp_sources:
        src_x, src_y, warp_mag, warp_angle = apply_warp(
            src_x, src_y, warp_sources, to_canvas, (out_w, out_h), time_phase)
    labels = mesh.assign(src_x.ravel(), src_y.ravel()).reshape(out_h, out_w)
    return labels.astype(np.int32), warp_mag, warp_angle


def _seam_darken(rgb, labels, strength):
    seam = np.zeros(labels.shape, dtype=bool)
    seam[:, :-1] |= labels[:, :-1] != labels[:, 1:]
    seam[:, 1:] |= labels[:, :-1] != labels[:, 1:]
    seam[:-1, :] |= labels[:-1, :] != labels[1:, :]
    seam[1:, :] |= labels[:-1, :] != labels[1:, :]
    out = rgb.copy()
    out[seam] *= (1.0 - strength)
    return out


def render_base_layer(image, mesh, cell_rgba, out_w, out_h, supersample,
                       background_rgb, seam_strength, palette=None,
                       warp_sources=None, warp_color_gain=0.5, time_phase=0.0):
    ss_w, ss_h = out_w * supersample, out_h * supersample
    to_canvas = _make_to_canvas(image.shape, ss_w, ss_h)
    labels, warp_mag, warp_angle = _label_image(
        mesh, ss_h, ss_w, image.shape, to_canvas, warp_sources, time_phase)

    rgb = cell_rgba[labels, :3].astype(np.float32)
    alpha = cell_rgba[labels, 3].astype(np.float32)
    bg = np.array(background_rgb, dtype=np.float32)
    rgb = rgb * alpha[..., None] + bg * (1.0 - alpha[..., None])

    if palette is not None and warp_sources:
        tint = palette.sample_cyclic(warp_angle).astype(np.float32)
        w = (warp_mag[..., None] * warp_color_gain).astype(np.float32)
        rgb = rgb * (1.0 - w) + tint * w

    rgb = _seam_darken(rgb, labels, seam_strength)
    return rgb, (ss_w, ss_h)


def composite_glow(base_rgb, image_shape, ss_size, glow_sources, palette,
                    supersample, time_phase=0.0, twinkle=0.0):
    ss_w, ss_h = ss_size
    to_canvas = _make_to_canvas(image_shape, ss_w, ss_h)
    glow_layer = render_glow_layer((ss_h, ss_w), glow_sources, to_canvas, palette,
                                    time_phase=time_phase, twinkle=twinkle)
    final = 1.0 - (1.0 - base_rgb) * (1.0 - glow_layer[..., :3] * glow_layer[..., 3:4])
    return np.clip(final, 0.0, 1.0)


def to_pil(rgb, out_w, out_h, supersample):
    img = Image.fromarray((rgb * 255.0 + 0.5).astype(np.uint8), mode="RGB")
    if supersample > 1:
        img = img.resize((out_w, out_h), Image.LANCZOS)
    return img


def render(image, mesh, cell_rgba, glow_sources, palette, warp_sources=None,
           out_size=1500, supersample=2, background_rgb=None, seam_strength=0.30,
           time_phase=0.0, twinkle=0.0, warp_color_gain=0.5):
    out_w, out_h = fit_output_size(image.shape, out_size)
    if background_rgb is None:
        background_rgb = tuple(np.asarray(palette.sample(0.0)) * 0.12)
    base_rgb, ss_size = render_base_layer(
        image, mesh, cell_rgba, out_w, out_h, supersample, background_rgb,
        seam_strength, palette=palette, warp_sources=warp_sources,
        warp_color_gain=warp_color_gain, time_phase=time_phase)
    final_rgb = composite_glow(
        base_rgb, image.shape, ss_size, glow_sources, palette, supersample,
        time_phase=time_phase, twinkle=twinkle)
    return to_pil(final_rgb, out_w, out_h, supersample)


# ============================================================
# animate.py -- GIF preview
# ============================================================

def build_preview_gif(image, mesh, cell_rgba, glow_sources, warp_sources, palette,
                       path, size=380, n_frames=20, fps=12.0, twinkle=0.16,
                       background_rgb=None, seam_strength=0.30, supersample=2,
                       warp_color_gain=0.5):
    """Unlike the original (mesh-only) animator, the mesh layer can no
    longer be rendered once and held fixed: because the warp field itself
    evolves with `time_phase`, every frame re-labels the mesh under that
    frame's warped coordinates, then composites that frame's glow/twinkle
    on top."""
    out_w, out_h = fit_output_size(image.shape, size)
    if background_rgb is None:
        background_rgb = tuple(np.asarray(palette.sample(0.0)) * 0.12)

    frames = []
    for i in range(n_frames):
        phase = 2.0 * np.pi * i / n_frames
        base_rgb, ss_size = render_base_layer(
            image, mesh, cell_rgba, out_w, out_h, supersample, background_rgb,
            seam_strength, palette=palette, warp_sources=warp_sources,
            warp_color_gain=warp_color_gain, time_phase=phase)
        final_rgb = composite_glow(
            base_rgb, image.shape, ss_size, glow_sources, palette, supersample,
            time_phase=phase, twinkle=twinkle)
        img = to_pil(final_rgb, out_w, out_h, supersample)
        frames.append(img.convert("P", palette=Image.ADAPTIVE, colors=220))

    duration_ms = int(round(1000.0 / fps))
    frames[0].save(path, save_all=True, append_images=frames[1:],
                    duration=duration_ms, loop=0)


# ============================================================
# Data: synthetic star field, or a real FITS if you have one
# ============================================================

def make_synthetic_image(ny=760, nx=960, n_stars=130, seed=3):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:ny, 0:nx].astype(np.float64)
    background = 100.0 + 30.0 * np.sin(xx / 200.0) * np.cos(yy / 300.0)
    image = background.copy()
    for _ in range(n_stars):
        x, y = rng.uniform(20, nx - 20), rng.uniform(20, ny - 20)
        flux = rng.pareto(1.5) * 300.0 + 50.0
        sigma = rng.uniform(1.2, 2.5)
        image += flux * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma ** 2))
    image += rng.normal(0.0, np.sqrt(np.clip(image, 1.0, None)))
    return image.astype(np.float64)


def load_fits_image(fits_path):
    """Best-effort real-FFI loader. Returns None (never raises) if astropy
    or the file isn't available, so callers fall back to synthetic data."""
    try:
        from astropy.io import fits
    except ImportError:
        return None
    try:
        with fits.open(fits_path) as hdul:
            for hdu in hdul:
                data = getattr(hdu, "data", None)
                if data is not None and getattr(data, "ndim", 0) == 2:
                    name = (hdu.name or "").upper()
                    if not any(tag in name for tag in ("UNCERT", "ERROR", "ERR", "QUALITY", "DQ")):
                        return np.asarray(data, dtype=np.float64)
            return np.asarray(hdul[1].data, dtype=np.float64)
    except Exception as exc:
        print(f"Could not load {fits_path!r} ({exc}); using synthetic image instead.")
        return None


# ============================================================
# Batch pipeline: one image or a directory tree of FITS files
# ============================================================

FITS_SUFFIXES = (".fits", ".fit", ".fts", ".fits.gz", ".fit.gz", ".fts.gz")


def discover_fits_files(input_path: Path, recursive: bool = True) -> List[Path]:
    """Return supported FITS files from a file or directory, in stable order."""
    input_path = Path(input_path).expanduser()
    if input_path.is_file():
        name = input_path.name.lower()
        if name.endswith(FITS_SUFFIXES):
            return [input_path]
        raise ValueError(f"Input file does not have a supported FITS extension: {input_path}")
    if not input_path.is_dir():
        raise FileNotFoundError(f"Input path does not exist: {input_path}")

    candidates = input_path.rglob("*") if recursive else input_path.glob("*")
    return sorted(
        (p for p in candidates if p.is_file() and p.name.lower().endswith(FITS_SUFFIXES)),
        key=lambda p: str(p).lower(),
    )


def safe_output_stem(path: Path) -> str:
    """Remove FITS extensions (including .fits.gz) for a clean output name."""
    name = path.name
    lower = name.lower()
    for suffix in (".fits.gz", ".fit.gz", ".fts.gz", ".fits", ".fit", ".fts"):
        if lower.endswith(suffix):
            return name[:-len(suffix)]
    return path.stem


def render_one_image(image: np.ndarray, cfg: RenderConfig, seed: Optional[int],
                     output_png: Path, output_gif: Optional[Path] = None,
                     show: bool = False) -> None:
    """Run the existing artwork pipeline for one 2-D image and save its results."""
    if image.ndim != 2:
        raise ValueError(f"Expected a 2-D FITS image; got shape {image.shape}")
    if not np.isfinite(image).any():
        raise ValueError("Image contains no finite pixel values")
    image = np.asarray(image, dtype=np.float64)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    if output_gif is not None:
        output_gif.parent.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(seed)
    palette = Palette()

    # 1. Background & noise
    bg_level, noise = sigma_clipped_stats(image)
    print(f"  Image shape: {image.shape} | Background: {bg_level:.2f} | Noise: {noise:.2f}")

    # 2. Detail field + adaptive mesh
    detail, gx, gy = detail_field(image)
    mesh = generate_voronoi_mesh(
        detail, target_cells=cfg.target_cells,
        relax_iters=cfg.relax_iters, rng=rng
    )
    print(f"  Mesh cells: {mesh.n_cells}")

    # 3. Per-cell statistics & colors
    mean, std = compute_cell_stats(image, mesh, mesh.n_cells)
    cell_rgba, _ = cell_colors(
        mean, std, bg_level, noise, palette, rng,
        jitter_strength=cfg.jitter_strength,
        alpha_floor=cfg.alpha_floor,
    )

    # 4. Bright sources -> glow and warp
    ys, xs, peaks = find_bright_sources(
        image, bg_level, noise,
        threshold_sigma=cfg.star_threshold_sigma,
        min_separation=cfg.star_min_separation,
        max_sources=cfg.star_max_sources,
    )
    glow_sources = build_glow_sources(ys, xs, peaks, bg_level, noise, rng)
    warp_sources = build_warp_sources(
        ys, xs, peaks, bg_level, noise, rng,
        base_radius=cfg.warp_base_radius,
        swirl_strength=cfg.warp_swirl,
        radial_strength=cfg.warp_radial,
    ) if cfg.warp_enabled else []
    print(f"  Bright sources: {len(glow_sources)} | Warp enabled: {cfg.warp_enabled}")

    # 5. Render and save still image
    result = render(
        image, mesh, cell_rgba, glow_sources, palette,
        warp_sources=warp_sources,
        out_size=cfg.out_size,
        supersample=cfg.supersample,
        seam_strength=cfg.seam_strength,
        warp_color_gain=cfg.warp_color_gain,
    )
    result.save(output_png)
    print(f"  Saved PNG: {output_png}")
    if show:
        display(result)

    # 6. Optional animated preview
    if output_gif is not None and cfg.animate:
        build_preview_gif(
            image, mesh, cell_rgba, glow_sources, warp_sources, palette,
            str(output_gif), size=cfg.anim_size, n_frames=cfg.anim_frames,
            fps=cfg.anim_fps, twinkle=cfg.anim_twinkle,
            seam_strength=cfg.seam_strength,
            warp_color_gain=cfg.warp_color_gain,
        )
        print(f"  Saved GIF: {output_gif}")


def process_fits_batch(input_path: Path, output_dir: Path,
                       cfg: Optional[RenderConfig] = None,
                       recursive: bool = True, make_gifs: bool = True,
                       max_files: Optional[int] = None,
                       show: bool = False) -> Tuple[int, int]:
    """Process FITS files found under input_path; return (successes, failures)."""
    input_path = Path(input_path).expanduser()
    output_dir = Path(output_dir).expanduser()
    cfg = cfg or RenderConfig()
    files = discover_fits_files(input_path, recursive=recursive)
    if max_files is not None:
        files = files[:max_files]
    if not files:
        print(f"No supported FITS files found under: {input_path}")
        return 0, 0

    # Check the optional dependency once, and give an actionable error.
    try:
        import astropy.io.fits  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "Reading FITS files requires Astropy. Install it with: pip install astropy"
        ) from exc

    print(f"Found {len(files)} FITS file(s). Output directory: {output_dir}")
    successes = failures = 0
    for index, fits_path in enumerate(files, start=1):
        print(f"\n[{index}/{len(files)}] {fits_path}")
        try:
            image = load_fits_image(str(fits_path))
            if image is None:
                raise ValueError("Could not read a 2-D science image from this FITS file")

            # Mirror the input subfolder layout under the output directory so
            # same-named files in different folders do not overwrite each other.
            if input_path.is_dir():
                relative_parent = fits_path.parent.relative_to(input_path)
            else:
                relative_parent = Path()
            target_dir = output_dir / relative_parent
            stem = safe_output_stem(fits_path)
            png_path = target_dir / f"{stem}_art.png"
            gif_path = target_dir / f"{stem}_art.gif" if make_gifs and cfg.animate else None

            # A deterministic but distinct seed per file makes runs reproducible
            # without giving every image exactly the same random mesh.
            file_seed = None if cfg.seed is None else cfg.seed + index - 1
            render_one_image(image, cfg, file_seed, png_path, gif_path, show=show)
            successes += 1
        except Exception as exc:
            failures += 1
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            print("  Continuing with the next FITS file.")

    print(f"\nBatch finished: {successes} succeeded, {failures} failed.")
    return successes, failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Create space-art renders from one FITS file or a directory of FITS files."
    )
    parser.add_argument(
        "--input", "-i", type=Path, default=None,
        help="FITS file or directory containing FITS files. Omit to use the synthetic demo image.",
    )
    parser.add_argument(
        "--output-dir", "-o", type=Path, default=Path("tess_art_outputs"),
        help="Directory for generated PNG/GIF files (default: tess_art_outputs).",
    )
    parser.add_argument(
        "--no-recursive", action="store_true",
        help="Only search the selected directory, not its subdirectories.",
    )
    parser.add_argument(
        "--no-animation", action="store_true",
        help="Save PNG images only; skip GIF generation.",
    )
    parser.add_argument(
        "--max-files", type=int, default=None,
        help="Optional limit for a quick test run.",
    )
    parser.add_argument(
        "--show", action="store_true",
        help="Display each generated image (useful in a notebook).",
    )
    args = parser.parse_args(argv)

    if args.max_files is not None and args.max_files < 1:
        parser.error("--max-files must be at least 1")

    cfg = RenderConfig()
    if args.no_animation:
        cfg.animate = False

    if args.input is None:
        # Preserve the original no-argument demo behavior, but save to a
        # portable output directory rather than a machine-specific location.
        print("No --input supplied; using the synthetic demo image.")
        image = make_synthetic_image()
        output_png = args.output_dir / "synthetic_art.png"
        output_gif = args.output_dir / "synthetic_art.gif" if cfg.animate else None
        try:
            render_one_image(
                image, cfg, cfg.seed, output_png, output_gif, show=args.show
            )
            return 0
        except Exception as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}")
            return 1

    try:
        successes, failures = process_fits_batch(
            args.input, args.output_dir, cfg=cfg,
            recursive=not args.no_recursive,
            make_gifs=not args.no_animation,
            max_files=args.max_files,
            show=args.show,
        )
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
