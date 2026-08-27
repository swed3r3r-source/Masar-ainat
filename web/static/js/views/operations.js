/* شاشات التشغيل: الرحلات، الإسناد والنشر، الخريطة المباشرة، التنبيهات،
   الاستثناءات، الشحنات، الطلبات الفورية، أداء السائقين. */

import {
  api, el, state, can, hasRole, fmt, kpi, table, field, select, toast, toastError,
  modal, confirmDialog, statusBadge, severityBadge, mount, clear, events, LABELS,
  ROUTE_STATUS_LABEL, ALERT_LABEL, WARNING_LABEL,
} from '../core.js';
import { MasarMap, MAP_COLORS, openNavigation } from '../map.js';

function hubOptions(includeAll = true) {
  const options = state.hubs.map((hub) => ({ value: hub.id, label: hub.name_ar }));
  return includeAll ? [{ value: '', label: 'كل المراكز' }, ...options] : options;
}

/* ===================================================== الرحلات ========= */

export async function routesView({ query, router }) {
  const host = el('div', {});
  const filters = {
    hub_id: query.hub_id || (hasRole('HUB_SUPERVISOR') ? state.user.hub_ids?.[0] : ''),
    service_date: query.service_date || fmt.isoDate(),
    status: query.status || '',
  };
  const tableHost = el('div', {});

  async function load() {
    mount(tableHost, el('div', { class: 'loading-block' }, el('span', { class: 'spinner' })));
    try {
      const response = await api.get('/api/routes', { query: filters });
      mount(tableHost, table([
        { label: 'المرجع', key: 'reference' },
        { label: 'اليوم', render: (r) => fmt.date(r.service_date) },
        { label: 'المركز', key: 'hub_name_ar' },
        { label: 'السائق', render: (r) => r.driver_name
          || el('span', { class: 'badge warning' }, 'بلا سائق') },
        { label: 'المركبة', key: 'plate_number' },
        { label: 'الحالة', render: (r) => statusBadge(r.status, ROUTE_STATUS_LABEL) },
        { label: 'البداية', render: (r) => fmt.time(r.planned_start_at) },
        { label: 'النهاية', render: (r) => fmt.time(r.planned_end_at) },
        { label: 'مدة العمل', numeric: true, render: (r) => fmt.minutes(r.working_minutes) },
        { label: 'المسافة', numeric: true, render: (r) => fmt.km(r.distance_km) },
        { label: 'الشحنات', numeric: true, render: (r) => fmt.num(r.shipment_count) },
        { label: 'بعيدة', render: (r) => r.is_long_haul
          ? el('span', { class: 'badge warning' }, 'نعم') : '—' },
      ], response.data, {
        onRowClick: (row) => router.go(`/routes/${row.id}`),
        empty: 'لا توجد رحلات بهذه المرشحات',
      }));
    } catch (error) { toastError(error); }
  }

  mount(host,
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' }, el('h1', {}, 'الرحلات'))),
    el('div', { class: 'card' },
      el('div', { class: 'filters' },
        field('مركز الانطلاق', select(hubOptions(), {
          value: filters.hub_id,
          onChange: (value) => { filters.hub_id = value; load(); },
        })),
        field('اليوم', el('input', {
          type: 'date', value: filters.service_date,
          onChange: (event) => { filters.service_date = event.target.value; load(); },
        })),
        field('الحالة', select([
          { value: '', label: 'كل الحالات' },
          ...Object.entries(ROUTE_STATUS_LABEL).map(([value, label]) => ({ value, label })),
        ], { value: filters.status, onChange: (value) => { filters.status = value; load(); } })),
        el('button', { class: 'btn', onClick: load }, '↻ تحديث')),
      tableHost));

  load();
  return host;
}

/* ================================================ الإسناد والنشر ======= */

