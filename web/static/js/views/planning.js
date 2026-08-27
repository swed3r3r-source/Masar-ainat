/* شاشات التخطيط: رفع الجدول، تشغيل المحرك، معاينة المسارات، تفاصيل الرحلة. */

import {
  api, el, state, can, fmt, kpi, table, field, select, toast, toastError, modal,
  confirmDialog, statusBadge, severityBadge, downloadUrl, mount, clear,
  PLAN_STATUS_LABEL, ROUTE_STATUS_LABEL, WARNING_LABEL, LABELS,
} from '../core.js';
import { MasarMap, MAP_COLORS, openNavigation } from '../map.js';

/* ==================================================== رفع الجدول ======= */

export async function importsView({ router }) {
  const response = await api.get('/api/imports');
  const imports = response.data || [];

  const fileInput = el('input', {
    type: 'file', accept: '.csv,.xlsx,.xlsm,text/csv',
    style: { display: 'none' },
    onChange: async (event) => {
      const file = event.target.files?.[0];
      if (!file) return;
      const button = document.getElementById('upload-btn');
      button.disabled = true;
      button.textContent = 'جارٍ الرفع…';
      try {
        const buffer = await file.arrayBuffer();
        const created = await api.post('/api/imports', buffer, {
          headers: { 'content-type': file.type || 'text/csv', 'x-file-name': file.name },
        });
        if (created.data.duplicate_of) {
          toast(`تنبيه: نفس الملف رُفع سابقًا باسم ${created.data.duplicate_of}`,
            { tone: 'warning', timeout: 9000 });
        }
        router.go(`/imports/${created.data.id}`);
      } catch (error) {
        toastError(error);
        button.disabled = false;
        button.textContent = '⬆ رفع ملف الجدول';
      }
    },
  });

  return el('div', {},
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, 'رفع الجدول الأسبوعي'),
        el('p', { class: 'subtitle' },
          'رفع الملف بنجاح لا يعني أن الخطة قابلة للتنفيذ — يمر كل صف بفحص بنيوي '
          + 'وفحص جدوى رياضي قبل أن يصبح شحنة.')),
      el('div', { class: 'page-actions' },
        el('button', { class: 'btn', onClick: () =>
          downloadUrl('/api/imports/template?format=csv', 'masar-template.csv') },
          '⬇ قالب CSV'),
        el('button', { class: 'btn', onClick: () =>
          downloadUrl('/api/imports/template?format=xlsx', 'masar-template.xlsx') },
          '⬇ قالب Excel'),
        can('schedule.upload') ? el('button', {
          class: 'btn primary', id: 'upload-btn',
          onClick: () => fileInput.click(),
        }, '⬆ رفع ملف الجدول') : null)),
    fileInput,

    el('div', { class: 'card' },
      el('div', { class: 'card-head' }, el('h3', {}, 'عمليات الرفع السابقة')),
      table([
        { label: 'المرجع', key: 'reference' },
        { label: 'الملف', key: 'original_filename', wrap: true },
        { label: 'الحالة', render: (row) => importStatusBadge(row.status) },
        { label: 'الصفوف', numeric: true, render: (row) => fmt.num(row.total_rows) },
        { label: 'صالحة', numeric: true, render: (row) =>
          el('span', { class: 'badge success' }, fmt.num(row.valid_rows)) },
        { label: 'غير صالحة', numeric: true, render: (row) => Number(row.invalid_rows)
          ? el('span', { class: 'badge danger' }, fmt.num(row.invalid_rows)) : '—' },
        { label: 'الفترة', render: (row) => row.period_start
          ? `${fmt.date(row.period_start)} — ${fmt.date(row.period_end)}` : '—' },
        { label: 'رفعها', key: 'uploaded_by_name' },
        { label: 'التاريخ', render: (row) => fmt.dateTime(row.created_at) },
      ], imports, {
        onRowClick: (row) => router.go(`/imports/${row.id}`),
        empty: 'لم يُرفع أي جدول بعد',
      })));
}

const IMPORT_STATUS = {
  UPLOADED: ['مرفوع', ''], MAPPING: ['مطابقة الأعمدة', 'info'],
  VALIDATING: ['قيد التحقق', 'info'], VALIDATED: ['مُتحقق', 'success'],
  PARTIALLY_VALID: ['صالح جزئيًا', 'warning'], REJECTED: ['مرفوض', 'danger'],
  COMMITTED: ['معتمد', 'brand'],
};

function importStatusBadge(status) {
  const [label, tone] = IMPORT_STATUS[status] || [status, ''];
  return el('span', { class: `badge ${tone}` }, label);
}

