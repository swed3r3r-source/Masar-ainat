/* شاشات الإدارة: البيانات الرئيسية، الإعدادات، المستخدمون، سجل التدقيق. */

import {
  api, el, state, can, fmt, table, field, select, toast, toastError, modal,
  confirmDialog, mount, clear, LABELS,
} from '../core.js';

const ENTITY_TITLES = {
  regions: 'المناطق', cities: 'المدن والمحافظات', hubs: 'مراكز الانطلاق',
  facilities: 'الجهات الصحية', drivers: 'السائقون', vehicles: 'المركبات',
  boxes: 'الصناديق', temperature_ranges: 'نطاقات الحرارة',
};

/* ============================================== البيانات الرئيسية ====== */

export async function masterDataView({ params, router }) {
  const entity = params.entity;
  const [schemaRes, listRes] = await Promise.all([
    api.get(`/api/md/${entity}/schema`),
    api.get(`/api/md/${entity}`, { query: { limit: 500 } }),
  ]);
  const schema = schemaRes.data;
  const host = el('div', {});
  const tableHost = el('div', {});
  let search = '';
  let includeInactive = false;

  const referenceCache = new Map();
  async function referenceOptions(fieldName) {
    const target = { region_id: 'regions', city_id: 'cities', hub_id: 'hubs',
      default_hub_id: 'hubs', user_id: null }[fieldName];
    if (!target) return null;
    if (!referenceCache.has(target)) {
      const rows = (await api.get(`/api/md/${target}`, { query: { limit: 500 } })).data;
      referenceCache.set(target, rows.map((row) => ({
        value: row.id, label: `${row.name_ar || row.code}` })));
    }
    return referenceCache.get(target);
  }

  async function load() {
    mount(tableHost, el('div', { class: 'loading-block' }, el('span', { class: 'spinner' })));
    try {
      const response = await api.get(`/api/md/${entity}`, {
        query: { limit: 500, search, include_inactive: includeInactive ? 'true' : '' },
      });
      const rows = response.data || [];
      const columns = schema.fields
        .filter((f) => !['working_hours', 'is_test_data'].includes(f.name))
        .slice(0, 9)
        .map((f) => ({
          label: f.label_ar,
          key: f.name,
          numeric: ['int', 'float'].includes(f.kind),
          render: (row) => renderCell(row, f),
        }));
      columns.push({
        label: '', render: (row) => can(writePermission(entity))
          ? el('div', { class: 'btn-row' },
            el('button', {
              class: 'btn sm',
              onClick: (event) => { event.stopPropagation(); openForm(row); },
            }, 'تعديل'),
            el('button', {
              class: 'btn sm ghost',
              onClick: async (event) => {
                event.stopPropagation();
                const reason = await confirmDialog({
                  title: 'إبطال السجل',
                  message: 'لا يُحذف السجل نهائيًا — يُعطَّل ويبقى في سجل التدقيق.',
                  confirmLabel: 'إبطال', tone: 'danger', requireReason: true,
                });
                if (!reason) return;
                try {
                  await api.post(`/api/md/${entity}/${row.id}/void`, { reason });
                  toast('أُبطل السجل', { tone: 'warning' });
                  load();
                } catch (error) { toastError(error); }
              },
            }, 'إبطال')) : null,
      });
      mount(tableHost, el('div', {},
        el('p', { class: 'muted small' }, `${fmt.num(rows.length)} سجل`),
        table(columns, rows, { empty: 'لا توجد سجلات' })));
    } catch (error) { toastError(error); }
  }

  function renderCell(row, fieldSpec) {
    const value = row[fieldSpec.name];
    if (value === null || value === undefined || value === '') return '—';
    if (fieldSpec.kind === 'bool') {
      return el('span', { class: `badge ${value ? 'success' : ''}` }, value ? 'نعم' : 'لا');
    }
    if (fieldSpec.name === 'facility_type') return LABELS.facilityType[value] || value;
    if (fieldSpec.name === 'temperature_mode') return LABELS.temperature[value] || value;
    if (fieldSpec.kind === 'uuid') {
      for (const cached of referenceCache.values()) {
        const found = cached.find((option) => option.value === value);
        if (found) return found.label;
      }
      return el('span', { class: 'mono tiny' }, String(value).slice(0, 8));
    }
    if (fieldSpec.kind === 'float') return fmt.num(value, 4);
    if (fieldSpec.kind === 'text[]') return (value || []).join('، ') || '—';
    return String(value);
  }

  async function openForm(existing) {
    const inputs = new Map();
    const rows = [];
    for (const fieldSpec of schema.fields) {
      if (fieldSpec.name === 'is_test_data') continue;
      const disabled = existing && !fieldSpec.updatable;
      let control;
      const options = await referenceOptions(fieldSpec.name);
      if (options) {
        control = select([{ value: '', label: '— اختر —' }, ...options],
          { value: existing?.[fieldSpec.name] });
      } else if (fieldSpec.choices) {
        control = select(
          fieldSpec.choices.map((choice) => ({
            value: choice,
            label: LABELS.facilityType[choice] || LABELS.temperature[choice] || choice,
          })), { value: existing?.[fieldSpec.name] });
      } else if (fieldSpec.kind === 'bool') {
        control = el('input', { type: 'checkbox',
          checked: existing ? !!existing[fieldSpec.name] : true });
      } else if (fieldSpec.kind === 'json') {
        control = el('textarea', { rows: 3,
          value: existing?.[fieldSpec.name]
            ? JSON.stringify(existing[fieldSpec.name], null, 1) : '' });
      } else if (fieldSpec.kind === 'text[]') {
        control = el('input', { type: 'text',
          value: (existing?.[fieldSpec.name] || []).join('، ') });
      } else {
        const type = { int: 'number', float: 'number', date: 'date', time: 'time' }[
          fieldSpec.kind] || 'text';
        control = el('input', { type, step: fieldSpec.kind === 'float' ? 'any' : null,
          value: existing?.[fieldSpec.name] ?? '' });
      }
      if (disabled) control.disabled = true;
      inputs.set(fieldSpec.name, { control, spec: fieldSpec });
      rows.push(field(fieldSpec.label_ar, control, { required: fieldSpec.required }));
    }

    modal({
      title: existing ? `تعديل — ${ENTITY_TITLES[entity]}` : `إضافة — ${ENTITY_TITLES[entity]}`,
      wide: true,
      body: el('div', { class: 'grid cols-2' }, rows),
      actions: (close) => [
        el('button', {
          class: 'btn primary',
          onClick: async () => {
            const payload = {};
            for (const [name, { control, spec }] of inputs) {
              if (control.disabled) continue;
              let value = spec.kind === 'bool' ? control.checked : control.value;
              if (value === '' && !spec.required) continue;
              if (spec.kind === 'json' && typeof value === 'string' && value.trim()) {
                try { value = JSON.parse(value); }
                catch { toast(`قيمة «${spec.label_ar}» ليست JSON صالحًا`, { tone: 'error' }); return; }
              }
              payload[name] = value;
            }
            try {
              if (existing) await api.patch(`/api/md/${entity}/${existing.id}`, payload);
              else await api.post(`/api/md/${entity}`, payload);
              toast(existing ? 'حُدّث السجل' : 'أُضيف السجل', { tone: 'success' });
              close(); load();
            } catch (error) { toastError(error); }
          },
        }, existing ? 'حفظ' : 'إضافة'),
        el('button', { class: 'btn ghost', onClick: close }, 'إلغاء'),
      ],
    });
  }

  mount(host,
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' }, el('h1', {}, ENTITY_TITLES[entity] || schema.label_ar)),
      el('div', { class: 'page-actions' },
        can(writePermission(entity)) ? el('button', {
          class: 'btn primary', onClick: () => openForm(null),
        }, '+ إضافة') : null)),
    el('div', { class: 'card' },
      el('div', { class: 'filters' },
        field('بحث', el('input', {
          type: 'search', placeholder: 'الاسم أو الرمز',
          onInput: (event) => { search = event.target.value; },
          onKeyDown: (event) => { if (event.key === 'Enter') load(); },
        })),
        el('label', { class: 'small muted' },
          el('input', { type: 'checkbox',
            onChange: (event) => { includeInactive = event.target.checked; load(); } }),
          ' إظهار غير المفعّل'),
        el('button', { class: 'btn', onClick: load }, '↻ تحديث')),
      tableHost));

  load();
  return host;
}

