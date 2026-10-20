/* AlphaSift "本次选股说明" panel.
 *
 * Adds a floating button on /screening. The panel loads the most recent
 * completed screening task and shows the strategy rules, data source,
 * scan/filter counts, LLM logic and warnings.
 */
(function () {
  var FILTER_LABELS = {
    exclude_st: "排除 ST",
    amount_min: "成交额下限",
    pe_ttm_min: "PE 下限",
    pe_ttm_max: "PE 上限",
    pb_min: "PB 下限",
    pb_max: "PB 上限",
    market_cap_min: "市值下限",
    market_cap_max: "市值上限",
    price_min: "价格下限",
    price_max: "价格上限",
    change_pct_min: "涨跌幅下限",
    change_pct_max: "涨跌幅上限",
    turnover_rate_min: "换手率下限",
    turnover_rate_max: "换手率上限",
    volume_ratio_min: "量比下限",
    volume_ratio_max: "量比上限"
  };
  var BUTTON_ID = "alphasift-explain-button";

  function isScreeningPage() {
    return location.pathname.replace(/\/+$/, "") === "/screening";
  }

  function ensureButton() {
    var button = document.getElementById(BUTTON_ID);
    if (!isScreeningPage()) {
      if (button) button.style.display = "none";
      return;
    }
    if (!button) {
      button = document.createElement("button");
      button.id = BUTTON_ID;
      button.type = "button";
      button.textContent = "本次选股说明";
      button.style.cssText =
        "position:fixed;right:24px;bottom:26px;z-index:2147483000;" +
        "padding:10px 16px;border-radius:999px;border:1px solid #38bdf8;" +
        "background:#0b1220;color:#38bdf8;font:600 13px -apple-system," +
        "BlinkMacSystemFont,'PingFang SC',sans-serif;cursor:pointer;" +
        "box-shadow:0 8px 24px rgba(0,0,0,.45)";
      button.addEventListener("click", openPanel);
      document.body.appendChild(button);
    }
    button.style.display = "block";
  }

  function el(tag, style, text) {
    var node = document.createElement(tag);
    if (style) node.style.cssText = style;
    if (text != null) node.textContent = text;
    return node;
  }

  function formatMoney(value) {
    var number = Number(value);
    if (!isFinite(number)) return String(value);
    if (Math.abs(number) >= 1e8) return (number / 1e8).toFixed(1) + " 亿";
    if (Math.abs(number) >= 1e4) return (number / 1e4).toFixed(1) + " 万";
    return String(number);
  }

  function formatFilter(key, value) {
    if (value == null || value === "") return null;
    var label = FILTER_LABELS[key] || key;
    if (typeof value === "boolean") return label + "：" + (value ? "是" : "否");
    if (key.indexOf("amount") >= 0 || key.indexOf("market_cap") >= 0) {
      return label + "：" + formatMoney(value);
    }
    return label + "：" + value;
  }

  function section(title) {
    return el(
      "div",
      "margin-top:16px;font-size:12px;letter-spacing:.08em;color:#94a3b8;text-transform:uppercase",
      title
    );
  }

  function closePanel() {
    var overlay = document.getElementById("alphasift-explain-overlay");
    if (overlay) overlay.remove();
  }

  function openPanel() {
    closePanel();
    var overlay = el(
      "div",
      "position:fixed;inset:0;z-index:2147483001;background:rgba(3,7,18,.72);" +
        "display:flex;align-items:center;justify-content:center;padding:20px"
    );
    overlay.id = "alphasift-explain-overlay";
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) closePanel();
    });

    var card = el(
      "div",
      "max-width:780px;width:100%;max-height:84vh;overflow:auto;background:#0b1220;" +
        "border:1px solid #1e293b;border-radius:14px;padding:22px 24px;color:#e5e7eb;" +
        "font:14px/1.65 -apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;" +
        "box-shadow:0 24px 64px rgba(0,0,0,.55)"
    );
    var header = el(
      "div",
      "display:flex;align-items:center;justify-content:space-between;gap:12px"
    );
    header.appendChild(el("div", "font-size:18px;font-weight:700", "本次选股说明"));
    var close = el(
      "button",
      "border:0;background:transparent;color:#94a3b8;font-size:22px;cursor:pointer;line-height:1",
      "×"
    );
    close.type = "button";
    close.addEventListener("click", closePanel);
    header.appendChild(close);
    card.appendChild(header);
    card.appendChild(
      el("div", "margin-top:4px;color:#94a3b8;font-size:12px", "正在读取最近一次已完成的选股…")
    );
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    fetch("/api/v1/alphasift/screen/tasks/latest", { credentials: "same-origin" })
      .then(function (response) {
        if (response.status === 401) throw new Error("请先登录后再查看");
        return response.json();
      })
      .then(function (data) {
        card.innerHTML = "";
        card.appendChild(header);
        if (!data || !data.result) {
          card.appendChild(
            el(
              "div",
              "margin-top:18px;padding:14px;border:1px dashed #334155;border-radius:10px;color:#94a3b8",
              "还没有已完成的选股任务。先在页面上运行一次选股，再回来看说明。"
            )
          );
          return;
        }
        render(card, data.result);
      })
      .catch(function (error) {
        card.innerHTML = "";
        card.appendChild(header);
        card.appendChild(
          el("div", "margin-top:16px;color:#f87171", error.message || "读取失败")
        );
      });
  }

  function render(card, result) {
    var diagnostics = result.diagnostics || {};
    var strategy = diagnostics.strategy || {};
    var rules = diagnostics.rules || {};
    var snapshot = diagnostics.snapshot || {};
    var llm = diagnostics.llm || {};

    card.appendChild(
      el(
        "div",
        "margin-top:14px;font-size:16px;font-weight:600;color:#f8fafc",
        (strategy.name || result.strategy || "选股策略") +
          (strategy.version ? "  v" + strategy.version : "")
      )
    );
    if (strategy.description) {
      card.appendChild(el("div", "margin-top:6px;color:#cbd5e1", strategy.description));
    }
    var meta = [];
    if (strategy.category) meta.push("类别：" + strategy.category);
    if (Array.isArray(strategy.tags) && strategy.tags.length) {
      meta.push("标签：" + strategy.tags.join(" / "));
    }
    if (meta.length) {
      card.appendChild(el("div", "margin-top:6px;color:#94a3b8;font-size:12px", meta.join("　")));
    }

    card.appendChild(section("数据来源与筛选"));
    var sourceLabels = {
      dsa_local: "本地数据库（行情 + 基本面）",
      tushare: "Tushare（外部）",
      sina: "Sina（外部）",
      efinance: "东方财富（外部）",
      akshare_em: "AkShare 东财（外部）",
      em_datacenter: "东财数据中心（外部）"
    };
    var source = sourceLabels[snapshot.source] || snapshot.source || "未知";
    var countText =
      "扫描 " + (snapshot.snapshotCount != null ? snapshot.snapshotCount : "?") +
      " → 过滤后 " + (snapshot.afterFilterCount != null ? snapshot.afterFilterCount : "?") +
      " → 候选 " + (snapshot.candidateCount != null ? snapshot.candidateCount : "?");
    card.appendChild(el("div", "margin-top:6px", "数据源：" + source));
    card.appendChild(el("div", "margin-top:4px", countText));

    card.appendChild(section("筛选规则"));
    var filters = rules.hardFilters || {};
    var filterLines = Object.keys(filters)
      .map(function (key) {
        return formatFilter(key, filters[key]);
      })
      .filter(Boolean);
    if (filterLines.length) {
      card.appendChild(el("div", "margin-top:6px", filterLines.join("；")));
    } else {
      card.appendChild(el("div", "margin-top:6px;color:#94a3b8", "该策略没有额外的硬过滤条件"));
    }
    var weights = rules.factorWeights || {};
    var weightKeys = Object.keys(weights);
    if (weightKeys.length) {
      var weightText = weightKeys
        .map(function (key) {
          var value = Number(weights[key]);
          return key + " " + (isFinite(value) ? (value * 100).toFixed(0) + "%" : weights[key]);
        })
        .join("　");
      card.appendChild(el("div", "margin-top:4px;color:#cbd5e1", "因子权重：" + weightText));
    }

    card.appendChild(section("LLM 判断"));
    if (llm.selectionLogic) {
      card.appendChild(el("div", "margin-top:6px;white-space:pre-wrap", llm.selectionLogic));
    } else {
      card.appendChild(el("div", "margin-top:6px;color:#fbbf24", "本次未获得 LLM 排序逻辑（已按因子分排序）"));
    }
    if (llm.marketView) {
      card.appendChild(el("div", "margin-top:6px;color:#cbd5e1", "市场观点：" + llm.marketView));
    }

    var warnings = (diagnostics.warnings || []).filter(Boolean);
    var errors = (diagnostics.sourceErrors || []).filter(Boolean);
    if (warnings.length || errors.length) {
      card.appendChild(section("数据提示"));
      warnings.concat(errors).slice(0, 8).forEach(function (text) {
        card.appendChild(el("div", "margin-top:4px;color:#fbbf24;font-size:12px", "· " + text));
      });
    }
  }

  function boot() {
    if (!document.body) {
      setTimeout(boot, 300);
      return;
    }
    ensureButton();
    setInterval(ensureButton, 800);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
