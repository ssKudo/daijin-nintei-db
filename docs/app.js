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

  const ALL_CATEGORIES_VALUE = '';
  const PRIORITY_CATEGORY_ORDER = [
    'fireproof',
    'quasi_fireproof',
    'fire_resistive',
    'fire_materials',
    'fire_doors',
    'sound_insulation',
    'penetration',
    'fire_brand',
    'wall_multiplier'
  ];
  const jaCollator = (typeof Intl !== 'undefined' && Intl.Collator)
    ? new Intl.Collator('ja')
    : { compare: (a, b) => String(a).localeCompare(String(b)) };

  /* ========================================================================
     Application State
     ======================================================================== */
  const appState = {
    manifest: null,
    manifestLoadFailed: false,
    cutoffDateJp: null,
    categoriesById: new Map(),
    prefixToCatIds: new Map(),
    categoryCache: new Map(),           // catId -> Array<Record>
    categoryInFlight: new Map(),        // catId -> Promise<Array<Record>>
    failedCatIds: new Set(),            // catIds whose JSON fetch failed (e.g. HTTP 503)
    certIndex: new Map(),               // normCertNo -> Array<Record>
    loadedCatIds: new Set(),
    allRecordsCache: null,              // flattened cache of all loaded records
    allCategoriesLoadPromise: null,     // singleton background loader promise
    progressiveRefreshTimer: null,
    hasRenderedInitialBatch: false,

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

  function extractCutoffDateJp(dataPeriod) {
    if (!dataPeriod) return '';
    const s = String(dataPeriod);
    const matches = s.match(/(?:令和|平成|昭和)(?:元|\d+)年\d{1,2}月\d{1,2}日/g);
    if (matches && matches.length > 0) {
      return matches[matches.length - 1].replace(/0(\d)[月日]/g, (m, d) => d + m.slice(-1));
    }
    return s;
  }

  function getCutoffLabel() {
    return appState.cutoffDateJp || '公開帳簿基準日';
  }

  function getDataPeriodLabel() {
    return (appState.manifest && appState.manifest.data_period) || '国土交通省公開帳簿';
  }

  function getCategoryDisplayName(catId) {
    if (catId === '__manifest__') return 'データマニフェスト（manifest.json）';
    const c = appState.categoriesById.get(catId);
    return c ? c.name : catId;
  }

  function applyManifestMetadata(manifest) {
    const cutoff = extractCutoffDateJp(manifest.data_period) || '公開帳簿基準日';
    appState.cutoffDateJp = cutoff;

    const totalEl = $('badge-total-records');
    if (totalEl) {
      const span = totalEl.querySelector('span');
      if (span) span.textContent = '全 ' + formatNumber(manifest.total_records) + ' 件収録';
    }
    const dateEl = $('badge-data-date');
    if (dateEl) {
      dateEl.textContent = 'データ基準日：' + cutoff + '（国交省公開帳簿）';
      if (manifest.data_period) dateEl.title = '収録期間: ' + manifest.data_period;
    }
    const ledgerHeading = $('decoder-ledger-heading');
    if (ledgerHeading) {
      ledgerHeading.textContent = '国交省公開帳簿 照合結果（' + cutoff + '基準）';
    }
    const footerLink = $('footer-source-link');
    if (footerLink && manifest.source_url) {
      footerLink.href = manifest.source_url;
    }
    const footerDate = $('footer-data-date');
    if (footerDate) footerDate.textContent = cutoff;
    const footerDiscDate = $('footer-disclaimer-date');
    if (footerDiscDate) footerDiscDate.textContent = cutoff + '時点';
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
      appState.manifestLoadFailed = true;
      const errMsg = manifestRes.reason ? manifestRes.reason.message : 'manifest.json の取得に失敗しました。';
      showGlobalError(
        'データマニフェスト（./data/manifest.json）を読み込めませんでした。ローカルで閲覧する場合は docs/ ディレクトリ内で `python -m http.server` 等を起動してアクセスしてください。詳細: ' + errMsg
      );
      if ($('badge-data-date')) {
        $('badge-data-date').textContent = 'データ基準日：マニフェスト未読込';
      }
      updateLoadedStatusUi();
      return false;
    }

    appState.manifestLoadFailed = false;
    hideGlobalError();

    const manifest = manifestRes.value;
    appState.manifest = manifest;
    applyManifestMetadata(manifest);

    // Build lookup maps
    appState.categoriesById.clear();
    appState.prefixToCatIds.clear();
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

    // Handle optional URL hash immediately (e.g., #decoder=QF045BE-1232, #batch)
    handleInitialHash();

    // Default selection is "すべて（全区分）" -> seamlessly load all 21 categories in background
    if ($('search-category')) {
      $('search-category').value = ALL_CATEGORIES_VALUE;
    }
    updateLoadedStatusUi();
    ensureAllCategoriesLoaded();
    return true;
  }

  function showGlobalError(msg) {
    const box = $('global-error');
    const txt = $('global-error-msg');
    if (box && txt) {
      txt.textContent = msg;
      box.classList.remove('hidden');
    }
  }

  function hideGlobalError() {
    const box = $('global-error');
    if (box) box.classList.add('hidden');
  }

  function getAllCategoryIdsPrioritized() {
    if (!appState.manifest || !Array.isArray(appState.manifest.categories)) return [];
    const allIds = appState.manifest.categories.map((c) => c.id);
    const idSet = new Set(allIds);
    const prioritized = PRIORITY_CATEGORY_ORDER.filter((id) => idSet.has(id));
    const prioSet = new Set(prioritized);
    allIds.forEach((id) => {
      if (!prioSet.has(id)) prioritized.push(id);
    });
    return prioritized;
  }

  function populateCategorySelect(categories) {
    const sel = $('search-category');
    if (!sel) return;
    sel.innerHTML = '';

    // Default first option: All categories cross-search
    const allOpt = document.createElement('option');
    allOpt.value = ALL_CATEGORIES_VALUE;
    const totalCount = appState.manifest ? appState.manifest.total_records : 0;
    allOpt.textContent = 'すべて（全' + categories.length + '区分・' + formatNumber(totalCount) + '件）';
    allOpt.selected = true;
    sel.appendChild(allOpt);

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

    sel.value = ALL_CATEGORIES_VALUE;
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
        appState.failedCatIds.delete(catId);
        appState.allRecordsCache = null; // invalidate flat cache
        appState.categoryInFlight.delete(catId);
        updateLoadedStatusUi();
        return list;
      })
      .catch((err) => {
        appState.failedCatIds.add(catId);
        appState.categoryInFlight.delete(catId);
        updateLoadedStatusUi();
        console.error('Failed to load category ' + catId, err);
        throw err;
      });

    appState.categoryInFlight.set(catId, promise);
    return promise;
  }

  /**
   * Load multiple categories concurrently with progress callback and event-loop yields.
   * Never swallows failures silently: records them in appState.failedCatIds and returns { loadedIds, failedIds }.
   */
  async function loadCategoriesBatch(catIds, onProgress) {
    const uniqueIds = Array.from(new Set(catIds)).filter((id) => appState.categoriesById.has(id));
    const total = uniqueIds.length;
    if (total === 0) {
      if (onProgress) onProgress(0, 0, 0, 0);
      return { loadedIds: [], failedIds: [] };
    }

    let completed = 0;
    let totalRowsLoaded = 0;
    const loadedIds = [];
    const failedIds = [];
    const concurrency = 4;
    let cursor = 0;

    async function worker() {
      while (cursor < uniqueIds.length) {
        const idx = cursor++;
        const id = uniqueIds[idx];
        try {
          const rows = await loadCategory(id);
          totalRowsLoaded += rows.length;
          loadedIds.push(id);
        } catch (e) {
          appState.failedCatIds.add(id);
          failedIds.push(id);
        }
        completed++;
        if (onProgress) onProgress(completed, total, totalRowsLoaded, failedIds.length);
        // Yield briefly to keep typing & scrolling smooth during bulk JSON parsing
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
    }

    const workers = [];
    for (let i = 0; i < Math.min(concurrency, uniqueIds.length); i++) {
      workers.push(worker());
    }
    await Promise.all(workers);
    return { loadedIds: loadedIds, failedIds: failedIds };
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

    // 2. Family fallback for newer/unlisted prefixes (e.g. QF075BE, EH090, FPLH060BE, QPEP020BE)
    const matched = new Set();
    const fam4 = p.slice(0, 4);
    const fam2 = p.slice(0, 2);
    if (['FPLH', 'QPEP', 'QFRM', 'MCLT', 'MCMU', 'MBLK'].indexOf(fam4) !== -1) {
      appState.prefixToCatIds.forEach((catSet, existingPrefix) => {
        if (existingPrefix.startsWith(fam4) || existingPrefix.startsWith(fam2)) {
          catSet.forEach((id) => matched.add(id));
        }
      });
    }
    if (matched.size === 0 &&
        ['FP', 'QF', 'PC', 'QP', 'PS', 'NH', 'FI', 'EA', 'EB', 'EC', 'ED', 'EH', 'EQ', 'NM', 'QM', 'RM', 'SO', 'MF'].indexOf(fam2) !== -1) {
      appState.prefixToCatIds.forEach((catSet, existingPrefix) => {
        if (existingPrefix.startsWith(fam2)) {
          catSet.forEach((id) => matched.add(id));
        }
      });
    }
    return Array.from(matched);
  }

  function getFailedCategoryIdsForCurrentScope() {
    if (appState.manifestLoadFailed || !appState.manifest) {
      return ['__manifest__'];
    }
    if (isAllCategoriesSelected()) {
      return Array.from(appState.failedCatIds);
    }
    const catId = $('search-category') ? $('search-category').value : '';
    return (catId && appState.failedCatIds.has(catId)) ? [catId] : [];
  }

  function getUnloadedCategoryIdsForCurrentScope() {
    if (!appState.manifest || !Array.isArray(appState.manifest.categories)) return ['__manifest__'];
    if (isAllCategoriesSelected()) {
      return appState.manifest.categories
        .map((c) => c.id)
        .filter((id) => !appState.categoryCache.has(id));
    }
    const catId = $('search-category') ? $('search-category').value : '';
    return (catId && !appState.categoryCache.has(catId)) ? [catId] : [];
  }

  function getTotalCategoriesCount() {
    return (appState.manifest && Array.isArray(appState.manifest.categories))
      ? appState.manifest.categories.length
      : 0;
  }

  function getAllLoadedRecords() {
    if (appState.allRecordsCache) return appState.allRecordsCache;
    const combined = [];
    const cats = (appState.manifest && Array.isArray(appState.manifest.categories))
      ? appState.manifest.categories
      : [];
    for (let c = 0; c < cats.length; c++) {
      const list = appState.categoryCache.get(cats[c].id);
      if (list) {
        for (let i = 0; i < list.length; i++) combined.push(list[i]);
      }
    }
    appState.allRecordsCache = combined;
    return combined;
  }

  function isAllCategoriesLoaded() {
    const totalCats = getTotalCategoriesCount();
    return totalCats > 0 && appState.loadedCatIds.size >= totalCats && appState.failedCatIds.size === 0;
  }

  function updateLoadedStatusUi() {
    const badge = $('loaded-status-badge');
    const retryBtn = $('btn-retry-failed');
    const failBanner = $('search-failed-banner');
    const failMsg = $('search-failed-msg');

    const totalCats = getTotalCategoriesCount();
    const loadedCats = appState.loadedCatIds.size;
    const failedCount = appState.failedCatIds.size;
    const loadedRows = getAllLoadedRecords().length;

    if (appState.manifestLoadFailed || !appState.manifest) {
      if (badge) {
        badge.className = 'badge badge--rose';
        badge.textContent = '⚠ マニフェスト未読込（検索不可）';
      }
      if (retryBtn) {
        retryBtn.classList.remove('hidden');
        retryBtn.textContent = 'マニフェスト読込を再試行';
      }
      return;
    }

    if (badge) {
      if (failedCount > 0) {
        badge.className = 'badge badge--rose';
        badge.textContent =
          '⚠ 一部読込失敗：' + loadedCats + ' / ' + totalCats + ' 区分（' + failedCount + '区分未読込）';
      } else if (totalCats > 0 && loadedCats >= totalCats) {
        badge.className = 'badge badge--emerald';
        badge.textContent = '✓ 全' + totalCats + '区分 読込済（' + formatNumber(loadedRows) + '件・横断検索対応）';
      } else if (totalCats > 0) {
        badge.className = 'badge badge--indigo';
        badge.textContent = '読込中：' + loadedCats + ' / ' + totalCats + ' 区分（' + formatNumber(loadedRows) + '件キャッシュ済）';
      }
    }

    if (retryBtn) {
      if (failedCount > 0) {
        retryBtn.classList.remove('hidden');
        retryBtn.textContent = '失敗区分を再試行（' + failedCount + '区分）';
      } else {
        retryBtn.classList.add('hidden');
      }
    }

    if (failBanner && failMsg) {
      if (failedCount > 0) {
        const names = Array.from(appState.failedCatIds).map(getCategoryDisplayName);
        failMsg.textContent =
          '未読込の区分（' + failedCount + '区分）：' + names.join('、') +
          '。これらの区分のレコードは検索結果およびCSV出力に含まれません。「失敗区分を再試行」をクリックして再読込してください。';
        failBanner.classList.remove('hidden');
      } else {
        failBanner.classList.add('hidden');
      }
    }
  }

  function scheduleProgressiveRefresh(immediate) {
    if (!isAllCategoriesSelected()) return;
    const run = () => {
      appState.progressiveRefreshTimer = null;
      if (!isAllCategoriesSelected()) return;
      refreshPrefixFilterOptions();
      runSearchFilter({ preservePage: appState.hasRenderedInitialBatch, skipAutoEnsure: true });
      appState.hasRenderedInitialBatch = true;
    };

    if (immediate || !appState.hasRenderedInitialBatch) {
      if (appState.progressiveRefreshTimer) {
        clearTimeout(appState.progressiveRefreshTimer);
        appState.progressiveRefreshTimer = null;
      }
      run();
      return;
    }

    if (!appState.progressiveRefreshTimer) {
      appState.progressiveRefreshTimer = setTimeout(run, 220);
    }
  }

  /**
   * Seamlessly fetch all unloaded categories in the background with live progress UI.
   * Deduplicated via singleton promise so multiple callers share the same in-flight work.
   * When forceRetryFailed=true, retries categories currently marked in failedCatIds.
   */
  function ensureAllCategoriesLoaded(forceRetryFailed) {
    if (!appState.manifest) return Promise.resolve({ loadedIds: [], failedIds: ['__manifest__'] });
    if (isAllCategoriesLoaded()) {
      updateLoadedStatusUi();
      return Promise.resolve({ loadedIds: [], failedIds: [] });
    }
    if (appState.allCategoriesLoadPromise) {
      return appState.allCategoriesLoadPromise;
    }

    const allIds = getAllCategoryIdsPrioritized();
    const totalCats = allIds.length;
    const targetIds = allIds.filter((id) => {
      if (appState.categoryCache.has(id)) return false;
      if (!forceRetryFailed && appState.failedCatIds.has(id)) return false;
      return true;
    });
    if (targetIds.length === 0) {
      updateLoadedStatusUi();
      return Promise.resolve({ loadedIds: [], failedIds: Array.from(appState.failedCatIds) });
    }

    appState.isLoadingCategories = true;
    const box = $('search-progress-box');
    const textEl = $('search-progress-text');
    const pctEl = $('search-progress-pct');
    const fillEl = $('search-progress-fill');

    const updateProgressBar = () => {
      const doneCats = appState.loadedCatIds.size;
      const failCats = appState.failedCatIds.size;
      const loadedRows = getAllLoadedRecords().length;
      const attempted = doneCats + failCats;
      const pct = totalCats > 0 ? Math.min(100, Math.round((attempted / totalCats) * 100)) : 100;
      if (textEl) {
        const failNote = failCats > 0 ? '・失敗 ' + failCats + ' 区分' : '';
        textEl.textContent =
          '全' + totalCats + '区分をバックグラウンド読込中…（完了 ' +
          doneCats + ' / ' + totalCats + ' 区分・' + formatNumber(loadedRows) + ' 件' + failNote + '）';
      }
      if (pctEl) pctEl.textContent = pct + '%';
      if (fillEl) fillEl.style.width = pct + '%';
    };

    if (box) box.classList.remove('hidden');
    updateProgressBar();

    const p = loadCategoriesBatch(targetIds, () => {
      updateProgressBar();
      updateLoadedStatusUi();
      scheduleProgressiveRefresh(false);
    }).finally(() => {
      appState.isLoadingCategories = false;
      appState.allCategoriesLoadPromise = null;
      updateProgressBar();
      updateLoadedStatusUi();
      if (isAllCategoriesSelected()) {
        scheduleProgressiveRefresh(true);
      }
      setTimeout(() => {
        if (box && !appState.isLoadingCategories) {
          box.classList.add('hidden');
        }
      }, 600);
    });

    appState.allCategoriesLoadPromise = p;
    return p;
  }

  async function retryFailedCategories() {
    if (appState.manifestLoadFailed || !appState.manifest) {
      await initData();
      return;
    }
    const failed = Array.from(appState.failedCatIds);
    if (failed.length === 0) {
      showToast('再試行対象の失敗区分はありません');
      return;
    }
    const retryBtn = $('btn-retry-failed');
    if (retryBtn) retryBtn.disabled = true;
    try {
      await ensureAllCategoriesLoaded(true);
      const remaining = appState.failedCatIds.size;
      if (remaining === 0) {
        showToast('失敗区分の再読み込みが完了しました');
      } else {
        showToast('一部区分（' + remaining + '区分）の再読み込みに再度失敗しました');
      }
      refreshPrefixFilterOptions();
      runSearchFilter({ preservePage: true, skipAutoEnsure: true });
    } finally {
      if (retryBtn) retryBtn.disabled = false;
    }
  }

  /* ========================================================================
     Tab 1: Search
     ======================================================================== */
  function isAllCategoriesSelected() {
    const sel = $('search-category');
    return !sel || sel.value === ALL_CATEGORIES_VALUE;
  }

  function getScopedRecords() {
    if (isAllCategoriesSelected()) {
      return getAllLoadedRecords();
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

  function cmpAsc(a, b) {
    const sa = a || '';
    const sb = b || '';
    return sa < sb ? -1 : (sa > sb ? 1 : 0);
  }

  function runSearchFilter(opts) {
    const preservePage = Boolean(opts && opts.preservePage);
    const skipAutoEnsure = Boolean(opts && opts.skipAutoEnsure);
    if (!skipAutoEnsure && isAllCategoriesSelected() && appState.manifest && !isAllCategoriesLoaded()) {
      ensureAllCategoriesLoaded(false);
    }

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

    // Fast sort (ASCII comparison for ISO dates & cert_no; cached Intl.Collator for Japanese applicant)
    filtered.sort((a, b) => {
      if (sortMode === 'date_desc') {
        return cmpAsc(b.date, a.date) || cmpAsc(a.cert_no, b.cert_no);
      }
      if (sortMode === 'date_asc') {
        return cmpAsc(a.date, b.date) || cmpAsc(a.cert_no, b.cert_no);
      }
      if (sortMode === 'cert_asc') {
        return cmpAsc(a.cert_no, b.cert_no);
      }
      if (sortMode === 'applicant_asc') {
        return jaCollator.compare(a.applicant || '', b.applicant || '') || cmpAsc(a.cert_no, b.cert_no);
      }
      return 0;
    });

    appState.searchFiltered = filtered;
    if (!preservePage) {
      appState.searchPage = 1;
    }
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
    const allMode = isAllCategoriesSelected();
    const stillLoadingAll = allMode && appState.isLoadingCategories;
    const failedScopeIds = getFailedCategoryIdsForCurrentScope();
    const unloadedScopeIds = getUnloadedCategoryIdsForCurrentScope();
    const totalCats = getTotalCategoriesCount();
    const pageSize = appState.searchPageSize;
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    if (appState.searchPage > totalPages) appState.searchPage = totalPages;

    const startIdx = (appState.searchPage - 1) * pageSize;
    const endIdx = Math.min(total, startIdx + pageSize);

    if (summaryEl) {
      const incompleteTag = failedScopeIds.length > 0
        ? ' <span class="badge badge--rose">※ ' + failedScopeIds.length + '区分読込失敗（結果不完全）</span>'
        : '';
      if (stillLoadingAll) {
        summaryEl.innerHTML =
          '該当 <strong>' + formatNumber(total) + '</strong> 件' +
          '（全区分読込中：' + appState.loadedCatIds.size + ' / ' + totalCats + ' 区分・' +
          formatNumber(scopeTotal) + ' 件を検索済…）' + incompleteTag;
      } else if (allMode) {
        summaryEl.innerHTML =
          '該当 <strong>' + formatNumber(total) + '</strong> 件' +
          '（' + appState.loadedCatIds.size + ' / ' + totalCats + '区分・' + formatNumber(scopeTotal) + ' 件中）' + incompleteTag;
      } else {
        const catObj = appState.categoriesById.get($('search-category').value);
        const catLabel = catObj ? catObj.name : '選択カテゴリ';
        summaryEl.innerHTML =
          '該当 <strong>' + formatNumber(total) + '</strong> 件' +
          '（' + escapeHtml(catLabel) + '・' + formatNumber(scopeTotal) + ' 件中）' + incompleteTag;
      }
    }
    if (exportBtn) {
      exportBtn.disabled = total === 0;
      exportBtn.textContent = (failedScopeIds.length > 0 || unloadedScopeIds.length > 0)
        ? '検索結果をCSV出力（一部未読込あり）'
        : '検索結果をCSV出力';
    }

    tbody.innerHTML = '';
    if (total === 0) {
      const tr = document.createElement('tr');
      const selectedCatId = $('search-category') ? $('search-category').value : '';
      if (stillLoadingAll) {
        tr.innerHTML =
          '<td colspan="6" class="empty-state">' +
          '<p><strong>全' + totalCats + '区分のデータをバックグラウンドで読み込み中です（' +
          appState.loadedCatIds.size + ' / ' + totalCats + ' 区分完了）…</strong></p>' +
          '<p>一致する認定レコードが見つかり次第、自動的に一覧へ表示されます。</p>' +
          '</td>';
      } else if (!allMode && selectedCatId && appState.failedCatIds.has(selectedCatId)) {
        tr.innerHTML =
          '<td colspan="6" class="empty-state">' +
          '<p><strong>選択した区分（' + escapeHtml(getCategoryDisplayName(selectedCatId)) + '）のデータ読み込みに失敗しました。</strong></p>' +
          '<p>データ未読込のため照合できませんでした（未掲載ではありません）。通信状態を確認して再試行してください。</p>' +
          '<p style="margin-top:10px;"><button type="button" class="btn btn--sm btn--rose inline-retry-failed-btn">失敗区分を再試行</button></p>' +
          '</td>';
      } else {
        const hintText = allMode
          ? 'キーワード・記号フィルタ・期間条件を見直してお試しください。'
          : 'キーワード・フィルタ条件を見直すか、カテゴリを「すべて（全区分）」に切り替えてお試しください。';
        const failExtra = failedScopeIds.length > 0
          ? '<br><strong style="color:var(--rose-text);">※ なお、' + failedScopeIds.length + '区分が読込失敗のため未照合です。再試行してご確認ください。</strong>'
          : '';
        tr.innerHTML =
          '<td colspan="6" class="empty-state">' +
          '<p><strong>条件に一致する認定レコードが見つかりませんでした。</strong></p>' +
          '<p>' + hintText + failExtra + '<br>公開帳簿の基準日（' + escapeHtml(getCutoffLabel()) + '）以降の新規認定は「認定番号 デコーダ」または建築性能基準推進協会（性能協）公式DBでご確認いただけます。</p>' +
          '</td>';
      }
      tbody.appendChild(tr);
      if (paginationEl) paginationEl.classList.add('hidden');
      return;
    }

    const frag = document.createDocumentFragment();
    for (let i = startIdx; i < endIdx; i++) {
      const r = appState.searchFiltered[i];
      const revoked = isRevokedRecord(r);
      const catMeta = appState.categoriesById.get(r.category_id);
      const catName = r.category_name || r.category_id || '';
      const catTip = catMeta && catMeta.group ? (catMeta.group + ' / ' + catName) : catName;
      const tr = document.createElement('tr');
      tr.className = 'clickable-row';
      tr.tabIndex = 0;
      tr.dataset.rowIdx = String(i);
      tr.innerHTML =
        '<td><span class="cert-pill">' + escapeHtml(r.cert_no) + '</span>' +
        (revoked ? '<div style="margin-top:4px;"><span class="badge badge--rose">取消・失効記載</span></div>' : '') + '</td>' +
        '<td><span class="badge badge--category" title="' + escapeHtml(catTip) + '">' + escapeHtml(catName) + '</span></td>' +
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

  function updateResetToAllButton() {
    const btn = $('btn-reset-to-all');
    if (btn) btn.classList.toggle('hidden', isAllCategoriesSelected());
  }

  function initSearchTab() {
    const catSelect = $('search-category');
    if (catSelect) {
      catSelect.addEventListener('change', async function () {
        const catId = catSelect.value;
        updateResetToAllButton();
        if (catId === ALL_CATEGORIES_VALUE) {
          ensureAllCategoriesLoaded();
          refreshPrefixFilterOptions();
          runSearchFilter();
          return;
        }
        if (!appState.categoryCache.has(catId)) {
          $('search-tbody').innerHTML = '<tr><td colspan="6" class="empty-state">カテゴリデータを読み込み中…</td></tr>';
        }
        try {
          await loadCategory(catId);
          // Only render if user hasn't switched away while loading
          if (catSelect.value === catId) {
            refreshPrefixFilterOptions();
            runSearchFilter();
          }
        } catch (err) {
          if (catSelect.value === catId) {
            runSearchFilter({ skipAutoEnsure: true });
          }
        }
      });
    }

    if ($('btn-retry-failed')) {
      $('btn-retry-failed').addEventListener('click', retryFailedCategories);
    }

    if ($('btn-reset-to-all')) {
      $('btn-reset-to-all').addEventListener('click', function () {
        if ($('search-category')) $('search-category').value = ALL_CATEGORIES_VALUE;
        updateResetToAllButton();
        ensureAllCategoriesLoaded();
        refreshPrefixFilterOptions();
        runSearchFilter();
      });
    }

    const debouncedSearch = debounce(() => runSearchFilter(), 120);
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
      if ($(id)) $(id).addEventListener('change', () => runSearchFilter());
    });
    if ($('btn-reset-filters')) {
      $('btn-reset-filters').addEventListener('click', function () {
        if ($('search-category')) $('search-category').value = ALL_CATEGORIES_VALUE;
        $('search-query').value = '';
        $('search-prefix').value = '';
        $('search-period').value = '';
        $('search-sort').value = 'date_desc';
        updateResetToAllButton();
        ensureAllCategoriesLoaded();
        refreshPrefixFilterOptions();
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

    // Row click / keyboard open modal (plus inline retry button for failed single category)
    const tbody = $('search-tbody');
    if (tbody) {
      tbody.addEventListener('click', function (e) {
        if (e.target.closest('.inline-retry-failed-btn')) {
          retryFailedCategories();
          return;
        }
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

    // Export search results as CSV (explicitly marks incomplete scope if any categories failed or remain unloaded)
    if ($('btn-export-search-csv')) {
      $('btn-export-search-csv').addEventListener('click', function () {
        const failedIds = getFailedCategoryIdsForCurrentScope();
        const unloadedIds = getUnloadedCategoryIdsForCurrentScope();
        const missingIds = Array.from(new Set(failedIds.concat(unloadedIds)));
        const isIncomplete = missingIds.length > 0;
        const missingNames = missingIds.map(getCategoryDisplayName).join('・');
        const completenessNote = isIncomplete
          ? '不完全（一部区分未読込：' + missingNames + '）'
          : '完全（対象区分すべて読込済）';

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
          r.period,
          completenessNote
        ]);
        const filename = isIncomplete
          ? 'daijin-nintei-search-results-incomplete.csv'
          : 'daijin-nintei-search-results.csv';
        downloadCsv(
          filename,
          ['認定番号', '記号', 'カテゴリ', '認定年月日(和暦)', '認定年月日(ISO)', '構造方法等の名称', '申請者', '住所', '性能評価機関', '帳簿区分', 'データ完全性'],
          rows
        );
        if (isIncomplete) {
          showToast('⚠ 一部区分（' + missingIds.length + '区分）が未読込のため不完全な結果（' + formatNumber(rows.length) + '件）をCSV出力しました');
        } else {
          showToast(formatNumber(rows.length) + ' 件をCSV出力しました');
        }
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

    const cutoffLabel = getCutoffLabel();
    const revokedBanner = isRevokedRecord(record)
      ? '<div class="status-banner status-banner--invalid" style="margin-bottom: 14px;">' +
          '<div class="status-banner-title">⚠ 公開帳簿上で取消・失効の記載があります</div>' +
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
      '<div class="modal-section-title">国交省公開帳簿メタデータ（' + escapeHtml(cutoffLabel) + '基準）</div>' +
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
        '<p>※ 本データは' + escapeHtml(cutoffLabel) + '時点の公開帳簿に基づく情報です。最新の有効性（変更・取消の有無）および具体的な構造仕様（別添）は、一般社団法人 建築性能基準推進協会（性能協）公式DBおよび認定書原本にて必ずご確認ください。</p>' +
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

    // Ledger lookup (async, on-demand category loading with strict failure detection)
    const ledgerEl = $('decoder-ledger-result');
    const seinokyoUrl = Decoder.getSeinokyoUrl();
    const cutoffLabel = getCutoffLabel();

    if (!appState.manifest || appState.manifestLoadFailed) {
      if (!decoded.is_valid_format) {
        ledgerEl.innerHTML =
          '<div class="status-banner status-banner--invalid">' +
            '<div class="status-banner-title">✕ 記号形式の不一致（台帳未照合）</div>' +
            '<p>入力された文字列は標準的な大臣認定番号の規則と一致しません。また、マニフェスト未読込のため公開帳簿との照合は実行されていません。</p>' +
          '</div>' +
          '<div class="scope-actions">' +
            '<button id="btn-decoder-retry" type="button" class="btn btn--sm btn--rose">台帳読込を再試行</button>' +
            '<a class="btn btn--sm" href="' + escapeHtml(seinokyoUrl) + '" target="_blank" rel="noopener noreferrer">建築性能基準推進協会（性能協DB）を開く ↗</a>' +
          '</div>';
      } else {
        ledgerEl.innerHTML =
          '<div class="status-banner status-banner--unchecked">' +
            '<div class="status-banner-title">? 台帳未照合（データ読込失敗：マニフェスト未読込）</div>' +
            '<p>記号形式は国交省ルールに整合していますが、データマニフェスト（manifest.json）の読み込みに失敗しているため、公開帳簿への掲載有無を照合できませんでした（<strong>未掲載ではありません</strong>）。</p>' +
          '</div>' +
          '<div class="scope-actions">' +
            '<button id="btn-decoder-retry" type="button" class="btn btn--sm btn--primary">データ読込を再試行</button>' +
            '<a class="btn btn--sm" href="' + escapeHtml(seinokyoUrl) + '" target="_blank" rel="noopener noreferrer">建築性能基準推進協会（性能協DB）で検索 ↗</a>' +
          '</div>';
      }
      const retryBtn0 = $('btn-decoder-retry');
      if (retryBtn0) {
        retryBtn0.addEventListener('click', async () => {
          await initData();
          runDecoder(trimmed);
        });
      }
      return;
    }

    ledgerEl.innerHTML = '<p class="card-desc">該当カテゴリの公開帳簿データを確認しています…</p>';

    const candidateCatIds = findCandidateCategoryIdsForPrefix(decoded.prefix);
    if (candidateCatIds.length > 0) {
      await loadCategoriesBatch(candidateCatIds);
    }

    // Abort if newer input superseded this request
    if (seq !== appState.decoderReqSeq) return;

    const hits = appState.certIndex.get(decoded.cert_no) || [];
    const failedCandidates = candidateCatIds.filter(
      (id) => appState.failedCatIds.has(id) || !appState.categoryCache.has(id)
    );

    if (hits.length > 0) {
      const rec = hits[0];
      const extraNote = hits.length > 1 ? '（他 ' + (hits.length - 1) + ' 件の一致あり）' : '';
      const revoked = isRevokedRecord(rec);
      const bannerCls = revoked ? 'status-banner status-banner--invalid' : 'status-banner status-banner--found';
      const bannerTitle = revoked
        ? '⚠ ' + cutoffLabel + ' 公開帳簿に一致（取消・失効の記載あり）' + extraNote
        : '✓ ' + cutoffLabel + ' 国交省公開帳簿に一致しました' + extraNote;
      const bannerText = revoked
        ? '帳簿上に取消または失効の記載（' + rec.date_jp + '）があります。採用可否を公式DB・原本で必ず確認してください。'
        : cutoffLabel + '時点の公開帳簿に収録されている認定番号です。';
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
    } else if (failedCandidates.length > 0 && decoded.is_valid_format) {
      const failedNames = failedCandidates.map(getCategoryDisplayName).join('、');
      ledgerEl.innerHTML =
        '<div class="status-banner status-banner--unchecked">' +
          '<div class="status-banner-title">? 台帳未照合（データ読込失敗）</div>' +
          '<p>記号形式は国土交通省の付番ルールに整合していますが、照合対象カテゴリ（<strong>' + escapeHtml(failedNames) + '</strong>）のデータ取得に失敗したため、公開帳簿への掲載有無を照合できませんでした。<strong>未掲載と判定されたわけではありません</strong>。</p>' +
        '</div>' +
        '<p class="card-desc" style="margin-bottom: 12px;">通信状態を確認のうえ「再試行」をクリックするか、性能協の公式検索システムで直接ご確認ください。</p>' +
        '<div class="scope-actions">' +
          '<button id="btn-decoder-retry" type="button" class="btn btn--sm btn--primary">再試行する</button>' +
          '<button id="btn-decoder-copy-open" type="button" class="btn btn--sm">番号をコピー</button>' +
          '<a class="btn btn--sm" href="' + escapeHtml(seinokyoUrl) + '" target="_blank" rel="noopener noreferrer">建築性能基準推進協会（性能協DB）で検索 ↗</a>' +
        '</div>';

      const retryBtn = $('btn-decoder-retry');
      if (retryBtn) retryBtn.addEventListener('click', () => runDecoder(trimmed));
      const cpBtn = $('btn-decoder-copy-open');
      if (cpBtn) cpBtn.addEventListener('click', () => copyToClipboard(decoded.cert_no, '認定番号「' + decoded.cert_no + '」をコピーしました'));
    } else if (decoded.is_valid_format) {
      ledgerEl.innerHTML =
        '<div class="status-banner status-banner--unlisted">' +
          '<div class="status-banner-title">▲ ' + escapeHtml(cutoffLabel) + ' 公開帳簿 未掲載（記号形式は妥当）</div>' +
          '<p>記号体系は国土交通省の付番ルールに整合していますが、' + escapeHtml(cutoffLabel) + '時点の公開帳簿には一致する番号がありません。<strong>基準日以降の新規認定番号</strong>、または番号の転記ミスの可能性があります。</p>' +
        '</div>' +
        '<p class="card-desc" style="margin-bottom: 12px;">最新の大臣認定・有効性の確認は、一般社団法人 建築性能基準推進協会（性能協）の公式検索システムで認定番号を照会してください。</p>' +
        '<div class="scope-actions">' +
          '<button id="btn-decoder-copy-open" type="button" class="btn btn--sm">番号をコピー</button>' +
          '<a class="btn btn--sm btn--primary" href="' + escapeHtml(seinokyoUrl) + '" target="_blank" rel="noopener noreferrer">建築性能基準推進協会（性能協DB）で検索 ↗</a>' +
        '</div>';

      const cpBtn = $('btn-decoder-copy-open');
      if (cpBtn) cpBtn.addEventListener('click', () => copyToClipboard(decoded.cert_no, '認定番号「' + decoded.cert_no + '」をコピーしました'));
    } else {
      const extraFail = failedCandidates.length > 0
        ? '（なお、一部カテゴリのデータ取得にも失敗しています）'
        : '';
      ledgerEl.innerHTML =
        '<div class="status-banner status-banner--invalid">' +
          '<div class="status-banner-title">✕ 記号形式の不一致・帳簿未一致</div>' +
          '<p>入力された文字列は標準的な大臣認定番号の規則と一致せず、' + escapeHtml(cutoffLabel) + '公開帳簿にも該当がありません' + escapeHtml(extraFail) + '。誤記・全角記号・ハイフン欠落等がないかご確認ください。</p>' +
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

    const majorMaterialCodes = ['NM', 'NE', 'QM', 'QE', 'RM', 'RE', 'EA', 'EB', 'EC', 'ED', 'EBN010', 'CAT', 'CAS'];
    const mItems = majorMaterialCodes
      .filter((k) => snap.exactCodes[k])
      .map((k) => {
        const v = snap.exactCodes[k];
        return '<li><span class="ref-code">' + escapeHtml(k) + '</span><span>' + escapeHtml(v.category + (v.sub_category ? '（' + v.sub_category + '）' : '')) + '</span></li>';
      }).join('');

    const otherCodes = ['SOI', 'DR', 'UR', 'FRM', 'QFRM', 'TBFC', 'MF2', 'MF3', 'MFN', 'MCLT', 'MCMU', 'MBLK', 'JM', 'JC', 'HSB', 'TACP'];
    const oItems = otherCodes
      .filter((k) => snap.exactCodes[k])
      .map((k) => {
        const v = snap.exactCodes[k];
        return '<li><span class="ref-code">' + escapeHtml(k) + '</span><span>' + escapeHtml(v.category + (v.sub_category ? '（' + v.sub_category + '）' : '')) + '</span></li>';
      }).join('');

    body.innerHTML =
      '<div class="ref-grid">' +
        '<div><strong>構造種別・防火設備（防耐火・貫通部・遮熱）</strong><ul class="ref-list">' + sItems + '</ul></div>' +
        '<div><strong>部位コード（耐力・非耐力）</strong><ul class="ref-list">' + pItems + '</ul></div>' +
        '<div><strong>防火材料・防火設備</strong><ul class="ref-list">' + mItems + '</ul></div>' +
        '<div><strong>遮音・屋根・構造・指定建築材料</strong><ul class="ref-list">' + oItems + '</ul></div>' +
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

    if (toLoad.length > 0 && appState.manifest) {
      if (runBtn) runBtn.disabled = true;
      if (progBox) progBox.classList.remove('hidden');
      if (progText) progText.textContent = '照合対象カテゴリ（' + toLoad.length + '件）を読み込み中…';
      if (progPct) progPct.textContent = '0%';
      if (progFill) progFill.style.width = '0%';

      try {
        await loadCategoriesBatch(toLoad, (done, total, _rows, failedCnt) => {
          const pct = Math.round((done / total) * 100);
          const failTxt = failedCnt > 0 ? '・失敗 ' + failedCnt + '件' : '';
          if (progText) progText.textContent = '照合対象カテゴリを読み込み中…（' + done + ' / ' + total + failTxt + '）';
          if (progPct) progPct.textContent = pct + '%';
          if (progFill) progFill.style.width = pct + '%';
        });
      } finally {
        if (runBtn) runBtn.disabled = false;
        setTimeout(() => { if (progBox) progBox.classList.add('hidden'); }, 400);
      }
    }

    // 3. Match against ledger with strict UNCHECKED vs UNLISTED separation & localContext contradiction analysis
    const batchFailedCatSet = new Set();
    const occurrences = extracted.map((item) => {
      const hits = appState.certIndex.get(item.cert_no) || [];
      const ledgerRecord = hits.length > 0 ? hits[0] : null;
      const candIds = findCandidateCategoryIdsForPrefix(item.prefix);
      const failedCands = (!appState.manifest || appState.manifestLoadFailed)
        ? ['__manifest__']
        : candIds.filter((id) => appState.failedCatIds.has(id) || !appState.categoryCache.has(id));
      failedCands.forEach((id) => batchFailedCatSet.add(id));

      let status;
      if (ledgerRecord) {
        status = 'FOUND';
      } else if (!item.decoded.is_valid_format) {
        status = 'INVALID_FORMAT';
      } else if (failedCands.length > 0) {
        status = 'UNCHECKED';
      } else {
        status = 'UNLISTED_VALID_FORMAT';
      }

      // Pass per-candidate localContext (Review #4) so neighboring certs on the same line do not bleed context
      const contextForAnalysis = item.localContext || item.lineText;
      const alerts = Decoder.analyzeContext(item.decoded, contextForAnalysis, ledgerRecord);

      return {
        cert_no: item.cert_no,
        raw: item.raw,
        source: item.source,
        lineNo: item.lineNo,
        lineText: item.lineText,
        localContext: contextForAnalysis,
        decoded: item.decoded,
        status: status,
        failedCategories: failedCands.map(getCategoryDisplayName),
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
          failedCategories: occ.failedCategories.slice(),
          ledgerRecord: occ.ledgerRecord,
          count: 0,
          locations: [],
          alerts: [],
          alertKeys: new Set()
        });
      }
      const g = groupMap.get(occ.cert_no);
      g.count++;
      g.locations.push({ source: occ.source, lineNo: occ.lineNo, lineText: occ.lineText, localContext: occ.localContext });
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

    // Update KPI summary cards (strictly separating UNCHECKED from UNLISTED)
    const uniqueFound = appState.batchGrouped.filter((g) => g.status === 'FOUND').length;
    const uniqueUnlisted = appState.batchGrouped.filter((g) => g.status === 'UNLISTED_VALID_FORMAT').length;
    const uniqueUnchecked = appState.batchGrouped.filter((g) => g.status === 'UNCHECKED').length;
    const alertCount = occurrences.reduce((acc, o) => acc + o.alerts.length, 0);

    $('kpi-total').textContent = formatNumber(occurrences.length);
    $('kpi-unique-sub').textContent = 'ユニーク ' + formatNumber(appState.batchGrouped.length) + ' 番号';
    $('kpi-found').textContent = formatNumber(uniqueFound);
    $('kpi-unlisted').textContent = formatNumber(uniqueUnlisted);
    if ($('kpi-unchecked')) $('kpi-unchecked').textContent = formatNumber(uniqueUnchecked);
    $('kpi-alerts').textContent = formatNumber(alertCount);

    // Show or hide batch fetch failure banner
    const batchFailBanner = $('batch-failed-banner');
    const batchFailMsg = $('batch-failed-msg');
    if (batchFailBanner && batchFailMsg) {
      if (uniqueUnchecked > 0) {
        const failedNames = Array.from(batchFailedCatSet).map(getCategoryDisplayName).join('、');
        batchFailMsg.textContent =
          '取得失敗カテゴリ：' + failedNames + '（対象 ' + uniqueUnchecked +
          ' 番号は「台帳未照合」として扱い、「未掲載」とは区別しています）。';
        batchFailBanner.classList.remove('hidden');
      } else {
        batchFailBanner.classList.add('hidden');
      }
    }

    $('batch-status-msg').textContent =
      formatNumber(occurrences.length) + ' 件（ユニーク ' + formatNumber(appState.batchGrouped.length) + ' 番号' +
      (uniqueUnchecked > 0 ? '・うち台帳未照合 ' + formatNumber(uniqueUnchecked) + ' 番号' : '') + '）を解析しました。';
    $('batch-results-section').classList.remove('hidden');

    renderBatchTable();
  }

  function renderBatchTable() {
    const tbody = $('batch-tbody');
    if (!tbody) return;
    const filter = appState.batchFilter;
    const dedupe = appState.batchDedupe;
    const seinokyoUrl = Decoder.getSeinokyoUrl();
    const cutoffLabel = getCutoffLabel();

    const rows = dedupe ? appState.batchGrouped : appState.batchOccurrences;
    const filtered = rows.filter((item) => {
      if (filter === 'found') return item.status === 'FOUND';
      if (filter === 'unlisted') return item.status === 'UNLISTED_VALID_FORMAT';
      if (filter === 'unchecked') return item.status === 'UNCHECKED';
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
      } else if (item.status === 'UNCHECKED') {
        statusHtml = '<span class="badge badge--unchecked">? 台帳未照合</span>';
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

      // Ledger info, UNCHECKED notice, or seinokyo link
      let ledgerHtml = '';
      if (item.ledgerRecord) {
        const rec = item.ledgerRecord;
        ledgerHtml =
          '<div><strong>' + escapeHtml(rec.applicant || '—') + '</strong></div>' +
          '<div class="cell-title" style="font-size:0.8rem;" title="' + escapeHtml(rec.title) + '">' + escapeHtml(rec.title || '—') + '</div>' +
          '<div style="font-size:0.75rem; color:var(--text-muted); margin-top:2px;">' + escapeHtml(rec.date_jp || rec.date || '') + ' / ' + escapeHtml(rec.category_name || '') + '</div>';
      } else if (item.status === 'UNCHECKED') {
        const failNames = (item.failedCategories && item.failedCategories.length > 0)
          ? item.failedCategories.join('、')
          : '対象カテゴリ';
        ledgerHtml =
          '<div style="font-size:0.8rem; color:var(--violet-text);">台帳未照合（読込失敗：' + escapeHtml(failNames) + '／<strong>未掲載ではありません</strong>）</div>' +
          '<div style="margin-top:5px; display:flex; gap:6px; flex-wrap:wrap;">' +
            '<button type="button" class="btn btn--sm btn--rose batch-inline-retry-btn">再試行</button>' +
            '<a class="btn btn--sm" href="' + escapeHtml(seinokyoUrl) + '" target="_blank" rel="noopener noreferrer">性能協DB ↗</a>' +
          '</div>';
      } else if (item.status === 'UNLISTED_VALID_FORMAT') {
        ledgerHtml =
          '<div style="font-size:0.8rem; color:var(--amber-text);">' + escapeHtml(cutoffLabel) + '帳簿未掲載（基準日以降の新規認定または誤記の要確認）</div>' +
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
    const cutoffLabel = getCutoffLabel();
    const totalOcc = appState.batchOccurrences.length;
    const uniqueCount = appState.batchGrouped.length;
    const foundCount = appState.batchGrouped.filter((g) => g.status === 'FOUND').length;
    const unlistedCount = appState.batchGrouped.filter((g) => g.status === 'UNLISTED_VALID_FORMAT').length;
    const uncheckedCount = appState.batchGrouped.filter((g) => g.status === 'UNCHECKED').length;
    const invalidCount = appState.batchGrouped.filter((g) => g.status === 'INVALID_FORMAT').length;
    const alertTotal = appState.batchOccurrences.reduce((a, o) => a + o.alerts.length, 0);

    const lines = [
      '# 大臣認定番号 一括スクリーニングレポート',
      '',
      '- 生成ツール: `daijin-nintei-db`（ブラウザ完全ローカル処理）',
      '- 照合帳簿基準日: ' + cutoffLabel + '（' + getDataPeriodLabel() + '・PDL1.0加工）',
      '- 検出総数: **' + totalOcc + '件**（ユニーク **' + uniqueCount + '番号**）',
      '- 台帳一致: **' + foundCount + '** ／ 未掲載・要確認（基準日以降新規等）: **' + unlistedCount + '** ／ 台帳未照合（読込失敗）: **' + uncheckedCount + '** ／ 形式異常: **' + invalidCount + '** ／ 注意アラート: **' + alertTotal + '件**',
      '',
      '| 状態 | 認定番号 | 記号解析 | 公開帳簿 申請者・名称 / 照合備考 | 注意アラート | 出現箇所 |',
      '|---|---|---|---|---|---|'
    ];

    const escMd = (s) => String(s || '—').replace(/\|/g, '｜').replace(/\r?\n/g, ' ');

    appState.batchGrouped.forEach((g) => {
      let st;
      if (g.status === 'FOUND') st = '台帳一致';
      else if (g.status === 'UNLISTED_VALID_FORMAT') st = '未掲載・要確認';
      else if (g.status === 'UNCHECKED') st = '台帳未照合（読込失敗）';
      else st = '形式異常';

      const decStr = [g.decoded.structure_type || g.decoded.category, g.decoded.duration_min ? g.decoded.duration_min + '分' : null, g.decoded.part].filter(Boolean).join(' / ');
      let ledgerStr;
      if (g.ledgerRecord) {
        ledgerStr = g.ledgerRecord.applicant + '「' + g.ledgerRecord.title + '」(' + (g.ledgerRecord.date_jp || g.ledgerRecord.date) + ')';
      } else if (g.status === 'UNCHECKED') {
        const failNames = (g.failedCategories && g.failedCategories.length) ? g.failedCategories.join('・') : '対象カテゴリ';
        ledgerStr = '対象カテゴリ読込失敗（' + failNames + '）のため未照合（未掲載ではありません）';
      } else if (g.status === 'UNLISTED_VALID_FORMAT') {
        ledgerStr = cutoffLabel + '帳簿未掲載（公式DB要確認）';
      } else {
        ledgerStr = '記号形式不一致（公式DB要確認）';
      }
      const alertStr = g.alerts.length > 0 ? g.alerts.map((a) => a.message).join('； ') : '—';
      const locStr = g.locations.map((l) => l.source + ':' + l.lineNo).join(', ');
      lines.push('| ' + [st, '`' + g.cert_no + '`', decStr, ledgerStr, alertStr, locStr].map(escMd).join(' | ') + ' |');
    });

    lines.push('');
    lines.push('> **免責事項**: 本レポートは' + cutoffLabel + '時点の国交省公開帳簿に基づく機械的スクリーニング補助結果です。基準日以降の新規認定・取消・変更および別添仕様は含まれません。最終的な法適合判断は、必ず有資格者が[一般社団法人 建築性能基準推進協会（性能協）大臣認定等検索システム](' + Decoder.getSeinokyoUrl() + ')および認定書原本にて確認してください。');
    return lines.join('\n');
  }

  function insertBatchSampleText() {
    const sample = [
      '【特記仕様書・仕上表 抜粋サンプル】',
      '1. 外壁 60分 QF045BE-1232 ／ 間仕切壁 45分 QF045BP-0001  ※1行複数番号の文脈切り分けテスト',
      '2. 外壁（耐力壁・60分耐火構造）：FP060NE-0001  ※耐力壁と非耐力壁記号の齟齬テスト',
      '3. 内装仕上げ（不燃ボード）：NM-8585',
      '4. 界壁（遮音構造）：SOI-0160',
      '5. 小規模竪穴区画（防火設備）：ED-0001 ／ 遮熱防火設備（90分）：EH090-0001',
      '6. 基準日以降の新規採用想定（未掲載テスト）：QF045BE-9999',
      '7. 誤記テスト（未定義部位記号）：FP060XX-0012'
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
    if ($('btn-batch-retry')) {
      $('btn-batch-retry').addEventListener('click', async function () {
        if (appState.manifestLoadFailed || !appState.manifest) {
          await initData();
        }
        runBatchScreening();
      });
    }
    if ($('btn-batch-clear')) {
      $('btn-batch-clear').addEventListener('click', function () {
        $('batch-textarea').value = '';
        appState.batchFiles = [];
        appState.batchOccurrences = [];
        appState.batchGrouped = [];
        renderBatchFileChips();
        $('batch-results-section').classList.add('hidden');
        if ($('batch-failed-banner')) $('batch-failed-banner').classList.add('hidden');
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

    // Clicking a cert pill opens Detail Modal if matched, else Decoder; inline retry button retries screening
    if ($('batch-tbody')) {
      $('batch-tbody').addEventListener('click', async function (e) {
        if (e.target.closest('.batch-inline-retry-btn')) {
          if (appState.manifestLoadFailed || !appState.manifest) {
            await initData();
          }
          runBatchScreening();
          return;
        }
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
        const hasUnchecked = appState.batchOccurrences.some((o) => o.status === 'UNCHECKED');
        const rows = appState.batchOccurrences.map((o) => {
          const checkNote = o.status === 'UNCHECKED'
            ? '台帳未照合（読込失敗：' + ((o.failedCategories && o.failedCategories.join('・')) || '対象カテゴリ') + '）'
            : (o.status === 'UNLISTED_VALID_FORMAT' ? getCutoffLabel() + '帳簿未掲載' : (o.status === 'FOUND' ? '台帳一致' : '形式異常'));
          return [
            o.status,
            checkNote,
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
          ];
        });
        downloadCsv(
          hasUnchecked ? 'daijin-nintei-screening-with-unchecked.csv' : 'daijin-nintei-screening.csv',
          ['照合状態', '照合備考', '認定番号', '構造種別', '耐火時間(分)', '部位', '公開帳簿申請者', '公開帳簿構造方法名', '公開帳簿認定日', '注意アラート', '出現箇所', '該当行テキスト'],
          rows
        );
        if (hasUnchecked) {
          showToast('⚠ 台帳未照合（読込失敗）の項目を含むスクリーニング結果をCSV出力しました');
        } else {
          showToast('スクリーニング結果をCSV出力しました');
        }
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
