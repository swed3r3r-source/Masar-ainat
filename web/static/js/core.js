/* ===================================================================
   نواة الواجهة: عميل API، الحالة، التوجيه، التنسيق، الإشعارات.
   بلا أي مكتبة خارجية.
   =================================================================== */

/* --------------------------------------------------------- عميل API ---- */

export class ApiError extends Error {
  constructor(status, payload) {
    const error = payload?.error || {};
    super(error.message || `خطأ ${status}`);
    this.status = status;
    this.code = error.code || 'ERROR';
    this.details = error.details || {};
  }
}

let onUnauthorized = null;
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

async function request(method, path, { body, headers, raw, query } = {}) {
  const url = new URL(path, window.location.origin);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, value);
      }
    }
  }

  const init = { method, headers: { ...(headers || {}) }, credentials: 'same-origin' };
  if (body instanceof ArrayBuffer || body instanceof Blob || body instanceof Uint8Array) {
    init.body = body;
  } else if (body !== undefined) {
    init.headers['content-type'] = 'application/json';
    init.body = JSON.stringify(body);
  }

  const response = await fetch(url, init);
  if (response.status === 401 && onUnauthorized) onUnauthorized();

  if (raw) {
    if (!response.ok) throw new ApiError(response.status, await safeJson(response));
    return response;
  }
  const payload = await safeJson(response);
  if (!response.ok || payload?.ok === false) throw new ApiError(response.status, payload);
  return payload;
}

async function safeJson(response) {
  try { return await response.json(); } catch { return null; }
}

export const api = {
  get:   (path, options) => request('GET', path, options),
  post:  (path, body, options) => request('POST', path, { ...options, body }),
  patch: (path, body, options) => request('PATCH', path, { ...options, body }),
  del:   (path, options) => request('DELETE', path, options),
  raw:   (method, path, options) => request(method, path, { ...options, raw: true }),
};

/* ---------------------------------------------------------- الحالة ----- */

export const state = {
  user: null,
  meta: null,
  hubs: [],
  activeHubId: null,
  listeners: new Set(),
};

export function subscribe(fn) {
  state.listeners.add(fn);
  return () => state.listeners.delete(fn);
}

export function notifyState() {
  for (const fn of state.listeners) { try { fn(state); } catch (e) { console.error(e); } }
}

export function can(permission) {
  return !!state.user?.permissions?.includes(permission);
}

export function hasRole(...roles) {
  return roles.includes(state.user?.role);
}

/* ------------------------------------------------------- بناء العناصر -- */

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'style' && typeof value === 'object') Object.assign(node.style, value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  appendAll(node, children);
  return node;
}

