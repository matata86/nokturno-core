#!/usr/bin/env python3
"""Rozešle jádro ze zdroje pravdy do konzumentů.

    python3 tools/sync_core.py --check          co by se změnilo, nezapisuje
    python3 tools/sync_core.py --check --diff   totéž, ale s celým diffem
    python3 tools/sync_core.py                  zapíše všem cílům
    python3 tools/sync_core.py kodi ha          zapíše jen vyjmenovaným

Doplněk pro Kodi načítá `resources/lib` ploše přes `sys.path`, ne jako balíček,
takže se mu relativní importy při zápisu zplošťují (`from .store` → `from store`,
v `engine.py` `from .lib.store` → `from store`). Ostatní cíle berou soubory tak, jak jsou.

Cesty cílů jsou relativní k tomuhle repu: všechny větve rodiny leží vedle něj
v `Nastroje/Nokturno/`. Cíl, který na disku není, se přeskočí — doplněk pro
Stremio zatím neexistuje.
"""
import argparse
import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "nokturno_core"

# from .modul import …  →  from modul import …   (jen uvnitř balíčku, ne stdlib);
# engine.py má knihovnu o úroveň níž: from .lib.modul import … → from modul import …
RELATIVE_IMPORT = re.compile(r"^from \.(?:lib\.)?(\w)", re.M)
# celý modul místo jmen z něj: from .lib import modul as x → import modul as x.
# Musí jít napřed — `RELATIVE_IMPORT` by z toho udělal `from lib import …` a balíček
# `lib` v ploché struktuře doplňku pro Kodi neexistuje.
PACKAGE_IMPORT = re.compile(r"^from \.lib import ", re.M)


class Target:
    def __init__(self, name, path, engine=False, flatten=False, package=False, note=""):
        self.name = name
        self.path = (ROOT / path).resolve()
        self.engine = engine      # bere i engine.py?
        self.flatten = flatten    # zploštit relativní importy?
        self.package = package    # je cíl samostatný balíček (chce i __init__.py jádra)?
        self.note = note

    @property
    def lib_dir(self):
        """Kam přijdou moduly z `nokturno_core/lib/`."""
        return self.path if self.flatten else self.path / "lib"

    @property
    def repo(self):
        """Kořen repa konzumenta — cíl leží u všech tří dvě úrovně pod ním."""
        return self.path.parent.parent

    def missing(self):
        """Chybí celý projekt? Samotná cílová složka se při zápisu vytvoří."""
        return not self.repo.exists()


TARGETS = [
    Target("kodi", "../Kodi/plugin.video.nokturno/resources/lib",
           engine=True, flatten=True,
           note="engine.py leží ploše vedle knihovny, default.py nad ním staví KodiEngine"),
    Target("ha", "../HA/nokturno-ha/custom_components/nokturno",
           engine=True, flatten=False,
           note="vlastní const.py si drží sám, jádro mu dodá lib/const.py"),
    Target("stremio", "../Stremio/nokturno-stremio/nokturno/core",
           engine=True, flatten=False, package=True,
           note="zatím neexistuje"),
]


def render(source: Path, flatten: bool) -> str:
    text = source.read_text(encoding="utf-8")
    if flatten:
        text = PACKAGE_IMPORT.sub("import ", text)
        text = RELATIVE_IMPORT.sub(r"from \1", text)
    return text


def plan(target: Target):
    """[(cílový soubor, nový obsah, stav)] pro jeden cíl."""
    jobs = []
    files = sorted(CORE.glob("lib/*.py"))
    if target.engine:
        files += [CORE / "engine.py", CORE / "__init__.py"]
    for source in files:
        if source.name == "__init__.py" and source.parent == CORE / "lib" and target.flatten:
            continue  # ploché načítání balíček nepotřebuje
        dest = (target.lib_dir / source.name) if source.parent.name == "lib" else (target.path / source.name)
        if target.flatten:
            dest = target.lib_dir / source.name   # engine.py ploše mezi knihovnou
        if source.name == "__init__.py" and source.parent == CORE and not target.package:
            continue  # hostitel (Kodi, HA) má vlastní __init__.py, nepřepisovat
        new = render(source, target.flatten)
        old = dest.read_text(encoding="utf-8") if dest.exists() else None
        state = "nový" if old is None else ("beze změny" if old == new else "změna")
        jobs.append((dest, new, state, old))
    return jobs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="*", help="které cíle; bez uvedení všechny")
    ap.add_argument("--check", action="store_true", help="jen vypsat, nezapisovat")
    ap.add_argument("--diff", action="store_true", help="vypsat i obsah změn")
    args = ap.parse_args()

    chosen = [t for t in TARGETS if not args.targets or t.name in args.targets]
    unknown = set(args.targets) - {t.name for t in TARGETS}
    if unknown:
        sys.exit(f"neznámý cíl: {', '.join(sorted(unknown))}")

    total = 0
    for target in chosen:
        if target.missing():
            print(f"\n{target.name}: přeskočeno, {target.repo} neexistuje ({target.note})")
            continue
        print(f"\n{target.name}: {target.path}")
        jobs = plan(target)
        changed = [j for j in jobs if j[2] != "beze změny"]
        for dest, new, state, old in jobs:
            if state == "beze změny":
                continue
            print(f"  {state:12} {dest.name}")
            if args.diff and old is not None:
                sys.stdout.writelines(difflib.unified_diff(
                    old.splitlines(True), new.splitlines(True),
                    fromfile=f"{target.name}/{dest.name}", tofile=f"core/{dest.name}"))
        if not changed:
            print("  vše aktuální")
        if not args.check:
            target.lib_dir.mkdir(parents=True, exist_ok=True)
            for dest, new, state, _old in changed:
                dest.write_text(new, encoding="utf-8")
        total += len(changed)

    if args.check:
        print(f"\ncelkem ke změně: {total} souborů (nic nezapsáno)")
    else:
        print(f"\nzapsáno: {total} souborů")


if __name__ == "__main__":
    main()
