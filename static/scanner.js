// static/js/scanner.js
(() => {
  const $ = (id) => document.getElementById(id);

  const video = $("video");
  const canvas = $("canvas");
  const ctx = canvas?.getContext("2d", { willReadFrequently: true });

  const picker = $("picker");
  const pickerList = $("pickerList");
  const pickerClose = $("pickerClose");

  const startBtn = $("startBtn");
  const submitBtn = $("submitBtn");
  const manualBtn = $("manualBtn");
  const torchBtn = $("torchBtn");
  const toast = $("toast");

  const locationSel = $("location");
  const warehouseSel = $("warehouse");
  const counterNameInput = $("counterName");
  const skuInput = $("sku");
  const qtyInput = $("qty");
  const codeInput = $("code");
  const recentList = $("recentList");

  let stream = null, rafId = null, torchOn = false, cameraActive = false;
  let lastPickSet = new Set(), pickTimer = null;
  let detectionMode = "none"; // "bd" | "zxing" | "quagga" | "manual"
  let runningZXing = null;

  const showToast = (m, ms=1500)=>{
    if(toast) {
      toast.textContent=m;
      toast.classList.remove("hidden");
      setTimeout(()=>toast.classList.add("hidden"), ms);
    }
  };
  const norm = (s)=> (s||"").toString().trim().toUpperCase().replace(/[^A-Z0-9\-_]/g,"");
  const isMobileUA = ()=> /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent);

  // Persist basic context
  const saveSession = ()=>{
    if(counterNameInput) localStorage.setItem("counterName", counterNameInput.value||"");
    if(locationSel) localStorage.setItem("location", locationSel.value||"");
    if(warehouseSel) localStorage.setItem("warehouse", warehouseSel.value||"");
  };
  const fillWarehouses = ()=>{
    if(!locationSel || !warehouseSel) return;
    const loc = locationSel.value;
    const opts = (window.STOCK_APP?.WAREHOUSES?.[loc]) || [];
    warehouseSel.innerHTML="";
    opts.forEach(w=>{ const o=document.createElement("option"); o.value=o.textContent=w; warehouseSel.appendChild(o); });
  };
  const initContext = ()=>{
    if(!locationSel) return;
    // locations
    const locs = window.STOCK_APP?.LOCATIONS || ["KIZAD","JEBEL_ALI"];
    locationSel.innerHTML="";
    locs.forEach(l=>{ const o=document.createElement("option"); o.value=o.textContent=l; locationSel.appendChild(o); });
    // restore
    if(counterNameInput) counterNameInput.value = localStorage.getItem("counterName") || "";
    locationSel.value = localStorage.getItem("location") || "KIZAD";
    fillWarehouses();
    if(warehouseSel) warehouseSel.value = localStorage.getItem("warehouse") || warehouseSel.value;
  };
  if(locationSel) locationSel.addEventListener("change", ()=>{ fillWarehouses(); saveSession(); });
  if(warehouseSel) warehouseSel.addEventListener("change", saveSession);
  if(counterNameInput) counterNameInput.addEventListener("blur", saveSession);
  initContext();

  async function startCamera(){
    if(cameraActive) return;

    await stopCamera();
    detectionMode="bd";
    showToast("Starting camera...");

    try{
      stream = await navigator.mediaDevices.getUserMedia({
        audio:false,
        video:{
          facingMode:{ideal:"environment"},
          width:{ideal:1280, max:1920}, 
          height:{ideal:720, max:1080},
          frameRate:{ideal:30, max:60}
        }
      });

      if(video) {
        video.srcObject = stream;
        await video.play();
        cameraActive = true;
        showToast("Camera active—initializing scanner...");

        // Update button text
        if(startBtn) startBtn.textContent = "Stop Camera";
      }

      // Turn ON laser
      const scannerWrap = document.getElementById("scannerWrap");
      if (scannerWrap) scannerWrap.classList.add("scanning");

      if(torchBtn) {
        torchBtn.style.display = 'block';
        torchBtn.classList.remove('hidden');
        updateTorchButton();
      }

      // Try detection methods in order
      if("BarcodeDetector" in window){ 
        console.log('Using BarcodeDetector API');
        await runBarcodeDetector(); 
      }
      else if(window.ZXing){ 
        console.log('Using ZXing library');
        detectionMode="zxing"; await runZXing(); 
      }
      else if(window.Quagga){ 
        console.log('Using Quagga library');
        detectionMode="quagga"; await runQuagga(); 
      }
      else { 
        console.log('No barcode scanner available');
        detectionMode="manual"; showToast("Loading scanner libraries..."); 
        // Give libraries time to load then retry
        setTimeout(()=>{
          if(window.ZXing || window.Quagga) startCamera();
          else showToast("Scanner not supported—use Manual Entry");
        }, 2000);
      }
    }catch(e){
      console.error('Camera error:', e); 
      detectionMode="manual"; 
      showToast("Camera access denied—use Manual Entry");

      // Turn OFF laser on error
      const scannerWrap = document.getElementById("scannerWrap");
      if (scannerWrap) scannerWrap.classList.remove("scanning");
    }
  }

  async function stopCamera(){
    if(rafId) cancelAnimationFrame(rafId); rafId=null;
    if(runningZXing && runningZXing.reset){ try{ runningZXing.reset(); }catch{} runningZXing=null; }
    if(window.Quagga && window.Quagga.stop){ try{ window.Quagga.stop(); }catch{} }
    if(stream){ 
      stream.getTracks().forEach(t=>t.stop()); 
      stream=null; 
      torchOn = false;
    }
    if(video) video.srcObject = null;
    cameraActive = false;

    // Update button text
    if(startBtn) startBtn.textContent = "Start Camera";

    // Hide torch button
    if(torchBtn) {
      torchBtn.style.display = 'none';
      torchBtn.classList.add('hidden');
    }

    // Turn OFF laser
    const scannerWrap = document.getElementById("scannerWrap");
    if (scannerWrap) scannerWrap.classList.remove("scanning");
  }

  function updateTorchButton() {
    if(!torchBtn) return;
    torchBtn.textContent = torchOn ? "Torch On" : "Torch Off";
    torchBtn.className = torchOn ? 
      "bg-yellow-600 text-white py-3 px-6 rounded-lg hover:bg-yellow-700 transition duration-200" :
      "bg-gray-600 text-white py-3 px-6 rounded-lg hover:bg-gray-700 transition duration-200";
  }

  async function toggleTorch() {
    if(!stream) {
      showToast("Start camera first");
      return;
    }

    try{
      const track = stream.getVideoTracks()[0];
      const caps = track.getCapabilities?.();

      if(caps && "torch" in caps){
        torchOn = !torchOn;
        await track.applyConstraints({advanced:[{torch:torchOn}]});
        updateTorchButton();
        showToast(torchOn ? "Torch on" : "Torch off");
      } else {
        showToast("Torch not supported on this device");
      }
    } catch(e) {
      console.error("Torch control error:", e);
      showToast("Torch control failed");
    }
  }

  async function runBarcodeDetector(){
    let detector;
    try{
      if(!('BarcodeDetector' in window)){
        throw new Error('BarcodeDetector not supported');
      }
      detector=new window.BarcodeDetector({formats:[
        "aztec","code_128","code_39","code_93","codabar",
        "data_matrix","ean_13","ean_8","itf","pdf417","qr_code","upc_a","upc_e"
      ]});
    }catch(e){
      console.log('BarcodeDetector failed, trying ZXing:', e);
      if(window.ZXing) return runZXing();
      console.log('ZXing not available, trying Quagga');
      if(window.Quagga) return runQuagga();
      detectionMode="manual"; return showToast("Scanner libraries loading...");
    }
    const tick = async ()=>{
      if(!video || !video.videoWidth || !stream) return schedule();
      try{
        const res = await detector.detect(video);
        handleDetections((res||[]).map(r=>r.rawValue));
      }catch{}
      schedule();
    };
    const schedule = ()=>{
      if(!stream) return;
      if(video && "requestVideoFrameCallback" in HTMLVideoElement.prototype){
        video.requestVideoFrameCallback(()=> setTimeout(tick,120));
      } else {
        rafId = requestAnimationFrame(()=> setTimeout(tick,120));
      }
    };
    schedule();
  }

  async function runZXing(){
    if(!window.ZXing) {
      console.log('ZXing not available, trying Quagga');
      return runQuagga();
    }
    try{
      const reader=new ZXing.BrowserMultiFormatReader();
      runningZXing=reader;
      showToast("ZXing scanner active");
      await reader.decodeFromVideoDevice(null, video, (result, err)=>{
        if(result?.text) {
          console.log('ZXing detected:', result.text);
          handleDetections([result.text]);
        }
      });
    }catch(e){
      console.error('ZXing failed:', e);
      runQuagga();
    }
  }

  async function runQuagga(){
    if(!window.Quagga) {
      detectionMode="manual"; 
      showToast("No scanner available—use Manual Entry");
      return;
    }
    return new Promise((resolve)=>{
      console.log('Initializing Quagga scanner...');
      window.Quagga.init({
        inputStream:{ 
          name:"Live", 
          type:"LiveStream", 
          target:video,
          constraints:{ 
            facingMode:"environment",
            width:{min:640, ideal:1280, max:1920},
            height:{min:480, ideal:720, max:1080}
          }
        },
        locator:{ patchSize:"medium", halfSample:true },
        numOfWorkers: Math.min(4, navigator.hardwareConcurrency||2),
        frequency:10, 
        locate:true,
        area: { // Define scanning area
          top:"20%", right:"20%", left:"20%", bottom:"20%"
        },
        decoder:{ 
          readers:[
            "code_128_reader","code_39_reader","code_93_reader",
            "ean_reader","ean_8_reader","upc_reader","upc_e_reader",
            "i2of5_reader","codabar_reader"
          ],
          debug: {
            drawBoundingBox: true,
            showFrequency: true,
            drawScanline: true,
            showPattern: true
          }
        }
      }, (err)=>{
        if(err){ 
          console.error('Quagga init error:', err); 
          detectionMode="manual"; 
          showToast("Camera scanner failed—use Manual Entry"); 
          return resolve(); 
        }
        detectionMode="quagga"; 
        showToast("Quagga scanner active—point camera at barcode");
        window.Quagga.start();
        window.Quagga.onDetected((result)=>{ 
          const code=result?.codeResult?.code; 
          if(code) {
            console.log('Quagga detected:', code);
            handleDetections([code]); 
          }
        });
        resolve();
      });
    });
  }

  function handleDetections(values){
    const distinct = Array.from(new Set(values.map(norm))).filter(Boolean);
    if(!distinct.length) return;
    distinct.forEach(v=> lastPickSet.add(v));
    clearTimeout(pickTimer);
    pickTimer = setTimeout(()=>{
      const arr = Array.from(lastPickSet); lastPickSet.clear();
      if(arr.length===1){
        if(codeInput) codeInput.value=arr[0];
        showToast(`Captured: ${arr[0]}`);
      }
      else if(picker && pickerList){
        pickerList.innerHTML="";
        arr.slice(0,12).forEach(v=>{
          const btn=document.createElement("button"); btn.type="button";
          btn.className="w-full border rounded p-2 text-left"; btn.textContent=v;
          btn.onclick=()=>{
            if(codeInput) codeInput.value=v;
            picker.classList.add("hidden");
            showToast(`Selected: ${v}`);
          };
          pickerList.appendChild(btn);
        });
        picker.classList.remove("hidden");
      }
    }, 200);
  }

  pickerClose?.addEventListener("click", ()=> picker?.classList.add("hidden"));

  async function submit(){
    const payload = {
      location: locationSel?.value || "",
      warehouse: warehouseSel?.value || "",
      counter_name: counterNameInput?.value.trim() || "",
      sku: skuInput?.value.trim() || "",
      serial_or_code: codeInput?.value.trim() || "",
      qty: Math.max(1, parseInt(qtyInput?.value||"1",10)),
      source: (detectionMode==="manual" || !stream) ? "manual" : "scan",
      device: isMobileUA() ? "mobile" : "desktop",
      ua: navigator.userAgent
    };

    if(!payload.location || !payload.warehouse || !payload.counter_name){
      showToast("Fill Location, Warehouse, Name");
      return;
    }
    if(!payload.serial_or_code){
      showToast("Enter a code/serial");
      if(codeInput) codeInput.focus();
      return;
    }

    try{
      const res = await fetch("/api/submit",{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify(payload)
      });

      if(res.status===409){
        // Silent handling of duplicates - no popup for timeline duplicates
        return;
      }
      if(!res.ok) throw new Error("Submit failed");
      onSubmitted(payload);
    }catch(e){
      console.error(e);
      showToast("Submit error");
    }
  }

  function onSubmitted(row){
    showToast("Saved");
    if(recentList) {
      const li=document.createElement("li");
      li.textContent = `• ${row.location}/${row.warehouse} • ${row.sku||"-"} • ${row.serial_or_code} x${row.qty}`;
      recentList.prepend(li);
      while(recentList.children.length>5) recentList.lastChild.remove();
    }
    if(codeInput) codeInput.value="";
    if(qtyInput) qtyInput.value="1";
  }

  document.addEventListener("visibilitychange", async ()=>{
    if(document.hidden){ await stopCamera(); }
    else if(detectionMode!=="manual"){ startCamera(); }
  });

  startBtn?.addEventListener("click", async () => {
    if(cameraActive) {
      await stopCamera();
    } else {
      await startCamera();
    }
  }, {passive:true});

  torchBtn?.addEventListener("click", toggleTorch, {passive:true});

  submitBtn?.addEventListener("click", submit, {passive:true});
  manualBtn?.addEventListener("click", async ()=>{
    await stopCamera();
    detectionMode="manual";
    showToast("Manual mode");
    if(codeInput) {
      codeInput.removeAttribute("readonly");
      codeInput.focus();
    }
  });
  window.addEventListener("beforeunload", stopCamera);
})();

