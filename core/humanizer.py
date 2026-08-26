"""Humanizer — profil pauz per konto.

Ładuje YAML z `humanizer/profiles/<name>.yaml`, pozwala pobrać zakres
dla danego typu akcji i zasnąć na losowy czas z tego zakresu.

Nie używa `yaml` (żeby uniknąć zależności) — parser minimalny dla naszej
prostej gramatyki: `key: value`, `key: {min: X, max: Y}`, listy `[a, b]`.
Jeśli w projekcie i tak pojawi się PyYAML w innej ścieżce, można przełączyć.
"""
from __future__ import annotations
import time
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

PROFILES_DIR = Path(__file__).resolve().parent.parent / "humanizer" / "profiles"


class HumanizerError(Exception):
    pass


SPEED_MIN, SPEED_MAX = 0.1, 20.0

# Gestowe mikro-pauzy — setki na sesję. cli filtruje je w events.jsonl
# (chyba że V2_DEBUG_PAUSES / long_pause). Panel i tak trzyma je w QUIET_KINDS.
PAUSE_MICRO_KINDS = frozenset({"tap", "swipe", "between_steps"})


def should_log_pause(pause_kind: str, *, long_pause_added_s: float = 0.0,
                     debug: bool = False) -> bool:
    """Czy zapisać event `humanizer.pause` (telemetry / panel SSE).

    Domyślnie pomija tap/swipe/between_steps bez long_pause — nie zalewają
    events.jsonl. Debug albo rare kinds (after_launch, before_sleep, …) = True.
    """
    if debug:
        return True
    kind = str(pause_kind or "")
    if float(long_pause_added_s or 0) > 0:
        return True
    return kind not in PAUSE_MICRO_KINDS


@dataclass
class Humanizer:
    """Profil pauz + **globalna prędkość działania**.

    `speed` to jedno pokrętło nad wszystkimi pauzami, niezależne od tego, jak
    ustawione są poszczególne zakresy: `2.0` znaczy „dwa razy szybciej"
    (każda pauza o połowę krótsza), `0.5` — dwa razy wolniej. Zakresy w profilu
    zostają nietknięte, więc proporcje między rodzajami pauz się nie zmieniają;
    zmienia się tylko tempo całości.

    Dotyczy WYŁĄCZNIE pauz ludzkich — czekania na reakcję aplikacji (dojście
    dumpu, przejście ekranu, odstęp double-tapu) są techniczne i skalowanie ich
    tylko psułoby stabilność.
    """
    profile: dict = field(default_factory=dict)
    speed: float = 1.0
    on_pause: Callable | None = None
    _action_counter: int = 0
    _rng: random.Random = field(default_factory=random.Random)

    def __post_init__(self):
        self.speed = _clamp_speed(self.speed)

    @classmethod
    def load(cls, profile_name: str = "default", overrides: dict | None = None,
             profiles_dir: Path | None = None, seed: int | None = None,
             speed: float | None = None) -> "Humanizer":
        d = profiles_dir or PROFILES_DIR
        path = d / f"{profile_name}.yaml"
        if not path.exists():
            raise HumanizerError(f"profil humanizera nie istnieje: {path}")
        profile = _parse_simple_yaml(path.read_text(encoding="utf-8"))
        if overrides:
            profile = _deep_merge(profile, overrides)
        # kolejność: profil → override konta → argument jawny
        eff_speed = speed if speed is not None else profile.get("speed", 1.0)
        h = cls(profile=profile, speed=_clamp_speed(eff_speed))
        if seed is not None:
            h._rng = random.Random(seed)
        return h

    # ---- skalowanie ----
    def scale(self, seconds: float) -> float:
        """Przelicz czas ludzki na czas po uwzględnieniu prędkości."""
        return float(seconds) / self.speed

    def sleep(self, seconds: float, why: str = "", kind: str = "sleep",
              long_added: float = 0.0, source: str = "sleep") -> float:
        """Uśpij na `seconds` czasu LUDZKIEGO (czyli po przeskalowaniu krócej).

        Jedno wejście dla wszystkiego, co czeka „bo tak zrobiłby człowiek":
        oglądanie rolki, czytanie opisu, przerwa między profilami.
        Callback `on_pause` (cli → events.jsonl); filtr mikro-pauz w cli.
        """
        t = self.scale(seconds)
        if t > 0:
            time.sleep(t)
        self._emit_pause(kind=kind, drawn_s=float(seconds), slept_s=t, why=why,
                         source=source, long_added=long_added)
        return t

    def _emit_pause(self, kind: str, drawn_s: float, slept_s: float, why: str,
                    source: str, long_added: float) -> None:
        """Wyemituj event pauzy przez `on_pause` jeśli podpięty (filtr w cli)."""
        if self.on_pause is None:
            return
        self.on_pause(
            pause_kind=kind,
            why=why,
            drawn_s=round(float(drawn_s), 6),
            slept_s=round(float(slept_s), 6),
            speed=self.speed,
            action_n=self._action_counter,
            long_pause_added_s=round(float(long_added), 6),
            source=source,
        )

    def range_for(self, kind: str) -> tuple[float, float]:
        """Zakres [min, max] dla danego typu akcji z sekcji per_action.
        Jeśli brak — fallback do 'between_steps'."""
        pa = self.profile.get("per_action", {})
        entry = pa.get(kind) or pa.get("between_steps")
        if not entry:
            raise HumanizerError(f"brak zakresu dla '{kind}' i brak fallbacku 'between_steps'")
        return float(entry["min"]), float(entry["max"])

    def draw(self, kind: str) -> float:
        """Wylosuj czas pauzy dla danego typu akcji (bez czekania)."""
        lo, hi = self.range_for(kind)
        return self._rng.uniform(lo, hi)

    def pause(self, kind: str, why: str = "") -> float:
        """Uśpij aktualny wątek na losowy czas dla `kind`. Zwraca ile spał.

        Zwracamy czas PO przeskalowaniu prędkością — czyli tyle, ile realnie
        trwała pauza, a nie ile wylosowałby profil. Emituje event pauzy.
        """
        drawn = self.draw(kind)
        self._action_counter += 1
        # opcjonalnie długa przerwa co N akcji
        lp = self.profile.get("long_pause", {}).get("every_n_actions", {})
        long_added = 0.0
        if lp and self._action_counter > 0 and lp.get("n") and (self._action_counter % lp["n"] == 0):
            long_added = self._rng.uniform(lp["min"], lp["max"])
        return self.sleep(drawn + long_added, why=why, kind=kind,
                          long_added=long_added, source="pause")

    def wrap(self, step_run, step_name: str = "between_steps"):
        """Dekorator — pauza pre + wywołanie + pauza post 30% mniejsza.
        Do FlowRunnera zamiast wołania step.run(...) bezpośrednio."""
        def wrapped(ctx, params):
            self.pause(step_name)
            res = step_run(ctx, params)
            # krótsza pauza po (żeby nie sumować pełnych zakresów co krok)
            lo, hi = self.range_for("between_steps")
            self.sleep(self._rng.uniform(lo * 0.3, hi * 0.5),
                       kind=step_name, source="wrap_post")
            return res
        return wrapped


