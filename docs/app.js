/**
 * daijin-nintei-db — Static Web Application Controller
 * Zero external dependencies | Works on GitHub Pages and local http.server.
 */
(function () {
  'use strict';

  const Decoder = window.DaijinDecoder;
  if (!Decoder) {
    console.error('DaijinDecoder is not loaded.');
    return;
  }

  /* ========================================================================
     Application State
     ======================================================================== */
  const appState = {
    manifest: null,
    categoriesById: new Map(),
    prefixToCatIds: new Map(),
    categoryCache: new Map(),           // catId -> Array<Record>
    categoryInFlight: new Map(),        // catId -> Promise<Array<Record>>
    certIndex: new Map(),               // normCertNo -> Array<Record>
    loadedCatIds: new Set(),

    // Search tab state
    searchFiltered: [],
    searchPage: 1,
    searchPageSize: 100,
    isLoadingCategories: false,

    // Decoder tab state
    decoderReqSeq: 0,
    currentDecoded: null,

    // Batch tab state
    batchFiles: [],                     // [{ id, name, size, text }]
    batchOccurrences: [],               // per-occurrence results
    batchGrouped: [],                   // deduplicated by cert_no
    batchFilter: 'all',
    batchDedupe: true,

    // Modal state
    modalRecord: null
  };

  /* ========================================================================
     Utilities
     ======================================================================== */
  const $ = (id) => document.getElementById(id);

  function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function formatNumber(n) {
    if (typeof n !== 'number' || Number.isNaN(n)) return '—';
    return n.toLocaleString('ja-JP');
  }

  function debounce(fn, ms) {
    let timer = null;
    return function () {
      const args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function () {
        fn.apply(null, args);
      }, ms);
    };
  }

  let toastTimer = null;
  function showToast(msg) {
    const el = $('toast');
    if (!el) return;
    el.textContent = msg;
    el.classList.add('is-visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      el.classList.remove('is-visible');
    }, 2400);
  }

  async function copyToClipboard(text, successMsg) {
    if (!text) return false;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      showToast(successMsg || 'コピーしました');
      return true;
    } catch (e) {
      showToast('クリップボードへのコピーに失敗しました');
      return false;
    }
  }

  function downloadCsv(filename, headers, rows) {
    const escapeCell = (v) => {
      const s = v == null ? '' : String(v);
      if (/[",\r\n]/.test(s)) {
        return '"' + s.replace(/"/g, '""') + '"';
      }
      return s;
    };
    const lines = [headers.map(escapeCell).join(',')];
    rows.forEach((r) => {
      lines.push(r.map(escapeCell).join(','));
    });
    // UTF-8 with BOM for Microsoft Excel friendliness
    const blob = new Blob(['\uFEFF' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function resolveCategoryUrl(cat) {
    if (!cat) return '';
    if (cat.json_file) {
      let f = String(cat.json_file).replace(/\\/g, '/').replace(/^\.\//, '');
      if (f.startsWith('docs/data/')) f = f.slice('docs/'.length);
      if (f.startsWith('data/')) return './' + f;
      if (f.startsWith('categories/')) return './data/' + f;
      return './data/categories/' + f;
    }
    return './data/categories/' + encodeURIComponent(cat.id) + '.json';
  }

  function isRevokedRecord(rec) {
    return Boolean(rec && rec.date_jp && /(取消|失効)/.test(rec.date_jp));
  }

  function formatLoadBearing(decoded) {
    if (!decoded || decoded.load_bearing === null || decoded.load_bearing === undefined) return '—';
    const isWall = decoded.part_code === 'BE' || decoded.part_code === 'BP' ||
                   decoded.part_code === 'NE' || decoded.part_code === 'NP';
    if (decoded.load_bearing === true) {
      return isWall ? '耐力壁' : '耐力あり（主要構造部等）';
    }
    return isWall ? '非耐力壁' : '非耐力';
  }

  function formatPeriodBadge(period) {
    if (period === 'H25_after') return '<span class="badge badge--indigo">H25以降</span>';
    if (period === 'H25_before') return '<span class="badge badge--neutral">H25以前</span>';
    return '<span class="badge badge--neutral">' + escapeHtml(period || '—') + '</span>';
  }

  function formatPeriodLabel(period) {
    if (period === 'H25_after') return '平成25年4月以降（H25_after）';
    if (period === 'H25_before') return '平成25年3月以前（H25_before）';
    return period || '—';
  }

  /* ========================================================================
     Theme Toggle
     ======================================================================== */
  function initTheme() {
    const saved = localStorage.getItem('daijin_nintei_theme');
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    const theme = saved || (prefersDark ? 'dark' : 'light');
    applyTheme(theme);

    const btn = $('btn-theme-toggle');
    if (btn) {
      btn.addEventListener('click', function () {
        const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        applyTheme(next);
        localStorage.setItem('daijin_nintei_theme', next);
      });
    }
  }

  function applyTheme(theme) {
    if (theme === 'dark') {
      document.documentElement.setAttribute('data-theme', 'dark');
      if ($('theme-icon')) $('theme-icon').textContent = '☀️';
      if ($('theme-label')) $('theme-label').textContent = 'ライト';
    } else {
      document.documentElement.removeAttribute('data-theme');
      if ($('theme-icon')) $('theme-icon').textContent = '🌙';
      if ($('theme-label')) $('theme-label').textContent = 'ダーク';
    }
  }

  /* ========================================================================
     Tab Navigation
     ======================================================================== */
  function switchTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach((btn) => {
      const active = btn.dataset.tab === tabName;
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    document.querySelectorAll('.tab-panel').forEach((panel) => {
      const active = panel.id === 'panel-' + tabName;
      panel.classList.toggle('is-active', active);
    });
  }

  function initTabs() {
    document.querySelectorAll('.tab-btn').forEach((btn) => {
      btn.addEventListener('click', function () {
        switchTab(btn.dataset.tab);
      });
    });
  }

  /* ========================================================================
     Data Loading & Indexing (Manifest, Code Rules, On-Demand Categories)
     ======================================================================== */
  async function fetchJson(url) {
    const res = await fetch(url, { cache: 'default' });
    if (!res.ok) {
      throw new Error('HTTP ' + res.status + ' (' + url + ')');
    }
    return res.json();
  }

  async function initData() {
    const [manifestRes, rulesRes] = await Promise.allSettled([
      fetchJson('./data/manifest.json'),
      fetchJson('./data/code_rules.json')
    ]);

    if (rulesRes.status === 'fulfilled' && rulesRes.value) {
      Decoder.setCodeRules(rulesRes.value);
    }

    renderQuickReference();

    if (manifestRes.status !== 'fulfilled' || !manifestRes.value) {
      const errMsg = manifestRes.reason ? manifestRes.reason.message : 'manifest.json の取得に失敗しました。';
      showGlobalError(
        'データマニフェスト（./data/manifest.json）を読み込めませんでした。ローカルで閲覧する場合は docs/ ディレクトリ内で `python -m http.server` 等を起動してアクセスしてください。詳細: ' + errMsg
      );
      return;
    }

    const manifest = manifestRes.value;
    appState.manifest = manifest;

    // Header badges
    const totalEl = $('badge-total-records');
    if (totalEl) {
      const span = totalEl.querySelector('span');
      if (span) span.textContent = '全 ' + formatNumber(manifest.total_records) + ' 件収録';
    }
    const dateEl = $('badge-data-date');
    if (dateEl && manifest.data_period) {
      dateEl.title = '収録期間: ' + manifest.data_period;
    }

    // Build lookup maps
    const categories = Array.isArray(manifest.categories) ? manifest.categories : [];
    Decoder.registerManifestPrefixes(categories);

    categories.forEach((cat) => {
      appState.categoriesById.set(cat.id, cat);
      if (Array.isArray(cat.prefixes)) {
        cat.prefixes.forEach((p) => {
          const key = String(p).toUpperCase();
          if (!appState.prefixToCatIds.has(key)) {
            appState.prefixToCatIds.set(key, new Set());
          }
          appState.prefixToCatIds.get(key).add(cat.id);
        });
      }
    });

    populateCategorySelect(categories);

    // Load default initial category (prefer fireproof/耐火構造 or first non-empty category)
    const defaultCat = pickDefaultCategory(categories);
    if (defaultCat) {
      $('search-category').value = defaultCat.id;
      await loadCategory(defaultCat.id);
      updateLoadedCategoryCount();
      refreshPrefixFilterOptions();
      runSearchFilter();
    }

    // Handle optional URL hash (e.g., #decoder=QF045BE-1232)
    handleInitialHash();
  }

  function showGlobalError(msg) {
    const box = $('global-error');
    const txt = $('global-error-msg');
    if (box && txt) {
      txt.textContent = msg;
      box.classList.remove('hidden');
    }
  }

  function pickDefaultCategory(categories) {
    if (!categories.length) return null;
    const preferred = categories.find((c) => /耐火構造/.test(c.name) && c.record_count > 0);
    if (preferred) return preferred;
    return categories.find((c) => c.record_count > 0) || categories[0];
  }

  function populateCategorySelect(categories) {
    const sel = $('search-category');
    if (!sel) return;
    sel.innerHTML = '';

    // Group categories by cat.group preserving manifest order
    const groups = new Map();
    categories.forEach((cat) => {
      const g = cat.group || 'その他';
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(cat);
    });

    groups.forEach((cats, groupName) => {
      const og = document.createElement('optgroup');
      og.label = groupName;
      cats.forEach((cat) => {
        const opt = document.createElement('option');
        opt.value = cat.id;
        opt.textContent = cat.name + '（' + formatNumber(cat.record_count) + '件）';
        og.appendChild(opt);
      });
      sel.appendChild(og);
    });
  }

  async function loadCategory(catId) {
    if (!catId) return [];
    if (appState.categoryCache.has(catId)) {
      return appState.categoryCache.get(catId);
    }
    if (appState.categoryInFlight.has(catId)) {
      return appState.categoryInFlight.get(catId);
    }

    const cat = appState.categoriesById.get(catId);
    if (!cat) return [];

    const url = resolveCategoryUrl(cat);
    const promise = fetchJson(url)
      .then((records) => {
        const list = Array.isArray(records) ? records : [];
        // Pre-compute normalized search fields for fast filtering and O(1) exact lookup
        for (let i = 0; i < list.length; i++) {
          const r = list[i];
          const norm = Decoder.normalizeCertNo(r.cert_no);
          r._normCert = norm;
          r._prefixUpper = String(r.prefix || norm.split('-')[0] || '').toUpperCase();
          r._haystack = (
            (r.cert_no || '') + ' ' +
            norm + ' ' +
            (r.applicant || '') + ' ' +
            (r.title || '') + ' ' +
            (r.evaluator || '') + ' ' +
            (r.category_name || '')
          ).toLowerCase();

          if (norm) {
            if (!appState.certIndex.has(norm)) {
              appState.certIndex.set(norm, []);
            }
            appState.certIndex.get(norm).push(r);
          }
        }
        appState.categoryCache.set(catId, list);
        appState.loadedCatIds.add(catId);
        appState.categoryInFlight.delete(catId);
        updateLoadedCategoryCount();
        return list;
      })
      .catch((err) => {
        appState.categoryInFlight.delete(catId);
        console.error('Failed to load category ' + catId, err);
        throw err;
      });

    appState.categoryInFlight.set(catId, promise);
    return promise;
  }

  /**
   * Load multiple categories concurrently with progress callback.
   */
  async function loadCategoriesBatch(catIds, onProgress) {
    const uniqueIds = Array.from(new Set(catIds)).filter((id) => appState.categoriesById.has(id));
    const total = uniqueIds.length;
    if (total === 0) {
      if (onProgress) onProgress(0, 0, 0);
      return;
    }

    let completed = 0;
    let totalRowsLoaded = 0;
    const concurrency = 4;
    let cursor = 0;

    async function worker() {
      while (cursor < uniqueIds.length) {
        const idx = cursor++;
        const id = uniqueIds[idx];
        try {
          const rows = await loadCategory(id);
          totalRowsLoaded += rows.length;
        } catch (e) {
          // Continue loading remaining categories
        }
        completed++;
        if (onProgress) onProgress(completed, total, totalRowsLoaded);
      }
    }

    const workers = [];
    for (let i = 0; i < Math.min(concurrency, uniqueIds.length); i++) {
      workers.push(worker());
    }
    await Promise.all(workers);
  }

  /**
   * Find candidate category IDs for a given certification prefix.
   * Uses exact prefix mapping from manifest.json first, then smart prefix-family fallback.
   */
  function findCandidateCategoryIdsForPrefix(prefix) {
    const p = String(prefix || '').toUpperCase();
    if (!p) return [];

    // 1. Exact prefix hit in manifest
    if (appState.prefixToCatIds.has(p)) {
      return Array.from(appState.prefixToCatIds.get(p));
    }

    // 2. Family fallback for newer/unlisted prefixes (e.g. QF075BE -> any category having QF* prefixes)
    const fam2 = p.slice(0, 2);
    const matched = new Set();
    if (['FP', 'QF', 'PC', 'QP', 'PS', 'NH', 'FI', 'EA', 'EB', 'EC', 'NM', 'QM', 'RM', 'SO', 'MF'].indexOf(fam2) !== -1) {
      appState.prefixToCatIds.forEach((catSet, existingPrefix) => {
        if (existingPrefix.startsWith(fam2)) {
          catSet.forEach((id) => matched.add(id));
        }
      });
    }
    return Array.from(matched);
  }

  function updateLoadedCategoryCount() {
    const el = $('loaded-cat-count');
    if (el) el.textContent = String(appState.loadedCatIds.size);
  }

  /* ========================================================================
     Tab 1: Search
     ======================================================================== */
  function getScopedRecords() {
    const searchAllLoaded = $('chk-search-all-loaded') && $('chk-search-all-loaded').checked;
    if (searchAllLoaded) {
      const combined = [];
      appState.loadedCatIds.forEach((id) => {
        const list = appState.categoryCache.get(id);
        if (list) {
          for (let i = 0; i < list.length; i++) combined.push(list[i]);
        }
      });
      return combined;
    }
    const catId = $('search-category') ? $('search-category').value : '';
    return appState.categoryCache.get(catId) || [];
  }

  function refreshPrefixFilterOptions() {
    const sel = $('search-prefix');
    if (!sel) return;
    const prev = sel.value;
    const records = getScopedRecords();
    const counts = new Map();
    for (let i = 0; i < records.length; i++) {
      const p = records[i]._prefixUpper || records[i].prefix || '';
      if (p) counts.set(p, (counts.get(p) || 0) + 1);
    }
    const sortedPrefixes = Array.from(counts.keys()).sort();

    sel.innerHTML = '<option value="">すべての記号（' + sortedPrefixes.length + '種）</option>';
    const frag = document.createDocumentFragment();
    sortedPrefixes.forEach((p) => {
      const opt = document.createElement('option');
      opt.value = p;
      opt.textContent = p + '（' + formatNumber(counts.get(p)) + '件）';
      frag.appendChild(opt);
    });
    sel.appendChild(frag);
    if (prev && counts.has(prev)) {
      sel.value = prev;
    }
  }

  function runSearchFilter() {
    const records = getScopedRecords();
    const rawQuery = ($('search-query') ? $('search-query').value : '').trim();
    const prefixFilter = $('search-prefix') ? $('search-prefix').value : '';
    const periodFilter = $('search-period') ? $('search-period').value : '';
    const sortMode = $('search-sort') ? $('search-sort').value : 'date_desc';

    // Show/hide clear button
    const clearBtn = $('btn-clear-query');
    if (clearBtn) clearBtn.classList.toggle('hidden', !rawQuery);

    // Split space-delimited AND tokens (normalizing fullwidth for cert matching)
    const tokens = rawQuery
      ? rawQuery
          .replace(/\u3000/g, ' ')
          .split(/\s+/)
          .filter(Boolean)
          .map((t) => {
            const norm = Decoder.normalizeCertNo(t).toLowerCase();
            return { raw: t.toLowerCase(), norm: norm };
          })
      : [];

    const filtered = [];
    for (let i = 0; i < records.length; i++) {
      const r = records[i];
      if (prefixFilter && r._prefixUpper !== prefixFilter) continue;
      if (periodFilter && r.period !== periodFilter) continue;
      if (tokens.length > 0) {
        let allMatch = true;
        for (let j = 0; j < tokens.length; j++) {
          const tk = tokens[j];
          if (r._haystack.indexOf(tk.raw) === -1 && r._haystack.indexOf(tk.norm) === -1) {
            allMatch = false;
            break;
          }
        }
        if (!allMatch) continue;
      }
      filtered.push(r);
    }

    // Sort
    filtered.sort((a, b) => {
      if (sortMode === 'date_desc') {
        return (b.date || '').localeCompare(a.date || '') || (a.cert_no || '').localeCompare(b.cert_no || '');
      }
      if (sortMode === 'date_asc') {
        return (a.date || '').localeCompare(b.date || '') || (a.cert_no || '').localeCompare(b.cert_no || '');
      }
      if (sortMode === 'cert_asc') {
        return (a.cert_no || '').localeCompare(b.cert_no || '');
      }
      if (sortMode === 'applicant_asc') {
        return (a.applicant || '').localeCompare(b.applicant || '', 'ja');
      }
      return 0;
    });

    appState.searchFiltered = filtered;
    appState.searchPage = 1;
    renderSearchTable();
  }

  function renderSearchTable() {
    const tbody = $('search-tbody');
    const summaryEl = $('search-results-summary');
    const paginationEl = $('search-pagination');
    const exportBtn = $('btn-export-search-csv');
    if (!tbody) return;

    const total = appState.searchFiltered.length;
    const scopeTotal = getScopedRecords().length;
    const pageSize = appState.searchPageSize;
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    if (appState.searchPage > totalPages) appState.searchPage = totalPages;

    const startIdx = (appState.searchPage - 1) * pageSize;
    const endIdx = Math.min(total, startIdx + pageSize);

    if (summaryEl) {
      summaryEl.innerHTML =
        '該当 <strong>' + formatNumber(total) + '</strong> 件' +
        '（対象母数 ' + formatNumber(scopeTotal) + ' 件）';
    }
    if (exportBtn) {
      exportBtn.disabled = total === 0;
    }

    tbody.innerHTML = '';
    if (total === 0) {
      const tr = document.createElement('tr');
      tr.innerHTML =
        '<td colspan="6" class="empty-state">' +
        '<p><strong>条件に一致する認定レコードが見つかりませんでした。</strong></p>' +
        '<p>別のカテゴリに切り替えるか、「読込済み全カテゴリを横断検索」「全カテゴリ一括読込」をお試しください。<br>令和5年5月以降の新規認定は「認定番号 デコーダ」または建築性能基準推進協会（性能協）公式DBでご確認いただけます。</p>' +
        '</td>';
      tbody.appendChild(tr);
      if (paginationEl) paginationEl.classList.add('hidden');
      return;
    }

    const frag = document.createDocumentFragment();
    for (let i = startIdx; i < endIdx; i++) {
      const r = appState.searchFiltered[i];
      const revoked = isRevokedRecord(r);
      const tr = document.createElement('tr');
      tr.className = 'clickable-row';
      tr.tabIndex = 0;
      tr.dataset.rowIdx = String(i);
      tr.innerHTML =
        '<td><span class="cert-pill">' + escapeHtml(r.cert_no) + '</span>' +
        (revoked ? '<div style="margin-top:4px;"><span class="badge badge--rose">取消・失効記載</span></div>' : '') + '</td>' +
        '<td><span class="badge badge--neutral">' + escapeHtml(r.category_name || r.category_id) + '</span></td>' +
        '<td><div class="cell-title" title="' + escapeHtml(r.title) + '">' + escapeHtml(r.title || '—') + '</div></td>' +
        '<td><div class="cell-applicant">' + escapeHtml(r.applicant || '—') + '</div></td>' +
        '<td class="cell-date" title="' + escapeHtml(r.date || '') + '">' + escapeHtml(r.date_jp || r.date || '—') + '</td>' +
        '<td>' + formatPeriodBadge(r.period) + '</td>';
      frag.appendChild(tr);
    }
    tbody.appendChild(frag);

    // Pagination UI
    if (paginationEl) {
      paginationEl.classList.toggle('hidden', total <= pageSize);
      $('search-page-range').textContent = formatNumber(startIdx + 1) + '–' + formatNumber(endIdx) + ' / ' + formatNumber(total) + ' 件';
      $('search-page-num').textContent = appState.searchPage + ' / ' + totalPages;
      $('btn-page-first').disabled = appState.searchPage <= 1;
      $('btn-page-prev').disabled = appState.searchPage <= 1;
      $('btn-page-next').disabled = appState.searchPage >= totalPages;
      $('btn-page-last').disabled = appState.searchPage >= totalPages;
    }
  }

  async function handleLoadPopularCategories() {
    if (!appState.manifest || appState.isLoadingCategories) return;
    const cats = appState.manifest.categories || [];
    // Select fire-related & acoustic popular categories dynamically
    const popular = cats.filter((c) => /耐火|準耐火|防火|遮音|区画貫通/.test(c.name + ' ' + (c.group || '')));
    const targetIds = (popular.length ? popular : cats.slice(0, 5)).map((c) => c.id);
    await loadWithProgress(targetIds, '主要防耐火・遮音カテゴリを読み込み中…');
    if ($('chk-search-all-loaded')) $('chk-search-all-loaded').checked = true;
    refreshPrefixFilterOptions();
    runSearchFilter();
    showToast('主要カテゴリ（' + targetIds.length + '件）を読み込みました');
  }

  async function handleLoadAllCategories() {
    if (!appState.manifest || appState.isLoadingCategories) return;
    const allIds = (appState.manifest.categories || []).map((c) => c.id);
    await loadWithProgress(allIds, '全カテゴリ帳簿を読み込み中…');
    if ($('chk-search-all-loaded')) $('chk-search-all-loaded').checked = true;
    refreshPrefixFilterOptions();
    runSearchFilter();
    showToast('全 ' + allIds.length + ' カテゴリの読み込みが完了しました');
  }

  async function loadWithProgress(catIds, label) {
    appState.isLoadingCategories = true;
    const box = $('search-progress-box');
    const textEl = $('search-progress-text');
    const pctEl = $('search-progress-pct');
    const fillEl = $('search-progress-fill');
    const btnAll = $('btn-load-all');
    const btnPop = $('btn-load-popular');

    if (btnAll) btnAll.disabled = true;
    if (btnPop) btnPop.disabled = true;
    if (box) box.classList.remove('hidden');
    if (textEl) textEl.textContent = label;
    if (pctEl) pctEl.textContent = '0%';
    if (fillEl) fillEl.style.width = '0%';

    try {
      await loadCategoriesBatch(catIds, (done, total, rows) => {
        const pct = total > 0 ? Math.round((done / total) * 100) : 100;
        if (textEl) {
          textEl.textContent = label + '（' + done + ' / ' + total + ' カテゴリ・' + formatNumber(rows) + ' 件）';
        }
        if (pctEl) pctEl.textContent = pct + '%';
        if (fillEl) fillEl.style.width = pct + '%';
      });
    } finally {
      appState.isLoadingCategories = false;
      if (btnAll) btnAll.disabled = false;
      if (btnPop) btnPop.disabled = false;
      setTimeout(() => {
        if (box) box.classList.add('hidden');
      }, 600);
    }
  }

  function initSearchTab() {
    const catSelect = $('search-category');
    if (catSelect) {
      catSelect.addEventListener('change', async function () {
        const catId = catSelect.value;
        if (!catId) return;
        if ($('chk-search-all-loaded')) $('chk-search-all-loaded').checked = false;
        $('search-tbody').innerHTML = '<tr><td colspan="6" class="empty-state">カテゴリデータを読み込み中…</td></tr>';
        try {
          await loadCategory(catId);
          refreshPrefixFilterOptions();
          runSearchFilter();
        } catch (err) {
          $('search-tbody').innerHTML = '<tr><td colspan="6" class="empty-state">読み込みに失敗しました: ' + escapeHtml(err.message) + '</td></tr>';
        }
      });
    }

    const debouncedSearch = debounce(runSearchFilter, 120);
    if ($('search-query')) {
      $('search-query').addEventListener('input', debouncedSearch);
    }
    if ($('btn-clear-query')) {
      $('btn-clear-query').addEventListener('click', function () {
        $('search-query').value = '';
        runSearchFilter();
        $('search-query').focus();
      });
    }
    ['search-prefix', 'search-period', 'search-sort'].forEach((id) => {
      if ($(id)) $(id).addEventListener('change', runSearchFilter);
    });
    if ($('chk-search-all-loaded')) {
      $('chk-search-all-loaded').addEventListener('change', function () {
        refreshPrefixFilterOptions();
        runSearchFilter();
      });
    }
    if ($('btn-reset-filters')) {
      $('btn-reset-filters').addEventListener('click', function () {
        $('search-query').value = '';
        $('search-prefix').value = '';
        $('search-period').value = '';
        $('search-sort').value = 'date_desc';
        runSearchFilter();
      });
    }
    if ($('search-page-size')) {
      $('search-page-size').addEventListener('change', function () {
        appState.searchPageSize = parseInt(this.value, 10) || 100;
        appState.searchPage = 1;
        renderSearchTable();
      });
    }

    // Pagination buttons
    if ($('btn-page-first')) $('btn-page-first').addEventListener('click', () => { appState.searchPage = 1; renderSearchTable(); });
    if ($('btn-page-prev')) $('btn-page-prev').addEventListener('click', () => { if (appState.searchPage > 1) { appState.searchPage--; renderSearchTable(); } });
    if ($('btn-page-next')) $('btn-page-next').addEventListener('click', () => {
      const maxP = Math.max(1, Math.ceil(appState.searchFiltered.length / appState.searchPageSize));
      if (appState.searchPage < maxP) { appState.searchPage++; renderSearchTable(); }
    });
    if ($('btn-page-last')) $('btn-page-last').addEventListener('click', () => {
      appState.searchPage = Math.max(1, Math.ceil(appState.searchFiltered.length / appState.searchPageSize));
      renderSearchTable();
    });

    if ($('btn-load-popular')) $('btn-load-popular').addEventListener('click', handleLoadPopularCategories);
    if ($('btn-load-all')) $('btn-load-all').addEventListener('click', handleLoadAllCategories);

    // Row click / keyboard open modal
    const tbody = $('search-tbody');
    if (tbody) {
      tbody.addEventListener('click', function (e) {
        const tr = e.target.closest('tr.clickable-row');
        if (!tr) return;
        const idx = parseInt(tr.dataset.rowIdx, 10);
        const rec = appState.searchFiltered[idx];
        if (rec) openDetailModal(rec);
      });
      tbody.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') {
          const tr = e.target.closest('tr.clickable-row');
          if (!tr) return;
          e.preventDefault();
          const idx = parseInt(tr.dataset.rowIdx, 10);
          const rec = appState.searchFiltered[idx];
          if (rec) openDetailModal(rec);
        }
      });
    }

    // Export search results as CSV
    if ($('btn-export-search-csv')) {
      $('btn-export-search-csv').addEventListener('click', function () {
        const rows = appState.searchFiltered.map((r) => [
          r.cert_no,
          r.prefix,
          r.category_name,
          r.date_jp,
          r.date,
          r.title,
          r.applicant,
          r.address,
          r.evaluator,
          r.period
        ]);
        downloadCsv(
          'daijin-nintei-search-results.csv',
          ['認定番号', '記号', 'カテゴリ', '認定年月日(和暦)', '認定年月日(ISO)', '構造方法等の名称', '申請者', '住所', '性能評価機関', '帳簿区分'],
          rows
        );
        showToast(formatNumber(rows.length) + ' 件をCSV出力しました');
      });
    }
  }

  /* ========================================================================
     Shared Visual Token Breakdown Renderer
     ======================================================================== */
  function renderTokenStripHtml(tokens) {
    if (!Array.isArray(tokens) || tokens.length === 0) return '';
    return tokens
      .map((tk) => {
        const cls = 'token-card token-card--' + escapeHtml(tk.kind || 'neutral');
        return (
          '<div class="' + cls + '">' +
          '<span class="token-label">' + escapeHtml(tk.label) + '</span>' +
          '<span class="token-code">' + escapeHtml(tk.text) + '</span>' +
          '<span class="token-detail">' + escapeHtml(tk.detail) + '</span>' +
          '</div>'
        );
      })
      .join('');
  }

  /* ========================================================================
     Detail Modal
     ======================================================================== */
  function openDetailModal(record) {
    const modal = $('detail-modal');
    if (!modal || !record) return;
    appState.modalRecord = record;

    const decoded = Decoder.decodeCertNo(record.cert_no);
    $('modal-cert-title').textContent = record.cert_no || '—';
    $('modal-period-badge').textContent = record.period === 'H25_after' ? 'H25以降' : (record.period === 'H25_before' ? 'H25以前' : (record.period || ''));
    $('modal-category-badge').textContent = record.category_name || record.category_id || '';
    $('btn-modal-seinokyo').href = Decoder.getSeinokyoUrl();

    const loadBearingText = formatLoadBearing(decoded);

    const revokedBanner = isRevokedRecord(record)
      ? '<div class="status-banner status-banner--invalid" style="margin-bottom: 14px;">' +
          '<div class="status-banner-title">⚠ R5.4帳簿上で取消・失効の記載があります</div>' +
          '<p>帳簿記載：' + escapeHtml(record.date_jp) + '。現在の効力・取扱いは必ず建築性能基準推進協会（性能協）公式DBおよび認定書原本をご確認ください。</p>' +
        '</div>'
      : '';

    const bodyHtml =
      revokedBanner +
      '<div class="modal-section-title">記号の自動デコード（建築基準法ルール）</div>' +
      '<div class="token-strip" style="margin-top: 6px;">' + renderTokenStripHtml(decoded.tokens) + '</div>' +
      '<dl class="spec-dl" style="margin-bottom: 16px;">' +
        '<dt>種別・区分</dt><dd>' + escapeHtml([decoded.group, decoded.category, decoded.sub_category].filter(Boolean).join(' ／ ') || '—') + '</dd>' +
        '<dt>耐火・遮炎時間</dt><dd>' + (decoded.duration_min ? '<strong class="mono">' + decoded.duration_min + ' 分間</strong>' : '—') + '</dd>' +
        '<dt>対象部位・耐力</dt><dd>' + escapeHtml((decoded.part || '—') + (decoded.load_bearing !== null ? '（' + loadBearingText + '）' : '')) + '</dd>' +
        '<dt>根拠条文</dt><dd>' + escapeHtml(decoded.law_article || '—') + '</dd>' +
      '</dl>' +
      '<div class="modal-section-title">国交省公開帳簿メタデータ（令和5年4月30日基準）</div>' +
      '<dl class="spec-dl">' +
        '<dt>認定番号</dt><dd class="mono"><strong>' + escapeHtml(record.cert_no) + '</strong></dd>' +
        '<dt>構造方法等の名称</dt><dd>' + escapeHtml(record.title || '—') + '</dd>' +
        '<dt>申請者の氏名・名称</dt><dd><strong>' + escapeHtml(record.applicant || '—') + '</strong></dd>' +
        '<dt>申請者の住所</dt><dd>' + escapeHtml(record.address || '—') + '</dd>' +
        '<dt>性能評価機関</dt><dd>' + escapeHtml(record.evaluator || '—') + '</dd>' +
        '<dt>認定年月日</dt><dd>' + escapeHtml(record.date_jp || '—') + (record.date ? ' <span class="mono" style="color:var(--text-muted)">(' + escapeHtml(record.date) + ')</span>' : '') + '</dd>' +
        '<dt>収録カテゴリ</dt><dd>' + escapeHtml(record.category_name || record.category_id || '—') + '</dd>' +
        '<dt>帳簿区分</dt><dd>' + escapeHtml(formatPeriodLabel(record.period)) + '</dd>' +
      '</dl>' +
      '<div class="status-banner status-banner--unlisted" style="margin-top: 16px; margin-bottom: 0;">' +
        '<p>※ 本データは令和5年4月30日時点の公開帳簿に基づく情報です。最新の有効性（変更・取消の有無）および具体的な構造仕様（別添）は、一般社団法人 建築性能基準推進協会（性能協）公式DBおよび認定書原本にて必ずご確認ください。</p>' +
      '</div>';

    $('modal-body').innerHTML = bodyHtml;
    modal.classList.remove('hidden');
    $('btn-modal-close').focus();
  }

  function closeDetailModal() {
    const modal = $('detail-modal');
    if (modal) modal.classList.add('hidden');
    appState.modalRecord = null;
  }

  function initModal() {
    const modal = $('detail-modal');
    if (!modal) return;
    $('btn-modal-close').addEventListener('click', closeDetailModal);
    modal.addEventListener('click', function (e) {
      if (e.target === modal) closeDetailModal();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
        closeDetailModal();
      }
    });
    $('btn-modal-copy').addEventListener('click', function () {
      if (appState.modalRecord) {
        copyToClipboard(appState.modalRecord.cert_no, '認定番号「' + appState.modalRecord.cert_no + '」をコピーしました');
      }
    });
    $('btn-modal-to-decoder').addEventListener('click', function () {
      if (!appState.modalRecord) return;
      const certNo = appState.modalRecord.cert_no;
      closeDetailModal();
      switchTab('decoder');
      $('decoder-input').value = certNo;
      runDecoder(certNo);
    });
  }

  /* ========================================================================
     Tab 2: Symbol Decoder
     ======================================================================== */
  async function runDecoder(rawVal) {
    const seq = ++appState.decoderReqSeq;
    const outputEl = $('decoder-output');
    const trimmed = String(rawVal || '').trim();

    if (!trimmed) {
      if (outputEl) outputEl.classList.add('hidden');
      appState.currentDecoded = null;
      return;
    }

    const decoded = Decoder.decodeCertNo(trimmed);
    appState.currentDecoded = decoded;
    if (outputEl) outputEl.classList.remove('hidden');

    // Normalized code & validity badge
    $('decoder-norm-badge').textContent = decoded.cert_no || trimmed;
    $('decoder-validity-badge').innerHTML = decoded.is_valid_format
      ? '<span class="badge badge--emerald">✓ 国交省記号ルール整合</span>'
      : '<span class="badge badge--rose">! 形式・記号を要確認</span>';

    // Token breakdown cards
    $('decoder-token-strip').innerHTML = renderTokenStripHtml(decoded.tokens);

    // Warnings
    const warnEl = $('decoder-warnings');
    if (decoded.warnings && decoded.warnings.length > 0) {
      warnEl.innerHTML =
        '<div class="alert-list">' +
        decoded.warnings
          .map((w) => '<div class="alert-item alert-item--error">⚠ ' + escapeHtml(w) + '</div>')
          .join('') +
        '</div>';
    } else {
      warnEl.innerHTML = '';
    }

    // Spec DL
    const loadBearingStr = formatLoadBearing(decoded);

    $('decoder-spec-dl').innerHTML =
      '<dt>正規化番号</dt><dd class="mono"><strong>' + escapeHtml(decoded.cert_no) + '</strong></dd>' +
      '<dt>認定記号（Prefix）</dt><dd class="mono">' + escapeHtml(decoded.prefix || '—') + '</dd>' +
      '<dt>通算番号</dt><dd class="mono">' + escapeHtml(decoded.serial || '—') + (decoded.branch ? '（枝番：' + escapeHtml(decoded.branch) + '）' : '') + '</dd>' +
      '<dt>構造種別・分類</dt><dd><strong>' + escapeHtml(decoded.structure_type || decoded.category || '未定義') + '</strong>' + (decoded.group ? ' <span class="badge badge--neutral">' + escapeHtml(decoded.group) + '</span>' : '') + '</dd>' +
      '<dt>細分類</dt><dd>' + escapeHtml(decoded.sub_category || '—') + '</dd>' +
      '<dt>性能時間</dt><dd>' + (decoded.duration_min ? '<strong class="mono">' + decoded.duration_min + ' 分間</strong>' + (decoded.duration_min >= 60 && decoded.duration_min % 60 === 0 ? '（' + (decoded.duration_min / 60) + '時間）' : '') : '—') + '</dd>' +
      '<dt>対象部位</dt><dd>' + escapeHtml(decoded.part || '—') + '</dd>' +
      '<dt>耐力区分</dt><dd>' + escapeHtml(loadBearingStr) + '</dd>' +
      '<dt>根拠条文</dt><dd>' + escapeHtml(decoded.law_article || '—') + '</dd>' +
      '<dt>解説</dt><dd>' + escapeHtml(decoded.description || '—') + '</dd>';

    // Ledger lookup (async, on-demand category loading)
    const ledgerEl = $('decoder-ledger-result');
    ledgerEl.innerHTML = '<p class="card-desc">該当カテゴリのR5.4帳簿データを確認しています…</p>';

    const candidateCatIds = findCandidateCategoryIdsForPrefix(decoded.prefix);
    if (candidateCatIds.length > 0) {
      await loadCategoriesBatch(candidateCatIds);
    }

    // Abort if newer input superseded this request
    if (seq !== appState.decoderReqSeq) return;

    const hits = appState.certIndex.get(decoded.cert_no) || [];
    const seinokyoUrl = Decoder.getSeinokyoUrl();

    if (hits.length > 0) {
      const rec = hits[0];
      const extraNote = hits.length > 1 ? '（他 ' + (hits.length - 1) + ' 件の一致あり）' : '';
      const revoked = isRevokedRecord(rec);
      const bannerCls = revoked ? 'status-banner status-banner--invalid' : 'status-banner status-banner--found';
      const bannerTitle = revoked
        ? '⚠ R5.4.30 公開帳簿に一致（取消・失効の記載あり）' + extraNote
        : '✓ R5.4.30 国交省公開帳簿に一致しました' + extraNote;
      const bannerText = revoked
        ? '帳簿上に取消または失効の記載（' + rec.date_jp + '）があります。採用可否を公式DB・原本で必ず確認してください。'
        : '令和5年4月30日時点の公開帳簿に収録されている認定番号です。';
      ledgerEl.innerHTML =
        '<div class="' + bannerCls + '">' +
          '<div class="status-banner-title">' + escapeHtml(bannerTitle) + '</div>' +
          '<p>' + escapeHtml(bannerText) + '</p>' +
        '</div>' +
        '<dl class="spec-dl" style="margin-bottom: 14px;">' +
          '<dt>構造方法等の名称</dt><dd>' + escapeHtml(rec.title || '—') + '</dd>' +
          '<dt>申請者（メーカー）</dt><dd><strong>' + escapeHtml(rec.applicant || '—') + '</strong></dd>' +
          '<dt>性能評価機関</dt><dd>' + escapeHtml(rec.evaluator || '—') + '</dd>' +
          '<dt>認定年月日</dt><dd>' + escapeHtml(rec.date_jp || rec.date || '—') + '</dd>' +
          '<dt>収録カテゴリ</dt><dd>' + escapeHtml(rec.category_name || rec.category_id || '—') + ' ' + formatPeriodBadge(rec.period) + '</dd>' +
        '</dl>' +
        '<div class="scope-actions">' +
          '<button id="btn-decoder-open-modal" type="button" class="btn btn--sm">帳簿詳細をすべて見る</button>' +
          '<a class="btn btn--sm btn--primary" href="' + escapeHtml(seinokyoUrl) + '" target="_blank" rel="noopener noreferrer">性能協DBで最新状況を確認 ↗</a>' +
        '</div>';

      const openBtn = $('btn-decoder-open-modal');
      if (openBtn) openBtn.addEventListener('click', () => openDetailModal(rec));
    } else if (decoded.is_valid_format) {
      ledgerEl.innerHTML =
        '<div class="status-banner status-banner--unlisted">' +
          '<div class="status-banner-title">▲ R5.4.30 公開帳簿 未掲載（記号形式は妥当）</div>' +
          '<p>記号体系は国土交通省の付番ルールに整合していますが、令和5年4月30日時点の公開帳簿には一致する番号がありません。<strong>令和5年5月以降の新規認定番号（2023〜2026年等）</strong>、または番号の転記ミスの可能性があります。</p>' +
        '</div>' +
        '<p class="card-desc" style="margin-bottom: 12px;">最新の大臣認定・有効性の確認は、一般社団法人 建築性能基準推進協会（性能協）の公式検索システムで認定番号を照会してください。</p>' +
        '<div class="scope-actions">' +
          '<button id="btn-decoder-copy-open" type="button" class="btn btn--sm">番号をコピー</button>' +
          '<a class="btn btn--sm btn--primary" href="' + escapeHtml(seinokyoUrl) + '" target="_blank" rel="noopener noreferrer">建築性能基準推進協会（性能協DB）で検索 ↗</a>' +
        '</div>';

      const cpBtn = $('btn-decoder-copy-open');
      if (cpBtn) cpBtn.addEventListener('click', () => copyToClipboard(decoded.cert_no, '認定番号「' + decoded.cert_no + '」をコピーしました'));
    } else {
      ledgerEl.innerHTML =
        '<div class="status-banner status-banner--invalid">' +
          '<div class="status-banner-title">✕ 記号形式の不一致・帳簿未一致</div>' +
          '<p>入力された文字列は標準的な大臣認定番号の規則と一致せず、R5.4.30公開帳簿にも該当がありません。誤記・全角記号・ハイフン欠落等がないかご確認ください。</p>' +
        '</div>' +
        '<a class="btn btn--sm" href="' + escapeHtml(seinokyoUrl) + '" target="_blank" rel="noopener noreferrer">建築性能基準推進協会（性能協DB）を開く ↗</a>';
    }
  }

  function renderQuickReference() {
    const body = $('decoder-ref-body');
    if (!body) return;
    const snap = Decoder.getRulesSnapshot();

    const sItems = Object.keys(snap.structureTypes).map((k) => {
      const v = snap.structureTypes[k];
      return '<li><span class="ref-code">' + escapeHtml(k) + '</span><span>' + escapeHtml(v.ja || v.name || '') + '</span></li>';
    }).join('');

    const pItems = Object.keys(snap.partCodes).map((k) => {
      const v = snap.partCodes[k];
      const lb = v.load_bearing === true ? ' [耐力]' : (v.load_bearing === false ? ' [非耐力]' : '');
      return '<li><span class="ref-code">' + escapeHtml(k) + '</span><span>' + escapeHtml((v.ja || v.name || '') + lb) + '</span></li>';
    }).join('');

    const majorMaterialCodes = ['NM', 'NE', 'QM', 'QE', 'RM', 'RE', 'EA', 'EB', 'EC', 'ED', 'CAT', 'CAS'];
    const mItems = majorMaterialCodes
      .filter((k) => snap.exactCodes[k])
      .map((k) => {
        const v = snap.exactCodes[k];
        return '<li><span class="ref-code">' + escapeHtml(k) + '</span><span>' + escapeHtml(v.category + (v.sub_category ? '（' + v.sub_category + '）' : '')) + '</span></li>';
      }).join('');

    const otherCodes = ['SOI', 'DR', 'UR', 'FRM', 'TBFC', 'MF2', 'MF3', 'MFN', 'JM', 'JC', 'HSB', 'TACP'];
    const oItems = otherCodes
      .filter((k) => snap.exactCodes[k])
      .map((k) => {
        const v = snap.exactCodes[k];
        return '<li><span class="ref-code">' + escapeHtml(k) + '</span><span>' + escapeHtml(v.category + (v.sub_category ? '（' + v.sub_category + '）' : '')) + '</span></li>';
      }).join('');

    body.innerHTML =
      '<div class="ref-grid">' +
        '<div><strong>構造種別（防耐火・貫通部）</strong><ul class="ref-list">' + sItems + '</ul></div>' +
        '<div><strong>部位コード（耐力・非耐力）</strong><ul class="ref-list">' + pItems + '</ul></div>' +
        '<div><strong>防火材料・防火設備</strong><ul class="ref-list">' + mItems + '</ul></div>' +
        '<div><strong>遮音・屋根・構造・シックハウス</strong><ul class="ref-list">' + oItems + '</ul></div>' +
      '</div>';
  }

  function initDecoderTab() {
    const form = $('decoder-form');
    const input = $('decoder-input');
    if (form) {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        runDecoder(input.value);
      });
    }
    if (input) {
      input.addEventListener('input', debounce(function () {
        runDecoder(input.value);
      }, 150));
    }
    if ($('btn-decoder-copy')) {
      $('btn-decoder-copy').addEventListener('click', function () {
        const val = appState.currentDecoded ? appState.currentDecoded.cert_no : Decoder.normalizeCertNo(input.value);
        if (!val) {
          showToast('コピーする認定番号がありません');
          return;
        }
        copyToClipboard(val, '正規化番号「' + val + '」をコピーしました');
      });
    }

    document.querySelectorAll('.chip-btn[data-sample]').forEach((btn) => {
      btn.addEventListener('click', function () {
        const code = btn.dataset.sample;
        input.value = code;
        runDecoder(code);
      });
    });
  }

  /* ========================================================================
     Tab 3: Batch Screening
     ======================================================================== */
  async function readLocalFileSmart(file) {
    const buf = await file.arrayBuffer();
    try {
      return new TextDecoder('utf-8', { fatal: true }).decode(buf);
    } catch (e) {
      try {
        return new TextDecoder('shift_jis').decode(buf);
      } catch (e2) {
        return new TextDecoder('utf-8').decode(buf);
      }
    }
  }

  function renderBatchFileChips() {
    const wrap = $('batch-file-chips');
    if (!wrap) return;
    wrap.innerHTML = '';
    appState.batchFiles.forEach((f) => {
      const chip = document.createElement('span');
      chip.className = 'file-chip';
      const kb = Math.max(1, Math.round(f.size / 1024));
      chip.innerHTML =
        '<span>' + escapeHtml(f.name) + ' (' + formatNumber(kb) + ' KB)</span>' +
        '<button type="button" aria-label="' + escapeHtml(f.name) + ' を削除">×</button>';
      chip.querySelector('button').addEventListener('click', function () {
        appState.batchFiles = appState.batchFiles.filter((x) => x.id !== f.id);
        renderBatchFileChips();
      });
      wrap.appendChild(chip);
    });
  }

  async function addBatchFiles(fileList) {
    const files = Array.from(fileList || []);
    for (const file of files) {
      // Guard against huge binary files (> 25MB)
      if (file.size > 25 * 1024 * 1024) {
        showToast(file.name + ' はサイズが大きすぎます（上限25MB）');
        continue;
      }
      const text = await readLocalFileSmart(file);
      appState.batchFiles.push({
        id: file.name + '_' + Date.now() + '_' + Math.random().toString(36).slice(2, 7),
        name: file.name,
        size: file.size,
        text: text
      });
    }
    renderBatchFileChips();
    if (files.length > 0) {
      showToast(files.length + ' 件のローカルファイルを読み込みました（外部送信なし）');
    }
  }

  async function runBatchScreening() {
    const textVal = ($('batch-textarea') ? $('batch-textarea').value : '').trim();
    const sources = [];
    if (textVal) {
      sources.push({ name: '貼り付けテキスト', text: textVal });
    }
    appState.batchFiles.forEach((f) => {
      sources.push({ name: f.name, text: f.text });
    });

    if (sources.length === 0) {
      showToast('テキストを貼り付けるか、ローカルファイルを追加してください');
      return;
    }

    // 1. Extract all candidate certification numbers
    const extracted = [];
    sources.forEach((src) => {
      const items = Decoder.extractCertNosFromText(src.text, src.name);
      for (let i = 0; i < items.length; i++) extracted.push(items[i]);
    });

    if (extracted.length === 0) {
      $('batch-status-msg').textContent = '大臣認定番号の候補は検出されませんでした。';
      $('batch-results-section').classList.add('hidden');
      showToast('大臣認定番号は検出されませんでした');
      return;
    }

    // 2. Collect required categories based on detected prefixes
    const neededCatIds = new Set();
    extracted.forEach((item) => {
      findCandidateCategoryIdsForPrefix(item.prefix).forEach((id) => neededCatIds.add(id));
    });
    const toLoad = Array.from(neededCatIds).filter((id) => !appState.categoryCache.has(id));

    const progBox = $('batch-progress-box');
    const progText = $('batch-progress-text');
    const progPct = $('batch-progress-pct');
    const progFill = $('batch-progress-fill');
    const runBtn = $('btn-batch-run');

    if (toLoad.length > 0) {
      if (runBtn) runBtn.disabled = true;
      if (progBox) progBox.classList.remove('hidden');
      if (progText) progText.textContent = '照合対象カテゴリ（' + toLoad.length + '件）を読み込み中…';
      if (progPct) progPct.textContent = '0%';
      if (progFill) progFill.style.width = '0%';

      try {
        await loadCategoriesBatch(toLoad, (done, total) => {
          const pct = Math.round((done / total) * 100);
          if (progText) progText.textContent = '照合対象カテゴリを読み込み中…（' + done + ' / ' + total + '）';
          if (progPct) progPct.textContent = pct + '%';
          if (progFill) progFill.style.width = pct + '%';
        });
      } finally {
        if (runBtn) runBtn.disabled = false;
        setTimeout(() => { if (progBox) progBox.classList.add('hidden'); }, 400);
      }
    }

    // 3. Match against ledger and run context contradiction analysis
    const occurrences = extracted.map((item) => {
      const hits = appState.certIndex.get(item.cert_no) || [];
      const ledgerRecord = hits.length > 0 ? hits[0] : null;
      const status = ledgerRecord
        ? 'FOUND'
        : (item.decoded.is_valid_format ? 'UNLISTED_VALID_FORMAT' : 'INVALID_FORMAT');
      const alerts = Decoder.analyzeContext(item.decoded, item.lineText, ledgerRecord);

      return {
        cert_no: item.cert_no,
        raw: item.raw,
        source: item.source,
        lineNo: item.lineNo,
        lineText: item.lineText,
        decoded: item.decoded,
        status: status,
        ledgerRecord: ledgerRecord,
        alerts: alerts
      };
    });

    // 4. Build deduplicated group inventory
    const groupMap = new Map();
    occurrences.forEach((occ) => {
      if (!groupMap.has(occ.cert_no)) {
        groupMap.set(occ.cert_no, {
          cert_no: occ.cert_no,
          decoded: occ.decoded,
          status: occ.status,
          ledgerRecord: occ.ledgerRecord,
          count: 0,
          locations: [],
          alerts: [],
          alertKeys: new Set()
        });
      }
      const g = groupMap.get(occ.cert_no);
      g.count++;
      g.locations.push({ source: occ.source, lineNo: occ.lineNo, lineText: occ.lineText });
      occ.alerts.forEach((a) => {
        const key = a.code + '|' + a.message;
        if (!g.alertKeys.has(key)) {
          g.alertKeys.add(key);
          g.alerts.push(Object.assign({ location: occ.source + ':' + occ.lineNo }, a));
        }
      });
    });

    appState.batchOccurrences = occurrences;
    appState.batchGrouped = Array.from(groupMap.values());

    // Update KPI summary cards
    const uniqueFound = appState.batchGrouped.filter((g) => g.status === 'FOUND').length;
    const uniqueUnlisted = appState.batchGrouped.filter((g) => g.status === 'UNLISTED_VALID_FORMAT').length;
    const alertCount = occurrences.reduce((acc, o) => acc + o.alerts.length, 0);

    $('kpi-total').textContent = formatNumber(occurrences.length);
    $('kpi-unique-sub').textContent = 'ユニーク ' + formatNumber(appState.batchGrouped.length) + ' 番号';
    $('kpi-found').textContent = formatNumber(uniqueFound);
    $('kpi-unlisted').textContent = formatNumber(uniqueUnlisted);
    $('kpi-alerts').textContent = formatNumber(alertCount);

    $('batch-status-msg').textContent =
      formatNumber(occurrences.length) + ' 件（ユニーク ' + formatNumber(appState.batchGrouped.length) + ' 番号）を解析しました。';
    $('batch-results-section').classList.remove('hidden');

    renderBatchTable();
  }

  function renderBatchTable() {
    const tbody = $('batch-tbody');
    if (!tbody) return;
    const filter = appState.batchFilter;
    const dedupe = appState.batchDedupe;
    const seinokyoUrl = Decoder.getSeinokyoUrl();

    const rows = dedupe ? appState.batchGrouped : appState.batchOccurrences;
    const filtered = rows.filter((item) => {
      if (filter === 'found') return item.status === 'FOUND';
      if (filter === 'unlisted') return item.status === 'UNLISTED_VALID_FORMAT';
      if (filter === 'alert') return item.alerts && item.alerts.length > 0;
      return true;
    });

    tbody.innerHTML = '';
    if (filtered.length === 0) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty-state">選択したフィルタ条件に該当する項目はありません。</td></tr>';
      return;
    }

    const frag = document.createDocumentFragment();
    filtered.forEach((item) => {
      const tr = document.createElement('tr');

      // Status badge
      let statusHtml = '';
      if (item.status === 'FOUND') {
        statusHtml = '<span class="badge badge--emerald">✓ 台帳一致</span>';
      } else if (item.status === 'UNLISTED_VALID_FORMAT') {
        statusHtml = '<span class="badge badge--amber">▲ 未掲載・要確認</span>';
      } else {
        statusHtml = '<span class="badge badge--rose">✕ 形式異常</span>';
      }
      if (dedupe && item.count > 1) {
        statusHtml += '<div style="margin-top:4px;"><span class="badge badge--neutral">重複 ' + item.count + '箇所</span></div>';
      }

      // Cert no cell (clickable to open modal or decoder)
      const certBtnHtml =
        '<button type="button" class="cert-pill batch-cert-btn" style="cursor:pointer;" data-cert="' + escapeHtml(item.cert_no) + '">' +
        escapeHtml(item.cert_no) +
        '</button>';

      // Decoded summary
      const dec = item.decoded;
      const decParts = [
        dec.structure_type || dec.category || '未定義',
        dec.duration_min ? dec.duration_min + '分' : null,
        dec.part || null
      ].filter(Boolean);
      const decHtml =
        '<div><strong>' + escapeHtml(decParts[0] || '—') + '</strong></div>' +
        (decParts.length > 1 ? '<div style="font-size:0.78rem; color:var(--text-muted);">' + escapeHtml(decParts.slice(1).join(' / ')) + '</div>' : '');

      // Ledger info or seinokyo link
      let ledgerHtml = '';
      if (item.ledgerRecord) {
        const rec = item.ledgerRecord;
        ledgerHtml =
          '<div><strong>' + escapeHtml(rec.applicant || '—') + '</strong></div>' +
          '<div class="cell-title" style="font-size:0.8rem;" title="' + escapeHtml(rec.title) + '">' + escapeHtml(rec.title || '—') + '</div>' +
          '<div style="font-size:0.75rem; color:var(--text-muted); margin-top:2px;">' + escapeHtml(rec.date_jp || rec.date || '') + ' / ' + escapeHtml(rec.category_name || '') + '</div>';
      } else if (item.status === 'UNLISTED_VALID_FORMAT') {
        ledgerHtml =
          '<div style="font-size:0.8rem; color:var(--amber-text);">R5.4.30帳簿未掲載（令和5年5月以降の新規認定または誤記の要確認）</div>' +
          '<div style="margin-top:5px;"><a class="btn btn--sm btn--amber" href="' + escapeHtml(seinokyoUrl) + '" target="_blank" rel="noopener noreferrer">性能協DBで確認 ↗</a></div>';
      } else {
        ledgerHtml =
          '<div style="font-size:0.8rem; color:var(--rose-text);">国交省標準記号に該当なし（要確認）</div>' +
          '<div style="margin-top:5px;"><a class="btn btn--sm" href="' + escapeHtml(seinokyoUrl) + '" target="_blank" rel="noopener noreferrer">性能協DB ↗</a></div>';
      }

      // Alerts
      let alertsHtml = '<span style="font-size:0.78rem; color:var(--text-muted);">—</span>';
      if (item.alerts && item.alerts.length > 0) {
        alertsHtml =
          '<div class="alert-list">' +
          item.alerts.map((a) => {
            const cls = a.severity === 'error' ? 'alert-item alert-item--error' : 'alert-item';
            const locTag = a.location ? '[' + escapeHtml(a.location) + '] ' : '';
            return '<div class="' + cls + '">' + locTag + escapeHtml(a.message) + '</div>';
          }).join('') +
          '</div>';
      }

      // Locations
      let locHtml = '';
      if (dedupe) {
        const shown = item.locations.slice(0, 3);
        locHtml = shown.map((loc) =>
          '<div style="margin-bottom:4px;">' +
            '<span class="mono" style="font-size:0.76rem; font-weight:700;">' + escapeHtml(loc.source) + ':' + loc.lineNo + '</span>' +
            '<div class="context-snippet" title="' + escapeHtml(loc.lineText) + '">' + escapeHtml(loc.lineText) + '</div>' +
          '</div>'
        ).join('');
        if (item.locations.length > 3) {
          locHtml += '<div style="font-size:0.74rem; color:var(--text-muted);">他 ' + (item.locations.length - 3) + ' 箇所</div>';
        }
      } else {
        locHtml =
          '<span class="mono" style="font-size:0.76rem; font-weight:700;">' + escapeHtml(item.source) + ':' + item.lineNo + '</span>' +
          '<div class="context-snippet" title="' + escapeHtml(item.lineText) + '">' + escapeHtml(item.lineText) + '</div>';
      }

      tr.innerHTML =
        '<td>' + statusHtml + '</td>' +
        '<td>' + certBtnHtml + '</td>' +
        '<td>' + decHtml + '</td>' +
        '<td>' + ledgerHtml + '</td>' +
        '<td>' + alertsHtml + '</td>' +
        '<td>' + locHtml + '</td>';

      frag.appendChild(tr);
    });
    tbody.appendChild(frag);
  }

  function buildBatchMarkdownReport() {
    const totalOcc = appState.batchOccurrences.length;
    const uniqueCount = appState.batchGrouped.length;
    const foundCount = appState.batchGrouped.filter((g) => g.status === 'FOUND').length;
    const unlistedCount = appState.batchGrouped.filter((g) => g.status === 'UNLISTED_VALID_FORMAT').length;
    const invalidCount = appState.batchGrouped.filter((g) => g.status === 'INVALID_FORMAT').length;
    const alertTotal = appState.batchOccurrences.reduce((a, o) => a + o.alerts.length, 0);

    const lines = [
      '# 大臣認定番号 一括スクリーニングレポート',
      '',
      '- 生成ツール: `daijin-nintei-db`（ブラウザ完全ローカル処理）',
      '- 照合帳簿基準日: 令和5年4月30日（国土交通省公開帳簿・PDL1.0加工）',
      '- 検出総数: **' + totalOcc + '件**（ユニーク **' + uniqueCount + '番号**）',
      '- R5.4台帳一致: **' + foundCount + '** ／ 未掲載・要確認（R5.5以降新規等）: **' + unlistedCount + '** ／ 形式異常: **' + invalidCount + '** ／ 注意アラート: **' + alertTotal + '件**',
      '',
      '| 状態 | 認定番号 | 記号解析 | R5.4帳簿 申請者・名称 | 注意アラート | 出現箇所 |',
      '|---|---|---|---|---|---|'
    ];

    const escMd = (s) => String(s || '—').replace(/\|/g, '｜').replace(/\r?\n/g, ' ');

    appState.batchGrouped.forEach((g) => {
      const st = g.status === 'FOUND' ? '台帳一致' : (g.status === 'UNLISTED_VALID_FORMAT' ? '未掲載・要確認' : '形式異常');
      const decStr = [g.decoded.structure_type || g.decoded.category, g.decoded.duration_min ? g.decoded.duration_min + '分' : null, g.decoded.part].filter(Boolean).join(' / ');
      const ledgerStr = g.ledgerRecord
        ? (g.ledgerRecord.applicant + '「' + g.ledgerRecord.title + '」(' + (g.ledgerRecord.date_jp || g.ledgerRecord.date) + ')')
        : 'R5.4帳簿未掲載（公式DB要確認）';
      const alertStr = g.alerts.length > 0 ? g.alerts.map((a) => a.message).join('； ') : '—';
      const locStr = g.locations.map((l) => l.source + ':' + l.lineNo).join(', ');
      lines.push('| ' + [st, '`' + g.cert_no + '`', decStr, ledgerStr, alertStr, locStr].map(escMd).join(' | ') + ' |');
    });

    lines.push('');
    lines.push('> **免責事項**: 本レポートは令和5年4月30日時点の国交省公開帳簿に基づく機械的スクリーニング補助結果です。令和5年5月以降の新規認定・取消・変更および別添仕様は含まれません。最終的な法適合判断は、必ず有資格者が[一般社団法人 建築性能基準推進協会（性能協）大臣認定等検索システム](' + Decoder.getSeinokyoUrl() + ')および認定書原本にて確認してください。');
    return lines.join('\n');
  }

  function insertBatchSampleText() {
    const sample = [
      '【特記仕様書・仕上表 抜粋サンプル】',
      '1. 外壁（耐力壁・45分準耐火構造）：QF045BE-1232',
      '2. 外壁（60分耐火・耐力壁）：QF045BE-1232  ※耐火時間の記載齟齬テスト',
      '3. 外壁（耐力壁・60分耐火構造）：FP060NE-0001  ※耐力壁と非耐力壁記号の齟齬テスト',
      '4. 内装仕上げ（不燃ボード）：NM-8585',
      '5. 界壁（遮音構造）：SOI-0160',
      '6. 外壁開口部（防火設備）：EB-0001',
      '7. 新規採用外壁（令和6年取得想定・未掲載テスト）：QF045BE-9999',
      '8. 誤記テスト（未定義部位記号）：FP060XX-0012'
    ].join('\n');
    $('batch-textarea').value = sample;
    showToast('サンプルテキストを挿入しました。「スクリーニング実行」をクリックしてください');
  }

  function initBatchTab() {
    const dropzone = $('batch-dropzone');
    const fileInput = $('batch-file-input');

    if (dropzone && fileInput) {
      dropzone.addEventListener('click', () => fileInput.click());
      dropzone.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          fileInput.click();
        }
      });
      fileInput.addEventListener('change', function () {
        if (this.files && this.files.length > 0) {
          addBatchFiles(this.files);
          this.value = '';
        }
      });
      dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add('is-dragover');
      });
      dropzone.addEventListener('dragleave', () => {
        dropzone.classList.remove('is-dragover');
      });
      dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('is-dragover');
        if (e.dataTransfer && e.dataTransfer.files) {
          addBatchFiles(e.dataTransfer.files);
        }
      });
    }

    if ($('btn-batch-sample')) {
      $('btn-batch-sample').addEventListener('click', insertBatchSampleText);
    }
    if ($('btn-batch-run')) {
      $('btn-batch-run').addEventListener('click', runBatchScreening);
    }
    if ($('btn-batch-clear')) {
      $('btn-batch-clear').addEventListener('click', function () {
        $('batch-textarea').value = '';
        appState.batchFiles = [];
        appState.batchOccurrences = [];
        appState.batchGrouped = [];
        renderBatchFileChips();
        $('batch-results-section').classList.add('hidden');
        $('batch-status-msg').textContent = '';
      });
    }

    document.querySelectorAll('.filter-pill[data-batch-filter]').forEach((btn) => {
      btn.addEventListener('click', function () {
        document.querySelectorAll('.filter-pill[data-batch-filter]').forEach((b) => b.classList.remove('is-active'));
        btn.classList.add('is-active');
        appState.batchFilter = btn.dataset.batchFilter;
        renderBatchTable();
      });
    });

    if ($('chk-batch-dedupe')) {
      $('chk-batch-dedupe').addEventListener('change', function () {
        appState.batchDedupe = this.checked;
        renderBatchTable();
      });
    }

    // Clicking a cert pill in batch table opens Detail Modal if matched, else opens Decoder
    if ($('batch-tbody')) {
      $('batch-tbody').addEventListener('click', function (e) {
        const btn = e.target.closest('.batch-cert-btn');
        if (!btn) return;
        const certNo = btn.dataset.cert;
        const hits = appState.certIndex.get(certNo) || [];
        if (hits.length > 0) {
          openDetailModal(hits[0]);
        } else {
          switchTab('decoder');
          $('decoder-input').value = certNo;
          runDecoder(certNo);
        }
      });
    }

    if ($('btn-batch-csv')) {
      $('btn-batch-csv').addEventListener('click', function () {
        const rows = appState.batchOccurrences.map((o) => [
          o.status,
          o.cert_no,
          o.decoded.structure_type || o.decoded.category || '',
          o.decoded.duration_min != null ? o.decoded.duration_min : '',
          o.decoded.part || '',
          o.ledgerRecord ? o.ledgerRecord.applicant : '',
          o.ledgerRecord ? o.ledgerRecord.title : '',
          o.ledgerRecord ? (o.ledgerRecord.date_jp || o.ledgerRecord.date) : '',
          o.alerts.map((a) => a.message).join(' / '),
          o.source + ':' + o.lineNo,
          o.lineText
        ]);
        downloadCsv(
          'daijin-nintei-screening.csv',
          ['照合状態', '認定番号', '構造種別', '耐火時間(分)', '部位', 'R5.4申請者', 'R5.4構造方法名', 'R5.4認定日', '注意アラート', '出現箇所', '該当行テキスト'],
          rows
        );
        showToast('スクリーニング結果をCSV出力しました');
      });
    }

    if ($('btn-batch-md')) {
      $('btn-batch-md').addEventListener('click', function () {
        const md = buildBatchMarkdownReport();
        copyToClipboard(md, 'Markdownレポートをクリップボードにコピーしました');
      });
    }
  }

  /* ========================================================================
     URL Hash Deep-Link Support
     ======================================================================== */
  function handleInitialHash() {
    const hash = window.location.hash || '';
    if (hash.startsWith('#decoder=')) {
      const code = decodeURIComponent(hash.slice('#decoder='.length));
      switchTab('decoder');
      if ($('decoder-input')) {
        $('decoder-input').value = code;
        runDecoder(code);
      }
    } else if (hash === '#decoder') {
      switchTab('decoder');
    } else if (hash === '#batch') {
      switchTab('batch');
    }
  }

  /* ========================================================================
     Bootstrap
     ======================================================================== */
  document.addEventListener('DOMContentLoaded', function () {
    initTheme();
    initTabs();
    initModal();
    initSearchTab();
    initDecoderTab();
    initBatchTab();
    initData();
  });
})();
