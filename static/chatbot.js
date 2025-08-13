// ---- Viewport fix ----
window.addEventListener('load', () => {
  const vh = window.innerHeight * 0.01;
  document.documentElement.style.setProperty('--vh', `${vh}px`);
});
window.addEventListener('resize', () => {
  const vh = window.innerHeight * 0.01;
  document.documentElement.style.setProperty('--vh', `${vh}px`);
});

document.addEventListener('DOMContentLoaded', () => {
  // ---------------- Chatbot (unchanged) ----------------
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
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
  }
  async function sendMessage() {
    const text = inputEl.value.trim(); if (!text) return;
    appendMessage('user', text); inputEl.value = '';
    try {
      const res = await fetch('/chat',{method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({message:text})});
      const data = await res.json(); const reply = (data && data.reply) ? data.reply : '...';
      const hasHTML = /<[^>]+>/.test(reply); appendMessage('bot', reply, !hasHTML);
    } catch { appendMessage('bot','Sorry, something went wrong.'); }
  }
  function appendMessage(sender, text, typewriter=false) {
    const wrapper = document.createElement('div'); wrapper.className = `message ${sender}`;
    const bubble = document.createElement('div'); bubble.className = 'bubble';
    wrapper.appendChild(bubble); msgsEl.appendChild(wrapper); msgsEl.scrollTop = msgsEl.scrollHeight;
    if (!typewriter) bubble.innerHTML = text;
    else { let i=0; (function typeChar(){ if(i<text.length){ bubble.innerHTML+=text.charAt(i++); msgsEl.scrollTop=msgsEl.scrollHeight; setTimeout(typeChar,15);} })(); }
  }

  // ---------------- Transport UI (multi-route) ----------------
  const routesContainer = document.getElementById('routesContainer');
  const addTruckTypeBtn = document.getElementById('add-truck-type');
  const addRouteBtn     = document.getElementById('add-route');
  const tripTypeGroup   = document.getElementById('tripTypeGroup');
  const cargoEl         = document.getElementById('cargo_type');

  const mainOriginEl      = document.getElementById('origin');
  const mainDestinationEl = document.getElementById('destination');

  // Helpers for truck options based on CICPA
  const CICPA_CITIES = (window.CICPA_CITIES || []).map(s => (s || '').toLowerCase());
  const LOCAL_TRUCKS = window.LOCAL_TRUCKS || [];
  const CICPA_TRUCKS = window.CICPA_TRUCKS || [];
  const isCicpaCity = city => !!city && CICPA_CITIES.includes(String(city).toLowerCase().trim());
  const trucksFor = city => isCicpaCity(city) ? CICPA_TRUCKS : LOCAL_TRUCKS;

  function buildTruckOptions(list, current) {
    const opts = ['<option value="">— Select Truck Type —</option>']
      .concat(list.map(t => `<option value="${t}">${t}</option>`)).join('');
    const tmp = document.createElement('select'); tmp.innerHTML = opts;
    if (current && list.includes(current)) tmp.value = current;
    return tmp.innerHTML;
  }

  // ---- DOM builders ----
  function make(el, cls){ const n = document.createElement(el); if(cls) n.className = cls; return n; }

  function createTripCard(routeCard, isFirstTrip=false){
    const tripCard = make('div','trip-card');
    const row = make('div','truck-type-row');

    // Selects
    const typeWrap = make('div','select-wrapper');
    typeWrap.innerHTML = `<label class="inline-label">Type</label>
      <select required></select>`;
    const qtyWrap = make('div','qty-wrapper');
    qtyWrap.innerHTML = `<label class="inline-label">QTY</label>
      <input type="number" min="1" value="1" required/>`;
    const clearBtn = document.createElement('button');
    clearBtn.type='button'; clearBtn.className='btn-remove'; clearBtn.textContent='Clear';

    row.append(typeWrap, qtyWrap, clearBtn);
    tripCard.appendChild(row);

    // For extra trips (inside a route), show trip type select; first/main trip uses the route trip type
    if(!isFirstTrip){
      const block = make('div','select-wrapper');
      block.style.gridColumn = '1 / span 3';
      block.innerHTML = `<label class="inline-label">Trip Type</label>
        <select class="trip-kind" required>
          <option value="one_way">One Way</option>
          <option value="back_load">Back Load</option>
        </select>`;
      tripCard.appendChild(block);
    }

    // set initial truck list according to this route's destination
    const destSel = routeCard.querySelector('.route-destination');
    const truckSel = typeWrap.querySelector('select');
    truckSel.innerHTML = buildTruckOptions(trucksFor(destSel.value), null);

    // change truck options if destination of route changes
    destSel.addEventListener('change', () => {
      const allowed = trucksFor(destSel.value);
      const cur = truckSel.value;
      truckSel.innerHTML = buildTruckOptions(allowed, cur);
    });

    // remove trip-card (not allowed to remove the first one of the first route)
    clearBtn.addEventListener('click', () => {
      const firstRoute = routesContainer.querySelector('.route-card:first-child');
      if (firstRoute && firstRoute.contains(tripCard)) {
        const firstTrip = firstRoute.querySelector('.trip-card:first-child');
        if (tripCard === firstTrip) return; // guard: cannot remove main trip
      }
      tripCard.remove();
    });

    return tripCard;
  }

  function createRouteCard(useMainHeader=false){
    const routeCard = make('div','route-card');

    // Header segment: either reuse main header (Route #1) or create local From/To + route trip type
    let originSel, destSel, routeTripContainer;

    if (useMainHeader){
      // Build a small header that references main picks (read-only labels for clarity)
      const hdr = make('div','form-row');
      const g1  = make('div','form-group');
      const g2  = make('div','form-group');
      g1.innerHTML = `<label>From</label><input class="route-origin ro" type="text" value="${mainOriginEl.value || ''}" readonly>`;
      g2.innerHTML = `<label>To</label><input class="route-destination ro" type="text" value="${mainDestinationEl.value || ''}" readonly>`;
      hdr.append(g1,g2);
      routeCard.appendChild(hdr);

      // Route trip = same radio as main (we’ll store the value, but the UI follows the big buttons above)
      routeTripContainer = make('div','form-group');
      routeTripContainer.innerHTML = `<label>Trip Type</label>
        <div class="trip-options trip-options-shadow">
          <span class="trip-pill ${document.querySelector('input[name="trip_type_main"]:checked').value==='one_way'?'selected':''}">One Way</span>
          <span class="trip-pill ${document.querySelector('input[name="trip_type_main"]:checked').value==='back_load'?'selected':''}">Back Load</span>
        </div>`;
      routeCard.appendChild(routeTripContainer);

      // hidden holders to capture as values when serializing
      originSel = make('input'); originSel.type='hidden'; originSel.className='route-origin';
      originSel.value = mainOriginEl.value || '';
      destSel   = make('input'); destSel.type='hidden'; destSel.className='route-destination';
      destSel.value   = mainDestinationEl.value || '';
      routeCard.append(originSel, destSel);
    } else {
      // Local From/To selectors for added routes
      const hdr = make('div','form-row');
      const g1  = make('div','form-group');
      const g2  = make('div','form-group');

      const originOptions = ['<option value="">— Select Pickup —</option>']
        .concat((window.PICKUP_LABELS||[]).map(v=>`<option>${v}</option>`)).join('');
      const destOptions   = ['<option value="">— Select City —</option>']
        .concat((window.DEST_LABELS||[]).map(v=>`<option>${v}</option>`)).join('');

      g1.innerHTML = `<label>From</label><select class="route-origin" required>${originOptions}</select>`;
      g2.innerHTML = `<label>To</label><select class="route-destination" required>${destOptions}</select>`;
      hdr.append(g1,g2);
      routeCard.appendChild(hdr);

      // Route-level trip type (pills)
      routeTripContainer = make('div','form-group');
      routeTripContainer.innerHTML = `<label>Trip Type</label>
        <div class="trip-options">
          <label class="selected"><input type="radio" name="route_trip_${Date.now()} " value="one_way" checked><span>One Way</span></label>
          <label><input type="radio" name="route_trip_${Date.now()} " value="back_load"><span>Back Load</span></label>
        </div>`;
      routeCard.appendChild(routeTripContainer);

      originSel = routeCard.querySelector('.route-origin');
      destSel   = routeCard.querySelector('.route-destination');
    }

    // Trip #1 in this route
    const firstTrip = createTripCard(routeCard, true);
    routeCard.appendChild(firstTrip);

    return routeCard;
  }

  // ----- Initialize Route #1 using main header selections -----
  function buildFirstRoute(){
    const r = createRouteCard(true);
    routesContainer.appendChild(r);
  }
  buildFirstRoute();

  // Main trip pill toggling affects Route #1 visual badge (not the extra routes)
  document.querySelectorAll('input[name="trip_type_main"]').forEach(r => {
    r.addEventListener('change', () => {
      document.querySelectorAll('.trip-options label').forEach(l => l.classList.remove('selected'));
      r.closest('label').classList.add('selected');
      // Update the visual pills inside first route
      const firstRoute = routesContainer.querySelector('.route-card:first-child');
      if (firstRoute){
        const pills = firstRoute.querySelectorAll('.trip-options-shadow .trip-pill');
        if (pills.length === 2){
          pills[0].classList.toggle('selected', r.value === 'one_way');
          pills[1].classList.toggle('selected', r.value === 'back_load');
        }
      }
    });
  });

  // Add truck type -> add a new trip-card to the LAST route by default
  addTruckTypeBtn.addEventListener('click', () => {
    let lastRoute = routesContainer.querySelector('.route-card:last-child');
    if (!lastRoute) { lastRoute = createRouteCard(true); routesContainer.appendChild(lastRoute); }
    lastRoute.appendChild(createTripCard(lastRoute, false));
  });

  // Add new From/To -> creates a brand new route-card
  addRouteBtn.addEventListener('click', () => {
    const newRoute = createRouteCard(false);
    routesContainer.appendChild(newRoute);
  });

  // Keep route #1 header in sync if user changes top From/To
  [mainOriginEl, mainDestinationEl].forEach(el => {
    el.addEventListener('change', () => {
      const firstRoute = routesContainer.querySelector('.route-card:first-child');
      if (!firstRoute) return;
      const oHolder = firstRoute.querySelector('input.route-origin[type="hidden"]');
      const dHolder = firstRoute.querySelector('input.route-destination[type="hidden"]');
      const oRO = firstRoute.querySelector('input.ro'); // readonly visual
      const dRO = firstRoute.querySelector('input.ro');
      if (oHolder) oHolder.value = mainOriginEl.value;
      if (dHolder) dHolder.value = mainDestinationEl.value;
      if (oRO) oRO.value = mainOriginEl.value;
      if (dRO) dRO.value = mainDestinationEl.value;
      // also refresh allowed trucks in its trip-cards
      const allowed = trucksFor(mainDestinationEl.value);
      firstRoute.querySelectorAll('.trip-card .select-wrapper select').forEach(sel=>{
        const cur = sel.value; sel.innerHTML = buildTruckOptions(allowed, cur);
      });
    });
  });

  // Serialize all routes to JSON on submit
  const form = document.getElementById('transportForm');
  form.addEventListener('submit', (e) => {
    // Validate: ensure each route has origin, destination, and at least one truck with qty>=1
    const routes = [];
    const routeCards = routesContainer.querySelectorAll('.route-card');
    routeCards.forEach((rc, idx) => {
      const originEl = rc.querySelector('.route-origin');
      const destEl   = rc.querySelector('.route-destination');
      const origin = originEl ? originEl.value : '';
      const dest   = destEl ? destEl.value   : '';

      // route trip (default = main if first route)
      let routeTrip = 'one_way';
      if (idx === 0) {
        const mainRadio = document.querySelector('input[name="trip_type_main"]:checked');
        routeTrip = mainRadio ? mainRadio.value : 'one_way';
      } else {
        const rSel = rc.querySelector('.trip-options input[type="radio"]:checked');
        routeTrip = rSel ? rSel.value : 'one_way';
      }

      // collect trip rows
      const trips = [];
      rc.querySelectorAll('.trip-card').forEach((tc, tIndex) => {
        const typeSel = tc.querySelector('.select-wrapper select');
        const qtyEl   = tc.querySelector('.qty-wrapper input[type="number"]');
        const perTripSel = tc.querySelector('.trip-kind');
        const tripKind = perTripSel ? perTripSel.value : routeTrip;

        if (typeSel && qtyEl && typeSel.value && Number(qtyEl.value) > 0){
          trips.push({ truck_type: typeSel.value, qty: Number(qtyEl.value), trip_kind: tripKind });
        }
      });

      if (origin && dest && trips.length){
        routes.push({ origin, destination: dest, route_trip: routeTrip, trips });
      }
    });

    document.getElementById('routes_json').value = JSON.stringify({
      cargo_type: cargoEl.value,
      routes
    });
    // let the form submit normally (server will parse routes_json)
  });

});