function writePermission(entity) {
  return {
    regions: 'geo.write', cities: 'geo.write', hubs: 'hubs.write',
    facilities: 'facilities.write', drivers: 'drivers.write',
    vehicles: 'vehicles.write', boxes: 'vehicles.write',
    temperature_ranges: 'settings.write',
  }[entity] || 'settings.write';
}

/* ==================================================== الإعدادات ======== */

export async function settingsView() {
  const host = el('div', {});
  const scope = { scope_type: 'KINGDOM', scope_id: '', hub_id: '' };
  const bodyHost = el('div', {});

  async function load() {
    mount(bodyHost, el('div', { class: 'loading-block' }, el('span', { class: 'spinner' })));
    try {
      const response = await api.get('/api/settings',
        { query: { hub_id: scope.hub_id || '' } });
      const { effective, overrides } = response.data;
      const groups = new Map();
      for (const item of effective) {
        if (!groups.has(item.group_ar)) groups.set(item.group_ar, []);
        groups.get(item.group_ar).push(item);
      }

      mount(bodyHost,
        el('div', { class: 'alert-box' },
          el('strong', {}, 'كيف تُحلّ القيم'),
          'الأخص يفوز: المملكة ← المنطقة ← المدينة ← مركز الانطلاق. '
          + 'يُعرض مع كل قيمة مصدرها الفعلي، فلا تبقى أي قيمة تشغيلية مجهولة المصدر.'),

        [...groups.entries()].map(([group, items]) => el('div', { class: 'card mb-4' },
          el('div', { class: 'card-head' }, el('h3', {}, group)),
          table([
            { label: 'الإعداد', render: (row) => el('div', {},
              el('div', {}, row.name_ar),
              row.description_ar
                ? el('div', { class: 'tiny muted' }, row.description_ar) : null), wrap: true },
            { label: 'القيمة الفعالة', numeric: true, render: (row) =>
              el('strong', {}, formatValue(row.value), row.unit_ar ? ` ${row.unit_ar}` : '') },
            { label: 'المصدر', render: (row) => el('span', {
              class: `badge ${row.source.startsWith('افتراضي') ? '' : 'brand'}` }, row.source) },
            { label: 'الافتراضي', numeric: true, render: (row) => formatValue(row.default) },
            { label: '', render: (row) => can('settings.write') ? el('button', {
              class: 'btn sm',
              onClick: () => openOverrideDialog(row, load),
            }, 'تجاوز') : null },
          ], items))),

        el('div', { class: 'card' },
          el('div', { class: 'card-head' }, el('h3', {}, 'التجاوزات المسجلة')),
          table([
            { label: 'الإعداد', key: 'setting_key' },
            { label: 'النطاق', render: (row) => `${row.scope_type}` },
            { label: 'القيمة', render: (row) => formatValue(row.value) },
            { label: 'السبب', key: 'reason', wrap: true },
            { label: 'عدّلها', key: 'updated_by_name' },
            { label: 'التاريخ', render: (row) => fmt.dateTime(row.updated_at) },
            { label: '', render: (row) => can('settings.write') ? el('button', {
              class: 'btn sm ghost',
              onClick: async () => {
                const reason = await confirmDialog({
                  title: 'حذف التجاوز',
                  message: 'ستعود القيمة إلى ما ترثه من النطاق الأعلى.',
                  confirmLabel: 'حذف', tone: 'danger', requireReason: true,
                });
                if (!reason) return;
                try {
                  await api.del(`/api/settings/${row.id}`, { body: { reason } });
                  toast('حُذف التجاوز', { tone: 'warning' });
                  load();
                } catch (error) { toastError(error); }
              },
            }, 'حذف') : null },
          ], overrides, { empty: 'لا توجد تجاوزات — كل القيم افتراضية على مستوى المملكة' })));
    } catch (error) { toastError(error); }
  }

  function formatValue(value) {
    if (typeof value === 'boolean') return value ? 'مفعّل' : 'معطّل';
    if (Array.isArray(value)) return value.join('، ') || '—';
    return String(value);
  }

  function openOverrideDialog(setting, onDone) {
    const scopeSelect = select([
      { value: 'KINGDOM', label: 'المملكة (الافتراضي العام)' },
      { value: 'REGION', label: 'منطقة' },
      { value: 'CITY', label: 'مدينة/محافظة' },
      { value: 'HUB', label: 'مركز انطلاق' },
    ], { value: 'HUB' });
    const targetHost = el('div', {});
    const valueInput = setting.kind === 'bool'
      ? el('input', { type: 'checkbox', checked: !!setting.value })
      : setting.choices
        ? select(setting.choices.map((c) => ({ value: c, label: c })), { value: setting.value })
        : el('input', {
          type: ['int', 'float'].includes(setting.kind) ? 'number' : 'text',
          step: setting.kind === 'float' ? 'any' : null,
          min: setting.minimum ?? null, max: setting.maximum ?? null,
          value: Array.isArray(setting.value) ? setting.value.join(', ') : setting.value,
        });
    const reasonInput = el('textarea', { rows: 2 });

    let targetSelect = null;
    const renderTarget = async () => {
      const type = scopeSelect.value;
      if (type === 'KINGDOM') { mount(targetHost); targetSelect = null; return; }
      const entity = { REGION: 'regions', CITY: 'cities', HUB: 'hubs' }[type];
      const rows = (await api.get(`/api/md/${entity}`, { query: { limit: 500 } })).data;
      targetSelect = select(rows.map((row) => ({ value: row.id, label: row.name_ar })), {});
      mount(targetHost, field('النطاق المستهدف', targetSelect, { required: true }));
    };
    scopeSelect.addEventListener('change', renderTarget);
    renderTarget();

    modal({
      title: `تجاوز: ${setting.name_ar}`,
      body: el('div', {},
        setting.description_ar
          ? el('p', { class: 'muted small' }, setting.description_ar) : null,
        el('p', { class: 'small' },
          `المدى المسموح: ${setting.minimum ?? '—'} إلى ${setting.maximum ?? '—'}`
          + (setting.unit_ar ? ` ${setting.unit_ar}` : '')),
        field('مستوى النطاق', scopeSelect, { required: true }),
        targetHost,
        field('القيمة', valueInput, { required: true }),
        field('سبب التغيير', reasonInput, { required: true,
          help: 'يُحفظ في سجل التدقيق مع القيمة القديمة والجديدة' })),
      actions: (close) => [
        el('button', {
          class: 'btn primary',
          onClick: async () => {
            const reason = reasonInput.value.trim();
            if (reason.length < 3) { toast('اكتب سبب التغيير', { tone: 'error' }); return; }
            try {
              await api.post('/api/settings', {
                key: setting.key,
                value: setting.kind === 'bool' ? valueInput.checked : valueInput.value,
                scope_type: scopeSelect.value,
                scope_id: targetSelect?.value || null,
                reason,
              });
              toast('حُفظ التجاوز', { tone: 'success' });
              close(); onDone?.();
            } catch (error) { toastError(error); }
          },
        }, 'حفظ'),
        el('button', { class: 'btn ghost', onClick: close }, 'إلغاء'),
      ],
    });
  }

  mount(host,
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, 'الإعدادات والقيود التشغيلية'),
        el('p', { class: 'subtitle' },
          'لا قيمة تشغيلية مكتوبة داخل الكود — كل ما يلي قابل للتعديل لكل نطاق.'))),
    el('div', { class: 'card mb-4' },
      el('div', { class: 'filters' },
        field('عرض القيم الفعالة لمركز', select(
          [{ value: '', label: 'المستوى الوطني' },
            ...state.hubs.map((hub) => ({ value: hub.id, label: hub.name_ar }))],
          { value: scope.hub_id, onChange: (v) => { scope.hub_id = v; load(); } })))),
    bodyHost);

  load();
  return host;
}

