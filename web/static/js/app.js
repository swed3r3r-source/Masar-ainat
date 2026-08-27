/* ===================================================================
   قشرة التطبيق: الدخول، التنقل حسب الدور، تسجيل الشاشات.
   =================================================================== */

import {
  api, state, el, mount, clear, can, hasRole, toast, toastError, field, kpi,
  Router, events, loadLabels, setUnauthorizedHandler, fmt, modal,
} from './core.js';

import { dashboardView } from './views/dashboard.js';
import {
  masterDataView, settingsView, usersView, auditView,
} from './views/admin.js';
import {
  importsView, importDetailView, planListView, planRunView, planDetailView,
  routeDetailView, planCompareView,
} from './views/planning.js';
import {
  routesView, assignView, liveMapView, alertsView, exceptionsView,
  shipmentsView, shipmentDetailView, onDemandView, driversPerformanceView,
} from './views/operations.js';
import { reportsView, driverEstimationView, hubMonitorView } from './views/reports.js';

const root = document.getElementById('root');

/* ---------------------------------------------------------- الدخول ---- */

function loginScreen({ message } = {}) {
  const email = el('input', { type: 'email', required: true, autocomplete: 'username',
    placeholder: 'name@organisation.gov.sa' });
  const password = el('input', { type: 'password', required: true,
    autocomplete: 'current-password' });
  const errorNode = el('div', { class: 'error', role: 'alert' }, message || '');
  const submit = el('button', { class: 'btn primary block lg', type: 'submit' }, 'تسجيل الدخول');

  const form = el('form', {
    onSubmit: async (event) => {
      event.preventDefault();
      errorNode.textContent = '';
      submit.disabled = true;
      submit.textContent = 'جارٍ التحقق…';
      try {
        const response = await api.post('/api/auth/login', {
          email: email.value.trim(), password: password.value,
        });
        state.user = response.data.user;
        await bootAuthenticated();
      } catch (error) {
        errorNode.textContent = error.message;
        submit.disabled = false;
        submit.textContent = 'تسجيل الدخول';
        password.value = '';
        password.focus();
      }
    },
  },
    field('البريد الإلكتروني', email, { required: true }),
    field('كلمة المرور', password, { required: true }),
    errorNode,
    submit,
    el('button', {
      type: 'button', class: 'btn ghost block', style: { marginTop: '8px' },
      onClick: () => resetPasswordDialog(email.value.trim()),
    }, 'نسيت كلمة المرور؟'));

  mount(root, el('div', { class: 'auth-shell' },
    el('div', { class: 'auth-card' },
      el('div', { class: 'logo' },
        el('div', { class: 'mark' }, 'م'),
        el('div', {},
          el('div', { class: 'name' }, 'مسار عينات'),
          el('div', { class: 'tag' }, 'منصة تخطيط وتشغيل نقل العينات الطبية'))),
      form)));
  email.focus();
}

function resetPasswordDialog(prefill = '') {
  const input = el('input', { type: 'email', value: prefill });
  modal({
    title: 'استعادة كلمة المرور',
    body: el('div', {},
      el('p', { class: 'muted small' },
        'سيُرسل رابط الاستعادة إلى بريدك إن كان مسجّلًا في النظام.'),
      field('البريد الإلكتروني', input, { required: true })),
    actions: (close) => [
      el('button', {
        class: 'btn primary',
        onClick: async () => {
          try {
            const response = await api.post('/api/auth/password/reset',
              { email: input.value.trim() });
            close();
            if (response.data?.dev_token) {
              toast(`رمز الاستعادة (بيئة تطوير): ${response.data.dev_token}`,
                { title: 'أُنشئ الرمز', timeout: 20000 });
            } else {
              toast('إن كان البريد مسجلًا فسيصلك رابط الاستعادة', { tone: 'success' });
            }
          } catch (error) { toastError(error); }
        },
      }, 'إرسال'),
      el('button', { class: 'btn ghost', onClick: close }, 'إلغاء'),
    ],
  });
}

/* ------------------------------------------------------- بنية التنقل -- */

