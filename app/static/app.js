/**
 * Dashboard client for the voice agent.
 *
 * Audio path (both directions are raw PCM16 @ 24kHz, base64 over one socket —
 * the same format the backend negotiates with OpenAI, so nothing transcodes):
 *
 *   mic -> AudioWorklet -> Float32 -> PCM16 -> base64 -> ws
 *   ws  -> base64 -> PCM16 -> Float32 -> scheduled AudioBuffers -> speakers
 */

const SAMPLE_RATE = 24000;
const BAR_COUNT = 28;

const els = {
  micBtn: document.getElementById('mic-btn'),
  hint: document.getElementById('orb-hint'),
  timer: document.getElementById('timer'),
  connPill: document.getElementById('conn-pill'),
  connDot: document.getElementById('conn-dot'),
  connText: document.getElementById('conn-text'),
  toast: document.getElementById('toast'),
  transcript: document.getElementById('transcript'),
  transcriptEmpty: document.getElementById('transcript-empty'),
  clearTranscript: document.getElementById('clear-transcript'),
  callsBody: document.getElementById('calls-body'),
  refreshCalls: document.getElementById('refresh-calls'),
  leadsList: document.getElementById('leads-list'),
  leadsCount: document.getElementById('leads-count'),
  modelLabel: document.getElementById('model-label'),
  phonePill: document.getElementById('phone-pill'),
  phoneEnabled: document.getElementById('phone-enabled'),
  phoneDisabled: document.getElementById('phone-disabled'),
  outboundForm: document.getElementById('outbound-form'),
  outboundNumber: document.getElementById('outbound-number'),
  barsUser: document.getElementById('bars-user'),
  barsAgent: document.getElementById('bars-agent'),
};

/** Live session state. Everything here is torn down by stopSession(). */
let ws = null;
let micStream = null;
let micCtx = null;
let playCtx = null;
let micAnalyser = null;
let agentAnalyser = null;
let playCursor = 0;          // next start time on the playback clock
let scheduledSources = [];   // so barge-in can stop queued audio
let timerHandle = null;
let vizHandle = null;
let startedAt = 0;
let active = false;

/* ------------------------------------------------------------------ audio */