export async function importDetailView({ params, router }) {
  const response = await api.get(`/api/imports/${params.id}`);
  const record = response.data.import;
  const rows = response.data.rows || [];
  const host = el('div', {});

  const stepIndex = { UPLOADED: 0, MAPPING: 1, VALIDATING: 2, VALIDATED: 2,
    PARTIALLY_VALID: 2, REJECTED: 2, COMMITTED: 3 }[record.status] ?? 0;

  const invalidRows = rows.filter((row) => !row.is_valid);
  const excludedCount = rows.filter((row) => row.is_excluded).length;

  const render = (current) => {
    const canCommit = ['VALIDATED', 'PARTIALLY_VALID'].includes(current.status)
      && can('schedule.commit');

    mount(host,
      el('div', { class: 'page-head' },
        el('div', { class: 'titles' },
          el('h1', {}, `استيراد ${current.reference}`),
          el('p', { class: 'subtitle' }, current.original_filename,
            ' · ', importStatusBadge(current.status))),
        el('div', { class: 'page-actions' },
          el('button', { class: 'btn ghost', onClick: () => router.go('/imports') }, 'رجوع'),
          invalidRows.length ? el('button', {
            class: 'btn',
            onClick: () => downloadUrl(`/api/imports/${params.id}/errors.csv`,
              `أخطاء-${current.reference}.csv`),
          }, '⬇ تنزيل ملف الأخطاء') : null,
          can('schedule.upload') ? el('button', {
            class: 'btn',
            onClick: async () => {
              try {
                const result = await api.post(`/api/imports/${params.id}/validate`, {});
                toast(`اكتمل التحقق: ${result.data.valid_rows} صالحة، `
                  + `${result.data.invalid_rows} غير صالحة`, { tone: 'success' });
                router.resolve();
              } catch (error) { toastError(error); }
            },
          }, '↻ إعادة التحقق') : null,
          canCommit ? el('button', {
            class: 'btn primary',
            onClick: async () => {
              const confirmed = await confirmDialog({
                title: 'اعتماد الاستيراد',
                message: `سيتم إنشاء ${current.valid_rows} شحنة من الصفوف الصالحة`
                  + (current.invalid_rows
                    ? `، واستبعاد ${current.invalid_rows} صفًا غير صالح.` : '.'),
                confirmLabel: 'اعتماد وإنشاء الشحنات',
              });
              if (!confirmed) return;
              try {
                const result = await api.post(`/api/imports/${params.id}/commit`,
                  { skip_invalid: true });
                toast(`أُنشئت ${result.data.created_shipments} شحنة`, { tone: 'success' });
                router.resolve();
              } catch (error) { toastError(error); }
            },
          }, '✓ اعتماد وإنشاء الشحنات') : null)),

      el('div', { class: 'steps' },
        ['رفع الملف', 'مطابقة الأعمدة', 'التحقق وفحص الجدوى', 'اعتماد وإنشاء الشحنات']
          .map((label, index) => el('div', {
            class: `step ${index === stepIndex ? 'active' : ''} ${index < stepIndex ? 'done' : ''}`,
          }, el('span', { class: 'n' }, index < stepIndex ? '✓' : String(index + 1)), label))),

      el('div', { class: 'kpi-grid mb-4' },
        kpi('إجمالي الصفوف', fmt.num(current.total_rows)),
        kpi('صالحة', fmt.num(current.valid_rows), { tone: 'success' }),
        kpi('غير صالحة', fmt.num(current.invalid_rows),
          { tone: current.invalid_rows ? 'danger' : '' }),
        kpi('مكررة', fmt.num(current.duplicate_rows),
          { tone: current.duplicate_rows ? 'warning' : '' }),
        kpi('مستبعدة يدويًا', fmt.num(excludedCount)),
        kpi('أيام الخطة', fmt.num(current.summary?.dates?.length || 0), {
          hint: current.period_start
            ? `${fmt.date(current.period_start)} — ${fmt.date(current.period_end)}` : '' })),

      current.summary?.by_code?.length
        ? el('div', { class: 'card mb-4' },
          el('div', { class: 'card-head' }, el('h3', {}, 'ملخص المشكلات حسب النوع')),
          table([
            { label: 'الرمز', key: 'code' },
            { label: 'الخطورة', render: (row) =>
              el('span', { class: `badge ${row.severity === 'ERROR' ? 'danger' : 'warning'}` },
                row.severity === 'ERROR' ? 'خطأ' : 'تحذير') },
            { label: 'العدد', numeric: true, render: (row) => fmt.num(row.count) },
            { label: 'مثال', key: 'sample_message_ar', wrap: true },
          ], current.summary.by_code))
        : null,

      el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h3', {}, 'الصفوف'),
          el('div', { class: 'spacer' }),
          el('label', { class: 'small muted' },
            el('input', {
              type: 'checkbox', id: 'only-invalid',
              onChange: (event) => renderRows(event.target.checked),
            }), ' إظهار الصفوف غير الصالحة فقط')),
        el('div', { id: 'rows-host' })));

    renderRows(false);
  };

  const renderRows = (onlyInvalid) => {
    const host2 = document.getElementById('rows-host');
    if (!host2) return;
    const visible = (onlyInvalid ? invalidRows : rows).slice(0, 300);
    mount(host2, table([
      { label: 'الصف', numeric: true, key: 'row_number' },
      { label: 'الحالة', render: (row) => row.is_excluded
        ? el('span', { class: 'badge' }, 'مستبعد')
        : row.is_valid ? el('span', { class: 'badge success' }, 'صالح')
          : el('span', { class: 'badge danger' }, 'غير صالح') },
      { label: 'جهة الالتقاط', render: (row) =>
        row.normalized?.pickup_name || row.raw?.['رمز جهة الالتقاط'] || '—', wrap: true },
      { label: 'جهة التسليم', render: (row) =>
        row.normalized?.dropoff_name || row.raw?.['رمز جهة التسليم'] || '—', wrap: true },
      { label: 'التاريخ', render: (row) =>
        row.normalized?.service_date || row.raw?.['تاريخ الخدمة'] || '—' },
      { label: 'المشكلات', wrap: true, render: (row) => {
        const issues = [...(row.errors || []), ...(row.warnings || [])];
        if (!issues.length) return '—';
        return el('div', { class: 'stack-2' }, issues.map((issue) =>
          el('div', { class: 'tiny' },
            el('span', { class: `badge ${issue.severity === 'ERROR' ? 'danger' : 'warning'}` },
              issue.column_label_ar || issue.code),
            ' ', issue.message_ar)));
      } },
    ], visible, { empty: onlyInvalid ? 'لا توجد صفوف غير صالحة' : 'لا توجد صفوف' }));
    if (visible.length === 300) {
      host2.append(el('p', { class: 'muted small mt-4' },
        'عُرضت أول ٣٠٠ صف — نزّل ملف الأخطاء لعرض الكل'));
    }
  };

  render(record);
  return host;
}