const NAV = [
  {
    label: 'الرئيسية',
    items: [
      { path: '/', title: 'لوحة المعلومات', icon: '▣' },
      { path: '/alerts', title: 'التنبيهات', icon: '⚠', permission: 'alerts.read',
        badgeKey: 'alerts' },
      { path: '/live', title: 'الخريطة المباشرة', icon: '◎', permission: 'tracking.read' },
    ],
  },
  {
    label: 'التخطيط المركزي',
    permission: 'plan.read',
    items: [
      { path: '/imports', title: 'رفع الجدول الأسبوعي', icon: '⬆', permission: 'schedule.read' },
      { path: '/plans', title: 'الخطط', icon: '◫', permission: 'plan.read' },
      { path: '/plans/new', title: 'إنشاء المسارات', icon: '⚙', permission: 'plan.optimize' },
      { path: '/estimation', title: 'تقدير السائقين', icon: '👤', permission: 'driver_estimation.read' },
      { path: '/hub-monitor', title: 'مراقبة تعديلات المراكز', icon: '≠',
        permission: 'hub_changes.monitor' },
    ],
  },
  {
    label: 'التشغيل',
    items: [
      { path: '/routes', title: 'الرحلات', icon: '⇉', permission: 'routes.read' },
      { path: '/assign', title: 'الإسناد والنشر', icon: '⊞', permission: 'routes.assign' },
      { path: '/ondemand', title: 'الطلبات الفورية', icon: '⚡', permission: 'shipments.read' },
      { path: '/shipments', title: 'الشحنات', icon: '▤', permission: 'shipments.read' },
      { path: '/exceptions', title: 'الحالات الاستثنائية', icon: '⊘',
        permission: 'shipments.read' },
      { path: '/drivers-performance', title: 'أداء السائقين', icon: '★',
        permission: 'reports.read' },
    ],
  },
  {
    label: 'التقارير والرقابة',
    items: [
      { path: '/reports', title: 'التقارير والمؤشرات', icon: '📈', permission: 'reports.read' },
      { path: '/audit', title: 'سجل التدقيق', icon: '🗒', permission: 'audit.read' },
    ],
  },
  {
    label: 'الإدارة',
    permission: 'users.read',
    items: [
      { path: '/users', title: 'المستخدمون والأدوار', icon: '👥', permission: 'users.read' },
      { path: '/md/regions', title: 'المناطق', icon: '🗺', permission: 'geo.read' },
      { path: '/md/cities', title: 'المدن والمحافظات', icon: '🏙', permission: 'geo.read' },
      { path: '/md/hubs', title: 'مراكز الانطلاق', icon: '🏢', permission: 'hubs.read' },
      { path: '/md/facilities', title: 'الجهات الصحية', icon: '🏥', permission: 'facilities.read' },
      { path: '/md/drivers', title: 'السائقون', icon: '🚗', permission: 'drivers.read' },
      { path: '/md/vehicles', title: 'المركبات', icon: '🚐', permission: 'vehicles.read' },
      { path: '/md/boxes', title: 'الصناديق', icon: '📦', permission: 'vehicles.read' },
      { path: '/settings', title: 'الإعدادات والقيود', icon: '⚙', permission: 'settings.read' },
    ],
  },
];

const ROUTES = [
  { path: '/', view: dashboardView },
  { path: '/alerts', view: alertsView, permission: 'alerts.read' },
  { path: '/live', view: liveMapView, permission: 'tracking.read' },
  { path: '/imports', view: importsView, permission: 'schedule.read' },
  { path: '/imports/:id', view: importDetailView, permission: 'schedule.read' },
  { path: '/plans', view: planListView, permission: 'plan.read' },
  { path: '/plans/new', view: planRunView, permission: 'plan.optimize' },
  { path: '/plans/compare', view: planCompareView, permission: 'plan.compare' },
  { path: '/plans/:id', view: planDetailView, permission: 'plan.read' },
  { path: '/routes', view: routesView, permission: 'routes.read' },
  { path: '/routes/:id', view: routeDetailView, permission: 'routes.read' },
  { path: '/assign', view: assignView, permission: 'routes.assign' },
  { path: '/ondemand', view: onDemandView, permission: 'shipments.read' },
  { path: '/shipments', view: shipmentsView, permission: 'shipments.read' },
  { path: '/shipments/:id', view: shipmentDetailView, permission: 'shipments.read' },
  { path: '/exceptions', view: exceptionsView, permission: 'shipments.read' },
  { path: '/drivers-performance', view: driversPerformanceView, permission: 'reports.read' },
  { path: '/estimation', view: driverEstimationView, permission: 'driver_estimation.read' },
  { path: '/hub-monitor', view: hubMonitorView, permission: 'hub_changes.monitor' },
  { path: '/reports', view: reportsView, permission: 'reports.read' },
  { path: '/audit', view: auditView, permission: 'audit.read' },
  { path: '/users', view: usersView, permission: 'users.read' },
  { path: '/settings', view: settingsView, permission: 'settings.read' },
  { path: '/md/:entity', view: masterDataView },
];

/* --------------------------------------------------------- القشرة ----- */

let router = null;
const badges = { alerts: 0 };

