# nokturno-core

Sdílené jádro Nokturna. Hledání a streamy ve WebShare, Sosáči, HellSpy, Luně
a na trackerech přes Prowlarr — bez vazby na hostitele.

Čistý Python 3, jen standardní knihovna. Žádný import z `xbmc*` ani
z `homeassistant`, což hlídá test.

## Kdo z toho žije

| Konzument | Bere | Poznámka |
|---|---|---|
| [plugin.video.nokturno](../plugin.video.nokturno) | `lib/` | doplněk pro Kodi; logiku enginu má rozpuštěnou v `default.py` |
| [nokturno-ha](../../../HA/Nokturno%20HA) | `lib/` + `engine.py` | integrace pro Home Assistant |
| nokturno-stremio | vše | doplněk pro Stremio, teprve vzniká |

## Pravidlo

**Jádro se edituje jen tady.** U konzumentů je vysypaná kopie, která se při
příštím rozeslání přepíše. Oprava udělaná u nich se tiše ztratí.

```bash
python3 tools/sync_core.py --check          # co by se změnilo, nezapisuje
python3 tools/sync_core.py --check --diff   # totéž i s obsahem změn
python3 tools/sync_core.py                  # rozešle všem
python3 tools/sync_core.py kodi             # jen jednomu
```

Doplněk pro Kodi načítá `resources/lib` ploše přes `sys.path`, ne jako balíček,
takže se mu relativní importy při zápisu zplošťují. Ostatní berou soubory beze změny.

## Testy

```bash
python3 -m unittest discover -s tests -v
```

Nesahají na síť. Ověřují tvar jádra a to, co na něm konzumenti vyžadují jmenovitě —
tedy přesně věci, které se při rozesílání dají tiše rozbít.

## Struktura

```
nokturno_core/
├── engine.py       jádro: hledání, sloučení zdrojů, streamy, řazení
├── lib/
│   ├── const.py    klíče nastavení a výchozí hodnoty
│   ├── luna_api.py cinemeta_api.py tmdb_api.py     katalogy a metadata
│   ├── webshare_api.py sosac_direct.py hellspy_api.py   zdroje streamů
│   ├── prowlarr.py qbittorrent.py                  torrenty
│   ├── streams.py mediainfo.py                     rozbor a řazení streamů
│   └── store.py sync.py stats.py trakt_api.py enrich.py sosac_api.py
└── ...
```

## Vznik

Vyčleněno 2026-09-12 z integrace pro Home Assistant, kde `engine.py` už bylo bez
jakékoli vazby na hostitele. Do té doby existovala knihovna jako dvě ruční kopie,
které se stihly rozejít v šesti souborech. Sloučení vzalo z každé větve to, co
měla navíc: cachování a řazení z větve pro Kodi, ukazatel průběhu v `enrich`
z větve pro Home Assistant.
