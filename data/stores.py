"""Data stores — na razie pliki JSONL. Interfejs ten sam co planowany
adapter Supabase (M9) — łatwo podmienić.

`RawProfilesStore` = surowa pula targetów (harvest / target_scraper).
`TargetCandidatesStore` = kolejka do `profile_interact` (po filtrze HTTP).
`RawFilterStateStore` = wyniki promocji raw → candidates (ok/remove).

Pipeline:
  scraper/harvest append → raw_profiles.jsonl
  → profile_filter (HTTP/proxy) → target_candidates.jsonl
  → profile.pick_from_store → profile_interact

Przykład wierszy: `data/accounts/_example/raw_profiles.example.jsonl`
"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RawProfilesStore:
    """Surowe username'y → `accounts/<id>/raw_profiles.jsonl` (append-only).

    Docelowy wiersz (target_scraper / list.harvest_usernames)::

        {
          "ts": "2026-08-08T11:11:59",   # ISO lokalne, dokłada store
          "source": "reel_likers",       # etykieta listy / source_tag
          "username": "pd77712",         # klucz dedupu
          "has_ring": false,             # per nick (story ring)
          "from_target": "basenispa.pl", # konto źródłowe scrape'u
          "content_kind": "reel"         # reel|post|null (followers → null)
        }

    Dedup NIE jest polem w pliku — to zachowanie zapisu (`dedup_days`):
    drugi zapis tego samego `username` w oknie (lub forever) jest pomijany.

    Legacy / inne pluginy mogą dokładać `from_author`, `niche_score`, `verdict`
    (reels_niche harvest) — nie kolidują ze schematem powyżej.
    """
    account_id: str
    data_root: Path
    _path: Path = field(init=False)

    def __post_init__(self):
        d = self.data_root / "accounts" / self.account_id
        d.mkdir(parents=True, exist_ok=True)
        self._path = d / "raw_profiles.jsonl"

    @property
    def file_path(self) -> Path:
        return self._path

    def add_many(self, usernames: list[str], source: str, *,
                 dedup_days: int | None = None, **extra) -> int:
        """Zapisz jednym append. Zwraca ile faktycznie nowych wierszy."""
        return self.add_rows([{"username": u} for u in usernames], source,
                             dedup_days=dedup_days, **extra)

    def add_rows(self, rows: list[dict], source: str, *,
                 dedup_days: int | None = None, **extra) -> int:
        """Jak `add_many`, ale każdy wiersz może nieść własne pola (np. `has_ring`).

        Potrzebne, bo aktywna relacja jest cechą KONKRETNEGO profilu, a nie
        całej porcji — a to od niej zależy późniejszy wybór kandydatów do
        interakcji („ring = profil publiczny").

        `extra` z wołającego (np. from_target, content_kind) trafia do każdego
        wiersza; pola w `row` nadpisują extra przy kolizji kluczy.

        Dedup między sesjami:
        - `dedup_days is None` — pomiń nick już obecny w pliku (forever)
        - `dedup_days <= 0` — bez dedupu historycznego (tylko w tej porcji)
        - `dedup_days > 0` — pomiń nick widziany w ostatnich N dniach
        """
        if not rows:
            return 0
        # Zbiór już zapisanych nicków (wg okna dedup) — raz na wywołanie
        already = self._usernames_in_window(dedup_days)
        seen_local: set[str] = set()
        saved = 0
        ts = _now_iso()
        with self._path.open("a", encoding="utf-8") as f:
            for row in rows:
                u = (row or {}).get("username")
                if not u or u in seen_local:
                    continue
                seen_local.add(u)
                # Duplikat względem historii / wcześniejszych sesji
                if u in already:
                    continue
                already.add(u)
                # Pola wiersza nadpisują extra (np. has_ring per nick)
                rec = {"ts": ts, "source": source, **extra, **row}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                saved += 1
        return saved

    def _usernames_in_window(self, dedup_days: int | None) -> set[str]:
        """Nicki już w pliku — pełny zbiór albo okno czasowe."""
        if dedup_days is not None and dedup_days <= 0:
            return set()
        if not self._path.exists():
            return set()
        cutoff = None
        if dedup_days is not None and dedup_days > 0:
            cutoff = time.time() - dedup_days * 86400
        out: set[str] = set()
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            u = row.get("username")
            if not u:
                continue
            if cutoff is not None:
                try:
                    t = time.mktime(time.strptime(row["ts"], "%Y-%m-%dT%H:%M:%S"))
                    if t < cutoff:
                        continue
                except Exception:
                    # Brak/zły ts — traktuj jako „w oknie" (bezpieczniej pominąć)
                    pass
            out.add(u)
        return out

    def read_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        out = []
        for l in self._path.read_text(encoding="utf-8").splitlines():
            if not l.strip():
                continue
            try:
                out.append(json.loads(l))
            except Exception:
                continue
        return out

    def unique_usernames(self) -> set:
        return {r["username"] for r in self.read_all() if r.get("username")}

    def preview(self, *, limit: int = 50, unique: bool = True) -> dict:
        """Podgląd dla panelu: najnowsze wpisy (unikalne username od końca pliku).

        Zwraca `{total, unique, shown, items, path}` — items to lista dictów
        z polami username/source/from_target/content_kind/ts/has_ring.
        """
        all_rows = self.read_all()
        total = len(all_rows)
        unique_n = len({r.get("username") for r in all_rows if r.get("username")})
        # Plik jest append-only — najnowsze na końcu
        newest_first = list(reversed(all_rows))
        if unique:
            seen: set[str] = set()
            picked = []
            for r in newest_first:
                u = r.get("username")
                if not u or u in seen:
                    continue
                seen.add(u)
                picked.append(r)
            newest_first = picked
        lim = max(1, min(int(limit), 500))
        items = []
        for r in newest_first[:lim]:
            items.append({
                "username": r.get("username"),
                "source": r.get("source"),
                "from_target": r.get("from_target"),
                "content_kind": r.get("content_kind"),
                "ts": r.get("ts"),
                "has_ring": bool(r.get("has_ring")),
            })
        return {
            "total": total,
            "unique": unique_n,
            "shown": len(items),
            "items": items,
            "path": str(self._path),
        }

    def mark_outcome(self, username: str, outcome: str, **extra) -> bool:
        """Ustaw `interacted_at` + `outcome` na OSTATNIM wierszu nicka.

        Harvest zostaje append-only — to jedyna mutacja pliku (rewrite).
        `outcome` np. private | gate_failed | match_failed | skipped |
        liked | followed | open_failed. Zwraca True gdy znaleziono wiersz.
        """
        if not username or not self._path.exists():
            return False
        raw_lines = self._path.read_text(encoding="utf-8").splitlines()
        # Zachowujemy kolejność; nieparsowalne linie zostawiamy bez zmian
        kept: list[str | dict] = []
        last_idx: int | None = None
        for line in raw_lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                kept.append(line)
                continue
            kept.append(row)
            if isinstance(row, dict) and row.get("username") == username:
                last_idx = len(kept) - 1
        if last_idx is None or not isinstance(kept[last_idx], dict):
            return False
        updated = {
            **kept[last_idx],
            "interacted_at": _now_iso(),
            "outcome": outcome,
            **extra,
        }
        kept[last_idx] = updated
        with self._path.open("w", encoding="utf-8") as f:
            for item in kept:
                if isinstance(item, dict):
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
                else:
                    f.write(str(item) + "\n")
        return True

    def pick(self, limit: int, exclude_usernames: set | None = None,
             not_added_within_s: float = 0.0,
             skip_interacted_within_s: float = 0.0) -> list[dict]:
        """Kandydaci z surowej puli — unikalne username, najnowsze pierwsze.

        - exclude_usernames: już odwiedzone (VisitedProfilesStore)
        - not_added_within_s: pomijaj wpisy z `ts` młodszym niż N sekund
        - skip_interacted_within_s: pomijaj nicki z `interacted_at` w oknie
          (hardening gdy visited-miss nie złapał dedupu)
        Zwraca listę dictów gotowych pod `profile_queue` (username + meta).
        """
        exclude = exclude_usernames or set()
        now = time.time()
        # Najnowsze na końcu pliku → odwróć, pierwszy wygrywa (unikalność)
        out: list[dict] = []
        seen: set[str] = set()
        for row in reversed(self.read_all()):
            u = row.get("username")
            if not u or u in exclude or u in seen:
                continue
            # Już interakcja w oknie — nie bierz też starszego wiersza tego nicka
            if skip_interacted_within_s > 0 and row.get("interacted_at"):
                try:
                    t = time.mktime(time.strptime(
                        row["interacted_at"], "%Y-%m-%dT%H:%M:%S"))
                    if now - t < skip_interacted_within_s:
                        seen.add(u)
                        continue
                except Exception:
                    seen.add(u)
                    continue
            if not_added_within_s > 0:
                ts = row.get("ts") or row.get("added_at")
                if ts:
                    try:
                        added = time.mktime(
                            time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
                        if now - added < not_added_within_s:
                            continue
                    except Exception:
                        pass
            seen.add(u)
            out.append({
                "username": u,
                "source": row.get("source"),
                "from_target": row.get("from_target"),
                "content_kind": row.get("content_kind"),
                "has_ring": bool(row.get("has_ring")),
                "ts": row.get("ts"),
                "added_at": row.get("ts") or row.get("added_at"),
                "outcome": row.get("outcome"),
                "interacted_at": row.get("interacted_at"),
            })
            if len(out) >= limit:
                break
        return out


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


@dataclass
class TargetCandidatesStore:
    """Pre-filtrowane profile do interakcji → `target_candidates.jsonl`.

    Zapisuje `profile_filter` (promocja z raw); bot czyta przez `pick`.

    Minimalny wiersz zgodny z `pick()` / `profile.pick_from_store`::

        {
          "username": "pd77712",              # wymagane
          "added_at": "2026-08-08T12:00:00",  # ISO; min_age_minutes
          "source": "reel_likers",            # opcjonalnie (audyt)
          "has_ring": false,                  # opcjonalnie (filtr UI)
          "from_target": "basenispa.pl",      # opcjonalnie
          "pre_filter_verdict": {}            # opcjonalnie
        }
    """
    account_id: str
    data_root: Path
    _path: Path = field(init=False)

    def __post_init__(self):
        d = self.data_root / "accounts" / self.account_id
        d.mkdir(parents=True, exist_ok=True)
        self._path = d / "target_candidates.jsonl"

    @property
    def file_path(self) -> Path:
        return self._path

    def read_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        return [json.loads(l) for l in self._path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def add(self, username: str, *, source: str = "profile_filter",
            dedup: bool = True, **extra) -> bool:
        """Dodaj kandydata. Przy `dedup=True` pomija nick już w pliku.

        Zwraca True gdy faktycznie zapisano wiersz.
        """
        if not username:
            return False
        if dedup:
            existing = {r.get("username") for r in self.read_all()
                        if r.get("username")}
            if username in existing:
                return False
        rec = {
            "username": username,
            "added_at": _now_iso(),
            "source": source,
            **extra,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True

    def add_many(self, rows: list[dict], *, source: str = "profile_filter",
                 dedup: bool = True) -> int:
        """Append wielu wierszy; każdy musi mieć `username`."""
        saved = 0
        for row in rows or []:
            u = (row or {}).get("username")
            if not u:
                continue
            extra = {k: v for k, v in row.items() if k != "username"}
            if self.add(u, source=row.get("source") or source,
                        dedup=dedup, **extra):
                saved += 1
        return saved

    def preview(self, *, limit: int = 50) -> dict:
        """Podgląd dla panelu — najnowsze na końcu pliku."""
        all_rows = self.read_all()
        newest_first = list(reversed(all_rows))
        lim = max(1, min(int(limit), 500))
        items = []
        for r in newest_first[:lim]:
            items.append({
                "username": r.get("username"),
                "source": r.get("source"),
                "from_target": r.get("from_target"),
                "added_at": r.get("added_at"),
                "has_ring": bool(r.get("has_ring")),
            })
        return {
            "total": len(all_rows),
            "shown": len(items),
            "items": items,
            "path": str(self._path),
        }

    def pick(self, limit: int, exclude_usernames: set | None = None,
             not_added_within_s: float = 0.0) -> list[dict]:
        """Zwraca do `limit` kandydatów.
        - exclude_usernames: pomijaj (np. już odwiedzone z VisitedProfilesStore)
        - not_added_within_s: pomijaj wpisy dodane w ostatnich N sekundach
                              (żeby nie kanibalizować pracy skryptu pre-filtracji
                              który może właśnie zapisuje)
        """
        exclude = exclude_usernames or set()
        now = time.time()
        out = []
        for row in self.read_all():
            u = row.get("username")
            if not u or u in exclude:
                continue
            if not_added_within_s > 0:
                try:
                    added = time.mktime(time.strptime(row["added_at"], "%Y-%m-%dT%H:%M:%S"))
                    if (now - added) < not_added_within_s:
                        continue
                except Exception:
                    pass
            out.append(row)
            if len(out) >= limit:
                break
        return out


@dataclass
class RawFilterStateStore:
    """Wyniki filtra HTTP per username → `raw_filter_state.jsonl`.

    `result`: ok (promowany) | remove (odrzucony). Skip sieciowy NIE trafia
    tu — username wraca do pending przy kolejnym runie.
    """
    account_id: str
    data_root: Path
    _path: Path = field(init=False)

    def __post_init__(self):
        d = self.data_root / "accounts" / self.account_id
        d.mkdir(parents=True, exist_ok=True)
        self._path = d / "raw_filter_state.jsonl"

    @property
    def file_path(self) -> Path:
        return self._path

    def read_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        out = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out

    def decided_usernames(self) -> set[str]:
        """Nicki z ostatecznym werdyktem ok/remove."""
        out: set[str] = set()
        for row in self.read_all():
            if row.get("result") in ("ok", "remove") and row.get("username"):
                out.add(row["username"])
        return out

    def mark(self, username: str, result: str, **extra) -> dict:
        """Append werdykt (ok/remove)."""
        rec = {
            "username": username,
            "ts": _now_iso(),
            "result": result,
            **extra,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec


@dataclass
class VisitedProfilesStore:
    """Profile które zostały odwiedzone przez profile.visit — z pełnym wynikiem
    (header, ai_verdict, action, scoring). Używane do dedup i statystyk."""
    account_id: str
    data_root: Path
    _path: Path = field(init=False)

    def __post_init__(self):
        d = self.data_root / "accounts" / self.account_id
        d.mkdir(parents=True, exist_ok=True)
        self._path = d / "visited_profiles.jsonl"

    @property
    def file_path(self) -> Path:
        return self._path

    def add(self, username: str, **fields) -> dict:
        rec = {"ts": _now_iso(), "username": username, **fields}
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def read_all(self) -> list[dict]:
        if not self._path.exists():
            return []
        return [json.loads(l) for l in self._path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def visited_within(self, days: int) -> set:
        """Set username'ów odwiedzonych w ostatnich N dni (dedup)."""
        cutoff = time.time() - days * 86400
        out = set()
        for row in self.read_all():
            try:
                t = time.mktime(time.strptime(row["ts"], "%Y-%m-%dT%H:%M:%S"))
                if t >= cutoff:
                    out.add(row["username"])
            except Exception:
                pass
        return out
