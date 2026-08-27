/* التقارير ومؤشرات الأداء، تقدير السائقين، مراقبة تعديلات المراكز. */

import {
  api, el, state, can, hasRole, fmt, kpi, table, field, select, toast, toastError,
  mount, downloadUrl, LABELS,
} from '../core.js';

const GROUPINGS = [
  { value: 'hub', label: 'مركز الانطلاق' },
  { value: 'region', label: 'المنطقة' },
  { value: 'city', label: 'المدينة' },
  { value: 'driver', label: 'السائق' },
  { value: 'facility', label: 'جهة الالتقاط' },
  { value: 'facility_type', label: 'نوع الجهة' },
  { value: 'service_type', label: 'نوع الخدمة' },
  { value: 'status', label: 'حالة الشحنة' },
  { value: 'date', label: 'اليوم' },
];

export async function reportsView({ router }) {
  const host = el('div', {});
  const filters = {
    date_from: fmt.isoDate(new Date(Date.now() - 13 * 86400000)),
    date_to: fmt.isoDate(),
    hub_id: hasRole('HUB_SUPERVISOR') ? state.user.hub_ids?.[0] : '',
    region_id: '', facility_type: '', service_type: '', status: '',
    request_kind: '', include_test_data: 'true',
  };
  let grouping = 'hub';
  let activeTab = 'kpi';
  const bodyHost = el('div', {});

  function queryString() {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value) params.set(key, value);
    }
    return params;
  }

  async function load() {
    mount(bodyHost, el('div', { class: 'loading-block' }, el('span', { class: 'spinner' })));
    try {
      if (activeTab === 'kpi') await renderKpi();
      else if (activeTab === 'grouped') await renderGrouped();
      else if (activeTab === 'exceptions') await renderExceptions();
      else if (activeTab === 'temperature') await renderTemperature();
      else if (activeTab === 'plan') await renderPlanVsExecution();
    } catch (error) { toastError(error); }
  }

  async function renderKpi() {
    const [kpiRes, routesRes] = await Promise.all([
      api.get('/api/reports/kpi', { query: filters }),
      api.get('/api/reports/routes', { query: filters }),
    ]);
    const m = kpiRes.data;
    const totals = routesRes.data.totals || {};

    mount(bodyHost,
      el('h3', {}, 'مؤشرات الشحنات'),
      el('div', { class: 'kpi-grid mb-4' },
        kpi('إجمالي الشحنات', fmt.num(m.shipment_count)),
        kpi('مكتملة', fmt.num(m.completed_count), { tone: 'success' }),
        kpi('ملغاة', fmt.num(m.cancelled_count)),
        kpi('فاشلة', fmt.num(m.failed_count), { tone: m.failed_count ? 'danger' : '' }),
        kpi('استثنائية', fmt.num(m.exception_count),
          { tone: m.exception_count ? 'warning' : '' }),
        kpi('غير قابلة للتخطيط', fmt.num(m.unplannable_count),
          { tone: m.unplannable_count ? 'danger' : '' }),
        kpi('طلبات فورية', fmt.num(m.on_demand_count), { tone: 'info' }),
        kpi('عدد القطع', fmt.num(m.piece_count))),

      el('h3', {}, 'الالتزام والتأخير'),
      el('div', { class: 'kpi-grid mb-4' },
        kpi('الالتزام بـ SLA', m.sla_compliance_pct === null ? '—'
          : fmt.pct(m.sla_compliance_pct), {
          tone: m.sla_compliance_pct === null ? ''
            : m.sla_compliance_pct >= 95 ? 'success'
              : m.sla_compliance_pct >= 85 ? 'warning' : 'danger',
          hint: `${fmt.num(m.sla_breached_count)} تجاوز من ${fmt.num(m.delivered_count)} تسليم`,
        }),
        kpi('الالتزام بنافذة الالتقاط', m.pickup_window_compliance_pct === null ? '—'
          : fmt.pct(m.pickup_window_compliance_pct), {
          tone: m.pickup_window_compliance_pct === null ? ''
            : m.pickup_window_compliance_pct >= 95 ? 'success' : 'warning',
          hint: `${fmt.num(m.pickup_breached_count)} تجاوز من ${fmt.num(m.picked_up_count)} التقاط`,
        }),
        kpi('متوسط تأخر التسليم', m.avg_delivery_delay
          ? fmt.minutes(m.avg_delivery_delay) : '—', { tone: 'warning' }),
        kpi('متوسط تأخر الالتقاط', m.avg_pickup_delay
          ? fmt.minutes(m.avg_pickup_delay) : '—'),
        kpi('نسبة الإكمال', m.completion_rate_pct === null ? '—'
          : fmt.pct(m.completion_rate_pct)),
        kpi('التزامات تسليم مفتوحة', fmt.num(m.open_obligation_count),
          { tone: m.open_obligation_count ? 'danger' : 'success' })),

      el('h3', {}, 'مقاييس الرحلات'),
      el('div', { class: 'kpi-grid mb-4' },
        kpi('الرحلات', fmt.num(totals.route_count)),
        kpi('السائقون', fmt.num(totals.driver_count)),
        kpi('رحلات بعيدة', fmt.num(totals.long_haul_count)),
        kpi('رحلات بلا سائق', fmt.num(totals.unassigned_count),
          { tone: totals.unassigned_count ? 'danger' : '' }),
        kpi('إجمالي المسافة', fmt.km(totals.total_distance_km)),
        kpi('زمن القيادة', fmt.minutes(totals.total_drive_minutes)),
        kpi('زمن الخدمة', fmt.minutes(totals.total_service_minutes)),
        kpi('زمن الانتظار', fmt.minutes(totals.total_wait_minutes)),
        kpi('متوسط عمل الرحلة', fmt.minutes(totals.avg_working_minutes)),
        kpi('نقاط الالتقاط', fmt.num(totals.pickup_points)),
        kpi('نقاط التسليم', fmt.num(totals.delivery_points)),
        kpi('التكلفة التقديرية', fmt.money(totals.total_cost))),

      el('div', { class: 'alert-box' },
        el('strong', {}, 'مصدر بيانات موحّد'),
        'كل الأرقام أعلاه تأتي من نفس الاستعلام الأساسي المستخدم في لوحات المعلومات '
        + 'وفي شاشات التفاصيل، فلا يمكن أن يختلف عدّاد بين شاشة وأخرى.'));
  }

  async function renderGrouped() {
    const rows = (await api.get('/api/reports/grouped',
      { query: { ...filters, group_by: grouping } })).data;
    mount(bodyHost,
      el('div', { class: 'row mb-4' },
        field('التجميع حسب', select(GROUPINGS, {
          value: grouping, onChange: (value) => { grouping = value; load(); } })),
        el('button', {
          class: 'btn',
          onClick: () => {
            const params = queryString();
            params.set('group_by', grouping);
            params.set('format', 'csv');
            downloadUrl(`/api/reports/grouped?${params}`, 'تقرير-مسار.csv');
          },
        }, '⬇ تصدير CSV')),
      table([
        { label: 'المجموعة', render: (row) =>
          LABELS.status[row.group_label] || LABELS.facilityType[row.group_label]
          || LABELS.serviceType[row.group_label] || row.group_label || '—', wrap: true },
        { label: 'الشحنات', numeric: true, render: (r) => fmt.num(r.shipment_count) },
        { label: 'مكتملة', numeric: true, render: (r) => fmt.num(r.completed_count) },
        { label: 'تجاوز SLA', numeric: true, render: (r) => Number(r.sla_breached_count)
          ? el('span', { class: 'badge danger' }, fmt.num(r.sla_breached_count)) : '—' },
        { label: 'تجاوز النافذة', numeric: true, render: (r) =>
          fmt.num(r.pickup_breached_count) },
        { label: 'فاشلة', numeric: true, render: (r) => fmt.num(r.failed_count) },
        { label: 'ملغاة', numeric: true, render: (r) => fmt.num(r.cancelled_count) },
        { label: 'غير مخططة', numeric: true, render: (r) => fmt.num(r.unplannable_count) },
        { label: 'فورية', numeric: true, render: (r) => fmt.num(r.on_demand_count) },
        { label: 'الرحلات', numeric: true, render: (r) => fmt.num(r.route_count) },
        { label: 'متوسط التأخير', numeric: true, render: (r) => r.avg_delivery_delay
          ? fmt.minutes(r.avg_delivery_delay) : '—' },
        { label: 'الالتزام بـ SLA', numeric: true, render: (r) =>
          r.sla_compliance_pct === null ? '—'
            : el('span', { class: `badge ${r.sla_compliance_pct >= 95 ? 'success'
              : r.sla_compliance_pct >= 85 ? 'warning' : 'danger'}` },
              fmt.pct(r.sla_compliance_pct)) },
      ], rows, { empty: 'لا توجد بيانات في هذه الفترة' }));
  }

  async function renderExceptions() {
    const rows = (await api.get('/api/reports/exceptions', { query: filters })).data;
    mount(bodyHost, table([
      { label: 'السبب', render: (r) => LABELS.exceptionReason[r.reason] || r.reason },
      { label: 'الإجمالي', numeric: true, render: (r) => fmt.num(r.total) },
      { label: 'محسومة', numeric: true, render: (r) => fmt.num(r.resolved) },
      { label: 'مفتوحة', numeric: true, render: (r) =>
        fmt.num(Number(r.total) - Number(r.resolved)) },
      { label: 'تُبقي التزام التسليم', numeric: true, render: (r) =>
        fmt.num(r.keeps_obligation) },
      { label: 'متوسط زمن الحسم', numeric: true, render: (r) => r.avg_resolution_minutes
        ? fmt.minutes(r.avg_resolution_minutes) : '—' },
    ], rows, { empty: 'لا توجد حالات استثنائية في هذه الفترة' }));
  }

  async function renderTemperature() {
    const data = (await api.get('/api/reports/temperature', { query: filters })).data;
    const coverage = data.coverage || {};
    mount(bodyHost,
      el('div', { class: 'kpi-grid mb-4' },
        kpi('شحنات تحتاج تبريدًا', fmt.num(coverage.shipments_total)),
        kpi('لها قراءات', fmt.num(coverage.shipments_with_readings), {
          tone: Number(coverage.shipments_with_readings) ? 'success' : 'danger',
          hint: Number(coverage.shipments_with_readings) ? '' : 'لا يوجد تكامل حساسات مفعّل',
        }),
        kpi('قراءات حساسات حقيقية', fmt.num(coverage.sensor_readings), { tone: 'success' }),
        kpi('قراءات محاكاة', fmt.num(coverage.simulated_readings),
          { tone: Number(coverage.simulated_readings) ? 'warning' : '' })),
      Number(coverage.simulated_readings) ? el('div', { class: 'alert-box warning' },
        el('strong', {}, 'توجد قراءات محاكاة'),
        'القراءات الموسومة SIMULATION اختبارية ولا تُحتسب ضمن تقارير الامتثال.') : null,
      el('div', { class: 'card' },
        el('div', { class: 'card-head' }, el('h3', {}, 'مخالفات الحرارة')),
        table([
          { label: 'الشحنة', key: 'shipment_reference' },
          { label: 'المركز', key: 'hub_name_ar' },
          { label: 'النوع', render: (r) => el('span',
            { class: 'badge danger' }, r.breach_kind === 'HIGH' ? 'ارتفاع' : 'انخفاض') },
          { label: 'النطاق المطلوب', numeric: true, render: (r) =>
            `${r.required_min_c}° — ${r.required_max_c}°` },
          { label: 'المسجَّل', numeric: true, render: (r) =>
            `${r.min_celsius}° / ${r.max_celsius}°` },
          { label: 'البداية', render: (r) => fmt.dateTime(r.started_at) },
          { label: 'المدة', numeric: true, render: (r) => fmt.minutes(r.duration_minutes) },
          { label: 'الإجراء', key: 'action_taken', wrap: true },
          { label: 'محاكاة', render: (r) => r.is_test_data
            ? el('span', { class: 'badge warning' }, 'نعم') : '—' },
        ], data.breaches, { empty: 'لا توجد مخالفات حرارة مسجلة' })));
  }

  async function renderPlanVsExecution() {
    const data = (await api.get('/api/reports/plan-vs-execution', { query: filters })).data;
    mount(bodyHost,
      el('div', { class: 'kpi-grid mb-4' },
        kpi('شحنات مخططة', fmt.num(data.planned_shipments)),
        kpi('التقاطات منفَّذة', fmt.num(data.executed_pickups)),
        kpi('تسليمات منفَّذة', fmt.num(data.executed_deliveries)),
        kpi('الالتزام بالخطة', data.plan_adherence_pct === null ? '—'
          : fmt.pct(data.plan_adherence_pct), {
          tone: data.plan_adherence_pct >= 85 ? 'success' : 'warning',
          hint: 'نسبة الالتقاطات ضمن ±١٥ دقيقة من الوقت المخطط' }),
        kpi('انحراف الالتقاط', data.avg_pickup_deviation
          ? fmt.minutes(data.avg_pickup_deviation) : '—'),
        kpi('انحراف التسليم', data.avg_delivery_deviation
          ? fmt.minutes(data.avg_delivery_deviation) : '—'),
        kpi('تعديلات على رحلات', fmt.num(data.revisions?.revision_count),
          { hint: `${fmt.num(data.revisions?.routes_modified)} رحلة عُدّلت` })),
      el('div', { class: 'card' },
        el('div', { class: 'card-head' }, el('h3', {}, 'أسباب تعديل الرحلات')),
        table([
          { label: 'نوع التعديل', key: 'change_kind' },
          { label: 'العدد', numeric: true, render: (r) => fmt.num(r.total) },
          { label: 'أمثلة على الأسباب', render: (r) =>
            (r.sample_reasons || []).slice(0, 3).join(' · '), wrap: true },
        ], data.revision_reasons, { empty: 'لم تُعدَّل أي رحلة منشورة' })));
  }

  const tabs = [
    ['kpi', 'المؤشرات الرئيسية'],
    ['grouped', 'تقرير مُجمَّع'],
    ['exceptions', 'الحالات الاستثنائية'],
    ['temperature', 'الحرارة وسلسلة الحيازة'],
    ['plan', 'الخطة مقابل التنفيذ'],
  ];

  const tabsHost = el('div', { class: 'tabs' }, tabs.map(([key, label]) =>
    el('button', {
      class: `tab ${activeTab === key ? 'active' : ''}`,
      dataset: { tab: key },
      onClick: () => {
        activeTab = key;
        for (const node of tabsHost.querySelectorAll('.tab')) {
          node.classList.toggle('active', node.dataset.tab === key);
        }
        load();
      },
    }, label)));

  mount(host,
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' }, el('h1', {}, 'التقارير ومؤشرات الأداء'))),
    el('div', { class: 'card mb-4' },
      el('div', { class: 'filters' },
        field('من', el('input', { type: 'date', value: filters.date_from,
          onChange: (e) => { filters.date_from = e.target.value; load(); } })),
        field('إلى', el('input', { type: 'date', value: filters.date_to,
          onChange: (e) => { filters.date_to = e.target.value; load(); } })),
        field('مركز الانطلاق', select(
          [{ value: '', label: 'كل المراكز' },
            ...state.hubs.map((hub) => ({ value: hub.id, label: hub.name_ar }))],
          { value: filters.hub_id, onChange: (v) => { filters.hub_id = v; load(); } })),
        field('نوع الجهة', select([
          { value: '', label: 'الكل' },
          ...Object.entries(LABELS.facilityType).map(([value, label]) => ({ value, label })),
        ], { value: filters.facility_type,
          onChange: (v) => { filters.facility_type = v; load(); } })),
        field('نوع الخدمة', select([
          { value: '', label: 'الكل' },
          ...Object.entries(LABELS.serviceType).map(([value, label]) => ({ value, label })),
        ], { value: filters.service_type,
          onChange: (v) => { filters.service_type = v; load(); } })),
        field('نوع الطلب', select([
          { value: '', label: 'الكل' }, { value: 'SCHEDULED', label: 'مجدول' },
          { value: 'ON_DEMAND', label: 'فوري' },
        ], { value: filters.request_kind,
          onChange: (v) => { filters.request_kind = v; load(); } })),
        el('button', { class: 'btn', onClick: load }, '↻ تحديث'))),
    tabsHost,
    bodyHost);

  load();
  return host;
}

