"""Composite grid — łączy N klatek w jeden ponumerowany obraz siatki.

Port z instagram_bot/actions/reels_core.py::_build_composite_image.
CPU/dysk only — nie dotyka telefonu, można wołać z osobnego wątku.
"""
from __future__ import annotations
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def build_composite_grid(frames: list, out_path: str | Path,
                         cols: int = 2, thumb_w: int = 320,
                         crop_top_pct: float = 0.035,
                         crop_bottom_pct: float = 0.09,
                         quality: int = 92) -> str | None:
    """
    Args:
      frames: lista klatek (BGR ndarray, np. z FrameBuffer)
      out_path: gdzie zapisać JPEG
      cols: kolumny siatki (2 = klasyk). Przy 1 klatce wymuszane cols=1
        (N tiles = N). Przy nieparzystej liczbie — pad czarnym do pełnego rzędu.
      thumb_w: szerokość pojedynczej klatki po resize
      crop_top_pct / crop_bottom_pct: procentowo obcinane paski UI
        (status bar u góry ~3.5%, pasek akcji rolki u dołu ~9%)
      quality: JPEG quality (92 zamiast 100 = ~40% mniejszy plik, bez różnicy dla AI)

    Zwraca ścieżkę do zapisanego pliku (str) albo None przy błędzie.
    """
    if not frames:
        return None
    try:
        import cv2
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        logger.error("build_composite_grid: brak zależności: %s", exc)
        return None

    try:
        font = _load_font()

        tiles = []
        for frame in frames:
            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            top = int(h * crop_top_pct)
            bottom = h - int(h * crop_bottom_pct)
            img = img.crop((0, top, w, bottom))
            aspect = img.height / img.width
            img = img.resize((thumb_w, int(thumb_w * aspect)), Image.LANCZOS)
            tiles.append(img)

        # 1 klatka → 1 kolumna (N tiles = N; bez pustego kafelka obok)
        # 2+: cols z argumentu; pad tylko do pełnego rzędu siatki
        grid_cols = 1 if len(tiles) == 1 else max(1, int(cols))
        n_real = len(tiles)
        rows = (n_real + grid_cols - 1) // grid_cols
        tile_w, tile_h = tiles[0].size
        # czarne kafelki tylko jako wypełnienie ostatniego rzędu (bez numerów)
        while len(tiles) < rows * grid_cols:
            tiles.append(Image.new("RGB", (tile_w, tile_h), (0, 0, 0)))

        composite = Image.new("RGB", (tile_w * grid_cols, tile_h * rows))
        draw = ImageDraw.Draw(composite)
        for idx, tile in enumerate(tiles, start=1):
            r, c = divmod(idx - 1, grid_cols)
            x1, y1 = c * tile_w, r * tile_h
            composite.paste(tile, (x1, y1))
            # numeruj tylko prawdziwe klatki — padding bez ramki/numeru
            if idx > n_real:
                continue
            # czerwona ramka + numer w rogu (żeby AI mogło referować konkretny kafelek)
            draw.rectangle([x1, y1, x1 + tile_w - 1, y1 + tile_h - 1],
                           outline=(255, 0, 0), width=4)
            _draw_number(draw, x1, y1, str(idx), font)

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        composite.save(out_path, format="JPEG", quality=quality, optimize=True)
        return str(out_path)
    except Exception as exc:
        logger.debug("build_composite_grid: błąd: %s", exc)
        return None


def fit_jpeg_box(src, out_path: str | Path, *,
                 max_w: int = 900, max_h: int = 1950,
                 quality: int = 90, max_upscale: float = 2.0) -> str | None:
    """Przeskaluj obraz do pudełka max_w×max_h (JPEG), zachowując proporcje.

    `src` — ścieżka, Path albo bajty (PNG/JPEG z screenshotu).
    Domyślne pudełko 900×1950 = poprzednie scrcpy 720 × 1.25 (proporcje phone).
    Skala w dół: mieści się w pudełku. Skala w górę: max `max_upscale`
    (domyślnie 2×) — małych cropów nie rozdmuchujemy ponad limit.
    Zwraca ścieżkę albo None przy błędzie.
    """
    # walidacja wejścia — bez sensownych wymiarów pudełka nie ma co skalować
    if src is None or max_w < 8 or max_h < 8:
        return None
    try:
        from io import BytesIO
        from PIL import Image
    except ImportError as exc:
        logger.error("fit_jpeg_box: brak zależności: %s", exc)
        return None
    try:
        # bajty z screenshotu albo plik już na dysku
        if isinstance(src, (bytes, bytearray)):
            img = Image.open(BytesIO(src))
        else:
            p = Path(src)
            if not p.is_file():
                return None
            img = Image.open(p)
        img = img.convert("RGB")
        w, h = img.size
        if w < 1 or h < 1:
            return None
        # contain w pudełku + twardy limit upscale (nie powiększaj drobnicy ×12)
        scale = min(max_w / w, max_h / h, float(max_upscale))
        # już mieści się i nie wymaga upscale — i tak zapisz JPEG (konwersja PNG→JPEG)
        if scale >= 0.999 and scale <= 1.001 and w <= max_w and h <= max_h:
            nw, nh = w, h
        else:
            nw = max(1, int(round(w * scale)))
            nh = max(1, int(round(h * scale)))
            # po zaokrągleniu nie wychodź poza pudełko
            if nw > max_w or nh > max_h:
                scale2 = min(max_w / nw, max_h / nh)
                nw = max(1, int(round(nw * scale2)))
                nh = max(1, int(round(nh * scale2)))
            if (nw, nh) != (w, h):
                img = img.resize((nw, nh), Image.LANCZOS)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, format="JPEG", quality=quality, optimize=True)
        return str(out_path)
    except Exception as exc:
        logger.debug("fit_jpeg_box: błąd: %s", exc)
        return None