export async function assignView({ router }) {
  const host = el('div', {});
  const hubId = state.user.hub_ids?.[0] || state.hubs[0]?.id || '';
  const filters = { hub_id: hubId, service_date: fmt.isoDate() };
  const bodyHost = el('div', {});

  async function load() {
    mount(bodyHost, el('div', { class: 'loading-block' }, el('span', { class: 'spinner' })));
    try {
      const response = await api.get('/api/routes', { query: filters });
      const routes = response.data || [];
      const unassigned = routes.filter((r) => !r.driver_id
        && !['COMPLETED', 'CANCELLED'].includes(r.status));
      const assigned = routes.filter((r) => r.status === 'ASSIGNED');
      const published = routes.filter((r) => r.status === 'PUBLISHED');
      const running = routes.filter((r) => r.status === 'IN_PROGRESS');

      mount(bodyHost,
        el('div', { class: 'kpi-grid mb-4' },
          kpi('إجمالي الرحلات', fmt.num(routes.length)),
          kpi('بانتظار الإسناد', fmt.num(unassigned.length),
            { tone: unassigned.length ? 'danger' : 'success' }),
          kpi('مُسندة (غير منشورة)', fmt.num(assigned.length), { tone: 'warning' }),
          kpi('منشورة', fmt.num(published.length), { tone: 'success' }),
          kpi('قيد التنفيذ', fmt.num(running.length), { tone: 'info' })),

        el('div', { class: 'card mb-4' },
          el('div', { class: 'card-head' },
            el('h3', {}, 'نشر خطة اليوم'),
            el('div', { class: 'spacer' }),
            unassigned.length
              ? el('span', { class: 'badge danger' },
                `${unassigned.length} رحلة بلا سائق — النشر ممنوع`)
              : el('span', { class: 'badge success' }, 'كل الرحلات مُسندة')),
          el('p', { class: 'muted small' },
            'النشر لكل يوم ومركز على حدة. لا تظهر الرحلة في تطبيق السائق قبل نشر يومها، '
            + 'ولا يُسمح بالنشر مع وجود رحلة تخرق قيدًا صلبًا.'),
          el('div', { class: 'btn-row' },
            can('routes.publish') ? el('button', {
              class: 'btn primary',
              disabled: unassigned.length > 0 || !assigned.length,
              onClick: async () => {
                try {
                  const result = await api.post('/api/publish', {
                    hub_id: filters.hub_id, service_date: filters.service_date,
                  });
                  toast(`نُشرت ${result.data.published_routes} رحلة`, { tone: 'success' });
                  load();
                } catch (error) { toastError(error); }
              },
            }, `➤ نشر ${filters.service_date}`) : null,
            can('routes.publish') && published.length ? el('button', {
              class: 'btn danger',
              onClick: async () => {
                const reason = await confirmDialog({
                  title: 'سحب النشر',
                  message: 'ستختفي الرحلات من تطبيق السائق. لا يمكن السحب بعد بدء التنفيذ.',
                  confirmLabel: 'سحب النشر', tone: 'danger', requireReason: true,
                });
                if (!reason) return;
                try {
                  await api.post('/api/unpublish', {
                    hub_id: filters.hub_id, service_date: filters.service_date, reason,
                  });
                  toast('سُحب النشر', { tone: 'warning' });
                  load();
                } catch (error) { toastError(error); }
              },
            }, 'سحب النشر') : null)),

        el('div', { class: 'card' },
          el('div', { class: 'card-head' }, el('h3', {}, 'الرحلات')),
          table([
            { label: 'الرحلة', key: 'reference' },
            { label: 'الحالة', render: (r) => statusBadge(r.status, ROUTE_STATUS_LABEL) },
            { label: 'البداية', render: (r) => fmt.time(r.planned_start_at) },
            { label: 'النهاية', render: (r) => fmt.time(r.planned_end_at) },
            { label: 'مدة العمل', numeric: true, render: (r) => fmt.minutes(r.working_minutes) },
            { label: 'الشحنات', numeric: true, render: (r) =>
              `${r.shipment_count} (${r.pickup_count}↑/${r.delivery_count}↓)` },
            { label: 'بعيدة', render: (r) => r.is_long_haul
              ? el('span', { class: 'badge warning' }, 'نعم') : '—' },
            { label: 'السائق', render: (r) => r.driver_name
              || el('span', { class: 'badge danger' }, 'بلا سائق') },
            { label: 'إجراءات', render: (r) => el('div', { class: 'btn-row' },
              el('button', {
                class: 'btn sm',
                onClick: (event) => { event.stopPropagation(); router.go(`/routes/${r.id}`); },
              }, 'تفاصيل'),
              can('routes.assign') && !['COMPLETED', 'CANCELLED'].includes(r.status)
                ? el('button', {
                  class: 'btn sm primary',
                  onClick: (event) => { event.stopPropagation(); openAssignDialog(r, load); },
                }, r.driver_id ? 'تغيير السائق' : 'إسناد') : null,
              can('routes.unassign') && r.driver_id && r.status !== 'IN_PROGRESS'
                ? el('button', {
                  class: 'btn sm ghost',
                  onClick: async (event) => {
                    event.stopPropagation();
                    const reason = await confirmDialog({
                      title: 'إزالة السائق',
                      message: `ستعود الرحلة ${r.reference} وشحناتها إلى قائمة الانتظار.`,
                      confirmLabel: 'إزالة', tone: 'danger', requireReason: true,
                    });
                    if (!reason) return;
                    try {
                      await api.post(`/api/routes/${r.id}/unassign`, { reason });
                      toast('أُزيل السائق وعادت الرحلة لقائمة الانتظار', { tone: 'warning' });
                      load();
                    } catch (error) { toastError(error); }
                  },
                }, 'إزالة') : null) },
          ], routes, { empty: 'لا توجد رحلات لهذا اليوم' })));
    } catch (error) { toastError(error); }
  }

  mount(host,
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, 'الإسناد والنشر'),
        el('p', { class: 'subtitle' },
          'النظام يبني الرحلات ويقترح السائقين المؤهلين مع سبب استبعاد كل مرشح، '
          + 'والقرار النهائي للمشرف.'))),
    el('div', { class: 'card mb-4' },
      el('div', { class: 'filters' },
        field('مركز الانطلاق', select(hubOptions(false), {
          value: filters.hub_id,
          onChange: (value) => { filters.hub_id = value; load(); },
        })),
        field('اليوم', el('input', {
          type: 'date', value: filters.service_date,
          onChange: (event) => { filters.service_date = event.target.value; load(); },
        })),
        el('button', { class: 'btn', onClick: load }, '↻ تحديث'))),
    bodyHost);

  load();
  return host;
}

async function openAssignDialog(route, onDone) {
  let payload;
  try {
    payload = (await api.get(`/api/routes/${route.id}/candidates`)).data;
  } catch (error) { toastError(error); return; }

  const [vehicles, boxes] = await Promise.all([
    api.get('/api/md/vehicles', { query: { hub_id: route.hub_id } })
      .then((r) => r.data).catch(() => []),
    api.get('/api/md/boxes', { query: { hub_id: route.hub_id } })
      .then((r) => r.data).catch(() => []),
  ]);

  let chosen = payload.candidates.find((c) => c.eligible)?.driver_id || null;
  const vehicleSelect = select(
    [{ value: '', label: 'بلا مركبة' },
      ...vehicles.map((v) => ({ value: v.id, label: v.plate_number }))], {});
  const boxSelect = select(
    [{ value: '', label: 'بلا صندوق' },
      ...boxes.map((b) => ({ value: b.id,
        label: `${b.code} — ${LABELS.temperature[b.temperature_mode] || ''}` }))], {});
  const reasonInput = el('textarea', { rows: 2,
    placeholder: 'سبب التعديل (إلزامي للرحلات المنشورة)' });

  const listHost = el('div', { class: 'stack-2' });
  const renderList = () => {
    mount(listHost, payload.candidates.map((candidate) => el('button', {
      class: `warning-card ${candidate.eligible ? 'sev-INFO' : 'sev-HIGH'}`,
      style: chosen === candidate.driver_id
        ? { borderColor: 'var(--brand)', background: 'var(--brand-soft)' } : {},
      disabled: !candidate.eligible,
      onClick: () => { if (candidate.eligible) { chosen = candidate.driver_id; renderList(); } },
    },
      el('div', { class: 'wc-head' },
        el('span', { class: 'wc-title' }, candidate.full_name),
        el('span', { class: 'badge' }, candidate.code),
        candidate.eligible
          ? el('span', { class: 'badge success' }, 'مؤهل')
          : el('span', { class: 'badge danger' }, 'غير مؤهل'),
        chosen === candidate.driver_id
          ? el('span', { class: 'badge brand' }, 'مختار') : null),
      el('div', { class: 'wc-line tiny' },
        `اليوم: ${candidate.assigned_routes_today} رحلة `
        + `· ${fmt.minutes(candidate.assigned_minutes_today)} عمل `
        + `· ${candidate.assigned_long_haul_today} رحلة بعيدة `
        + `| الأسبوع: ${candidate.week_routes} رحلة · `
        + `${fmt.minutes(candidate.week_minutes)}`),
      candidate.blockers.length
        ? el('div', { class: 'wc-line' }, el('b', {}, 'موانع: '),
          candidate.blockers.join('؛ ')) : null,
      candidate.notes.length
        ? el('div', { class: 'wc-line tiny muted' }, candidate.notes.join('؛ ')) : null)));
  };
  renderList();

  modal({
    title: `إسناد الرحلة ${route.reference}`,
    wide: true,
    body: el('div', {},
      el('p', { class: 'muted small' },
        'المرشحون مرتبون بالأقل عملًا أسبوعيًا أولًا (عدالة التوزيع). '
        + 'كل مرشح غير مؤهل يظهر معه سبب الاستبعاد صراحةً.'),
      listHost,
      el('div', { class: 'grid cols-2 mt-4' },
        field('المركبة', vehicleSelect),
        field('الصندوق', boxSelect)),
      route.status === 'PUBLISHED'
        ? field('سبب التعديل', reasonInput, { required: true,
          help: 'الرحلة منشورة — التعديل يُسجَّل في سجل التدقيق ويُشعر السائق فورًا' })
        : null),
    actions: (close) => [
      el('button', {
        class: 'btn primary',
        onClick: async () => {
          if (!chosen) { toast('اختر سائقًا', { tone: 'error' }); return; }
          try {
            await api.post(`/api/routes/${route.id}/assign`, {
              driver_id: chosen,
              vehicle_id: vehicleSelect.value || null,
              box_id: boxSelect.value || null,
              reason: reasonInput.value.trim() || null,
            });
            toast('تم الإسناد', { tone: 'success' });
            close();
            onDone?.();
          } catch (error) { toastError(error); }
        },
      }, 'إسناد'),
      el('button', { class: 'btn ghost', onClick: close }, 'إلغاء'),
    ],
  });
}