/* ================================================ تشغيل محرك المسارات == */

export async function planRunView({ router }) {
  const hubs = state.hubs.length ? state.hubs
    : (await api.get('/api/md/hubs', { query: { limit: 500 } })).data;
  const imports = can('schedule.read')
    ? (await api.get('/api/imports')).data.filter((i) => i.status === 'COMMITTED')
    : [];

  const selectedHubs = new Set(hubs.map((hub) => hub.id));
  const dateFrom = el('input', { type: 'date', value: fmt.isoDate() });
  const dateTo = el('input', { type: 'date', value: fmt.isoDate() });
  const timeLimit = el('input', { type: 'number', value: '20', min: '1', max: '600' });
  const importSelect = select(
    [{ value: '', label: 'بلا ربط باستيراد محدد' },
      ...imports.map((i) => ({ value: i.id,
        label: `${i.reference} — ${fmt.date(i.period_start)} إلى ${fmt.date(i.period_end)}` }))],
    {});
  const providerSelect = select([
    { value: '', label: `المُعدّ حاليًا (${state.meta.config.routing_provider})` },
    { value: 'osrm', label: 'OSRM — أزمنة طريق حقيقية' },
    { value: 'haversine', label: 'تقديري (خط مستقيم × معامل التفافية) — معلن' },
  ], {});
  const nameInput = el('input', { type: 'text', placeholder: 'اسم الخطة (اختياري)' });
  const resultHost = el('div', { class: 'mt-4' });

  const runButton = el('button', { class: 'btn primary lg', onClick: run }, '⚙ تشغيل المحرك');

  async function run() {
    const dates = [];
    const start = new Date(dateFrom.value);
    const end = new Date(dateTo.value);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end < start) {
      toast('حدد فترة صحيحة', { tone: 'error' });
      return;
    }
    for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
      dates.push(d.toISOString().slice(0, 10));
    }
    if (dates.length > 14) { toast('أقصى فترة ١٤ يومًا في تشغيل واحد', { tone: 'error' }); return; }
    if (!selectedHubs.size) { toast('اختر مركز انطلاق واحدًا على الأقل', { tone: 'error' }); return; }

    runButton.disabled = true;
    runButton.textContent = 'جارٍ التشغيل…';
    mount(resultHost, el('div', { class: 'loading-block' },
      el('span', { class: 'spinner' }),
      el('p', { class: 'muted' },
        `يعمل المحرك على ${selectedHubs.size} مركز × ${dates.length} يوم — `
        + 'الحل رياضي وقد يستغرق دقائق حسب حجم المسألة')));

    try {
      const result = await api.post('/api/plans/run', {
        hub_ids: [...selectedHubs],
        dates,
        import_id: importSelect.value || null,
        time_limit_seconds: Number(timeLimit.value) || 20,
        routing_provider: providerSelect.value || null,
        name: nameInput.value.trim() || null,
      });
      toast('اكتمل التخطيط', { tone: 'success' });
      router.go(`/plans/${result.data.plan_id}`);
    } catch (error) {
      toastError(error);
      mount(resultHost, el('div', { class: 'alert-box danger' },
        el('strong', {}, 'فشل تشغيل المحرك'), error.message));
    } finally {
      runButton.disabled = false;
      runButton.textContent = '⚙ تشغيل المحرك';
    }
  }

  return el('div', {},
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, 'إنشاء المسارات'),
        el('p', { class: 'subtitle' },
          'يُبنى الحل بنموذج رياضي (PDPTW) لا بترتيب تقديري: إدراج بالندم ثم '
          + 'تحسين ALNS، ويمر كل مسار على فحص القيود الصلبة قبل قبوله.'))),

    el('div', { class: 'card' },
      el('div', { class: 'card-head' }, el('h3', {}, 'نطاق التشغيل')),
      el('div', { class: 'grid cols-2' },
        field('من تاريخ', dateFrom, { required: true }),
        field('إلى تاريخ', dateTo, { required: true }),
        field('الاستيراد المرتبط', importSelect,
          { help: 'للربط بين الخطة والجدول الأسبوعي الذي أنتجها' }),
        field('اسم الخطة', nameInput),
        field('مزوّد المسافات', providerSelect,
          { help: state.meta.config.routing_is_estimated
            ? '⚠ المزوّد الحالي تقديري — الخطة ستُوسَم بذلك ولن تُعرض كأنها أزمنة طريق حقيقية'
            : 'يُستخدم لحساب مصفوفة الأزمنة والمسافات' }),
        field('مهلة الحل (ثانية)', timeLimit,
          { help: 'كلما زادت المهلة تحسّنت الجودة — تُسجَّل مع الخطة' })),

      el('div', { class: 'field' },
        el('label', {}, 'مراكز الانطلاق'),
        el('div', { class: 'btn-row' }, hubs.map((hub) => {
          const checkbox = el('input', {
            type: 'checkbox', checked: true,
            onChange: (event) => {
              if (event.target.checked) selectedHubs.add(hub.id);
              else selectedHubs.delete(hub.id);
            },
          });
          return el('label', { class: 'btn sm' }, checkbox, ' ', hub.name_ar);
        }))),

      el('div', { class: 'btn-row mt-4' }, runButton)),
    resultHost);
}

/* ==================================================== قائمة الخطط ====== */