function floatToPCM16Base64(float32) {
  const pcm = new DataView(new ArrayBuffer(float32.length * 2));
  for (let i = 0; i < float32.length; i++) {
    const clamped = Math.max(-1, Math.min(1, float32[i]));
    pcm.setInt16(i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  }
  return bytesToBase64(new Uint8Array(pcm.buffer));
}

function bytesToBase64(bytes) {
  let binary = '';
  const CHUNK = 0x8000; // avoid blowing the argument limit on large buffers
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

function base64ToFloat32(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

  const samples = new Int16Array(bytes.buffer);
  const float32 = new Float32Array(samples.length);
  for (let i = 0; i < samples.length; i++) float32[i] = samples[i] / 0x8000;
  return float32;
}

/** Queue an agent audio chunk so chunks play gapless, back to back. */
function playChunk(b64) {
  const float32 = base64ToFloat32(b64);
  if (!float32.length) return;

  const buffer = playCtx.createBuffer(1, float32.length, SAMPLE_RATE);
  buffer.copyToChannel(float32, 0);

  const source = playCtx.createBufferSource();
  source.buffer = buffer;
  source.connect(agentAnalyser);

  // A small lead-in absorbs network jitter without an audible delay.
  const now = playCtx.currentTime;
  if (playCursor < now) playCursor = now + 0.04;

  source.start(playCursor);
  playCursor += buffer.duration;

  scheduledSources.push(source);
  source.onended = () => {
    scheduledSources = scheduledSources.filter((s) => s !== source);
  };
}

/** Barge-in: drop everything still queued so the agent stops mid-sentence. */
function stopPlayback() {
  scheduledSources.forEach((s) => {
    try { s.stop(); } catch { /* already finished */ }
  });
  scheduledSources = [];
  playCursor = 0;
}

/* ------------------------------------------------------------- visualizer */

function buildBars(container) {
  container.innerHTML = '';
  for (let i = 0; i < BAR_COUNT; i++) container.appendChild(document.createElement('i'));
}

function renderBars(container, analyser, data) {
  if (!analyser) return;
  analyser.getByteFrequencyData(data);
  const bars = container.children;
  const step = Math.floor(data.length / bars.length);
  for (let i = 0; i < bars.length; i++) {
    const value = data[i * step] / 255;
    bars[i].style.height = `${3 + value * 23}px`;
    bars[i].style.opacity = String(0.28 + value * 0.72);
  }
}

function startVisualizer() {
  const micData = new Uint8Array(micAnalyser.frequencyBinCount);
  const agentData = new Uint8Array(agentAnalyser.frequencyBinCount);
  const tick = () => {
    renderBars(els.barsUser, micAnalyser, micData);
    renderBars(els.barsAgent, agentAnalyser, agentData);
    vizHandle = requestAnimationFrame(tick);
  };
  tick();
}

function resetBars() {
  [els.barsUser, els.barsAgent].forEach((c) => {
    for (const bar of c.children) {
      bar.style.height = '3px';
      bar.style.opacity = '0.28';
    }
  });
}

/* --------------------------------------------------------------- session */

function setStatus(state, text) {
  els.connPill.dataset.state = state;
  els.connText.textContent = text;
}

function showToast(message, kind = 'info') {
  els.toast.textContent = message;
  els.toast.dataset.kind = kind;
  els.toast.hidden = false;
}

function hideToast() {
  els.toast.hidden = true;
}

async function startSession() {
  document.body.classList.add('session-connecting');
  setStatus('connecting', 'Connecting');
  hideToast();

  try {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
  } catch (err) {
    document.body.classList.remove('session-connecting');
    setStatus('error', 'Mic blocked');
    showToast('Microphone permission denied. Allow mic access and try again.', 'error');
    return;
  }

  micCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
  playCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
  // Browsers start contexts suspended until a user gesture; the click qualifies.
  await Promise.all([micCtx.resume(), playCtx.resume()]);

  agentAnalyser = playCtx.createAnalyser();
  agentAnalyser.fftSize = 128;
  agentAnalyser.connect(playCtx.destination);

  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${protocol}//${location.host}/browser/session`);

  ws.onopen = async () => {
    try {
      await micCtx.audioWorklet.addModule('/static/mic-worklet.js');

      const source = micCtx.createMediaStreamSource(micStream);
      micAnalyser = micCtx.createAnalyser();
      micAnalyser.fftSize = 128;

      const capture = new AudioWorkletNode(micCtx, 'mic-capture');
      capture.port.onmessage = (event) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ event: 'audio', payload: floatToPCM16Base64(event.data) }));
        }
      };

      // The worklet has no audible output, but the graph only pulls frames if
      // it reaches a destination — route it through a muted gain node.
      const mute = micCtx.createGain();
      mute.gain.value = 0;
      source.connect(micAnalyser);
      source.connect(capture);
      capture.connect(mute);
      mute.connect(micCtx.destination);
    } catch (err) {
      showToast(`Could not start audio capture: ${err.message}`, 'error');
      setStatus('error', 'Audio error');
      stopSession();
      return;
    }

    active = true;
    document.body.classList.remove('session-connecting');
    document.body.classList.add('session-live');
    setStatus('live', 'Live');
    els.hint.textContent = 'Listening — speak naturally, interrupt any time';
    els.micBtn.setAttribute('aria-label', 'End session');

    startedAt = Date.now();
    timerHandle = setInterval(updateTimer, 1000);
    startVisualizer();
  };

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    switch (msg.event) {
      case 'audio':   playChunk(msg.payload); break;
      case 'clear':   stopPlayback(); break;
      case 'transcript': appendTurn(msg.role, msg.text); break;
      case 'tool':    handleToolEvent(msg); break;
      case 'error':   showToast(msg.message, 'error'); setStatus('error', 'Error'); break;
    }
  };

  ws.onerror = () => {
    // Fires before onclose, so this state survives the teardown that follows.
    showToast('Connection failed. Is the server running and OPENAI_API_KEY set?', 'error');
    setStatus('error', 'Error');
  };

  // Always tear down, even if the socket died before it ever opened — otherwise
  // the mic stays hot and the orb is stuck in its non-interactive state.
  ws.onclose = () => stopSession({ keepStatus: !active });
}

/**
 * Tear down every resource a session holds. Safe to call from any state —
 * including a session that never finished connecting.
 *
 * `keepStatus` preserves an error already shown in the status pill, so a
 * failed connection doesn't get overwritten with a reassuring "Idle".
 */
function stopSession({ keepStatus = false } = {}) {
  active = false;

  if (ws) {
    if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ event: 'stop' }));
    ws.onclose = null; // don't re-enter through the close handler
    ws.close();
    ws = null;
  }
  if (micStream) { micStream.getTracks().forEach((t) => t.stop()); micStream = null; }
  stopPlayback();
  if (micCtx) { micCtx.close(); micCtx = null; }
  if (playCtx) { playCtx.close(); playCtx = null; }

  clearInterval(timerHandle);
  cancelAnimationFrame(vizHandle);
  micAnalyser = agentAnalyser = null;
  resetBars();

  document.body.classList.remove('session-live', 'session-connecting');
  if (!keepStatus) {
    setStatus('idle', 'Idle');
    els.hint.textContent = 'Click the mic and just start talking';
  } else {
    els.hint.textContent = 'Session ended before it started — see the message below';
  }
  els.micBtn.setAttribute('aria-label', 'Start talking to the agent');
  els.timer.textContent = '00:00';

  loadCalls();
  loadLeads();
}

