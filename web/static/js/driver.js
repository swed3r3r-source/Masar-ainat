/* ===================================================================
   تطبيق السائق (PWA) — أزرار كبيرة، عربي RTL، ويعمل دون اتصال.

   قواعد ثابتة مطبَّقة في الواجهة كما في الخادم:
   * لا تظهر رحلة قبل نشر يومها، ولا تبدأ قبل تاريخها.
   * التسلسل: ابدأ ← وصلت ← التقطت/سلّمت. لا قفز فوق محطة.
   * «وصلت» لا تتطلب تصويرًا؛ التصوير بعد الالتقاط أو التسليم.
   * **لا يوجد حقل لإدخال درجة الحرارة** — القراءات من الحساس فقط.
   * الأحداث تُخزَّن محليًا عند انقطاع الاتصال وتُزامَن بمعرّف يمنع التكرار.
   =================================================================== */

import {
  api, el, mount, clear, fmt, field, toast, toastError, modal, confirmDialog,
  select, statusBadge, LABELS, loadLabels, state, setUnauthorizedHandler,
} from './core.js';
import { openNavigation } from './map.js';

const root = document.getElementById('root');
const STORE_KEY = 'masar.driver.queue.v1';
const CACHE_KEY = 'masar.driver.routes.v1';

/* ------------------------------------------------ الطابور دون اتصال --- */

const queue = {
  read() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY) || '[]'); }
    catch { return []; }
  },
  write(items) { localStorage.setItem(STORE_KEY, JSON.stringify(items)); },
  push(event) {
    const items = this.read();
    items.push({ ...event, queued_at: new Date().toISOString() });
    this.write(items);
    updateOfflineBar();
    return event;
  },
  remove(clientEventIds) {
    this.write(this.read().filter((item) => !clientEventIds.includes(item.client_event_id)));
    updateOfflineBar();
  },
  get size() { return this.read().length; },
};

function newEventId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

const cache = {
  read() {
    try { return JSON.parse(localStorage.getItem(CACHE_KEY) || 'null'); }
    catch { return null; }
  },
  write(value) {
    try { localStorage.setItem(CACHE_KEY, JSON.stringify(value)); } catch { /* ممتلئ */ }
  },
};

let offlineBar = null;
function updateOfflineBar() {
  if (!offlineBar) {
    offlineBar = el('div', { class: 'offline-bar hidden' });
    document.body.append(offlineBar);
  }
  const pending = queue.size;
  if (!navigator.onLine) {
    offlineBar.className = 'offline-bar';
    offlineBar.textContent = pending
      ? `لا يوجد اتصال — ${pending} حدثًا محفوظًا وسيُرسل تلقائيًا`
      : 'لا يوجد اتصال — يمكنك متابعة العمل وستُحفظ الأحداث';
  } else if (pending) {
    offlineBar.className = 'offline-bar syncing';
    offlineBar.textContent = `جارٍ مزامنة ${pending} حدثًا…`;
  } else {
    offlineBar.className = 'offline-bar hidden';
  }
}

async function syncQueue() {
  const items = queue.read();
  if (!items.length || !navigator.onLine) { updateOfflineBar(); return; }
  updateOfflineBar();
  try {
    const response = await api.post('/api/driver/sync', { events: items });
    const data = response.data;
    const settled = data.results
      .filter((result) => result.status !== 'FAILED')
      .map((result) => result.client_event_id);
    queue.remove(settled);
    if (data.applied) {
      toast(`زُومنت ${data.applied} عملية`, { tone: 'success' });
    }
    if (data.failed) {
      const failures = data.results.filter((r) => r.status === 'FAILED');
      toast(`تعذرت مزامنة ${data.failed} عملية: ${failures[0]?.message || ''}`,
        { tone: 'error', timeout: 12000 });
    }
    await renderRoutes();
  } catch (error) {
    console.warn('تأجلت المزامنة', error);
  }
  updateOfflineBar();
}

window.addEventListener('online', () => { updateOfflineBar(); syncQueue(); });
window.addEventListener('offline', updateOfflineBar);

/* ------------------------------------------------------- تتبع الموقع -- */

