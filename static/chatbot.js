// static/chatbot.js

// Fix for mobile viewport height
window.addEventListener('load', () => {
  let vh = window.innerHeight * 0.01;
  document.documentElement.style.setProperty('--vh', `${vh}px`);
});
window.addEventListener('resize', () => {
  let vh = window.innerHeight * 0.01;
  document.documentElement.style.setProperty('--vh', `${vh}px`);
});

document.addEventListener('DOMContentLoaded', () => {
  const chatBox    = document.getElementById('chat-box');
  const chatToggle = document.querySelector('.chat-toggle');
  const chatClose  = document.getElementById('chat-close');
  const sendBtn    = document.getElementById('chat-send');
  const inputEl    = document.getElementById('chat-input');
  const msgsEl     = document.getElementById('chat-messages');

  const AI_ENDPOINT     = '/smart_chat';   // one endpoint only (no double round-trip)
  const FAST_MODE       = true;            // show replies immediately
  const TYPE_SPEED_MS   = 6;               // used only if we enable typewriter for short texts
  const TYPE_MAX_CHARS  = 160;             // only typewriter for very short answers

  chatToggle?.addEventListener('click', () => chatBox.classList.toggle('open'));
  chatClose ?.addEventListener('click', () => chatBox.classList.remove('open'));
  sendBtn   ?.addEventListener('click', sendMessage);
  inputEl   ?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  async function sendMessage() {
    const text = (inputEl.value || '').trim();
    if (!text) return;
    appendMessage('user', text);
    inputEl.value = '';

    // show a quick "thinking" bubble so users see instant response
    const thinkingId = appendMessage('bot', '…', false, true);

    try {
      const res = await fetch(AI_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text })
      });
      let data = {};
      try { data = await res.json(); } catch { data = {}; }
      const reply = String(data.reply || 'Sorry, something went wrong.').trim();

      // replace thinking bubble
      replaceMessage(thinkingId, reply);
    } catch {
      replaceMessage(thinkingId, 'Sorry, something went wrong.');
    }
  }

  // --- sanitizer: escape everything, then allow only <a> tags; turn \n into <br> ---
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

  // Append message bubble; returns a message id
  let _msgCounter = 0;
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
      bubble.textContent = text;
      return id;
    }

    const safeHTML = renderWithLinksAndBreaks(text);

    // Fast mode: render immediately
    if (FAST_MODE) {
      bubble.innerHTML = safeHTML;
      msgsEl.scrollTop = msgsEl.scrollHeight;
      return id;
    }

    // Optional typewriter only for short answers
    const short = text.length <= TYPE_MAX_CHARS;
    if (sender === 'bot' && typewriter && short) {
      typeHTML(safeHTML, bubble, TYPE_SPEED_MS);
    } else {
      bubble.innerHTML = safeHTML;
    }
    msgsEl.scrollTop = msgsEl.scrollHeight;
    return id;
  }

  // Replace an existing message bubble by id
  function replaceMessage(id, newText) {
    const node = msgsEl.querySelector(`[data-mid="${id}"] .bubble`);
    if (!node) return;
    const safeHTML = renderWithLinksAndBreaks(newText);
    if (FAST_MODE || newText.length > TYPE_MAX_CHARS) {
      node.innerHTML = safeHTML;
    } else {
      node.innerHTML = '';
      typeHTML(safeHTML, node, TYPE_SPEED_MS);
    }
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }

  // HTML-safe typewriter: tags injected instantly, text chars typed
  function typeHTML(html, el, speed = 8) {
    const parts = html.split(/(<[^>]+>)/g).filter(Boolean);
    let pIndex = 0, cIndex = 0;

    function step() {
      if (pIndex >= parts.length) return;
      const part = parts[pIndex];

      if (part.startsWith('<')) {
        el.innerHTML += part;
        pIndex++; cIndex = 0;
        msgsEl.scrollTop = msgsEl.scrollHeight;
        setTimeout(step, 0);
      } else {
        if (cIndex <= part.length) {
          if (!el.lastChild || el.lastChild.nodeName !== 'SPAN' || !el.lastChild.classList.contains('tw')) {
            const span = document.createElement('span');
            span.className = 'tw';
            el.appendChild(span);
          }
          el.lastChild.textContent = part.slice(0, cIndex);
          msgsEl.scrollTop = msgsEl.scrollHeight;
          cIndex++;
          setTimeout(step, speed);
        } else {
          const span = el.lastChild;
          if (span && span.classList && span.classList.contains('tw')) {
            const txt = document.createTextNode(span.textContent);
            el.replaceChild(txt, span);
          }
          pIndex++; cIndex = 0;
          setTimeout(step, 0);
        }
      }
    }
    step();
  }
});