/* ================================================ الخريطة المباشرة ===== */

export async function liveMapView({ router }) {
  const host = el('div', {});
  const mapHost = el('div', {});
  const listHost = el('div', { class: 'stack-2' });
  const hubFilter = hasRole('HUB_SUPERVISOR') ? state.user.hub_ids?.[0] : '';
  let mapInstance = null;
  let timer = null;

  async function refresh() {
    try {
      const response = await api.get('/api/tracking/live',
        { query: { hub_id: hubFilter } });
      const drivers = response.data || [];

      mapInstance.setMarkers(drivers.map((driver) => ({
        id: driver.driver_id, lat: driver.lat, lon: driver.lon,
        kind: 'DRIVER', stale: driver.is_stale,
        title: driver.full_name, label: '',
      })));
      if (!mapInstance.fitted && drivers.length) {
        mapInstance.fit(drivers);
        mapInstance.fitted = true;
      }

      mount(listHost, drivers.length ? drivers.map((driver) => el('button', {
        class: `warning-card ${driver.is_stale ? 'sev-HIGH' : 'sev-INFO'}`,
        onClick: () => {
          mapInstance.center = { lat: Number(driver.lat), lon: Number(driver.lon) };
          mapInstance.zoom = 13;
          mapInstance.select(driver.driver_id);
          mapInstance.draw();
        },
      },
        el('div', { class: 'wc-head' },
          el('span', { class: 'wc-title' }, driver.full_name),
          el('span', { class: 'badge' }, driver.code),
          driver.is_stale
            ? el('span', { class: 'badge danger' }, 'توقف التحديث')
            : el('span', { class: 'badge success' }, 'متصل')),
        el('div', { class: 'wc-line tiny' },
          driver.route_reference
            ? `${driver.route_reference} · ${ROUTE_STATUS_LABEL[driver.route_status] || ''} `
              + `· ${driver.completed_stops || 0}/${driver.total_stops || 0} محطة`
            : 'بلا رحلة نشطة'),
        el('div', { class: 'wc-line tiny muted' },
          `آخر تحديث ${fmt.ago(driver.recorded_at)} `
          + `(${fmt.time(driver.recorded_at)}) · ${driver.hub_name_ar}`),
        driver.route_id ? el('div', { class: 'btn-row', style: { marginTop: '6px' } },
          el('span', {
            class: 'btn sm',
            onClick: (event) => { event.stopPropagation(); router.go(`/routes/${driver.route_id}`); },
          }, 'فتح الرحلة')) : null))
        : el('div', { class: 'empty' }, 'لا توجد مواقع سائقين مسجلة'));
    } catch (error) { toastError(error); }
  }

  mount(host,
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, 'الخريطة المباشرة'),
        el('p', { class: 'subtitle' },
          `يُعرض آخر موقع مسجَّل لكل سائق مع وقت التحديث. `
          + `تُوسم المواقع الأقدم من ${state.meta.config.tracking_stale_seconds} ثانية بأنها متوقفة.`)),
      el('div', { class: 'page-actions' },
        el('button', { class: 'btn', onClick: refresh }, '↻ تحديث'))),
    el('div', { class: 'grid split' },
      el('div', { class: 'card' }, mapHost),
      el('div', { class: 'card' },
        el('div', { class: 'card-head' }, el('h3', {}, 'السائقون')),
        listHost)));

  mapInstance = new MasarMap(mapHost, {
    tileUrl: state.meta.config.tile_url,
    attribution: state.meta.config.tile_attribution,
    height: 560,
  });
  mapInstance.setLegend([
    { color: MAP_COLORS.driver, label: 'سائق نشط' },
    { color: MAP_COLORS.stale, label: 'توقف تحديث الموقع' },
  ]);
  mapInstance.center = { lat: 24.7136, lon: 46.6753 };
  mapInstance.zoom = 6;

  refresh();
  timer = setInterval(refresh, 15000);
  const off = events.on('position', refresh);
  const observer = new MutationObserver(() => {
    if (!document.body.contains(host)) {
      clearInterval(timer); off(); observer.disconnect(); mapInstance.destroy();
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });

  return host;
}

/* ==================================================== التنبيهات ======== */