export async function planListView({ router }) {
  const response = await api.get('/api/plans');
  const plans = response.data || [];

  return el('div', {},
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' }, el('h1', {}, 'الخطط المحفوظة')),
      el('div', { class: 'page-actions' },
        can('plan.compare') && plans.length >= 2 ? el('button', {
          class: 'btn', onClick: () => router.go('/plans/compare'),
        }, '⇄ مقارنة خطتين') : null,
        can('plan.optimize') ? el('button', {
          class: 'btn primary', onClick: () => router.go('/plans/new'),
        }, '⚙ خطة جديدة') : null)),

    table([
      { label: 'المرجع', key: 'reference' },
      { label: 'الاسم', key: 'name_ar', wrap: true },
      { label: 'الحالة', render: (row) => statusBadge(row.status, PLAN_STATUS_LABEL) },
      { label: 'الفترة', render: (row) =>
        `${fmt.date(row.period_start)} — ${fmt.date(row.period_end)}` },
      { label: 'الرحلات', numeric: true, render: (row) => fmt.num(row.metrics?.route_count) },
      { label: 'السائقون', numeric: true, render: (row) => fmt.num(row.metrics?.drivers_used) },
      { label: 'غير مخططة', numeric: true, render: (row) => {
        const value = Number(row.metrics?.unplannable_count || 0);
        return value ? el('span', { class: 'badge danger' }, fmt.num(value)) : '—';
      } },
      { label: 'المسافة', numeric: true, render: (row) => fmt.km(row.metrics?.total_distance_km) },
      { label: 'زمن الحل', numeric: true, render: (row) =>
        row.solve_ms ? `${fmt.num(row.solve_ms)} م.ث` : '—' },
      { label: 'الأزمنة', render: (row) => row.routing_estimated
        ? el('span', { class: 'badge warning' }, 'تقديرية')
        : el('span', { class: 'badge success' }, 'طريق حقيقي') },
      { label: 'أنشأها', key: 'created_by_name' },
      { label: 'التاريخ', render: (row) => fmt.dateTime(row.created_at) },
    ], plans, {
      onRowClick: (row) => router.go(`/plans/${row.id}`),
      empty: 'لا توجد خطط محفوظة',
    }));
}

export async function planCompareView({ router }) {
  const plans = (await api.get('/api/plans')).data || [];
  const options = plans.map((p) => ({ value: p.id,
    label: `${p.reference} — ${p.name_ar}` }));
  const selectA = select(options, { value: plans[1]?.id });
  const selectB = select(options, { value: plans[0]?.id });
  const host = el('div', { class: 'mt-4' });

  async function compare() {
    try {
      const result = await api.get('/api/plans/compare',
        { query: { a: selectA.value, b: selectB.value } });
      const data = result.data;
      mount(host, el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h3', {}, `${data.plan_a.reference} ⇄ ${data.plan_b.reference}`)),
        table([
          { label: 'المؤشر', key: 'label_ar' },
          { label: data.plan_a.reference, numeric: true,
            render: (r) => fmt.num(r.plan_a, 2) },
          { label: data.plan_b.reference, numeric: true,
            render: (r) => fmt.num(r.plan_b, 2) },
          { label: 'الفرق', numeric: true, render: (r) => {
            const better = r.delta < 0;
            return el('span', { class: `badge ${better ? 'success' : r.delta > 0 ? 'danger' : ''}` },
              `${r.delta > 0 ? '+' : ''}${fmt.num(r.delta, 2)}`
              + (r.delta_pct !== null ? ` (${fmt.pct(r.delta_pct)})` : ''));
          } },
        ], data.comparison)));
    } catch (error) { toastError(error); }
  }

  return el('div', {},
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, 'مقارنة الخطط'),
        el('p', { class: 'subtitle' },
          'المقارنة على نفس المقاييس ومن نفس مصدر البيانات.'))),
    el('div', { class: 'card' },
      el('div', { class: 'grid cols-2' },
        field('الخطة المرجعية (أ)', selectA),
        field('الخطة المقارَنة (ب)', selectB)),
      el('button', { class: 'btn primary', onClick: compare }, 'قارن')),
    host);
}

/* ================================================ معاينة المسارات ====== */

