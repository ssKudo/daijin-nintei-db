/**
 * daijin-nintei-db — Official MLIT Minister Certification Number Decoder & Screener Engine
 * Pure deterministic JavaScript (zero dependencies, browser + Node.js compatible).
 * Works for all historical and latest (2024–2026+) certification numbers.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.DaijinDecoder = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const DEFAULT_SEINOKYO_URL = 'https://www.seinokyo.jp/ninteidb/pub/';

  /** Built-in fallback structure types (hydrated/overridden by code_rules.json when loaded) */
  const FALLBACK_STRUCTURE_TYPES = {
    FP: {
      ja: '耐火構造',
      en: 'Fireproof Construction',
      law_article: '建築基準法第2条第七号・施行令第107条',
      description: '通常の火災が終了するまで倒壊及び延焼を防止するために必要な性能を有する構造'
    },
    QF: {
      ja: '準耐火構造',
      en: 'Quasi-Fireproof Construction',
      law_article: '建築基準法第2条第七号の二・施行令第107条の2（60分は令第129条の2の3第1項第一号ロ）',
      description: '通常の火災による延焼を抑制するために必要な性能を有する構造'
    },
    PC: {
      ja: '防火構造',
      en: 'Fire Preventive Construction',
      law_article: '建築基準法第2条第八号・施行令第108条',
      description: '建築物の周囲において発生する通常の火災による延焼を抑制するための外壁・軒裏の構造'
    },
    QP: {
      ja: '準防火構造',
      en: 'Quasi-Fire Preventive Construction',
      law_article: '建築基準法第23条・施行令第109条の6',
      description: '法22条区域内の木造建築物等の外壁に求められる20分間の防火性能を有する構造'
    },
    PS: {
      ja: '防火区画貫通部',
      en: 'Fire Compartment Penetration Seal',
      law_article: '建築基準法施行令第129条の2の4第1項第七号ハ（旧令第129条の2の5）',
      description: '防火区画の壁・床を貫通する給水管・配電管等の貫通部処理措置'
    },
    NH: {
      ja: '防火壁の設置を要しない床等',
      en: 'Fire-Preventive Floor (Exempt from Fire Wall)',
      law_article: '建築基準法施行令第109条の3第二号ハ・第115条の2第1項第四号',
      description: '防火壁・防火床の代替等に係る床等の構造'
    },
    FI: {
      ja: '防火壁を設けた部分の屋根等',
      en: 'Flame Insulation Roof / Element',
      law_article: '建築基準法施行令第109条の3第一号・第113条第1項第三号',
      description: '防火壁を設けた部分の屋根の構造等'
    }
  };

  /** Built-in fallback part codes */
  const FALLBACK_PART_CODES = {
    BE: { ja: '外壁（耐力壁）', en: 'Bearing Exterior Wall', load_bearing: true, element_group: 'exterior_wall' },
    BP: { ja: '間仕切壁（耐力壁）', en: 'Bearing Partition Wall', load_bearing: true, element_group: 'partition_wall' },
    NE: { ja: '外壁（非耐力壁）', en: 'Non-bearing Exterior Wall', load_bearing: false, element_group: 'exterior_wall' },
    NP: { ja: '間仕切壁（非耐力壁）', en: 'Non-bearing Partition Wall', load_bearing: false, element_group: 'partition_wall' },
    CN: { ja: '柱', en: 'Column', load_bearing: true, element_group: 'column' },
    FL: { ja: '床', en: 'Floor', load_bearing: true, element_group: 'floor' },
    BM: { ja: 'はり', en: 'Beam', load_bearing: true, element_group: 'beam' },
    RF: { ja: '屋根', en: 'Roof', load_bearing: false, element_group: 'roof' },
    ST: { ja: '階段', en: 'Stair', load_bearing: null, element_group: 'stair' },
    RS: { ja: '軒裏', en: 'Soffit (Eaves Underside)', load_bearing: false, element_group: 'soffit' },
    PR: { ja: 'ひさし', en: 'Pent Roof / Canopy', load_bearing: false, element_group: 'canopy' },
    CE: { ja: '強化天井', en: 'Fire-Resistant Ceiling', load_bearing: false, element_group: 'ceiling' },
    WL: { ja: '壁（区画貫通部）', en: 'Wall (Penetration)', load_bearing: null, element_group: 'wall' }
  };

  /** Built-in fallback exact prefix rules (grounded in MLIT 001069853.pdf & 001288118.pdf) */
  const FALLBACK_EXACT_CODES = {
    // 防火材料
    NM: { group: '防火材料', category: '不燃材料', sub_category: '一般', duration_min: 20, part: '材料全般', load_bearing: null, law_article: '建築基準法第2条第九号・施行令第108条の2', description: '加熱開始後20分間燃焼しない等の性能を有する不燃材料' },
    NE: { group: '防火材料', category: '不燃材料', sub_category: '外部仕上用', duration_min: 20, part: '外部仕上げ', load_bearing: null, law_article: '建築基準法第2条第九号・施行令第108条の2第一号・第二号', description: '外部の仕上げに用いる場合に限る不燃材料' },
    QM: { group: '防火材料', category: '準不燃材料', sub_category: '一般', duration_min: 10, part: '材料全般', load_bearing: null, law_article: '建築基準法施行令第1条第五号・第108条の2', description: '加熱開始後10分間燃焼しない等の性能を有する準不燃材料' },
    QE: { group: '防火材料', category: '準不燃材料', sub_category: '外部仕上用', duration_min: 10, part: '外部仕上げ', load_bearing: null, law_article: '建築基準法施行令第1条第五号・第108条の2第一号・第二号', description: '外部の仕上げに用いる場合に限る準不燃材料' },
    RM: { group: '防火材料', category: '難燃材料', sub_category: '一般', duration_min: 5, part: '材料全般', load_bearing: null, law_article: '建築基準法施行令第1条第六号・第108条の2', description: '加熱開始後5分間燃焼しない等の性能を有する難燃材料' },
    RE: { group: '防火材料', category: '難燃材料', sub_category: '外部仕上用', duration_min: 5, part: '外部仕上げ', load_bearing: null, law_article: '建築基準法施行令第1条第六号・第108条の2第一号・第二号', description: '外部の仕上げに用いる場合に限る難燃材料' },
    // 防火設備
    EA: { group: '防火設備', category: '特定防火設備', sub_category: '防火区画の開口部（60分遮炎）', duration_min: 60, part: '開口部（防火設備）', load_bearing: null, law_article: '建築基準法施行令第112条第1項', description: '防火区画の開口部に設ける1時間遮炎性能を有する特定防火設備' },
    EB: { group: '防火設備', category: '防火設備（遮炎性能）', sub_category: '耐火建築物等の外壁開口部（20分遮炎）', duration_min: 20, part: '外壁開口部（防火設備）', load_bearing: null, law_article: '建築基準法第2条第九号の二ロ・施行令第109条の2', description: '耐火建築物等の延焼のおそれのある部分の外壁開口部に設ける20分遮炎防火設備' },
    EC: { group: '防火設備', category: '防火設備（準遮炎性能）', sub_category: '防火地域等の外壁開口部（20分準遮炎）', duration_min: 20, part: '外壁開口部（防火設備）', load_bearing: null, law_article: '建築基準法第61条・施行令第136条の2の3（旧法第64条）', description: '防火地域・準防火地域内の外壁開口部に設ける20分準遮炎防火設備' },
    EBN: { group: '防火設備', category: '防火設備（両面遮炎・延焼防止等）', sub_category: '外壁開口部等', duration_min: 20, part: '開口部（防火設備）', load_bearing: null, law_article: '建築基準法第2条第九号の二ロ・施行令第109条の2関連', description: '遮炎性能に係る防火設備（新区分・個別仕様は認定書原本を確認）' },
    ECN: { group: '防火設備', category: '防火設備（準遮炎・延焼防止等）', sub_category: '外壁開口部等', duration_min: 20, part: '開口部（防火設備）', load_bearing: null, law_article: '建築基準法施行令第136条の2の3関連', description: '準遮炎性能に係る防火設備（新区分・個別仕様は認定書原本を確認）' },
    ED: { group: '防火設備', category: '防火ダンパー等', sub_category: '風道貫通部', duration_min: null, part: '風道・ダクト', load_bearing: null, law_article: '建築基準法施行令第112条第21項（旧第16項）', description: '界壁・防火区画等を貫通する風道に設ける防火設備（防火ダンパー等）' },
    CAT: { group: '防火設備', category: '防火設備の作動性能等', sub_category: '熱感知器連動', duration_min: null, part: '自動閉鎖機構', load_bearing: null, law_article: '建築基準法施行令第112条第19項第一号（旧第14項第一号）', description: '熱感知器と連動した防火設備の自動閉鎖機構' },
    CAS: { group: '防火設備', category: '防火設備の作動性能等', sub_category: '煙感知器連動', duration_min: null, part: '自動閉鎖機構', load_bearing: null, law_article: '建築基準法施行令第112条第19項第二号（旧第14項第二号）', description: '煙感知器と連動した防火設備の自動閉鎖機構' },
    // 飛び火構造（屋根）
    DR: { group: '防耐火構造', category: '飛び火構造（屋根）', sub_category: '防火地域・準防火地域（一般）', duration_min: null, part: '屋根', load_bearing: false, law_article: '建築基準法第62条・施行令第136条の2の2第一号・第二号（旧法第63条）', description: '防火地域又は準防火地域内の建築物の屋根（火の粉に対する不燃性能）' },
    DW: { group: '防耐火構造', category: '飛び火構造（屋根）', sub_category: '防火地域・準防火地域（不燃性物品保管倉庫）', duration_min: null, part: '屋根', load_bearing: false, law_article: '建築基準法第62条・施行令第136条の2の2第一号', description: '防火地域等の不燃性物品保管倉庫用屋根' },
    UR: { group: '防耐火構造', category: '飛び火構造（屋根）', sub_category: '法22条指定区域（一般）', duration_min: null, part: '屋根', load_bearing: false, law_article: '建築基準法第22条第1項・施行令第109条の5第一号・第二号', description: '法22条区域内にある建築物の屋根（飛び火性能）' },
    UW: { group: '防耐火構造', category: '飛び火構造（屋根）', sub_category: '法22条指定区域（不燃性物品保管倉庫）', duration_min: null, part: '屋根', load_bearing: false, law_article: '建築基準法第22条第1項・施行令第109条の5第一号', description: '法22条区域内の不燃性物品保管倉庫用屋根' },
    // その他の防耐火構造
    CC: { group: '防耐火構造', category: '柱の防火被覆', sub_category: '鋼構造柱の防火被覆', duration_min: null, part: '柱', load_bearing: true, law_article: '建築基準法施行令第70条', description: '鉄骨造の柱の防火被覆' },
    CCN: { group: '防耐火構造', category: '不燃性を有する建築物の部分', sub_category: '煙突周囲等', duration_min: null, part: '煙突周囲等', load_bearing: null, law_article: '建築基準法施行令第115条第1項第三号ロ', description: '煙突周囲等の不燃性を有する建築物の部分' },
    FPC: { group: '防耐火構造', category: '煙突周囲の防火措置', sub_category: '煙突周囲の不燃性', duration_min: null, part: '煙突周囲', load_bearing: null, law_article: '建築基準法施行令第115条関連', description: '煙突周囲の建築物の部分の不燃性' },
    // 遮音・設備・シックハウス
    SOI: { group: '遮音構造', category: '界壁の遮音構造', sub_category: '長屋・共同住宅の界壁', duration_min: null, part: '界壁', load_bearing: null, law_article: '建築基準法第30条・施行令第22条の3', description: '長屋又は共同住宅の各戸の界壁に求められる遮音構造' },
    SOC: { group: '遮音構造', category: '界壁・界床の遮音構造', sub_category: '遮音関連認定', duration_min: null, part: '界壁・界床', load_bearing: null, law_article: '建築基準法第30条・施行令第22条の3関連', description: '遮音構造に係る認定' },
    VEC: { group: '建築設備等', category: '火気使用室の換気設備', sub_category: '換気設備', duration_min: null, part: '換気設備', load_bearing: null, law_article: '建築基準法施行令第20条の3第2項第一号ロ', description: '火気使用室に設ける換気設備' },
    LAE: { group: '建築設備等', category: '非常用照明装置', sub_category: '非常用照明', duration_min: null, part: '照明設備', load_bearing: null, law_article: '建築基準法施行令第126条の5第二号', description: '非常用の照明装置' },
    MF2: { group: 'シックハウス対策', category: 'ホルムアルデヒド発散建築材料', sub_category: '第2種ホルムアルデヒド発散建築材料（F☆☆）', duration_min: null, part: '建築材料', load_bearing: null, law_article: '建築基準法施行令第20条の7第2項', description: '第2種ホルムアルデヒド発散建築材料' },
    MF3: { group: 'シックハウス対策', category: 'ホルムアルデヒド発散建築材料', sub_category: '第3種ホルムアルデヒド発散建築材料（F☆☆☆）', duration_min: null, part: '建築材料', load_bearing: null, law_article: '建築基準法施行令第20条の7第3項', description: '第3種ホルムアルデヒド発散建築材料' },
    MFN: { group: 'シックハウス対策', category: 'ホルムアルデヒド発散建築材料', sub_category: '規制対象外（F☆☆☆☆）', duration_min: null, part: '建築材料', load_bearing: null, law_article: '建築基準法施行令第20条の7第4項', description: 'ホルムアルデヒド発散量が極めて少ない規制対象外材料（F☆☆☆☆相当）' },
    RLFC: { group: 'シックハウス対策', category: '居室のホルムアルデヒド対策', sub_category: '年間ホルムアルデヒド量を保つ居室', duration_min: null, part: '居室', load_bearing: null, law_article: '建築基準法施行令第20条の7第9項', description: '年間を通じてホルムアルデヒド濃度を基準以下に保つ居室の構造' },
    // 壁倍率・接合部・指定建築材料
    FRM: { group: '構造・壁倍率', category: '木造軸組工法の壁倍率', sub_category: '軸組壁倍率', duration_min: null, part: '耐力壁（木造軸組）', load_bearing: true, law_article: '建築基準法施行令第46条第4項表一の（八）', description: '木造軸組工法における耐力壁の倍率認定' },
    TBFC: { group: '構造・壁倍率', category: '枠組壁工法の壁倍率', sub_category: '枠組壁倍率（2×4）', duration_min: null, part: '耐力壁（枠組壁）', load_bearing: true, law_article: '建築基準法施行規則第8条の3・平成13年国交告第1540号', description: '枠組壁工法（ツーバイフォー）における耐力壁の倍率認定' },
    JM: { group: '構造・接合部', category: '鋼材の接合部', sub_category: '鋼材の接合', duration_min: null, part: '接合部', load_bearing: true, law_article: '建築基準法施行令第67条第1項', description: '鉄骨造の鋼材の接合方法' },
    JC: { group: '構造・接合部', category: '継手又は仕口の構造', sub_category: '継手・仕口', duration_min: null, part: '継手・仕口', load_bearing: true, law_article: '建築基準法施行令第67条第2項', description: '鉄骨造の継手又は仕口の構造方法' },
    HSB: { group: '構造・接合部', category: '高力ボルト接合', sub_category: '高力ボルト', duration_min: null, part: '接合部', load_bearing: true, law_article: '建築基準法施行令第68条第3項', description: '高力ボルト接合の構造方法' },
    // 指定建築材料（法第37条第二号）
    MSTL: { group: '指定建築材料', category: '指定建築材料', sub_category: '構造用鋼材及び鋳鋼', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: '構造用鋼材及び鋳鋼の品質認定' },
    MBLT: { group: '指定建築材料', category: '指定建築材料', sub_category: '高力ボルト及びボルト', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: '高力ボルト及びボルトの品質認定' },
    MCBL: { group: '指定建築材料', category: '指定建築材料', sub_category: '構造用ケーブル', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: '構造用ケーブルの品質認定' },
    MSRB: { group: '指定建築材料', category: '指定建築材料', sub_category: '鉄筋', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: '鉄筋の品質認定' },
    MWLD: { group: '指定建築材料', category: '指定建築材料', sub_category: '溶接材料', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: '溶接材料（炭素鋼・ステンレス鋼・アルミニウム）の品質認定' },
    MTRN: { group: '指定建築材料', category: '指定建築材料', sub_category: 'ターンバックル', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: 'ターンバックルの品質認定' },
    MCON: { group: '指定建築材料', category: '指定建築材料', sub_category: 'コンクリート', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: 'コンクリートの品質認定' },
    MBLK: { group: '指定建築材料', category: '指定建築材料', sub_category: 'コンクリートブロック', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: 'コンクリートブロックの品質認定' },
    MVBR: { group: '指定建築材料', category: '指定建築材料', sub_category: '免震材料', duration_min: null, part: '免震材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: '免震材料（積層ゴム・ダンパー等）の品質認定' },
    MWGM: { group: '指定建築材料', category: '指定建築材料', sub_category: '木質接着成形軸材料', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: '木質接着成形軸材料の品質認定' },
    MWCM: { group: '指定建築材料', category: '指定建築材料', sub_category: '木質複合軸材料', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: '木質複合軸材料の品質認定' },
    MWTP: { group: '指定建築材料', category: '指定建築材料', sub_category: '木質断熱複合パネル', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: '木質断熱複合パネルの品質認定' },
    MWGP: { group: '指定建築材料', category: '指定建築材料', sub_category: '木質接着複合パネル', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: '木質接着複合パネルの品質認定' },
    MTPS: { group: '指定建築材料', category: '指定建築材料', sub_category: 'タッピンねじ等', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: 'タッピンねじその他これに類するものの品質認定' },
    MPIN: { group: '指定建築材料', category: '指定建築材料', sub_category: '打込み鋲', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: '打込み鋲の品質認定' },
    MALA: { group: '指定建築材料', category: '指定建築材料', sub_category: 'アルミニウム合金材', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: 'アルミニウム合金材の品質認定' },
    MMJT: { group: '指定建築材料', category: '指定建築材料', sub_category: 'トラス用機械式継手', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: 'トラス用機械式継手の品質認定' },
    MMEM: { group: '指定建築材料', category: '指定建築材料', sub_category: '膜材料・テント倉庫用膜材料', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: '膜材料及びテント倉庫用膜材料の品質認定' },
    MCMU: { group: '指定建築材料', category: '指定建築材料', sub_category: 'セラミックメーソンリーユニット', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: 'セラミックメーソンリーユニットの品質認定' },
    MAEN: { group: '指定建築材料', category: '指定建築材料', sub_category: '石綿飛散防止材', duration_min: null, part: '建築材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: '石綿飛散防止材の品質認定' },
    MTDN: { group: '指定建築材料', category: '指定建築材料', sub_category: '緊張材', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: 'プレストレストコンクリート用緊張材の品質認定' },
    MALC: { group: '指定建築材料', category: '指定建築材料', sub_category: '軽量気泡コンクリート（ALC）', duration_min: null, part: '構造材料', load_bearing: null, law_article: '建築基準法第37条第二号', description: '軽量気泡コンクリートパネル（ALC）の品質認定' },
    // 鉄骨製作工場・構造規定
    TFBJ: { group: '鉄骨製作工場・構造規定', category: '鉄骨製作工場認定', sub_category: 'Jグレード', duration_min: null, part: '鉄骨溶接部', load_bearing: true, law_article: '建築基準法施行規則第1条の3第1項第一号', description: '鉄骨製作工場における溶接部認定（Jグレード）' },
    TFBR: { group: '鉄骨製作工場・構造規定', category: '鉄骨製作工場認定', sub_category: 'Rグレード', duration_min: null, part: '鉄骨溶接部', load_bearing: true, law_article: '建築基準法施行規則第1条の3第1項第一号', description: '鉄骨製作工場における溶接部認定（Rグレード）' },
    TFBM: { group: '鉄骨製作工場・構造規定', category: '鉄骨製作工場認定', sub_category: 'Mグレード', duration_min: null, part: '鉄骨溶接部', load_bearing: true, law_article: '建築基準法施行規則第1条の3第1項第一号', description: '鉄骨製作工場における溶接部認定（Mグレード）' },
    TFBH: { group: '鉄骨製作工場・構造規定', category: '鉄骨製作工場認定', sub_category: 'Hグレード', duration_min: null, part: '鉄骨溶接部', load_bearing: true, law_article: '建築基準法施行規則第1条の3第1項第一号', description: '鉄骨製作工場における溶接部認定（Hグレード）' },
    TFBS: { group: '鉄骨製作工場・構造規定', category: '鉄骨製作工場認定', sub_category: 'Sグレード', duration_min: null, part: '鉄骨溶接部', load_bearing: true, law_article: '建築基準法施行規則第1条の3第1項第一号', description: '鉄骨製作工場における溶接部認定（Sグレード）' },
    TPRG: { group: '構造規定・その他', category: '構造計算プログラム', sub_category: '大臣認定プログラム', duration_min: null, part: '構造計算', load_bearing: null, law_article: '建築基準法施行規則第1条の3第1項本文', description: '構造計算プログラムの認定' },
    TPPG: { group: '構造規定・その他', category: 'プレハブ駐車場', sub_category: '型式適合・認定', duration_min: null, part: '工作物・建築物', load_bearing: null, law_article: '建築基準法施行規則第1条の3第1項本文', description: 'プレハブ駐車場の認定' },
    TACP: { group: '構造規定・その他', category: '基礎ぐいの許容支持力', sub_category: '杭工法認定', duration_min: null, part: '基礎ぐい', load_bearing: true, law_article: '建築基準法施行規則第1条の3第1項本文', description: '基礎ぐいの許容支持力に係る構造方法認定' },
    TSTB: { group: '構造規定・その他', category: '図書省略（鉄骨造）', sub_category: '型式適合認定等', duration_min: null, part: '鉄骨造建築物', load_bearing: null, law_article: '建築基準法施行規則第1条の3第1項本文', description: '建築物等の図書省略（鉄骨造）' },
    TWDB: { group: '構造規定・その他', category: '図書省略（木造）', sub_category: '型式適合認定等', duration_min: null, part: '木造建築物', load_bearing: null, law_article: '建築基準法施行規則第1条の3第1項本文', description: '建築物等の図書省略（木造）' },
    TRCB: { group: '構造規定・その他', category: '図書省略（RC造）', sub_category: '型式適合認定等', duration_min: null, part: 'RC造建築物', load_bearing: null, law_article: '建築基準法施行規則第1条の3第1項本文', description: '建築物等の図書省略（鉄筋コンクリート造）' },
    TOPB: { group: '構造規定・その他', category: '既存不適格の段階改修', sub_category: '二以上の工期に分ける工事', duration_min: null, part: '既存建築物', load_bearing: null, law_article: '建築基準法施行規則第1条の3関連', description: '二以上の工期に分ける既存不適格建築物の認定' }
  };

  /** Building 7-letter certification code positions (MLIT 001069848.pdf) */
  const BUILDING_CODE_POSITIONS = [
    { pos: 1, title: '構造計算（時刻歴）', map: { H: '超高層（法第20条）', M: '超高層以外（法第20条）', S: '工作物（令第139・140・144条）', N: '非該当' } },
    { pos: 2, title: '主要構造部の耐火性能', map: { F: '耐火性能（令第108条の3第1項）', S: '延焼拡大防止性能（令第112条第3項）', N: '非該当' } },
    { pos: 3, title: '防火設備（防火区画）', map: { E: '該当（令第108条の3第4項）', N: '非該当' } },
    { pos: 4, title: '避難安全性能', map: { B: '全館避難（令第129条の2第1項）', F: '階避難（令第129条第1項）', S: '区画避難（令第128条の6第1項）', N: '非該当' } },
    { pos: 5, title: '主要構造部等の性能', map: { L: '大規模建築物（法第21条第1項）', S: '特殊建築物（法第27条第1項）', P: '防火・準防火地域（法第61条第1項）', N: '非該当' } },
    { pos: 6, title: '防排煙性能', map: { S: '特別避難階段の階段室等（令第123条第3項二号）', E: '非常用EV昇降路等（令第129条の13の3第13項）', W: '兼用付室（令第123条・第129条の13の3）', N: '非該当' } },
    { pos: 7, title: '非常用進入口', map: { E: '該当（令第126条の6第1項三号）', N: '非該当' } }
  ];

  /** Runtime rules state (merged with code_rules.json when loaded) */
  const state = {
    seinokyoUrl: DEFAULT_SEINOKYO_URL,
    structureTypes: Object.assign({}, FALLBACK_STRUCTURE_TYPES),
    partCodes: Object.assign({}, FALLBACK_PART_CODES),
    exactCodes: Object.assign({}, FALLBACK_EXACT_CODES),
    knownLedgerPrefixes: new Set(),
    rulesLoaded: false
  };

  /**
   * Merge official code_rules.json produced by pipeline into the decoder state.
   * Handles both array and map forms of `exact_codes`.
   */
  function setCodeRules(rules) {
    if (!rules || typeof rules !== 'object') return;
    if (rules.seinokyo_search_url) {
      state.seinokyoUrl = rules.seinokyo_search_url;
    }
    if (rules.structure_types && typeof rules.structure_types === 'object') {
      Object.keys(rules.structure_types).forEach(function (k) {
        const key = String(k).toUpperCase();
        state.structureTypes[key] = Object.assign(
          {},
          state.structureTypes[key] || {},
          rules.structure_types[k]
        );
      });
    }
    if (rules.part_codes && typeof rules.part_codes === 'object') {
      Object.keys(rules.part_codes).forEach(function (k) {
        const key = String(k).toUpperCase();
        state.partCodes[key] = Object.assign(
          {},
          state.partCodes[key] || {},
          rules.part_codes[k]
        );
      });
    }
    if (rules.exact_codes) {
      if (Array.isArray(rules.exact_codes)) {
        rules.exact_codes.forEach(function (entry) {
          if (entry && entry.code) {
            const key = String(entry.code).toUpperCase();
            state.exactCodes[key] = Object.assign({}, state.exactCodes[key] || {}, entry);
          }
        });
      } else if (typeof rules.exact_codes === 'object') {
        Object.keys(rules.exact_codes).forEach(function (k) {
          const key = String(k).toUpperCase();
          state.exactCodes[key] = Object.assign({}, state.exactCodes[key] || {}, rules.exact_codes[k]);
        });
      }
    }
    state.rulesLoaded = true;
  }

  /** Register additional valid prefixes observed in manifest.json */
  function registerManifestPrefixes(categories) {
    if (!Array.isArray(categories)) return;
    categories.forEach(function (cat) {
      if (cat && Array.isArray(cat.prefixes)) {
        cat.prefixes.forEach(function (p) {
          if (p) state.knownLedgerPrefixes.add(String(p).toUpperCase());
        });
      }
    });
  }

  function getSeinokyoUrl() {
    return state.seinokyoUrl || DEFAULT_SEINOKYO_URL;
  }

  function getRulesSnapshot() {
    return {
      seinokyoUrl: state.seinokyoUrl,
      structureTypes: state.structureTypes,
      partCodes: state.partCodes,
      exactCodes: state.exactCodes,
      rulesLoaded: state.rulesLoaded
    };
  }

  /**
   * Normalize fullwidth ASCII, Japanese hyphens/dashes, spaces, and case.
   * Example: "ＦＰ０６０ＢＥ－０１２３" -> "FP060BE-0123"
   */
  function normalizeCertNo(raw) {
    if (raw == null) return '';
    let s = String(raw).trim();
    // Fullwidth alphanumeric & ASCII punctuation (U+FF01..U+FF5E -> U+0021..U+007E)
    s = s.replace(/[\uFF01-\uFF5E]/g, function (ch) {
      return String.fromCharCode(ch.charCodeAt(0) - 0xFEE0);
    });
    // Fullwidth space to halfwidth
    s = s.replace(/\u3000/g, ' ');
    // Normalize all hyphen/dash/prolonged-sound variants
    s = s.replace(/[‐‑‒–—―−ー－﹣]/g, '-');
    // Remove whitespace around hyphens and internal spaces
    s = s.replace(/\s*-\s*/g, '-').replace(/\s+/g, '');
    return s.toUpperCase();
  }

  /**
   * Build visual token pill for UI rendering
   */
  function makeToken(text, label, detail, kind) {
    return { text: text, label: label, detail: detail || '', kind: kind || 'neutral' };
  }

  /**
   * Decode a 4/5/7-letter Building certification prefix (MLIT 001069848.pdf + legacy H25_before 4/5-char codes)
   */
  function tryDecodeBuildingCode(prefix, serial, branch) {
    if (!/^([HMSN][FSN][EN][BFSN]([LSPN]([SEWN][EN])?)?)$/.test(prefix)) return null;
    if (/^N+$/.test(prefix)) return null;
    const activeItems = [];
    const tokens = [];
    for (let i = 0; i < prefix.length; i++) {
      const ch = prefix[i];
      const def = BUILDING_CODE_POSITIONS[i];
      const meaning = (def && def.map[ch]) || ch;
      if (ch !== 'N' && def) {
        activeItems.push(def.title + '：' + meaning);
      }
      tokens.push(makeToken(ch, def ? def.title : ('区分' + (i + 1)), meaning, ch === 'N' ? 'muted' : 'structure'));
    }
    if (serial) {
      tokens.push(makeToken(serial, '通算番号', '建築物認定の通算番号', 'serial'));
    }
    if (branch) {
      tokens.push(makeToken(branch, '変更・枝番', '変更認定番号／枝番', 'branch'));
    }
    return {
      is_valid_format: true,
      structure_type: '建築物・工作物認定（' + prefix.length + '文字コード）',
      duration_min: null,
      part: prefix[0] === 'S' ? '工作物' : '建築物全体',
      part_code: null,
      element_group: 'building',
      load_bearing: null,
      group: '建築物／工作物（時刻歴・耐火・避難等）',
      category: prefix[0] === 'S' ? '工作物認定' : '建築物認定',
      sub_category: activeItems.join(' / '),
      law_article: activeItems.join('；'),
      description: '建築物・工作物関連の認定コード（' + activeItems.join('、') + '）',
      tokens: tokens
    };
  }

  /**
   * Decode Elevator / Escalator / Amusement Facility codes (MLIT 001069849.pdf + legacy 4-char codes)
   */
  function tryDecodeElevatorCode(prefix, serial, branch) {
    // Elevator: E + 3 or 5 flags (legacy 4-char E[PN][CN][SN] and current 6-char E[PN][CN][SN][UN][UN])
    if (/^E[PN][CN][SN]([UN][UN])?$/.test(prefix) && !/^EN+$/.test(prefix)) {
      const defs = [
        { idx: 1, title: 'かご・主要支持部分', code: 'P', law: '令第129条の4第1項三号' },
        { idx: 2, title: '制御器', code: 'C', law: '令第129条の8第2項' },
        { idx: 3, title: '制動装置', code: 'S', law: '令第129条の10第2項' },
        { idx: 4, title: '戸開走行保護装置', code: 'U', law: '令第129条の10第4項（三号一）' },
        { idx: 5, title: '地震時管制', code: 'U', law: '令第129条の10第4項（三号二）' }
      ];
      const active = [];
      const tokens = [makeToken('E', '設備種別', 'エレベーター', 'structure')];
      defs.forEach(function (d) {
        if (d.idx >= prefix.length) return;
        const ch = prefix[d.idx];
        const hit = ch === d.code;
        if (hit) active.push(d.title + '（' + d.law + '）');
        tokens.push(makeToken(ch, d.title, hit ? '該当：' + d.law : '非該当', hit ? 'part' : 'muted'));
      });
      if (serial) tokens.push(makeToken(serial, '通算番号', '昇降機認定の通算番号', 'serial'));
      if (branch) tokens.push(makeToken(branch, '変更・枝番', '枝番', 'branch'));
      return {
        is_valid_format: true,
        structure_type: 'エレベーター認定',
        duration_min: null,
        part: 'エレベーター',
        part_code: null,
        element_group: 'elevator',
        load_bearing: null,
        group: '昇降機・遊戯施設',
        category: 'エレベーター',
        sub_category: active.join(' / '),
        law_article: active.join('；'),
        description: '昇降機（エレベーター）に係る認定：' + active.join('、'),
        tokens: tokens
      };
    }
    // Escalator: ES + 3 flags
    if (/^ES[PN][SN][FN]$/.test(prefix) && prefix !== 'ESNNN') {
      const defs = [
        { idx: 2, title: 'かご・主要支持部分', code: 'P', law: '令第129条の12第2項' },
        { idx: 3, title: '制動装置', code: 'S', law: '令第129条の12第5項' },
        { idx: 4, title: '脱落防止', code: 'F', law: '令第129条の12第1項六号' }
      ];
      const active = [];
      const tokens = [makeToken('ES', '設備種別', 'エスカレーター', 'structure')];
      defs.forEach(function (d) {
        const ch = prefix[d.idx];
        const hit = ch === d.code;
        if (hit) active.push(d.title + '（' + d.law + '）');
        tokens.push(makeToken(ch, d.title, hit ? '該当：' + d.law : '非該当', hit ? 'part' : 'muted'));
      });
      if (serial) tokens.push(makeToken(serial, '通算番号', '昇降機認定の通算番号', 'serial'));
      if (branch) tokens.push(makeToken(branch, '変更・枝番', '枝番', 'branch'));
      return {
        is_valid_format: true,
        structure_type: 'エスカレーター認定',
        duration_min: null,
        part: 'エスカレーター',
        part_code: null,
        element_group: 'elevator',
        load_bearing: null,
        group: '昇降機・遊戯施設',
        category: 'エスカレーター',
        sub_category: active.join(' / '),
        law_article: active.join('；'),
        description: '昇降機（エスカレーター）に係る認定：' + active.join('、'),
        tokens: tokens
      };
    }
    // Amusement: A + 3 flags (also supports legacy APCS)
    if (/^A[PN][CSN][ESN]$/.test(prefix) && prefix !== 'ANNN') {
      const defs = [
        { idx: 1, title: '主要支持部分', code: 'P', law: '令第144条二号' },
        { idx: 2, title: '客席・制御部分', code: 'S', alt: 'C', law: '令第144条四号イ' },
        { idx: 3, title: '非常止め・制動装置', code: 'E', alt: 'S', law: '令第144条六号' }
      ];
      const active = [];
      const tokens = [makeToken('A', '種別', '遊戯施設', 'structure')];
      defs.forEach(function (d) {
        const ch = prefix[d.idx];
        const hit = ch === d.code || (d.alt && ch === d.alt);
        if (hit) active.push(d.title + '（' + d.law + '）');
        tokens.push(makeToken(ch, d.title, hit ? '該当：' + d.law : '非該当', hit ? 'part' : 'muted'));
      });
      if (serial) tokens.push(makeToken(serial, '通算番号', '遊戯施設認定の通算番号', 'serial'));
      if (branch) tokens.push(makeToken(branch, '変更・枝番', '枝番', 'branch'));
      return {
        is_valid_format: true,
        structure_type: '遊戯施設認定',
        duration_min: null,
        part: '遊戯施設',
        part_code: null,
        element_group: 'amusement',
        load_bearing: null,
        group: '昇降機・遊戯施設',
        category: '遊戯施設',
        sub_category: active.join(' / '),
        law_article: active.join('；'),
        description: '遊戯施設に係る認定：' + active.join('、'),
        tokens: tokens
      };
    }
    return null;
  }

  /**
   * Core deterministic decoder for any Japanese Minister Certification number.
   * @param {string} rawInput
   * @returns {object} structured decode result
   */
  function decodeCertNo(rawInput) {
    const normalized = normalizeCertNo(rawInput);
    const warnings = [];

    if (!normalized) {
      return {
        raw: rawInput || '',
        cert_no: '',
        prefix: '',
        serial: '',
        branch: null,
        structure_type: null,
        structure_code: null,
        duration_min: null,
        part: null,
        part_code: null,
        element_group: null,
        load_bearing: null,
        group: null,
        category: null,
        sub_category: null,
        law_article: null,
        description: null,
        tokens: [],
        is_valid_format: false,
        warnings: ['認定番号が入力されていません。']
      };
    }

    // Split by hyphen: PREFIX-SERIAL(-BRANCH)? with optional parenthesized sub-number e.g. FP030RF-2027(2) or FP060FL-0093-2(1)
    const parts = normalized.split('-');
    const prefix = parts[0] || '';
    let serial = parts.length >= 2 ? parts[1] : '';
    let branch = parts.length >= 3 ? parts.slice(2).join('-') : null;

    // Separate parenthesized sub-number attached directly to serial, e.g. "2027(2)" -> serial="2027", branch="(2)"
    if (serial) {
      const parenMatch = serial.match(/^([0-9]+[A-Z]?)(\([0-9A-Z]+\))$/);
      if (parenMatch) {
        serial = parenMatch[1];
        branch = branch ? (parenMatch[2] + '-' + branch) : parenMatch[2];
      }
    }

    const isShortSerialAllowed = /^(LAE|VEC|DW\d[A-Z0-9]*)$/.test(prefix);
    const serialRe = isShortSerialAllowed ? /^\d{1,6}[A-Z]?$/ : /^\d{3,6}[A-Z]?$/;

    if (parts.length < 2 || !serial) {
      warnings.push('ハイフン区切りの通算番号（例：-0001）が不足しています。記号部分のみを参考解析します。');
    } else if (!serialRe.test(serial)) {
      warnings.push('通算番号「' + serial + '」が標準的な数字形式（通常4桁または5桁）と異なります。');
    }

    const base = {
      raw: rawInput,
      cert_no: normalized,
      prefix: prefix,
      serial: serial,
      branch: branch,
      structure_type: null,
      structure_code: null,
      duration_min: null,
      part: null,
      part_code: null,
      element_group: null,
      load_bearing: null,
      group: null,
      category: null,
      sub_category: null,
      law_article: null,
      description: null,
      tokens: [],
      is_valid_format: false,
      warnings: warnings
    };

    const hasValidSerial = Boolean(serial && serialRe.test(serial));

    // 1. Compound fire/penetration pattern: e.g. FP060BE, QF045BE, QF075CN, PC030RS, QP020BE, PS060WL, NH030FL, FI020RF
    const compoundMatch = prefix.match(/^([A-Z]{2})(\d{3})([A-Z]{2,3})$/);
    if (compoundMatch) {
      const sCode = compoundMatch[1];
      const durStr = compoundMatch[2];
      const durMin = parseInt(durStr, 10);
      const pCode = compoundMatch[3];

      const sDef = state.structureTypes[sCode];
      const pDef = state.partCodes[pCode];
      const exactDef = state.exactCodes[prefix];

      const tokens = [];
      tokens.push(makeToken(
        sCode,
        '構造種別',
        sDef ? sDef.ja + (sDef.en ? ' (' + sDef.en + ')' : '') : '未定義の構造種別記号',
        sDef ? 'structure' : 'error'
      ));
      tokens.push(makeToken(
        durStr,
        '耐火・遮炎時間',
        durMin + '分間' + (durMin >= 60 && durMin % 60 === 0 ? '（' + (durMin / 60) + '時間）' : ''),
        (durMin >= 15 && durMin <= 300) ? 'duration' : 'error'
      ));
      tokens.push(makeToken(
        pCode,
        '対象部位',
        pDef ? pDef.ja + (pDef.en ? ' (' + pDef.en + ')' : '') : '未定義の部位コード',
        pDef ? 'part' : 'error'
      ));
      if (serial) {
        tokens.push(makeToken(serial, '通算番号', '認定通算番号', hasValidSerial ? 'serial' : 'error'));
      }
      if (branch) {
        tokens.push(makeToken(branch, '枝番', '変更・枝番', 'branch'));
      }

      if (!sDef) warnings.push('構造種別コード「' + sCode + '」は国交省標準記号に定義されていません。');
      if (!pDef) warnings.push('部位コード「' + pCode + '」は国交省標準記号に定義されていません。');
      if (durMin < 15 || durMin > 300) {
        warnings.push('性能時間「' + durStr + '（' + durMin + '分）」が一般的な防耐火基準時間と大きく異なります。');
      }

      const validCompound = Boolean(sDef && pDef && durMin >= 15 && durMin <= 300 && hasValidSerial);

      // Determine article with precision (60-min QF has special article 令129条の2の3)
      let lawArticle = (exactDef && exactDef.law_article) || (sDef && sDef.law_article) || null;
      if (!exactDef && sCode === 'QF' && durMin === 60) {
        lawArticle = '建築基準法施行令第129条の2の3第1項第一号ロ（1時間準耐火構造）';
      } else if (!exactDef && sCode === 'QF' && (durMin === 75 || durMin === 90)) {
        lawArticle = '建築基準法第2条第七号の二・施行令第107条の2（75分・90分準耐火構造・令和元年改正等）';
      } else if (!exactDef && sCode === 'QF') {
        lawArticle = '建築基準法第2条第七号の二・施行令第107条の2';
      }

      return Object.assign(base, {
        structure_type: sDef ? sDef.ja : (exactDef ? exactDef.category : null),
        structure_code: sCode,
        duration_min: durMin,
        part: pDef ? pDef.ja : (exactDef ? exactDef.part : null),
        part_code: pCode,
        element_group: pDef ? (pDef.element_group || inferElementGroup(pCode, pDef.ja)) : null,
        load_bearing: pDef && typeof pDef.load_bearing === 'boolean'
          ? pDef.load_bearing
          : (exactDef && typeof exactDef.load_bearing === 'boolean' ? exactDef.load_bearing : null),
        group: (exactDef && exactDef.group) || (sCode === 'PS' ? '防火区画貫通部' : '防耐火構造'),
        category: (exactDef && exactDef.category) || (sDef ? sDef.ja : '防耐火構造'),
        sub_category: (exactDef && exactDef.sub_category) || (durMin + '分間 / ' + (pDef ? pDef.ja : pCode)),
        law_article: lawArticle,
        description: (exactDef && exactDef.description) ||
          ((sDef ? sDef.ja : sCode) + '・' + durMin + '分間・' + (pDef ? pDef.ja : pCode) +
            (exactDef ? '' : '（標準記号規則からの自動解析）')),
        tokens: tokens,
        is_valid_format: validCompound
      });
    }

    // 2. Exact code match (e.g. NM, NE, QM, EA, EB, EC, SOI, FRM, TBFC, MSTL, TFBH, MF2, etc.)
    const exact = state.exactCodes[prefix];
    if (exact) {
      const tokens = [
        makeToken(prefix, exact.group || '認定記号', exact.category + (exact.sub_category ? '（' + exact.sub_category + '）' : ''), 'structure')
      ];
      if (exact.duration_min) {
        tokens.push(makeToken(exact.duration_min + '分', '基準時間', '対象法令に基づく標準性能時間', 'duration'));
      }
      if (serial) {
        tokens.push(makeToken(serial, '通算番号', '認定通算番号', hasValidSerial ? 'serial' : 'error'));
      }
      if (branch) {
        tokens.push(makeToken(branch, '枝番', '変更・枝番', 'branch'));
      }

      return Object.assign(base, {
        structure_type: exact.category || exact.group,
        structure_code: prefix,
        duration_min: typeof exact.duration_min === 'number' ? exact.duration_min : null,
        part: exact.part || null,
        part_code: null,
        element_group: inferElementGroup(prefix, exact.part || exact.category || ''),
        load_bearing: typeof exact.load_bearing === 'boolean' ? exact.load_bearing : null,
        group: exact.group || exact.category,
        category: exact.category || prefix,
        sub_category: exact.sub_category || null,
        law_article: exact.law_article || null,
        description: exact.description || (exact.category + (exact.sub_category ? '（' + exact.sub_category + '）' : '')),
        tokens: tokens,
        is_valid_format: hasValidSerial
      });
    }

    // 3. Building 7-letter certification code (001069848.pdf)
    const bldg = tryDecodeBuildingCode(prefix, serial, branch);
    if (bldg) {
      bldg.is_valid_format = Boolean(bldg.is_valid_format && hasValidSerial);
      return Object.assign(base, bldg);
    }

    // 4. Elevator / Escalator / Amusement codes (001069849.pdf)
    const elev = tryDecodeElevatorCode(prefix, serial, branch);
    if (elev) {
      elev.is_valid_format = Boolean(elev.is_valid_format && hasValidSerial);
      return Object.assign(base, elev);
    }

    // 5. Septic tank (浄化槽) patterns e.g. DW3N-xxxx, DW5-xxxx, etc. or known ledger prefix from manifest.json
    if (/^DW\d[A-Z0-9]*$/.test(prefix)) {
      const tokens = [
        makeToken(prefix, '浄化槽認定記号', '合併処理浄化槽等の構造認定', 'structure')
      ];
      if (serial) tokens.push(makeToken(serial, '通算番号', '認定通算番号', hasValidSerial ? 'serial' : 'error'));
      return Object.assign(base, {
        structure_type: '浄化槽の構造認定',
        structure_code: prefix,
        part: '浄化槽',
        element_group: 'equipment',
        group: '建築設備等',
        category: '合併処理浄化槽の構造',
        sub_category: prefix,
        law_article: '建築基準法施行令第35条第1項等',
        description: '合併処理浄化槽等の構造方法認定記号',
        tokens: tokens,
        is_valid_format: hasValidSerial
      });
    }

    // 6. Prefix observed in manifest.json (even if not in 001069853 PDF table, e.g., special/newer prefixes)
    if (state.knownLedgerPrefixes.has(prefix)) {
      const tokens = [
        makeToken(prefix, '認定記号', '国交省公開帳簿に収録されている認定記号', 'structure')
      ];
      if (serial) tokens.push(makeToken(serial, '通算番号', '認定通算番号', hasValidSerial ? 'serial' : 'error'));
      if (branch) tokens.push(makeToken(branch, '枝番', '枝番', 'branch'));
      return Object.assign(base, {
        structure_type: '大臣認定（記号：' + prefix + '）',
        structure_code: prefix,
        group: '国交省帳簿収録記号',
        category: '認定記号 ' + prefix,
        sub_category: null,
        law_article: '各認定書・国交省帳簿を参照',
        description: '国土交通省公開帳簿に存在する認定記号です（詳細区分は帳簿照合結果または認定書原本をご確認ください）。',
        tokens: tokens,
        is_valid_format: hasValidSerial
      });
    }

    // 7. Unknown / malformed
    warnings.push('認定記号「' + prefix + '」は登録済みの国交省認定記号ルールに一致しません。転記ミスや独自表記でないかご確認ください。');
    const errTokens = [makeToken(prefix || normalized, '未定義記号', '既知の大臣認定記号と不一致', 'error')];
    if (serial) errTokens.push(makeToken(serial, '通算番号', '番号部', hasValidSerial ? 'serial' : 'error'));
    return Object.assign(base, {
      tokens: errTokens,
      is_valid_format: false
    });
  }

  function inferElementGroup(code, label) {
    const s = String(label || '');
    if (/外壁/.test(s)) return 'exterior_wall';
    if (/間仕切|界壁/.test(s)) return 'partition_wall';
    if (/柱/.test(s)) return 'column';
    if (/はり|梁/.test(s)) return 'beam';
    if (/床|スラブ/.test(s)) return 'floor';
    if (/屋根/.test(s)) return 'roof';
    if (/軒裏/.test(s)) return 'soffit';
    if (/階段/.test(s)) return 'stair';
    if (/天井/.test(s)) return 'ceiling';
    if (/開口部|防火設備/.test(s)) return 'opening';
    if (/壁/.test(s)) return 'wall';
    return null;
  }

  /**
   * Extract all candidate Minister Certification numbers from arbitrary text.
   * Returns list of `{ raw, cert_no, lineNo, lineText, source, decoded }`.
   */
  function extractCertNosFromText(text, sourceName) {
    if (!text) return [];
    const src = sourceName || '入力テキスト';
    const lines = String(text).split(/\r\n|\r|\n/);

    // Build known exact prefixes set for precision (avoids matching random words like ISO-9001 or UTF-8)
    const exactList = Object.keys(state.exactCodes).concat(Array.from(state.knownLedgerPrefixes));
    const exactSet = new Set(exactList);

    // Match candidate token: ASCII/fullwidth letters+digits prefix, hyphen variant, 2-6 digits (+ optional -branch / (sub))
    // Negative lookbehind/lookahead avoided for max browser compatibility; use boundary verification.
    const candidateRe = /([A-Za-zＡ-Ｚａ-ｚ][A-Za-z0-9Ａ-Ｚａ-ｚ０-９]{1,8})\s*[‐‑‒–—―−ー－﹣-]\s*([0-9０-９]{2,6}(?:[‐‑‒–—―−ー－﹣-][0-9０-９]{1,3})?(?:[(（][0-9０-９]{1,3}[)）])?)/g;

    const results = [];
    lines.forEach(function (rawLine, idx) {
      const lineNo = idx + 1;
      let match;
      candidateRe.lastIndex = 0;
      while ((match = candidateRe.exec(rawLine)) !== null) {
        const startIdx = match.index;
        const endIdx = startIdx + match[0].length;
        // Boundary check: ensure not glued inside an alphanumeric identifier/URL path like "abcFP060BE-0001"
        const prevCh = startIdx > 0 ? rawLine[startIdx - 1] : '';
        const nextCh = endIdx < rawLine.length ? rawLine[endIdx] : '';
        if (/[A-Za-z0-9_]/.test(prevCh) || /[A-Za-z0-9_]/.test(nextCh)) {
          continue;
        }

        const rawToken = match[0];
        const norm = normalizeCertNo(rawToken);
        const normParts = norm.split('-');
        const prefix = normParts[0];
        const rawSerialDigits = (normParts[1] || '').replace(/\(.+\)$/, '');

        // Only allow 2-digit serials for equipment prefixes that genuinely use them (LAE, VEC, DW*)
        if (rawSerialDigits.length < 3 && !/^(LAE|VEC|DW\d[A-Z0-9]*)$/.test(prefix)) {
          continue;
        }

        // Filter out obvious non-cert patterns (ISO-9001, JIS-1234, UTF-8, HTTP-200, CAD layer names, etc.)
        const isKnownExact = exactSet.has(prefix);
        const isCompoundLike = /^(FP|QF|PC|QP|PS|NH|FI)\d{3}[A-Z]{2,3}$/.test(prefix);
        // Also capture plausible typo of compound fire code (e.g. QF045XX-0001 or FP999BE-0001) so screener warns on typos!
        const isSuspiciousCompound = /^(FP|QF|PC|QP|PS)\d{2,3}[A-Z]{1,3}$/.test(prefix);
        const isBuilding = /^([HMSN][FSN][EN][BFSN]([LSPN]([SEWN][EN])?)?)$/.test(prefix) && !/^N+$/.test(prefix);
        const isElevator = (/^E[PN][CN][SN]([UN][UN])?$/.test(prefix) && !/^EN+$/.test(prefix)) ||
                           (/^ES[PN][SN][FN]$/.test(prefix) && prefix !== 'ESNNN') ||
                           (/^A[PN][CSN][ESN]$/.test(prefix) && prefix !== 'ANNN');
        const isSeptic = /^DW\d[A-Z0-9]*$/.test(prefix);

        if (!isKnownExact && !isCompoundLike && !isSuspiciousCompound && !isBuilding && !isElevator && !isSeptic) {
          continue;
        }

        const decoded = decodeCertNo(norm);
        results.push({
          raw: rawToken,
          cert_no: decoded.cert_no,
          prefix: decoded.prefix,
          lineNo: lineNo,
          lineText: rawLine.trim(),
          source: src,
          decoded: decoded
        });
      }
    });

    return results;
  }

  /**
   * Heuristic contradiction check between surrounding text context and decoded cert / ledger record.
   * Returns array of `{ severity: 'warning'|'error', code, message }`.
   */
  function analyzeContext(decoded, lineText, ledgerRecord) {
    const alerts = [];
    if (!decoded) return alerts;

    if (!decoded.is_valid_format) {
      alerts.push({
        severity: 'error',
        code: 'FORMAT_INVALID',
        message: '記号形式が国交省標準ルールと不一致（' + (decoded.warnings[0] || '要確認') + '）'
      });
    }

    // 0. Revocation / Expiration check against R5.4 ledger date_jp
    if (ledgerRecord && ledgerRecord.date_jp && /(取消|失効)/.test(ledgerRecord.date_jp)) {
      alerts.push({
        severity: 'error',
        code: 'REVOKED_OR_EXPIRED',
        message: 'R5.4帳簿上で取消・失効の記載あり（' + ledgerRecord.date_jp + '）：採用可否を必ず原本・公式DBで確認してください'
      });
    }

    const text = String(lineText || '');
    if (!text) return alerts;

    // 1. Fire-resistance duration mismatch check
    if (typeof decoded.duration_min === 'number' && ['FP', 'QF', 'PC', 'QP', 'PS'].indexOf(decoded.structure_code) !== -1) {
      const mentionedMinutes = new Set();
      // Match patterns like "60分", "45分間", "120分", "30分"
      const minMatches = text.matchAll(/(?:^|[^\d])(20|30|45|60|75|90|120|180|240)\s*分/g);
      for (const m of minMatches) {
        mentionedMinutes.add(parseInt(m[1], 10));
      }
      // Match patterns like "1時間", "2時間", "3時間", "１時間"
      const hrMatches = text.matchAll(/([123１２３])\s*時間/g);
      for (const m of hrMatches) {
        const d = m[1].replace(/[１２３]/g, function (c) { return String.fromCharCode(c.charCodeAt(0) - 0xFEE0); });
        mentionedMinutes.add(parseInt(d, 10) * 60);
      }

      if (mentionedMinutes.size > 0 && !mentionedMinutes.has(decoded.duration_min)) {
        const listStr = Array.from(mentionedMinutes).map(function (v) { return v + '分'; }).join('・');
        alerts.push({
          severity: 'warning',
          code: 'DURATION_MISMATCH',
          message: '耐火時間の不一致可能性：本文に「' + listStr + '」とあるが、認定記号は ' + decoded.duration_min + '分（' + decoded.prefix + '）'
        });
      }
    }

    // 2. Load-bearing vs Non-load-bearing mismatch
    if (decoded.load_bearing === false) {
      // Check if text mentions "耐力壁" without "非耐力壁"
      const cleaned = text.replace(/非耐力壁/g, '');
      if (/耐力壁/.test(cleaned)) {
        alerts.push({
          severity: 'warning',
          code: 'LOAD_BEARING_MISMATCH',
          message: '耐力区分の不一致可能性：本文に「耐力壁」とあるが、認定記号は非耐力壁（' + decoded.part_code + '：' + decoded.part + '）'
        });
      }
    } else if (decoded.load_bearing === true && (decoded.part_code === 'BE' || decoded.part_code === 'BP')) {
      if (/非耐力壁/.test(text)) {
        alerts.push({
          severity: 'warning',
          code: 'LOAD_BEARING_MISMATCH',
          message: '耐力区分の不一致可能性：本文に「非耐力壁」とあるが、認定記号は耐力壁（' + decoded.part_code + '：' + decoded.part + '）'
        });
      }
    }

    // 3. Structure type mismatch (e.g. "耐火構造" vs QF/PC)
    if (decoded.structure_code === 'QF' || decoded.structure_code === 'PC' || decoded.structure_code === 'QP') {
      const noQuasi = text.replace(/準耐火構造|準耐火|防耐火|耐火建築物|耐火時間/g, '');
      if (/耐火構造|耐火外壁|耐火間仕切|耐火壁|耐火耐力壁|耐火柱|耐火床|耐火はり|耐火梁|(?:60|120|180)分耐火/.test(noQuasi)) {
        alerts.push({
          severity: 'warning',
          code: 'STRUCTURE_TYPE_MISMATCH',
          message: '構造種別の不一致可能性：本文に「耐火」とあるが、認定記号は「' + decoded.structure_type + '（' + decoded.structure_code + '）」'
        });
      }
    }

    // 4. Fire material grade mismatch (e.g., "不燃" vs QM/RM)
    if (decoded.prefix === 'QM' || decoded.prefix === 'QE' || decoded.prefix === 'RM' || decoded.prefix === 'RE') {
      const noSemi = text.replace(/準不燃|難燃/g, '');
      if (/不燃材料|不燃ボード|不燃仕上/.test(noSemi)) {
        alerts.push({
          severity: 'warning',
          code: 'MATERIAL_GRADE_MISMATCH',
          message: '防火材料区分の不一致可能性：本文に「不燃」とあるが、認定記号は「' + decoded.category + '（' + decoded.prefix + '）」'
        });
      }
    }

    // 5. Building element / part mismatch
    if (decoded.element_group) {
      const eg = decoded.element_group;
      const isWallCode = eg === 'exterior_wall' || eg === 'partition_wall';
      if (isWallCode) {
        if (/(?:^|[^\w])(?:床構造|耐火床|準耐火床|床スラブ|界床)(?:[^\w]|$)/.test(text) && !/壁/.test(text)) {
          alerts.push({
            severity: 'warning',
            code: 'PART_MISMATCH',
            message: '部位の不一致可能性：本文は「床」に関する記述だが、認定記号は壁（' + decoded.part + '）'
          });
        } else if (eg === 'exterior_wall' && /間仕切壁|界壁/.test(text) && !/外壁/.test(text)) {
          alerts.push({
            severity: 'warning',
            code: 'PART_MISMATCH',
            message: '部位の不一致可能性：本文に「間仕切壁／界壁」とあるが、認定記号は外壁（' + decoded.part_code + '）'
          });
        } else if (eg === 'partition_wall' && /外壁/.test(text) && !/間仕切|界壁/.test(text)) {
          alerts.push({
            severity: 'warning',
            code: 'PART_MISMATCH',
            message: '部位の不一致可能性：本文に「外壁」とあるが、認定記号は間仕切壁（' + decoded.part_code + '）'
          });
        }
      } else if (eg === 'floor' && /外壁|間仕切壁/.test(text) && !/床|スラブ/.test(text)) {
        alerts.push({
          severity: 'warning',
          code: 'PART_MISMATCH',
          message: '部位の不一致可能性：本文に「壁」とあるが、認定記号は床（' + decoded.part + '）'
        });
      } else if (eg === 'column' && /(?:はり|梁|床)/.test(text) && !/柱/.test(text)) {
        alerts.push({
          severity: 'warning',
          code: 'PART_MISMATCH',
          message: '部位の不一致可能性：本文に柱の記述がなく別部位だが、認定記号は柱（' + decoded.part_code + '）'
        });
      }
    }

    return alerts;
  }

  return {
    setCodeRules: setCodeRules,
    registerManifestPrefixes: registerManifestPrefixes,
    getSeinokyoUrl: getSeinokyoUrl,
    getRulesSnapshot: getRulesSnapshot,
    normalizeCertNo: normalizeCertNo,
    decodeCertNo: decodeCertNo,
    extractCertNosFromText: extractCertNosFromText,
    analyzeContext: analyzeContext
  };
}));