export async function alertsView({ router }) {
  const host = el('div', {});
  const filters = { severity: '', type: '', only_open: 'true',
    hub_id: hasRole('HUB_SUPERVISOR') ? state.user.hub_ids?.[0] : '' };
  const listHost = el('div', {});

  async function load() {
    mount(listHost, el('div', { class: 'loading-block' }, el('span', { class: 'spinner' })));
    try {
      const response = await api.get('/api/alerts', { query: { ...filters, limit: 200 } });
      const alerts = response.data || [];
      mount(listHost, alerts.length
        ? el('div', { class: 'stack-2' }, alerts.map((alert) => el('div', {
          class: `warning-card sev-${alert.severity}`,
        },
          el('div', { class: 'wc-head' },
            el('span', { class: 'wc-title' },
              ALERT_LABEL[alert.alert_type] || alert.alert_type),
            severityBadge(alert.severity),
            alert.route_reference
              ? el('span', { class: 'badge brand' }, alert.route_reference) : null,
            alert.shipment_reference
              ? el('span', { class: 'badge info' }, alert.shipment_reference) : null,
            alert.resolved_at ? el('span', { class: 'badge success' }, 'مُغلق') : null),
          el('div', { class: 'wc-line' }, alert.body_ar),
          el('div', { class: 'wc-line tiny muted' },
            `${fmt.dateTime(alert.created_at)} · ${fmt.ago(alert.created_at)}`,
            alert.hub_name_ar ? ` · ${alert.hub_name_ar}` : '',
            alert.driver_name ? ` · ${alert.driver_name}` : ''),
          alert.action_note
            ? el('div', { class: 'wc-line' }, el('b', {}, 'الإجراء المتخذ: '), alert.action_note)
            : null,
          el('div', { class: 'btn-row', style: { marginTop: '8px' } },
            alert.route_id ? el('button', {
              class: 'btn sm', onClick: () => router.go(`/routes/${alert.route_id}`),
            }, 'فتح الرحلة') : null,
            alert.shipment_id ? el('button', {
              class: 'btn sm', onClick: () => router.go(`/shipments/${alert.shipment_id}`),
            }, 'فتح الشحنة') : null,
            can('alerts.act') && !alert.resolved_at ? el('button', {
              class: 'btn sm primary',
              onClick: async () => {
                const note = await confirmDialog({
                  title: 'إغلاق التنبيه',
                  message: 'لا يُغلق التنبيه دون تسجيل الإجراء المتخذ.',
                  confirmLabel: 'إغلاق', requireReason: true,
                  reasonLabel: 'الإجراء المتخذ',
                });
                if (!note) return;
                try {
                  await api.post(`/api/alerts/${alert.id}/resolve`, { action_note: note });
                  toast('أُغلق التنبيه', { tone: 'success' });
                  load();
                } catch (error) { toastError(error); }
              },
            }, '✓ تسجيل إجراء وإغلاق') : null))))
        : el('div', { class: 'empty' }, el('span', { class: 'icon' }, '✓'),
          'لا توجد تنبيهات بهذه المرشحات'));
    } catch (error) { toastError(error); }
  }

  mount(host,
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, 'التنبيهات'),
        el('p', { class: 'subtitle' },
          'كل تنبيه مرتبط بشحنة أو رحلة أو مركز، ولا يُغلق إلا بإجراء مسجَّل.')),
      el('div', { class: 'page-actions' },
        el('button', {
          class: 'btn',
          onClick: async () => {
            try {
              await api.post('/api/alerts/scan', {});
              toast('اكتمل فحص التنبيهات', { tone: 'success' });
              load();
            } catch (error) { toastError(error); }
          },
        }, '↻ فحص الآن'))),
    el('div', { class: 'card mb-4' },
      el('div', { class: 'filters' },
        field('مركز الانطلاق', select(hubOptions(), {
          value: filters.hub_id, onChange: (v) => { filters.hub_id = v; load(); } })),
        field('الخطورة', select([
          { value: '', label: 'الكل' },
          { value: 'CRITICAL', label: 'حرجة' }, { value: 'HIGH', label: 'عالية' },
          { value: 'MEDIUM', label: 'متوسطة' }, { value: 'LOW', label: 'منخفضة' },
        ], { value: filters.severity, onChange: (v) => { filters.severity = v; load(); } })),
        field('النوع', select([
          { value: '', label: 'الكل' },
          ...Object.entries(ALERT_LABEL).map(([value, label]) => ({ value, label })),
        ], { value: filters.type, onChange: (v) => { filters.type = v; load(); } })),
        field('الحالة', select([
          { value: 'true', label: 'المفتوحة فقط' }, { value: 'false', label: 'الكل' },
        ], { value: filters.only_open, onChange: (v) => { filters.only_open = v; load(); } })))),
    listHost);

  load();
  const off = events.on('alert', load);
  return host;
}

/* =============================================== الحالات الاستثنائية === */

export async function exceptionsView({ router }) {
  const host = el('div', {});
  const filters = { status: '', hub_id: hasRole('HUB_SUPERVISOR')
    ? state.user.hub_ids?.[0] : '' };
  const listHost = el('div', {});

  async function load() {
    mount(listHost, el('div', { class: 'loading-block' }, el('span', { class: 'spinner' })));
    try {
      const response = await api.get('/api/exceptions', { query: filters });
      const rows = response.data || [];
      mount(listHost, table([
        { label: 'الشحنة', key: 'shipment_reference' },
        { label: 'السبب', render: (r) =>
          el('span', { class: 'badge warning' },
            LABELS.exceptionReason[r.reason] || r.reason) },
        { label: 'الملاحظة', key: 'note', wrap: true },
        { label: 'الرحلة', key: 'route_reference' },
        { label: 'المركز', key: 'hub_name_ar' },
        { label: 'المبلِّغ', key: 'driver_name' },
        { label: 'الوقت', render: (r) => fmt.dateTime(r.occurred_at) },
        { label: 'الالتزام', render: (r) => r.keeps_obligation
          ? el('span', { class: 'badge danger' }, 'تسليم مفتوح') : '—' },
        { label: 'الحالة', render: (r) => r.status === 'RESOLVED'
          ? el('span', { class: 'badge success' }, 'محسومة')
          : el('span', { class: 'badge danger' }, 'مفتوحة') },
        { label: 'الإجراء', key: 'action_taken', wrap: true },
        { label: '', render: (r) => can('exceptions.resolve') && r.status !== 'RESOLVED'
          ? el('button', {
            class: 'btn sm primary',
            onClick: (event) => { event.stopPropagation(); openResolveDialog(r, load); },
          }, 'حسم') : null },
      ], rows, {
        onRowClick: (row) => router.go(`/shipments/${row.shipment_id}`),
        empty: 'لا توجد حالات استثنائية',
      }));
    } catch (error) { toastError(error); }
  }

  mount(host,
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, 'الحالات الاستثنائية'),
        el('p', { class: 'subtitle' },
          'لا تُحذف الشحنة ولا تاريخها. عند تعذر التسليم يبقى التزام التسليم '
          + 'مفتوحًا حتى يقرر المشرف الإجراء.'))),
    el('div', { class: 'card mb-4' },
      el('div', { class: 'filters' },
        field('مركز الانطلاق', select(hubOptions(), {
          value: filters.hub_id, onChange: (v) => { filters.hub_id = v; load(); } })),
        field('الحالة', select([
          { value: '', label: 'الكل' }, { value: 'OPEN', label: 'مفتوحة' },
          { value: 'RESOLVED', label: 'محسومة' },
        ], { value: filters.status, onChange: (v) => { filters.status = v; load(); } })),
        el('button', { class: 'btn', onClick: load }, '↻ تحديث'))),
    listHost);

  load();
  return host;
}

