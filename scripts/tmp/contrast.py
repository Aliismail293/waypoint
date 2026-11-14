"""WCAG contrast of the UI's text colours on the glass panels (worst case: over the blurred blobs)."""


def rgb(h: str) -> tuple[float, ...]:
    return tuple(int(h[i : i + 2], 16) for i in (1, 3, 5))


def mix(top: tuple[float, ...], bottom: tuple[float, ...], alpha: float) -> tuple[float, ...]:
    return tuple(t * alpha + b * (1 - alpha) for t, b in zip(top, bottom, strict=True))


def lum(c: tuple[float, ...]) -> float:
    def f(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (f(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


page = rgb("#ECEFF6")
blob1 = mix(rgb("#A5B4FC"), page, 0.45)  # strongest blob under a panel
blob2 = mix(rgb("#FCD34D"), page, 0.30)
white = (255, 255, 255)
grounds = {
    "glass on page": mix(white, page, 0.68),
    "glass on indigo blob": mix(white, blob1, 0.68),
    "glass on amber blob": mix(white, blob2, 0.68),
}
texts = {
    "ink": "#141A2E",
    "ink-2": "#3B4256",
    "muted": "#4F576B",
    "primary": "#4338CA",
    "danger-text": "#9B1C1C",
    "success": "#0E7A43",
}
worst = 99.0
for gname, g in grounds.items():
    for tname, t in texts.items():
        r = ratio(rgb(t), g)
        worst = min(worst, r)
        print(f"{gname:22s} {tname:12s} {r:5.2f} {'ok' if r >= 4.5 else 'FAIL'}")
pairs = {
    "white on primary (buttons, active tab)": (white, rgb("#4338CA")),
    "primary-dark on primary-soft (badges)": (rgb("#312E81"), rgb("#E0E7FF")),
    "white on danger-free user bubble": (white, rgb("#4338CA")),
    "warn text on notice": (rgb("#92400E"), rgb("#FEF3C7")),
}
for name, (a, b) in pairs.items():
    r = ratio(a, b)
    worst = min(worst, r)
    print(f"{name:42s} {r:5.2f} {'ok' if r >= 4.5 else 'FAIL'}")
print("worst:", round(worst, 2))
