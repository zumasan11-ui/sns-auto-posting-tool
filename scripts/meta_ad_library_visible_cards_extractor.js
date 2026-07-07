(function () {
  "use strict";

  const DEFAULTS = {
    searchName: "",
    minDurationDays: 90,
    existingUrls: [],
    maxRows: 0,
    excludeVideoAds: true,
    excludeCarouselAds: true,
    sendToUrl: "",
    copyToClipboard: true,
  };

  const INTERNAL_HOSTS = [
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "fb.com",
    "meta.com",
    "www.meta.com",
    "instagram.com",
    "www.instagram.com",
  ];

  function normalizeSpace(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function normalizeUrl(value) {
    if (!value) return "";
    try {
      const url = new URL(value, location.href);
      if (url.hostname === "l.facebook.com" || url.hostname === "lm.facebook.com") {
        const target = url.searchParams.get("u");
        if (target) return target;
      }
      url.hash = "";
      return url.toString();
    } catch (_) {
      return String(value || "").trim();
    }
  }

  function isExternalUrl(value) {
    if (!value) return false;
    try {
      const url = new URL(value, location.href);
      return /^https?:$/.test(url.protocol) && !INTERNAL_HOSTS.includes(url.hostname);
    } catch (_) {
      return false;
    }
  }

  function parseLibraryId(text) {
    const patterns = [
      /ライブラリID[:：]?\s*([0-9]+)/i,
      /Library ID[:：]?\s*([0-9]+)/i,
      /Ad ID[:：]?\s*([0-9]+)/i,
    ];
    for (const pattern of patterns) {
      const match = text.match(pattern);
      if (match) return match[1];
    }
    return "";
  }

  function parseDateParts(year, month, day) {
    const y = Number(year);
    const m = Number(month);
    const d = Number(day);
    if (!y || !m || !d) return "";
    return `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
  }

  function parseStartDate(text) {
    const patterns = [
      /開始日[:：]?\s*(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日/i,
      /掲載開始日[:：]?\s*(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日/i,
      /Started running on\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})/i,
      /Started running\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})/i,
      /(?:掲載開始日|開始日)[:：]?\s*(\d{4})[年/-]\s*(\d{1,2})[月/-]\s*(\d{1,2})日?/,
      /(\d{4})[/-](\d{1,2})[/-](\d{1,2})/,
    ];
    const jp = text.match(patterns[0]) || text.match(patterns[1]);
    if (jp) return parseDateParts(jp[1], jp[2], jp[3]);

    const en = text.match(patterns[2]) || text.match(patterns[3]);
    if (en) {
      const month = {
        january: 1,
        february: 2,
        march: 3,
        april: 4,
        may: 5,
        june: 6,
        july: 7,
        august: 8,
        september: 9,
        october: 10,
        november: 11,
        december: 12,
      }[en[1].toLowerCase()];
      return parseDateParts(en[3], month, en[2]);
    }

    const jpSlash = text.match(patterns[4]);
    if (jpSlash) return parseDateParts(jpSlash[1], jpSlash[2], jpSlash[3]);

    const iso = text.match(patterns[5]);
    return iso ? parseDateParts(iso[1], iso[2], iso[3]) : "";
  }

  function durationDays(startDate) {
    if (!startDate) return null;
    const start = new Date(`${startDate}T00:00:00`);
    if (Number.isNaN(start.getTime())) return null;
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    return Math.max(0, Math.floor((today - start) / 86400000));
  }

  function formatDurationDisplay(days) {
    const dayCount = Number(days || 0);
    const months = Math.max(Math.floor(dayCount / 30), 0);
    if (dayCount < 365) return `${months}ヶ月`;
    const years = Math.floor(months / 12);
    const remainingMonths = months % 12;
    return remainingMonths ? `${years}年${remainingMonths}ヶ月` : `${years}年`;
  }

  function isVideoCard(root) {
    const text = normalizeSpace(root.innerText);
    if (root.querySelector("video")) return true;
    if (/動画を再生|Video player|\b0:00\s*\/\s*0:/i.test(text)) return true;
    return Array.from(root.querySelectorAll("[aria-label], [role]")).some((node) => {
      const label = normalizeSpace(node.getAttribute("aria-label") || node.getAttribute("role") || "");
      return /動画|video/i.test(label);
    });
  }

  function isCarouselCard(root) {
    const text = normalizeSpace(root.innerText);
    if (/カルーセル|carousel|(?:カード|Card)\s*\d+\s*(?:\/|／|of)\s*\d+/i.test(text)) return true;

    const labels = Array.from(root.querySelectorAll("[aria-label], [aria-roledescription], [title], [role], [data-testid]")).map((node) =>
      normalizeSpace(
        [
          node.getAttribute("aria-label"),
          node.getAttribute("aria-roledescription"),
          node.getAttribute("title"),
          node.getAttribute("role"),
          node.getAttribute("data-testid"),
        ]
          .filter(Boolean)
          .join(" ")
      )
    );
    if (labels.some((label) => /ad-library-ad-carousel-container|カルーセル|carousel|次のカード|前のカード|次のアイテム|前のアイテム|Next card|Previous card|Next item|Previous item/i.test(label))) {
      return true;
    }

    const navLikeCount = labels.filter((label) => /次へ|前へ|次のアイテム|前のアイテム|Next|Previous|戻る|進む/i.test(label)).length;
    const creativeImages = Array.from(root.querySelectorAll("img")).filter((img) => {
      const rect = img.getBoundingClientRect();
      return rect.width >= 120 && rect.height >= 80;
    });
    return navLikeCount >= 1 && creativeImages.length >= 2;
  }

  function findCardRoot(element) {
    let current = element;
    let best = element;
    while (current && current !== document.body) {
      const text = normalizeSpace(current.innerText);
      if (text.length > 200 && text.length < 9000 && parseLibraryId(text) && parseStartDate(text)) {
        best = current;
      }
      if (text.length > 9000) break;
      current = current.parentElement;
    }
    return best;
  }

  function findCandidateRoots() {
    const nodes = Array.from(document.querySelectorAll("div, article, section"));
    const byId = new Map();
    for (const node of nodes) {
      const text = normalizeSpace(node.innerText);
      if (!text || !parseLibraryId(text) || !parseStartDate(text)) continue;
      const root = findCardRoot(node);
      const id = parseLibraryId(normalizeSpace(root.innerText));
      if (id && !byId.has(id)) byId.set(id, root);
    }
    return Array.from(byId.values());
  }

  function linesFromText(text) {
    return String(text || "")
      .split(/\n+/)
      .map(normalizeSpace)
      .filter(Boolean);
  }

  function pickPageName(lines) {
    const ignored = /^(アクティブ|Active|広告|Sponsored|ライブラリID|Library ID|詳細を見る|See ad details|開始日|Started running)/i;
    for (const line of lines.slice(0, 12)) {
      if (ignored.test(line)) continue;
      if (/^https?:\/\//.test(line)) continue;
      if (line.length >= 2 && line.length <= 80) return line;
    }
    return "";
  }

  function pickPageNameFromLinks(root) {
    for (const anchor of Array.from(root.querySelectorAll("a[href]"))) {
      const text = normalizeSpace(anchor.innerText || anchor.getAttribute("aria-label") || "");
      if (!text || text.length > 80) continue;
      if (/^(広告|Sponsored|スポンサー広告|詳細を見る|広告の詳細を見る|Learn More|詳しくはこちら|詳細を表示)$/i.test(text)) continue;
      try {
        const url = new URL(anchor.href, location.href);
        if ((url.hostname === "www.facebook.com" || url.hostname === "facebook.com") && !url.pathname.startsWith("/ads/")) {
          return text;
        }
      } catch (_) {
        // Ignore malformed links and fall back to text parsing.
      }
    }
    return "";
  }

  function pickTitleAndBody(lines, pageName) {
    const ignored = /^(アクティブ|Active|広告|Sponsored|スポンサー広告|ライブラリID|Library ID|詳細を見る|See ad details|広告の詳細を見る|開始日|掲載開始日|Started running|プラットフォーム|Platforms|この広告には|ドロップダウンを開く|削除|Learn More|詳しくはこちら|詳細を表示)/i;
    const content = lines.filter((line) => {
      if (!line || line === pageName) return false;
      if (ignored.test(line)) return false;
      if (/^https?:\/\//.test(line)) return false;
      if (/^\d+$/.test(line)) return false;
      return line.length >= 3;
    });
    const title = content.find((line) => line.length <= 80) || "";
    const body = content.find((line) => line !== title && line.length >= 20) || content[0] || "";
    return { title, body };
  }

  function pickLandingUrl(root) {
    const links = Array.from(root.querySelectorAll("a[href]"))
      .map((anchor) => normalizeUrl(anchor.href))
      .filter(isExternalUrl);
    return Array.from(new Set(links))[0] || "";
  }

  function extractCard(root, options) {
    const rawText = root.innerText || "";
    const text = normalizeSpace(rawText);
    const lines = linesFromText(rawText);
    const libraryId = parseLibraryId(text);
    const startDate = parseStartDate(text);
    const days = durationDays(startDate);
    const isVideo = isVideoCard(root);
    const isCarousel = isCarouselCard(root);
    const adDisplayName = pickPageNameFromLinks(root) || pickPageName(lines);
    const titleAndBody = pickTitleAndBody(lines, adDisplayName);
    const adLibraryUrl = libraryId ? `https://www.facebook.com/ads/library/?id=${libraryId}` : "";
    return {
      "検索名": options.searchName,
      "会社名": "",
      "サービス名": adDisplayName,
      "掲載開始日": startDate,
      "掲載期間": formatDurationDisplay(days),
      "掲載期間日数": days,
      "広告ライブラリURL": adLibraryUrl,
      "LP URL": pickLandingUrl(root),
      "広告本文": titleAndBody.body,
      "広告タイトル": titleAndBody.title,
      "広告表示名": adDisplayName,
      "is_video": isVideo,
      "is_carousel": isCarousel,
      "media_type": isVideo ? "video" : isCarousel ? "carousel" : "image",
      "raw_text": text.slice(0, 4000),
      "_libraryId": libraryId,
    };
  }

  function csvEscape(value) {
    const text = value == null ? "" : String(value);
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }

  function toCsv(rows) {
    const headers = ["検索名", "会社名", "掲載開始日", "掲載期間", "広告ライブラリURL", "LP URL", "広告本文", "広告タイトル"];
    return [headers.join(","), ...rows.map((row) => headers.map((header) => csvEscape(row[header])).join(","))].join("\n");
  }

  function showResult(rows, csv) {
    const old = document.getElementById("meta-ad-visible-card-extractor-result");
    if (old) old.remove();

    const panel = document.createElement("div");
    panel.id = "meta-ad-visible-card-extractor-result";
    panel.style.cssText = [
      "position:fixed",
      "right:16px",
      "bottom:16px",
      "z-index:2147483647",
      "width:min(760px,calc(100vw - 32px))",
      "max-height:70vh",
      "background:#fff",
      "color:#111",
      "border:1px solid #ccc",
      "box-shadow:0 12px 36px rgba(0,0,0,.25)",
      "border-radius:8px",
      "padding:12px",
      "font:13px/1.5 system-ui,-apple-system,BlinkMacSystemFont,sans-serif",
    ].join(";");

    const title = document.createElement("div");
    title.textContent = `Meta広告カード抽出: ${rows.length}件`;
    title.style.cssText = "font-weight:700;margin-bottom:8px;";

    const textarea = document.createElement("textarea");
    textarea.value = csv;
    textarea.style.cssText = "width:100%;height:220px;box-sizing:border-box;font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;";

    const close = document.createElement("button");
    close.textContent = "閉じる";
    close.style.cssText = "margin-top:8px;margin-right:8px;";
    close.addEventListener("click", () => panel.remove());

    const copy = document.createElement("button");
    copy.textContent = "CSVをコピー";
    copy.style.cssText = "margin-top:8px;";
    copy.addEventListener("click", async () => {
      textarea.select();
      try {
        await navigator.clipboard.writeText(csv);
        copy.textContent = "コピー済み";
      } catch (_) {
        document.execCommand("copy");
        copy.textContent = "コピー済み";
      }
    });

    panel.append(title, textarea, close, copy);
    document.body.append(panel);
  }

  async function sendRows(rows, sendToUrl) {
    if (!sendToUrl || !rows.length) return null;
    const response = await fetch(sendToUrl, {
      method: "POST",
      mode: "cors",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: "meta_ad_library_visible_cards", rows }),
    });
    return response.text();
  }

  async function run(userOptions) {
    const options = Object.assign({}, DEFAULTS, userOptions || {});
    const existing = new Set(options.existingUrls || []);
    const roots = findCandidateRoots();
    const rows = roots
      .map((root) => extractCard(root, options))
      .filter((row) => row["_libraryId"])
      .filter((row) => !options.excludeVideoAds || !row.is_video)
      .filter((row) => !options.excludeCarouselAds || !row.is_carousel)
      .filter((row) => Number(row["掲載期間日数"]) >= Number(options.minDurationDays))
      .filter((row) => !existing.has(row["広告ライブラリURL"]))
      .sort((a, b) => Number(b["掲載期間日数"] || 0) - Number(a["掲載期間日数"] || 0));

    const seen = new Set();
    const uniqueRows = rows.filter((row) => {
      const key = row["広告ライブラリURL"];
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    const limitedRows = Number(options.maxRows) > 0 ? uniqueRows.slice(0, Number(options.maxRows)) : uniqueRows;
    const csv = toCsv(limitedRows);
    window.META_AD_EXTRACTOR_LAST_RESULT = { rows: limitedRows, csv };
    showResult(limitedRows, csv);
    if (options.copyToClipboard && navigator.clipboard) {
      try {
        await navigator.clipboard.writeText(csv);
      } catch (_) {
        // The visible textarea still lets the user copy manually.
      }
    }
    if (options.sendToUrl) {
      await sendRows(limitedRows, options.sendToUrl);
    }
    console.table(limitedRows);
    return { rows: limitedRows, csv };
  }

  window.MetaAdLibraryVisibleCardsExtractor = { run };
  run(window.META_AD_EXTRACTOR_OPTIONS || {});
})();