function openResolveDialog(exceptionRow, onDone) {
  const statusSelect = select([
    { value: 'IN_PROGRESS', label: 'استئناف التنفيذ' },
    { value: 'PENDING_ASSIGNMENT', label: 'إعادة الجدولة (تعود لقائمة الانتظار)' },
    { value: 'ARRIVED_PICKUP', label: 'استئناف عند محطة الالتقاط' },
    { value: 'PICKED_UP', label: 'اعتماد الالتقاط والمتابعة' },
    { value: 'ARRIVED_DELIVERY', label: 'استئناف عند محطة التسليم' },
    { value: 'COMPLETED', label: 'إغلاق كمكتملة' },
    { value: 'CANCELLED_BEFORE_PICKUP', label: 'إلغاء قبل الالتقاط' },
    { value: 'FAILED', label: 'إغلاق كفاشلة' },
  ], {});
  const actionInput = el('textarea', { rows: 3,
    placeholder: 'صف الإجراء المتخذ — يُحفظ في سجل التدقيق' });

  modal({
    title: `حسم حالة: ${LABELS.exceptionReason[exceptionRow.reason] || exceptionRow.reason}`,
    body: el('div', {},
      el('div', { class: 'alert-box' },
        el('strong', {}, exceptionRow.shipment_reference),
        exceptionRow.note || 'بلا ملاحظة إضافية'),
      field('الحالة الجديدة للشحنة', statusSelect, { required: true }),
      field('الإجراء المتخذ', actionInput, { required: true })),
    actions: (close) => [
      el('button', {
        class: 'btn primary',
        onClick: async () => {
          const action = actionInput.value.trim();
          if (action.length < 3) { toast('اكتب الإجراء المتخذ', { tone: 'error' }); return; }
          try {
            await api.post(`/api/exceptions/${exceptionRow.id}/resolve`, {
              action_taken: action, new_shipment_status: statusSelect.value,
            });
            toast('حُسمت الحالة', { tone: 'success' });
            close(); onDone?.();
          } catch (error) { toastError(error); }
        },
      }, 'حسم'),
      el('button', { class: 'btn ghost', onClick: close }, 'إلغاء'),
    ],
  });
}

/* ==================================================== الشحنات ========== */

export async function shipmentsView({ query, router }) {
  const host = el('div', {});
  const filters = {
    hub_id: query.hub_id || (hasRole('HUB_SUPERVISOR') ? state.user.hub_ids?.[0] : ''),
    status: query.status || '', service_date: query.service_date || '',
    request_kind: query.request_kind || '', search: '',
  };
  const listHost = el('div', {});

  async function load() {
    mount(listHost, el('div', { class: 'loading-block' }, el('span', { class: 'spinner' })));
    try {
      const response = await api.get('/api/shipments', { query: { ...filters, limit: 300 } });
      mount(listHost, el('div', {},
        el('p', { class: 'muted small' },
          `إجمالي المطابق: ${fmt.num(response.pagination?.total)}`),
        table([
          { label: 'المرجع', key: 'reference' },
          { label: 'النوع', render: (r) => r.request_kind === 'ON_DEMAND'
            ? el('span', { class: 'badge warning' }, 'فوري')
            : el('span', { class: 'badge' }, 'مجدول') },
          { label: 'الحالة', render: (r) => statusBadge(r.status) },
          { label: 'من', key: 'pickup_name', wrap: true },
          { label: 'إلى', key: 'dropoff_name', wrap: true },
          { label: 'اليوم', render: (r) => fmt.date(r.service_date) },
          { label: 'نافذة الالتقاط', render: (r) =>
            `${fmt.time(r.pickup_window_from)}–${fmt.time(r.pickup_window_to)}` },
          { label: 'SLA', render: (r) => fmt.time(r.sla_deadline) },
          { label: 'الالتقاط الفعلي', render: (r) => fmt.time(r.actual_pickup_at) },
          { label: 'التسليم الفعلي', render: (r) => r.actual_dropoff_at
            ? el('span', { class: r.sla_breached ? 'badge danger' : 'badge success' },
              fmt.time(r.actual_dropoff_at)) : '—' },
          { label: 'التأخير', numeric: true, render: (r) => r.delay_minutes
            ? el('span', { class: 'badge danger' }, fmt.minutes(r.delay_minutes)) : '—' },
          { label: 'السائق', key: 'driver_name' },
          { label: 'الرحلة', key: 'route_reference' },
        ], response.data, {
          onRowClick: (row) => router.go(`/shipments/${row.id}`),
          empty: 'لا توجد شحنات بهذه المرشحات',
        })));
    } catch (error) { toastError(error); }
  }

  mount(host,
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' }, el('h1', {}, 'الشحنات'))),
    el('div', { class: 'card mb-4' },
      el('div', { class: 'filters' },
        field('بحث', el('input', {
          type: 'search', placeholder: 'رقم الشحنة أو المرجع الخارجي',
          onInput: (event) => { filters.search = event.target.value; },
          onKeyDown: (event) => { if (event.key === 'Enter') load(); },
        })),
        field('مركز الانطلاق', select(hubOptions(), {
          value: filters.hub_id, onChange: (v) => { filters.hub_id = v; load(); } })),
        field('الحالة', select([
          { value: '', label: 'كل الحالات' },
          ...Object.entries(LABELS.status).map(([value, label]) => ({ value, label })),
        ], { value: filters.status, onChange: (v) => { filters.status = v; load(); } })),
        field('النوع', select([
          { value: '', label: 'الكل' }, { value: 'SCHEDULED', label: 'مجدول' },
          { value: 'ON_DEMAND', label: 'فوري' },
        ], { value: filters.request_kind,
          onChange: (v) => { filters.request_kind = v; load(); } })),
        field('اليوم', el('input', {
          type: 'date', value: filters.service_date,
          onChange: (event) => { filters.service_date = event.target.value; load(); } })),
        el('button', { class: 'btn', onClick: load }, '↻ تحديث'))),
    listHost);

  load();
  return host;
}

