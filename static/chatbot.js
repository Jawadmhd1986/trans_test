// static/chatbot.js

// --- Mobile 100vh fix ---
window.addEventListener('load', () => {
  const vh = window.innerHeight * 0.01;
  document.documentElement.style.setProperty('--vh', `${vh}px`);
});
window.addEventListener('resize', () => {
  const vh = window.innerHeight * 0.01;
  document.documentElement.style.setProperty('--vh', `${vh}px`);
});

document.addEventListener('DOMContentLoaded', () => {
  // DOM
  const chatBox    = document.getElementById('chat-box');
  const chatToggle = document.querySelector('.chat-toggle');
  const chatClose  = document.getElementById('chat-close');
  const sendBtn    = document.getElementById('chat-send');
  const inputEl    = document.getElementById('chat-input');
  const msgsEl     = document.getElementById('chat-messages');

  // Endpoint
  const AI_ENDPOINT = '/smart_chat';

  // --- Typing configuration (tweak if you like) ---
  const TYPEWRITER_ENABLED   = true;  // turn off to show instantly
  const TYPE_BASE_SPEED_MS   = 10;    // ms per tick (lower = faster)
  const TYPE_MAX_DURATION_MS = 5500;  // cap total typing time per message
  const TYPE_MIN_STRIDE      = 1;     // min characters per tick
  const TYPE_ALLOW_CLICK_SKIP = true; // click the bot bubble to skip typing

  // -----------------------------------------------
  // UI wiring
  chatToggle?.addEventListener('click', () => chatBox.classList.toggle('open'));
  chatClose ?.addEventListener('click', () => chatBox.classList.remove('open'));
  sendBtn   ?.addEventListener('click', sendMessage);
  inputEl   ?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // Message id counter
  let _msgCounter = 0;

  async function sendMessage() {
    const text = (inputEl.value || '').trim();
    if (!text) return;

    appendMessage('user', text);
    inputEl.value = '';

    // "Thinking…" bubble with animated dots so user sees instant feedback
    const thinking = appendMessage('bot', '…', false, true); // returns { id, stop }
    try {
      const res = await fetch(AI_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text })
      });

      let reply = '';
      try {
        const data = await res.json();
        reply = String(data.reply || '').trim();
      } catch {
        // If non-JSON, try raw text
        reply = String(await res.text?.() || '').trim();
      }
      if (!reply) reply = 'Sorry, something went wrong.';

      thinking.stop(); // remove animation
      replaceMessage(thinking.id, reply, /*typewriter*/ true);
    } catch {
      thinking.stop();
      replaceMessage(thinking.id, 'Sorry, something went wrong.', false);
    }
  }

  // --------------------------
  // Message helpers

  // Escape everything, then allow only <a> tags; convert \n to <br>
  function renderWithLinksAndBreaks(text) {
    let out = String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    out = out
      .replace(/&lt;a\b([\s\S]*?)&gt;/gi, '<a$1>')
      .replace(/&lt;\/a&gt;/gi, '</a>');
    out = out.replace(/\n/g, '<br>');
    return out;
  }

  // Append bubble. Returns:
  // - id (string) and
  // - stop() for thinking animation (if isThinking = true)
  function appendMessage(sender, text, typewriter = false, isThinking = false) {
    const id = `msg_${++_msgCounter}`;

    const wrapper = document.createElement('div');
    wrapper.className = `message ${sender}`;
    wrapper.dataset.mid = id;

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    wrapper.appendChild(bubble);
    msgsEl.appendChild(wrapper);
    msgsEl.scrollTop = msgsEl.scrollHeight;

    if (isThinking) {
      // Animated dots: ".", "..", "..."
      let dots = 0;
      bubble.textContent = '…';
      const iv = setInterval(() => {
        dots = (dots + 1) % 4;
        bubble.textContent = dots === 0 ? '…' : '.'.repeat(dots);
        msgsEl.scrollTop = msgsEl.scrollHeight;
      }, 350);
      return {
        id,
        stop: () => {
          clearInterval(iv);
        }
      };
    }

    const safeHTML = renderWithLinksAndBreaks(text);
    if (sender === 'bot' && TYPEWRITER_ENABLED && typewriter) {
      typeHTMLAdaptive(safeHTML, bubble, wrapper);
    } else {
      bubble.innerHTML = safeHTML;
    }
    msgsEl.scrollTop = msgsEl.scrollHeight;
    return { id, stop: () => {} };
  }

  // Replace bubble content by id
  function replaceMessage(id, newText, typewriter = false) {
    const bubble = msgsEl.querySelector(`[data-mid="${id}"] .bubble`);
    const wrapper = msgsEl.querySelector(`[data-mid="${id}"]`);
    if (!bubble || !wrapper) return;

    const safeHTML = renderWithLinksAndBreaks(newText);
    if (TYPEWRITER_ENABLED && typewriter) {
      bubble.innerHTML = ''; // clear before typing
      typeHTMLAdaptive(safeHTML, bubble, wrapper);
    } else {
      bubble.innerHTML = safeHTML;
      msgsEl.scrollTop = msgsEl.scrollHeight;
    }
  }

  // --------------------------
  // Typing logic (adaptive, skips tags immediately, caps total duration)

  function typeHTMLAdaptive(html, el, wrapper) {
    // Compute stride so total time <= TYPE_MAX_DURATION_MS
    const plain = html.replace(/<[^>]+>/g, '');
    const len = plain.length || 1;
    const maxTicks = Math.max(1, Math.floor(TYPE_MAX_DURATION_MS / TYPE_BASE_SPEED_MS));
    const stride = Math.max(TYPE_MIN_STRIDE, Math.ceil(len / maxTicks));

    // Split into tags/text parts
    const parts = html.split(/(<[^>]+>)/g).filter(Boolean);
    let pIndex = 0, cIndex = 0;
    let skipped = false;

    // Allow click to skip typing
    if (TYPE_ALLOW_CLICK_SKIP && wrapper) {
      wrapper.style.cursor = 'pointer';
      const skipHandler = () => {
        skipped = true;
        el.innerHTML = html;
        msgsEl.scrollTop = msgsEl.scrollHeight;
        wrapper.removeEventListener('click', skipHandler);
        wrapper.style.cursor = '';
      };
      wrapper.addEventListener('click', skipHandler);
    }

    function step() {
      if (skipped) return;

      if (pIndex >= parts.length) {
        if (wrapper) wrapper.style.cursor = '';
        return;
      }
      const part = parts[pIndex];

      if (part.startsWith('<')) {
        // inject full tag immediately (keep links intact)
        el.innerHTML += part;
        pIndex++;
        cIndex = 0;
        msgsEl.scrollTop = msgsEl.scrollHeight;
        setTimeout(step, 0);
      } else {
        // type this text part in chunks of 'stride'
        if (cIndex <= part.length) {
          if (!el.lastChild || el.lastChild.nodeName !== 'SPAN' || !el.lastChild.classList.contains('tw')) {
            const span = document.createElement('span');
            span.className = 'tw';
            el.appendChild(span);
          }
          cIndex = Math.min(part.length, cIndex + stride);
          el.lastChild.textContent = part.slice(0, cIndex);
          msgsEl.scrollTop = msgsEl.scrollHeight;
          setTimeout(step, TYPE_BASE_SPEED_MS);
        } else {
          // finalize this part: replace typing span with plain text node
          const span = el.lastChild;
          if (span && span.classList && span.classList.contains('tw')) {
            const txt = document.createTextNode(span.textContent);
            el.replaceChild(txt, span);
          }
          pIndex++;
          cIndex = 0;
          setTimeout(step, 0);
        }
      }
    }
    step();
  }
});
