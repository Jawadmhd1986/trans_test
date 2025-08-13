// ---- Viewport fix (unchanged) ----
window.addEventListener('load', () => {
  const vh = window.innerHeight * 0.01;
  document.documentElement.style.setProperty('--vh', `${vh}px`);
});
window.addEventListener('resize', () => {
  const vh = window.innerHeight * 0.01;
  document.documentElement.style.setProperty('--vh', `${vh}px`);
});

document.addEventListener('DOMContentLoaded', () => {
  // ---------------- Chatbot (UNCHANGED UX & API) ----------------
  const chatBox    = document.getElementById('chat-box');
  const chatToggle = document.querySelector('.chat-toggle');
  const chatClose  = document.getElementById('chat-close');
  const sendBtn    = document.getElementById('chat-send');
  const inputEl    = document.getElementById('chat-input');
  const chatWin    = document.getElementById('chatWin');
  const msgsEl     = document.getElementById('chat-messages');

  function openChat(){
    chatWin.hidden = false;
    inputEl.focus();
  }
  function closeChat(){ chatWin.hidden = true; }

  if (chatToggle) chatToggle.addEventListener('click', openChat);
  if (chatClose)  chatClose.addEventListener('click', closeChat);

  function appendMessage(sender, text, typewriter = false) {
    const wrapper = document.createElement('div');
    wrapper.className = `message ${sender}`;
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    wrapper.appendChild(bubble);
    msgsEl.appendChild(wrapper);
    msgsEl.scrollTop = msgsEl.scrollHeight;

    if (!typewriter) {
      bubble.innerHTML = text;
    } else {
      let i = 0;
      (function typeChar(){
        if (i < text.length) {
          bubble.innerHTML += text.charAt(i++);
          msgsEl.scrollTop = msgsEl.scrollHeight;
          requestAnimationFrame(typeChar);
        }
      })();
    }
  }

  async function sendMessage() {
    const text = (inputEl.value || '').trim();
    if (!text) return;
    inputEl.value = '';
    appendMessage('user', text);

    try {
      const res = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text })
      });
      const data = await res.json();
      const reply = (data && data.reply) ? data.reply : '...';
      const hasHTML = /<[^>]+>/.test(reply);
      appendMessage('bot', reply, !hasHTML);
    } catch {
      appendMessage('bot', 'Sorry, something went wrong.');
    }
  }

  if (sendBtn) sendBtn.addEventListener('click', sendMessage);
  if (inputEl) inputEl.addEventListener('keydown', e => {
    if (e.key === 'Enter') sendMessage();
  });

  // ---------------- Trip UI (existing) ----------------
  const truckTypeContainer = document.getElementById('truckTypeContainer');
  const addTruckTypeBtn    = document.getElementById('add-truck-type');
  const destEl             = document.getElementById('destination');
  const tripTypeGroup      = document.getElementById('tripTypeGroup');

  function getGlobalTrip() {
    const checked = document.querySelector('input[name="trip_type"]:checked');
    return checked ? checked.value : 'one_way';
  }

  // CICPA filtering support (arrays injected by template)
  const CICPA_CITIES = (window.CICPA_CITIES || []).map(s => (s || '').toLowerCase());
  const LOCAL_TRUCKS = window.LOCAL_TRUCKS || [];
  const CICPA_TRUCKS = window.CICPA_TRUCKS || [];
  function isCicpaCity(city){ return !!city && CICPA_CITIES.includes(String(city).toLowerCase().trim()); }
  function truckListForCity(city){ return isCicpaCity(city) ? CICPA_TRUCKS : LOCAL_TRUCKS; }

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

  // ---------- Trip Card helpers ----------
  function makeCard() {
    const card = document.createElement('div');
    card.className = 'trip-card';
    return card;
  }

  function normalizeFirstRowUI() {
    const allRows = document.querySelectorAll('.truck-row');
    allRows.forEach((row, idx) => {
      const hidden = row.querySelector('.trip-kind-hidden');
      const inline = row.querySelector('.trip-kind-inline');
      if (idx === 0) {
        // first row gets the hidden input synced to main radio
        if (inline) inline.remove();
        if (!hidden) {
          const h = document.createElement('input');
          h.type = 'hidden';
          h.name = 'trip_kind[]';
          h.className = 'trip-kind-hidden';
          row.appendChild(h);
        }
        const hEl = row.querySelector('.trip-kind-hidden');
        if (hEl) hEl.value = getGlobalTrip();
      } else {
        // other rows get an inline select
        if (hidden) hidden.remove();
        if (!inline) {
          const div = document.createElement('div');
          div.className = 'trip-kind-inline';
          div.innerHTML = `
            <label class="inline-label">Trip Type</label>
            <select name="trip_kind[]" required>
              <option value="one_way">One Way</option>
              <option value="back_load">Back Load</option>
            </select>`;
          row.appendChild(div);
        }
        const sel = row.querySelector('.trip-kind-inline select');
        if (sel) sel.value = getGlobalTrip();
      }
    });
  }

  function createTruckRow(index) {
    const row = document.createElement('div');
    row.className = 'form-row truck-row';

    // Truck type select (filtered by CICPA)
    const typeWrap = document.createElement('div');
    typeWrap.className = 'form-group';
    typeWrap.innerHTML = `
      <label>Type</label>
      <select name="truck_type[]" required>
        ${buildOptions(truckListForCity(currentCity()))}
      </select>`;
    row.appendChild(typeWrap);

    // Qty
    const qtyWrap = document.createElement('div');
    qtyWrap.className = 'form-group';
    qtyWrap.innerHTML = `
      <label>QTY</label>
      <input type="number" name="truck_qty[]" min="1" step="1" value="1" required>`;
    row.appendChild(qtyWrap);

    // Remove row button
    const rm = document.createElement('button');
    rm.type = 'button';
    rm.className = 'btn-remove';
    rm.textContent = 'Remove';
    rm.addEventListener('click', () => {
      const parentCard = row.closest('.trip-card');
      row.remove();
      // if the card is empty (no .truck-row), remove it
      if (parentCard && parentCard.querySelectorAll('.truck-row').length === 0) {
        parentCard.remove();
      }
      normalizeFirstRowUI();
    });
    row.appendChild(rm);

    // first row => hidden input that mirrors global trip
    if (index === 0) {
      const hidden = document.createElement('input');
      hidden.type  = 'hidden';
      hidden.name  = 'trip_kind[]';
      hidden.className = 'trip-kind-hidden';
      hidden.value = getGlobalTrip();
      row.appendChild(hidden);
    } else {
      // Additional rows get their own visible trip selector
      const tripBlock = document.createElement('div');
      tripBlock.className = 'select-wrapper';
      tripBlock.style.gridColumn = '1 / span 3';
      tripBlock.innerHTML = `
        <label class="inline-label">Trip Type</label>
        <select name="trip_kind[]" required>
          <option value="one_way">One Way</option>
          <option value="back_load">Back Load</option>
        </select>
      `;
      tripBlock.querySelector('select').value = getGlobalTrip();
      row.appendChild(tripBlock);
    }

    // Clear button removes the WHOLE CARD that owns this row
    row.querySelector('.btn-remove').addEventListener('click', (e) => {
      const card = e.currentTarget.closest('.trip-card');
      if (card) card.remove();
      normalizeFirstRowUI();
    });

    return row;
  }

  // Global trip radios affect first row hidden input & default for others
  document.querySelectorAll('input[name="trip_type"]').forEach(r => {
    r.addEventListener('change', normalizeFirstRowUI);
  });

  // ---------- Initialize: build FIRST card with Trip Type + first row ----------
  if (truckTypeContainer && addTruckTypeBtn) {
    // Create first card and move the existing Trip Type group into it
    const firstCard = makeCard();
    // Move the Trip Type group (label + buttons) into the first card
    if (tripTypeGroup) firstCard.appendChild(tripTypeGroup);

    // Add the first truck row
    firstCard.appendChild(createTruckRow(0));
    truckTypeContainer.appendChild(firstCard);

    // Add-row button → new card with its own row (and visible per-row Trip Type select)
    addTruckTypeBtn.addEventListener('click', () => {
      const idx = truckTypeContainer.querySelectorAll('.trip-card').length; // next index
      const card = makeCard();
      card.appendChild(createTruckRow(idx));
      truckTypeContainer.appendChild(card);
      // focus new row trip selector if available
      const tripSel = card.querySelector('select[name="trip_kind[]"]');
      if (tripSel) tripSel.focus();
    });
  }

  // Re-filter truck types when destination changes
  if (destEl) {
    destEl.addEventListener('change', () => {
      const allowed = truckListForCity(currentCity());
      document.querySelectorAll('select[name="truck_type[]"]').forEach(typeSel => {
        const cur = typeSel.value;
        typeSel.innerHTML = buildOptions(allowed, cur);
      });
    });
  }

  // Keep first row synced with main radio on load
  normalizeFirstRowUI();

  /* ---------- NEW: Add From / To (multi-route) ---------- */
  const routesWrap  = document.getElementById('routes');
  const addRouteBtn = document.getElementById('add-route');
  if (routesWrap && addRouteBtn) {
    addRouteBtn.addEventListener('click', () => {
      // Clone the first route row
      const firstRow = routesWrap.querySelector('.form-row');
      if (!firstRow) return;
      const clone = firstRow.cloneNode(true);

      // Clear selections
      clone.querySelectorAll('select').forEach(sel => {
        sel.selectedIndex = 0;
        // Ensure array-style names (backend supports origin[]/destination[])
        if (sel.name === 'origin') sel.name = 'origin[]';
        if (sel.name === 'destination') sel.name = 'destination[]';
      });

      // Remove duplicate IDs from cloned selects
      clone.querySelectorAll('#origin, #destination').forEach(el => {
        el.removeAttribute('id');
      });

      routesWrap.appendChild(clone);
      clone.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  }

});