/* ==================================================== المستخدمون ======= */

export async function usersView() {
  const host = el('div', {});
  const tableHost = el('div', {});

  async function load() {
    mount(tableHost, el('div', { class: 'loading-block' }, el('span', { class: 'spinner' })));
    try {
      const users = (await api.get('/api/users')).data || [];
      mount(tableHost, table([
        { label: 'الاسم', key: 'full_name' },
        { label: 'البريد', key: 'email' },
        { label: 'الدور', render: (row) => el('span', { class: 'badge brand' },
          LABELS.role[row.role] || row.role) },
        { label: 'النطاق', render: (row) => (row.scopes || []).length
          ? el('span', { class: 'badge' }, `${row.scopes.length} نطاق`) : 'وطني', wrap: true },
        { label: 'مفعّل', render: (row) => el('span',
          { class: `badge ${row.is_active ? 'success' : 'danger'}` },
          row.is_active ? 'نعم' : 'لا') },
        { label: 'كلمة مرور مؤقتة', render: (row) => row.must_change_password
          ? el('span', { class: 'badge warning' }, 'نعم') : '—' },
        { label: 'آخر دخول', render: (row) => fmt.dateTime(row.last_login_at) },
        { label: '', render: (row) => can('users.write') ? el('button', {
          class: 'btn sm', onClick: () => openUserForm(row, load) }, 'تعديل') : null },
      ], users, { empty: 'لا يوجد مستخدمون' }));
    } catch (error) { toastError(error); }
  }

  mount(host,
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, 'المستخدمون والأدوار'),
        el('p', { class: 'subtitle' },
          'الصلاحية تُطبَّق في الخادم وفي قاعدة البيانات معًا — إخفاء الأزرار وحده ليس أمانًا.')),
      el('div', { class: 'page-actions' },
        can('users.write') ? el('button', {
          class: 'btn primary', onClick: () => openUserForm(null, load) }, '+ مستخدم') : null,
        el('button', {
          class: 'btn',
          onClick: async () => {
            const matrix = (await api.get('/api/meta/permissions')).data;
            modal({
              title: 'مصفوفة الصلاحيات الكاملة',
              wide: true,
              body: table([
                { label: 'الصلاحية', key: 'name_ar', wrap: true },
                { label: 'المجموعة', key: 'group' },
                ...matrix.roles.map((role) => ({
                  label: LABELS.role[role] || role,
                  render: (row) => row[role]
                    ? el('span', { class: 'badge success' }, '✓') : '—',
                })),
              ], matrix.rows),
            });
          },
        }, '🔐 مصفوفة الصلاحيات'))),
    el('div', { class: 'card' }, tableHost));

  load();
  return host;
}

