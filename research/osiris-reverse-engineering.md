# OSIRIS (osirisai.live) — reverse-engineering notes

Target: `https://osirisai.live/?layers=maritime,cctv,cctv_previews,live_news,earthquakes,global_incidents,day_night,cables,sdk_sea,sdk_air,sdk_naval`

Date: 2026-09-18

Method: the site itself is blocked from this sandbox's egress proxy, so the live instance was only sampled through a third-party fetch service (cached snapshots, dates noted where used). Everything else comes from reading the platform's own source, which is public. The live build is `simplifaisoul/osiris` (branch `master`, last commit 2026-09-17, PR #376). A second, diverged fork (`carbon-evolution/osiris`, last commit 2026-07-27) was also read for comparison and is called "the fork" below.

---

## 1. TL;DR

- OSIRIS is a single Next.js 16 / React 19 app with a MapLibre GL 6.7 globe. There is no separate backend for the map: every feed is a Next.js route under `/api` that fetches a public keyless upstream, normalises it to JSON, and caches it. The browser polls those routes and pushes GeoJSON into MapLibre sources.
- The eleven `layers=` keys in your URL are exactly the app's default-on layers. The app writes them back into the address bar with `history.replaceState` 1.5 s after any change, so that URL is just "fresh load, nothing toggled".
- Three of the eleven keys draw nothing in the current build: `sdk_air` and `sdk_naval` toggle line layers that never receive features, and `cables` is only a data-loading flag whose lines are drawn by `sdk_sea`. `global_incidents` is not GDELT despite its route name; it is the GDACS disaster RSS.
- Marketing says "open-source Palantir alternative". Technically it is a feed aggregator and map renderer with an OSINT lookup panel bolted on. The "SDK" (Polybolos, Anduril Lattice adapter) is a type system plus an ingest endpoint that holds entities in a process-local `Map` and, on the public instance, contained zero entities when sampled.
- Hosting is one Docker Compose stack on a VPS behind Cloudflare: the Next app, an nginx tile cache, a small Express "intel" service (OpenSanctions + Wikidata), and an Umami analytics container that the app's middleware posts every visitor's IP to.
- Of the last 156 commits on the canonical repo, 88 are authored by "Gemini CLI". The codebase is heavily AI-written, and it shows in both directions: verbose, well-commented modules, and features whose names outlived their implementation.

---

## 2. What the URL means

`page.tsx` holds one `activeLayers` state object. On mount it reads `?layers=` and sets every key to `active.includes(key)`; on every change it debounces 1.5 s and rewrites the URL. Nothing else is persisted (view position comes from IP geolocation via `/api/geo` on each load).

Default-on keys in the live build, in declaration order:

| Key | Panel label | Panel group | Draws |
|---|---|---|---|
| `maritime` | Maritime / Naval | MARITIME | ports, chokepoints, AIS ships |
| `cctv` | CCTV Cameras | SURVEIL | camera dots |
| `cctv_previews` | Live Previews (child of cctv) | SURVEIL | live frame tiles at zoom ≥ 13 |
| `live_news` | Live News Feeds | SURVEIL | broadcaster dots, YouTube tiles at zoom ≥ 13 |
| `earthquakes` | Earthquakes | HAZARD | USGS quakes |
| `global_incidents` | Global Incidents | THREAT | GDACS disaster alerts |
| `day_night` | Day / Night Cycle | DISPLAY | solar terminator polygon |
| `cables` | (no toggle in panel) | — | loads static cable GeoJSON |
| `sdk_sea` | Maritime Lines | OSIRIS SDK | the cable lines |
| `sdk_air` | (no toggle in panel) | — | nothing |
| `sdk_naval` | (no toggle in panel) | — | nothing |

Everything else (flights, satellites, fires, weather, nuclear, malware, botnet C2, Cloudflare Radar, terrain) defaults off and is fetched only when toggled ("layer-aware loading": a `Set` of fetched keys prevents refetch; polling intervals are registered per active layer).

---

## 3. Architecture

### 3.1 Client

- `src/app/page.tsx` (~1,500 lines): owns `dataRef` (a mutable object holding every feed's array), `activeLayers`, panel state, and all fetch scheduling. Panels are `next/dynamic` lazy imports.
- `src/components/OsirisMap.tsx` (~3,000 lines): one MapLibre map. On style load it registers ~35 empty GeoJSON sources and ~100 layers up front; toggling a layer means `setData` on its source (empty `FeatureCollection` to hide) plus `setLayoutProperty visibility`. Popups are hand-built HTML strings with inline `onclick` handlers calling `window.openOsirisIntel(...)`.
- Basemap: CARTO `dark-matter-gl-style` for both themes in the live build (the fork uses CARTO Voyager for its light theme). Every `cartocdn.com` request is rewritten through `transformRequest` to `/api/proxy-tiles?url=...`, a same-origin proxy with a cartocdn host allowlist and a one-year Next fetch cache. A "SAT" toggle adds an ArcGIS `World_Imagery` raster source. Globe projection comes from MapLibre 6's native `setProjection({type:'globe'})`; a "MAP/2D" toggle switches to Mercator.
- MapLibre's worker is self-hosted at `/vendor/maplibre/<version>/` (copied from `node_modules` by `tools/prepare-map-worker.mjs` in `predev`/`prebuild`, and committed) with a custom Turbopack loader so the worker URL survives bundling.
- Two CSS themes ("core" light, "ghost" dark violet) via CSS variables; fonts JetBrains Mono + Inter; Tailwind 4 for layout; framer-motion for panel animation; lucide icons; `react-force-graph-2d` for the entity graph; `lightweight-charts` for markets; `hls.js` for camera video; `satellite.js` for TLE propagation.
- Total: ~49.9k lines of TS/TSX. Vitest suite exists for the lib layer (`*.test.ts` next to most `src/lib` modules).

### 3.2 API layer

~70 route files, 57 documented at `/docs` (a static catalogue in `src/app/docs/apiCatalog.ts`, kept in sync by hand). Conventions: GET reads, POST writes, JSON bodies, `Cache-Control` set per route (45–60 s for live feeds, a day for reference data), errors as `{error, detail}`. Nothing needs auth except `/api/sdk/ingest` and `/api/github-webhook`. AI routes (Gemini, via `@google/generative-ai`) are rate-limited to 5/min/IP.

Route groups: system (`health`, `stats`), aviation/space (`flights`, `aircraft`, `satellites`, `satellites/orbit`, `space-weather`, `astra`), earth (`earthquakes`, `fires`, `weather`, `air-quality`, `radar`, `sentinel`), geopolitical (`conflicts`, `frontlines`, `gdelt`, `gdelt-events`, `country-risk`, `region-dossier`), media/markets (`news`, `live-news`, `markets`, `markets/history`, `crypto`, `chain/daily`, `scm-suppliers`), surveillance/infra (`cctv` + `proxy`, `stream-status`, `resolve`, `texas/snapshot`; `infrastructure`; `maritime`; `arcgis`; `proxy-tiles`; `geo`; `directions`; `geosearch`; `flight-route`), cyber (`cyber-threats`, `cyber-attacks`, `malware`, `malware/stream` SSE, `cloudflare-radar`), OSINT toolkit (`osint/{dns,whois,certs,ip,shodan,bgp,mac,phone,github,leaks,hudsonrock,cve,sanctions,threats,sweep,crypto,username}`), `scanner` (proxy to a separate sidecar), `entity/expand`, `ai/{analyze,briefing,overview}`, `sdk/{ingest,stream}`, `github-webhook`.

### 3.3 Cross-cutting mechanisms

- **Upstream fetch**: `lib/stealthFetch.ts` wraps `fetch` with a random desktop/mobile User-Agent and a 10 s hard timeout. It also generates a fake "residential" IP from hard-coded ISP ranges, but the generated IP is never placed in a header, so it is dead code; only the UA rotates.
- **SSRF guard**: `lib/ssrf-guard.ts` canonicalises IPs, resolves hostnames, and rejects anything in RFC1918, loopback, link-local (cloud metadata), CGNAT, multicast, IPv4-mapped IPv6, etc. Used by the camera proxies and OSINT routes that fetch user-supplied hosts.
- **Source cache**: `lib/sourceCache.ts` is an in-memory TTL cache (default 30 min, 500 keys) with in-flight dedup and stale-on-error. Every CCTV region fetcher is wrapped in it.
- **Response snapshots**: hot routes (`/api/maritime`, `/api/stats`, `/api/cctv`) build one pre-serialised response per TTL and hand every caller the same string, because the author measured 15–19 s responses under load when each visitor recomputed.
- **Fetch pool**: `lib/fetch-pool.ts` limits region fan-out to 4 concurrent upstreams with a 12 s per-region budget.
- **Analytics**: `src/middleware.ts` runs on every page view (assets excluded) and fires two POSTs to an Umami container on the Docker network: a page view and a custom "Network Log" event whose payload is the visitor's IP (from `cf-connecting-ip`). Bounded to 2 s because an earlier version starved the app's own connection pool.
- **Headers**: HSTS, `X-Frame-Options: SAMEORIGIN`, nosniff, and a CSP of `default-src 'self' 'unsafe-inline' 'unsafe-eval' https: wss: data: blob:` which permits essentially everything over HTTPS.

### 3.4 Hosting and deployment

- `docker-compose.yml`: `osiris` (Next standalone on `node:22-alpine`, port 3000), `osiris-cache` (nginx on 8080 with a 10 GB, 365-day `proxy_cache` for `/proxy/tiles/<cartocdn host>/...`, everything else proxied to the app), `osiris-intel` (Express on 4000: downloads the OpenSanctions OFAC SDN CSV daily, indexes names in memory, queries Wikidata SPARQL with an LRU cache, exposes `GET /resolve`), joined to an external `umami_default` network. CasaOS app-store metadata is embedded in the compose file.
- `deploy.sh`: `git push origin master`, then SSH to a Tailscale-addressed VPS, `git pull`, `docker-compose down && up -d --build`. The live domain is fronted by Cloudflare (the code reads `cf-connecting-ip`, and comments note Cloudflare does not cache `/api`).
- CI: `.github/workflows/release.yml` builds and pushes `ghcr.io/simplifaisoul/osiris` on `v*` tags. `next.config.ts` disables `standalone` output when `VERCEL` is set, so a Vercel deployment path also exists; the README still says "Vercel Edge Network".
- The RECON port scanner is a separate sibling repo (`osiris-scanner`, not public in either repo read here) reached via `SCANNER_URL`/`SCANNER_KEY`; `/api/scanner` returns 503 when unset. `scripts/start-osiris.sh` in the fork launches both.
- Optional env: `AIS_API_KEY` (aisstream.io), `CLOUDFLARE_API_TOKEN` (Radar layers), `SDK_INGEST_KEY`, `ITS_KR_KEY`, `TRAFIKVERKET_KEY`, `OPENSKY_*`, `N2YO_API_KEY`, `FIRMS_API_KEY`, `OSIRIS_TELEGRAM_CHANNELS`, `UMAMI_WEBSITE_ID`, Gemini key.

---

## 4. The eleven layers, one by one

### 4.1 `maritime` — ports, chokepoints, AIS vessels

Route: `src/app/api/maritime/route.ts` (347 lines).

- **Static data** in the route file: 54 `PORTS` (container ports with TEU volume and rank, energy terminals with bpd, naval bases with fleet assignment) and 10 `CHOKEPOINTS` (Hormuz, Malacca, Suez, Bab el-Mandeb, Panama, Turkish Straits, Danish Straits, Cape of Good Hope, Taiwan Strait, Lombok) each with a baseline `risk`.
- **Live data**: at module load the server opens one WebSocket to `wss://stream.aisstream.io/v0/stream` (only if `AIS_API_KEY` is set), subscribes to nine bounding boxes over strategic chokepoints plus a global box, and filters `PositionReport` + `ShipStaticData`. Messages are folded into a process-global `Map<MMSI, ship>` (cap 20,000, FIFO eviction; ships older than 10 min dropped at read time). Ship type is derived from the AIS type code (35 = military, 60–69 passenger, 80–89 tanker, 70–79 cargo, promoted to "container" if length ≥ 150 m). Heading falls back to COG when true heading is the 511 sentinel. Reconnects after 5 s on close.
- **GET**: rebuilds a snapshot at most every 5 s: for each port counts ships within 50 km and those under 0.5 kn ("waiting"), derives `congestion` NORMAL / CONGESTED / SEVERE and a fake dwell time; for each chokepoint counts ships within 100 km and escalates `risk`. Returns `{ports, chokepoints, ships, ...}` as a pre-serialised string with `max-age=5, stale-while-revalidate=15`.
- **Client**: fetched on toggle and polled every 10 s. Three sources: `maritime` (ports, colour by type: naval red, energy orange, container teal), `maritime-choke` (colour by risk), `maritime-ships` (colour by vessel type, labels at zoom ≥ 5).
- **Live observation** (fetch-service snapshot, 2026-09): every port reported `LIVE: 0 (WAITING: 0)` and `ships` was effectively empty, so the public instance had no AIS feed at that moment. aisstream's free tier is terrestrial-only with one connection per key, which the fork's comments describe as "almost nothing over Gulf / Red Sea / Indian Ocean". The fork adds Redis persistence (6 h retention) and an optional Kpler/MarineTraffic enrichment; the live build has neither.

### 4.2 `cctv` — camera catalogue

Route: `src/app/api/cctv/route.ts` (996 lines) plus ~60 per-region modules.

- **48 region fetchers**, each a function returning `{id, lat, lng, name, city, country, feed_url|stream_url, stream_type, source}`. Sources include transport authorities (TfL JamCams, WSDOT, Caltrans ArcGIS, Ottawa, Quebec 511 MP4 clips, Ontario 511, Asfinag, Rijkswaterstaat, Finland digitraffic, Hong Kong TD, Taiwan THB/freeway MJPEG, NZTA, and US state DOTs for UT, OR, MI, IN, NV, LA, FL, GA, NC, AZ, TX), the OpenCCTV directory (~145k cameras, sampled into east/south-east/west Asia because its batch endpoint returns at most 50 rows), and generated SkylineWebcams lists for Latin America, Africa, Europe and Asia (mostly wrappers around public YouTube livestreams). The README claims 17,000+; comments in the code mention 19,000 to 35,000 depending on which upstreams answer.
- **Caching**: each region is wrapped in `sourceCache` (30 min TTL). The full `region=all` payload is built once, gzipped once (~7.8 MB JSON), persisted to `.cache/cctv-catalog.json`, restored at boot, and refreshed in the background every 5 min; visitors never wait on an upstream once a catalogue exists. Regions are fetched 4 at a time with a 12 s budget; stragglers are reported in `pendingRegions` and the client (`lib/camera-catalog.ts`) retries only those, at most 3 attempts with backoff.
- **Viewport mode** also exists (`?lat&lng&radius`) with hand-written bounding boxes per country, but the app requests `region=all`.
- **Rendering**: `cctv-glow`, `cctv-dots`, `cctv-label` (labels at zoom ≥ 10). Click opens a popup with an "OPEN FEED" button that mounts `CameraViewer`, which handles: JPG (cache-busted `<img>`, refetch driven by `onLoad`), MJPEG (`/api/cctv/proxy?url=` extracts the first JPEG frame server-side from the multipart stream, host allowlist of Taiwanese, Finnish, Portuguese, Indonesian and Lithuanian hosts, Referer injection where needed), HLS (`hls.js`, routed through `/api/cctv/hls` for Indonesian CDNs that omit CORS), MP4, iframe embeds, and YouTube/Skyline pages resolved to a current livestream id by `/api/cctv/resolve` (three TTLs: 30 min for a resolved id, 5 min for "no feed", 30 s for "unreachable"). TxDOT returns base64 JPEGs in JSON, so `/api/cctv/texas/snapshot` decodes them and validates the JPEG magic bytes.

### 4.3 `cctv_previews` — live frames on the map

Component: `src/components/CctvPreviews.tsx` (476 lines), added 2026-08-28 (PR #301), video tiles 2026-08-29 (#305), toggle 2026-08-31 (#310).

- Only active when both `cctv` and `cctv_previews` are on and zoom ≥ 13.
- When the map settles, it picks the nearest cameras in view, at most 8 tiles, at most 4 of them decoding video (MP4/HLS); the rest stay dots. Tile geometry 176×99 px 16:9 plus a 20 px caption; placement and overlap avoidance live in `lib/map-tile-layout.ts` and are shared with the live-news tiles.
- Tiles are plain DOM elements positioned with `map.project()` on every `move` event (no React re-render during panning).
- Refresh policy (`lib/camera-preview.ts`): JPG every 15 s with a random 0–2 s stagger, MP4 every 60 s, MJPEG and HLS never re-pointed. Embeds (YouTube/iframe) never get a tile. `hls.js` is dynamically imported only when an HLS tile is on screen.
- Frames are fetched by the browser directly from the camera operator, not through OSIRIS, except for the MJPEG and Indonesian-HLS proxies.

### 4.4 `live_news` — 24/7 broadcasters

Route: `src/app/api/live-news/route.ts` (51 lines). A static array of 15 feeds (README says 23–25): NBC, CBS, ABC, Bloomberg, C-SPAN, CBC (all `embed_allowed: false`, opened on youtube.com), Sky News, France 24, DW, Al Jazeera, NHK World, CNA, WION (embeddable via `youtube.com/embed/live_stream?channel=...&autoplay=1&mute=1`), CGTN (external), RT (Rumble). Cached a day.

Rendering: `news-glow`/`news-dots`/`news-label` in rose `#EC407A`, labels at zoom ≥ 4. Click opens an in-app player panel for embeddable feeds or a new tab otherwise. `LiveNewsPreviews.tsx` mirrors the CCTV tiles: at zoom ≥ 13 over the broadcaster's city, up to 4 YouTube iframes (208×117) play muted on the map.

### 4.5 `earthquakes` — USGS

Route: `src/app/api/earthquakes/route.ts`. One fetch of `earthquake.usgs.gov/.../summary/2.5_day.geojson` (M2.5+, 24 h), mapped to `{id, lat, lng, depth, magnitude, place, time, url, tsunami, felt, alert}`, `s-maxage=60`. The fork additionally merges EMSC/seismicportal.eu and dedupes within 0.5° and 60 s.

Client: fetched at boot (priority 1, together with news and markets) and polled every 15 min, skipped while the tab is hidden. Rendering: `eq-circles` with radius interpolated from magnitude (M2.5 → 3 px, M5 → 8 px, M7 → 14 px) and colour amber → orange → red; `eq-label` shows "M x.x" for M ≥ 4.5. Significant quakes also scroll in the status-bar ticker.

### 4.6 `global_incidents` — GDACS, not GDELT

Route: `src/app/api/gdelt/route.ts` (98 lines). Despite the path, the live build fetches `https://www.gdacs.org/xml/rss.xml` (the UN/EC Global Disaster Alert and Coordination System), splits it on `<item>`, regex-extracts title/link/description/`geo:lat`/`geo:long`/`gdacs:eventtype`, and maps EQ/TC/FL/VO/WF/DR to earthquake/weather/flood/volcano/wildfire/drought (everything else "incident"). `source: 'GDACS RSS API'`, `s-maxage=300`. Live sample (2026-09) confirmed: ids `gdacs-0…`, mostly wildfire notifications and earthquakes.

History: the layer once pointed at Liveuamap Ukraine (fixed in #280), a real GDELT rewrite landed in #320 and was reverted in #323 ("keep the CCTV sources"). Real GDELT GEO 2.0 events live in a separate, default-off `gdelt_events` layer (`/api/gdelt-events`, `lib/gdeltEvents.ts`). The fork's version of `/api/gdelt` is the opposite: seven GDELT GEO keyword queries in parallel plus optional ACLED, and, if both return nothing, an "OSIRIS Simulated Incident Engine" that scatters random fake events over Ukraine, Gaza, Lebanon, Sudan, DRC, Taiwan, Paris and New York.

Rendering: `gdelt-dots`, 4 px red circles at 50 % opacity, no labels. The `sdk_entities` builder also samples these as INTEL nodes (see 4.8).

### 4.7 `day_night` — solar terminator

Entirely client-side. `computeSolarTerminator()` in `OsirisMap.tsx`: solar declination ≈ −23.44° · cos(2π/365 · (dayOfYear + 10)), subsolar longitude = (12 − UTC hours) · 15°, then for every 2° of longitude the terminator latitude `atan(−cos(Δλ) / tan(δ))`. The polyline is closed to the dark pole (south if δ ≥ 0) to form a polygon. Pushed into the `day-night` source as a fill (`#000022` core / `#0D0030` ghost, opacity 0.35) and recomputed every 5 minutes. No equation-of-time or refraction correction; accuracy is on the order of a degree.

### 4.8 `cables`, `sdk_sea`, `sdk_air`, `sdk_naval` — submarine cables and the "SDK"

**Data**: `public/data/submarine-cables.json` (~730 KB GeoJSON, 700-odd `LineString`/`MultiLineString` features with `name`, `id`, and a per-cable `color`). The fork's planning notes (`.omo/plans/osiris-cable-rendering-fix.md`) record that it was captured from submarinecablemap.com's `cable-geo.json` (TeleGeography) along with `landing-point-geo.json` (1,916 points) and per-cable metadata; the live build ships only the cable geometry. `/api/infrastructure/cables` in the fork serves the same file with a one-day cache.

**Loading**: `page.tsx` fetches the JSON once when `activeLayers.cables` is true and stores `submarine_cables`. `cables` has no toggle in the current layer panel; it survives only as a URL key and a default.

**Drawing**: `OsirisMap.tsx` builds `sdk-links` features only for the SEA domain: each cable becomes a line with `domain: 'SEA'`, colour forced to `#1976D2`, after dropping cables whose original colour is one of three light blues ("background arcs"). Layer `sdk-sea` draws them at 0.8–2.5 px, opacity 0.3–0.7. The panel exposes this as "OSIRIS SDK → Maritime Lines" (`sdk_sea`). So: `cables=true` loads, `sdk_sea=true` draws; either alone shows nothing.

**`sdk_air` / `sdk_naval`**: they set visibility on `sdk-air*` (cyan, AIR filter) and `sdk-intel*` (violet, INTEL filter) line layers, but the link builder has a comment "curated routes for AIR/INTEL" and no code, so those sources are always empty. `page.tsx` separately computes `sdk_entities` (60 sampled flights, 60 sampled ships, all quakes, all incidents, all news points) on every data change, but `OsirisMap` unconditionally calls `setGeo('sdk-entities', [])`. Net effect: two of the eleven keys are inert and one CPU-only computation runs for nothing.

**The Polybolos SDK itself** (`src/lib/sdk/`): a type system (`Domain` AIR/SEA/LAND/SPACE/CYBER/EW/SUBSURFACE, `EntityType` TRACK/FACILITY/EVENT/SENSOR/SIGNAL/INTEL, `ThreatLevel`, `Classification` up to SECRET), a `PolybolosClient` with translators from each OSIRIS feed into `PolybolosEntity`, and a `LatticeAdapter` whose Lattice types are, per its own comment, "simulated from Lattice SDK docs". Server side: `POST /api/sdk/ingest` (fail-closed on `SDK_INGEST_KEY`; the fork instead accepted the hard-coded keys `polybolos-dev-key` and `lattice-integration-key`, meaning anyone could push arbitrary map entities to a fork-based instance) stores entities in a process-global `Map`; `GET /api/sdk/stream` is SSE with a status event (`latticeStatus: 'disconnected'` and `feedCount: 9` hard-coded), a 15 s heartbeat, and a 5 s poll that emits the first 500 entities when the store changed. Live sample (2026-09-18): `entityCount: 0`, no ingest history.

---

## 5. Other things on the default screen

- **Splash**: 2.5 s "ESTABLISHING SECURE CONNECTION… INITIALIZING FEEDS… CALIBRATING SENSORS… SYSTEM READY" timer, then IP geolocation via `/api/geo` (ipapi.co with freeipapi.com fallback) flies the map to your city at zoom 12.
- **Status bar / ticker**: `/api/markets` (Yahoo Finance chart API, defence equities and commodities), `/api/crypto`, significant quakes, `/api/space-weather` (NOAA SWPC Kp, flares). Markets retry up to 3× on cold start because a cold upstream returns an empty set.
- **`/api/news` ("Live Alerts")**: scrapes public Telegram channels through `t.me/s/<handle>` web previews (7 channels as of 2026-09-17, each labelled with `lean` and `bloc`: independent / russian / western / regional), per-channel 3 min cache, falling back to BBC, Al Jazeera and GDACS RSS if Telegram blocks the host. `risk_score` is a keyword count and coordinates are preset country anchors, which the docs page admits.
- **`/api/stats`**: fans out to six internal routes once per 30 s, shares the computation between concurrent callers, and returns six counters. The live snapshot (2026-09-03) returned all zeros, consistent with the rework that landed in #345 or a period where the internal fetches timed out.
- **`/api/health`**: the live snapshot (2026-09-03) still returned the old unconditional `status: operational` shape; master has since rewritten it to `status: serving, scope: process, checks_performed: none`.
- **Right rail**: RECON (OSINT panel, 20+ lookups, all keyless except Shodan/Hudson Rock/IsMalicious), SPACE (satellite readout, ISS 24/7 downlink), MARKETS, ALERTS, DRAW (area-of-interest toolkit that replaced the entity graph in #291), ROUTE (directions via the public OSRM router with a Valhalla fallback, turn-by-turn navigation), SEARCH, ARCGIS (public Feature Service search and spatial query, no key), REMOTE (Web Bluetooth device scanner, "BLE sniffer", browser hardware probe).

---

## 6. Repo lineage and development pattern

| | `simplifaisoul/osiris` | `carbon-evolution/osiris` |
|---|---|---|
| Role | canonical; what osirisai.live runs; what `/docs` tells you to clone | fork, diverged ~July 2026 |
| Last commit read | 2026-09-17 (#376) | 2026-07-27 (#5) |
| License | MIT © 2026 simplifaisoul | MIT |
| Map lib | MapLibre 6.7, self-hosted worker | MapLibre 5.24 + deck.gl 9 + turf + h3 + proj4 |
| Persistence | none (process memory, `.cache/` file) | Redis + Postgres/PostGIS cache-first (`cacheFirst`, `feed_snapshots`), optional OpenSearch + Neo4j, node-cron workers (KEV, EPSS, CVE, ThreatFox, URLhaus, MalwareBazaar) |
| Extra layers | ArcGIS, Cloudflare Radar, GDELT events, botnet C2, satellite categories, 3D terrain | temperature rasters (Open-Meteo, NOAA OISST, NDBC buoys), EuRepoC, GPS jamming, ransomware.live, power plants, threat-intel blocklists, Tor, MITRE |
| Incidents | GDACS RSS | GDELT GEO + ACLED + simulated fallback |
| Maritime | aisstream, in-memory | aisstream + Redis + Kpler |
| SDK ingest | fail-closed on env key | hard-coded dev keys |

Recent commit messages on master (all 2026-08/09) are almost entirely fixes and expansions of the CCTV catalogue, plus reverts of feature work that broke ("revert: roll back the nuclear and GDELT work"). Author split over the last 156 commits: Gemini CLI 88, simplifaisoul 66, two one-off contributors. `scratch/` in the repo contains scraping scripts and an `injection_tutorial.html`; `engine/` is an empty Python `__pycache__`; `runs/ledger.jsonl` looks like an agent run log. Other public forks (enzg/osiris-live, tov-a, ZEZE1020, TITANS-AEROSPACE) carry the same README.

---

## 7. Honest assessment

What is genuinely good:

- The layer pattern is clean and easy to extend: one route file per source, one `fetchEndpoint` line in `page.tsx`, one `setGeo` and a handful of `addLayer` calls. Adding a country's traffic cameras is a 30-line module.
- The CCTV catalogue work is real engineering: 48 upstreams, per-source caching, bounded fan-out, persisted gzipped snapshot, partial-region retry on the client, and a preview-tile system with a sensible frame budget. This is the most mature part of the platform and where most recent effort went.
- Snapshot-per-TTL on the hot routes, `stale-while-revalidate` everywhere, and the tile proxy plus nginx cache make a single VPS survive real traffic.
- SSRF guard, host allowlists on every proxy, and fail-closed ingest are correct.

What the branding oversells:

- "Palantir alternative": there is no data model, no entity resolution across feeds (the "intel" service does name-matching against OFAC and Wikidata), no persistence in the live build, no user accounts, and the "SDK"/"Common Operating Picture" is a JSON schema plus an in-memory map.
- Several layers are static reference data dressed as live intelligence: ports and chokepoints are hard-coded arrays with `risk` bumped by ship counts; conflict zones are 13 hand-written anchors; live news is 15 YouTube links.
- Name drift: `/api/gdelt` serves GDACS; `sdk_air`/`sdk_naval` draw nothing; README counts (cameras, broadcasters) do not match the code.
- `stealthFetch`'s "residential IP pool" is decorative, but the intent (evading upstream bot controls) is stated in its docstring.
- Privacy: every page view sends the visitor's IP as a named analytics event to a self-hosted Umami, without disclosure in the UI (a `/privacy` page exists on master; not audited here).
- Upstream fragility: aisstream free tier, GDELT's changing hosts, YouTube embed policy, and dozens of DOT endpoints mean the map's contents depend on which upstreams are up. The public instance showed zero ships and zero stats in the samples taken.

---

## 8. Reproducing it

```bash
git clone https://github.com/simplifaisoul/osiris.git
cd osiris
npm install          # runs tools/prepare-map-worker.mjs (copies the MapLibre worker into public/vendor)
npm run dev          # http://localhost:3000, no keys needed
```

With `AIS_API_KEY` (free aisstream.io key) you get live ships; with `CLOUDFLARE_API_TOKEN` (Radar read) the two NETINTEL layers appear; with `SDK_INGEST_KEY` you can `POST /api/sdk/ingest` your own entities and watch them via `GET /api/sdk/stream`. `docker compose up -d` brings up the same three-container stack the public site runs, minus Umami and Cloudflare.

If the goal is to build something similar rather than run this: the reusable ideas are (1) one Next route per upstream with a shared source cache and stale-on-error, (2) pre-serialised snapshots on any route that aggregates in-memory state, (3) MapLibre with all sources registered up front and layers toggled by `setData`, and (4) DOM overlay tiles positioned on `move` for anything that needs `<img>`/`<video>`/iframes over the map.
