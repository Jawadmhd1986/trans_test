(() => {
  const $ = (id) => document.getElementById(id);
  const locSel = $("location");
  const whSel  = $("warehouse");
  const lineSel= $("lineCode");
  const counterIn = $("counterName");
  const startBtn = $("startBtn");
  const hint = $("targetHint");
  const tlSetupBtn = $("tlSetupBtn");

  const tlModal = $("tlModal");
  const modalTitle = $("modalTitle");
  const modalContent = $("modalContent");
  const closeModal = $("closeModal");
  const toast = $("toast");
  const toastMessage = $("toastMessage");

  const warehouses = window.warehouses;

  // State for the current job being worked on
  let jobState = {
    job_id: null,
    line_id: null,
    line_code: null,
    target_qty: 0,
    scanned_total: 0,
    status: '',
    assigned: []
  };

  let currentCounter = null; // To store the logged-in counter name
  let currentTLSession = null; // To track if a TL is logged in

  function save() {
    localStorage.setItem("loc", locSel.value);
    localStorage.setItem("wh", whSel.value);
    localStorage.setItem("line", lineSel.value);
    localStorage.setItem("counter", counterIn.value.trim());
  }

  function load() {
    locSel.value = localStorage.getItem("loc") || locSel.value;
    whSel.value  = localStorage.getItem("wh")  || whSel.value;
    counterIn.value = localStorage.getItem("counter") || "";

    // Update warehouse dropdown
    if (locSel.value) {
      updateWarehouses();
      whSel.value = localStorage.getItem("wh") || "";
    }
  }

  function updateWarehouses() {
    const location = locSel.value;
    whSel.innerHTML = '<option value="">Select Warehouse</option>';

    if (location && warehouses[location]) {
      whSel.disabled = false;
      warehouses[location].forEach(warehouse => {
        const option = document.createElement('option');
        option.value = warehouse;
        option.textContent = warehouse;
        whSel.appendChild(option);
      });
    } else {
      whSel.disabled = true;
    }
  }

  async function fetchLines() {
    hint.textContent = "Loading lines…";
    lineSel.innerHTML = `<option value="">Select Line (configured by TL)</option>`;
    startBtn.disabled = true;

    if (!locSel.value || !whSel.value) {
      hint.textContent = "Select location and warehouse first.";
      return;
    }

    const qs = new URLSearchParams({
      location: locSel.value,
      warehouse: whSel.value,
      t: Date.now()
    });
    try {
      const res = await fetch(`/api/lines?${qs.toString()}`, {
        cache: "no-store",
        credentials: "same-origin"
      });
      const d = await res.json();

      if (!res.ok || !d.ok || !d.lines?.length) {
        // Show different message based on whether TL is logged in
        if (currentTLSession) {
          hint.textContent = `No lines configured by you (${currentTLSession.displayName}). Use 'Setup Line & Assign Counters' to create lines.`;
        } else {
          hint.textContent = "No lines configured by Team Leader.";
        }
        return;
      }

      const remembered = localStorage.getItem("line") || "";

      // Filter lines based on user role and assignment
      const userData = localStorage.getItem('dsv_user');
      let availableLines = d.lines;

      if (userData) {
        try {
          const user = JSON.parse(userData);
          // For counters, only show lines they are assigned to
          if (user.role === 'counter') {
            const assignedLines = [];
            const counterName = user.name.toLowerCase().trim();
            
            console.log(`DEBUG: Filtering lines for counter: ${counterName}`);
            console.log(`DEBUG: Available lines:`, d.lines);
            
            for (const line of d.lines) {
              console.log(`DEBUG: Checking line ${line.line_code}, assigned: ${line.assigned}`);
              
              // Check if counter is assigned to this line (case-insensitive)
              const isAssigned = line.assigned.some(assignedCounter => 
                assignedCounter && assignedCounter.toLowerCase().trim() === counterName
              );
              
              console.log(`DEBUG: Counter ${counterName} assigned to line ${line.line_code}: ${isAssigned}`);
              
              if (isAssigned) {
                // Always include assigned lines - let the state check happen later
                console.log(`DEBUG: Adding line ${line.line_code} to available lines`);
                assignedLines.push(line);
              }
            }
            
            console.log(`DEBUG: Final assigned lines for ${counterName}:`, assignedLines);
            availableLines = assignedLines;
          }
        } catch (e) {
          console.error('Error filtering lines for counter:', e);
          // If user data is invalid, show all lines
        }
      }

      if (availableLines.length === 0) {
        const userData = localStorage.getItem('dsv_user');
        if (userData) {
          try {
            const user = JSON.parse(userData);
            if (user.role === 'counter') {
              hint.innerHTML = `
                <div class="text-orange-600 font-medium">All your assigned lines have been completed.</div>
                <div class="text-sm text-gray-600 mt-1">Contact your Team Leader to reset completed lines or assign new ones.</div>
                <div class="text-xs text-gray-500 mt-2">Assigned lines: ${d.lines.map(l => `Line ${l.line_code}`).join(', ')}</div>
              `;
            } else {
              hint.textContent = "No active lines available.";
            }
          } catch (e) {
            hint.textContent = "All assigned lines have been completed. Contact your Team Leader for new assignments.";
          }
        } else {
          hint.textContent = "All assigned lines have been completed. Contact your Team Leader for new assignments.";
        }
        return;
      }

      availableLines.forEach(l => {
        const opt = document.createElement("option");
        opt.value = l.line_code;
        const assignedText = l.assigned.filter(Boolean).length > 0 ?
          l.assigned.filter(Boolean).join(" & ") : "No counters assigned";
        opt.textContent = `Line ${l.line_code} • Target: ${l.target_qty} • Assigned: ${assignedText}`;
        lineSel.appendChild(opt);
      });

      // restore previous line if still valid
      if (remembered && [...lineSel.options].some(o => o.value === remembered)) {
        lineSel.value = remembered;
      }

      if (lineSel.value) {
        updateStateHint();
      } else {
        if (currentTLSession) {
          hint.textContent = `Select a line you configured as ${currentTLSession.displayName}.`;
        } else {
          hint.textContent = "Select a line to continue.";
        }
      }
    } catch (err) {
      console.error("Fetch lines error:", err);
      hint.textContent = "Error loading lines.";
    }
  }

  async function updateStateHint() {
    hint.textContent = "";
    startBtn.disabled = true;

    const line = lineSel.value;
    const counter = counterIn.value.trim();

    // Check basic form completeness first
    if (!locSel.value || !whSel.value) {
      hint.textContent = "Select location and warehouse first.";
      return;
    }

    if (!line) {
      hint.textContent = "Select a line to continue.";
      return;
    }

    if (!counter) {
      hint.textContent = "Enter your counter name.";
      return;
    }

    const qs = new URLSearchParams({
      location: locSel.value,
      warehouse: whSel.value,
      line_code: line,
      counter
    });

    try {
      const res = await fetch(`/api/job/state?${qs.toString()}`);
      const d = await res.json();

      if (!res.ok || !d.ok) {
        hint.textContent = "Line not configured by Team Leader.";
        return;
      }

      // Store job state
      jobState = {
        job_id: d.job_id,
        line_id: d.line_id,
        line_code: line,
        target_qty: d.target_qty,
        scanned_total: d.scanned_total,
        status: d.status,
        assigned: d.assigned.filter(Boolean)
      };

      const assignedCounters = jobState.assigned;
      hint.innerHTML = `Target: <b>${jobState.target_qty}</b> • Scanned: <b>${jobState.scanned_total}</b> • Assigned: ${assignedCounters.join(" & ")}`;

      if (d.is_assigned) {
        startBtn.disabled = false;
        hint.innerHTML += ` • <span class="text-green-600">✓ You can start counting!</span>`;
      } else {
        startBtn.disabled = true;
        hint.innerHTML += ` • <span class="text-red-600">✗ You are not assigned to this line.</span>`;
      }
    } catch (err) {
      console.error("State check error:", err);
      hint.textContent = "Error checking assignment.";
    }
  }

  // Event listeners
  ["input","blur"].forEach(ev => counterIn.addEventListener(ev, ()=>{ save(); updateStateHint(); }));
  locSel.addEventListener("change", ()=>{ save(); updateWarehouses(); fetchLines(); });
  whSel.addEventListener("change",  ()=>{ save(); fetchLines(); });
  lineSel.addEventListener("change",()=>{ save(); updateStateHint(); });

  $("startForm").addEventListener("submit", (e) => {
    e.preventDefault();
    if (startBtn.disabled) return;
    const u = new URL("/count", location.origin);
    u.searchParams.set("location", locSel.value);
    u.searchParams.set("warehouse", whSel.value);
    u.searchParams.set("line", lineSel.value);
    u.searchParams.set("counter", counterIn.value.trim());
    location.href = u.toString();
  });

  // TL Access Control
  let pendingTlAction = null;

  function requireTlAccess(action) {
    // Check if TL is already authenticated
    if (currentTLSession) {
      // TL is already logged in, execute action directly
      if (action) {
        action();
      }
      return;
    }

    // Show TL access modal
    pendingTlAction = action;
    const tlAccessModal = document.getElementById('tlAccessModal');
    if (tlAccessModal) {
      tlAccessModal.classList.remove('hidden');
    } else {
      // Fallback - if no modal, execute action (for setup button)
      if (action) {
        action();
      }
    }
  }

  async function verifyTlAccess() {
    const tlName = document.getElementById('tlAccessName').value;
    const tlPin = document.getElementById('tlAccessPin').value;

    if (!tlName || !tlPin) {
      showToast('Please select TL and enter PIN');
      return false;
    }

    try {
      const response = await fetch('/api/tl/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tl_name: tlName,
          tl_pin: tlPin,
          tl_display_name: tlName
        })
      });

      const result = await response.json();

      if (response.ok && result.ok) {
        // Store TL session info
        currentTLSession = {
          name: tlName,
          displayName: tlName,
          isManager: result.manager || false
        };

        const tlAccessModal = document.getElementById('tlAccessModal');
        if (tlAccessModal) {
          tlAccessModal.classList.add('hidden');
        }

        // Clear form
        document.getElementById('tlAccessName').value = '';
        document.getElementById('tlAccessPin').value = '';

        // Update UI to show TL is logged in
        updateTLStatus();

        // Execute pending action
        if (pendingTlAction) {
          pendingTlAction();
          pendingTlAction = null;
        }

        // Refresh lines to show only TL's lines
        if (locSel.value && whSel.value) {
          await fetchLines();
        }

        // Update the line selection hint to be more specific for TLs
        const hint = document.getElementById('targetHint');
        if (hint && currentTLSession) {
          hint.textContent = `Select a line you configured as ${currentTLSession.displayName}.`;
        }

        return true;
      } else {
        showToast(result.reason === 'bad_pin' ? 'Invalid PIN' : 'Access denied');
        return false;
      }
    } catch (error) {
      showToast('Network error');
      return false;
    }
  }

  function updateTLStatus() {
    // Update TL status display
    const userDisplay = document.getElementById('userDisplay');
    if (userDisplay && currentTLSession) {
      const userData = localStorage.getItem('dsv_user');
      if (userData) {
        try {
          const user = JSON.parse(userData);
          userDisplay.textContent = `${user.role.toUpperCase()}: ${user.name} | TL Session: ${currentTLSession.displayName}`;
        } catch (e) {
          userDisplay.textContent = `TL Session: ${currentTLSession.displayName}`;
        }
      }
    }
  }

  // View Logs Button
  const btnViewLogs = document.getElementById('btnViewLogs');
  if (btnViewLogs) {
    btnViewLogs.addEventListener('click', function() {
      ensureTLAccessModal(); // Ensure modal exists
      requireTlAccess(() => {
        window.location.href = '/log';
      });
    });
  }

  // Add TL Access Modal to the page if it doesn't exist
  function ensureTLAccessModal() {
    if (!document.getElementById('tlAccessModal')) {
      const modalHTML = `
        <div id="tlAccessModal" class="fixed inset-0 bg-black bg-opacity-50 hidden z-50">
          <div class="flex items-center justify-center min-h-screen p-4">
            <div class="bg-white rounded-lg max-w-md w-full p-6">
              <div class="flex justify-between items-center mb-4">
                <h3 class="text-lg font-semibold">TL Access Required</h3>
                <button id="closeTlAccess" class="text-gray-400 hover:text-gray-600">
                  <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                  </svg>
                </button>
              </div>
              <div class="space-y-4">
                <div>
                  <label class="block text-sm font-medium text-gray-700 mb-2">TL Name</label>
                  <select id="tlAccessName" class="w-full p-3 border border-gray-300 rounded-lg">
                    <option value="">Select Team Leader</option>
                    <option value="Altaf">Altaf</option>
                    <option value="Faran">Faran</option>
                    <option value="Rojin">Rojin</option>
                  </select>
                </div>
                <div>
                  <label class="block text-sm font-medium text-gray-700 mb-2">PIN</label>
                  <input type="password" id="tlAccessPin" class="w-full p-3 border border-gray-300 rounded-lg" placeholder="Enter PIN">
                </div>
                <div class="flex gap-3">
                  <button id="tlAccessSubmit" class="flex-1 bg-blue-600 text-white py-3 px-6 rounded-lg hover:bg-blue-700">
                    Login
                  </button>
                  <button id="tlAccessCancel" class="flex-1 bg-gray-600 text-white py-3 px-6 rounded-lg hover:bg-gray-700">
                    Cancel
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      `;
      document.body.insertAdjacentHTML('beforeend', modalHTML);

      // Add event listeners for the new modal
      const tlAccessSubmit = document.getElementById('tlAccessSubmit');
      const closeTlAccess = document.getElementById('closeTlAccess');
      const tlAccessCancel = document.getElementById('tlAccessCancel');

      if (tlAccessSubmit) {
        tlAccessSubmit.addEventListener('click', verifyTlAccess);
      }

      if (closeTlAccess) {
        closeTlAccess.addEventListener('click', function() {
          const tlAccessModal = document.getElementById('tlAccessModal');
          if (tlAccessModal) {
            tlAccessModal.classList.add('hidden');
          }
          pendingTlAction = null;
        });
      }

      if (tlAccessCancel) {
        tlAccessCancel.addEventListener('click', function() {
          const tlAccessModal = document.getElementById('tlAccessModal');
          if (tlAccessModal) {
            tlAccessModal.classList.add('hidden');
          }
          pendingTlAction = null;
        });
      }
    }
  }

  // TL Setup
  tlSetupBtn.addEventListener('click', function() {
    modalTitle.textContent = 'Setup Line & Assign Counters';
    modalContent.innerHTML = `
      <div class="space-y-4">
        <div class="mb-4 p-3 bg-blue-50 rounded-lg">
          <p class="text-sm text-blue-800">
            <strong>Note:</strong> You can only view and manage lines that you have created. Other Team Leaders' setups will not be visible to you.
          </p>
        </div>
        <div>
          <label class="block text-sm font-medium text-gray-700 mb-2">TL Name</label>
          <select id="tlName" class="w-full p-3 border border-gray-300 rounded-lg">
            <option value="">Select Team Leader</option>
            <option value="Altaf">Altaf</option>
            <option value="Faran">Faran</option>
            <option value="Rojin">Rojin</option>
          </select>
        </div>

        <div>
          <label class="block text-sm font-medium text-gray-700 mb-2">TL PIN</label>
          <input type="password" id="tlPin" class="w-full p-3 border border-gray-300 rounded-lg" placeholder="Enter PIN">
        </div>

        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-2">Location</label>
            <select id="setupLocation" class="w-full p-3 border border-gray-300 rounded-lg">
              <option value="">Select</option>
              ${Object.keys(warehouses).map(loc => `<option value="${loc}">${loc}</option>`).join('')}
            </select>
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-2">Warehouse</label>
            <select id="setupWarehouse" class="w-full p-3 border border-gray-300 rounded-lg" disabled>
              <option value="">Select</option>
            </select>
          </div>
        </div>

        <div>
          <label class="block text-sm font-medium text-gray-700 mb-2">Line Code</label>
          <input type="text" id="setupLineCode" class="w-full p-3 border border-gray-300 rounded-lg" placeholder="e.g., L-01 or 500">
        </div>

        <div>
          <label class="block text-sm font-medium text-gray-700 mb-2">Target Quantity</label>
          <input type="number" id="targetQty" class="w-full p-3 border border-gray-300 rounded-lg" placeholder="e.g., 500">
        </div>

        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-2">Counter 1</label>
            <select id="counter1" class="w-full p-3 border border-gray-300 rounded-lg">
              <option value="">Select Counter</option>
              <option value="Hani">Hani</option>
              <option value="hasan">hasan</option>
              <option value="sami">sami</option>
              <option value="erol">erol</option>
              <option value="ali">ali</option>
              <option value="omar">omar</option>
            </select>
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-2">Counter 2</label>
            <select id="counter2" class="w-full p-3 border border-gray-300 rounded-lg">
              <option value="">Select Counter</option>
              <option value="Hani">Hani</option>
              <option value="hasan">hasan</option>
              <option value="sami">sami</option>
              <option value="erol">erol</option>
              <option value="ali">ali</option>
              <option value="omar">omar</option>
            </select>
          </div>
        </div>

        <div class="flex gap-3">
          <button id="setupSubmit" class="flex-1 bg-green-600 text-white py-3 px-6 rounded-lg hover:bg-green-700 transition duration-200">
            Setup Line
          </button>
          <button id="deleteLineBtn" class="bg-red-600 text-white py-3 px-6 rounded-lg hover:bg-red-700 transition duration-200">
            Delete Line
          </button>
        </div>
      </div>
    `;

    // Setup warehouse dropdown for setup
    const setupLocationSelect = document.getElementById('setupLocation');
    const setupWarehouseSelect = document.getElementById('setupWarehouse');

    setupLocationSelect.addEventListener('change', function() {
      const location = this.value;
      setupWarehouseSelect.innerHTML = '<option value="">Select</option>';

      if (location && warehouses[location]) {
        setupWarehouseSelect.disabled = false;
        warehouses[location].forEach(warehouse => {
          const option = document.createElement('option');
          option.value = warehouse;
          option.textContent = warehouse;
          setupWarehouseSelect.appendChild(option);
        });
      } else {
        setupWarehouseSelect.disabled = true;
      }
    });

    // Delete line functionality
    document.getElementById('deleteLineBtn').addEventListener('click', async function() {
      const lineCode = document.getElementById('setupLineCode').value.trim();
      const location = document.getElementById('setupLocation').value;
      const warehouse = document.getElementById('setupWarehouse').value;

      if (!lineCode || !location || !warehouse) {
        showToast('Please fill location, warehouse, and line code first');
        return;
      }

      const passcode = prompt('Enter passcode to delete line:');
      if (passcode !== '240986') {
        showToast('Invalid passcode');
        return;
      }

      if (!confirm(`Are you sure you want to delete line ${lineCode}? This action cannot be undone.`)) {
        return;
      }

      try {
        const deleteRes = await fetch('/api/line/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            location: location,
            warehouse: warehouse,
            line_code: lineCode,
            tl_name: document.getElementById('tlName').value.trim(),
            pin: document.getElementById('tlPin').value
          })
        });

        if (deleteRes.ok) {
          showToast('Line deleted successfully!');
          // Clear the form
          document.getElementById('setupLineCode').value = '';
          document.getElementById('targetQty').value = '';
          document.getElementById('counter1').value = '';
          document.getElementById('counter2').value = '';
          // Refresh the lines dropdown if same location/warehouse
          if (location === locSel.value && warehouse === whSel.value) {
            await fetchLines();
          }
        } else {
          const error = await deleteRes.json();
          showToast(error.error || 'Delete failed');
        }
      } catch (err) {
        showToast('Network error');
      }
    });

    // Setup submit
    document.getElementById('setupSubmit').addEventListener('click', async function() {
      const data = {
        location: document.getElementById('setupLocation').value,
        warehouse: document.getElementById('setupWarehouse').value,
        line_code: document.getElementById('setupLineCode').value.trim(),
        target_qty: parseInt(document.getElementById('targetQty').value),
        counter1: document.getElementById('counter1').value.trim(),
        counter2: document.getElementById('counter2').value.trim(),
        tl_name: document.getElementById('tlName').value.trim(),
        pin: document.getElementById('tlPin').value
      };

      if (!data.location || !data.warehouse || !data.line_code || !data.target_qty || !data.counter1 || !data.counter2 || !data.tl_name || !data.pin) {
        showToast('Please fill all fields');
        return;
      }

      try {
        const setupRes = await fetch('/api/line/upsert', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data)
        });

        if (setupRes.ok) {
          showToast('Line setup successful!');
          tlModal.classList.add('hidden');
          // Refresh the lines dropdown if same location/warehouse
          if (data.location === locSel.value && data.warehouse === whSel.value) {
            await fetchLines();
          }
          // Dispatch event so other parts of the app can react to changes
          window.dispatchEvent(new CustomEvent("lines-updated", {
            detail: {
              location: data.location,
              warehouse: data.warehouse,
              line_code: data.line_code
            }
          }));
        } else {
          const error = await setupRes.json();
          showToast(error.error || 'Setup failed');
        }
      } catch (err) {
        showToast('Network error');
      }
    });

    tlModal.classList.remove('hidden');
  });

  // Close modal
  closeModal.addEventListener('click', function() {
    tlModal.classList.add('hidden');
  });

  // Toast function
  function showToast(message, type = 'info') {
    // Create a small toast notification
    const toast = document.createElement('div');
    let bgColor = '#f59e0b'; // default orange
    
    switch(type) {
      case 'success':
        bgColor = '#10b981';
        break;
      case 'error':
        bgColor = '#ef4444';
        break;
      case 'warning':
        bgColor = '#f59e0b';
        break;
      case 'info':
      default:
        bgColor = '#3b82f6';
        break;
    }
    
    toast.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        background: ${bgColor};
        color: white;
        padding: 12px 20px;
        border-radius: 6px;
        z-index: 10000;
        font-size: 14px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    `;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => {
      toast.remove();
    }, 3000);
  }

  // Error and Success message functions (kept for backward compatibility if needed)
  function showError(message) {
    const errorDiv = document.getElementById('errorMessage');
    errorDiv.textContent = message;
    errorDiv.style.display = 'block';
    setTimeout(() => {
      errorDiv.style.display = 'none';
    }, 5000);
  }

  function showSuccess(message) {
    const successDiv = document.getElementById('successMessage');
    successDiv.textContent = message;
    successDiv.style.display = 'block';
    setTimeout(() => {
      successDiv.style.display = 'none';
    }, 3000);
  }

  // User Authentication Check
  function checkUserAuth() {
    const userData = localStorage.getItem('dsv_user');
    console.log('Checking user auth, userData:', userData ? 'exists' : 'missing');
    
    if (!userData) {
      console.log('No user data found, redirecting to signin');
      window.location.href = '/signin';
      return false;
    }

    try {
      const user = JSON.parse(userData);
      console.log('User data parsed:', user);
      
      if (!user.signedInAt || !user.name || !user.role) {
        console.log('Invalid user data structure, clearing and redirecting');
        localStorage.removeItem('dsv_user');
        window.location.href = '/signin';
        return false;
      }
      
      const signedInTime = new Date(user.signedInAt);
      const now = new Date();
      const hoursDiff = (now - signedInTime) / (1000 * 60 * 60);

      if (hoursDiff >= 8) {
        // Session expired
        console.log('Session expired, clearing and redirecting');
        localStorage.removeItem('dsv_user');
        window.location.href = '/signin';
        return false;
      }

      // Update user display
      const userDisplay = document.getElementById('userDisplay');
      if (userDisplay) {
        userDisplay.textContent = `${user.role.toUpperCase()}: ${user.name}`;
      }

      // Hide TL actions if user is not a TL or Manager
      if (user.role !== 'tl' && user.role !== 'manager') {
        const tlSection = document.getElementById('tlSection');
        if (tlSection) tlSection.style.display = 'none';
      }

      // Show insights dashboard for managers only
      if (user.role === 'manager') {
        const managerInsights = document.getElementById('managerInsights');
        if (managerInsights) {
          managerInsights.classList.remove('hidden');
        }
      }

      console.log('User authentication successful');
      return true;
    } catch (e) {
      console.error('Error parsing user data:', e);
      localStorage.removeItem('dsv_user');
      window.location.href = '/signin';
      return false;
    }
  }

  // Sign out functionality
  const signOutBtn = document.getElementById('signOutBtn');
  if (signOutBtn) {
    signOutBtn.addEventListener('click', function() {
      localStorage.removeItem('dsv_user');
      window.location.href = '/signin';
    });
  }

  // Check authentication before initializing
  if (!checkUserAuth()) {
    console.log("User not authenticated, redirecting to sign-in");
    window.location.href = '/signin';
    return; // Stop execution if not authenticated
  }

  // Load counter assignments if user is a counter
  async function loadCounterAssignments() {
    const userData = localStorage.getItem('dsv_user');
    if (!userData) return;

    try {
      const user = JSON.parse(userData);
      if (user.role === 'counter') {
        console.log('Loading jobs for counter:', user.name);
        
        // Auto-fill counter name FIRST before making API call
        counterIn.value = user.name;
        currentCounter = user.name;
        save(); // Save the counter name immediately
        
        // Show loading indicator
        const loadingDiv = document.createElement('div');
        loadingDiv.id = 'assignmentLoading';
        loadingDiv.className = 'mb-4 p-4 bg-blue-50 border border-blue-200 rounded-lg text-center';
        loadingDiv.innerHTML = `
          <div class="inline-flex items-center">
            <svg class="animate-spin -ml-1 mr-3 h-5 w-5 text-blue-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span class="text-blue-800 font-medium">Loading your jobs...</span>
          </div>
          <p class="text-sm text-blue-600 mt-2">Counter: ${user.name}</p>
        `;
        
        const form = document.getElementById('startForm');
        if (form && form.parentNode) {
          form.parentNode.insertBefore(loadingDiv, form);
        }
        
        const response = await fetch(`/api/counter/jobs?counter=${encodeURIComponent(user.name)}`);
        if (response.ok) {
          const result = await response.json();
          console.log('Counter jobs API response:', result);
          
          if (result.ok && result.items && result.items.length > 0) {
            // Get the first job (or you could show a picker if multiple)
            const job = result.items[0];
            console.log('Found job:', job);

            // Auto-fill location
            locSel.value = job.location;

            // Update warehouse dropdown and select warehouse
            updateWarehouses();
            
            // Wait for warehouses to load, then set warehouse
            await new Promise(resolve => setTimeout(resolve, 200));
            whSel.value = job.warehouse;

            // Save the selections
            save();

            // Load lines for this location/warehouse
            await new Promise(resolve => setTimeout(resolve, 200));
            await fetchLines();

            // Auto-select the line if it matches job
            await new Promise(resolve => setTimeout(resolve, 300));
            console.log('Looking for line option with value:', job.line_code);
            for (let option of lineSel.options) {
              console.log('Checking option:', option.value, option.textContent);
              if (option.value === job.line_code) {
                lineSel.value = job.line_code;
                save();
                await updateStateHint();
                console.log('Line auto-selected:', job.line_code);
                break;
              }
            }

            // Show job info prominently
            const jobInfo = document.createElement('div');
            jobInfo.className = 'mb-6 p-6 bg-gradient-to-r from-green-50 to-blue-50 border-2 border-green-300 rounded-xl shadow-lg';
            jobInfo.innerHTML = `
              <div class="flex items-start justify-between mb-4">
                <div>
                  <h3 class="text-xl font-bold text-green-800 mb-2">🎯 Your Active Job</h3>
                  <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                    <div>
                      <span class="font-semibold text-gray-700">Location:</span>
                      <span class="ml-2 px-2 py-1 bg-blue-100 text-blue-800 rounded">${job.location}</span>
                    </div>
                    <div>
                      <span class="font-semibold text-gray-700">Warehouse:</span>
                      <span class="ml-2 px-2 py-1 bg-blue-100 text-blue-800 rounded">${job.warehouse}</span>
                    </div>
                    <div>
                      <span class="font-semibold text-gray-700">Line Code:</span>
                      <span class="ml-2 px-2 py-1 bg-green-100 text-green-800 rounded font-bold">${job.line_code}</span>
                    </div>
                    <div>
                      <span class="font-semibold text-gray-700">Target Qty:</span>
                      <span class="ml-2 px-2 py-1 bg-orange-100 text-orange-800 rounded font-bold">${job.target_qty}</span>
                    </div>
                    <div>
                      <span class="font-semibold text-gray-700">Status:</span>
                      <span class="ml-2 px-2 py-1 ${job.status === 'open' ? 'bg-green-100 text-green-800' : 'bg-yellow-100 text-yellow-800'} rounded">
                        ${job.status || 'Ready to start'}
                      </span>
                    </div>
                  </div>
                </div>
                <div class="ml-4">
                  <button id="quickStartBtn" class="bg-green-600 text-white px-6 py-3 rounded-lg hover:bg-green-700 transition duration-200 font-bold text-lg shadow-md">
                    🚀 Start Counting
                  </button>
                </div>
              </div>
              <div class="mt-4 p-3 bg-white rounded-lg border border-green-200">
                <p class="text-sm text-gray-600 mb-2">
                  <strong>Ready to continue?</strong> Your job has been automatically loaded. Click "Start Counting" to continue.
                </p>
              </div>
            `;

            // Remove loading indicator
            const loadingDiv = document.getElementById('assignmentLoading');
            if (loadingDiv) {
              loadingDiv.remove();
            }
            
            // Insert before the form
            const form = document.getElementById('startForm');
            if (form && form.parentNode) {
              form.parentNode.insertBefore(jobInfo, form);
              
              // Show success toast
              showToast('✅ Job loaded successfully!', 'success');
              
              // Add click handler for quick start button
              const quickStartBtn = document.getElementById('quickStartBtn');
              if (quickStartBtn) {
                quickStartBtn.addEventListener('click', function() {
                  // Trigger form submission if all fields are filled
                  if (locSel.value && whSel.value && lineSel.value && counterIn.value.trim()) {
                    // Check if start button is enabled
                    if (!startBtn.disabled) {
                      const u = new URL("/count", location.origin);
                      u.searchParams.set("location", locSel.value);
                      u.searchParams.set("warehouse", whSel.value);
                      u.searchParams.set("line", lineSel.value);
                      u.searchParams.set("counter", counterIn.value.trim());
                      location.href = u.toString();
                    } else {
                      showToast('Please wait for verification...', 'warning');
                    }
                  } else {
                    showToast('Job not fully loaded yet. Please wait...', 'warning');
                  }
                });
              }
            }

          } else {
            console.log('No jobs found for counter:', user.name);
            
            // Remove loading indicator
            const loadingDiv = document.getElementById('assignmentLoading');
            if (loadingDiv) {
              loadingDiv.remove();
            }
            
            // Counter has no jobs - show prominent message
            const noJobDiv = document.createElement('div');
            noJobDiv.className = 'mb-6 p-6 bg-gradient-to-r from-orange-50 to-red-50 border-2 border-orange-300 rounded-xl shadow-lg';
            noJobDiv.innerHTML = `
              <div class="text-center">
                <div class="text-4xl mb-4">⚠️</div>
                <h3 class="text-xl font-bold text-orange-800 mb-3">No Active Jobs Found</h3>
                <div class="bg-white p-4 rounded-lg border border-orange-200 mb-4">
                  <p class="text-sm text-gray-700 mb-2">
                    <strong>Counter:</strong> <span class="font-mono bg-gray-100 px-2 py-1 rounded">${user.name}</span>
                  </p>
                  <p class="text-sm text-orange-700">
                    You have no active counting jobs available.
                  </p>
                </div>
                <div class="space-y-2 text-sm text-gray-600">
                  <p><strong>What to do next:</strong></p>
                  <ul class="list-disc list-inside space-y-1 text-left max-w-md mx-auto">
                    <li>Contact your Team Leader to get assigned to a line</li>
                    <li>Check if you've completed all your assigned work</li>
                    <li>Or manually select location/warehouse below</li>
                  </ul>
                </div>
              </div>
            `;

            const form = document.getElementById('startForm');
            if (form && form.parentNode) {
              form.parentNode.insertBefore(noJobDiv, form);
            }
          }
        } else {
          console.error('Counter jobs API request failed:', response.status);
          
          // Remove loading indicator
          const loadingDiv = document.getElementById('assignmentLoading');
          if (loadingDiv) {
            loadingDiv.remove();
          }
          
          // Show error message but still auto-fill counter name
          const errorDiv = document.createElement('div');
          errorDiv.className = 'mb-4 p-3 bg-red-50 border border-red-200 rounded-lg';
          errorDiv.innerHTML = `
            <p class="text-sm text-red-800">
              <strong>⚠️ Job Check Failed:</strong> Unable to load your jobs. Please select location and warehouse manually.
              <br><small>Counter: ${user.name}</small>
            </p>
          `;

          const form = document.getElementById('startForm');
          if (form && form.parentNode) {
            form.parentNode.insertBefore(errorDiv, form);
          }
        }
      }
    } catch (error) {
      console.error('Error loading counter jobs:', error);
      
      // Remove loading indicator
      const loadingDiv = document.getElementById('assignmentLoading');
      if (loadingDiv) {
        loadingDiv.remove();
      }
      
      // Still try to auto-fill counter name
      try {
        const user = JSON.parse(userData);
        if (user.role === 'counter') {
          counterIn.value = user.name;
          currentCounter = user.name;
          save();
        }
      } catch (e) {}
    }
  }

  // Check for reconciliation notifications if TL is logged in
  async function checkReconcileNotifications() {
    if (!currentTLSession) return;

    try {
      const response = await fetch('/api/reconcile/notification_count', {
        credentials: 'same-origin'
      });

      if (response.ok) {
        const data = await response.json();
        const badge = document.getElementById('reconcileNotificationBadge');

        if (badge && data.ok) {
          if (data.count > 0) {
            badge.textContent = data.count;
            badge.classList.remove('hidden');

            // Update page title with notification
            const originalTitle = document.title;
            if (!originalTitle.includes('🔔')) {
              document.title = `🔔 (${data.count}) ${originalTitle}`;
            }
          } else {
            badge.classList.add('hidden');

            // Clear notification from title
            const currentTitle = document.title;
            if (currentTitle.includes('🔔')) {
              document.title = currentTitle.replace(/🔔 \(\d+\) /, '');
            }
          }
        }
      }
    } catch (error) {
      // Silently fail
    }
  }

  // Check for pending notifications for all TLs when reconcile button is clicked
  async function checkPendingNotificationsForAllTLs() {
    try {
      // Get all pending requests without TL authentication
      const response = await fetch('/api/reconcile/pending_count_all', {
        credentials: 'same-origin'
      });

      if (response.ok) {
        const data = await response.json();
        const badge = document.getElementById('reconcileNotificationBadge');

        if (badge && data.ok && data.count > 0) {
          badge.textContent = data.count;
          badge.classList.remove('hidden');
          badge.style.backgroundColor = '#f59e0b'; // Orange color to indicate general notifications

          // Show a different style to indicate these are general notifications
          const reconcileBtn = document.getElementById('btnReconcile');
          if (reconcileBtn) {
            reconcileBtn.style.borderLeft = '4px solid #f59e0b';
          }
        }
      }
    } catch (error) {
      // Silently fail
    }
  }

  // Check for notifications every 5 seconds when TL is logged in
  setInterval(() => {
    if (currentTLSession) {
      checkReconcileNotifications();
    } else {
      // Check for general notifications even when no TL is logged in
      checkPendingNotificationsForAllTLs();
    }
  }, 5000);

  // Initial check for notifications
  if (currentTLSession) {
    checkReconcileNotifications();
  } else {
    // Check for pending notifications on page load
    checkPendingNotificationsForAllTLs();
  }

  // Add click handler to reconciliation center button
  const btnReconcile = document.getElementById('btnReconcile');
  if (btnReconcile) {
    btnReconcile.addEventListener('click', function() {
      ensureTLAccessModal(); // Ensure modal exists
      requireTlAccess(() => {
        window.location.href = '/reconcile';
      });
      // Check for notifications when button is clicked
      checkPendingNotificationsForAllTLs();
    });
  }

  // Load counter jobs for the current counter
  // Load counter jobs
  function loadCounterJobs() {
    if (!currentCounter) return;

    fetch(`/api/counter/jobs?counter=${encodeURIComponent(currentCounter)}`)
      .then(response => response.json())
      .then(data => {
        if (data.ok) {
          updateJobSelect(data.items);
        } else {
          console.error('Failed to load jobs:', data);
        }
      })
      .catch(error => {
        console.error('Error loading jobs:', error);
      });
  }

  // Update job select to use new data structure
  function updateJobSelect(jobs) {
    const jobSelect = document.getElementById('jobSelect');
    jobSelect.innerHTML = '<option value="">Select a job...</option>';

    jobs.forEach(job => {
      const option = document.createElement('option');
      option.value = `${job.location}-${job.warehouse}-${job.line_code}`;
      option.textContent = `${job.location} - ${job.warehouse} - ${job.line_code} (Target: ${job.target_qty})`;
      jobSelect.appendChild(option);
    });
  }

  // Add Item
  document.getElementById('addItem').addEventListener('click', async () => {
    if (!jobState.job_id) {
      showError('No job selected');
      return;
    }

    const sku = document.getElementById('itemSku').value.trim();
    const serial = document.getElementById('itemSerial').value.trim();
    const qty = parseInt(document.getElementById('itemQty').value) || 1;
    const source = 'manual';

    if (!serial) {
      showError('Serial/Code is required');
      return;
    }

    try {
      const response = await fetch('/api/scan/add', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          job_id: jobState.job_id,
          line_id: jobState.line_id,
          counter_name: currentCounter,
          sku: sku,
          serial_or_code: serial,
          qty: qty,
          source: source
        }),
      });

      if (response.status === 409) {
        const result = await response.json();
        if (result.duplicate) {
          showToast('Duplicate: same SKU & Serial already scanned');
          return;
        }
      }

      if (response.ok) {
        const result = await response.json();
        if (result.ok) {
          // Update the scanned total
          jobState.scanned_total = result.scanned_total;
          updateUI();

          // Clear the form
          document.getElementById('itemSku').value = '';
          document.getElementById('itemSerial').value = '';
          document.getElementById('itemQty').value = '1';

          // Load recent scans
          loadRecentScans();

          showSuccess('Item added successfully');
        }
      } else {
        console.warn('Add item failed with status:', response.status);
      }
    } catch (error) {
      console.warn('Error adding item:', error);
    }
  });

  // Update UI elements like counts and button visibility
  function updateUI() {
    const countsDisplay = document.getElementById('countsDisplay');
    const submitBtn = document.getElementById('submitBtn');
    const reconcileBtn = document.getElementById('reconcileBtn');

    if (jobState.job_id) {
      const remaining = jobState.target_qty - jobState.scanned_total;
      countsDisplay.innerHTML = `
                <div class="bg-blue-50 p-4 rounded-lg">
                    <div class="grid grid-cols-3 gap-4 text-center">
                        <div>
                            <div class="text-2xl font-bold text-blue-600">${jobState.target_qty}</div>
                            <div class="text-sm text-gray-600">Target</div>
                        </div>
                        <div>
                            <div class="text-2xl font-bold text-green-600">${jobState.scanned_total}</div>
                            <div class="text-sm text-gray-600">Scanned</div>
                        </div>
                        <div>
                            <div class="text-2xl font-bold ${remaining === 0 ? 'text-green-600' : remaining > 0 ? 'text-orange-600' : 'text-red-600'}">${remaining}</div>
                            <div class="text-sm text-gray-600">Remaining</div>
                        </div>
                    </div>
                </div>
            `;

      // Button logic according to SOP
      const equal = (jobState.scanned_total === jobState.target_qty);
      const locked = (jobState.status === "locked_recon");
      const approved = (jobState.status === "variance_approved");

      // Submit visible/enabled only if approved || equal
      submitBtn.style.display = (approved || equal) ? 'block' : 'none';

      // Request Reconciliation visible only if !equal && !locked && !approved
      reconcileBtn.style.display = (!equal && !locked && !approved) ? 'block' : 'none';
    } else {
      countsDisplay.innerHTML = '';
      submitBtn.style.display = 'none';
      reconcileBtn.style.display = 'none';
    }
  }

  // Check for TL response to reconciliation
  async function checkReconcileResponse() {
    if (!jobState.job_id || jobState.status !== 'locked_recon') return;

    try {
      const response = await fetch(`/api/reconcile/check_response?job_id=${jobState.job_id}`);
      const data = await response.json();

      if (data.resolved && !data.acknowledged) {
        // Show TL response
        showSuccess(`TL Response: ${data.response}`);

        // Acknowledge the response
        await fetch('/api/reconcile/acknowledge', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            job_id: jobState.job_id
          }),
        });

        // Refresh job state
        await loadJobState();
      }
    } catch (error) {
      console.error('Error checking reconcile response:', error);
    }
  }

  // Auto-refresh job state every 3 seconds
  function startAutoRefresh() {
    setInterval(async () => {
      if (jobState.job_id) {
        await loadJobState();
        if (jobState.status === 'variance_approved') {
          updateUI(); // Enable submit button
        }
      }
    }, 3000);
  }

  // Request Reconciliation
  document.getElementById('reconcileBtn').addEventListener('click', async () => {
    const reason = prompt('Please provide a reason for reconciliation:');
    if (!reason || !reason.trim()) {
      return;
    }

    try {
      const response = await fetch('/api/reconcile/request', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          job_id: jobState.job_id,
          line_id: jobState.line_id,
          counter_name: currentCounter,
          reason: reason.trim()
        }),
      });

      const result = await response.json();

      if (response.ok && result.ok) {
        showSuccess('Reconciliation request sent to TL');

        // Set job status to locked and update UI
        jobState.status = 'locked_recon';
        updateUI();

        // Start checking for TL response
        setInterval(checkReconcileResponse, 3000);
      } else if (result.reason === 'no_mismatch') {
        showError('No reconciliation needed - counts match target');
      } else {
        showError(result.error || 'Failed to send reconciliation request');
      }
    } catch (error) {
      console.error('Error requesting reconciliation:', error);
      showError('Network error occurred');
    }
  });

  // Load current job state
  async function loadJobState() {
    if (!jobState.job_id) return;

    const qs = new URLSearchParams({
      location: locSel.value,
      warehouse: whSel.value,
      line_code: jobState.line_code,
      counter: currentCounter
    });

    try {
      const res = await fetch(`/api/job/state?${qs.toString()}`);
      const d = await res.json();

      if (!res.ok || !d.ok) {
        console.error("Failed to load job state:", d);
        return;
      }

      // Update job state, preserving existing values if API response is incomplete
      jobState = {
        job_id: d.job_id || jobState.job_id,
        line_id: d.line_id || jobState.line_id,
        line_code: d.line_code || jobState.line_code,
        target_qty: d.target_qty !== undefined ? d.target_qty : jobState.target_qty,
        scanned_total: d.scanned_total !== undefined ? d.scanned_total : jobState.scanned_total,
        status: d.status || jobState.status,
        assigned: d.assigned || jobState.assigned
      };

      updateUI(); // Refresh UI elements that depend on job state
    } catch (err) {
      console.error("Error loading job state:", err);
    }
  }

  // Load recent scans for the current job
  async function loadRecentScans() {
    if (!jobState.job_id) return;

    try {
      const response = await fetch(`/api/scan/recent?job_id=${jobState.job_id}`);
      const data = await response.json();

      const recentScansList = document.getElementById('recentScansList');
      recentScansList.innerHTML = ''; // Clear previous list

      if (response.ok && data.ok && data.scans.length > 0) {
        data.scans.forEach(scan => {
          const li = document.createElement('li');
          li.className = 'py-2 flex justify-between text-sm';
          li.innerHTML = `
                        <span>${scan.sku || 'N/A'} - ${scan.serial_or_code}</span>
                        <span class="text-gray-600">${scan.qty} @ ${new Date(scan.timestamp).toLocaleTimeString()}</span>
                    `;
          recentScansList.appendChild(li);
        });
      } else {
        recentScansList.innerHTML = '<li class="py-2 text-center text-gray-500">No recent scans found.</li>';
      }
    } catch (error) {
      console.error('Error loading recent scans:', error);
      const recentScansList = document.getElementById('recentScansList');
      recentScansList.innerHTML = '<li class="py-2 text-center text-red-500">Error loading scans.</li>';
    }
  }

  // Initialize event listeners for the counter interface
  function setupEventListeners() {
    // Event listener for job selection change
    document.getElementById('jobSelect').addEventListener('change', async (e) => {
      const selectedJob = e.target.value;
      if (!selectedJob) {
        // Clear job state if no job is selected
        jobState = {
          job_id: null, line_id: null, line_code: null, target_qty: 0, scanned_total: 0, status: '', assigned: []
        };
        updateUI();
        document.getElementById('countsDisplay').innerHTML = '';
        document.getElementById('recentScansList').innerHTML = '';
        return;
      }

      const [location, warehouse, line_code] = selectedJob.split('-');

      // Update location and warehouse selectors if they are not already set
      // This is useful if the user switches jobs in a different LW
      if (locSel.value !== location) {
        locSel.value = location;
        updateWarehouses();
      }
      if (whSel.value !== warehouse) {
        whSel.value = warehouse;
      }

      // Update line selector
      lineSel.value = line_code;

      // Fetch the state for the selected job
      await updateStateHint();

      // Load job details if state hint update was successful
      if (jobState.job_id) {
        await loadJobState(); // Fetch full job state
        loadRecentScans(); // Load recent scans for this job
      }
    });

    // Submit button for manual item addition
    document.getElementById('submitBtn').addEventListener('click', async () => {
      if (!jobState.job_id) {
        showError('No job selected');
        return;
      }

      try {
        const response = await fetch('/api/job/submit', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            job_id: jobState.job_id,
            line_id: jobState.line_id,
            counter_name: currentCounter,
            scanned_total: jobState.scanned_total,
            target_qty: jobState.target_qty
          }),
        });

        const result = await response.json();

        if (response.ok && result.ok) {
          showSuccess('Job submitted successfully!');
          // Clear job state after submission
          jobState = {
            job_id: null, line_id: null, line_code: null, target_qty: 0, scanned_total: 0, status: '', assigned: []
          };
          updateUI();
          document.getElementById('countsDisplay').innerHTML = '';
          document.getElementById('recentScansList').innerHTML = '';
          document.getElementById('jobSelect').value = ''; // Reset job dropdown
        } else {
          showError(result.error || 'Failed to submit job');
        }
      } catch (error) {
        console.error('Error submitting job:', error);
        showError('Network error occurred');
      }
    });
  }

  // Load counter name from URL if present (e.g., after redirect from signin)
  function loadCounterFromUrl() {
    const urlParams = new URLSearchParams(window.location.search);
    const counterName = urlParams.get('counter');
    if (counterName) {
      counterIn.value = counterName;
      currentCounter = counterName; // Set current counter
      save();
    }
  }

  // Initialize
  async function initialize() {
    load();

    // Load counter assignments first, then regular initialization
    await loadCounterAssignments();

    if (locSel.value && whSel.value) {
      fetchLines();
    }

    // Set up event listeners after initial load
    setupEventListeners();
    loadCounterFromUrl();
    startAutoRefresh();
    
    // Focus on counter name field only if it's empty
    if (!counterIn.value.trim()) {
      document.getElementById('counterName').focus();
    }
  }

  // Start initialization
  initialize();

  // Periodically refresh lines to catch target updates
  setInterval(() => {
    if (locSel.value && whSel.value) {
      fetchLines();
    }
  }, 15000); // Refresh every 15 seconds

  // Make refresh functions callable from other scripts (reconcile.js)
  async function BMAD_refreshLine({ location, warehouse, line_code } = {}) {
    const locSel = document.getElementById("location");
    const whSel  = document.getElementById("warehouse");
    const lineSel= document.getElementById("lineCode");

    // If the patch is called for current LW, refetch lines with cache-busting
    const sameLW = !location || !warehouse ||
                   (locSel.value === location && whSel.value === warehouse);

    if (sameLW) {
      console.log("Refreshing lines for:", { location, warehouse, line_code });
      await fetchLines(); // will refetch list with cache busting
      if (line_code) {
        lineSel.value = line_code;
        localStorage.setItem("line", line_code);
      }
      await updateStateHint();
      console.log("Lines refreshed successfully");
    }
  }
  window.BMAD_refreshLine = BMAD_refreshLine;

  // Also respond to a custom event raised by reconcile.js
  window.addEventListener("lines-updated", (ev) => {
    BMAD_refreshLine(ev.detail || {});
  });


})();