def stitch_vertical_crops(png_parts: list[bytes],
                          crops: list[tuple[int, int, int, int]],
                          out_path: str | Path, quality: int = 90) -> str | None:
    """Sklej pionowo wycinki z jednego lub wielu PNG.

    `crops[i]` = (x1, y1, x2, y2) względem `png_parts[i]` (albo pierwszego,
    gdy parts ma jeden element). Puste / zdegenerowane cropy pomijamy.
    Używane przy discovery niszy: media z otwarcia + opis bez kolejnego posta.
    """
    if not png_parts or not crops:
        return None
    try:
        from io import BytesIO
        from PIL import Image
    except ImportError as exc:
        logger.error("stitch_vertical_crops: brak zależności: %s", exc)
        return None
    try:
        strips = []
        for i, crop in enumerate(crops):
            raw = png_parts[i] if i < len(png_parts) else png_parts[0]
            img = Image.open(BytesIO(raw)).convert("RGB")
            x1, y1, x2, y2 = crop
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(img.width, x2), min(img.height, y2)
            if x2 - x1 < 8 or y2 - y1 < 8:
                continue
            strips.append(img.crop((x1, y1, x2, y2)))
        if not strips:
            return None
        w = max(s.width for s in strips)
        # wyrównaj szerokość (różne cropy bywają pełnoekranowe)
        norm = []
        for s in strips:
            if s.width != w:
                s = s.resize((w, max(1, int(s.height * w / s.width))),
                             Image.LANCZOS)
            norm.append(s)
        total_h = sum(s.height for s in norm)
        out = Image.new("RGB", (w, total_h))
        y = 0
        for s in norm:
            out.paste(s, (0, y))
            y += s.height
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out.save(out_path, format="JPEG", quality=quality, optimize=True)
        return str(out_path)
    except Exception as exc:
        logger.debug("stitch_vertical_crops: błąd: %s", exc)
        return None


def annotate_grid(png_bytes: bytes, tiles: list[tuple[int, int, int, int]],
                  out_path: str | Path, quality: int = 90) -> str | None:
    """Zrzut ekranu profilu → obraz siatki z ponumerowanymi kafelkami.

    Numerację rysujemy MY, po realnych `bounds` kafelków z drzewa, zamiast
    prosić model, żeby sam policzył kafelki na surowym zrzucie. Model liczy
    je zawodnie (mylą go nagłówek, zakładki i ucięty ostatni wiersz), a nam
    numer musi się zgadzać co do jednego — po nim wskazujemy, w co tapnąć.

    Obraz jest przycinany do zakresu znalezionych kafelków, więc nagłówek
    profilu nie zjada połowy kadru ani tokenów.

    Zwraca ścieżkę do JPEG-a albo None (brak zależności / błąd / brak kafelków).
    """
    if not png_bytes or not tiles:
        return None
    try:
        from io import BytesIO
        from PIL import Image, ImageDraw
    except ImportError as exc:
        logger.error("annotate_grid: brak zależności: %s", exc)
        return None

    try:
        img = Image.open(BytesIO(png_bytes)).convert("RGB")
        w, h = img.size
        crop_top = max(0, min(t[1] for t in tiles))
        crop_bottom = min(h, max(t[3] for t in tiles))
        if crop_bottom <= crop_top:
            return None

        grid = img.crop((0, crop_top, w, crop_bottom))
        draw = ImageDraw.Draw(grid)
        font = _load_font(size=42)
        for idx, (x1, y1, x2, y2) in enumerate(tiles, start=1):
            ry1, ry2 = y1 - crop_top, y2 - crop_top
            draw.rectangle([x1, ry1, x2 - 1, ry2 - 1], outline=(255, 0, 0), width=5)
            _draw_number(draw, x1, ry1, str(idx), font)

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        grid.save(out_path, format="JPEG", quality=quality, optimize=True)
        return str(out_path)
    except Exception as exc:
        logger.debug("annotate_grid: błąd: %s", exc)
        return None


def _load_font(size: int = 30):
    from PIL import ImageFont
    for p in ("C:\\Windows\\Fonts\\arialbd.ttf",
              "C:\\Windows\\Fonts\\arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_number(draw, x1: int, y1: int, label: str, font) -> None:
    tb = draw.textbbox((0, 0), label, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad = 6
    box_x1, box_y1 = x1 + 4, y1 + 4
    box_x2, box_y2 = box_x1 + tw + pad * 2, box_y1 + th + pad * 2
    draw.rectangle([box_x1, box_y1, box_x2, box_y2], fill=(0, 0, 0))
    draw.text((box_x1 + pad - tb[0], box_y1 + pad - tb[1]),
              label, fill=(255, 255, 0), font=font)
