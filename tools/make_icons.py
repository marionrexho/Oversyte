"""Render PNG app icons (same design as web/icon.svg) with OpenCV."""
from pathlib import Path

import cv2
import numpy as np

WEB = Path(__file__).resolve().parent.parent / "web"
BLUE = (255, 140, 79)  # BGR of #4f8cff
WHITE = (255, 255, 255)


def eye(size: int, bg: bool, color, pad=0.0) -> np.ndarray:
    ss = 4  # supersample for smooth edges
    S = size * ss
    img = np.zeros((S, S, 4), np.uint8)
    if bg:
        img[:] = (*BLUE, 255)
    s = S / 64 * (1 - 2 * pad)
    o = S * pad
    pts_top, pts_bot = [], []
    for t in np.linspace(0, 1, 60):
        # quadratic beziers matching the SVG path
        x = (1 - t) ** 2 * 8 + 2 * (1 - t) * t * 32 + t ** 2 * 56
        yt = (1 - t) ** 2 * 32 + 2 * (1 - t) * t * 2 + t ** 2 * 32
        yb = (1 - t) ** 2 * 32 + 2 * (1 - t) * t * 62 + t ** 2 * 32
        pts_top.append((o + x * s, o + yt * s))
        pts_bot.append((o + x * s, o + yb * s))
    poly = np.array(pts_top + pts_bot[::-1], np.int32)
    cv2.polylines(img, [poly], True, (*color, 255), int(4.5 * s), cv2.LINE_AA)
    cv2.circle(img, (int(o + 32 * s), int(o + 32 * s)), int(7.5 * s), (*color, 255), -1, cv2.LINE_AA)
    return cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)


for n in (180, 192, 512):
    cv2.imwrite(str(WEB / f"icon-{n}.png"), eye(n, True, WHITE, pad=0.1)[:, :, :3])
cv2.imwrite(str(WEB / "badge-72.png"), eye(72, False, WHITE))  # monochrome status-bar badge
print("icons written to", WEB)