export async function planDetailView({ params, router }) {
  const response = await api.get(`/api/plans/${params.id}`);
  const { plan, days, routes, warnings, estimations, unplannable } = response.data;
  const metrics = plan.metrics || {};
  const improvement = metrics.improvement;

  const host = el('div', {});
  const canApprove = plan.status === 'OPTIMIZED' && can('plan.approve');
  const canDispatch = plan.status === 'APPROVED' && can('plan.dispatch');

  const warningsBySeverity = warnings.reduce((acc, w) => {
    acc[w.severity] = (acc[w.severity] || 0) + 1; return acc;
  }, {});

  mount(host,
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, plan.name_ar),
        el('p', { class: 'subtitle' },
          plan.reference, ' · ', statusBadge(plan.status, PLAN_STATUS_LABEL),
          ` · ${fmt.date(plan.period_start)} — ${fmt.date(plan.period_end)}`,
          plan.created_by_name ? ` · أنشأها ${plan.created_by_name}` : '')),
      el('div', { class: 'page-actions' },
        el('button', { class: 'btn ghost', onClick: () => router.go('/plans') }, 'رجوع'),
        canApprove ? el('button', {
          class: 'btn primary',
          onClick: async () => {
            const blocking = unplannable.length;
            const parts = [];
            if (blocking) {
              parts.push(`تنبيه: ${blocking} شحنة غير قابلة للتخطيط ستبقى خارج `
                + 'الخطة. الاعتماد لا يجعلها مخططة، ويُسجَّل ذلك في سجل التدقيق.');
            }
            // بوابة الأزمنة التقديرية: الإقرار صريح ومسجَّل، والاعتماد
            // مرفوض أصلًا في بيئة الإنتاج (يرفضه الخادم لا الواجهة).
            if (plan.routing_estimated) {
              parts.push('⚠ أزمنة هذه الخطة تقديرية لا حقيقية. الانحراف المقيس '
                + 'عن شبكة الطرق يبلغ +28٪ مسافةً و+64٪ زمنًا. الاعتماد هنا '
                + 'إقرار صريح بأنها غير صالحة لالتزام SLA حقيقي، ويُسجَّل باسمك '
                + 'في سجل التدقيق. في بيئة الإنتاج هذا الاعتماد مرفوض.');
            }
            if (!parts.length) {
              parts.push('سيتم اعتماد الخطة تمهيدًا لإرسالها لمراكز الانطلاق.');
            }
            const confirmed = await confirmDialog({
              title: 'اعتماد الخطة',
              message: parts.join(' '),
              confirmLabel: plan.routing_estimated ? 'أقرّ واعتمد' : 'اعتماد',
            });
            if (!confirmed) return;
            try {
              await api.post(`/api/plans/${params.id}/approve`,
                { acknowledge_estimated: !!plan.routing_estimated });
              toast('اعتُمدت الخطة', { tone: 'success' });
              router.resolve();
            } catch (error) { toastError(error); }
          },
        }, '✓ اعتماد الخطة') : null,
        canDispatch ? el('button', {
          class: 'btn primary',
          onClick: async () => {
            try {
              const result = await api.post(`/api/plans/${params.id}/dispatch`, {});
              toast(`أُرسلت الخطة إلى ${result.data.hub_count} مركز`, { tone: 'success' });
              router.resolve();
            } catch (error) { toastError(error); }
          },
        }, '➤ إرسال لمراكز الانطلاق') : null)),

    plan.routing_estimated ? el('div', { class: 'alert-box warning' },
      el('strong', {}, 'أزمنة القيادة في هذه الخطة تقديرية'),
      `المزوّد المستخدم: ${plan.routing_provider}. التقدير مبني على المسافة `
      + 'الجوية × معامل التفافية، وليس على شبكة طرق حقيقية. لا تُعتمد هذه الخطة '
      + 'للتشغيل الفعلي قبل تفعيل مزوّد طرق حقيقي.') : null,

    plan.failure_reason ? el('div', { class: 'alert-box danger' },
      el('strong', {}, 'فشل التشغيل'), plan.failure_reason) : null,

    // ------------------------------------------------- المقاييس ------
    el('h3', {}, 'مقاييس الخطة'),
    el('div', { class: 'kpi-grid mb-4' },
      kpi('الشحنات', fmt.num(metrics.shipment_count), {
        hint: `${fmt.num(metrics.planned_shipment_count)} مخططة` }),
      kpi('الرحلات', fmt.num(metrics.route_count)),
      kpi('أيام الخطة', fmt.num(metrics.day_count)),
      kpi('مراكز الانطلاق', fmt.num(metrics.hub_count)),
      kpi('سائقون مستخدمون', fmt.num(metrics.drivers_used), {
        hint: `الحد الأدنى النظري ${fmt.num(metrics.drivers_theoretical_minimum)}` }),
      kpi('سائقون متوفرون', fmt.num(metrics.drivers_available), {
        tone: Number(metrics.drivers_available) < Number(metrics.drivers_used)
          ? 'danger' : 'success' }),
      kpi('موصى به', fmt.num(metrics.drivers_required)),
      kpi('رحلات بلا سائق', fmt.num(metrics.unassigned_route_count),
        { tone: metrics.unassigned_route_count ? 'warning' : '' }),
      kpi('غير قابلة للتخطيط', fmt.num(metrics.unplannable_count),
        { tone: metrics.unplannable_count ? 'danger' : 'success' }),
      kpi('التحذيرات', fmt.num(metrics.warning_count),
        { tone: warningsBySeverity.CRITICAL ? 'danger' : '' }),
      kpi('إجمالي المسافة', fmt.km(metrics.total_distance_km)),
      kpi('زمن القيادة', fmt.minutes(metrics.total_drive_minutes)),
      kpi('زمن الخدمة', fmt.minutes(metrics.total_service_minutes)),
      kpi('زمن الانتظار', fmt.minutes(metrics.total_wait_minutes),
        { tone: Number(metrics.total_wait_minutes) > 240 ? 'warning' : '' }),
      kpi('التكلفة التقديرية', fmt.money(metrics.estimated_cost)),
      kpi('رحلات بعيدة', fmt.num(metrics.long_haul_route_count)),
      kpi('زمن تشغيل الخوارزمية', `${fmt.num(plan.solve_ms)} م.ث`),
      kpi('مؤشر عدالة التوزيع', fmt.num(metrics.fairness_index, 1), {
        hint: 'الانحراف المعياري لأحمال العمل — كلما قل كان التوزيع أعدل' })),

    // ------------------------------------------- نسبة التحسين -------
    improvement
      ? el('div', { class: 'card mb-4' },
        el('div', { class: 'card-head' },
          el('h3', {}, 'مقابل خطة الأساس'),
          el('div', { class: 'spacer' }),
          el('span', { class: 'badge' }, improvement.baseline_label_ar)),
        table([
          { label: 'المؤشر', key: 'label' },
          { label: 'خطة الأساس', numeric: true, key: 'baseline' },
          { label: 'الخطة المحسّنة', numeric: true, key: 'optimized' },
          { label: 'التحسين', numeric: true, render: (row) => row.pct === null
            ? '—'
            : el('span', { class: `badge ${row.pct > 0 ? 'success' : row.pct < 0 ? 'danger' : ''}` },
              fmt.pct(row.pct, 1)) },
        ], [
          ['السائقون', 'drivers'], ['زمن القيادة (دقيقة)', 'drive_minutes'],
          ['المسافة (كم)', 'distance_km'], ['التكلفة', 'cost'],
        ].map(([label, key]) => ({
          label,
          baseline: fmt.num(improvement[key].baseline, 1),
          optimized: fmt.num(improvement[key].optimized, 1),
          pct: improvement[key].improvement_pct,
        }))))
      : el('div', { class: 'alert-box' },
        el('strong', {}, 'لا تُعرض نسبة تحسين'),
        'لم تُحسب خطة أساس لهذه الخطة، ولا يجوز عرض نسبة تحسين بلا مرجع تُقاس عليه.'),

    // -------------------------------------------- تقدير السائقين ----
    estimations.length ? el('div', { class: 'card mb-4' },
      el('div', { class: 'card-head' }, el('h3', {}, 'تقدير السائقين وتبريره')),
      el('div', { class: 'stack' }, estimations.map((estimate) =>
        el('div', {},
          el('div', { class: 'row' },
            el('strong', {}, estimate.hub_name_ar),
            el('span', { class: 'badge' }, fmt.date(estimate.service_date)),
            el('span', { class: 'badge brand' },
              `الحد الأدنى النظري ${fmt.num(estimate.theoretical_minimum)}`),
            el('span', { class: 'badge info' }, `المستخدم ${fmt.num(estimate.used)}`),
            el('span', { class: 'badge success' }, `الموصى به ${fmt.num(estimate.recommended)}`),
            el('span', { class: 'badge' }, `المتوفر ${fmt.num(estimate.available)}`),
            el('span', { class: `badge ${Number(estimate.gap) < 0 ? 'danger' : 'success'}` },
              Number(estimate.gap) < 0
                ? `عجز ${fmt.num(Math.abs(estimate.gap))}` : `فائض ${fmt.num(estimate.gap)}`)),
          el('ul', { class: 'small muted', style: { margin: '8px 0 0', paddingInlineStart: '18px' } },
            (estimate.justification || []).map((item) =>
              el('li', {}, el('b', {}, `+${item.drivers} `), item.label_ar, ' — ', item.detail_ar))),
          estimate.sla_impact?.scenarios?.length
            ? el('div', { class: 'small mt-4' },
              el('b', {}, 'أثر تقليل السائقين على SLA: '),
              (estimate.sla_impact.scenarios || []).map((s) => s.detail_ar).join(' · '))
            : null))))
      : null,

    // ---------------------------------------------- التحذيرات -------
    el('div', { class: 'card mb-4' },
      el('div', { class: 'card-head' },
        el('h3', {}, `التحذيرات (${warnings.length})`),
        el('div', { class: 'spacer' }),
        Object.entries(warningsBySeverity).map(([severity, count]) =>
          el('span', { class: 'badge', style: { marginInlineStart: '4px' } },
            severityBadge(severity), ' ', String(count)))),
      warnings.length
        ? el('div', { class: 'stack-2' }, warnings.map((warning) =>
          el('button', {
            class: `warning-card sev-${warning.severity}`,
            onClick: () => {
              if (warning.route_id) router.go(`/routes/${warning.route_id}`);
              else if (warning.shipment_id) router.go(`/shipments/${warning.shipment_id}`);
            },
          },
            el('div', { class: 'wc-head' },
              el('span', { class: 'wc-title' },
                WARNING_LABEL[warning.warning_type] || warning.warning_type),
              severityBadge(warning.severity),
              warning.route_reference
                ? el('span', { class: 'badge brand' }, warning.route_reference) : null,
              warning.shipment_reference
                ? el('span', { class: 'badge info' }, warning.shipment_reference) : null),
            el('div', { class: 'wc-line' }, el('b', {}, 'السبب: '), warning.reason_ar),
            el('div', { class: 'wc-line' }, el('b', {}, 'الجهة المتأثرة: '),
              warning.affected_entity_ar),
            el('div', { class: 'wc-line' }, el('b', {}, 'الإجراء المقترح: '),
              warning.suggested_action_ar),
            el('div', { class: 'wc-line tiny muted' }, fmt.dateTime(warning.occurred_at)))))
        : el('div', { class: 'empty' }, 'لا توجد تحذيرات')),

    // ------------------------------------- الشحنات غير القابلة للتخطيط
    unplannable.length ? el('div', { class: 'card mb-4' },
      el('div', { class: 'card-head' },
        el('h3', {}, `شحنات غير قابلة للتخطيط (${unplannable.length})`)),
      table([
        { label: 'الشحنة', key: 'reference' },
        { label: 'من', key: 'pickup_name', wrap: true },
        { label: 'إلى', key: 'dropoff_name', wrap: true },
        { label: 'النافذة', render: (row) =>
          `${fmt.time(row.pickup_window_from)} — ${fmt.time(row.pickup_window_to)}` },
        { label: 'SLA', render: (row) => fmt.time(row.sla_deadline) },
        { label: 'السبب', render: (row) =>
          el('span', { class: 'badge danger' }, row.unplannable_reason) },
        { label: 'التفصيل', key: 'unplannable_detail', wrap: true },
      ], unplannable, { onRowClick: (row) => router.go(`/shipments/${row.id}`) }))
      : null,

    // ------------------------------------------------- أيام النشر ----
    el('div', { class: 'card mb-4' },
      el('div', { class: 'card-head' }, el('h3', {}, 'أيام الخطة والنشر')),
      table([
        { label: 'اليوم', render: (row) => fmt.date(row.service_date) },
        { label: 'مركز الانطلاق', key: 'hub_name_ar' },
        { label: 'الرحلات', numeric: true, render: (row) => fmt.num(row.metrics?.route_count) },
        { label: 'النشر', render: (row) => row.is_published
          ? el('span', { class: 'badge success' }, 'منشور')
          : el('span', { class: 'badge warning' }, 'غير منشور') },
        { label: 'نُشر في', render: (row) => fmt.dateTime(row.published_at) },
        { label: 'بواسطة', key: 'published_by_name' },
      ], days)),

    // --------------------------------------------------- الرحلات ----
    el('div', { class: 'card' },
      el('div', { class: 'card-head' }, el('h3', {}, `الرحلات (${routes.length})`)),
      table([
        { label: 'المرجع', key: 'reference' },
        { label: 'اليوم', render: (row) => fmt.date(row.service_date) },
        { label: 'المركز', key: 'hub_name_ar' },
        { label: 'السائق', render: (row) => row.driver_name
          || el('span', { class: 'badge warning' }, 'غير مُسند') },
        { label: 'الحالة', render: (row) => statusBadge(row.status, ROUTE_STATUS_LABEL) },
        { label: 'البداية', render: (row) => fmt.time(row.planned_start_at) },
        { label: 'آخر تسليم', render: (row) => fmt.time(row.planned_end_at) },
        { label: 'مدة العمل', numeric: true, render: (row) => fmt.minutes(row.working_minutes) },
        { label: 'المسافة', numeric: true, render: (row) => fmt.km(row.distance_km) },
        { label: 'الشحنات', numeric: true, render: (row) => fmt.num(row.shipment_count) },
        { label: 'التقاط', numeric: true, render: (row) => fmt.num(row.pickup_count) },
        { label: 'تسليم', numeric: true, render: (row) => fmt.num(row.delivery_count) },
        { label: 'بعيدة', render: (row) => row.is_long_haul
          ? el('span', { class: 'badge warning' }, `${fmt.km(row.max_hub_distance_km)}`) : '—' },
      ], routes, { onRowClick: (row) => router.go(`/routes/${row.id}`) })));

  return host;
}

