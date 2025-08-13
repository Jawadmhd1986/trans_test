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
  const msgsEl     = document.getElementById('chat-messages');

  if (chatToggle && chatBox && chatClose && sendBtn && inputEl && msgsEl) {
    chatToggle.addEventListener('click', () => chatBox.classList.toggle('open'));
    chatClose.addEventListener('click', () => chatBox.classList.remove('open'));
    sendBtn.addEventListener('click', sendMessage);
    inputEl.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });
  }

  async function sendMessage() {
    const text = inputEl.value.trim();
    if (!text) return;
    appendMessage('user', text);
    inputEl.value = '';

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
          setTimeout(typeChar, 15);
        }
      })();
    }
  }

  // ---------------- Transport UI ----------------

  const truckTypeContainer = document.getElementById('truckTypeContainer');
  const addTruckTypeBtn    = document.getElementById('add-truck-type');
  const destEl             = document.getElementById('destination');
  const tripTypeGroup      = document.getElementById('tripTypeGroup'); // contains label + .trip-options

  // Main trip toggle (top of form)
  const tripRadios = document.querySelectorAll('input[name="trip_type"]');
  tripRadios.forEach(radio => {
    radio.addEventListener('change', () => {
      document.querySelectorAll('.trip-options label').forEach(l => l.classList.remove('selected'));
      const label = radio.closest('label'); if (label) label.classList.add('selected');
      normalizeFirstRowUI();
    });
  });

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

  function createTruckRow(index /* 0-based */) {
    const row = document.createElement('div');
    row.className = 'truck-type-row';

    const allowed = truckListForCity(currentCity());
    const options = buildOptions(allowed, null);

    row.innerHTML = `
      <div class="select-wrapper">
        <label class="inline-label">Type</label>
        <select name="truck_type[]" required>${options}</select>
      </div>

      <div class="qty-wrapper">
        <label class="inline-label">QTY</label>
        <input type="number" name="truck_qty[]" min="1" value="1" required />
      </div>

      <button type="button" class="btn-remove" title="Remove Truck Type">Clear</button>
    `;

    if (index === 0) {
      // First row follows the main trip (hidden input)
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

  function normalizeFirstRowUI() {
    const cards = [...truckTypeContainer.querySelectorAll('.trip-card')];
    const firstCard = cards[0];
    if (!firstCard) return;

    // Ensure the first card has NO visible per-row trip selector and has hidden input synced
    const firstRow = firstCard.querySelector('.truck-type-row');
    if (!firstRow) return;

    // remove any visible select in first card
    const sel = firstRow.querySelector('select[name="trip_kind[]"]');
    if (sel) sel.closest('.select-wrapper')?.remove();

    // ensure hidden trip input exists and is synced
    let hidden = firstRow.querySelector('input.trip-kind-hidden[name="trip_kind[]"]');
    if (!hidden) {
      hidden = document.createElement('input');
      hidden.type = 'hidden';
      hidden.name = 'trip_kind[]';
      hidden.className = 'trip-kind-hidden';
      firstRow.appendChild(hidden);
    }
    hidden.value = getGlobalTrip();
  }

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
});