// === Job wiring on /count ===
(() => {
  const getQ = (k)=> new URLSearchParams(location.search).get(k) || "";
  const loc = getQ("location"), wh = getQ("warehouse"), line = getQ("line"), counter = getQ("counter");

  const $ = (id) => document.getElementById(id);
  const jobInfo = $("jobInfo");
  const totalEl = $("scannedTotal");
  const targetEl = $("targetQty");
  const addBtn = $("addBtn");
  const finalizeBtn = $("finalizeBtn");

  // Reconciliation elements
  const reconcileStatus = document.getElementById('reconcileStatus');
  const reconcileResponse = document.getElementById('reconcileResponse');
  const tlResponseNotification = document.getElementById('tlResponseNotification');
  const tlResponseContent = document.getElementById('tlResponseContent');
  const acknowledgeBtn = document.getElementById('acknowledgeResponse');


  let jobState = { job_id:null, line_id:null, scanned_total:0, target_qty:0, status:"open" };
  let currentJobId = null; // To store the current job ID for reconciliation checks

  async function refreshJobState() { // Renamed for clarity and to avoid conflict
    const qs = new URLSearchParams({ location:loc, warehouse:wh, line_code:line, counter });
    const r = await fetch(`/api/job/state?${qs.toString()}`);
    if (!r.ok) { alert("Line not configured or you are not assigned."); history.back(); return; }
    const d = await r.json();
    if (!d.ok) { alert("Line not configured."); history.back(); return; }
    if (!d.is_assigned) { alert("You are not assigned to this line."); history.back(); return; }
    jobState = {
      job_id: d.job_id, line_id: d.line_id,
      scanned_total: d.scanned_total||0, target_qty: d.target_qty||0, status: d.status
    };
    currentJobId = d.job_id; // Update currentJobId
    totalEl.textContent = jobState.scanned_total;
    targetEl.textContent = jobState.target_qty;
    // Enable finalize only if matched or variance approved
    finalizeBtn.disabled = !((jobState.status === "variance_approved") || (jobState.scanned_total === jobState.target_qty));

    // Handle reconciliation status display
    if (jobState.status === "reconcile_requested") {
      if (reconcileStatus) reconcileStatus.classList.remove('hidden');
      if (reconcileResponse) reconcileResponse.classList.add('hidden');
      if (tlResponseNotification) tlResponseNotification.classList.add('hidden');
    } else if (jobState.status === "variance_approved") {
      if (reconcileStatus) reconcileStatus.classList.add('hidden');
      if (reconcileResponse) reconcileResponse.classList.remove('hidden'); // Show TL's response
      if (tlResponseNotification) tlResponseNotification.classList.add('hidden'); // Hide TL notification, response is shown directly
    } else {
      if (reconcileStatus) reconcileStatus.classList.add('hidden');
      if (reconcileResponse) reconcileResponse.classList.add('hidden');
      if (tlResponseNotification) tlResponseNotification.classList.add('hidden');
    }

    // Load recent scans
    loadRecentScans();
  }

  async function addItem() {
    const sku = ($("sku")?.value || "").trim();
    const code = ($("code")?.value || "").trim();
    const qty  = Math.max(1, parseInt(($("qty")?.value || "1"), 10));
    if (!code) { alert("Enter or scan a Serial/Code."); return; }
    const body = {
      job_id: jobState.job_id,
      line_id: jobState.line_id,
      counter_name: counter,
      sku, serial_or_code: code, qty,
      source: (window.stream ? "scan" : "manual")
    };
    const res = await fetch("/api/scan/add", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify(body) });
    if (res.status === 409) { alert("Duplicate: this box was already scanned for this line."); return; }
    if (!res.ok) { alert("Failed to add item."); return; }
    const d = await res.json();
    jobState.scanned_total = d.scanned_total;
    totalEl.textContent = jobState.scanned_total;
    // reset inputs for next box
    $("code").value = ""; $("qty").value = "1";
    // update finalize button availability
    finalizeBtn.disabled = !((jobState.status === "variance_approved") || (jobState.scanned_total === jobState.target_qty));

    // Refresh recent scans
    loadRecentScans();
  }

  async function finalizeJob() {
    const res = await fetch("/api/submit/final", { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ job_id: jobState.job_id }) });
    if (res.status === 412) { alert("Scanned total does not match target. Ask TL for reconciliation."); return; }
    if (!res.ok) { alert("Failed to submit."); return; }
    alert("Submitted. You can view it in Submission Log / Export Excel.");
    location.href = "/log";
  }

  async function loadRecentScans() {
    if (!jobState.job_id) return;

    try {
      const res = await fetch(`/api/recent-scans?job_id=${jobState.job_id}`);
      if (res.ok) {
        const scans = await res.json();
        updateRecentDisplay(scans);
      }
    } catch (e) {
      console.error('Failed to load recent scans:', e);
    }
  }

  function updateRecentDisplay(scans) {
    const container = document.getElementById('recentScans');
    if (!container) return;

    if (scans.length === 0) {
      container.innerHTML = '<p class="text-gray-500 text-center py-4">No recent scans</p>';
      return;
    }

    container.innerHTML = scans.map(item => `
      <div class="flex justify-between items-center p-3 bg-gray-50 rounded border-l-4 border-blue-500">
        <div>
          <p class="font-medium">${item.serial_code}</p>
          <p class="text-sm text-gray-600">${item.sku || 'No SKU'} • Qty: ${item.qty || 1}</p>
        </div>
        <div class="text-right">
          <p class="text-sm text-gray-500">${item.counter_name} • ${item.source}</p>
          <p class="text-xs text-gray-400">${item.time}</p>
        </div>
      </div>
    `).join('');
  }

  // --- Reconciliation specific functions ---

  async function checkReconcileResponse() {
            if (!currentJobId) return;

            try {
                const response = await fetch(`/api/reconcile/check_response?job_id=${currentJobId}`);
                if (response.ok) {
                    const data = await response.json();

                    if (data.resolved && !data.acknowledged) {
                        // Show TL response notification
                        const tlNotification = document.getElementById('tlResponseNotification');
                        const tlContent = document.getElementById('tlResponseContent');
                        const reconcileStatus = document.getElementById('reconcileStatus');

                        if (tlContent) {
                            tlContent.innerHTML = `
                                <div class="mb-2">
                                    <strong>Response Time:</strong> ${data.resolved_at}
                                </div>
                                <div class="mb-2">
                                    <strong>Action Taken:</strong> ${data.response}
                                </div>
                                <div class="text-xs text-green-600">
                                    Click "Acknowledge & Continue" to proceed with your work.
                                </div>
                            `;
                        }

                        if (tlNotification) {
                            tlNotification.classList.remove('hidden');
                        }

                        if (reconcileStatus) {
                            reconcileStatus.classList.add('hidden');
                        }

                        // Play notification sound and vibrate if available
                        try {
                            // Simple beep sound
                            const audioContext = new (window.AudioContext || window.webkitAudioContext)();
                            const oscillator = audioContext.createOscillator();
                            const gainNode = audioContext.createGain();

                            oscillator.connect(gainNode);
                            gainNode.connect(audioContext.destination);

                            oscillator.frequency.value = 800;
                            oscillator.type = 'sine';

                            gainNode.gain.setValueAtTime(0.3, audioContext.currentTime);
                            gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.5);

                            oscillator.start(audioContext.currentTime);
                            oscillator.stop(audioContext.currentTime + 0.5);
                        } catch (e) {}

                        // Vibrate if supported
                        if (navigator.vibrate) {
                            navigator.vibrate([200, 100, 200]);
                        }

                        // Show toast notification
                        showToast('Team Leader has responded to your reconciliation request!', 'success');
                    } else if (data.resolved && data.acknowledged) {
                        // If already acknowledged by this user, ensure notifications are hidden
                        if (tlNotification) tlNotification.classList.add('hidden');
                        if (reconcileStatus) reconcileStatus.classList.add('hidden');
                    }
                }
            } catch (error) {
                // Silently continue if check fails
            }
        }

  // Wire buttons if they exist
  if (addBtn) addBtn.addEventListener("click", addItem);
  if (finalizeBtn) finalizeBtn.addEventListener("click", finalizeJob);

  // Add acknowledge response button handler
  if (acknowledgeBtn) {
      acknowledgeBtn.addEventListener('click', async function() {
          const tlNotification = document.getElementById('tlResponseNotification');
          const reconcileStatus = document.getElementById('reconcileStatus'); // Assuming this should also be hidden

          if (tlNotification) tlNotification.classList.add('hidden');
          if (reconcileStatus) reconcileStatus.classList.add('hidden');

          // Mark the response as acknowledged by this counter
          try {
              await fetch('/api/reconcile/acknowledge', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ job_id: currentJobId })
              });
          } catch (error) {
              console.error("Failed to acknowledge reconciliation response:", error);
              showToast("Could not acknowledge response. Please try again.", 'error');
              return; // Prevent refreshing state if acknowledgement failed
          }

          // Force refresh the job state to get updated target/status
          await refreshJobState(); // Use await as refreshJobState is async

          showToast('Response acknowledged. You can continue working.', 'success');
      });
  }

  // Initial state refresh and start checking for responses
  refreshJobState().then(() => {
    if (jobState.status === "reconcile_requested" || jobState.status === "variance_approved") {
      // Check for reconciliation response every 2 seconds for faster notifications
      setInterval(checkReconcileResponse, 2000);
    }
  });
})();