export async function shipmentDetailView({ params, router }) {
  const [detail, temperature] = await Promise.all([
    api.get(`/api/shipments/${params.id}`),
    can('temperature.read')
      ? api.get(`/api/temperature/shipments/${params.id}`).catch(() => null) : null,
  ]);
  const s = detail.data.shipment;
  const temp = temperature?.data;

  return el('div', {},
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, `الشحنة ${s.reference}`),
        el('p', { class: 'subtitle' },
          statusBadge(s.status),
          s.request_kind === 'ON_DEMAND'
            ? el('span', { class: 'badge warning' }, ' فوري') : null,
          ` · ${fmt.date(s.service_date)} · ${s.hub_name_ar}`,
          s.external_reference ? ` · مرجع خارجي ${s.external_reference}` : '')),
      el('div', { class: 'page-actions' },
        el('button', { class: 'btn ghost', onClick: () => history.back() }, 'رجوع'),
        s.route_id ? el('button', {
          class: 'btn', onClick: () => router.go(`/routes/${s.route_id}`) },
          'فتح الرحلة') : null)),

    s.unplannable_reason ? el('div', { class: 'alert-box danger' },
      el('strong', {}, `غير قابلة للتخطيط — ${s.unplannable_reason}`),
      s.unplannable_detail) : null,
    s.sla_breached ? el('div', { class: 'alert-box danger' },
      el('strong', {}, 'تجاوز SLA'),
      `تأخر التسليم ${fmt.minutes(s.delay_minutes)} عن الموعد النهائي`) : null,
    s.delivery_obligation_open ? el('div', { class: 'alert-box warning' },
      el('strong', {}, 'التزام تسليم مفتوح'),
      'لم تُسلَّم العينة بعد ويجب حسم الحالة — لا تُغلق الشحنة تلقائيًا.') : null,

    el('div', { class: 'grid cols-2' },
      el('div', { class: 'card' },
        el('div', { class: 'card-head' }, el('h3', {}, 'الالتقاط')),
        el('dl', { class: 'kv' },
          el('dt', {}, 'الجهة'), el('dd', {}, s.pickup_name),
          el('dt', {}, 'النوع'), el('dd', {}, LABELS.facilityType[s.pickup_type] || '—'),
          el('dt', {}, 'المسؤول'), el('dd', {}, s.pickup_contact_name || '—'),
          el('dt', {}, 'التواصل'), el('dd', { class: 'mono' }, s.pickup_contact_phone || '—'),
          el('dt', {}, 'العنوان'), el('dd', {}, s.pickup_address || '—'),
          el('dt', {}, 'الإحداثيات'), el('dd', { class: 'mono' },
            `${Number(s.pickup_lat).toFixed(5)}, ${Number(s.pickup_lon).toFixed(5)}`),
          el('dt', {}, 'النافذة'), el('dd', {},
            `${fmt.time(s.pickup_window_from)} — ${fmt.time(s.pickup_window_to)}`),
          el('dt', {}, 'المخطط'), el('dd', {}, fmt.dateTime(s.planned_pickup_at)),
          el('dt', {}, 'الفعلي'), el('dd', {}, fmt.dateTime(s.actual_pickup_at)))),

      el('div', { class: 'card' },
        el('div', { class: 'card-head' }, el('h3', {}, 'التسليم')),
        el('dl', { class: 'kv' },
          el('dt', {}, 'الجهة'), el('dd', {}, s.dropoff_name),
          el('dt', {}, 'النوع'), el('dd', {}, LABELS.facilityType[s.dropoff_type] || '—'),
          el('dt', {}, 'المسؤول'), el('dd', {}, s.dropoff_contact_name || '—'),
          el('dt', {}, 'التواصل'), el('dd', { class: 'mono' }, s.dropoff_contact_phone || '—'),
          el('dt', {}, 'العنوان'), el('dd', {}, s.dropoff_address || '—'),
          el('dt', {}, 'الإحداثيات'), el('dd', { class: 'mono' },
            `${Number(s.dropoff_lat).toFixed(5)}, ${Number(s.dropoff_lon).toFixed(5)}`),
          el('dt', {}, 'SLA'), el('dd', {}, fmt.dateTime(s.sla_deadline)),
          el('dt', {}, 'المخطط'), el('dd', {}, fmt.dateTime(s.planned_dropoff_at)),
          el('dt', {}, 'الفعلي'), el('dd', {}, fmt.dateTime(s.actual_dropoff_at))))),

    el('div', { class: 'card mt-4' },
      el('div', { class: 'card-head' }, el('h3', {}, 'المحتوى والتشغيل')),
      el('dl', { class: 'kv' },
        el('dt', {}, 'عدد القطع'), el('dd', {}, fmt.num(s.piece_count)),
        el('dt', {}, 'أنواع العينات'), el('dd', {}, (s.sample_types || []).join('، ') || '—'),
        el('dt', {}, 'نطاق الحرارة'), el('dd', {}, LABELS.temperature[s.temperature_mode]),
        el('dt', {}, 'نوع الخدمة'), el('dd', {}, LABELS.serviceType[s.service_type]),
        el('dt', {}, 'السائق'), el('dd', {}, s.driver_name || '—'),
        el('dt', {}, 'الرحلة'), el('dd', {}, s.route_reference || '—'),
        el('dt', {}, 'ملاحظات'), el('dd', {}, s.notes || '—'))),

    temp ? el('div', { class: 'card mt-4' },
      el('div', { class: 'card-head' },
        el('h3', {}, 'درجة الحرارة'),
        el('div', { class: 'spacer' }),
        el('span', { class: `badge ${temp.status === 'IN_RANGE' ? 'success'
          : temp.status === 'NO_SENSOR' ? '' : 'danger'}` }, temp.status),
        temp.has_simulated_data
          ? el('span', { class: 'badge warning' }, 'تحتوي قراءات محاكاة') : null),
      temp.message_ar ? el('div', { class: 'alert-box' }, temp.message_ar) : null,
      el('p', { class: 'small muted' },
        `النطاق المطلوب: ${temp.required_range.min}° إلى ${temp.required_range.max}° `
        + `· مزوّد الحساسات: ${temp.provider.provider} `
        + `(${temp.provider.is_real_integration ? 'تكامل حقيقي' : 'غير مفعّل'})`),
      temp.readings.length ? table([
        { label: 'الوقت', render: (r) => fmt.dateTime(r.recorded_at) },
        { label: 'الحرارة', numeric: true, render: (r) => `${r.celsius}°` },
        { label: 'الحالة', render: (r) => el('span',
          { class: `badge ${r.status === 'IN_RANGE' ? 'success' : 'danger'}` }, r.status) },
        { label: 'المصدر', render: (r) => r.source === 'SIMULATION'
          ? el('span', { class: 'badge warning' }, 'محاكاة') : r.source },
      ], temp.readings.slice(0, 20)) : null,
      temp.breaches.length ? el('div', { class: 'mt-4' },
        el('h4', {}, 'مخالفات الحرارة'),
        table([
          { label: 'النوع', key: 'breach_kind' },
          { label: 'من', render: (r) => fmt.dateTime(r.started_at) },
          { label: 'إلى', render: (r) => fmt.dateTime(r.ended_at) },
          { label: 'المدة', numeric: true, render: (r) => fmt.minutes(r.duration_minutes) },
          { label: 'المدى', numeric: true, render: (r) => `${r.min_celsius}° / ${r.max_celsius}°` },
          { label: 'الإجراء', key: 'action_taken', wrap: true },
        ], temp.breaches)) : null,
      temp.custody_chain?.length ? el('div', { class: 'mt-4' },
        el('h4', {}, 'سلسلة الحيازة'),
        table([
          { label: 'من', key: 'from_party' }, { label: 'إلى', key: 'to_party' },
          { label: 'الوقت', render: (r) => fmt.dateTime(r.occurred_at) },
        ], temp.custody_chain)) : null) : null,

    detail.data.exceptions.length ? el('div', { class: 'card mt-4' },
      el('div', { class: 'card-head' }, el('h3', {}, 'الحالات الاستثنائية')),
      table([
        { label: 'السبب', render: (r) => LABELS.exceptionReason[r.reason] || r.reason },
        { label: 'الملاحظة', key: 'note', wrap: true },
        { label: 'الوقت', render: (r) => fmt.dateTime(r.occurred_at) },
        { label: 'الحالة', key: 'status' },
        { label: 'الإجراء', key: 'action_taken', wrap: true },
      ], detail.data.exceptions)) : null,

    detail.data.documents.length ? el('div', { class: 'card mt-4' },
      el('div', { class: 'card-head' }, el('h3', {}, 'المستندات والإثباتات')),
      el('div', { class: 'row' }, detail.data.documents.map((doc) =>
        el('a', { class: 'btn sm', href: `/api/documents/${doc.id}`, target: '_blank' },
          `${doc.doc_kind} · ${fmt.dateTime(doc.uploaded_at)}`)))) : null,

    el('div', { class: 'card mt-4' },
      el('div', { class: 'card-head' }, el('h3', {}, 'سجل تغييرات الحالة')),
      table([
        { label: 'من', render: (r) => LABELS.status[r.from_status] || r.from_status || '—' },
        { label: 'إلى', render: (r) => statusBadge(r.to_status) },
        { label: 'الوقت', render: (r) => fmt.dateTime(r.changed_at) },
        { label: 'الدور', key: 'actor_role' },
        { label: 'السبب', key: 'reason', wrap: true },
        { label: 'المصدر', key: 'source' },
      ], detail.data.status_history)),

    el('div', { class: 'card mt-4' },
      el('div', { class: 'card-head' }, el('h3', {}, 'الأحداث الفعلية')),
      table([
        { label: 'الحدث', key: 'event_type' },
        { label: 'وقت الحدث', render: (r) => fmt.dateTime(r.occurred_at) },
        { label: 'وقت الاستلام', render: (r) => fmt.dateTime(r.received_at) },
        { label: 'الموقع', render: (r) => r.lat
          ? el('span', { class: 'mono tiny' },
            `${Number(r.lat).toFixed(4)}, ${Number(r.lon).toFixed(4)}`) : '—' },
        { label: 'دون اتصال', render: (r) => r.was_offline
          ? el('span', { class: 'badge warning' }, 'مزامنة لاحقة') : '—' },
      ], detail.data.events)));
}