let positionTimer = null;
// معرّف مراقبة الموقع يُحفظ في متغيّر مستقل: setInterval يعيد **رقمًا** في
// المتصفح، وإسناد خاصية إلى رقم يرمي TypeError في الوحدات (strict mode).
let positionWatchId = null;
let lastPosition = null;
const positionBuffer = [];

function startTracking(routeId) {
  stopTracking();
  if (!navigator.geolocation) return;
  const push = (position) => {
    lastPosition = {
      lat: position.coords.latitude, lon: position.coords.longitude,
      speed_kmh: position.coords.speed ? position.coords.speed * 3.6 : null,
      heading_deg: position.coords.heading ?? null,
      accuracy_m: position.coords.accuracy ?? null,
      recorded_at: new Date(position.timestamp).toISOString(),
      route_id: routeId,
    };
    positionBuffer.push(lastPosition);
  };
  navigator.geolocation.getCurrentPosition(push, () => {}, { enableHighAccuracy: true });
  positionWatchId = navigator.geolocation.watchPosition(push, () => {},
    { enableHighAccuracy: true, maximumAge: 10000, timeout: 20000 });

  positionTimer = setInterval(async () => {
    if (!positionBuffer.length || !navigator.onLine) return;
    const batch = positionBuffer.splice(0, positionBuffer.length);
    try { await api.post('/api/positions', { points: batch }); }
    catch { positionBuffer.unshift(...batch.slice(-20)); }
  }, 15000);
}

function stopTracking() {
  if (positionTimer !== null) {
    clearInterval(positionTimer);
    positionTimer = null;
  }
  if (positionWatchId !== null) {
    navigator.geolocation.clearWatch(positionWatchId);
    positionWatchId = null;
  }
}

function currentCoords() {
  return lastPosition ? { lat: lastPosition.lat, lon: lastPosition.lon } : {};
}

/* ------------------------------------------------------------ الدخول - */

function loginScreen(message = '') {
  const email = el('input', { type: 'email', autocomplete: 'username',
    inputmode: 'email', placeholder: 'بريد السائق' });
  const password = el('input', { type: 'password', autocomplete: 'current-password' });
  const errorNode = el('div', { class: 'error' }, message);
  const submit = el('button', { class: 'btn primary block lg', type: 'submit' }, 'دخول');

  mount(root, el('div', { class: 'auth-shell' },
    el('form', {
      class: 'auth-card',
      onSubmit: async (event) => {
        event.preventDefault();
        submit.disabled = true; submit.textContent = 'جارٍ التحقق…';
        try {
          const response = await api.post('/api/auth/login',
            { email: email.value.trim(), password: password.value });
          state.user = response.data.user;
          if (state.user.role !== 'DRIVER') {
            errorNode.textContent = 'هذا التطبيق مخصص للسائقين — استخدم الواجهة الرئيسية';
            submit.disabled = false; submit.textContent = 'دخول';
            return;
          }
          await boot();
        } catch (error) {
          errorNode.textContent = error.message;
          submit.disabled = false; submit.textContent = 'دخول';
        }
      },
    },
      el('div', { class: 'logo' },
        el('div', { class: 'mark' }, 'م'),
        el('div', {},
          el('div', { class: 'name' }, 'مسار عينات'),
          el('div', { class: 'tag' }, 'تطبيق السائق'))),
      field('البريد الإلكتروني', email),
      field('كلمة المرور', password),
      errorNode, submit)));
}

/* --------------------------------------------------------- الشاشات --- */

let currentRoute = null;