/* ================================================ تقدير السائقين ======= */

export async function driverEstimationView({ router }) {
  const host = el('div', {});
  const filters = {
    date_from: fmt.isoDate(),
    date_to: fmt.isoDate(new Date(Date.now() + 7 * 86400000)),
  };
  const bodyHost = el('div', {});

  async function load() {
    mount(bodyHost, el('div', { class: 'loading-block' }, el('span', { class: 'spinner' })));
    try {
      const rows = (await api.get('/api/reports/driver-capacity', { query: filters })).data;
      const unjustified = rows.filter((row) => row.unjustified_excess > 0);
      const shortages = rows.filter((row) => Number(row.gap) < 0);

      mount(bodyHost,
        el('div', { class: 'kpi-grid mb-4' },
          kpi('سجلات التقدير', fmt.num(rows.length)),
          kpi('مراكز بعجز سائقين', fmt.num(shortages.length),
            { tone: shortages.length ? 'danger' : 'success' }),
          kpi('حالات زيادة غير مبررة', fmt.num(unjustified.length),
            { tone: unjustified.length ? 'warning' : 'success' })),

        unjustified.length ? el('div', { class: 'alert-box warning' },
          el('strong', {}, 'زيادة غير مبررة في عدد السائقين'),
          `${unjustified.length} حالة استُخدم فيها سائقون أكثر من الحد الأدنى النظري `
          + 'دون سبب مسجّل (قيد خلط أو تعارض نوافذ أو رحلة بعيدة أو تباعد جغرافي).')
          : null,

        el('div', { class: 'card' },
          el('div', { class: 'card-head' }, el('h3', {}, 'التقدير حسب المركز واليوم')),
          table([
            { label: 'المركز', key: 'hub_name_ar' },
            { label: 'اليوم', render: (r) => fmt.date(r.service_date) },
            { label: 'الحد الأدنى النظري', numeric: true, render: (r) =>
              fmt.num(r.theoretical_minimum) },
            { label: 'المستخدم', numeric: true, render: (r) => fmt.num(r.used) },
            { label: 'الموصى به', numeric: true, render: (r) => fmt.num(r.recommended) },
            { label: 'المتوفر', numeric: true, render: (r) => fmt.num(r.available) },
            { label: 'العجز/الفائض', numeric: true, render: (r) => el('span',
              { class: `badge ${Number(r.gap) < 0 ? 'danger' : 'success'}` },
              Number(r.gap) < 0 ? `عجز ${fmt.num(Math.abs(r.gap))}`
                : `فائض ${fmt.num(r.gap)}`) },
            { label: 'الزيادة', numeric: true, render: (r) => fmt.num(r.excess_drivers) },
            { label: 'مبررة', numeric: true, render: (r) => fmt.num(r.justified_excess) },
            { label: 'غير مبررة', numeric: true, render: (r) => r.unjustified_excess
              ? el('span', { class: 'badge warning' }, fmt.num(r.unjustified_excess)) : '—' },
            { label: 'الحكم', render: (r) => el('span',
              { class: `badge ${r.flag === 'مبرر' ? 'success' : 'warning'}` }, r.flag) },
            { label: 'حجم العمل', numeric: true, render: (r) =>
              fmt.minutes(r.workload_minutes) },
            { label: 'التبرير', wrap: true, render: (r) => el('div', { class: 'tiny' },
              (r.justification || []).map((item) =>
                el('div', {}, `+${item.drivers} ${item.label_ar}`))) },
          ], rows, { empty: 'لا توجد تقديرات في هذه الفترة — شغّل المحرك أولًا' })));
    } catch (error) { toastError(error); }
  }

  mount(host,
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, 'تقدير السائقين'),
        el('p', { class: 'subtitle' },
          'الحد الأدنى النظري مبرهن رياضيًا (حجم العمل، قيد عدم الخلط، تعارض النوافذ) '
          + 'ولا يمكن لأي خطة أن تنزل تحته. كل سائق إضافي له سبب مسجّل.'))),
    el('div', { class: 'card mb-4' },
      el('div', { class: 'filters' },
        field('من', el('input', { type: 'date', value: filters.date_from,
          onChange: (e) => { filters.date_from = e.target.value; load(); } })),
        field('إلى', el('input', { type: 'date', value: filters.date_to,
          onChange: (e) => { filters.date_to = e.target.value; load(); } })),
        el('button', { class: 'btn', onClick: load }, '↻ تحديث'))),
    bodyHost);

  load();
  return host;
}