/* =============================================== الطلبات الفورية ======= */

export async function onDemandView({ router }) {
  const host = el('div', {});
  const filters = { status: '', hub_id: hasRole('HUB_SUPERVISOR')
    ? state.user.hub_ids?.[0] : '' };
  const listHost = el('div', {});

  async function load() {
    mount(listHost, el('div', { class: 'loading-block' }, el('span', { class: 'spinner' })));
    try {
      const response = await api.get('/api/ondemand', { query: filters });
      mount(listHost, table([
        { label: 'المرجع', key: 'reference' },
        { label: 'الحالة', render: (r) => statusBadge(r.status) },
        { label: 'من', key: 'pickup_name', wrap: true },
        { label: 'إلى', key: 'dropoff_name', wrap: true },
        { label: 'النافذة', render: (r) =>
          `${fmt.time(r.pickup_window_from)}–${fmt.time(r.pickup_window_to)}` },
        { label: 'SLA', render: (r) => fmt.dateTime(r.sla_deadline) },
        { label: 'الرحلة', key: 'route_reference' },
        { label: 'السائق', key: 'driver_name' },
        { label: 'أُنشئ', render: (r) => fmt.ago(r.created_at) },
        { label: 'إجراءات', render: (r) => el('div', { class: 'btn-row' },
          can('ondemand.review') && r.status === 'PENDING_APPROVAL' ? [
            el('button', {
              class: 'btn sm primary',
              onClick: async (event) => {
                event.stopPropagation();
                try {
                  await api.post(`/api/ondemand/${r.id}/review`, { approve: true });
                  toast('اعتُمد الطلب — انتقل إلى قائمة الإسناد', { tone: 'success' });
                  load();
                } catch (error) { toastError(error); }
              },
            }, 'موافقة'),
            el('button', {
              class: 'btn sm danger',
              onClick: async (event) => {
                event.stopPropagation();
                const reason = await confirmDialog({
                  title: 'رفض الطلب الفوري',
                  message: `سيُبلَّغ مقدم الطلب بسبب الرفض.`,
                  confirmLabel: 'رفض', tone: 'danger', requireReason: true,
                });
                if (!reason) return;
                try {
                  await api.post(`/api/ondemand/${r.id}/review`,
                    { approve: false, reason });
                  toast('رُفض الطلب', { tone: 'warning' });
                  load();
                } catch (error) { toastError(error); }
              },
            }, 'رفض'),
          ] : null,
          can('routes.assign') && ['PENDING_ASSIGNMENT', 'UNPLANNABLE'].includes(r.status)
            ? el('button', {
              class: 'btn sm primary',
              onClick: (event) => { event.stopPropagation(); openInsertionDialog(r, load); },
            }, 'فحص الإدراج وإسناد') : null) },
      ], response.data, {
        onRowClick: (row) => router.go(`/shipments/${row.id}`),
        empty: 'لا توجد طلبات فورية',
      }));
    } catch (error) { toastError(error); }
  }

  mount(host,
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, 'الطلبات الفورية'),
        el('p', { class: 'subtitle' },
          'الطلب الفوري لا يدخل التحسين الأسبوعي؛ يُفحص إدراجه في جداول السائقين '
          + 'النشطين، ومن موقع السائق الحالي إن كان قد بدأ العمل.'))),
    el('div', { class: 'card mb-4' },
      el('div', { class: 'filters' },
        field('مركز الانطلاق', select(hubOptions(), {
          value: filters.hub_id, onChange: (v) => { filters.hub_id = v; load(); } })),
        field('الحالة', select([
          { value: '', label: 'الكل' },
          { value: 'PENDING_APPROVAL', label: 'بانتظار المراجعة' },
          { value: 'PENDING_ASSIGNMENT', label: 'بانتظار الإسناد' },
          { value: 'PUBLISHED', label: 'منشورة' },
          { value: 'COMPLETED', label: 'مكتملة' },
          { value: 'REJECTED', label: 'مرفوضة' },
          { value: 'CANCELLED_BEFORE_PICKUP', label: 'ملغاة' },
        ], { value: filters.status, onChange: (v) => { filters.status = v; load(); } })),
        el('button', { class: 'btn', onClick: load }, '↻ تحديث'))),
    listHost);

  load();
  const off = events.on('on_demand', load);
  return host;
}

