"""Dociągnięcie wykresów postów, które dumpnęły overlay bez scrolla.

Zawsze swipe na wykres przed zrzutem. Bez scrcpy. Komentarze PL.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2].parent))

from v2.calibration.recon_pro_dash._dump_all_items import (  # noqa: E402
    _ensure_filter,
    _scroll_chart,
    _tap_views_row,
)
from v2.calibration.recon_pro_dash._dump_pass2 import leave  # noqa: E402
from v2.calibration.recon_pro_dash._dump_typical_windows import (  # noqa: E402
    _blob,
    _flat,
    _set_range_content,
    _set_range_overview,
)
from v2.calibration.recon_pro_dash._recon import (  # noqa: E402
    acquire_lock,
    connect,
    dump,
    unlock,
    wake,
)

# 7d: 42/54/278 nie ma na liście — tylko 14 i 30
NEED = (
    (394, 30),
    (42, 14), (42, 30),
    (54, 14), (54, 30),
    (278, 14), (278, 30),
)


def main() -> int:
    lock = acquire_lock()
    try:
        dev = connect()
        print("wake", wake(dev), "unlock", unlock(dev))
        leave(dev)
        last_days = None
        for views, days in NEED:
            if last_days != days:
                leave(dev)
                if days == 14:
                    _set_range_overview(dev, "14 dni")
                    _set_range_content(dev, "30 dni")
                else:
                    _set_range_content(dev, "30 dni")
                _ensure_filter(dev, "Posty")
                last_days = days
            if "ogólne" not in _blob(_flat(dev)):
                leave(dev)
                _ensure_filter(dev, "Posty")
            print(f"→ post {views} {days}d")
            _tap_views_row(dev, views, 0)
            time.sleep(0.8)
            _scroll_chart(dev)
            _scroll_chart(dev)
            dump(dev, f"41_post_{views}_{days}d", f"PASS3 wykres post {views} {days}")
            leave(dev)
        return 0
    finally:
        try:
            lock.release()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