async function openUserForm(existing, onDone) {
  const nameInput = el('input', { type: 'text', value: existing?.full_name || '' });
  const emailInput = el('input', { type: 'email', value: existing?.email || '',
    disabled: !!existing });
  const phoneInput = el('input', { type: 'text', value: existing?.phone || '' });
  const passwordInput = el('input', { type: 'password', autocomplete: 'new-password' });
  const roleSelect = select(
    (state.meta.roles || []).map((role) => ({ value: role.key, label: role.label_ar })),
    { value: existing?.role || 'HUB_SUPERVISOR' });
  const activeInput = el('input', { type: 'checkbox',
    checked: existing ? existing.is_active : true });

  const scopeHost = el('div', {});
  const selectedScopes = new Set();
  const renderScopes = async () => {
    const role = roleSelect.value;
    const entity = { HUB_SUPERVISOR: 'hubs', DRIVER: 'hubs',
      EXTERNAL_REQUESTER: 'facilities', INTEGRATION: 'facilities' }[role];
    if (!entity) {
      mount(scopeHost, el('p', { class: 'muted small' },
        'هذا الدور بنطاق وطني — لا يحتاج تحديد مراكز أو جهات.'));
      selectedScopes.clear();
      return;
    }
    const scopeType = entity === 'hubs' ? 'HUB' : 'FACILITY';
    const rows = (await api.get(`/api/md/${entity}`, { query: { limit: 500 } })).data;
    mount(scopeHost, el('div', { class: 'btn-row' }, rows.map((row) => {
      const checkbox = el('input', {
        type: 'checkbox',
        onChange: (event) => {
          const key = `${scopeType}:${row.id}`;
          if (event.target.checked) selectedScopes.add(key);
          else selectedScopes.delete(key);
        },
      });
      return el('label', { class: 'btn sm' }, checkbox, ' ', row.name_ar);
    })));
  };
  roleSelect.addEventListener('change', renderScopes);
  renderScopes();

  modal({
    title: existing ? `تعديل ${existing.full_name}` : 'مستخدم جديد',
    wide: true,
    body: el('div', {},
      el('div', { class: 'grid cols-2' },
        field('الاسم الكامل', nameInput, { required: true }),
        field('البريد الإلكتروني', emailInput, { required: true }),
        field('الجوال', phoneInput),
        field('الدور', roleSelect, { required: true }),
        existing ? null : field('كلمة المرور المؤقتة', passwordInput, { required: true,
          help: '١٢ محرفًا على الأقل، تحتوي رقمًا وحرفًا ورمزًا خاصًا' }),
        existing ? el('div', { class: 'field' },
          el('label', {}, 'الحساب مفعّل'), activeInput) : null),
      el('div', { class: 'field' }, el('label', {}, 'النطاق'), scopeHost)),
    actions: (close) => [
      el('button', {
        class: 'btn primary',
        onClick: async () => {
          const scopes = [...selectedScopes].map((key) => {
            const [scope_type, scope_id] = key.split(':');
            return { scope_type, scope_id };
          });
          try {
            if (existing) {
              await api.patch(`/api/users/${existing.id}`, {
                full_name: nameInput.value.trim(), phone: phoneInput.value.trim(),
                role: roleSelect.value, is_active: activeInput.checked,
                scopes, reason: 'تعديل بيانات المستخدم من شاشة الإدارة',
              });
            } else {
              await api.post('/api/users', {
                full_name: nameInput.value.trim(), email: emailInput.value.trim(),
                phone: phoneInput.value.trim(), role: roleSelect.value,
                password: passwordInput.value, scopes,
              });
            }
            toast(existing ? 'حُدّث المستخدم' : 'أُنشئ المستخدم', { tone: 'success' });
            close(); onDone?.();
          } catch (error) { toastError(error); }
        },
      }, existing ? 'حفظ' : 'إنشاء'),
      el('button', { class: 'btn ghost', onClick: close }, 'إلغاء'),
    ],
  });
}

