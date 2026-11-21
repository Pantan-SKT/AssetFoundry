/* Offline UI localization: source text is German, with a separate English catalog.
   Only UI strings are translated; asset names, filesystem paths and diagnostic data never are.
   New languages can be added under frontend/locales/ without changing the batch engine. */
'use strict';
window.AF_I18N = (() => {
  const locales = new Map();
  let language = 'de';
  let nodes = null;
  const attributes = ['title', 'placeholder', 'aria-label', 'alt'];
  const dynamic = new Set(['crumb','sidebarConnection','scanState','pipeRoot','pipeWorkflow','pipeRun',
    'batchTimerLabel','jobElapsedName','previewMeta','previewName','workflowSummary','workflowState',
    'gpuName','vramExtra','modalTitle','modalDescription','modalConfirm','recentAssetList',
    'assetRows','logEntries','pageNumbers','toastArea']);
  function capture() {
    if (nodes) return;
    nodes = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      const parent = node.parentElement;
      if (!parent || parent.closest('svg,script,style') || parent.closest('[data-no-i18n]') ||
          [...dynamic].some(id => parent.closest('#'+id))) continue;
      const raw = node.nodeValue;
      if (raw.trim() && /[A-Za-zÄÖÜäöüß]/.test(raw)) nodes.push({node, raw});
    }
    document.querySelectorAll('[title],[placeholder],[aria-label],[alt]').forEach(el => {
      if (el.closest('svg') || el.closest('[data-no-i18n]')) return;
      attributes.forEach(attr => {
        if (el.hasAttribute(attr)) nodes.push({element:el,attr,raw:el.getAttribute(attr)});
      });
    });
  }
  const translate = (source, params) => {
    const dict = locales.get(language) || {};
    let out = dict[source] || source;
    if (params) for (const [key,value] of Object.entries(params)) out=out.replaceAll('{'+key+'}',String(value));
    return out;
  };
  function apply() {
    capture();
    for (const entry of nodes) {
      const orig = entry.raw;
      const translated = translate(orig.trim());
      const value = orig.replace(orig.trim(), translated);
      if (entry.node?.isConnected) entry.node.nodeValue = value;
      if (entry.element?.isConnected) entry.element.setAttribute(entry.attr, value);
    }
    document.documentElement.lang=language;
    document.title='3D Asset Generator Batch';
    document.querySelectorAll('[data-language-picker]').forEach(p=>{p.value=language;});
  }
  async function use(value) {
    if (!['de','en'].includes(value)) throw new Error('Unsupported language');
    if (value==='en' && !locales.has('en')) {
      const response = await fetch('/static/locales/en.json',{cache:'no-store'});
      if (!response.ok) throw new Error('Translation catalog unavailable');
      const table = await response.json();
      if (!table || Array.isArray(table) || typeof table!=='object') throw new Error('Invalid translation catalog');
      locales.set('en', table);
    }
    language=value;
    apply();
  }
  function logMessage(message) {
    if (language!=='en') return message;
    let translation = translate(message);
    if (translation !== message) return translation;
    // Translate known application event prefixes, preserving filenames, paths,
    // prompt IDs, and raw ComfyUI diagnostics exactly as supplied.
    const scan = /^(\d+) PNGs rekursiv eingelesen · (\d+) GLBs vorhanden\.$/.exec(message);
    if (scan) return `${scan[1]} PNGs scanned recursively · ${scan[2]} GLBs already exist.`;
    const running = /^Batch gestartet: (\d+) ausgewählt · (.*)$/.exec(message);
    if (running) return `Batch started: ${running[1]} selected · ${translate(running[2])}`;
    const job = /^(.*?)  ·  ([^·]+)(?:  ·  (.*))?$/.exec(message);
    if (job) return `${job[1]}  ·  ${translate(job[2].trim())}${job[3] ? '  ·  ' + translate(job[3]) : ''}`;
    const prefixes = [
      'Asset nicht mehr vorhanden: ', 'Asset gesperrt: ', 'Vorhandenes GLB übersprungen: ',
      'Bild geändert: ', 'Fehlerjob erst explizit zurücksetzen: ',
      'Unklarer Job ohne ID, manuell zurücksetzen: ',
      'Vorhandenen ComfyUI-Auftrag fortsetzen: ',
      'Einreichung unklar; keine automatische Wiederholung: ',
      'ComfyUI-Verbindung erfolgreich: ', 'ComfyUI nicht erreichbar: ',
      'ComfyUI-API-Workflow geprüft und zugeordnet: ',
      'Leistungseinstellungen gespeichert · Cooldown ',
      'Cooldown zwischen Generierungen: ', 'Batch abgebrochen: ',
      'Scan fehlgeschlagen: ', 'Dateikonflikt bei ',
      'ComfyUI-Abfrage fehlgeschlagen; versuche erneut: ',
      'Laufzeit-Metadaten konnten nicht gespeichert werden: '
    ];
    for (const prefix of prefixes) {
      if (message.startsWith(prefix)) return translate(prefix)+message.slice(prefix.length);
    }
    return message;
  }
  return {use, translate, logMessage, apply, get language(){return language;}, capture};
})();