function buildShell() {
  const navHost = el('nav', { class: 'app-nav', 'aria-label': 'التنقل الرئيسي' });
  const outlet = el('main', { class: 'app-main', id: 'view-outlet' });
  const cfg = state.meta?.config || {};

  const navToggle = el('button', {
    class: 'nav-toggle', 'aria-label': 'القائمة',
    onClick: () => navHost.classList.toggle('open'),
  }, '☰');

  const header = el('header', { class: 'app-header' },
    navToggle,
    el('div', { class: 'brand' },
      el('div', { class: 'mark' }, 'م'),
      el('span', {}, 'مسار عينات')),
    el('div', { class: 'spacer' }),
    el('div', { class: 'who' },
      el('strong', {}, state.user.full_name),
      state.meta?.roles?.find((r) => r.key === state.user.role)?.label_ar || state.user.role),
    el('button', {
      class: 'btn ghost sm', style: { color: '#cfe0da' },
      onClick: async () => {
        try { await api.post('/api/auth/logout', {}); } catch { /* تجاهل */ }
        state.user = null;
        events.close();
        loginScreen({ message: '' });
      },
    }, 'خروج'));

  const shell = el('div', { class: 'app' },
    header,
    cfg.environment !== 'production'
      ? el('div', { class: 'env-ribbon' },
        `بيئة ${cfg.environment === 'test' ? 'اختبار' : 'تطوير'} — البيانات المعروضة تجريبية وموسومة كذلك`
          + (cfg.routing_is_estimated ? ' · أزمنة القيادة تقديرية وليست أزمنة طريق حقيقية' : ''))
      : null,
    el('div', { class: 'app-body' }, navHost, outlet));

  mount(root, shell);
  renderNav(navHost);
  return { navHost, outlet };
}

function renderNav(navHost) {
  clear(navHost);
  for (const group of NAV) {
    const items = group.items.filter((item) => !item.permission || can(item.permission));
    if (!items.length) continue;
    if (group.permission && !can(group.permission)) continue;
    navHost.append(el('div', { class: 'nav-group' },
      el('div', { class: 'label' }, group.label),
      items.map((item) => el('button', {
        class: 'nav-item',
        dataset: { path: item.path },
        onClick: () => {
          router.go(item.path);
          navHost.classList.remove('open');
        },
      },
        el('span', { 'aria-hidden': 'true' }, item.icon),
        el('span', {}, item.title),
        item.badgeKey && badges[item.badgeKey]
          ? el('span', { class: 'badge danger' }, String(badges[item.badgeKey]))
          : null))));
  }
}

function highlightNav(path) {
  for (const node of document.querySelectorAll('.nav-item')) {
    const target = node.dataset.path;
    const active = target === path
      || (target !== '/' && path.startsWith(target + '/'));
    node.classList.toggle('active', !!active);
  }
}

/* --------------------------------------------------------- الإقلاع --- */

async function refreshAlertBadge() {
  if (!can('alerts.read')) return;
  try {
    const response = await api.get('/api/alerts', { query: { only_open: 'true', limit: 1 } });
    badges.alerts = response.pagination?.total || 0;
    const navHost = document.querySelector('.app-nav');
    if (navHost) {
      renderNav(navHost);
      highlightNav(router?.current?.path || '/');
    }
  } catch { /* غير حرج */ }
}

async function bootAuthenticated() {
  const meta = await api.get('/api/meta');
  state.meta = meta.data;
  loadLabels(meta.data);

  if (can('hubs.read')) {
    try {
      const hubs = await api.get('/api/md/hubs', { query: { limit: 500 } });
      state.hubs = hubs.data || [];
      state.activeHubId = state.user.hub_ids?.[0] || state.hubs[0]?.id || null;
    } catch { state.hubs = []; }
  }

  const { outlet } = buildShell();
  router = new Router(outlet, ROUTES, {
    onNavigate: (route, path) => highlightNav(path),
  });
  router.start();

  events.connect();
  events.on('alert', () => { refreshAlertBadge(); });
  events.on('*', (payload) => {
    if (payload.topic === 'alert' && payload.payload?.severity === 'CRITICAL') {
      toast(payload.payload.body_ar, { title: payload.payload.title_ar, tone: 'error',
        timeout: 12000 });
    }
  });
  refreshAlertBadge();
  setInterval(refreshAlertBadge, 60000);

  if (state.user.must_change_password) {
    toast('يجب تغيير كلمة المرور المؤقتة من الملف الشخصي', { tone: 'warning', timeout: 12000 });
  }
}

async function boot() {
  setUnauthorizedHandler(() => {
    if (state.user) {
      state.user = null;
      events.close();
      loginScreen({ message: 'انتهت الجلسة — يرجى تسجيل الدخول من جديد' });
    }
  });

  // نقطة واحدة تعيد 200 دائمًا: إما مستخدم أو null — بلا أخطاء في السجل
  const probe = await api.get('/api/auth/session');
  if (probe.data?.user) {
    state.user = probe.data.user;
    await bootAuthenticated();
  } else {
    loginScreen();
  }
}

boot().catch((error) => {
  console.error(error);
  mount(root, el('div', { class: 'empty', style: { margin: '80px auto', maxWidth: '520px' } },
    el('span', { class: 'icon' }, '⚠️'),
    el('h3', {}, 'تعذر بدء التطبيق'),
    el('p', { class: 'muted' }, error.message || String(error))));
});

export { router };