function appendAll(node, children) {
  for (const child of children.flat(4)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

export function mount(node, ...children) { clear(node); appendAll(node, children); return node; }

/* ------------------------------------------------------------ التنسيق - */

const TZ = 'Asia/Riyadh';

const dateFmt = new Intl.DateTimeFormat('ar-SA-u-nu-latn-ca-gregory',
  { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' });
const timeFmt = new Intl.DateTimeFormat('ar-SA-u-nu-latn',
  { timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false });
const dateTimeFmt = new Intl.DateTimeFormat('ar-SA-u-nu-latn-ca-gregory',
  { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false });

export const fmt = {
  date(value) { return value ? dateFmt.format(new Date(value)) : '—'; },
  time(value) { return value ? timeFmt.format(new Date(value)) : '—'; },
  dateTime(value) { return value ? dateTimeFmt.format(new Date(value)) : '—'; },
  isoDate(value) {
    const d = value ? new Date(value) : new Date();
    const local = new Date(d.getTime() + 3 * 3600 * 1000);
    return local.toISOString().slice(0, 10);
  },
  num(value, digits = 0) {
    if (value === null || value === undefined || value === '') return '—';
    const n = Number(value);
    if (Number.isNaN(n)) return '—';
    return n.toLocaleString('ar-SA-u-nu-latn',
      { minimumFractionDigits: digits, maximumFractionDigits: digits });
  },
  minutes(value) {
    if (value === null || value === undefined) return '—';
    const total = Math.round(Number(value));
    const sign = total < 0 ? '−' : '';
    const abs = Math.abs(total);
    const h = Math.floor(abs / 60), m = abs % 60;
    if (h && m) return `${sign}${h} س ${m} د`;
    if (h) return `${sign}${h} س`;
    return `${sign}${m} د`;
  },
  km(value) {
    if (value === null || value === undefined) return '—';
    return `${Number(value).toLocaleString('ar-SA-u-nu-latn',
      { maximumFractionDigits: 1 })} كم`;
  },
  money(value) {
    if (value === null || value === undefined) return '—';
    return `${Number(value).toLocaleString('ar-SA-u-nu-latn',
      { maximumFractionDigits: 0 })} ر.س`;
  },
  pct(value, digits = 1) {
    if (value === null || value === undefined) return '—';
    return `${Number(value).toFixed(digits)}٪`;
  },
  ago(value) {
    if (!value) return '—';
    const seconds = Math.round((Date.now() - new Date(value).getTime()) / 1000);
    if (seconds < 45) return 'الآن';
    if (seconds < 3600) return `قبل ${Math.round(seconds / 60)} دقيقة`;
    if (seconds < 86400) return `قبل ${Math.round(seconds / 3600)} ساعة`;
    return `قبل ${Math.round(seconds / 86400)} يوم`;
  },
};

/* ---------------------------------------------------------- التسميات -- */

export const LABELS = {
  status: {},
  facilityType: {},
  serviceType: {},
  temperature: {},
  exceptionReason: {},
  role: {},
};

export function loadLabels(meta) {
  const put = (target, list) => { for (const item of list || []) target[item.key] = item.label_ar; };
  put(LABELS.status, meta.enums?.shipment_statuses);
  put(LABELS.facilityType, meta.enums?.facility_types);
  put(LABELS.serviceType, meta.enums?.service_types);
  put(LABELS.temperature, meta.enums?.temperature_modes);
  put(LABELS.exceptionReason, meta.enums?.exception_reasons);
  put(LABELS.role, meta.roles);
}

export const ROUTE_STATUS_LABEL = {
  DRAFT: 'مسودة', PLANNED: 'مخططة', ASSIGNED: 'مُسندة', PUBLISHED: 'منشورة',
  IN_PROGRESS: 'قيد التنفيذ', COMPLETED: 'مكتملة', CANCELLED: 'ملغاة',
};
export const PLAN_STATUS_LABEL = {
  DRAFT: 'مسودة', OPTIMIZING: 'قيد التحسين', OPTIMIZED: 'مُحسّنة',
  APPROVED: 'معتمدة', DISPATCHED: 'مُرسلة للمراكز', SUPERSEDED: 'مُستبدلة',
  FAILED: 'فشلت',
};
export const SEVERITY_LABEL = {
  INFO: 'معلومة', LOW: 'منخفضة', MEDIUM: 'متوسطة', HIGH: 'عالية', CRITICAL: 'حرجة',
};
export const WARNING_LABEL = {
  SLA_TIGHT: 'هامش SLA ضيق',
  WINDOW_TIGHT: 'نافذة التقاط ضيقة',
  LONG_WAIT: 'انتظار طويل',
  SHIFT_NEAR_LIMIT: 'اقتراب حد الوردية',
  LONG_HAUL_ROUTE: 'رحلة بعيدة',
  MIXED_FACILITY_EXEMPTION_USED: 'استُخدم استثناء الخلط',
  ESTIMATED_TRAVEL_TIME: 'أزمنة قيادة تقديرية',
  UNASSIGNED_ROUTE: 'رحلة بلا سائق',
  UNPLANNABLE_SHIPMENT: 'شحنة غير قابلة للتخطيط',
  DRIVER_SHORTAGE: 'نقص سائقين',
  UNBALANCED_WORKLOAD: 'توزيع عمل غير متوازن',
  SECOND_PICKUP_ORDER_FORCED: 'ترتيب التقاط ثانٍ مفروض',
};
export const ALERT_LABEL = {
  PICKUP_WINDOW_APPROACHING: 'اقتراب موعد الالتقاط',
  PICKUP_LATE: 'تأخر الالتقاط',
  DELIVERY_LATE: 'تأخر التسليم',
  SLA_AT_RISK: 'خطر تجاوز SLA',
  SLA_BREACHED: 'تجاوز SLA',
  REQUEST_CANCELLED: 'إلغاء طلب',
  SAMPLES_NOT_READY: 'العينات غير جاهزة',
  PICKUP_FAILED: 'تعذر الالتقاط',
  DELIVERY_FAILED: 'تعذر التسليم',
  TEMPERATURE_BREACH: 'مخالفة حرارة',
  TRACKING_STALE: 'توقف تحديث الموقع',
  PUBLISHED_ROUTE_MODIFIED: 'تعديل رحلة منشورة',
  NEW_ON_DEMAND_REQUEST: 'طلب فوري جديد',
  ROUTE_WITHOUT_DRIVER: 'رحلة بلا سائق',
  DRIVER_SHORTAGE: 'نقص سائقين',
  ASSIGNMENT_CONFLICT: 'تعارض إسناد',
};

const STATUS_TONE = {
  COMPLETED: 'success', DELIVERED: 'success', PUBLISHED: 'info',
  IN_PROGRESS: 'info', ASSIGNED: 'brand', PLANNED: 'brand',
  PENDING_ASSIGNMENT: 'warning', PENDING_APPROVAL: 'warning',
  EXCEPTION: 'danger', FAILED: 'danger', UNPLANNABLE: 'danger',
  REJECTED: 'danger', CANCELLED_BEFORE_PICKUP: 'warning',
  ARRIVED_PICKUP: 'info', ARRIVED_DELIVERY: 'info', PICKED_UP: 'info',
};

export function statusBadge(status, labels = LABELS.status) {
  return el('span', { class: `badge ${STATUS_TONE[status] || ''}` },
    labels[status] || status || '—');
}

export function severityBadge(severity) {
  const tone = { CRITICAL: 'critical', HIGH: 'danger', MEDIUM: 'warning',
                 LOW: 'info', INFO: '' }[severity] || '';
  return el('span', { class: `badge ${tone}` }, SEVERITY_LABEL[severity] || severity);
}

/* -------------------------------------------------------- الإشعارات --- */

let toastHost = null;

export function toast(message, { title, tone = '', timeout = 5200 } = {}) {
  if (!toastHost) {
    toastHost = el('div', { class: 'toasts', role: 'status', 'aria-live': 'polite' });
    document.body.append(toastHost);
  }
  const node = el('div', { class: `toast ${tone}` },
    title ? el('strong', {}, title) : null, message);
  toastHost.append(node);
  setTimeout(() => node.remove(), timeout);
  return node;
}

export function toastError(error, fallback = 'تعذر إتمام العملية') {
  const message = error instanceof ApiError ? error.message : (error?.message || fallback);
  const details = error instanceof ApiError ? error.details : null;
  let extra = '';
  if (details?.blockers?.length) extra = ' — ' + details.blockers.join('؛ ');
  else if (details?.violations?.length) {
    extra = ' — ' + details.violations.map((v) => v.detail_ar).join('؛ ');
  } else if (details?.missing_fields?.length) {
    extra = ' — ' + details.missing_fields.join('، ');
  }
  toast(message + extra, { title: 'خطأ', tone: 'error', timeout: 9000 });
  return null;
}

/* ---------------------------------------------------------- الحوارات -- */

export function modal({ title, body, actions, wide = false, onClose }) {
  const backdrop = el('div', { class: 'modal-backdrop' });
  const close = () => { backdrop.remove(); document.removeEventListener('keydown', onKey); onClose?.(); };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);

  const dialog = el('div', { class: `modal ${wide ? 'wide' : ''}`, role: 'dialog', 'aria-modal': 'true' },
    el('div', { class: 'modal-head' },
      el('h3', {}, title),
      el('button', { class: 'btn ghost sm', onClick: close, 'aria-label': 'إغلاق' }, '✕')),
    el('div', { class: 'modal-body' }, body),
    actions ? el('div', { class: 'modal-foot' }, actions(close)) : null);

  backdrop.append(dialog);
  backdrop.addEventListener('click', (event) => { if (event.target === backdrop) close(); });
  document.body.append(backdrop);
  const focusable = dialog.querySelector('input, select, textarea, button.primary');
  focusable?.focus();
  return { close, dialog };
}

export function confirmDialog({ title, message, confirmLabel = 'تأكيد', tone = 'primary',
                                requireReason = false, reasonLabel = 'سبب الإجراء',
                                minReason = 3 }) {
  return new Promise((resolve) => {
    const reasonInput = el('textarea', {
      rows: 3, placeholder: 'اكتب السبب — يُحفظ في سجل التدقيق ولا يمكن تعديله لاحقًا',
    });
    const errorNode = el('div', { class: 'error' });
    let settled = false;

    const { close } = modal({
      title,
      body: el('div', {},
        el('p', {}, message),
        requireReason ? el('div', { class: 'field required' },
          el('label', {}, reasonLabel), reasonInput, errorNode) : null),
      actions: (closeFn) => [
        el('button', {
          class: `btn ${tone}`,
          onClick: () => {
            const reason = reasonInput.value.trim();
            if (requireReason && reason.length < minReason) {
              errorNode.textContent = `السبب مطلوب (${minReason} أحرف على الأقل)`;
              reasonInput.focus();
              return;
            }
            settled = true; closeFn(); resolve(requireReason ? reason : true);
          },
        }, confirmLabel),
        el('button', { class: 'btn ghost', onClick: () => { settled = true; closeFn(); resolve(null); } },
          'إلغاء'),
      ],
      onClose: () => { if (!settled) resolve(null); },
    });
  });
}

/* ---------------------------------------------------------- التوجيه --- */

export class Router {
  constructor(outlet, routes, { onNavigate } = {}) {
    this.outlet = outlet;
    this.routes = routes;
    this.onNavigate = onNavigate;
    this.current = null;
    window.addEventListener('hashchange', () => this.resolve());
  }

  start() { this.resolve(); }

  go(path) {
    if (window.location.hash === `#${path}`) this.resolve();
    else window.location.hash = path;
  }

  parse() {
    const raw = window.location.hash.replace(/^#/, '') || '/';
    const [pathPart, queryPart] = raw.split('?');
    const segments = pathPart.split('/').filter(Boolean);
    const query = Object.fromEntries(new URLSearchParams(queryPart || ''));
    return { path: '/' + segments.join('/'), segments, query };
  }

  async resolve() {
    const { path, segments, query } = this.parse();
    let matched = null;
    let params = {};

    for (const route of this.routes) {
      const routeSegments = route.path.split('/').filter(Boolean);
      if (routeSegments.length !== segments.length) continue;
      const candidate = {};
      let ok = true;
      for (let i = 0; i < routeSegments.length; i += 1) {
        if (routeSegments[i].startsWith(':')) candidate[routeSegments[i].slice(1)] = segments[i];
        else if (routeSegments[i] !== segments[i]) { ok = false; break; }
      }
      if (ok) { matched = route; params = candidate; break; }
    }

    if (!matched) matched = this.routes.find((r) => r.path === '/') || this.routes[0];
    if (matched.permission && !can(matched.permission)) {
      mount(this.outlet, el('div', { class: 'empty' },
        el('span', { class: 'icon' }, '⛔'),
        el('h3', {}, 'لا تملك صلاحية الوصول إلى هذه الشاشة'),
        el('p', { class: 'muted' }, `الصلاحية المطلوبة: ${matched.permission}`)));
      this.onNavigate?.(matched, path);
      return;
    }

    this.current = { route: matched, params, query, path };
    mount(this.outlet, el('div', { class: 'loading-block' },
      el('span', { class: 'spinner' }), ' جارٍ التحميل…'));
    this.onNavigate?.(matched, path);
    try {
      const view = await matched.view({ params, query, router: this });
      mount(this.outlet, view);
      this.outlet.scrollTop = 0;
      window.scrollTo({ top: 0 });
    } catch (error) {
      console.error(error);
      mount(this.outlet, el('div', { class: 'empty' },
        el('span', { class: 'icon' }, '⚠️'),
        el('h3', {}, 'تعذر تحميل الشاشة'),
        el('p', { class: 'muted' }, error.message || String(error)),
        el('button', { class: 'btn', onClick: () => this.resolve() }, 'إعادة المحاولة')));
    }
  }
}

/* ------------------------------------------------ التحديثات الفورية --- */

export class EventStream {
  constructor() {
    this.source = null;
    this.handlers = new Map();
    this.connected = false;
  }

  connect(topics = []) {
    if (this.source) this.source.close();
    const url = topics.length ? `/api/events?topics=${topics.join(',')}` : '/api/events';
    this.source = new EventSource(url, { withCredentials: true });
    this.source.addEventListener('ready', () => { this.connected = true; });
    this.source.addEventListener('masar', (event) => {
      let payload;
      try { payload = JSON.parse(event.data); } catch { return; }
      for (const handler of this.handlers.get(payload.topic) || []) handler(payload);
      for (const handler of this.handlers.get('*') || []) handler(payload);
    });
    this.source.addEventListener('error', () => { this.connected = false; });
    return this;
  }

  on(topic, handler) {
    if (!this.handlers.has(topic)) this.handlers.set(topic, new Set());
    this.handlers.get(topic).add(handler);
    return () => this.handlers.get(topic)?.delete(handler);
  }

  close() { this.source?.close(); this.source = null; this.connected = false; }
}

export const events = new EventStream();

/* ------------------------------------------------------------ مساعدات - */

export function table(columns, rows, { onRowClick, empty = 'لا توجد بيانات' } = {}) {
  if (!rows?.length) {
    return el('div', { class: 'empty' }, el('span', { class: 'icon' }, '∅'), empty);
  }
  return el('div', { class: 'table-wrap' },
    el('table', { class: 'data' },
      el('thead', {}, el('tr', {}, columns.map((column) =>
        el('th', { class: column.numeric ? 'num' : '' }, column.label)))),
      el('tbody', {}, rows.map((row) =>
        el('tr', {
          class: onRowClick ? 'clickable' : '',
          onClick: onRowClick ? () => onRowClick(row) : null,
        }, columns.map((column) =>
          el('td', { class: `${column.numeric ? 'num' : ''} ${column.wrap ? 'wrap' : ''}` },
            column.render ? column.render(row) : (row[column.key] ?? '—'))))))));
}

export function kpi(label, value, { unit, hint, tone, onClick } = {}) {
  return el('div', {
    class: `kpi ${tone ? `accent-${tone}` : ''} ${onClick ? 'clickable' : ''}`,
    onClick,
  },
    el('div', { class: 'label' }, label),
    el('div', { class: 'value' }, value, unit ? el('span', { class: 'unit' }, ' ' + unit) : null),
    hint ? el('div', { class: 'hint' }, hint) : null);
}

export function field(label, control, { help, required, error } = {}) {
  return el('div', { class: `field ${required ? 'required' : ''}` },
    el('label', {}, label), control,
    help ? el('div', { class: 'help' }, help) : null,
    error ? el('div', { class: 'error' }, error) : null);
}

export function select(options, { value, onChange, placeholder } = {}) {
  const node = el('select', { onChange: onChange ? (e) => onChange(e.target.value) : null });
  if (placeholder) node.append(el('option', { value: '' }, placeholder));
  for (const option of options) {
    node.append(el('option', {
      value: option.value,
      selected: String(option.value) === String(value) || null,
    }, option.label));
  }
  return node;
}

export function debounce(fn, delay = 300) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), delay); };
}

export function downloadUrl(url, filename) {
  const link = el('a', { href: url, download: filename || '' });
  document.body.append(link);
  link.click();
  link.remove();
}