async function renderRoutes() {
  let routes;
  try {
    routes = (await api.get('/api/driver/routes', { query: { days: 3 } })).data;
    cache.write({ routes, at: Date.now() });
  } catch (error) {
    const cached = cache.read();
    if (!cached) { toastError(error); return; }
    routes = cached.routes;
    toast('عرض بيانات محفوظة — لا يوجد اتصال', { tone: 'warning' });
  }

  const today = fmt.isoDate();
  mount(root, el('div', { class: 'driver-app' },
    el('div', { class: 'driver-head' },
      el('div', { class: 'title' }, 'رحلاتي'),
      el('button', {
        class: 'btn ghost sm', style: { color: '#cfe0da' },
        onClick: () => syncQueue(),
      }, '↻'),
      el('button', {
        class: 'btn ghost sm', style: { color: '#cfe0da' },
        onClick: async () => {
          if (queue.size && !confirm(
            `يوجد ${queue.size} حدثًا غير مُزامن. الخروج قد يؤخر إرسالها. هل تريد الخروج؟`)) {
            return;
          }
          stopTracking();
          try { await api.post('/api/auth/logout', {}); } catch { /* تجاهل */ }
          loginScreen();
        },
      }, 'خروج')),
    el('div', { class: 'driver-body' },
      el('p', { class: 'muted small' },
        `${state.user.full_name} · ${fmt.date(new Date())}`),
      routes.length ? routes.map((route) => {
        const isToday = String(route.service_date).slice(0, 10) === today;
        const done = Number(route.completed_stops) || 0;
        const total = Number(route.total_stops) || 0;
        return el('div', {
          class: `driver-stop ${route.status === 'IN_PROGRESS' ? 'current' : ''} `
            + `${route.status === 'COMPLETED' ? 'done' : ''}`,
        },
          el('div', { class: 'head' },
            el('div', { class: 'seq' }, route.status === 'COMPLETED' ? '✓' : '⇉'),
            el('div', { style: { flex: 1 } },
              el('div', { class: 'name' }, route.reference),
              el('div', { class: 'meta' },
                `${fmt.date(route.service_date)} · ${route.hub_name_ar}`))),
          el('div', { class: 'meta' },
            `البداية ${fmt.time(route.planned_start_at)} · `
            + `${route.shipment_count} شحنة · ${fmt.km(route.distance_km)}`
            + (route.is_long_haul ? ' · رحلة بعيدة' : '')),
          total ? el('div', { class: 'progress', style: { marginTop: '10px' } },
            el('span', { style: { width: `${(done / total) * 100}%` } })) : null,
          el('div', { class: 'meta', style: { marginTop: '4px' } },
            `${done} من ${total} محطة`),
          el('div', { class: 'driver-actions' },
            el('button', {
              class: 'btn primary',
              disabled: !isToday && route.status === 'PUBLISHED',
              onClick: () => openRoute(route.id),
            }, route.status === 'PUBLISHED' && !isToday
              ? `تبدأ ${fmt.date(route.service_date)}`
              : route.status === 'COMPLETED' ? 'عرض التفاصيل' : 'فتح الرحلة')));
      }) : el('div', { class: 'empty' },
        el('span', { class: 'icon' }, '📭'),
        el('h3', {}, 'لا توجد رحلات منشورة'),
        el('p', { class: 'muted' },
          'ستظهر رحلتك هنا فور نشر خطة اليوم من مشرف مركز الانطلاق.')))));
  updateOfflineBar();
}

async function openRoute(routeId) {
  let detail;
  try {
    detail = (await api.get(`/api/routes/${routeId}`)).data;
  } catch (error) { toastError(error); return; }
  currentRoute = detail;
  // عطل في التتبع لا يجوز أن يمنع السائق من فتح رحلته: التتبع مساعد،
  // وتسجيل المحطات هو العمل. يُسجَّل الخطأ ولا يُبتلع صامتًا.
  if (detail.route.status === 'IN_PROGRESS') {
    try { startTracking(routeId); }
    catch (error) { console.error('تعذر بدء تتبع الموقع', error); }
  }
  renderRoute();
}