async function openInsertionDialog(request, onDone) {
  let payload;
  try {
    payload = (await api.get(`/api/ondemand/${request.id}/options`)).data;
  } catch (error) { toastError(error); return; }

  let chosen = payload.options[0] || null;
  const listHost = el('div', { class: 'stack-2' });
  const render = () => {
    mount(listHost, payload.options.length
      ? payload.options.map((option) => el('button', {
        class: 'warning-card sev-INFO',
        style: chosen === option
          ? { borderColor: 'var(--brand)', background: 'var(--brand-soft)' } : {},
        onClick: () => { chosen = option; render(); },
      },
        el('div', { class: 'wc-head' },
          el('span', { class: 'wc-title' }, option.route_reference),
          el('span', { class: 'badge' }, option.driver_name || 'بلا سائق'),
          option.route_started
            ? el('span', { class: 'badge info' }, 'الرحلة جارية') : null,
          chosen === option ? el('span', { class: 'badge brand' }, 'مختار') : null),
        el('div', { class: 'wc-line' },
          `زيادة ${fmt.minutes(option.added_minutes)} و${fmt.km(option.added_km)} `
          + `· النهاية الجديدة ${fmt.time(option.new_end_at)}`),
        el('div', { class: 'wc-line tiny muted' },
          `المسار محسوب من: ${option.computed_from} `
          + `· أقل هامش زمني متبقٍ ${fmt.minutes(option.min_slack_minutes)}`)))
      : el('div', { class: 'alert-box danger' },
        el('strong', {}, 'لا يوجد إدراج ممكن دون خرق قيد صلب'),
        el('ul', { style: { margin: '4px 0 0', paddingInlineStart: '18px' } },
          payload.rejections.map((r) => el('li', {}, r.message_ar)))));
  };
  render();

  modal({
    title: `إدراج الطلب ${request.reference}`,
    wide: true,
    body: el('div', {},
      el('p', { class: 'muted small' },
        'كل خيار مفحوص مقابل كل القيود الصلبة للرحلة القائمة — لا يُعرض خيار يخرق '
        + 'نافذة أو SLA لشحنة أخرى.'),
      listHost),
    actions: (close) => [
      payload.options.length ? el('button', {
        class: 'btn primary',
        onClick: async () => {
          if (!chosen) return;
          try {
            await api.post(`/api/ondemand/${request.id}/assign`, {
              route_id: chosen.route_id,
              pickup_position: chosen.pickup_position,
              delivery_position: chosen.delivery_position,
            });
            toast('أُدرج الطلب ووصل السائق فورًا', { tone: 'success' });
            close(); onDone?.();
          } catch (error) { toastError(error); }
        },
      }, 'إدراج وإسناد') : null,
      el('button', { class: 'btn ghost', onClick: close }, 'إغلاق'),
    ],
  });
}

/* ================================================ أداء السائقين ======== */

export async function driversPerformanceView({ router }) {
  const host = el('div', {});
  const filters = {
    date_from: fmt.isoDate(new Date(Date.now() - 6 * 86400000)),
    date_to: fmt.isoDate(),
    hub_id: hasRole('HUB_SUPERVISOR') ? state.user.hub_ids?.[0] : '',
    include_test_data: 'true',
  };
  const bodyHost = el('div', {});

  async function load() {
    mount(bodyHost, el('div', { class: 'loading-block' }, el('span', { class: 'spinner' })));
    try {
      const response = await api.get('/api/reports/routes', { query: filters });
      const { totals, per_driver: perDriver, fairness } = response.data;
      mount(bodyHost,
        el('div', { class: 'kpi-grid mb-4' },
          kpi('الرحلات', fmt.num(totals.route_count)),
          kpi('السائقون', fmt.num(totals.driver_count)),
          kpi('المسافة', fmt.km(totals.total_distance_km)),
          kpi('زمن القيادة', fmt.minutes(totals.total_drive_minutes)),
          kpi('زمن الانتظار', fmt.minutes(totals.total_wait_minutes)),
          kpi('نقاط الالتقاط', fmt.num(totals.pickup_points)),
          kpi('نقاط التسليم', fmt.num(totals.delivery_points)),
          kpi('رحلات بعيدة', fmt.num(totals.long_haul_count)),
          kpi('التكلفة', fmt.money(totals.total_cost))),

        fairness ? el('div', { class: 'card mb-4' },
          el('div', { class: 'card-head' }, el('h3', {}, 'عدالة توزيع العمل')),
          el('div', { class: 'kpi-grid' },
            kpi('المتوسط', fmt.minutes(fairness.mean_minutes)),
            kpi('الانحراف المعياري', fmt.minutes(fairness.std_dev_minutes)),
            kpi('الأعلى', fmt.minutes(fairness.max_minutes)),
            kpi('الأدنى', fmt.minutes(fairness.min_minutes)),
            kpi('الفارق', fmt.pct(fairness.spread_pct),
              { tone: fairness.spread_pct > 35 ? 'warning' : 'success' })),
          fairness.spread_pct > 35 ? el('div', { class: 'alert-box warning mt-4' },
            el('strong', {}, 'التوزيع غير متوازن'),
            'الفارق بين أثقل سائق وأخفهم يتجاوز ٣٥٪. ارفع وزن عدالة التوزيع في '
            + 'إعدادات التخطيط وأعد التشغيل، أو أعد التوزيع يدويًا.') : null) : null,

        el('div', { class: 'card' },
          el('div', { class: 'card-head' }, el('h3', {}, 'تفصيل حسب السائق')),
          table([
            { label: 'السائق', key: 'driver_name' },
            { label: 'الرمز', key: 'driver_code' },
            { label: 'الرحلات', numeric: true, render: (r) => fmt.num(r.route_count) },
            { label: 'بدأها', numeric: true, render: (r) => fmt.num(r.started_routes) },
            { label: 'أكملها', numeric: true, render: (r) => fmt.num(r.completed_routes) },
            { label: 'بعيدة', numeric: true, render: (r) => fmt.num(r.long_haul_count) },
            { label: 'المسافة', numeric: true, render: (r) => fmt.km(r.distance_km) },
            { label: 'ساعات العمل', numeric: true, render: (r) => fmt.minutes(r.working_minutes) },
            { label: 'التقاط', numeric: true, render: (r) => fmt.num(r.pickup_points) },
            { label: 'تسليم', numeric: true, render: (r) => fmt.num(r.delivery_points) },
            { label: 'الحمل', render: (r) => {
              const max = Math.max(...perDriver.map((d) => Number(d.working_minutes) || 0), 1);
              const pct = (Number(r.working_minutes) / max) * 100;
              return el('div', { class: 'progress', style: { width: '90px' } },
                el('span', { style: { width: `${pct}%` } }));
            } },
          ], perDriver, { empty: 'لا توجد رحلات في هذه الفترة' })));
    } catch (error) { toastError(error); }
  }

  mount(host,
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' }, el('h1', {}, 'أداء السائقين وعدالة التوزيع'))),
    el('div', { class: 'card mb-4' },
      el('div', { class: 'filters' },
        field('من', el('input', { type: 'date', value: filters.date_from,
          onChange: (e) => { filters.date_from = e.target.value; load(); } })),
        field('إلى', el('input', { type: 'date', value: filters.date_to,
          onChange: (e) => { filters.date_to = e.target.value; load(); } })),
        field('مركز الانطلاق', select(hubOptions(), {
          value: filters.hub_id, onChange: (v) => { filters.hub_id = v; load(); } })),
        el('button', { class: 'btn', onClick: load }, '↻ تحديث'))),
    bodyHost);

  load();
  return host;
}
