"""Legacy CLI filtra plików txt — cienki wrapper wokół `profile_filter.core`.

HTTP jak `TARGETY/proxy1.py`; transport curl_cffi+Chrome; IP rotuje na każdy request.
Preferuj: `python -m v2.cli filter-profiles --account=...`
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# umożliw import v2.* gdy uruchamiane z katalogu profile_filter/
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT.parent))

from v2.profile_filter.core import (  # noqa: E402
    FilterCriteria,
    InstagramScraper,
    load_proxies,
)

# Kompatybilność ze starym skryptem
limit = 100
filtered_file_name = "targety.txt"
target_file_name = "all2.txt"
FOLLOWERS_MIN = 60
FOLLOWERS_MAX = 20000
asfalty = True


def wybierz_proxy():
    proxies = load_proxies()
    opcje = list(proxies.keys()) + ["losowo"]
    print("Wybierz proxy:")
    for i, nazwa in enumerate(opcje, start=1):
        if nazwa == "losowo":
            opis = "losowo z obu powyższych"
        else:
            opis = proxies[nazwa].split("@")[-1]
        print(f"{i}. {nazwa} ({opis})")
    try:
        wybor = int(input("Podaj numer proxy: ")) - 1
    except ValueError:
        wybor = -1
    if 0 <= wybor < len(opcje):
        wybrane = opcje[wybor]
        print(f"Wybrano proxy: {wybrane}")
        return wybrane
    print("Nieprawidłowy numer. Spróbuj ponownie.")
    return wybierz_proxy()


def wybierz_folder():
    foldery = [f for f in os.listdir() if os.path.isdir(f)
               and f not in ("zcookies", "zskrypty", "__pycache__")]
    print("Wybierz folder, z którego chcesz przeprocesować pliki:")
    for i, folder in enumerate(foldery):
        print(f"{i + 1}. {folder}")
    wybor = int(input("Podaj numer folderu: ")) - 1
    if 0 <= wybor < len(foldery):
        wybrany_folder = foldery[wybor]
        print(f"Wybrano folder: {wybrany_folder}")
        return wybrany_folder, foldery
    print("Nieprawidłowy numer folderu. Spróbuj ponownie.")
    return wybierz_folder()


def process_users_txt(scraper: InstagramScraper, folder: str,
                      check_limit: int, max_saved: int):
    """Legacy I/O: all2.txt → targety.txt (jak historyczny proxy1)."""
    start_time = time.time()
    target_file = os.path.join(folder, target_file_name)
    filtered_file = os.path.join(folder, filtered_file_name)
    with open(target_file, "r", encoding="utf-8") as f:
        usernames = f.read().splitlines()

    for index, username in enumerate(usernames[:check_limit], start=1):
        elapsed_time = time.time() - start_time
        formatted_time = time.strftime("%H:%M:%S", time.gmtime(elapsed_time))
        print(f"{index}. {username} | {formatted_time}")
        result = scraper.scrape_user(username)

        if result == "skip":
            usernames.remove(username)
            usernames.append(username)
            with open(target_file, "w", encoding="utf-8") as f:
                f.write("\n".join(usernames) + "\n")
            print("Skip...")
            print("----------------------------")
        elif result == "ok":
            usernames.remove(username)
            with open(target_file, "w", encoding="utf-8") as f:
                f.write("\n".join(usernames) + "\n")
            with open(filtered_file, "a", encoding="utf-8") as f:
                f.write(username + "\n")
        elif result == "remove":
            usernames.remove(username)
            with open(target_file, "w", encoding="utf-8") as f:
                f.write("\n".join(usernames) + "\n")

        if scraper.stats.saved >= max_saved:
            print(f"Osiągnięto limit {max_saved} zapisanych użytkowników. "
                  "Zatrzymuję skrypt.")
            break


if __name__ == "__main__":
    tryb_proxy = wybierz_proxy()
    criteria = FilterCriteria(
        followers_min=FOLLOWERS_MIN,
        followers_max=FOLLOWERS_MAX,
        reject_arabic_bio=asfalty,
    )
    scraper = InstagramScraper(tryb_proxy, criteria=criteria)
    folder, _all_folders = wybierz_folder()
    max_saved = int(input("Ilość oczekiwanych targetów: "))
    process_users_txt(scraper, folder, limit, max_saved)
    print(folder)
    print(f"Łączna liczba requestów: {scraper.stats.total_requests}")
    print(f"Liczba sprawdzonych {scraper.stats.checked}")
    print(f"Liczba pominiętych: {scraper.stats.skip}")
    print(f"Liczba zapisanych użytkowników: {scraper.stats.saved}")
