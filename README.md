# nokturno-core

[![Ko-fi](https://img.shields.io/badge/Ko--fi-podpo%C5%99%20autora-ff5e5b?logo=ko-fi&logoColor=white)](https://ko-fi.com/matata86) [![PayPal](https://img.shields.io/badge/PayPal-paypal.me%2Fmatata86-00457C?logo=paypal&logoColor=white)](https://paypal.me/matata86) [![Bitcoin](https://img.shields.io/badge/Bitcoin-BTC-f7931a?logo=bitcoin&logoColor=white)](#podpora)

Sdílené jádro Nokturna. Hledání a streamy ve WebShare, Sosáči, HellSpy, Sledujteto, FastShare, Luně
a na trackerech přes Prowlarr — bez vazby na hostitele.

Čistý Python 3, jen standardní knihovna. Žádný import z `xbmc*` ani
z `homeassistant`, což hlídá test.

> **Patří k sobě:** nad tímhle jádrem stojí [**Nokturno pro Kodi**](https://github.com/matata86/plugin.video.nokturno), [**Nokturno pro Home Assistant**](https://github.com/matata86/nokturno-ha) a [**Nokturno pro Stremio**](https://github.com/matata86/nokturno-stremio) (i Nuvio) — tři samostatné doplňky nad stejnými zdroji.

## Kdo z toho žije

| Konzument | Bere | Poznámka |
|---|---|---|
| [plugin.video.nokturno](https://github.com/matata86/plugin.video.nokturno) | `lib/` + `engine.py` (ploše) | doplněk pro Kodi; `default.py` nad jádrem staví `KodiEngine` |
| [nokturno-ha](https://github.com/matata86/nokturno-ha) | `lib/` + `engine.py` | integrace pro Home Assistant |
| [nokturno-stremio](https://github.com/matata86/nokturno-stremio) | vše | doplněk pro Stremio |

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
│   ├── crash.py                                    hlášení o pádech (otisk, mazání citlivých údajů, fronta)
│   └── store.py sync.py stats.py trakt_api.py enrich.py sosac_api.py
└── ...
```

## Vznik

Vyčleněno 2026-09-12 z integrace pro Home Assistant, kde `engine.py` už bylo bez
jakékoli vazby na hostitele. Do té doby existovala knihovna jako dvě ruční kopie,
které se stihly rozejít v šesti souborech. Sloučení vzalo z každé větve to, co
měla navíc: cachování a řazení z větve pro Kodi, ukazatel průběhu v `enrich`
z větve pro Home Assistant.

---

## Podpora

[![Podpoř Nokturno — Ko-fi, PayPal, Bitcoin](.github/podpora.png)](https://ko-fi.com/matata86)

- **Ko-fi:** https://ko-fi.com/matata86
- **PayPal:** https://paypal.me/matata86
- **Bitcoin:** `bc1qhjwt8xxmuym0xsd50yfpvjph00386uz73gqwlc`

## Výkon (od 2026-09-14)

- `Engine.streams()` se pěti zdrojů (Luna/Sosáč, WebShare, HellSpy, Sledujteto, FastShare) ptá
  **souběžně** (`ThreadPoolExecutor`), pořadí výsledků drží kvůli párování v `_merge_direct`.
  Líné klienty (`ws`, `hs`, `st`, `fs`, `sosac`) zakládá před spuštěním vláken.
- `original_titles()` se za jeden výpis počítá jednou (paměť v enginu, 5 min; po výpadku
  Wikidat jen 30 s, aby další výpis zkusil znovu) — dřív pětkrát, při výpadku Wikidat až 100 s navíc.
- WebShare: selhání loginu už nezamkne zdroj do restartu (`WS_RETRY_S = 60`); re-login jen když
  server odmítl (`WebshareApiError`), ne při síťové chybě.
- TMDB: `external_ids` + `images` jedním dotazem (`append_to_response`) — stránka katalogu 1 + 20
  požadavků místo 1 + 40; sezóny seriálu souběžně.
- Sosáč: hledání seriálů nad indexem (`sosac:tvindex`, jednou denně) místo 27 souborů a normalizace
  tisíců názvů při každém dotazu.
- `enrich`: jeden sdílený executor na proces (dřív nový na každé hledání s `shutdown(wait=False)` —
  desítky visících vláken) a dedup rozpracovaných dotazů na tentýž titul.
- Úložiště: značka `.nokturno-rev` se čte nejvýš jednou za minutu (`REV_TTL`); hledání Luny má jednu
  cache (v `LunaApi`), ne dvě.

## Údržba (2026-09-14)

- Jedna `human_size` a jedna `fold` ve `streams.py` (dřív 4× a 2× s různým zaokrouhlením/chováním);
  ostatní moduly je jen re-exportují, klienti importují dál z původních míst.
- Cinemeta jen přes `CinemetaApi` (`Engine.search_catalog` měl třetího vlastního klienta).
- User-Agent už netvrdí, že je Kodi nebo Home Assistant — `Nokturno (+github)`; `stats.send()`
  dostává `agent` od hostitele.
- Pryč diagnostické lešení Sledujteto (`last_keys`, `last_sample`, INFO logování), `HISTORY_MAX`,
  `_logged` v qBittorrentu, prázdná větev v `sosac_direct.streams`; `urllib.error` importovaný explicitně.