/* =================================================== سجل التدقيق ======= */

export async function auditView({ router }) {
  const host = el('div', {});
  const filters = { action: '', entity_type: '', date_from: '', date_to: '', limit: 200 };
  const tableHost = el('div', {});

  async function load() {
    mount(tableHost, el('div', { class: 'loading-block' }, el('span', { class: 'spinner' })));
    try {
      const response = await api.get('/api/audit', { query: filters });
      mount(tableHost, el('div', {},
        el('p', { class: 'muted small' },
          `${fmt.num(response.pagination?.total)} سجل مطابق`),
        table([
          { label: 'الوقت', render: (row) => fmt.dateTime(row.occurred_at) },
          { label: 'المستخدم', render: (row) => row.actor_name || row.actor_user_id || 'النظام' },
          { label: 'الدور', render: (row) => LABELS.role[row.actor_role] || row.actor_role },
          { label: 'العملية', render: (row) =>
            el('span', { class: 'badge brand' }, row.action) },
          { label: 'الكيان', render: (row) =>
            `${row.entity_type || '—'}${row.entity_label ? ` · ${row.entity_label}` : ''}`,
            wrap: true },
          { label: 'السبب', key: 'reason', wrap: true },
          { label: 'IP', render: (row) => el('span', { class: 'mono tiny' },
            row.ip_address || '—') },
          { label: 'التغيير', render: (row) => (row.old_value || row.new_value)
            ? el('button', {
              class: 'btn sm ghost',
              onClick: () => modal({
                title: `تفاصيل: ${row.action}`,
                body: el('div', {},
                  el('h4', {}, 'القيمة السابقة'),
                  el('pre', { class: 'mono small',
                    style: { background: 'var(--bg-sunken)', padding: '10px',
                      borderRadius: '6px', overflow: 'auto', direction: 'ltr' } },
                    JSON.stringify(row.old_value ?? null, null, 2)),
                  el('h4', {}, 'القيمة الجديدة'),
                  el('pre', { class: 'mono small',
                    style: { background: 'var(--bg-sunken)', padding: '10px',
                      borderRadius: '6px', overflow: 'auto', direction: 'ltr' } },
                    JSON.stringify(row.new_value ?? null, null, 2))),
              }),
            }, 'عرض') : '—' },
        ], response.data, { empty: 'لا توجد سجلات مطابقة' })));
    } catch (error) { toastError(error); }
  }

  mount(host,
    el('div', { class: 'page-head' },
      el('div', { class: 'titles' },
        el('h1', {}, 'سجل التدقيق'),
        el('p', { class: 'subtitle' },
          'سجل إلحاقي فقط — لا يمكن تعديله ولا حذفه، ويمنع ذلك محفّز في قاعدة البيانات نفسها.'))),
    el('div', { class: 'card mb-4' },
      el('div', { class: 'filters' },
        field('العملية', el('input', { type: 'text', placeholder: 'مثال: ROUTE_ASSIGN',
          onChange: (e) => { filters.action = e.target.value.trim().toUpperCase(); load(); } })),
        field('نوع الكيان', el('input', { type: 'text', placeholder: 'route / shipment / user',
          onChange: (e) => { filters.entity_type = e.target.value.trim(); load(); } })),
        field('من', el('input', { type: 'date',
          onChange: (e) => { filters.date_from = e.target.value; load(); } })),
        field('إلى', el('input', { type: 'date',
          onChange: (e) => { filters.date_to = e.target.value; load(); } })),
        el('button', { class: 'btn', onClick: load }, '↻ تحديث'))),
    el('div', { class: 'card' }, tableHost));

  load();
  return host;
}
