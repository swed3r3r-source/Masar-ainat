/* واجهة مقدم الطلب الخارجي — طلب فوري لجهته فقط، ومتابعة طلباته. */

import {
  api, el, mount, fmt, field, select, table, toast, toastError, confirmDialog,
  statusBadge, LABELS, loadLabels, state, setUnauthorizedHandler,
} from './core.js';

const root = document.getElementById('root');

function loginScreen(message = '') {
  const email = el('input', { type: 'email', autocomplete: 'username' });
  const password = el('input', { type: 'password', autocomplete: 'current-password' });
  const errorNode = el('div', { class: 'error' }, message);
  const submit = el('button', { class: 'btn primary block lg', type: 'submit' }, 'دخول');

  mount(root, el('div', { class: 'auth-shell' },
    el('form', {
      class: 'auth-card',
      onSubmit: async (event) => {
        event.preventDefault();
        submit.disabled = true;
        try {
          const response = await api.post('/api/auth/login',
            { email: email.value.trim(), password: password.value });
          state.user = response.data.user;
          await boot();
        } catch (error) {
          errorNode.textContent = error.message;
          submit.disabled = false;
        }
      },
    },
      el('div', { class: 'logo' },
        el('div', { class: 'mark' }, 'م'),
        el('div', {},
          el('div', { class: 'name' }, 'مسار عينات'),
          el('div', { class: 'tag' }, 'بوابة طلب نقل العينات'))),
      field('البريد الإلكتروني', email),
      field('كلمة المرور', password),
      errorNode, submit)));
}

async function boot() {
  const meta = await api.get('/api/meta');
  state.meta = meta.data;
  loadLabels(meta.data);
  await render();
}

async function render() {
  const [facilitiesRes, requestsRes] = await Promise.all([
    api.get('/api/md/facilities', { query: { limit: 200 } }),
    api.get('/api/ondemand', { query: { limit: 100 } }),
  ]);
  const facilities = facilitiesRes.data || [];
  const myFacility = facilities.find((f) => f.id === state.user.facility_id);
  const destinations = facilities.filter(
    (f) => ['LABORATORY', 'BLOOD_BANK'].includes(f.facility_type));

  mount(root, el('div', { class: 'app' },
    el('header', { class: 'app-header' },
      el('div', { class: 'brand' }, el('div', { class: 'mark' }, 'م'), 'مسار عينات'),
      el('div', { class: 'spacer' }),
      el('div', { class: 'who' },
        el('strong', {}, state.user.full_name),
        myFacility?.name_ar || 'جهة صحية'),
      el('button', {
        class: 'btn ghost sm', style: { color: '#cfe0da' },
        onClick: async () => {
          try { await api.post('/api/auth/logout', {}); } catch { /* تجاهل */ }
          loginScreen();
        },
      }, 'خروج')),
    el('main', { class: 'app-main', style: { maxWidth: '1000px', margin: '0 auto' } },
      el('div', { class: 'page-head' },
        el('div', { class: 'titles' },
          el('h1', {}, 'طلب نقل عينات'),
          el('p', { class: 'subtitle' },
            'الطلب يمر بمراجعة برج التحكم قبل الإسناد، ويمكنك إلغاؤه قبل الالتقاط فقط.'))),
      newRequestCard(myFacility, destinations),
      el('div', { class: 'card mt-4' },
        el('div', { class: 'card-head' }, el('h3', {}, 'طلباتي')),
        table([
          { label: 'المرجع', key: 'reference' },
          { label: 'الحالة', render: (row) => statusBadge(row.status) },
          { label: 'إلى', key: 'dropoff_name', wrap: true },
          { label: 'النافذة', render: (row) =>
            `${fmt.time(row.pickup_window_from)}–${fmt.time(row.pickup_window_to)}` },
          { label: 'الموعد النهائي', render: (row) => fmt.dateTime(row.sla_deadline) },
          { label: 'السائق', key: 'driver_name' },
          { label: 'أُنشئ', render: (row) => fmt.ago(row.created_at) },
          { label: 'السبب', render: (row) =>
            row.rejection_reason || row.cancel_reason || '—', wrap: true },
          { label: '', render: (row) => canCancel(row.status) ? el('button', {
            class: 'btn sm danger',
            onClick: async () => {
              const reason = await confirmDialog({
                title: 'إلغاء الطلب',
                message: 'يمكن الإلغاء قبل الالتقاط فقط. سيظهر الإلغاء لدى السائق تلقائيًا.',
                confirmLabel: 'إلغاء الطلب', tone: 'danger', requireReason: true,
              });
              if (!reason) return;
              try {
                await api.post(`/api/ondemand/${row.id}/cancel`, { reason });
                toast('أُلغي الطلب', { tone: 'warning' });
                render();
              } catch (error) { toastError(error); }
            },
          }, 'إلغاء') : null },
        ], requestsRes.data, { empty: 'لم تُنشئ أي طلب بعد' })))));
}

