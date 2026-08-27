/* لوحة المعلومات — تتغير حسب دور المستخدم ونطاقه. */

import {
  api, el, state, can, hasRole, fmt, kpi, table, statusBadge, severityBadge,
  ALERT_LABEL, ROUTE_STATUS_LABEL, toastError,
} from '../core.js';

export async function dashboardView({ router }) {
  const today = fmt.isoDate();
  const scopeHub = hasRole('HUB_SUPERVISOR') ? state.user.hub_ids?.[0] : null;

  const [kpiRes, routesRes, alertsRes, alertSummary] = await Promise.all([
    can('reports.read')
      ? api.get('/api/reports/kpi', { query: { date_from: today, date_to: today,
          hub_id: scopeHub, include_test_data: 'true' } }).catch(() => null)
      : null,
    can('routes.read')
      ? api.get('/api/routes', { query: { service_date: today, hub_id: scopeHub } })
        .catch(() => null)
      : null,
    can('alerts.read')
      ? api.get('/api/alerts', { query: { only_open: 'true', limit: 8, hub_id: scopeHub } })
        .catch(() => null)
      : null,
    can('alerts.read') ? api.get('/api/alerts/summary').catch(() => null) : null,
  ]);

  const metrics = kpiRes?.data || {};
  const routes = routesRes?.data || [];
  const alerts = alertsRes?.data || [];

  const inProgress = routes.filter((r) => r.status === 'IN_PROGRESS').length;
  const published = routes.filter((r) => r.status === 'PUBLISHED').length;
  const unassigned = routes.filter((r) => !r.driver_id).length;
  const completed = routes.filter((r) => r.status === 'COMPLETED').length;

  const roleLabel = state.meta?.roles?.find((r) => r.key === state.user.role)?.label_ar;

  return el('div', {},
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, `أهلًا، ${state.user.full_name}`),
        el('p', { class: 'subtitle' },
          `${roleLabel} · ${fmt.date(new Date())}`,
          scopeHub && state.hubs.length
            ? ` · ${state.hubs.find((h) => h.id === scopeHub)?.name_ar || ''}`
            : ''))),

    // ------------------------------------------------ مؤشرات اليوم ----
    el('h3', {}, 'اليوم التشغيلي'),
    el('div', { class: 'kpi-grid mb-4' },
      kpi('رحلات اليوم', fmt.num(routes.length), {
        hint: `${published} منشورة · ${inProgress} جارية · ${completed} مكتملة`,
        onClick: () => router.go('/routes'),
      }),
      kpi('رحلات بلا سائق', fmt.num(unassigned), {
        tone: unassigned ? 'danger' : 'success',
        hint: unassigned ? 'تحتاج إسنادًا قبل النشر' : 'كل الرحلات مُسندة',
        onClick: can('routes.assign') ? () => router.go('/assign') : null,
      }),
      kpi('شحنات اليوم', fmt.num(metrics.shipment_count ?? '—'), {
        hint: `${fmt.num(metrics.completed_count || 0)} مكتملة`,
        onClick: () => router.go('/shipments'),
      }),
      kpi('الالتزام بـ SLA', metrics.sla_compliance_pct !== null
        && metrics.sla_compliance_pct !== undefined
        ? fmt.pct(metrics.sla_compliance_pct) : '—', {
        tone: metrics.sla_compliance_pct === null ? '' :
          metrics.sla_compliance_pct >= 95 ? 'success'
            : metrics.sla_compliance_pct >= 85 ? 'warning' : 'danger',
        hint: metrics.sla_breached_count
          ? `${fmt.num(metrics.sla_breached_count)} شحنة تجاوزت الموعد`
          : 'لا تجاوزات مسجلة اليوم',
      }),
      kpi('حالات استثنائية', fmt.num(metrics.exception_count || 0), {
        tone: metrics.exception_count ? 'warning' : '',
        hint: metrics.open_obligation_count
          ? `${fmt.num(metrics.open_obligation_count)} التزام تسليم مفتوح`
          : 'لا التزامات مفتوحة',
        onClick: () => router.go('/exceptions'),
      }),
      kpi('طلبات فورية', fmt.num(metrics.on_demand_count || 0), {
        tone: 'info',
        onClick: () => router.go('/ondemand'),
      })),

    // -------------------------------------------------- التنبيهات ----
    el('div', { class: 'grid split' },
      el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h3', {}, 'رحلات اليوم'),
          el('div', { class: 'spacer' }),
          el('button', { class: 'btn sm ghost', onClick: () => router.go('/routes') },
            'عرض الكل')),
        table([
          { label: 'الرحلة', key: 'reference' },
          { label: 'المركز', key: 'hub_name_ar' },
          { label: 'السائق', render: (r) => r.driver_name
            || el('span', { class: 'badge danger' }, 'بلا سائق') },
          { label: 'الحالة', render: (r) => statusBadge(r.status, ROUTE_STATUS_LABEL) },
          { label: 'المحطات', numeric: true,
            render: (r) => `${r.pickup_count}↑ ${r.delivery_count}↓` },
          { label: 'المسافة', numeric: true, render: (r) => fmt.km(r.distance_km) },
          { label: 'البداية', numeric: true, render: (r) => fmt.time(r.planned_start_at) },
        ], routes.slice(0, 12), {
          onRowClick: (row) => router.go(`/routes/${row.id}`),
          empty: 'لا توجد رحلات مجدولة اليوم',
        })),

      el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h3', {}, 'تنبيهات مفتوحة'),
          el('div', { class: 'spacer' }),
          el('button', { class: 'btn sm ghost', onClick: () => router.go('/alerts') },
            'عرض الكل')),
        alerts.length
          ? el('div', { class: 'stack-2' }, alerts.map((alert) =>
            el('button', {
              class: `warning-card sev-${alert.severity}`,
              onClick: () => router.go(alert.route_id ? `/routes/${alert.route_id}`
                : alert.shipment_id ? `/shipments/${alert.shipment_id}` : '/alerts'),
            },
              el('div', { class: 'wc-head' },
                el('span', { class: 'wc-title' },
                  ALERT_LABEL[alert.alert_type] || alert.alert_type),
                severityBadge(alert.severity)),
              el('div', { class: 'wc-line' }, alert.body_ar),
              el('div', { class: 'wc-line tiny muted' }, fmt.ago(alert.created_at)))))
          : el('div', { class: 'empty' }, el('span', { class: 'icon' }, '✓'),
            'لا توجد تنبيهات مفتوحة'))),

    // ---------------------------------------- ملخص التنبيهات للمراكز --
    hasRole('ADMIN', 'CENTRAL_PLANNER', 'CONTROL_TOWER', 'AUDITOR')
      && alertSummary?.data?.length
      ? el('div', { class: 'card mt-4' },
        el('div', { class: 'card-head' }, el('h3', {}, 'التنبيهات المفتوحة حسب مركز الانطلاق')),
        table([
          { label: 'مركز الانطلاق', key: 'hub_name_ar' },
          { label: 'حرجة', numeric: true, render: (r) => Number(r.critical) ?
            el('span', { class: 'badge critical' }, fmt.num(r.critical)) : '—' },
          { label: 'عالية', numeric: true, render: (r) => Number(r.high) ?
            el('span', { class: 'badge danger' }, fmt.num(r.high)) : '—' },
          { label: 'متوسطة', numeric: true, render: (r) => fmt.num(r.medium) },
          { label: 'الإجمالي', numeric: true, render: (r) => fmt.num(r.total) },
        ], alertSummary.data))
      : null,

    // ---------------------------------------------- روابط سريعة ------
    el('div', { class: 'card mt-4' },
      el('div', { class: 'card-head' }, el('h3', {}, 'إجراءات سريعة')),
      el('div', { class: 'btn-row' },
        can('schedule.upload') ? el('button', { class: 'btn primary',
          onClick: () => router.go('/imports') }, '⬆ رفع الجدول الأسبوعي') : null,
        can('plan.optimize') ? el('button', { class: 'btn',
          onClick: () => router.go('/plans/new') }, '⚙ إنشاء المسارات') : null,
        can('routes.assign') ? el('button', { class: 'btn',
          onClick: () => router.go('/assign') }, '⊞ الإسناد والنشر') : null,
        can('tracking.read') ? el('button', { class: 'btn',
          onClick: () => router.go('/live') }, '◎ الخريطة المباشرة') : null,
        can('reports.read') ? el('button', { class: 'btn',
          onClick: () => router.go('/reports') }, '📈 التقارير') : null)));
}