/* ================================================= تفاصيل الرحلة ======= */

export async function routeDetailView({ params, router }) {
  const response = await api.get(`/api/routes/${params.id}`);
  const { route, stops, shipments, warnings, feasibility_violations: violations } = response.data;

  const host = el('div', {});
  const mapHost = el('div', {});
  const stopsHost = el('div', { class: 'stop-list' });

  const points = stops.map((stop) => ({
    id: stop.id, lat: stop.lat, lon: stop.lon,
    kind: stop.kind, seq: stop.sequence,
    title: stop.label_ar,
  }));

  let mapInstance = null;
  let selected = null;

  const renderStops = () => {
    mount(stopsHost, stops.map((stop) => {
      const late = stop.window_to && stop.planned_service_start
        && new Date(stop.planned_service_start) > new Date(stop.window_to);
      return el('button', {
        class: `stop-row kind-${stop.kind} ${stop.status === 'DONE' ? 'done' : ''} `
          + `${selected === stop.id ? 'selected' : ''}`,
        onClick: () => {
          selected = stop.id;
          mapInstance?.select(stop.id);
          if (Number.isFinite(Number(stop.lat))) {
            mapInstance.center = { lat: Number(stop.lat), lon: Number(stop.lon) };
            mapInstance.draw();
          }
          renderStops();
        },
      },
        el('span', { class: 'stop-seq' },
          stop.kind === 'HUB_START' ? '⌂' : String(stop.sequence)),
        el('span', { class: 'stop-main' },
          el('span', { class: 'stop-title' }, stop.label_ar),
          el('span', { class: 'stop-sub' },
            stop.shipment_reference ? `${stop.shipment_reference} · ` : '',
            stop.facility_type ? `${LABELS.facilityType[stop.facility_type] || ''} · ` : '',
            stop.leg_distance_km ? `${fmt.km(stop.leg_distance_km)} / `
              + `${fmt.minutes(stop.leg_minutes)}` : 'نقطة البداية',
            stop.wait_minutes > 0 ? ` · انتظار ${fmt.minutes(stop.wait_minutes)}` : '',
            stop.leg_is_estimated ? ' · زمن تقديري' : '')),
        el('span', { class: 'stop-time' },
          el('span', { class: late ? 'late' : '' }, fmt.time(stop.planned_arrival_at)),
          stop.window_from ? el('div', { class: 'tiny muted' },
            `${fmt.time(stop.window_from)}–${fmt.time(stop.window_to)}`) : null,
          stop.actual_completed_at ? el('div', { class: 'tiny' },
            el('span', { class: 'badge success' }, `فعلي ${fmt.time(stop.actual_completed_at)}`))
            : null));
    }));
  };

  mount(host,
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, `الرحلة ${route.reference}`),
        el('p', { class: 'subtitle' },
          statusBadge(route.status, ROUTE_STATUS_LABEL),
          ` · ${route.hub_name_ar} · ${fmt.date(route.service_date)}`,
          route.driver_name ? ` · ${route.driver_name}` : ' · بلا سائق',
          route.plate_number ? ` · ${route.plate_number}` : '',
          route.box_code ? ` · صندوق ${route.box_code}` : '')),
      el('div', { class: 'page-actions' },
        el('button', { class: 'btn ghost', onClick: () => history.back() }, 'رجوع'),
        can('tracking.read') && ['IN_PROGRESS', 'COMPLETED'].includes(route.status)
          ? el('button', { class: 'btn', onClick: () => showTrack(route.id) },
            '◎ المسار المنفَّذ') : null,
        can('routes.assign') && ['PLANNED', 'ASSIGNED'].includes(route.status)
          ? el('button', { class: 'btn primary', onClick: () => router.go('/assign') },
            'إسناد سائق') : null)),

    violations.length ? el('div', { class: 'alert-box danger' },
      el('strong', {}, 'هذه الرحلة تخرق قيودًا صلبة ولا يمكن نشرها'),
      el('ul', { style: { margin: '4px 0 0', paddingInlineStart: '18px' } },
        violations.map((v) => el('li', {}, `${v.rule_code}: ${v.detail_ar}`)))) : null,

    el('div', { class: 'kpi-grid mb-4' },
      kpi('البداية المخططة', fmt.time(route.planned_start_at)),
      kpi('آخر تسليم', fmt.time(route.planned_end_at)),
      kpi('مدة العمل', fmt.minutes(route.working_minutes), {
        tone: Number(route.working_minutes) > 552 ? 'warning' : '' }),
      kpi('المسافة', fmt.km(route.distance_km)),
      kpi('زمن القيادة', fmt.minutes(route.drive_minutes)),
      kpi('زمن الخدمة', fmt.minutes(route.service_minutes)),
      kpi('الانتظار', fmt.minutes(route.wait_minutes)),
      kpi('الشحنات', fmt.num(route.shipment_count), {
        hint: `${route.pickup_count} التقاط · ${route.delivery_count} تسليم` }),
      kpi('رحلة بعيدة', route.is_long_haul ? 'نعم' : 'لا', {
        tone: route.is_long_haul ? 'warning' : '',
        hint: `أقصى مسافة من المركز ${fmt.km(route.max_hub_distance_km)}` }),
      kpi('موقع نهاية الرحلة', route.end_lat
        ? `${Number(route.end_lat).toFixed(3)}, ${Number(route.end_lon).toFixed(3)}` : '—', {
        hint: 'تبدأ منه الرحلة التالية للسائق إن وُجدت' })),

    el('div', { class: 'grid split' },
      el('div', { class: 'card' },
        el('div', { class: 'card-head' }, el('h3', {}, 'المسار على الخريطة')),
        mapHost),
      el('div', { class: 'card' },
        el('div', { class: 'card-head' }, el('h3', {}, 'تسلسل المحطات')),
        stopsHost)),

    warnings.length ? el('div', { class: 'card mt-4' },
      el('div', { class: 'card-head' }, el('h3', {}, 'تحذيرات هذه الرحلة')),
      el('div', { class: 'stack-2' }, warnings.map((warning) =>
        el('div', { class: `warning-card sev-${warning.severity}` },
          el('div', { class: 'wc-head' },
            el('span', { class: 'wc-title' },
              WARNING_LABEL[warning.warning_type] || warning.warning_type),
            severityBadge(warning.severity)),
          el('div', { class: 'wc-line' }, warning.reason_ar),
          el('div', { class: 'wc-line' }, el('b', {}, 'الإجراء: '),
            warning.suggested_action_ar))))) : null,

    el('div', { class: 'card mt-4' },
      el('div', { class: 'card-head' }, el('h3', {}, 'شحنات الرحلة')),
      table([
        { label: 'الشحنة', key: 'reference' },
        { label: 'من', key: 'pickup_name', wrap: true },
        { label: 'إلى', key: 'dropoff_name', wrap: true },
        { label: 'الحالة', render: (row) => statusBadge(row.status) },
        { label: 'القطع', numeric: true, render: (row) => fmt.num(row.piece_count) },
        { label: 'الحرارة', render: (row) => LABELS.temperature[row.temperature_mode] },
        { label: 'الالتقاط المخطط', render: (row) => fmt.time(row.planned_pickup_at) },
        { label: 'الالتقاط الفعلي', render: (row) => fmt.time(row.actual_pickup_at) },
        { label: 'SLA', render: (row) => fmt.time(row.sla_deadline) },
        { label: 'التسليم الفعلي', render: (row) => row.actual_dropoff_at
          ? el('span', { class: row.sla_breached ? 'badge danger' : 'badge success' },
            fmt.time(row.actual_dropoff_at)) : '—' },
      ], shipments, { onRowClick: (row) => router.go(`/shipments/${row.id}`) })));

  mapInstance = new MasarMap(mapHost, {
    tileUrl: state.meta.config.tile_url,
    attribution: state.meta.config.tile_attribution,
    height: 460,
  });
  mapInstance.setRoutes([{ points, color: MAP_COLORS.planned, width: 3 }]);
  mapInstance.setLegend([
    { color: MAP_COLORS.hub, label: 'مركز الانطلاق' },
    { color: MAP_COLORS.pickup, label: 'التقاط' },
    { color: MAP_COLORS.delivery, label: 'تسليم' },
  ]);
  mapInstance.fit(points);
  mapInstance.onMarkerClick = (marker) => {
    selected = marker.id;
    mapInstance.select(marker.id);
    renderStops();
    document.querySelector('.stop-row.selected')?.scrollIntoView(
      { block: 'nearest', behavior: 'smooth' });
  };
  renderStops();

  async function showTrack(routeId) {
    try {
      const track = await api.get(`/api/tracking/routes/${routeId}`);
      const data = track.data;
      mapInstance.setTracks([{ points: data.actual_track, color: MAP_COLORS.actual }]);
      mapInstance.setLegend([
        { color: MAP_COLORS.planned, label: 'المسار المخطط' },
        { color: MAP_COLORS.actual, label: 'المسار المنفَّذ' },
      ]);
      toast(`${data.point_count} نقطة تتبع · أقصى انحراف ${fmt.km(data.max_deviation_km)}`,
        { tone: 'success' });
    } catch (error) { toastError(error); }
  }

  return host;
}
