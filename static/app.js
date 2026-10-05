const $ = (id) => document.getElementById(id);
const names = {codex: 'Codex', shell: 'Терминал', login: 'Вход в ChatGPT', resume: 'История Codex'};
let sessions = [], active = null, socket = null, terminal, fit, reconnectTimer, refreshTimer, toastTimer;
let generation = 0;

async function api(path, options = {}) {
  const response = await fetch('/api' + path, {headers: {'Content-Type': 'application/json'}, ...options});
  if (response.status === 401 && path !== '/login') { showLogin(); throw new Error('Войдите в пространство'); }
  if (!response.ok) throw new Error((await response.text()) || 'Ошибка запроса');
  return response.json();
}
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 6000); }
function disconnect() { generation++; clearTimeout(reconnectTimer); if (socket) { socket.onclose = null; socket.close(); socket = null; } }
function showLogin() { disconnect(); clearInterval(refreshTimer); $('app').hidden = true; $('login-screen').hidden = false; if ($('create-dialog').open) $('create-dialog').close(); $('password').focus(); }
function connection(label, ok = true) { $('connection-label').textContent = label; $('connection-dot').classList.toggle('offline', !ok); }

async function loadSessions() { sessions = await api('/sessions'); renderSessions(); }
function renderSessions() {
  const list = $('session-list'); list.replaceChildren();
  $('session-count').textContent = sessions.filter(s => !s.exited).length;
  if (!sessions.length) { const p = document.createElement('p'); p.className = 'empty-list'; p.textContent = 'Здесь появятся ваши сессии'; list.append(p); }
  for (const s of sessions) {
    const button = document.createElement('button'); button.className = 'session-item' + (active === s.id ? ' selected' : '');
    const icon = document.createElement('span'); icon.className = 'session-icon'; icon.textContent = s.kind === 'shell' ? '>_' : '✳';
    const text = document.createElement('span'); text.textContent = names[s.kind];
    const sub = document.createElement('small'); sub.textContent = (s.exited ? 'Завершена · ' : '') + new Date(s.created * 1000).toLocaleTimeString('ru', {hour: '2-digit', minute: '2-digit'}) + ' · ' + s.cwd;
    text.append(sub); button.append(icon, text); button.onclick = () => openSession(s.id); list.append(button);
  }
}
function send(data) { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(data)); }
function resize() { if (!terminal || $('terminal-view').hidden) return; fit.fit(); send({type: 'resize', cols: terminal.cols, rows: terminal.rows}); }
function initTerminal() {
  if (terminal) return;
  terminal = new Terminal({cursorBlink: true, fontSize: 14, fontFamily: 'Mono, monospace', scrollback: 10000, allowProposedApi: false, theme: {background: '#101312', foreground: '#dbe5de', cursor: '#baf5ce', selectionBackground: '#355840', black: '#19201b', red: '#ed9b93', green: '#baf5ce', yellow: '#e8cc91', blue: '#93bde5', magenta: '#c5a6dd', cyan: '#a1d5d1', white: '#e4ebe7', brightBlack: '#7f9486'}});
  fit = new FitAddon.FitAddon(); terminal.loadAddon(fit); terminal.open($('terminal'));
  terminal.onData(data => send({type: 'input', data}));
  new ResizeObserver(resize).observe($('terminal'));
}
function openSession(id) {
  const session = sessions.find(s => s.id === id); if (!session) return;
  disconnect(); active = id; localStorage.setItem('codex-active', id);
  $('welcome').hidden = true; $('terminal-view').hidden = false; $('exit-banner').hidden = true;
  $('page-title').textContent = names[session.kind]; $('terminal-title').textContent = names[session.kind]; $('terminal-path').textContent = session.cwd;
  initTerminal(); terminal.reset(); resize(); renderSessions(); connect(id); terminal.focus();
}
function connect(id) {
  const current = generation;
  connection('Подключение…', false);
  socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/sessions/${id}/ws`);
  socket.onopen = () => { if (current !== generation) return; connection('Соединение активно'); resize(); };
  socket.onmessage = event => {
    if (current !== generation) return;
    const data = JSON.parse(event.data);
    if (data.type === 'output') terminal.write(data.data);
    if (data.type === 'exit') { $('exit-banner').textContent = `Сессия завершена · код ${data.code}. Создайте новую сессию или продолжите Codex из истории.`; $('exit-banner').hidden = false; connection('Сессия завершена', false); loadSessions().catch(() => {}); }
  };
  socket.onclose = event => {
    if (current !== generation) return;
    if (event.code === 4001) { showLogin(); return; }
    connection('Переподключение…', false);
    reconnectTimer = setTimeout(async () => {
      try { await loadSessions(); if (current !== generation) return; if (!sessions.some(s => s.id === id)) { overview(); return; } terminal.reset(); connect(id); }
      catch { if (current === generation) socket.onclose({code: 0}); }
    }, 1800);
  };
}
function overview() { disconnect(); active = null; localStorage.removeItem('codex-active'); $('terminal-view').hidden = true; $('welcome').hidden = false; $('page-title').textContent = 'Обзор'; connection('Готов к работе'); renderSessions(); }
async function updateStatus() {
  const status = await api('/status'); $('version').textContent = `Codex CLI ${status.version}`;
  $('account-status').textContent = status.authenticated ? 'Codex авторизован · готов к работе' : 'Для работы Codex войдите в свой аккаунт';
  $('account-dot').classList.toggle('offline', !status.authenticated); $('auth-codex').textContent = status.authenticated ? 'Сменить аккаунт ↗' : 'Войти в ChatGPT ↗';
}
async function start() {
  await loadSessions(); $('login-screen').hidden = true; $('app').hidden = false;
  const previous = localStorage.getItem('codex-active'); if (sessions.some(s => s.id === previous)) openSession(previous); else overview();
  updateStatus().catch(e => toast(e.message)); clearInterval(refreshTimer); refreshTimer = setInterval(() => { loadSessions().catch(() => {}); updateStatus().catch(() => {}); }, 15000);
}
function createDialog(kind = 'codex') { $('session-kind').value = kind; $('create-error').textContent = ''; $('create-dialog').showModal(); }
$('login-form').onsubmit = async event => { event.preventDefault(); const button = event.submitter; button.disabled = true; $('login-error').textContent = ''; try { await api('/login', {method: 'POST', body: JSON.stringify({password: $('password').value})}); $('password').value = ''; await start(); } catch (e) { $('login-error').textContent = e.message; } finally { button.disabled = false; } };
$('create-form').onsubmit = async event => { event.preventDefault(); const button = event.submitter; button.disabled = true; try { const s = await api('/sessions', {method: 'POST', body: JSON.stringify({kind: $('session-kind').value, cwd: $('session-cwd').value, cols: terminal?.cols || 100, rows: terminal?.rows || 30})}); await loadSessions(); $('create-dialog').close(); openSession(s.id); } catch(e) { $('create-error').textContent = e.message; } finally { button.disabled = false; } };
$('new-session').onclick = () => createDialog(); $('start-codex').onclick = () => createDialog(); $('start-shell').onclick = () => createDialog('shell'); $('auth-codex').onclick = () => createDialog('login'); $('cancel-dialog').onclick = () => $('create-dialog').close();
$('interrupt').onclick = () => { send({type: 'input', data: '\x03'}); terminal.focus(); }; $('escape').onclick = () => { send({type: 'input', data: '\x1b'}); terminal.focus(); }; $('enter').onclick = () => { send({type: 'input', data: '\r'}); terminal.focus(); };
for (const [id, delta] of [['font-down', -1], ['font-up', 1]]) $(id).onclick = () => { terminal.options.fontSize = Math.max(10, Math.min(24, terminal.options.fontSize + delta)); resize(); };
$('close-session').onclick = async () => { if (!active || !confirm('Завершить эту сессию? Запущенные в ней процессы будут остановлены.')) return; try { await api('/sessions/' + active, {method: 'DELETE'}); overview(); await loadSessions(); } catch(e) { toast(e.message); } };
$('logout').onclick = async () => { try { await api('/logout', {method: 'POST', body: '{}'}); showLogin(); } catch(e) { toast(e.message); } };
document.addEventListener('keydown', event => { if (event.key.toLowerCase() === 'n' && !event.ctrlKey && !event.metaKey && !event.altKey && !$('app').hidden && !$('create-dialog').open && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName) && !document.activeElement.closest('.xterm')) { event.preventDefault(); createDialog(); } });
start().catch(() => showLogin());