# ============================================================
# Minimalny parser YAML (bez PyYAML)
# ============================================================
_KEY_VAL_RE = re.compile(r"^(?P<indent>\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<value>.*?)\s*$")


def _parse_simple_yaml(text: str) -> dict:
    """Parser dla naszej gramatyki: klucz: wartość / klucz: {a: 1, b: 2} / [1, 2, 3].
    Zagnieżdżenia przez 2-spacjowe wcięcie."""
    lines = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]

    for raw in lines:
        # strip inline comments
        line = re.sub(r"\s+#.*$", "", raw)
        m = _KEY_VAL_RE.match(line)
        if not m:
            raise HumanizerError(f"nie umiem sparsować linii: {raw!r}")
        indent = len(m.group("indent"))
        key    = m.group("key")
        value  = m.group("value")

        # dostosuj stack do bieżącego wcięcia
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else root

        if value == "":
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_value(value)
    return root


def _parse_value(v: str) -> Any:
    v = v.strip()
    if v.startswith("{") and v.endswith("}"):
        # {min: 0.15, max: 0.4}
        body = v[1:-1].strip()
        d = {}
        for pair in _split_top_commas(body):
            k, _, val = pair.partition(":")
            d[k.strip()] = _parse_scalar(val.strip())
        return d
    if v.startswith("[") and v.endswith("]"):
        body = v[1:-1].strip()
        if not body:
            return []
        return [_parse_scalar(x.strip()) for x in _split_top_commas(body)]
    return _parse_scalar(v)


def _parse_scalar(v: str) -> Any:
    if v in ("true", "True"):  return True
    if v in ("false", "False"): return False
    if v in ("null", "None", "~"): return None
    try:    return int(v)
    except: pass
    try:    return float(v)
    except: pass
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def _split_top_commas(s: str) -> list[str]:
    """Split po przecinkach ale nie wewnątrz {} lub []."""
    out, buf, depth = [], [], 0
    for ch in s:
        if ch in "{[":  depth += 1
        elif ch in "}]": depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(buf)); buf = []
        else:
            buf.append(ch)
    if buf: out.append("".join(buf))
    return out


def _clamp_speed(v) -> float:
    """Prędkość poza rozsądkiem to albo literówka w configu, albo pomyłka
    o rząd wielkości — w obu wypadkach lepiej przyciąć niż stanąć na godzinę
    albo zasypać telefon gestami."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 1.0
    if v <= 0:
        return 1.0
    return max(SPEED_MIN, min(SPEED_MAX, v))


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