function renderRoute() {
  const { route, stops } = currentRoute;
  const workStops = stops.filter((stop) => stop.kind !== 'HUB_START');
  const nextIndex = workStops.findIndex(
    (stop) => !['DONE', 'SKIPPED', 'FAILED'].includes(stop.status));

  mount(root, el('div', { class: 'driver-app' },
    el('div', { class: 'driver-head' },
      el('button', {
        class: 'btn ghost sm', style: { color: '#cfe0da' },
        onClick: () => { stopTracking(); renderRoutes(); },
      }, '← رجوع'),
      el('div', { class: 'title' }, route.reference),
      el('span', { class: 'badge' }, route.status === 'IN_PROGRESS' ? 'جارية'
        : route.status === 'COMPLETED' ? 'مكتملة' : 'منشورة')),

    el('div', { class: 'driver-body' },
      el('div', { class: 'card mb-4' },
        el('div', { class: 'row' },
          el('div', {},
            el('div', { class: 'small muted' }, 'مركز الانطلاق'),
            el('strong', {}, route.hub_name_ar)),
          el('div', {},
            el('div', { class: 'small muted' }, 'البداية'),
            el('strong', {}, fmt.time(route.planned_start_at))),
          el('div', {},
            el('div', { class: 'small muted' }, 'المحطات'),
            el('strong', {}, `${workStops.length}`)),
          route.plate_number ? el('div', {},
            el('div', { class: 'small muted' }, 'المركبة'),
            el('strong', {}, route.plate_number)) : null,
          route.box_code ? el('div', {},
            el('div', { class: 'small muted' }, 'الصندوق'),
            el('strong', {}, route.box_code)) : null)),

      route.status === 'PUBLISHED' ? el('div', { class: 'driver-actions mb-4' },
        el('button', {
          class: 'btn primary lg block',
          onClick: async () => {
            await submitAction({
              action: 'START_ROUTE', route_id: route.id,
              endpoint: `/api/driver/routes/${route.id}/start`,
              successMessage: 'بدأت الرحلة — بالتوفيق',
            });
            try { startTracking(route.id); }
            catch (error) { console.error('تعذر بدء تتبع الموقع', error); }
          },
        }, '▶ ابدأ الرحلة')) : null,

      workStops.map((stop, index) => renderStop(stop, index, nextIndex, route)))));
  updateOfflineBar();
}

function renderStop(stop, index, nextIndex, route) {
  const isNext = index === nextIndex && route.status === 'IN_PROGRESS';
  const isDone = ['DONE', 'SKIPPED', 'FAILED'].includes(stop.status);
  const isPickup = stop.kind === 'PICKUP';
  const late = stop.window_to && new Date() > new Date(stop.window_to) && !isDone;

  return el('div', {
    class: `driver-stop kind-${stop.kind} ${isNext ? 'current' : ''} ${isDone ? 'done' : ''}`,
  },
    el('div', { class: 'head' },
      el('div', { class: 'seq' }, isDone ? '✓' : String(stop.sequence)),
      el('div', { style: { flex: 1 } },
        el('div', { class: 'name' }, stop.facility_name || stop.label_ar),
        el('div', { class: 'meta' },
          isPickup ? 'التقاط' : 'تسليم',
          stop.shipment_reference ? ` · ${stop.shipment_reference}` : '',
          stop.piece_count ? ` · ${stop.piece_count} قطعة` : '')),
      stop.status === 'FAILED' ? el('span', { class: 'badge danger' }, 'استثناء') : null),

    el('div', { class: 'meta' },
      el('div', {},
        `الوقت المخطط: ${fmt.time(stop.planned_arrival_at)}`,
        stop.window_from
          ? ` · النافذة ${fmt.time(stop.window_from)}–${fmt.time(stop.window_to)}` : ''),
      late ? el('div', { style: { color: 'var(--danger)', fontWeight: 700 } },
        '⚠ تجاوزت النافذة الزمنية') : null,
      stop.sla_deadline && !isPickup
        ? el('div', {}, `الموعد النهائي: ${fmt.time(stop.sla_deadline)}`) : null,
      stop.address ? el('div', {}, stop.address) : null,
      stop.contact_name
        ? el('div', {}, `المسؤول: ${stop.contact_name}`
          + (stop.contact_phone ? ` — ${stop.contact_phone}` : '')) : null,
      stop.temperature_mode && stop.temperature_mode !== 'AMBIENT'
        ? el('div', {},
          el('span', { class: 'badge info' },
            `يتطلب ${LABELS.temperature[stop.temperature_mode] || stop.temperature_mode}`))
        : null,
      stop.actual_completed_at
        ? el('div', { style: { color: 'var(--success)', fontWeight: 700 } },
          `نُفذت ${fmt.time(stop.actual_completed_at)}`) : null),

    isNext ? el('div', { class: 'driver-actions' },
      stop.contact_phone ? el('a', { class: 'btn', href: `tel:${stop.contact_phone}` },
        `📞 اتصال بالمسؤول`) : null,
      el('button', {
        class: 'btn',
        onClick: () => openNavigation(stop.lat, stop.lon, stop.facility_name),
      }, '🧭 الملاحة'),

      stop.status === 'PENDING' ? el('button', {
        class: 'btn primary',
        onClick: () => submitAction({
          action: 'ARRIVED', stop_id: stop.id,
          endpoint: `/api/driver/stops/${stop.id}/arrive`,
          successMessage: 'سُجّل الوصول',
        }),
      }, '📍 وصلت') : null,

      stop.status === 'ARRIVED' ? el('button', {
        class: 'btn primary',
        onClick: () => submitAction({
          action: isPickup ? 'PICKED_UP' : 'DELIVERED',
          stop_id: stop.id,
          endpoint: `/api/driver/stops/${stop.id}/${isPickup ? 'pickup' : 'deliver'}`,
          successMessage: isPickup ? 'سُجّل الالتقاط' : 'سُجّل التسليم',
          afterSuccess: () => promptForProof(stop, isPickup),
        }),
      }, isPickup ? '📦 التقطت العينات' : '✅ سلّمت العينات') : null,

      isDone ? el('button', {
        class: 'btn',
        onClick: () => promptForProof(stop, isPickup),
      }, '📷 إرفاق مستند') : null,

      el('button', {
        class: 'btn danger',
        onClick: () => openExceptionDialog(stop),
      }, '⚠ حالة استثنائية')) : null);
}

