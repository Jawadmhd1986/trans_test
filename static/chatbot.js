/* ===== 100vh mobile fix (unchanged) ===== */
window.addEventListener('load', () => {
  const vh = window.innerHeight * 0.01;
  document.documentElement.style.setProperty('--vh', `${vh}px`);
});
window.addEventListener('resize', () => {
  const vh = window.innerHeight * 0.01;
  document.documentElement.style.setProperty('--vh', `${vh}px`);
});

document.addEventListener('DOMContentLoaded', () => {
  /* =========================================================
     CHATBOT (kept same behavior & UI)
  ========================================================= */
  const chatPanel  = document.getElementById('chat-box');
  const msgsEl     = document.getElementById('chat-messages');
  const inputEl    = document.getElementById('chat-input');
  const chatToggle = document.querySelector('.chat-toggle');
  const chatClose  = document.getElementById('chat-close');
  const sendBtn    = document.getElementById('chat-send');

  function appendMessage(sender, text) {
    const wrap = document.createElement('div');
    wrap.className = `message ${sender}`;
    const b = document.createElement('div');
    b.className = 'bubble';
    b.innerHTML = text.replace(/\n/g, '<br>');
    wrap.appendChild(b);
    msgsEl.appendChild(wrap);
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }
  async function ask(text) {
    appendMessage('me', text);
    inputEl.value = '';
    const typing = document.createElement('div');
    typing.className = 'message bot';
    typing.innerHTML = '<div class="bubble">…</div>';
    msgsEl.appendChild(typing);
    msgsEl.scrollTop = msgsEl.scrollHeight;
    try {
      const r = await fetch('/chat', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ message: text })
      });
      const j = await r.json();
      typing.remove();
      appendMessage('bot', j.reply || 'OK');
    } catch {
      typing.remove();
      appendMessage('bot', 'Sorry, I could not reach the server.');
    }
  }
  chatToggle?.addEventListener('click', () => chatPanel.classList.add('open'));
  chatClose?.addEventListener('click', () => chatPanel.classList.remove('open'));
  sendBtn?.addEventListener('click', () => inputEl.value.trim() && ask(inputEl.value.trim()));
  inputEl?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && inputEl.value.trim()) ask(inputEl.value.trim());
  });

  /* =========================================================
     ORIGINAL SINGLE-ROUTE UI (unchanged)
     These IDs/classes come from your existing HTML.
  ========================================================= */
  const form               = document.querySelector('form[action*="generate_transport"]');
  const originEl           = document.getElementById('origin');
  const destEl             = document.getElementById('destination');
  const tripTypeGroup      = document.getElementById('tripTypeGroup');
  const truckTypeContainer = document.getElementById('truckTypeContainer');
  const addTruckTypeBtn    = document.getElementById('add-truck-type');

  const CICPA_CITIES = (window.CICPA_CITIES || []).map(s => (s||'').toLowerCase());
  const LOCAL_TRUCKS = window.LOCAL_TRUCKS || [];
  const CICPA_TRUCKS = window.CICPA_TRUCKS || [];
  const TRUCK_TYPES  = window.TRUCK_TYPES  || [];

  function isCicpaCity(city){ return !!city && CICPA_CITIES.includes(String(city).toLowerCase().trim()); }
  function allowedTrucksFor(city){ return isCicpaCity(city) ? CICPA_TRUCKS : LOCAL_TRUCKS; }
  function buildOptions(list, cur=''){
    return ['<option value="">— Select Truck Type —</option>']
      .concat(list.map(t => `<option value="${t}">${t}</option>`)).join('');
  }

  function makeCard() {
    const card = document.createElement('div'); card.className = 'trip-card';
    const rows = document.createElement('div'); rows.className = 'rows';
    const actions = document.createElement('div'); actions.className = 'actions';
    const x = document.createElement('button'); x.type='button'; x.className='clear-row'; x.textContent='✕';
    actions.appendChild(x);
    x.addEventListener('click', () => {
      const all = truckTypeContainer.querySelectorAll('.trip-card');
      if (all.length > 1) card.remove();
    });
    card.appendChild(rows); card.appendChild(actions);
    return card;
  }
  function createTruckRow(cityFilter) {
    const row = document.createElement('div');
    row.className = 'row three truck-row';
    row.innerHTML = `
      <div class="field">
        <label>Type</label>
        <select name="truck_type[]" class="truck-type">${buildOptions(allowedTrucksFor(cityFilter||destEl?.value))}</select>
      </div>
      <div class="field sm">
        <label>QTY</label>
        <input type="number" name="truck_qty[]" class="truck-qty" value="1" min="1">
      </div>
      <div class="field">
        <label>Trip</label>
        <select name="trip_kind[]" class="trip-kind">
          <option value="">Use Main Trip</option>
          <option value="one_way">One Way</option>
          <option value="back_load">Back Load</option>
        </select>
      </div>`;
    return row;
  }

  // Build your original first card (as before)
  if (truckTypeContainer && addTruckTypeBtn) {
    const first = makeCard();
    if (tripTypeGroup) first.insertBefore(tripTypeGroup, first.firstChild);
    first.querySelector('.rows').appendChild(createTruckRow());
    truckTypeContainer.appendChild(first);

    addTruckTypeBtn.addEventListener('click', () => {
      const card = makeCard();
      card.querySelector('.rows').appendChild(createTruckRow());
      truckTypeContainer.appendChild(card);
    });

    destEl?.addEventListener('change', () => {
      const allowed = allowedTrucksFor(destEl.value);
      document.querySelectorAll('select.truck-type').forEach(sel => {
        const cur = sel.value;
        sel.innerHTML = buildOptions(allowed, cur);
        if (allowed.includes(cur)) sel.value = cur;
      });
    });
  }

  /* =========================================================
     ADD-ON: Multi-route with NO HTML/CSS changes
     - Injects "+ Add From / To" button after your "+ Add Truck Type"
     - Keeps current visual style untouched
     - Packs extra routes into hidden fields for backend
  ========================================================= */
  if (form && addTruckTypeBtn && originEl && destEl) {
    // Hidden counter for backend
    let groups = 1;
    const groupsCount = document.createElement('input');
    groupsCount.type = 'hidden';
    groupsCount.name = 'groups_count';
    groupsCount.id   = 'groups_count';
    groupsCount.value = String(groups);
    form.appendChild(groupsCount);

    // Container that will hold extra routes (kept invisible)
    const hiddenHolder = document.createElement('div');
    hiddenHolder.style.display = 'none';
    form.appendChild(hiddenHolder);

    // Insert the "+ Add From / To" button right after "+ Add Truck Type"
    const addRouteBtn = document.createElement('button');
    addRouteBtn.type = 'button';
    addRouteBtn.id = 'add-from-to';
    addRouteBtn.className = 'btn-add';
    addRouteBtn.textContent = '+ Add From / To';
    addTruckTypeBtn.insertAdjacentElement('afterend', addRouteBtn);

    // Template for creating a hidden, indexed route block
    function makeHiddenRoute(index) {
      const wrap = document.createElement('div');
      wrap.className = 'route-hidden';
      wrap.dataset.index = String(index);

      // clone current visible selections as default values
      const curOrigin = originEl.value || '';
      const curDest   = destEl.value || '';
      const mainTrip  = (document.querySelector('input[name="trip_type"]:checked') || {}).value || 'one_way';
      const cargoSel  = document.getElementById('cargo_type');
      const cargoVal  = cargoSel ? cargoSel.value : 'general';

      wrap.innerHTML = `
        <input type="hidden" name="origin_${index}" value="${curOrigin}">
        <input type="hidden" name="destination_${index}" value="${curDest}">
        <input type="hidden" name="trip_type_${index}" value="${mainTrip}">
        <input type="hidden" name="cargo_type_${index}" value="${cargoVal}">
        <input type="hidden" name="truck_type_${index}[]" value="">
        <input type="hidden" name="truck_qty_${index}[]"  value="1">
        <input type="hidden" name="trip_kind_${index}[]"   value="">
      `;
      hiddenHolder.appendChild(wrap);
      return wrap;
    }

    // When user clicks "+ Add From / To":
    addRouteBtn.addEventListener('click', () => {
      groups += 1;
      groupsCount.value = String(groups);

      // create hidden route with current selections as a starting point
      const route = makeHiddenRoute(groups - 1);

      // Also mirror the currently visible truck rows into this hidden route
      const truckTypes = Array.from(document.querySelectorAll('select[name="truck_type[]"]'));
      const truckQtys  = Array.from(document.querySelectorAll('input[name="truck_qty[]"]'));
      const tripKinds  = Array.from(document.querySelectorAll('select[name="trip_kind[]"]'));

      // Clear placeholder first inputs created in template:
      const phTypes = route.querySelectorAll(`input[name="truck_type_${groups-1}[]"]`);
      const phQtys  = route.querySelectorAll(`input[name="truck_qty_${groups-1}[]"]`);
      const phTrips = route.querySelectorAll(`input[name="trip_kind_${groups-1}[]"]`);
      phTypes.forEach(n => n.remove());
      phQtys.forEach(n => n.remove());
      phTrips.forEach(n => n.remove());

      // Add each visible truck row to the hidden route
      truckTypes.forEach((sel, i) => {
        const t = document.createElement('input');
        t.type='hidden'; t.name=`truck_type_${groups-1}[]`; t.value=sel.value || '';
        const q = document.createElement('input');
        q.type='hidden'; q.name=`truck_qty_${groups-1}[]`; q.value=(truckQtys[i]?.value || '1');
        const k = document.createElement('input');
        k.type='hidden'; k.name=`trip_kind_${groups-1}[]`; k.value=(tripKinds[i]?.value || '');

        route.appendChild(t); route.appendChild(q); route.appendChild(k);
      });

      // Optional: brief confirmation
      addRouteBtn.disabled = true;
      setTimeout(() => { addRouteBtn.disabled = false; }, 250);
    });

    // On submit, treat the visible (main) route as index 0
    form.addEventListener('submit', () => {
      // ensure index 0 fields exist reflecting the visible UI
      // (server expects origin_0, destination_0, trip_type_0, cargo_type_0, truck_type_0[], ...)
      const ensure = (name, val) => {
        let el = form.querySelector(`input[name="${name}"]`);
        if (!el) {
          el = document.createElement('input');
          el.type = 'hidden'; el.name = name; form.appendChild(el);
        }
        el.value = val;
      };

      const mainTrip  = (document.querySelector('input[name="trip_type"]:checked') || {}).value || 'one_way';
      const cargoSel  = document.getElementById('cargo_type');

      ensure('origin_0', originEl.value || '');
      ensure('destination_0', destEl.value || '');
      ensure('trip_type_0', mainTrip);
      ensure('cargo_type_0', cargoSel ? cargoSel.value : 'general');

      // Map the visible truck rows to truck_type_0[], truck_qty_0[], trip_kind_0[]
      // Create a staging div to hold them
      const stage = form.querySelector('#_stage0') || (() => {
        const d = document.createElement('div'); d.id = '_stage0'; d.style.display='none'; form.appendChild(d); return d;
      })();
      stage.innerHTML = ''; // clear

      const truckTypes = Array.from(document.querySelectorAll('select[name="truck_type[]"]'));
      const truckQtys  = Array.from(document.querySelectorAll('input[name="truck_qty[]"]'));
      const tripKinds  = Array.from(document.querySelectorAll('select[name="trip_kind[]"]'));
      truckTypes.forEach((sel, i) => {
        const t = document.createElement('input');
        t.type='hidden'; t.name='truck_type_0[]'; t.value=sel.value || '';
        const q = document.createElement('input');
        q.type='hidden'; q.name='truck_qty_0[]'; q.value=(truckQtys[i]?.value || '1');
        const k = document.createElement('input');
        k.type='hidden'; k.name='trip_kind_0[]'; k.value=(tripKinds[i]?.value || '');
        stage.appendChild(t); stage.appendChild(q); stage.appendChild(k);
      });
    });
  }
});
