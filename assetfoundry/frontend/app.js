/* AssetFoundry local dashboard: no CDN, no framework, no external calls. */
'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  const i18n = window.AF_I18N;
  const t = (phrase, params) => i18n.translate(phrase, params);
  i18n.capture();
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const ICON = (id) => `<svg class="icon"><use href="#${id}"/></svg>`;
  const qs = new URLSearchParams(location.search);
  const token = qs.get('token') || sessionStorage.getItem('assetfoundry-token');
  if (qs.get('token')) {
    sessionStorage.setItem('assetfoundry-token', token);
    history.replaceState({}, '', '/');
  }
  if (!token) { document.body.innerHTML = '<div style="padding:3rem;color:#fff">'+t('Bitte AssetFoundry über Start.bat starten.')+'</div>'; return; }
  let page = 'dashboard', search = '', filter = 'all', currentPage = 0, state = null;
  let loading = false, settingsDirty = false, settingsLoaded = false, workflowLoaded = false;
  let previewRel = '', previewUrl = '', workflowInfo = null, modalResolve = null;

  // Thumbnails are only fetched for visible table rows, never for the entire library.
  // Requests use the existing authenticated API; the session token is not put in URLs.
  const thumbCache = new Map(), thumbPending = new Map(), thumbFailures = new Set();
  const thumbQueue = [];
  const MAX_THUMB_CACHE = 200, MAX_THUMB_PARALLEL = 4;
  let thumbActive = 0, thumbObserver = null, thumbRoot = '';

  function clearThumbnailCache() {
    if (thumbObserver) thumbObserver.disconnect();
    for (const url of thumbCache.values()) URL.revokeObjectURL(url);
    thumbCache.clear();
    thumbFailures.clear();
  }
  function rememberThumbnail(key, url) {
    if (thumbCache.has(key)) URL.revokeObjectURL(thumbCache.get(key));
    thumbCache.set(key, url);
    while (thumbCache.size > MAX_THUMB_CACHE) {
      const stale = [...thumbCache.keys()].find(k =>
        ![...document.querySelectorAll('#assetRows [data-thumb-key]')].some(el => el.dataset.thumbKey === k));
      if (!stale) break;
      const oldURL = thumbCache.get(stale);
      thumbCache.delete(stale);
      URL.revokeObjectURL(oldURL);
    }
  }
  function pumpThumbnails() {
    while (thumbActive < MAX_THUMB_PARALLEL && thumbQueue.length) {
      const job = thumbQueue.shift();
      thumbActive++;
      (async () => {
        try {
          const response = await fetch('/api/thumbnail?rel=' + encodeURIComponent(job.rel), {
            headers: {'X-AssetFoundry-Token': token, 'Accept-Language': i18n.language}, cache: 'no-store'
          });
          if (!response.ok) throw new Error(t('Thumbnail nicht verfügbar'));
          const type = response.headers.get('Content-Type') || '';
          if (!type.startsWith('image/png')) throw new Error(t('Ungültiger Thumbnail-Typ'));
          const blob = await response.blob();
          if (blob.size > 2 * 1024 * 1024) throw new Error(t('Thumbnail zu gross'));
          const url = URL.createObjectURL(blob);
          rememberThumbnail(job.key, url);
          job.resolve(url);
        } catch (err) {
          thumbFailures.add(job.key);
          job.reject(err);
        } finally {
          thumbPending.delete(job.key);
          thumbActive--;
          pumpThumbnails();
        }
      })();
    }
  }
  function getThumbnail(key, rel) {
    if (thumbCache.has(key)) {
      const url = thumbCache.get(key);
      thumbCache.delete(key);
      thumbCache.set(key, url); // LRU: recent items stay warm.
      return Promise.resolve(url);
    }
    if (thumbFailures.has(key)) return Promise.reject(new Error(t('Vorschau nicht verfügbar')));
    if (!thumbPending.has(key)) {
      const promise = new Promise((resolve, reject) => {
        thumbQueue.push({key, rel, resolve, reject});
        pumpThumbnails();
      });
      thumbPending.set(key, promise);
    }
    return thumbPending.get(key);
  }
  function showThumbnail(image, url) {
    if (!image.isConnected) return;
    image.src = url;
    image.hidden = false;
    image.closest('.asset-thumb-frame')?.classList.add('has-image');
  }
  function revealThumbnail(image) {
    const {thumbKey: key, thumbRel: rel} = image.dataset;
    if (thumbCache.has(key)) { showThumbnail(image, thumbCache.get(key)); return; }
    if (thumbFailures.has(key)) {
      image.closest('.asset-thumb-frame')?.setAttribute('title', t('Miniaturansicht nicht verfügbar'));
      return;
    }
    getThumbnail(key, rel).then(url => showThumbnail(image, url)).catch(() => {
      if (image.isConnected) image.closest('.asset-thumb-frame')?.setAttribute('title', t('Miniaturansicht nicht verfügbar'));
    });
  }
  function connectVisibleThumbnails() {
    if (thumbObserver) thumbObserver.disconnect();
    const images = document.querySelectorAll('#assetRows [data-thumb-key]');
    if ('IntersectionObserver' in window) {
      if (!thumbObserver) {
        thumbObserver = new IntersectionObserver(entries => {
          entries.forEach(entry => {
            if (entry.isIntersecting) {
              thumbObserver.unobserve(entry.target);
              const image = entry.target.querySelector('[data-thumb-key]');
              if (image) revealThumbnail(image);
            }
          });
        }, {root: document.querySelector('.table-wrap'), rootMargin: '130px 0px'});
      }
      images.forEach(image => {
        if (thumbCache.has(image.dataset.thumbKey)) showThumbnail(image, thumbCache.get(image.dataset.thumbKey));
        else if (page === 'assets') thumbObserver.observe(image.closest('.asset-thumb-frame'));
      });
    } else if (page === 'assets') {
      images.forEach(revealThumbnail);
    }
  }

  async function request(path, obj) {
    const options = {headers: {'X-AssetFoundry-Token': token, 'Accept-Language': i18n.language}, cache: 'no-store'};
    if (obj !== undefined) { options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(obj); }
    const r = await fetch(path, options);
    if (!r.ok) {
      let e; try { e = await r.json(); } catch (_) { e = null; }
      throw new Error(t(e?.error || `HTTP ${r.status}`));
    }
    return r.headers.get('Content-Type')?.startsWith('application/json') ? r.json() : r;
  }
  function notify(message, error=false) {
    const n = document.createElement('div');
    n.className = 'toast' + (error ? ' error' : '');
    n.textContent = message;
    $('toastArea').prepend(n);
    setTimeout(() => n.remove(), 5000);
  }
  async function action(path, obj, message) {
    try { const r = await request(path, obj); if (message) notify(message); await loadState(); return r; }
    catch (e) { notify(e.message, true); throw e; }
  }
  const labels = { dashboard:'Übersicht', assets:'Asset-Bibliothek', workflow:'Workflow', system:'System & Leistung', logs:'Aktivitätsprotokoll' };
  const L = key => t(labels[key] || key);
  function navigate(next) {
    page = next;
    document.querySelectorAll('.view').forEach(e => e.classList.toggle('active', e.id === 'page-'+next));
    document.querySelectorAll('.nav-item').forEach(e => e.classList.toggle('active', e.dataset.page === next));
    $('crumb').textContent = L(next);
    if (next === 'workflow') inspectWorkflow(true);
    if (next === 'assets') setTimeout(() => $('assetSearch').focus(), 60);
    loadState();
    window.scrollTo({top:0, behavior:'smooth'});
  }
  document.addEventListener('click', e => {
    const nav = e.target.closest('[data-page]');
    if (nav) { navigate(nav.dataset.page); return; }
    if (e.target.closest('[data-command="start"]')) showStart();
    if (e.target.closest('[data-command="diagnostics"]')) exportDiagnostics();
  });
  const statusClass = (s) => s === 'vorhanden' || s === 'fertig' ? 'good' :
    ['fehler','ungültige GLB','gesperrt','unklar','bild-geändert','ausgabe-fehler','zeitlimit'].includes(s) ? 'bad' : 'pending';
  const fmtNum = n => Number(n || 0).toLocaleString(i18n.language==='de'?'de-CH':'en-US');
  const formatElapsed = seconds => {
    if (!Number.isFinite(seconds) || seconds < 0) return '—';
    const n = Math.floor(seconds), hours = Math.floor(n / 3600), min = Math.floor((n % 3600) / 60), sec = n % 60;
    return `${String(hours).padStart(2,'0')}:${String(min).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  };
  function renderTimers() {
    if (!state) return;
    const batch = state.batch || {};
    const hasRun = !!batch.started_at;
    const now = Date.now()/1000;
    const end = batch.ended_at || (state.running ? now : batch.heartbeat_at);
    const duration = hasRun ? Math.max(0, end - batch.started_at) : -1;
    setText('batchElapsed', formatElapsed(duration));
    setText('dashElapsed', formatElapsed(duration));
    setText('batchTimerLabel', t(state.running ? 'Batch läuft seit' : hasRun ? (batch.outcome === 'paused' ? 'Pausierter Batch' : batch.outcome === 'running' ? 'Unterbrochener Batch' : 'Letzter Batch') : 'Noch kein Batch'));
    document.querySelector('.batch-timing-strip')?.classList.toggle('is-running', !!state.running);
    const job = (state.assets || []).find(x => x.rel === batch.current_rel);
    const start = batch.current_started_at || job?.job_started_at || 0;
    setText('jobElapsed', start ? formatElapsed(Math.max(0, (job?.job_finished_at || now) - start)) : '—');
    setText('jobElapsedName', batch.current_rel ? batch.current_rel.split('/').pop() : t(state.running ? 'Warte auf nächsten Job / Ressourcen' : 'Kein Auftrag aktiv'));
    const preview = (state.assets || []).find(x => x.rel === previewRel);
    setText('previewElapsed', preview?.job_started_at ? formatElapsed(Math.max(0, (preview.job_finished_at || (state.running ? now : batch.ended_at || batch.heartbeat_at || now)) - preview.job_started_at)) : '—');
    document.querySelectorAll('[data-job-start]').forEach(el => {
      const started = Number(el.dataset.jobStart), finished = Number(el.dataset.jobEnd);
      const until = finished || (state.running ? now : batch.ended_at || batch.heartbeat_at || now);
      el.textContent = started ? formatElapsed(Math.max(0, until - started)) : '—';
    });
  }
  const fmtSize = bytes => bytes > 1048576 ? (bytes / 1048576).toFixed(1) + ' MB' : (bytes / 1024).toFixed(0) + ' KB';
  function setText(id, value) { $(id).textContent = String(value); }
  function conn(stateName) {
    const dictionary = {online: 'ComfyUI verbunden', offline:'Nicht erreichbar', checking:'Prüfe Verbindung …', unknown:'Nicht geprüft'};
    setText('sidebarConnection', t(dictionary[stateName] || 'Nicht geprüft'));
    $('sidebarDot').className = 'dot ' + (stateName === 'online' ? '' : stateName === 'offline' ? 'error' : 'muted');
  }
  function render(stateData) {
    state = stateData;
    if (!previewRel) setText('previewMeta', t('Wähle ein PNG aus, um Details anzuzeigen.'));
    if (!workflowInfo && !$('workflowPath').value.trim()) {
      setText('workflowSummary', t('Noch kein Workflow eingelesen.'));
      setText('workflowState', t('Noch nicht eingerichtet'));
    }
    const c = state.counts, s = state.settings;
    setText('navAssetCount', fmtNum(c.total)); setText('appVersion', state.version);
    ['mTotal','mExisting','mMissing','mSelected'].forEach((id, i) => setText(id, fmtNum([c.total,c.existing,c.missing,c.selected][i])));
    setText('selectedCount', fmtNum(c.selected)); setText('filteredCount', fmtNum(c.filtered));
    setText('scanState', state.scanning ? t('Scan läuft …') : `${c.total} ${t(c.total===1?'PNG gefunden':'PNGs gefunden')}`);
    setText('pipeRoot', s.root || t('Noch kein Ordner gewählt'));
    setText('pipeWorkflow', s.workflow_path ? s.workflow_path.split(/[/\\]/).pop() : t('API.json fehlt'));
    setText('pipeRun', t(state.running ? 'Verarbeitung läuft …' : c.batch_total ? 'Letzter Durchlauf' : 'Bereit zum Start'));
    $('pipeRootStatus').innerHTML = s.root ? ICON('i-check') : '–';
    $('pipeWorkflowStatus').innerHTML = s.workflow_path ? ICON('i-check') : '–';
    $('pipeRunStatus').innerHTML = state.running ? '●' : '–';
    ['pipeRootStatus','pipeWorkflowStatus','pipeRunStatus'].forEach((id, i) => $(id).classList.toggle('good', i === 0 ? !!s.root : i === 1 ? !!s.workflow_path : state.running));
    setText('batchProgressText', c.batch_done + ' / ' + c.batch_total);
    $('batchProgress').style.width = (c.batch_total ? Math.min(100, c.batch_done * 100 / c.batch_total) : 0) + '%';
    conn(state.connection);
    if (!settingsDirty) loadSettingsToForm(s);
    if (!workflowLoaded) {
      $('workflowPath').value = s.workflow_path || '';
      $('imageInput').value = s.image_input || 'image'; $('prefixInput').value = s.prefix_input || 'filename_prefix';
      workflowLoaded = true;
    }
    if (document.activeElement !== $('folderPath')) $('folderPath').value = s.root || '';
    renderMetrics(state.metrics);
    renderRecent(state.assets.slice(0, 5));
    renderAssets(state);
    renderLogs(state.logs);
    renderTimers();
    const running = state.running || state.scanning;
    ['scanButton','browseFolder','chooseMissing','selectVisible','clearSelection','resetButton','saveWorkflow','saveSettings'].forEach(id => $(id).disabled = running);
    $('pauseButton').disabled = !state.running;
  }
  function renderRecent(assets) {
    const list = $('recentAssetList');
    if (!assets.length) { list.innerHTML = '<div class="log-empty">'+esc(t('Noch keine Assets. Wähle einen Ordner in der Asset-Bibliothek.'))+'</div>'; return; }
    list.innerHTML = assets.map(a => {
      const parts = a.rel.split('/'); const name = parts.pop(), folder = parts.join('/') || t('Hauptordner');
      return `<div class="recent-row"><div class="file-icon">${ICON('i-image')}</div><div class="recent-text"><div class="recent-name">${esc(name)}</div><div class="recent-path">${esc(folder)}</div></div><span class="status-pill ${statusClass(a.status)}">${esc(t(a.status))}</span></div>`;
    }).join('');
  }
  function renderAssets(s) {
    const rows = $('assetRows');
    const root = s.settings.root || '';
    if (root !== thumbRoot) { clearThumbnailCache(); thumbRoot = root; }
    if (!s.assets.length) {
      rows.innerHTML = `<tr><td colspan="6" style="padding:24px;color:#95a4c0">${t(s.scanning ? 'Scanner läuft …' : 'Keine passenden PNG-Dateien gefunden.')}</td></tr>`;
    } else {
      rows.innerHTML = s.assets.map(a => {
        const parts = a.rel.split('/'), name = parts.pop(), folder = parts.join('/') || t('Hauptordner');
        const key = JSON.stringify([root, a.rel, a.size, a.mtime_ns || '']);
        return `<tr class="${previewRel === a.rel ? 'focused' : ''}" data-rel="${esc(a.rel)}">
          <td><input aria-label="${esc(name)} ${esc(t('auswählen'))}" type="checkbox" data-check="1" ${a.selected?'checked':''} ${a.status === 'gesperrt' || s.running || s.scanning ? 'disabled':''}/></td>
          <td title="${esc(a.rel)}"><div class="asset-identity">
            <span class="asset-thumb-frame" title="${esc(t('PNG-Vorschau'))}"><span class="asset-thumb-placeholder" aria-hidden="true">${ICON('i-image')}</span><img class="asset-thumb" alt="" hidden loading="lazy" decoding="async" data-thumb-rel="${esc(a.rel)}" data-thumb-key="${esc(key)}"/></span>
            <span class="asset-file-details"><span class="asset-file-name">${esc(name)}</span><span class="file-subtitle">${esc(folder)}</span></span>
          </div></td>
          <td><span class="status-pill ${statusClass(a.status)}">${esc(t(a.status))}</span></td>
          <td>${esc(fmtSize(a.size))}</td><td class="time-cell mono-time" data-job-start="${Number(a.job_started_at)||0}" data-job-end="${Number(a.job_finished_at)||0}">—</td><td>${ICON('i-chevron')}</td></tr>`;
      }).join('');
    }
    connectVisibleThumbnails();
    const from = s.counts.filtered ? s.page*s.page_size+1 : 0;
    const to = Math.min((s.page+1)*s.page_size,s.counts.filtered);
    setText('pageNumbers', `${from}–${to} ${t('von')} ${fmtNum(s.counts.filtered)}`);
    $('pageBack').disabled = currentPage <= 0;
    $('pageNext').disabled = (currentPage+1)*s.page_size >= s.counts.filtered;
  }
  $('assetRows').addEventListener('change', async e => {
    if (!e.target.matches('input[data-check]')) return;
    const tr = e.target.closest('tr');
    try { await action('/api/toggle', {rel:tr.dataset.rel,selected:e.target.checked}); }
    catch (_) { await loadState(); }
  });
  $('assetRows').addEventListener('click', e => {
    if (e.target.closest('input[type=checkbox]')) return;
    const tr = e.target.closest('tr[data-rel]'); if (tr) showPreview(tr.dataset.rel);
  });
  async function showPreview(rel) {
    previewRel = rel;
    const asset = state?.assets.find(a => a.rel === rel);
    setText('previewName', rel.split('/').pop());
    setText('previewMeta', t(asset?.has_glb?'GLB bereits vorhanden':'GLB fehlt') + ' · ' + rel);
    $('previewImage').hidden = true; $('previewPlaceholder').style.display = 'flex';
    await loadState();
    try {
      const response = await fetch('/api/preview?rel='+encodeURIComponent(rel), {headers:{'X-AssetFoundry-Token': token}});
      if (!response.ok) throw Error(t('Bildvorschau nicht verfügbar.'));
      const blob = await response.blob();
      if (previewRel !== rel) return;
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(blob);
      $('previewImage').src = previewUrl; $('previewImage').hidden = false;
      $('previewPlaceholder').style.display = 'none';
    } catch(e) { notify(e.message, true); }
  }
  function renderMetrics(m) {
    const keys = [['cpu','cpuVal','cpuBar'],['ram','ramVal','ramBar'],['gpu','gpuVal','gpuBar'],['vram','vramVal','vramBar']];
    for (const [key,label,bar] of keys) {
      const val = m[key];
      setText(label, val == null ? '–' : Math.round(val) + '%');
      $(bar).style.width = val == null ? '0%' : Math.min(100,Math.max(0,val)) + '%';
    }
    setText('vramExtra', m.vram_total_mib ? `${m.vram_used_mib} / ${m.vram_total_mib} MiB` : t('Videospeicher'));
    setText('gpuName', m.gpu_name ? m.gpu_name + (m.warning ? ' · ' + m.warning : '') : (m.warning || t('GPU-Metriken nicht verfügbar')));
  }
  function renderLogs(logs) {
    const target = $('logEntries');
    target.innerHTML = logs.length ? logs.slice().reverse().map(item => {
      const time = new Date(item.time*1000).toLocaleTimeString(i18n.language==='de'?'de-CH':'en-US',{hour12:false});
      return `<div class="log-row ${item.level === 'error' ? 'error' : ''}"><time>${esc(time)}</time><span>${esc(i18n.logMessage(item.message))}</span></div>`;
    }).join('') : '<div class="log-empty">'+esc(t('Noch keine Ereignisse.'))+'</div>';
  }
  async function loadState() {
    if (loading) return;
    loading = true;
    try {
      const data = await request('/api/state?' + new URLSearchParams({q: search, filter, page:String(currentPage),size:'75'}));
      if (data.settings?.language && data.settings.language!==i18n.language) await i18n.use(data.settings.language);
      render(data);
      $('crumb').textContent=L(page);
    } catch (e) { console.error(e); }
    finally { loading = false; }
  }
  async function selectMode(mode) {
    try { await action('/api/select', {mode, q:search, filter}, t('Asset-Auswahl aktualisiert.')); }
    catch (_) {}
  }
  $('chooseMissing').onclick = () => selectMode('missing');
  $('selectVisible').onclick = () => selectMode('visible');
  $('clearSelection').onclick = () => selectMode('none');
  $('assetSearch').addEventListener('input', e => {
    search = e.target.value;
    currentPage = 0;
    clearTimeout(window.assetSearchTimer);
    window.assetSearchTimer = setTimeout(loadState, 180);
  });
  $('assetFilter').onchange = e => { filter = e.target.value; currentPage = 0; loadState(); };
  $('pageBack').onclick = () => { currentPage=Math.max(0,currentPage-1);loadState(); };
  $('pageNext').onclick = () => { currentPage++;loadState(); };
  $('topRefresh').onclick = () => { notify(t('Ansicht aktualisiert.')); loadState(); };
  $('scanButton').onclick = async () => { try { await action('/api/scan', {root: $('folderPath').value.trim()}, t('Ordner wird rekursiv gescannt.')); } catch (_) {} };
  async function browse(which) {
    try {
      const data = await request(which === 'folder' ? '/api/choose-folder':'/api/choose-workflow', {});
      if (data.path) {
        if (which === 'folder') { $('folderPath').value=data.path; await action('/api/scan',{root:data.path},t('PNG-Scan gestartet.')); }
        else { $('workflowPath').value=data.path; await inspectWorkflow(); }
      }
    } catch(e) { notify(e.message,true); }
  }
  $('browseFolder').onclick = () => browse('folder');
  $('browseWorkflow').onclick = () => browse('workflow');
  $('sideConnect').onclick = async () => { navigate('system'); try { await action('/api/ping', {}, t('Verbindung wird geprüft …')); } catch(_) {} };
  $('testComfy').onclick = async () => { try { await saveSettings(false); await action('/api/ping',{}, t('Verbindung wird geprüft …')); } catch (_) {} };

  function confirmDialog(title, description, choices, buttonText) {
    $('modalTitle').textContent = title; $('modalDescription').textContent = description;
    const opts = $('modalOptions'); opts.replaceChildren();
    if (choices?.length) {
      opts.className='modal-options';
      choices.forEach((x, i) => {
        const label=document.createElement('label'); label.className='modal-choice';
        const input=document.createElement('input'); input.type='radio'; input.name='modalChoice'; input.value=x.value; input.checked = i === 0;
        label.appendChild(input);label.appendChild(document.createTextNode(x.label));opts.appendChild(label);
      });
    }
    $('modalConfirm').textContent=buttonText||t('Bestätigen');
    $('modalScrim').hidden=false;
    return new Promise(resolve=>{modalResolve=resolve;});
  }
  function closeModal(value) {
    if (modalResolve) { const f=modalResolve;modalResolve=null;$('modalScrim').hidden=true; f(value); }
  }
  $('modalCancel').onclick = () => closeModal(null);
  $('modalClose').onclick = () => closeModal(null);
  $('modalScrim').addEventListener('click', e=> { if(e.target===$('modalScrim'))closeModal(null); });
  $('modalConfirm').onclick = () => { closeModal(document.querySelector('input[name=modalChoice]:checked')?.value || 'confirm'); };
  window.addEventListener('keydown', e=> { if(e.key==='Escape')closeModal(null); });
  async function showStart() {
    if (!state || state.running || state.scanning) { notify(t('Ein Auftrag oder Scan ist bereits aktiv.'),true);return; }
    const c=state.counts;
    if(!c.selected){notify(t('Wähle zuerst mindestens ein Asset.'),true);navigate('assets');return;}
    const options = c.selected_existing ? [
      {value:'skip',label:`${t('Vorhandene GLB überspringen')} (${c.selected_existing}) – ${i18n.language==='de'?'empfohlen':'recommended'}`} ,
      {value:'replace',label:`${t('Ausgewählte GLB sicher überschreiben')} (${c.selected_existing}), ${t('Backup falls aktiviert')}`} 
    ] : [{value:'skip',label:`${c.selected} ${t('ausgewählte PNG(s) ohne vorhandene GLB generieren')}`}] ;
    const choice=await confirmDialog(t('Batch-Verarbeitung starten'),
      i18n.language==='de' ? `${c.selected} Assets ausgewählt, davon ${c.selected_missing} ohne und ${c.selected_existing} mit bestehender GLB. Die Auswahl gilt nur für diese Assets.` : `${c.selected} assets selected: ${c.selected_missing} without an existing GLB and ${c.selected_existing} with one. Only selected assets are affected.`,
      options,t('Generierung starten'));
    if(!choice) return;
    try { await action('/api/start',{replace:choice==='replace'},t('Batch-Generierung gestartet.'));navigate('assets'); }
    catch (_) { navigate('workflow'); }
  }
  $('pauseButton').onclick = async () => { try { await action('/api/pause',{},t('Pause nach dem aktuellen Auftrag angefordert.')); } catch(_){} };
  $('resetButton').onclick = async () => {
    if (!state?.counts.selected) { notify(t('Zuerst Assets auswählen.'),true);return; }
    const choice=await confirmDialog(t('Ausgewählte Jobs zurücksetzen?'),
      t('Gespeicherte ComfyUI-Auftrags-IDs werden entfernt. Vorhandene GLBs bleiben erhalten. Ein alter ComfyUI-Auftrag könnte trotzdem noch laufen – kontrolliere ihn vor einem erneuten Start.'),[], t('Ja, zurücksetzen'));
    if(choice) { try { await action('/api/reset',{},t('Ausgewählte Job-Zuordnungen zurückgesetzt.')); }catch(_){} }
  };
  $('quitApp').onclick = async()=>{
    const choice=await confirmDialog(t('3D Asset Generator Batch beenden?'),
      t('Der lokale Dashboard-Server wird geschlossen. Ein laufender Batch muss vorher pausiert und abgeschlossen werden.'),[], t('Beenden'));
    if(choice) { try { await request('/api/quit',{}); document.body.innerHTML='<div style="padding:4rem;color:white;font:18px Segoe UI">'+t('3D Asset Generator Batch wurde beendet. Dieses Fenster kann geschlossen werden.')+'</div>';  } catch(e) {notify(e.message,true);} }
  };

  async function inspectWorkflow(silent=false) {
    const path=$('workflowPath').value.trim();
    if(!path) return;
    try {
      const info=await request('/api/workflow?' + new URLSearchParams({path}));
      workflowInfo=info;
      setText('workflowSummary', `${info.nodes} ${t('Nodes erkannt')} · ${info.images.length} ${t('Bild-Eingänge')} · ${info.outputs.length} ${t('mögliche GLB-Exporte')}`);
      $('workflowSummary').className='workflow-summary good';
      setText('workflowState',t('Workflow analysiert'));
      const s=state?.settings || {};
      for(const [id,opts,saved] of [['imageNode',info.images,s.image_node],['outputNode',info.outputs,s.output_node]]){
        const select=$(id);const last=select.value;
        select.replaceChildren(new Option(t('Bitte auswählen'),''));
        opts.forEach(o => select.add(new Option(o.label,o.id)));
        const desired = last || saved || (opts.length === 1 ? opts[0].id : '');
        if ([...select.options].some(o=>o.value === desired)) select.value=desired;
      }
      if(!silent) notify(t('Workflow wurde analysiert.'));
    }catch(e){
      workflowInfo=null;
      setText('workflowSummary',t('Workflow nicht lesbar:')+' '+e.message);
      $('workflowSummary').className='workflow-summary';
      setText('workflowState',t('Workflow prüfen'));
      if(!silent)notify(e.message,true);
    }
  }
  $('inspectWorkflow').onclick=()=>inspectWorkflow(false);
  $('saveWorkflow').onclick=async()=>{
    const data={workflow_path:$('workflowPath').value.trim(),image_node:$('imageNode').value,
      output_node:$('outputNode').value,image_input:$('imageInput').value.trim(),prefix_input:$('prefixInput').value.trim()};
    try { await action('/api/workflow',data,t('Workflow-Zuordnung erfolgreich gespeichert.')); }
    catch (_) {}
  };

  const settingMap={comfy_url:'comfyUrl',cooldown_seconds:'cooldown',cpu_limit:'cpuLimit',ram_limit:'ramLimit',
    gpu_limit:'gpuLimit',vram_limit:'vramLimit',gpu_index:'gpuIndex',max_resource_wait_seconds:'resourceTimeout',
    max_job_seconds:'jobTimeout',poll_seconds:'pollInterval',gate_resources:'gateResources',backup_on_replace:'backupOnReplace'};
  const ints=['cpu_limit','ram_limit','gpu_limit','vram_limit','gpu_index','max_resource_wait_seconds','max_job_seconds'];
  const floats=['cooldown_seconds','poll_seconds'];
  function loadSettingsToForm(s){
    if (!settingsLoaded){Object.entries(settingMap).forEach(([k,id]) => {const e=$(id); if(k==='gate_resources'||k==='backup_on_replace')e.checked=!!s[k]; else e.value=s[k];});
      $('cooldownSlider').value=Math.min(60,Number(s.cooldown_seconds));settingsLoaded=true;}
  }
  function collectSettings(){
    const out={};Object.entries(settingMap).forEach(([k,id])=>{
      const e=$(id);if(e.type==='checkbox')out[k]=e.checked;
      else if(ints.includes(k))out[k]=Number(e.value);
      else if(floats.includes(k))out[k]=Number(e.value.replace(',','.'));
      else out[k]=e.value.trim();
    });return out;
  }
  document.querySelectorAll('#page-system input').forEach(el=>el.addEventListener('input',()=>settingsDirty=true));
  $('cooldownSlider').oninput=e=> { $('cooldown').value=e.target.value;settingsDirty=true; };
  $('cooldown').oninput=e=> { $('cooldownSlider').value=Math.min(60,Number(e.target.value)||0);settingsDirty=true; };
  async function saveSettings(showToast=true){
    const r=await action('/api/settings',collectSettings(),showToast?t('Leistungseinstellungen gespeichert.'):null);
    settingsDirty=false;settingsLoaded=false;
    return r;
  }
  $('saveSettings').onclick=async()=>{try{await saveSettings();}catch(_){}};
  async function exportDiagnostics() {
    const choice=await confirmDialog(t('Fehler- & Diagnose-Dump erstellen'),
      t('Enthält Jobstatus, Fehlermeldungen, Systemdaten und Logs – keine PNGs, GLBs oder Workflow-JSON. Dateinamen können in Fehlertexten stehen. Bitte ZIP vor dem Teilen prüfen.'),[
        {value:'safe', label:t('Absolute Ordnerpfade verdecken (empfohlen)')},
        {value:'full', label:t('Vollständige Dateipfade für die Fehlersuche einschliessen')}
      ], t('Diagnose-ZIP erstellen'));
    if (!choice) return;
    try {
      const response=await request('/api/diagnostics', {include_paths:choice==='full'});
      const blob=await response.blob();
      const file=URL.createObjectURL(blob);
      const link=document.createElement('a');
      const date=new Date();
      const stamp=[date.getFullYear(),String(date.getMonth()+1).padStart(2,'0'),String(date.getDate()).padStart(2,'0')].join('')+'-'+[date.getHours(),date.getMinutes(),date.getSeconds()].map(x=>String(x).padStart(2,'0')).join('');
      link.href=file;link.download='3D-Asset-Generator-Batch-Diagnose-'+stamp+'.zip';
      document.body.append(link);link.click();link.remove();
      setTimeout(()=>URL.revokeObjectURL(file),6000);
      notify(t('Fehler- & Diagnose-Dump wird heruntergeladen.'));
    } catch(e) {notify(t('Diagnose-Dump fehlgeschlagen:')+' '+e.message,true);}
  }
  async function changeLanguage(next) {
    if (next===i18n.language) return;
    try {
      await request('/api/language', {language:next});
      await i18n.use(next);
      document.querySelectorAll('[data-language-picker]').forEach(e=>e.value=next);
      $('crumb').textContent=L(page);
      if (state) {state.settings.language=next; render(state);}
      if (workflowInfo) {
        setText('workflowSummary', `${workflowInfo.nodes} ${t('Nodes erkannt')} · ${workflowInfo.images.length} ${t('Bild-Eingänge')} · ${workflowInfo.outputs.length} ${t('mögliche GLB-Exporte')}`);
        setText('workflowState', t('Workflow analysiert'));
      }
    } catch (err) {
      document.querySelectorAll('[data-language-picker]').forEach(e=>e.value=i18n.language);
      notify(err.message, true);
    }
  }
  document.querySelectorAll('[data-language-picker]').forEach(e=>e.addEventListener('change',event=>changeLanguage(event.target.value)));
  $('copyLogs').onclick = async()=>{
    const lines=(state?.logs||[]).map(x => new Date(x.time*1000).toLocaleString(i18n.language==='de'?'de-CH':'en-US')+' ['+x.level+'] '+x.message).join('\n');
    try { await navigator.clipboard.writeText(lines);notify(t('Protokoll kopiert.')); }
    catch(e){notify(t('Kopieren nicht verfügbar:')+' '+e.message,true);}
  };
  loadState();
  setInterval(loadState, 2600);
  setInterval(renderTimers, 1000);
})();