/* ------------------------------------------------------ تنفيذ الإجراء - */

async function submitAction({ action, endpoint, stop_id, route_id, shipment_id,
  successMessage, extra = {}, afterSuccess }) {
  const clientEventId = newEventId();
  const payload = {
    ...currentCoords(),
    occurred_at: new Date().toISOString(),
    client_event_id: clientEventId,
    ...extra,
  };

  if (!navigator.onLine) {
    queue.push({ action, stop_id, route_id, shipment_id,
      client_event_id: clientEventId, ...payload });
    toast('لا يوجد اتصال — حُفظ الإجراء وسيُرسل تلقائيًا', { tone: 'warning' });
    applyOptimistic(action, stop_id, route_id);
    renderRoute();
    return;
  }

  try {
    await api.post(endpoint, payload);
    toast(successMessage, { tone: 'success' });
    if (route_id || currentRoute) {
      const id = route_id || currentRoute.route.id;
      currentRoute = (await api.get(`/api/routes/${id}`)).data;
      renderRoute();
    }
    afterSuccess?.();
  } catch (error) {
    if (error.status >= 500 || error.code === 'DEPENDENCY_UNAVAILABLE') {
      queue.push({ action, stop_id, route_id, shipment_id,
        client_event_id: clientEventId, ...payload });
      toast('تعذر الاتصال بالخادم — حُفظ الإجراء وسيُرسل لاحقًا', { tone: 'warning' });
      applyOptimistic(action, stop_id, route_id);
      renderRoute();
    } else {
      toastError(error);
    }
  }
}

function applyOptimistic(action, stopId, routeId) {
  if (!currentRoute) return;
  if (action === 'START_ROUTE') {
    currentRoute.route.status = 'IN_PROGRESS';
    return;
  }
  const stop = currentRoute.stops.find((item) => item.id === stopId);
  if (!stop) return;
  if (action === 'ARRIVED') {
    stop.status = 'ARRIVED';
    stop.actual_arrival_at = new Date().toISOString();
  } else if (action === 'PICKED_UP' || action === 'DELIVERED') {
    stop.status = 'DONE';
    stop.actual_completed_at = new Date().toISOString();
  } else if (action === 'EXCEPTION') {
    stop.status = 'FAILED';
  }
}

/* --------------------------------------------------- رفع المستندات --- */

function promptForProof(stop, isPickup) {
  if (!stop.shipment_id) return;
  const input = el('input', {
    type: 'file', accept: 'image/*,application/pdf', capture: 'environment',
    style: { display: 'none' },
    onChange: async (event) => {
      const file = event.target.files?.[0];
      if (!file) return;
      try {
        const buffer = await file.arrayBuffer();
        const coords = currentCoords();
        const query = new URLSearchParams({
          shipment_id: stop.shipment_id,
          doc_kind: isPickup ? 'PICKUP_PROOF' : 'DELIVERY_PROOF',
          stop_id: stop.id,
        });
        if (coords.lat) { query.set('lat', coords.lat); query.set('lon', coords.lon); }
        await api.post(`/api/documents?${query}`, buffer, {
          headers: { 'content-type': file.type || 'image/jpeg', 'x-file-name': file.name },
        });
        toast('رُفع المستند', { tone: 'success' });
      } catch (error) { toastError(error); }
      input.remove();
    },
  });
  document.body.append(input);
  input.click();
}