function canCancel(status) {
  return ['PENDING_APPROVAL', 'PENDING_ASSIGNMENT', 'PLANNED', 'ASSIGNED',
    'PUBLISHED', 'IN_PROGRESS', 'ARRIVED_PICKUP'].includes(status);
}

function newRequestCard(myFacility, destinations) {
  const now = new Date();
  const toLocal = (date) => new Date(date.getTime() + 3 * 3600 * 1000)
    .toISOString().slice(0, 16);

  const destinationSelect = select(
    destinations.map((f) => ({ value: f.id,
      label: `${f.name_ar} (${LABELS.facilityType[f.facility_type]})` })), {});
  const fromInput = el('input', { type: 'datetime-local',
    value: toLocal(new Date(now.getTime() + 45 * 60000)) });
  const toInput = el('input', { type: 'datetime-local',
    value: toLocal(new Date(now.getTime() + 165 * 60000)) });
  const slaInput = el('input', { type: 'datetime-local',
    value: toLocal(new Date(now.getTime() + 5 * 3600000)) });
  const piecesInput = el('input', { type: 'number', value: '1', min: '1' });
  const temperatureSelect = select(
    Object.entries(LABELS.temperature).map(([value, label]) => ({ value, label })),
    { value: 'CHILLED' });
  const serviceSelect = select([
    { value: 'URGENT', label: 'عاجل' }, { value: 'STAT', label: 'فوري حرج' },
    { value: 'ROUTINE', label: 'روتيني' },
  ], { value: 'URGENT' });
  const samplesInput = el('input', { type: 'text', placeholder: 'دم، بول، مسحة' });
  const notesInput = el('textarea', { rows: 2 });
  const contactName = el('input', { type: 'text', value: myFacility?.contact_name || '' });
  const contactPhone = el('input', { type: 'text', value: myFacility?.contact_phone || '' });

  return el('div', { class: 'card' },
    el('div', { class: 'card-head' }, el('h3', {}, 'طلب جديد')),
    myFacility ? el('div', { class: 'alert-box' },
      el('strong', {}, 'جهة الالتقاط'),
      `${myFacility.name_ar} — لا يمكنك إنشاء طلب لجهة أخرى.`) : null,
    el('div', { class: 'grid cols-2' },
      field('جهة التسليم', destinationSelect, { required: true }),
      field('نوع الخدمة', serviceSelect, { required: true }),
      field('بداية نافذة الالتقاط', fromInput, { required: true }),
      field('نهاية نافذة الالتقاط', toInput, { required: true }),
      field('الموعد النهائي للتسليم', slaInput, { required: true,
        help: 'يجب أن يكون بعد بداية نافذة الالتقاط بوقت كافٍ للتنفيذ' }),
      field('عدد القطع', piecesInput),
      field('نطاق الحرارة', temperatureSelect),
      field('أنواع العينات', samplesInput),
      field('مسؤول التسليم من جهتك', contactName),
      field('رقم التواصل', contactPhone)),
    field('ملاحظات', notesInput),
    el('button', {
      class: 'btn primary lg',
      onClick: async () => {
        try {
          await api.post('/api/ondemand', {
            pickup_facility_id: state.user.facility_id,
            dropoff_facility_id: destinationSelect.value,
            pickup_window_from: new Date(fromInput.value).toISOString(),
            pickup_window_to: new Date(toInput.value).toISOString(),
            sla_deadline: new Date(slaInput.value).toISOString(),
            service_type: serviceSelect.value,
            piece_count: Number(piecesInput.value) || 1,
            temperature_mode: temperatureSelect.value,
            sample_types: samplesInput.value.split(/[،,]/).map((s) => s.trim())
              .filter(Boolean),
            notes: notesInput.value.trim(),
            contact_name: contactName.value.trim(),
            contact_phone: contactPhone.value.trim(),
          });
          toast('أُرسل الطلب — بانتظار مراجعة برج التحكم', { tone: 'success' });
          render();
        } catch (error) { toastError(error); }
      },
    }, 'إرسال الطلب'));
}

setUnauthorizedHandler(() => {
  if (state.user) { state.user = null; loginScreen('انتهت الجلسة'); }
});

(async () => {
  const probe = await api.get('/api/auth/session');
  if (probe.data?.user) {
    state.user = probe.data.user;
    await boot();
  } else {
    loginScreen();
  }
})();