function updateTimer() {
  const elapsed = Math.floor((Date.now() - startedAt) / 1000);
  const mm = String(Math.floor(elapsed / 60)).padStart(2, '0');
  const ss = String(elapsed % 60).padStart(2, '0');
  els.timer.textContent = `${mm}:${ss}`;
}

/* ------------------------------------------------------------ transcript */

function appendTurn(role, text) {
  els.transcriptEmpty.hidden = true;

  const turn = document.createElement('div');
  turn.className = `turn ${role}`;

  const who = document.createElement('span');
  who.className = 'who';
  who.textContent = role === 'user' ? 'You' : 'Agent';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = text;

  turn.append(who, bubble);
  els.transcript.appendChild(turn);
  els.transcript.scrollTop = els.transcript.scrollHeight;
}

function handleToolEvent(msg) {
  if (msg.name === 'save_lead') {
    showToast('Lead captured from this conversation.', 'tool');
    loadLeads();
  } else {
    showToast(`Tool run: ${msg.name}`, 'tool');
  }
}

/* ------------------------------------------------------------------ data */

async function loadCalls() {
  try {
    const calls = await fetch('/calls?limit=10').then((r) => r.json());
    if (!calls.length) {
      els.callsBody.innerHTML = '<tr class="empty-row"><td colspan="5">No calls yet.</td></tr>';
      return;
    }
    els.callsBody.innerHTML = calls.map((call) => `
      <tr>
        <td><span class="tag ${call.direction}">${call.direction}</span></td>
        <td>${escapeHtml(call.from_number || '—')}</td>
        <td>${escapeHtml(call.status)}</td>
        <td>${call.transcripts.length}</td>
        <td>${formatTime(call.started_at)}</td>
      </tr>`).join('');
  } catch {
    /* leave the previous rows in place if a refresh fails */
  }
}

async function loadLeads() {
  try {
    const leads = await fetch('/calls/leads?limit=10').then((r) => r.json());
    els.leadsCount.textContent = leads.length;
    if (!leads.length) {
      els.leadsList.innerHTML =
        '<li class="empty-row">The agent saves a lead here when a caller shares their details.</li>';
      return;
    }
    els.leadsList.innerHTML = leads.map((lead) => `
      <li>
        <div class="lead-name">${escapeHtml(lead.name || 'Unnamed caller')}</div>
        <div class="lead-meta">${escapeHtml(lead.phone || 'no number')} · ${formatTime(lead.created_at)}</div>
        <div class="lead-meta">${escapeHtml(lead.reason || '')}</div>
      </li>`).join('');
  } catch {
    /* non-fatal */
  }
}

async function loadInfo() {
  try {
    const info = await fetch('/api/info').then((r) => r.json());
    els.modelLabel.textContent = `${info.model} · voice: ${info.voice}`;

    if (!info.openai_configured) {
      showToast('OPENAI_API_KEY is missing from .env — add it and restart the server.', 'error');
      setStatus('error', 'No API key');
      els.micBtn.disabled = true;
      els.hint.textContent = 'Add your OpenAI API key to .env to start talking';
    }

    if (info.twilio_configured) {
      els.phoneEnabled.hidden = false;
      els.phoneDisabled.hidden = true;
      if (info.phone_number) {
        els.phonePill.textContent = info.phone_number;
        els.phonePill.hidden = false;
      }
    }
  } catch {
    els.modelLabel.textContent = 'Backend unreachable';
  }
}

function formatTime(iso) {
  // Timestamps are UTC; the Z suffix keeps browsers from reading them as local.
  const date = new Date(/[Z+]/.test(iso) ? iso : `${iso}Z`);
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function escapeHtml(value) {
  const div = document.createElement('div');
  div.textContent = String(value);
  return div.innerHTML;
}

/* -------------------------------------------------------------- wire-up */

els.micBtn.addEventListener('click', () => (active ? stopSession() : startSession()));

els.clearTranscript.addEventListener('click', () => {
  els.transcript.querySelectorAll('.turn').forEach((n) => n.remove());
  els.transcriptEmpty.hidden = false;
});

els.refreshCalls.addEventListener('click', () => { loadCalls(); loadLeads(); });

els.outboundForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = els.outboundForm.querySelector('button');
  button.disabled = true;
  try {
    const res = await fetch('/calls/outbound', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to_number: els.outboundNumber.value }),
    });
    const body = await res.json();
    showToast(res.ok ? `Calling ${els.outboundNumber.value}…` : body.detail, res.ok ? 'info' : 'error');
    if (res.ok) setTimeout(loadCalls, 2500);
  } catch {
    showToast('Could not reach the server.', 'error');
  } finally {
    button.disabled = false;
  }
});

// Closing the tab mid-session should hang up rather than leak an open socket.
window.addEventListener('beforeunload', () => { if (active) stopSession(); });

buildBars(els.barsUser);
buildBars(els.barsAgent);
loadInfo();
loadCalls();
loadLeads();