/* ------------------------------------------------ الحالات الاستثنائية - */

function openExceptionDialog(stop) {
  const reasons = (state.meta?.enums?.exception_reasons || []).filter(
    (item) => item.key !== 'CANCELLED_BEFORE_PICKUP');
  const reasonSelect = select(
    reasons.map((item) => ({ value: item.key, label: item.label_ar })), {});
  const noteInput = el('textarea', { rows: 3, placeholder: 'وصف مختصر لما حدث' });
  const proofNote = el('div', { class: 'help' });

  const REQUIRES_PROOF = ['FACILITY_CLOSED', 'BOX_DAMAGED',
    'LOCATION_UNREACHABLE', 'NO_STAFF'];
  const fileInput = el('input', { type: 'file', accept: 'image/*',
    capture: 'environment' });

  const updateProofNote = () => {
    proofNote.textContent = REQUIRES_PROOF.includes(reasonSelect.value)
      ? '⚠ هذا السبب يتطلب إرفاق صورة إثبات قبل الحفظ'
      : 'الإثبات اختياري لهذا السبب';
  };
  reasonSelect.addEventListener('change', updateProofNote);
  updateProofNote();

  modal({
    title: 'تسجيل حالة استثنائية',
    body: el('div', {},
      el('p', { class: 'muted small' },
        `المحطة: ${stop.facility_name || stop.label_ar}`),
      field('السبب', reasonSelect, { required: true }),
      field('ملاحظة', noteInput),
      field('صورة الإثبات', fileInput, { help: proofNote.textContent }),
      proofNote,
      el('p', { class: 'tiny muted' },
        'لن تُحذف الشحنة ولا تاريخها. إن تعذر التسليم يبقى التزام التسليم مفتوحًا '
        + 'حتى يقرر المشرف الإجراء.')),
    actions: (close) => [
      el('button', {
        class: 'btn danger',
        onClick: async () => {
          const reason = reasonSelect.value;
          const file = fileInput.files?.[0];
          if (REQUIRES_PROOF.includes(reason) && !file) {
            toast('هذا السبب يتطلب صورة إثبات', { tone: 'error' });
            return;
          }
          try {
            const coords = currentCoords();
            const created = await api.post('/api/exceptions', {
              shipment_id: stop.shipment_id,
              reason, note: noteInput.value.trim(),
              stop_id: stop.id, has_proof: !!file,
              client_event_id: newEventId(),
              occurred_at: new Date().toISOString(),
              ...coords,
            });
            if (file) {
              const buffer = await file.arrayBuffer();
              const query = new URLSearchParams({
                shipment_id: stop.shipment_id, doc_kind: 'EXCEPTION_PROOF',
                stop_id: stop.id, exception_id: created.data.exception_id,
              });
              await api.post(`/api/documents?${query}`, buffer, {
                headers: { 'content-type': file.type || 'image/jpeg',
                  'x-file-name': file.name },
              });
            }
            toast('سُجّلت الحالة وأُبلغ المشرف', { tone: 'warning' });
            close();
            currentRoute = (await api.get(`/api/routes/${currentRoute.route.id}`)).data;
            renderRoute();
          } catch (error) { toastError(error); }
        },
      }, 'تسجيل الحالة'),
      el('button', { class: 'btn ghost', onClick: close }, 'إلغاء'),
    ],
  });
}

/* ---------------------------------------------------------- الإقلاع -- */

async function boot() {
  const meta = await api.get('/api/meta');
  state.meta = meta.data;
  loadLabels(meta.data);
  await renderRoutes();
  syncQueue();
  setInterval(syncQueue, 45000);

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => { /* غير حرج */ });
  }
}

setUnauthorizedHandler(() => {
  if (state.user) { state.user = null; stopTracking(); loginScreen('انتهت الجلسة'); }
});

(async () => {
  const probe = await api.get('/api/auth/session');
  if (probe.data?.user) {
    state.user = probe.data.user;
    if (state.user.role !== 'DRIVER') { window.location.href = '/'; return; }
    await boot();
  } else {
    loginScreen();
  }
})();
