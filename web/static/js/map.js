/* ===================================================================
   عارض خرائط مدمج — بلا أي مكتبة خارجية ولا اعتماد على CDN.

   يرسم على <canvas> بإسقاط ويب-مركاتور القياسي، ويدعم:
   * تحريك وتكبير باللمس والفأرة
   * طبقة بلاطات اختيارية (عنوانها قابل للإعداد؛ بدونها تُرسم شبكة إحداثيات)
   * مسارات بأسهم اتجاه وترقيم محطات
   * تمييز الالتقاط عن التسليم
   * مواقع سائقين لحظية مع وسم قِدَم البيانات
   * مسار مخطط مقابل مسار منفَّذ

   سبب البناء الداخلي: المتطلبات تمنع الارتباط بمزوّد واحد، وبيئة النشر قد
   تكون معزولة عن الإنترنت. عنوان البلاطات يُضبط من الإعدادات، وإن كان
   فارغًا تعمل الخريطة بالكامل بلا أي طلب خارجي.
   =================================================================== */

const TILE_SIZE = 256;
const MAX_ZOOM = 18;
const MIN_ZOOM = 3;

export function lonToX(lon, zoom) {
  return ((lon + 180) / 360) * TILE_SIZE * 2 ** zoom;
}

export function latToY(lat, zoom) {
  const clamped = Math.max(-85.05112878, Math.min(85.05112878, lat));
  const rad = (clamped * Math.PI) / 180;
  return ((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2)
    * TILE_SIZE * 2 ** zoom;
}

export function xToLon(x, zoom) { return (x / (TILE_SIZE * 2 ** zoom)) * 360 - 180; }

export function yToLat(y, zoom) {
  const n = Math.PI - (2 * Math.PI * y) / (TILE_SIZE * 2 ** zoom);
  return (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
}

const PALETTE = {
  hub:      '#1f5c4a',
  pickup:   '#1f5f8b',
  delivery: '#1d6f4a',
  driver:   '#8a5a00',
  stale:    '#9d2c2c',
  planned:  '#3f7fae',
  actual:   '#8a5a00',
  grid:     'rgba(120,140,136,.22)',
  label:    '#16211f',
  labelBg:  'rgba(255,255,255,.86)',
};

export class MasarMap {
  constructor(container, {
    tileUrl = '', attribution = '', height = 420, interactive = true,
  } = {}) {
    this.tileUrl = tileUrl;
    this.attribution = attribution;
    this.center = { lat: 24.7136, lon: 46.6753 };
    this.zoom = 10;
    this.layers = { routes: [], markers: [], tracks: [] };
    this.tiles = new Map();
    this.onMarkerClick = null;
    this.selectedMarker = null;

    this.shell = document.createElement('div');
    this.shell.className = 'map-shell';
    this.shell.style.height = `${height}px`;

    this.canvas = document.createElement('canvas');
    this.canvas.className = 'map-canvas';
    this.canvas.setAttribute('role', 'img');
    this.canvas.setAttribute('aria-label', 'خريطة المسارات والمواقع');
    this.shell.append(this.canvas);

    if (interactive) this.#buildControls();
    if (attribution) {
      const node = document.createElement('div');
      node.className = 'map-attribution';
      node.textContent = attribution;
      this.shell.append(node);
    }

    container.append(this.shell);
    this.ctx = this.canvas.getContext('2d');
    this.#resize();

    this.observer = new ResizeObserver(() => { this.#resize(); this.draw(); });
    this.observer.observe(this.shell);
    if (interactive) this.#bindInteraction();
    this.draw();
  }

  destroy() { this.observer?.disconnect(); this.shell.remove(); }

  /* ------------------------------------------------------- الطبقات --- */

  setRoutes(routes) { this.layers.routes = routes || []; this.draw(); return this; }
  setMarkers(markers) { this.layers.markers = markers || []; this.draw(); return this; }
  setTracks(tracks) { this.layers.tracks = tracks || []; this.draw(); return this; }

  setLegend(items) {
    this.legendNode?.remove();
    if (!items?.length) return this;
    const node = document.createElement('div');
    node.className = 'map-legend';
    for (const item of items) {
      const row = document.createElement('div');
      row.className = 'row';
      const swatch = document.createElement('span');
      swatch.className = 'swatch';
      swatch.style.background = item.color;
      row.append(swatch, document.createTextNode(item.label));
      node.append(row);
    }
    this.legendNode = node;
    this.shell.append(node);
    return this;
  }

  select(markerId) { this.selectedMarker = markerId; this.draw(); }

  /* ------------------------------------------------------ العرض ------ */

  fit(points, { padding = 56 } = {}) {
    const valid = (points || []).filter(
      (p) => Number.isFinite(Number(p.lat)) && Number.isFinite(Number(p.lon)));
    if (!valid.length) return this;
    if (valid.length === 1) {
      this.center = { lat: Number(valid[0].lat), lon: Number(valid[0].lon) };
      this.zoom = 13;
      this.draw();
      return this;
    }
    const lats = valid.map((p) => Number(p.lat));
    const lons = valid.map((p) => Number(p.lon));
    const minLat = Math.min(...lats), maxLat = Math.max(...lats);
    const minLon = Math.min(...lons), maxLon = Math.max(...lons);
    this.center = { lat: (minLat + maxLat) / 2, lon: (minLon + maxLon) / 2 };

    const width = Math.max(this.canvas.clientWidth - padding * 2, 64);
    const height = Math.max(this.canvas.clientHeight - padding * 2, 64);
    let best = MIN_ZOOM;
    for (let zoom = MAX_ZOOM; zoom >= MIN_ZOOM; zoom -= 1) {
      const dx = Math.abs(lonToX(maxLon, zoom) - lonToX(minLon, zoom));
      const dy = Math.abs(latToY(minLat, zoom) - latToY(maxLat, zoom));
      if (dx <= width && dy <= height) { best = zoom; break; }
    }
    this.zoom = best;
    this.draw();
    return this;
  }

  /* ------------------------------------------------------ الرسم ------ */

  #resize() {
    const ratio = window.devicePixelRatio || 1;
    const width = this.shell.clientWidth || 640;
    const height = this.shell.clientHeight || 420;
    this.canvas.width = Math.round(width * ratio);
    this.canvas.height = Math.round(height * ratio);
    this.canvas.style.width = `${width}px`;
    this.canvas.style.height = `${height}px`;
    this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  }

  project(lat, lon) {
    const width = this.canvas.clientWidth, height = this.canvas.clientHeight;
    const cx = lonToX(this.center.lon, this.zoom);
    const cy = latToY(this.center.lat, this.zoom);
    return {
      x: lonToX(Number(lon), this.zoom) - cx + width / 2,
      y: latToY(Number(lat), this.zoom) - cy + height / 2,
    };
  }

  unproject(x, y) {
    const width = this.canvas.clientWidth, height = this.canvas.clientHeight;
    const cx = lonToX(this.center.lon, this.zoom);
    const cy = latToY(this.center.lat, this.zoom);
    return {
      lon: xToLon(x - width / 2 + cx, this.zoom),
      lat: yToLat(y - height / 2 + cy, this.zoom),
    };
  }

  draw() {
    const ctx = this.ctx;
    const width = this.canvas.clientWidth, height = this.canvas.clientHeight;
    if (!width || !height) return;

    const dark = window.matchMedia?.('(prefers-color-scheme: dark)').matches;
    ctx.fillStyle = dark ? '#0b110f' : '#eceff2';
    ctx.fillRect(0, 0, width, height);

    if (this.tileUrl) this.#drawTiles(ctx, width, height);
    else this.#drawGrid(ctx, width, height);

    for (const track of this.layers.tracks) this.#drawTrack(ctx, track);
    for (const route of this.layers.routes) this.#drawRoute(ctx, route);
    for (const marker of this.layers.markers) this.#drawMarker(ctx, marker);
  }

  #drawGrid(ctx, width, height) {
    ctx.strokeStyle = PALETTE.grid;
    ctx.lineWidth = 1;
    ctx.font = '10px system-ui, sans-serif';
    ctx.fillStyle = 'rgba(120,140,136,.7)';
    const stepDegrees = this.zoom >= 12 ? 0.05 : this.zoom >= 9 ? 0.25 : 1;
    const topLeft = this.unproject(0, 0);
    const bottomRight = this.unproject(width, height);

    for (let lon = Math.floor(topLeft.lon / stepDegrees) * stepDegrees;
         lon <= bottomRight.lon; lon += stepDegrees) {
      const { x } = this.project(topLeft.lat, lon);
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke();
      ctx.fillText(lon.toFixed(2), x + 3, 12);
    }
    for (let lat = Math.floor(bottomRight.lat / stepDegrees) * stepDegrees;
         lat <= topLeft.lat; lat += stepDegrees) {
      const { y } = this.project(lat, topLeft.lon);
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke();
      ctx.fillText(lat.toFixed(2), 4, y - 3);
    }
  }

  #drawTiles(ctx, width, height) {
    const zoom = Math.round(this.zoom);
    const scale = 2 ** (this.zoom - zoom);
    const cx = lonToX(this.center.lon, zoom);
    const cy = latToY(this.center.lat, zoom);
    const halfW = width / 2 / scale, halfH = height / 2 / scale;
    const minTileX = Math.floor((cx - halfW) / TILE_SIZE);
    const maxTileX = Math.floor((cx + halfW) / TILE_SIZE);
    const minTileY = Math.floor((cy - halfH) / TILE_SIZE);
    const maxTileY = Math.floor((cy + halfH) / TILE_SIZE);
    const limit = 2 ** zoom;

    for (let tx = minTileX; tx <= maxTileX; tx += 1) {
      for (let ty = minTileY; ty <= maxTileY; ty += 1) {
        if (ty < 0 || ty >= limit) continue;
        const wrapped = ((tx % limit) + limit) % limit;
        const key = `${zoom}/${wrapped}/${ty}`;
        let image = this.tiles.get(key);
        if (!image) {
          image = new Image();
          image.crossOrigin = 'anonymous';
          image.onload = () => this.draw();
          image.onerror = () => { image.failed = true; };
          image.src = this.tileUrl
            .replace('{z}', zoom).replace('{x}', wrapped).replace('{y}', ty);
          this.tiles.set(key, image);
        }
        if (image.complete && !image.failed && image.naturalWidth) {
          const sx = (tx * TILE_SIZE - cx) * scale + width / 2;
          const sy = (ty * TILE_SIZE - cy) * scale + height / 2;
          ctx.drawImage(image, sx, sy, TILE_SIZE * scale + 1, TILE_SIZE * scale + 1);
        }
      }
    }
  }

  #drawRoute(ctx, route) {
    const points = (route.points || [])
      .filter((p) => Number.isFinite(Number(p.lat)))
      .map((p) => ({ ...p, ...this.project(p.lat, p.lon) }));
    if (points.length < 2) { this.#drawRoutePoints(ctx, points, route); return; }

    ctx.save();
    ctx.strokeStyle = route.color || PALETTE.planned;
    ctx.lineWidth = route.width || 3;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    if (route.dashed) ctx.setLineDash([7, 5]);
    ctx.globalAlpha = route.dimmed ? 0.35 : 1;

    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (const point of points.slice(1)) ctx.lineTo(point.x, point.y);
    ctx.stroke();
    ctx.setLineDash([]);

    // أسهم الاتجاه في منتصف كل مقطع
    ctx.fillStyle = route.color || PALETTE.planned;
    for (let i = 0; i < points.length - 1; i += 1) {
      const a = points[i], b = points[i + 1];
      const dx = b.x - a.x, dy = b.y - a.y;
      const length = Math.hypot(dx, dy);
      if (length < 34) continue;
      const mx = a.x + dx / 2, my = a.y + dy / 2;
      const angle = Math.atan2(dy, dx);
      ctx.save();
      ctx.translate(mx, my);
      ctx.rotate(angle);
      ctx.beginPath();
      ctx.moveTo(7, 0); ctx.lineTo(-4, 4.5); ctx.lineTo(-4, -4.5);
      ctx.closePath(); ctx.fill();
      ctx.restore();
    }
    ctx.restore();
    this.#drawRoutePoints(ctx, points, route);
  }

  #drawRoutePoints(ctx, points, route) {
    if (route.hidePoints) return;
    for (const point of points) {
      this.#drawMarker(ctx, {
        ...point,
        kind: point.kind || 'PICKUP',
        label: point.seq !== undefined ? String(point.seq) : '',
        id: point.id,
        title: point.title,
      });
    }
  }

  #drawTrack(ctx, track) {
    const points = (track.points || [])
      .filter((p) => Number.isFinite(Number(p.lat)))
      .map((p) => this.project(p.lat, p.lon));
    if (points.length < 2) return;
    ctx.save();
    ctx.strokeStyle = track.color || PALETTE.actual;
    ctx.lineWidth = track.width || 2.5;
    ctx.globalAlpha = 0.85;
    ctx.setLineDash(track.dashed ? [4, 4] : []);
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (const point of points.slice(1)) ctx.lineTo(point.x, point.y);
    ctx.stroke();
    ctx.restore();
  }

  #drawMarker(ctx, marker) {
    const position = marker.x !== undefined && marker.y !== undefined
      ? marker : this.project(marker.lat, marker.lon);
    const { x, y } = position;
    const width = this.canvas.clientWidth, height = this.canvas.clientHeight;
    if (x < -60 || y < -60 || x > width + 60 || y > height + 60) return;

    const kind = marker.kind || 'PICKUP';
    const color = marker.color
      || (kind === 'HUB_START' || kind === 'HUB' ? PALETTE.hub
        : kind === 'DELIVERY' ? PALETTE.delivery
        : kind === 'DRIVER' ? (marker.stale ? PALETTE.stale : PALETTE.driver)
        : PALETTE.pickup);
    const selected = this.selectedMarker && this.selectedMarker === marker.id;
    const radius = selected ? 15 : (kind === 'DRIVER' ? 11 : 12);

    ctx.save();
    ctx.beginPath();
    if (kind === 'DELIVERY') {
      ctx.rect(x - radius, y - radius, radius * 2, radius * 2);
    } else if (kind === 'HUB_START' || kind === 'HUB') {
      ctx.moveTo(x, y - radius - 2);
      ctx.lineTo(x + radius + 2, y);
      ctx.lineTo(x, y + radius + 2);
      ctx.lineTo(x - radius - 2, y);
      ctx.closePath();
    } else {
      ctx.arc(x, y, radius, 0, Math.PI * 2);
    }
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = selected ? 3 : 2;
    ctx.strokeStyle = '#fff';
    ctx.stroke();

    if (marker.label) {
      ctx.fillStyle = '#fff';
      ctx.font = `700 ${radius > 12 ? 12 : 11}px system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(marker.label, x, y + 0.5);
    }
    if (marker.stale) {
      ctx.beginPath();
      ctx.arc(x + radius - 1, y - radius + 1, 4.5, 0, Math.PI * 2);
      ctx.fillStyle = PALETTE.stale;
      ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();
    }
    if (selected && marker.title) {
      ctx.font = '600 12px system-ui, sans-serif';
      const metrics = ctx.measureText(marker.title);
      const boxWidth = metrics.width + 12;
      ctx.fillStyle = PALETTE.labelBg;
      ctx.fillRect(x - boxWidth / 2, y - radius - 26, boxWidth, 20);
      ctx.fillStyle = PALETTE.label;
      ctx.textAlign = 'center';
      ctx.fillText(marker.title, x, y - radius - 16);
    }
    ctx.restore();
  }

  /* -------------------------------------------------- التفاعل ------- */

  #buildControls() {
    const controls = document.createElement('div');
    controls.className = 'map-controls';
    const make = (label, title, handler) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = label;
      button.title = title;
      button.setAttribute('aria-label', title);
      button.addEventListener('click', handler);
      return button;
    };
    controls.append(
      make('+', 'تكبير', () => { this.zoom = Math.min(MAX_ZOOM, this.zoom + 1); this.draw(); }),
      make('−', 'تصغير', () => { this.zoom = Math.max(MIN_ZOOM, this.zoom - 1); this.draw(); }),
      make('⤢', 'ملاءمة الكل', () => this.fitAll()),
    );
    this.shell.append(controls);
  }

  fitAll() {
    const points = [
      ...this.layers.markers,
      ...this.layers.routes.flatMap((r) => r.points || []),
      ...this.layers.tracks.flatMap((t) => t.points || []),
    ];
    this.fit(points);
  }

  #bindInteraction() {
    let dragging = false;
    let last = null;
    let moved = 0;

    const pointerDown = (event) => {
      dragging = true; moved = 0;
      last = { x: event.clientX, y: event.clientY };
      this.canvas.setPointerCapture?.(event.pointerId);
    };
    const pointerMove = (event) => {
      if (!dragging || !last) return;
      const dx = event.clientX - last.x, dy = event.clientY - last.y;
      moved += Math.abs(dx) + Math.abs(dy);
      last = { x: event.clientX, y: event.clientY };
      const cx = lonToX(this.center.lon, this.zoom) - dx;
      const cy = latToY(this.center.lat, this.zoom) - dy;
      this.center = { lon: xToLon(cx, this.zoom), lat: yToLat(cy, this.zoom) };
      this.draw();
    };
    const pointerUp = (event) => {
      if (dragging && moved < 6) this.#handleClick(event);
      dragging = false; last = null;
    };

    this.canvas.addEventListener('pointerdown', pointerDown);
    this.canvas.addEventListener('pointermove', pointerMove);
    this.canvas.addEventListener('pointerup', pointerUp);
    this.canvas.addEventListener('pointercancel', () => { dragging = false; });
    this.canvas.addEventListener('wheel', (event) => {
      event.preventDefault();
      const rect = this.canvas.getBoundingClientRect();
      const before = this.unproject(event.clientX - rect.left, event.clientY - rect.top);
      this.zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM,
        this.zoom + (event.deltaY < 0 ? 0.5 : -0.5)));
      const after = this.unproject(event.clientX - rect.left, event.clientY - rect.top);
      this.center = {
        lat: this.center.lat + (before.lat - after.lat),
        lon: this.center.lon + (before.lon - after.lon),
      };
      this.draw();
    }, { passive: false });
  }

  #handleClick(event) {
    if (!this.onMarkerClick) return;
    const rect = this.canvas.getBoundingClientRect();
    const px = event.clientX - rect.left, py = event.clientY - rect.top;
    const candidates = [
      ...this.layers.markers,
      ...this.layers.routes.flatMap((r) => r.points || []),
    ];
    let closest = null, bestDistance = 22;
    for (const marker of candidates) {
      if (!Number.isFinite(Number(marker.lat))) continue;
      const { x, y } = this.project(marker.lat, marker.lon);
      const distance = Math.hypot(x - px, y - py);
      if (distance < bestDistance) { bestDistance = distance; closest = marker; }
    }
    if (closest) this.onMarkerClick(closest);
  }
}

/** يفتح الملاحة الخارجية على الجهاز (§23). */
export function openNavigation(lat, lon, label = '') {
  const encoded = encodeURIComponent(label || `${lat},${lon}`);
  const isApple = /iPad|iPhone|iPod|Macintosh/.test(navigator.userAgent);
  const url = isApple
    ? `https://maps.apple.com/?daddr=${lat},${lon}&q=${encoded}`
    : `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`;
  window.open(url, '_blank', 'noopener');
}

export const MAP_COLORS = PALETTE;
