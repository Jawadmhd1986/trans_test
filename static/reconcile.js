(() => {
  const $ = (id) => document.getElementById(id);

  const openBtn = $("btnReconcile");
  const modal = $("reconModal");
  const closeBtn = $("reconClose");

  const tlName = $("recTlName");
  const tlPin = $("recTlPin");
  const locSel = $("recLocation");
  const whSel = $("recWarehouse");
  const lineIn = $("recLineCode");

  const stateBox = $("recState");
  const currentTargetInput = $("recCurrentTarget");
  const targetInput = $("recNewTarget");

  // Helper functions for showing messages
  function showSuccess(message) {
    const msgBox = document.getElementById('messageBox');
    if (msgBox) {
      msgBox.className = 'alert alert-success';
      msgBox.textContent = message;
      msgBox.style.display = 'block';
    }
  }

  function showError(message) {
    const msgBox = document.getElementById('messageBox');
    if (msgBox) {
      msgBox.className = 'alert alert-danger';
      msgBox.textContent = message;
      msgBox.style.display = 'block';
    }
  }

  function open() { 
    if (modal) {
      modal.classList.remove("hidden"); 
    }
  }

  function close() { 
    if (modal) modal.classList.add("hidden"); 
  }

  const post = (url, body) =>
    fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });

  const get = (url) =>
    fetch(url, { credentials: "same-origin" });

  async function tlLogin() {
    const tl_name = tlName?.value?.trim() || "";
    const tl_pin = tlPin?.value?.trim() || "";

    if (!tl_name || !tl_pin) {
      alert("Please enter TL name and PIN");
      return false;
    }

    try {
      const r = await post("/api/tl/login", { tl_name, tl_pin, tl_display_name: tl_name });
      const d = await r.json().catch(() => ({}));

      if (!r.ok || !d.ok) {
        alert(d.reason === "bad_pin" ? "Wrong PIN" : "Login failed");
        return false;
      }

      if (d.created) {
        alert("TL account created successfully!");
      } else if (d.pin_set) {
        alert("PIN set successfully!");
      }

      // Hide login section and show workspace section
      const loginSection = $("tlLoginSection");
      const workspaceSection = $("tlWorkspaceSection");

      if (loginSection) loginSection.classList.add("hidden");
      if (workspaceSection) workspaceSection.classList.remove("hidden");

      // Auto-populate TL's locations and warehouses based on their assignments
      await populateTLAssignments();

      // Load pending requests immediately after login
      await loadPendingRequests();

      // Check if there are pending requests and auto-direct to them
      await autoDirectToPendingRequests();

      return true;
    } catch (e) {
      alert("Network error during login");
      return false;
    }
  }

  async function populateTLAssignments() {
    try {
      // Get all TL's lines across all locations
      const allLocations = ["KIZAD", "JEBEL_ALI"];
      const allWarehouses = {
        "KIZAD": ["KIZAD-W1"],
        "JEBEL_ALI": ["JA-W1", "JA-W2", "JA-W3"]
      };

      let tlLocations = new Set();
      let tlWarehouses = new Set();

      // Check each location/warehouse combination for TL's lines
      for (const location of allLocations) {
        for (const warehouse of allWarehouses[location]) {
          const qs = new URLSearchParams({ location, warehouse });
          const r = await get(`/api/lines?${qs.toString()}`);

          if (r.ok) {
            const d = await r.json();
            if (d.ok && d.lines && d.lines.length > 0) {
              tlLocations.add(location);
              tlWarehouses.add(warehouse);
            }
          }
        }
      }

      // Update location dropdown
      if (locSel && tlLocations.size > 0) {
        locSel.innerHTML = '<option value="">Select Location</option>';
        Array.from(tlLocations).forEach(location => {
          const option = document.createElement('option');
          option.value = location;
          option.textContent = location;
          locSel.appendChild(option);
        });

        // Auto-select first location if only one
        if (tlLocations.size === 1) {
          locSel.value = Array.from(tlLocations)[0];
          await updateWarehouseOptions();
        }
      }

      // If only one location, auto-populate warehouses
      if (tlLocations.size === 1) {
        await updateWarehouseOptions();
      }

    } catch (e) {
      console.error("Error populating TL assignments:", e);
    }
  }

  async function updateWarehouseOptions() {
    const selectedLocation = locSel?.value;
    if (!selectedLocation || !whSel) return;

    const allWarehouses = {
      "KIZAD": ["KIZAD-W1"],
      "JEBEL_ALI": ["JA-W1", "JA-W2", "JA-W3"]
    };

    // Clear warehouse options
    whSel.innerHTML = '<option value="">Select Warehouse</option>';

    // Get TL's warehouses for this location
    const tlWarehouses = new Set();

    if (allWarehouses[selectedLocation]) {
      for (const warehouse of allWarehouses[selectedLocation]) {
        const qs = new URLSearchParams({ location: selectedLocation, warehouse });
        const r = await get(`/api/lines?${qs.toString()}`);

        if (r.ok) {
          const d = await r.json();
          if (d.ok && d.lines && d.lines.length > 0) {
            tlWarehouses.add(warehouse);
          }
        }
      }
    }

    // Populate warehouse dropdown with TL's warehouses only
    Array.from(tlWarehouses).forEach(warehouse => {
      const option = document.createElement('option');
      option.value = warehouse;
      option.textContent = warehouse;
      whSel.appendChild(option);
    });

    // Auto-select first warehouse if only one
    if (tlWarehouses.size === 1) {
      whSel.value = Array.from(tlWarehouses)[0];
      await loadTLLines();
    }
  }

  async function loadTLLines() {
    const location = locSel?.value || "";
    const warehouse = whSel?.value || "";

    if (!location || !warehouse) {
      if (lineIn) {
        lineIn.innerHTML = '<option value="">Select location and warehouse first</option>';
        lineIn.disabled = true;
      }
      return;
    }

    try {
      // Get pending requests to filter lines that need action
      const pendingResponse = await get("/api/reconcile/tl_queue");
      let pendingRequests = [];

      if (pendingResponse.ok) {
        const pendingData = await pendingResponse.json();
        if (pendingData.ok && pendingData.requests) {
          pendingRequests = pendingData.requests;
        }
      }

      const qs = new URLSearchParams({ location, warehouse });
      const r = await get(`/api/lines?${qs.toString()}`);

      if (lineIn) {
        lineIn.innerHTML = '<option value="">Select Line</option>';

        if (r.ok) {
          const d = await r.json();
          if (d.ok && d.lines && d.lines.length > 0) {
            // Filter lines to show only those with pending requests or all if no pending requests
            const linesToShow = pendingRequests.length > 0 ? 
              d.lines.filter(line => 
                pendingRequests.some(req => 
                  req.line_code === line.line_code && 
                  req.location === location && 
                  req.warehouse === warehouse
                )
              ) : d.lines;

            if (linesToShow.length > 0) {
              linesToShow.forEach(line => {
                const option = document.createElement('option');
                option.value = line.line_code;
                const assignedText = line.assigned.filter(Boolean).length > 0 ? 
                  ` • ${line.assigned.filter(Boolean).join(" & ")}` : " • No counters";
                const pendingIndicator = pendingRequests.some(req => 
                  req.line_code === line.line_code && 
                  req.location === location && 
                  req.warehouse === warehouse
                ) ? " 🔔" : "";
                option.textContent = `${line.line_code} • Target: ${line.target_qty}${assignedText}${pendingIndicator}`;
                lineIn.appendChild(option);
              });
              lineIn.disabled = false;

              // Auto-select first line if only one with pending requests
              if (linesToShow.length === 1 && pendingRequests.length > 0) {
                lineIn.value = linesToShow[0].line_code;
                await loadState();
              }
            } else {
              lineIn.innerHTML = pendingRequests.length > 0 ? 
                '<option value="">No lines require action in this location/warehouse</option>' :
                '<option value="">No lines configured yet</option>';
              lineIn.disabled = true;
            }
          } else {
            lineIn.innerHTML = '<option value="">No lines configured yet</option>';
            lineIn.disabled = true;
          }
        } else {
          lineIn.innerHTML = '<option value="">Error loading lines</option>';
          lineIn.disabled = true;
        }
      }

      // Load pending requests after loading lines
      await loadPendingRequests();
    } catch (e) {
      if (lineIn) {
        lineIn.innerHTML = '<option value="">Error loading lines</option>';
        lineIn.disabled = true;
      }
    }
  }

  async function loadPendingRequests() {
    const pendingSection = $("pendingRequests");
    const requestsList = $("requestsList");

    if (!pendingSection || !requestsList) return;

    try {
      const r = await get("/api/reconcile/tl_queue");

      if (r.ok) {
        const d = await r.json();

        if (d.ok && d.requests && d.requests.length > 0) {
          pendingSection.classList.remove("hidden");

          // Update page title with notification count
          const originalTitle = document.title;
          if (!originalTitle.includes('🔔')) {
            document.title = `🔔 (${d.requests.length}) ${originalTitle}`;
          }

          requestsList.innerHTML = d.requests.map(req => `
            <div class="border border-red-300 rounded-lg p-4 bg-red-50">
              <div class="flex justify-between items-start mb-2">
                <div>
                  <h4 class="font-semibold text-red-800">Line ${req.line_code} (${req.location}/${req.warehouse})</h4>
                  <p class="text-sm text-gray-600">Requested by: <strong>${req.requested_by}</strong> at ${req.created_at}</p>
                </div>
                <span class="bg-red-100 text-red-800 px-2 py-1 rounded text-xs">PENDING</span>
              </div>

              <div class="grid grid-cols-2 gap-4 mb-3">
                <div class="text-sm">
                  <span class="text-gray-600">Scanned:</span> <strong>${req.scanned_total}</strong>
                </div>
                <div class="text-sm">
                  <span class="text-gray-600">Target:</span> <strong>${req.target_qty}</strong>
                </div>
              </div>

              <p class="text-sm mb-3"><strong>Reason:</strong> ${req.reason || 'No reason provided'}</p>

              <div class="grid grid-cols-2 gap-2 mb-3">
                <input type="number" id="newTarget_${req.id}" placeholder="New target" 
                       value="${req.target_qty}" class="border rounded p-2 text-sm">
                <input type="text" id="note_${req.id}" placeholder="Response note" 
                       class="border rounded p-2 text-sm">
              </div>

              <div class="flex gap-2">
                <button onclick="respondToRequest(${req.id}, 'edit_target')" 
                        class="bg-blue-600 text-white px-3 py-1 rounded text-sm hover:bg-blue-700">
                  Edit Target
                </button>
                <button onclick="respondToRequest(${req.id}, 'approve_variance')" 
                        class="bg-green-600 text-white px-3 py-1 rounded text-sm hover:bg-green-700">
                  Approve Variance
                </button>
              </div>
            </div>
          `).join('');
        } else {
          pendingSection.classList.add("hidden");

          // Clear notification from title
          const currentTitle = document.title;
          if (currentTitle.includes('🔔')) {
            document.title = currentTitle.replace(/🔔 \(\d+\) /, '');
          }
        }
      }
    } catch (e) {
      console.error("Error loading pending requests:", e);
    }
  }

  async function autoDirectToPendingRequests() {
    try {
      const r = await get("/api/reconcile/tl_queue");

      if (r.ok) {
        const d = await r.json();

        if (d.ok && d.requests && d.requests.length > 0) {
          // If there are pending requests, auto-select the first one
          const firstRequest = d.requests[0];

          // Auto-populate location, warehouse, and line based on first request
          if (locSel) {
            locSel.value = firstRequest.location;
            await updateWarehouseOptions();
          }

          if (whSel) {
            whSel.value = firstRequest.warehouse;
            await loadTLLines();
          }

          if (lineIn) {
            lineIn.value = firstRequest.line_code;
            await loadState();
          }

          // Show a message about the pending request
          if (stateBox) {
            stateBox.innerHTML = `
              <div class="p-3 bg-red-50 border border-red-200 rounded">
                <p class="text-red-800 font-medium">🔔 Pending Reconciliation Request</p>
                <p class="text-sm text-red-700 mt-1">Line ${firstRequest.line_code} has a pending request from ${firstRequest.requested_by}.</p>
                <p class="text-xs text-red-600 mt-2">Please review the request below and take action.</p>
              </div>
            `;
          }
        }
      }
    } catch (e) {
      console.error("Error auto-directing to pending requests:", e);
    }
  }

  // Make function global so it can be called from HTML
  window.respondToRequest = async function(queueId, action) {
    const newTarget = $(`newTarget_${queueId}`)?.value;
    const note = $(`note_${queueId}`)?.value || "";

    if (action === 'edit_target' && (!newTarget || newTarget < 0)) {
      alert("Please enter a valid new target quantity");
      return;
    }

    try {
      const r = await post("/api/reconcile/tl_respond", {
        queue_id: queueId,
        action: action,
        new_target: action === 'edit_target' ? parseInt(newTarget) : null,
        note: note
      });

      if (r.ok) {
        alert(action === 'edit_target' ? 
          "Target updated successfully!" : 
          "Variance approved successfully!");

        // Reload pending requests
        await loadPendingRequests();

        // Trigger refresh for counters
        window.dispatchEvent(new CustomEvent("lines-updated"));
      } else {
        const error = await r.json();
        alert(error.error || "Failed to respond to request");
      }
    } catch (e) {
      alert("Network error responding to request");
    }
  };

  async function loadState() {
    const location = locSel?.value || "";
    const warehouse = whSel?.value || "";
    const line_code = lineIn?.value?.trim() || "";

    if (!location || !warehouse || !line_code) {
      if (stateBox) stateBox.textContent = "Please fill all fields";
      return;
    }

    try {
      const qs = new URLSearchParams({ location, warehouse, line_code, t: Date.now() });
      const r = await get(`/api/reconcile/state?${qs.toString()}`);

      if (!r.ok) { 
        if (stateBox) stateBox.textContent = "Line not configured."; 
        return; 
      }

      const d = await r.json();
      if (stateBox) {
        stateBox.innerHTML = `
          <strong>Line ${line_code}</strong><br/>
          Target: <b>${d.target_qty}</b> • Scanned: <b>${d.scanned_total}</b> • Status: <b>${d.status}</b><br/>
          Assigned: ${d.assigned.filter(Boolean).join(" & ")}
        `;
        stateBox.dataset.lineId = d.line_id;
        stateBox.dataset.jobId = d.job_id;
      }

      // Show current target in the readonly field
      if (currentTargetInput) currentTargetInput.value = d.target_qty;
      // Only pre-fill new target with current value if it's empty (initial load)
      if (targetInput) {
        if (!targetInput.value) targetInput.value = d.target_qty;
        targetInput.dataset.current = d.target_qty;
      }
    } catch (e) {
      if (stateBox) stateBox.textContent = "Error loading state";
    }
  }

  // Check for existing TL session and auto-login
  async function checkExistingTLSession() {
    try {
      // Check if TL is already authenticated by trying to get notification count
      const response = await fetch('/api/reconcile/notification_count', {
        credentials: 'same-origin'
      });

      if (response.ok) {
        const data = await response.json();
        if (data.ok) {
          // TL is already authenticated, hide login and show workspace
          const loginSection = $("tlLoginSection");
          const workspaceSection = $("tlWorkspaceSection");

          if (loginSection) loginSection.classList.add("hidden");
          if (workspaceSection) workspaceSection.classList.remove("hidden");

          // Auto-populate TL's locations and warehouses based on their assignments
          await populateTLAssignments();

          // Load pending requests immediately
          await loadPendingRequests();

          // Check if there are pending requests and auto-direct to them
          await autoDirectToPendingRequests();

          return true;
        }
      }
    } catch (error) {
      // TL not authenticated, show login form
      console.log("No existing TL session found");
    }
    return false;
  }

  // Event listeners
  const loginBtn = $("recLoginBtn");
  if (loginBtn) {
    loginBtn.addEventListener("click", async () => {
      await tlLogin();
    });
  }

  // Check for existing session on page load
  checkExistingTLSession();

  // Event listeners for location/warehouse changes - only active after login
  if (locSel) {
    locSel.addEventListener("change", async () => {
      const workspaceSection = $("tlWorkspaceSection");
      if (workspaceSection && !workspaceSection.classList.contains("hidden")) {
        await updateWarehouseOptions();
      }
    });
  }

  if (whSel) {
    whSel.addEventListener("change", async () => {
      const workspaceSection = $("tlWorkspaceSection");
      if (workspaceSection && !workspaceSection.classList.contains("hidden")) {
        await loadTLLines();
      }
    });
  }

  if (lineIn) {
    lineIn.addEventListener("change", async () => { 
      const workspaceSection = $("tlWorkspaceSection");
      if (workspaceSection && !workspaceSection.classList.contains("hidden")) {
        await loadState();
      }
    });
  }

  const editTargetBtn = $("recEditTargetBtn");
  if (editTargetBtn) {
    editTargetBtn.addEventListener("click", async () => {
      const lineId = parseInt(stateBox?.dataset?.lineId || "0", 10);
      const newT = parseInt(targetInput?.value || "0", 10);
      const currentT = parseInt(currentTargetInput?.value || "0", 10);

      if (!lineId || newT < 0) {
        alert("Invalid line ID or target value");
        return;
      }

      if (newT === currentT) {
        alert("New target is the same as current target");
        return;
      }

      try {
        const r = await post("/api/reconcile/edit_target", { line_id: lineId, new_target: newT });

        if (!r.ok) return alert("Failed to edit target");

        // Clear the new target input before reloading state
        if (targetInput) targetInput.value = "";

        await loadState();
        alert("Target updated successfully! Counters will see the new target immediately.");
      } catch (e) {
        alert("Network error updating target");
      }
    });
  }

  async function approveVarianceFlow() {
    // Read UI values
    const loc   = locSel?.value || "";
    const wh    = whSel?.value || "";
    const line  = (lineIn?.value || "").trim();
    const jobId = parseInt(stateBox?.dataset?.jobId || "0", 10);
    const lineId= parseInt(stateBox?.dataset?.lineId || "0", 10);
    const note  = ($("recNote")?.value || "").trim();

    if (!jobId || !lineId) {
      alert("Missing job or line information");
      return;
    }

    try {
      // If New Target provided and different, save it first
      const currentTargetStr = currentTargetInput?.value || "0";
      const newTargetStr = targetInput?.value?.trim() || "";

      if (newTargetStr !== "" && newTargetStr !== currentTargetStr) {
        const newTarget = parseInt(newTargetStr, 10);
        if (!isNaN(newTarget) && newTarget >= 0) {
          const r1 = await post("/api/reconcile/edit_target", { line_id: lineId, new_target: newTarget });
          if (!r1.ok) { 
            alert("Failed to update target"); 
            return; 
          }
        }
      }

      // Approve variance
      const r2 = await post("/api/reconcile/approve_variance", { job_id: jobId, note });
      if (!r2.ok) { 
        alert("Failed to approve variance"); 
        return; 
      }

      // Clear form fields
      if (targetInput) targetInput.value = "";
      if ($("recNote")) $("recNote").value = "";

      // Ask Counter Access to refresh (two ways: direct call + event)
      if (window.BMAD_refreshLine) {
        await window.BMAD_refreshLine({ location: loc, warehouse: wh, line_code: line });
      }
      window.dispatchEvent(new CustomEvent("lines-updated", { detail: { location: loc, warehouse: wh, line_code: line }}));

      alert("Variance approved. Counter Access updated.");

      // Force refresh the page to ensure all data is updated
      setTimeout(() => {
        window.location.reload();
      }, 1000);
    } catch (e) {
      console.error("Error in approve variance flow:", e);
      alert("Network error during approval");
    }
  }

  const approveVarBtn = $("recApproveVarBtn");
  if (approveVarBtn) {
    approveVarBtn.addEventListener("click", approveVarianceFlow);
  }

  if (openBtn) openBtn.addEventListener("click", open);
  if (closeBtn) closeBtn.addEventListener("click", close);

  // Auto-refresh pending requests every 5 seconds when TL is logged in
  setInterval(async () => {
    const workspaceSection = $("tlWorkspaceSection");
    if (workspaceSection && !workspaceSection.classList.contains("hidden")) {
      await loadPendingRequests();
    }
  }, 5000);

  // Load TL queue on initial load after login check
  // Use tlAuthenticated flag to ensure it only runs after successful login
  let tlAuthenticated = false; // Assume not authenticated initially
  async function checkAndLoadQueue() {
    // This check might need to be more robust, e.g., checking a session cookie or token
    // For now, we rely on the existence of the workspace section to indicate login
    const workspaceSection = $("tlWorkspaceSection");
    if (workspaceSection && !workspaceSection.classList.contains("hidden")) {
      tlAuthenticated = true;
      await loadTLQueue();
    }
  }

  // Load TL queue
  async function loadTLQueue() {
    if (!tlAuthenticated) return;

    try {
      const response = await fetch('/api/reconcile/inbox');
      const data = await response.json();

      if (data.ok) {
        displayQueue(data.requests);
      } else {
        console.error('Failed to load TL inbox:', data);
      }
    } catch (error) {
      console.error('Error loading TL inbox:', error);
    }
  }

  function displayQueue(requests) {
    const queueContainer = document.getElementById('reconcileQueue');

    if (requests.length === 0) {
      queueContainer.innerHTML = '<p class="text-gray-500 text-center py-8">No pending reconciliation requests</p>';
      return;
    }

    const html = requests.map(req => `
      <div class="bg-white border rounded-lg p-4 shadow-sm">
        <div class="flex justify-between items-start mb-3">
          <div>
            <h3 class="font-semibold text-lg">${req.line_code}</h3>
            <p class="text-gray-600">${req.location} - ${req.warehouse}</p>
          </div>
          <span class="text-sm text-gray-500">${req.created_at}</span>
        </div>

        <div class="mb-3">
          <p class="text-sm"><strong>Requested by:</strong> ${req.requested_by}</p>
        </div>

        <div class="grid grid-cols-3 gap-4 mb-4">
          <div class="text-center">
            <div class="text-xl font-bold text-green-600">${req.requested_qty}</div>
            <div class="text-sm text-gray-600">Requested</div>
          </div>
          <div class="text-center">
            <div class="text-xl font-bold text-orange-600">${req.target_qty}</div>
            <div class="text-sm text-gray-600">Current Target</div>
          </div>
          <div class="text-center">
            <div class="text-xl font-bold text-red-600">${req.requested_qty - req.target_qty}</div>
            <div class="text-sm text-gray-600">Variance</div>
          </div>
        </div>

        <div class="flex space-x-2">
          <button onclick="resolveRequest(${req.request_id}, ${req.requested_qty})" 
                  class="flex-1 bg-green-600 text-white py-2 px-4 rounded hover:bg-green-700">
            Accept Requested Qty
          </button>
          <button onclick="showEditTargetModal(${req.request_id}, ${req.requested_qty})" 
                  class="flex-1 bg-blue-600 text-white py-2 px-4 rounded hover:bg-blue-700">
            Set Custom Target
          </button>
        </div>
      </div>
    `).join('');

    queueContainer.innerHTML = html;
  }

  // Resolve request - accept requested qty
  window.resolveRequest = async function(requestId, requestedQty) {
    try {
      const response = await fetch('/api/reconcile/resolve', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          request_id: requestId
          // new_target not provided, will use requested_qty
        }),
      });

      const result = await response.json();

      if (response.ok && result.ok) {
        showSuccess(`Target updated to ${result.target_qty}. Counter can now submit.`);
        await loadTLQueue();
      } else {
        showError(result.error || 'Failed to resolve reconciliation');
      }
    } catch (error) {
      console.error('Error resolving request:', error);
      showError('Network error occurred');
    }
  };

  // Show edit target modal
  window.showEditTargetModal = function(requestId, requestedQty) {
    const modal = document.getElementById('editTargetModal');
    const input = document.getElementById('newTargetInput');

    input.value = requestedQty;
    modal.style.display = 'block';

    // Store request ID for later use
    modal.dataset.requestId = requestId;
  };

  // Save target
  document.getElementById('saveTargetBtn').addEventListener('click', async () => {
    const modal = document.getElementById('editTargetModal');
    const requestId = modal.dataset.requestId;
    const newTarget = parseInt(document.getElementById('newTargetInput').value);

    if (!newTarget || newTarget < 0) {
      showError('Please enter a valid target quantity');
      return;
    }

    try {
      const response = await fetch('/api/reconcile/resolve', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          request_id: requestId,
          new_target: newTarget
        }),
      });

      const result = await response.json();

      if (response.ok && result.ok) {
        showSuccess(`Target updated to ${result.target_qty}. Counter can now submit.`);
        modal.style.display = 'none';
        await loadTLQueue();
      } else {
        showError(result.error || 'Failed to update target');
      }
    } catch (error) {
      console.error('Error updating target:', error);
      showError('Network error occurred');
    }
  });

  // Close edit target modal
  document.getElementById('closeEditTargetModal').addEventListener('click', () => {
    document.getElementById('editTargetModal').style.display = 'none';
  });

  // Initial check and load
  checkExistingTLSession().then(authenticated => {
    if (authenticated) {
      tlAuthenticated = true;
      checkAndLoadQueue(); // Load queue after successful session check
    }
  });
})();