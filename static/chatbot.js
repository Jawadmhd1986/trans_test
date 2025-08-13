/* =========================================================
   Viewport fix (unchanged)
========================================================= */
window.addEventListener('load', () => {
  const vh = window.innerHeight * 0.01;
  document.documentElement.style.setProperty('--vh', `${vh}px`);
});
window.addEventListener('resize', () => {
  const vh = window.innerHeight * 0.01;
  document.documentElement.style.setProperty('--vh', `${vh}px`);
});

/* =========================================================
   DOM READY
========================================================= */
document.addEventListener('DOMContentLoaded', () => {
  /* -------------------------------------------------------
     CHATBOT  (all original behavior preserved)
  ------------------------------------------------------- */
  const chatBox    = document.getElementById('chat-box');
  const chatToggle = document.querySelector('.chat-toggle');
  const chatClose  = document.getElementById('chat-close');
  const sendBtn    = document.getElementById('chat-send');
  const chatPanel  = document.getElementById('chat-box');
  const msgsEl     = document.getElementById('chat-messages');
  const inputEl    = document.getElementById('chat-input');

  function openChat()  { chatPanel?.classList.add('open');  }
  function closeChat() { chatPanel?.classList.remove('open'); }

  chatToggle?.addEventListener('click', openChat);
  chatClose?.addEventListener('click', closeChat);

  sendBtn?.addEventListener('click', () => {
    const v = (inputEl?.value || '').trim();
    if (!v) return;
    ask(v);
  });
  inputEl?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const v = (inputEl?.value || '').trim();
      if (!v) return;
      ask(v);
    }
  });

  function appendMessage(sender, text, typewriter = false) {
    const wrapper = document.createElement('div');
    wrapper.className = `message ${sender}`;
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    wrapper.appendChild(bubble);
    msgsEl.appendChild(wrapper);
    msgsEl.scrollTop = msgsEl.scrollHeight;

    const setHTML = (t) => { bubble.innerHTML = t; msgsEl.scrollTop = msgsEl.scrollHeight; };

    // allow HTML from backend safely (we assume trusted server content)
    if (!typewriter) {
      setHTML(text);
      return;
    }
    let i = 0;
    (function typeChar(){
      if (i < text.length) {
        setHTML((bubble.innerHTML || '') + text.charAt(i++));
        requestAnimationFrame(typeChar);
      }
    })();
  }

  async function ask(text) {
    appendMessage('me', text, false);
    inputEl.value = '';
    appendMessage('bot', '…', false);
    try {
      const res = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text })
      });
      const data = await res.json();
      const reply = (data && data.reply) ? data.reply : '...';
      const hasHTML = /<[^>]+>/.test(reply);
      // remove the last temp '…'
      const last = msgsEl.querySelector('.message.bot:last-child .bubble');
      if (last) last.parentElement.parentElement.remove();
      appendMessage('bot', reply, !hasHTML);
    } catch {
      const last = msgsEl.querySelector('.message.bot:last-child .bubble');
      if (last) last.parentElement.parentElement.remove();
      appendMessage('bot', 'Sorry, something went wrong.');
    }
  }

  /* -------------------------------------------------------
     ORIGINAL SINGLE-ROUTE UI (kept exactly as before)
     These hooks are used when your old HTML is present.
  ------------------------------------------------------- */
  const tripTypeGroup       = document.getElementById('tripTypeGroup');
  const truckTypeContainer  = document.getElementById('truckTypeContainer');
  const addTruckTypeBtn     = document.getElementById('add-truck-type');
  const originEl            = document.getElementById('origin');
  const destEl              = document.getElementById('destination');

  // Globals injected by template
  const CICPA_CITIES = (window.CICPA_CITIES || []).map(s => (s || '').toLowerCase());
  const LOCAL_TRUCKS = window.LOCAL_TRUCKS || [];
  const CICPA_TRUCKS = window.CICPA_TRUCKS || [];
  const TRUCK_TYPES  = window.TRUCK_TYPES  || [];

  function isCicpaCity(city){ return !!city && CICPA_CITIES.includes(String(city).toLowerCase().trim()); }
  function truckListForCity(city){ return isCicpaCity(city) ? CICPA_TRUCKS : LOCAL_TRUCKS; }

  function getGlobalTrip() {
    const checked = document.querySelector('input[name="trip_type"]:checked');
    return checked ? checked.value : 'one_way';
  }

  function buildOptions(list, current) {
    const opts = ['<option value="">— Select Truck Type —</option>']
      .concat(list.map(t => `<option value="${t}">${t}</option>`))
      .join('');
    const wrap = document.createElement('select');
    wrap.innerHTML = opts;
    if (current && list.includes(current)) wrap.value = current;
    return wrap.innerHTML;
  }

  function currentCity(){ return destEl ? destEl.value : ''; }

  function normalizeFirstRowUI() {
    const firstCard = truckTypeContainer?.querySelector('.trip-card');
    if (!firstCard) return;
    const firstRowTrip = firstCard.querySelector('select[name="trip_kind[]"]');
    if (firstRowTrip) {
      // hide, since first card uses main Trip Type radios
      firstRowTrip.closest('.field')?.classList.add('hidden');
    }
  }

  function makeCard() {
    const card = document.createElement('div');
    card.className = 'trip-card';

    const rows = document.createElement('div');
    rows.className = 'rows';
    card.appendChild(rows);

    const actions = document.createElement('div');
    actions.className = 'actions';
    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'clear-row';
    removeBtn.title = 'Remove';
    removeBtn.textContent = '✕';
    actions.appendChild(removeBtn);
    card.appendChild(actions);

    removeBtn.addEventListener('click', () => {
      const all = truckTypeContainer.querySelectorAll('.trip-card');
      if (all.length > 1) card.remove();
    });
    return card;
  }

  function createTruckRow(idx, cityForFilter) {
    const allowed = truckListForCity(cityForFilter || currentCity());
    const row = document.createElement('div');
    row.className = 'row three truck-row';
    row.innerHTML = `
      <div class="field">
        <label>Type</label>
        <select name="truck_type[]" class="truck-type">
          ${buildOptions(allowed, '')}
        </select>
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
      </div>
    `;
    return row;
  }

  // Initialize legacy/single route UI
  if (truckTypeContainer && addTruckTypeBtn) {
    const firstCard = makeCard();

    // Move Trip Type group inside first card (your original behavior)
    if (tripTypeGroup) firstCard.insertBefore(tripTypeGroup, firstCard.firstChild);

    // Add the first truck row
    firstCard.querySelector('.rows').appendChild(createTruckRow(0));
    truckTypeContainer.appendChild(firstCard);

    // Add new card on click
    addTruckTypeBtn.addEventListener('click', () => {
      const idx = truckTypeContainer.querySelectorAll('.trip-card').length;
      const card = makeCard();
      card.querySelector('.rows').appendChild(createTruckRow(idx));
      truckTypeContainer.appendChild(card);
      const tripSel = card.querySelector('select[name="trip_kind[]"]');
      if (tripSel) tripSel.focus();
    });

    // Rebuild options when destination changes (CICPA filter)
    destEl?.addEventListener('change', () => {
      const allowed = truckListForCity(currentCity());
      document.querySelectorAll('select[name="truck_type[]"]').forEach(sel => {
        const cur = sel.value;
        sel.innerHTML = buildOptions(allowed, cur);
      });
    });

    // Keep first row synced with main radio on load
    normalizeFirstRowUI();
  }

  /* -------------------------------------------------------
     MULTI-ROUTE (ADDITIVE)
     Activates only if the new multi-route HTML is present.
  ------------------------------------------------------- */
  const routeHolder   = document.getElementById('route-groups');
  const addRouteBtn   = document.getElementById('add-route');
  const groupsCountEl = document.getElementById('groups_count');

  // When multi-route markup exists, we initialize each route block
  if (routeHolder && groupsCountEl) {
    // Helper: build truck row HTML for a specific route index
    function truckRowHTML(idx, city) {
      const allowed = truckListForCity(city || '');
      const opts = allowed.map(t => `<option value="${t}">${t}</option>`).join('');
      return `
        <div class="row three truck-row">
          <div class="field">
            <label>Type</label>
            <select name="truck_type_${idx}[]" class="truck-type">
              ${opts}
            </select>
          </div>
          <div class="field sm">
            <label>QTY</label>
            <input type="number" name="truck_qty_${idx}[]" class="truck-qty" value="1" min="1">
          </div>
          <div class="field">
            <label>Trip</label>
            <select name="trip_kind_${idx}[]" class="trip-kind">
              <option value="">Use Main Trip</option>
              <option value="one_way">One Way</option>
              <option value="back_load">Back Load</option>
            </select>
          </div>
          <button type="button" class="clear-row" title="Remove row">✕</button>
        </div>`;
    }

    function initRoute(groupEl) {
      const idx         = parseInt(groupEl.dataset.index, 10) || 0;
      const originSel   = groupEl.querySelector(`#origin_${idx}`);
      const destSel     = groupEl.querySelector(`#destination_${idx}`);
      const addTruckBtn = groupEl.querySelector('.add-truck');
      const rowsBox     = groupEl.querySelector(`.truck-rows[data-index="${idx}"]`);

      // Destination filtering for trucks
      function refreshTruckLists() {
        const allowed = truckListForCity(destSel?.value || '');
        rowsBox.querySelectorAll('select.truck-type').forEach(sel => {
          const cur = sel.value;
          sel.innerHTML = ['<option value="">— Select Truck Type —</option>']
            .concat(allowed.map(t => `<option value="${t}">${t}</option>`))
            .join('');
          if (allowed.includes(cur)) sel.value = cur;
        });
      }
      destSel?.addEventListener('change', refreshTruckLists);

      // Add truck row in this group
      addTruckBtn?.addEventListener('click', () => {
        rowsBox.insertAdjacentHTML('beforeend', truckRowHTML(idx, destSel?.value));
      });

      // Remove row (keep at least one)
      rowsBox?.addEventListener('click', (e) => {
        if (e.target.classList.contains('clear-row')) {
          const row = e.target.closest('.truck-row');
          if (rowsBox.querySelectorAll('.truck-row').length > 1) row.remove();
        }
      });
    }

    // Initialize any existing groups in the DOM
    routeHolder.querySelectorAll('.route-group').forEach(initRoute);

    // Add route button
    addRouteBtn?.addEventListener('click', () => {
      const next = routeHolder.querySelectorAll('.route-group').length;
      const proto = routeHolder.querySelector('.route-group[data-index="0"]');
      if (!proto) return;

      // clone and reindex
      const clone = proto.cloneNode(true);
      clone.dataset.index = String(next);
      clone.querySelectorAll('[id]').forEach(el => {
        el.id = el.id.replace('_0', `_${next}`);
      });
      clone.querySelectorAll('[name]').forEach(el => {
        el.name = el.name
          .replace(/origin_\d+/, `origin_${next}`)
          .replace(/destination_\d+/, `destination_${next}`)
          .replace(/trip_type_\d+/, `trip_type_${next}`)
          .replace(/truck_type_\d+\[\]/, `truck_type_${next}[]`)
          .replace(/truck_qty_\d+\[\]/, `truck_qty_${next}[]`)
          .replace(/trip_kind_\d+\[\]/, `trip_kind_${next}[]`)
          .replace(/cargo_type_\d+/, `cargo_type_${next}`);
      });

      // clear values
      clone.querySelectorAll('input[type="number"]').forEach(i => i.value = 1);
      clone.querySelectorAll('select').forEach(s => { if (s.name.startsWith('truck_type_')) s.selectedIndex = 0; });
      // route label
      const head = clone.querySelector('.route-header span');
      if (head) head.innerHTML = `Route <b>#${next+1}</b>`;

      // ensure truck rows box has its data-index updated
      const rowsBox = clone.querySelector('.truck-rows');
      if (rowsBox) rowsBox.setAttribute('data-index', String(next));

      // update the add-truck button dataset
      const atb = clone.querySelector('.add-truck');
      if (atb) atb.setAttribute('data-index', String(next));

      routeHolder.appendChild(clone);
      groupsCountEl.value = String(next + 1);

      // init behaviors on the new group
      initRoute(clone);
      window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
    });
  }
});