/* ======================================== مراقبة تعديلات المراكز ======= */

export async function hubMonitorView({ router }) {
  const rows = (await api.get('/api/reports/hub-modifications')).data;
  return el('div', {},
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, 'مراقبة تعديلات مراكز الانطلاق'),
        el('p', { class: 'subtitle' },
          'كل تعديل على رحلة منشورة يُسجَّل بمراجعة مرقّمة تحمل السبب والفرق '
          + 'بين ما قبل التعديل وما بعده.'))),
    el('div', { class: 'card' },
      table([
        { label: 'المركز', key: 'hub_name_ar' },
        { label: 'عدد التعديلات', numeric: true, render: (r) => fmt.num(r.revision_count) },
        { label: 'رحلات عُدّلت', numeric: true, render: (r) => fmt.num(r.routes_modified) },
        { label: 'تغيير سائق', numeric: true, render: (r) => fmt.num(r.driver_reassignments) },
        { label: 'إضافة محطة', numeric: true, render: (r) => fmt.num(r.stops_added) },
        { label: 'إزالة محطة', numeric: true, render: (r) => fmt.num(r.stops_removed) },
        { label: 'آخر تعديل', render: (r) => fmt.dateTime(r.last_change_at) },
        { label: 'الأسباب المسجّلة', wrap: true, render: (r) =>
          (r.reasons || []).slice(0, 4).join(' · ') || '—' },
      ], rows, { empty: 'لم تُعدَّل أي رحلة منشورة بعد' })));
}
