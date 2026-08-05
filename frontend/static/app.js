const state = { lines: [], chars: [], emotions: [], generating: false, hasGenerated: false, selectMode: false, selected: new Set(), failures: {}, pronunciation: [], projectType: "srt", aiMode: "api", webgal: { source: "", dialogues: [], emotions: {}, translations: {}, generated: {}, failures: {}, generating: false, lastExport: "", psyVoice: false, psyCharacter: "", lang: "zh", analyzing: false, progress: { current: 0, total: 0 }, exportDir: "" } };
let audioPlayer = null;
let analysisController = null;
let webgalParseController = null;
let webgalTranslateController = null;
let projectSelectedIds = new Set();

async function api(url, opts = {}) {
  const timeoutMs = opts.timeout || 180000;
  const externalSignal = opts.signal;
  const controller = new AbortController();
  let timedOut = false;
  const onAbort = () => controller.abort();
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort();
    else externalSignal.addEventListener("abort", onAbort, { once: true });
  }
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  try {
    const res = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts, signal: controller.signal });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      const error = new Error(err.error || "HTTP " + res.status);
      if (err.code) error.code = err.code;
      throw error;
    }
    return res.json();
  } catch (e) {
    if (timedOut) {
      const error = new Error("请求超时，请检查网络或稍后重试");
      error.code = "TIMEOUT";
      throw error;
    }
    throw e;
  } finally {
    clearTimeout(timer);
    if (externalSignal) externalSignal.removeEventListener("abort", onAbort);
  }
}

function showConfirmModal(message, options) {
  options = options || {};
  return new Promise(resolve => {
    const modal = document.getElementById("confirm-modal");
    if (!modal) { resolve(window.confirm(message)); return; }
    const titleEl = document.getElementById("confirm-modal-title");
    const msgEl = document.getElementById("confirm-modal-message");
    const okBtn = document.getElementById("btn-modal-ok");
    const cancelBtn = document.getElementById("btn-modal-cancel");
    const altBtn = document.getElementById("btn-modal-alt");
    titleEl.textContent = options.title || "确认操作";
    msgEl.textContent = message;
    okBtn.textContent = options.okText || "确定";
    cancelBtn.textContent = options.cancelText || "取消";
    cancelBtn.classList.remove("hidden");
    if (altBtn) {
      altBtn.textContent = options.altText || "";
      altBtn.classList.toggle("hidden", !options.altText);
    }
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      modal.classList.add("hidden");
      okBtn.onclick = null;
      cancelBtn.onclick = null;
      if (altBtn) altBtn.onclick = null;
      modal.onclick = null;
      resolve(value);
    };
    okBtn.onclick = () => finish(true);
    cancelBtn.onclick = () => finish(false);
    if (altBtn) altBtn.onclick = () => finish("alt");
    modal.onclick = (e) => { if (e.target === modal) finish(false); };
    modal.classList.remove("hidden");
  });
}

function showAlertModal(message, options) {
  options = options || {};
  return new Promise(resolve => {
    const modal = document.getElementById("confirm-modal");
    if (!modal) { window.alert(message); resolve(); return; }
    const titleEl = document.getElementById("confirm-modal-title");
    const msgEl = document.getElementById("confirm-modal-message");
    const okBtn = document.getElementById("btn-modal-ok");
    const cancelBtn = document.getElementById("btn-modal-cancel");
    const altBtn = document.getElementById("btn-modal-alt");
    titleEl.textContent = options.title || "提示";
    msgEl.textContent = message;
    okBtn.textContent = options.okText || "知道了";
    cancelBtn.classList.add("hidden");
    if (altBtn) altBtn.classList.add("hidden");
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      modal.classList.add("hidden");
      okBtn.onclick = null;
      cancelBtn.onclick = null;
      if (altBtn) { altBtn.onclick = null; altBtn.classList.add("hidden"); }
      modal.onclick = null;
      cancelBtn.classList.remove("hidden");
      resolve();
    };
    okBtn.onclick = finish;
    modal.onclick = (e) => { if (e.target === modal) finish(); };
    modal.classList.remove("hidden");
  });
}


async function loadConfig() {
  const cfg = await api("/api/config");
  state.chars = cfg.characters;
  state.emotions = cfg.emotions;
  setNarrationInputs(cfg.narration || {});
  loadDeployPath(cfg);
  loadCleanPath(cfg);
  updateDeployBanner(cfg.gptsovits_path);
  loadPronunciation();
  loadWebgalMap(cfg);
  loadWebgalRetranslate(cfg);
  const savedAi = loadAIConfigFromStorage();
  if (savedAi) {
    applyAIConfig(savedAi);
  } else if (cfg.deepseek) {
    document.getElementById("ai-name").value = cfg.deepseek.name || "DeepSeek";
    document.getElementById("ai-base-url").value = cfg.deepseek.base_url || "";
    document.getElementById("ai-model").value = cfg.deepseek.model || "";
  }
  if (cfg.has_api_key) {
    try {
      const keyData = await api("/api/config/api_key");
      if (keyData.api_key_preview) {
        document.getElementById("api-key").placeholder = "已保存 " + keyData.api_key_preview;
      }
    } catch (e) {}
  }
  if (cfg.dpapi_ok === false) {
    const status = document.getElementById("ai-config-status");
    if (status) {
      status.textContent = "注意：当前系统无法加密保存 API Key，密钥不会写入本地文件";
      status.className = "status-text error";
    }
  }
}

function handleAnalyzeSuccess(data) {
    if (data.status === "cancelled") {
      status.textContent = "已停止分析";
      status.className = "status-text";
      return;
    }
    state.lines = data.lines;
    state.failures = {};
    state.hasGenerated = false;
    state.selectMode = false;
    state.selected = new Set();
    document.getElementById("btn-select-mode").textContent = "选择模式";
    document.getElementById("btn-select-mode").classList.remove("active");
    document.getElementById("selection-toolbar").classList.add("hidden");
    const srtBtn = document.getElementById("btn-srt-only");
    if (srtBtn) srtBtn.classList.add("hidden");
    renderLines();
    const skipped = data.skipped || [];
    if (skipped.length) {
      const nums = skipped.slice(0, 6).map(s => "第" + s.line_no + "行").join("、");
      status.textContent = "已分析 " + data.lines.length + " 条台词；" + skipped.length + " 行格式不对已跳过（" + nums + (skipped.length > 6 ? " 等" : "") + "）";
      status.className = "status-text error";
    } else if (data.translated_only) {
      status.textContent = "情绪已是最新，已补齐日语翻译";
      status.className = "status-text success";
    } else if (data.reused) {
      status.textContent = "剧本未变化，情绪与翻译已是最新，未重复调用 AI";
      status.className = "status-text success";
    } else {
      status.textContent = "已分析 " + data.lines.length + " 条台词";
      status.className = "status-text success";
    }
    document.getElementById("step-review").classList.remove("hidden");
    document.getElementById("step-download").classList.add("hidden");
    document.getElementById("btn-merge").classList.add("hidden");
    document.getElementById("btn-generate").disabled = false;
    document.getElementById("btn-generate").textContent = "生成全部语音";
    const cancelBtnReset = document.getElementById("btn-cancel-generate");
    if (cancelBtnReset) cancelBtnReset.classList.add("hidden");
    document.getElementById("progress-text").classList.add("hidden");
    refreshHistory();
    if (!data.reused && !data.emotions_reused && emotionParamsEnabled && state.aiMode !== "manual") suggestEmotionParams();
    const issues = data.proofread || [];
    if (issues.length) showProofreadModal(issues);
}

function updateAiModeUI() {
  const analyzeBtn = document.getElementById("btn-analyze");
  if (analyzeBtn) analyzeBtn.textContent = state.aiMode === "manual" ? "客户端生成" : "分析情绪";
  const wgBtn = document.getElementById("btn-webgal-analyze");
  if (wgBtn) wgBtn.textContent = state.aiMode === "manual" ? "客户端生成" : "可选：AI 分析情绪";
  if (state.aiMode !== "manual") {
    const srtBox = document.getElementById("client-ai-box");
    if (srtBox) srtBox.classList.add("hidden");
    const wgBox = document.getElementById("wg-client-ai-box");
    if (wgBox) wgBox.classList.add("hidden");
  }
}

async function copyTextToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch (e) { /* fall through to legacy copy */ }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); } catch (e2) {}
  document.body.removeChild(ta);
}

async function openClientAi() {
  const status = document.getElementById("analyze-status");
  const box = document.getElementById("client-ai-box");
  const text = document.getElementById("script-input").value.trim();
  if (!text) {
    if (status) { status.textContent = "请先粘贴剧本内容"; status.className = "status-text error"; }
    return;
  }
  const lang = document.getElementById("script-lang").value;
  if (box) box.classList.remove("hidden");
  const out = document.getElementById("client-prompt-output");
  if (out) out.value = "正在生成提示词...";
  try {
    const data = await api("/api/analyze/prompt", { method: "POST", body: JSON.stringify({ text, lang }) });
    if (out) out.value = data.prompt;
    await copyTextToClipboard(data.prompt);
    if (status) { status.textContent = "提示词已生成并复制，请粘贴到你的 AI 客户端"; status.className = "status-text success"; }
    const resultInput = document.getElementById("client-result-input");
    if (resultInput) resultInput.focus();
  } catch (e) {
    if (status) { status.textContent = e.message; status.className = "status-text error"; }
  }
}

async function applyClientResult() {
  const status = document.getElementById("analyze-status");
  const resultInput = document.getElementById("client-result-input");
  const result = resultInput ? resultInput.value.trim() : "";
  if (!result) {
    if (status) { status.textContent = "请先粘贴 AI 返回的 JSON 结果"; status.className = "status-text error"; }
    return;
  }
  const text = document.getElementById("script-input").value.trim();
  const lang = document.getElementById("script-lang").value;
  if (status) { status.textContent = "正在应用 AI 结果..."; status.className = "status-text"; }
  try {
    const data = await api("/api/analyze/import", { method: "POST", body: JSON.stringify({ text, lang, result }) });
    handleAnalyzeSuccess(data);
  } catch (e) {
    if (status) { status.textContent = e.message; status.className = "status-text error"; }
  }
}

async function openWgClientAi(mode) {
  const box = document.getElementById("wg-client-ai-box");
  if (!state.webgal.dialogues.length) { setWebGalStatus("请先解析脚本", "error"); return; }
  if (box) box.classList.remove("hidden");
  const out = document.getElementById("wg-client-prompt-output");
  if (out) out.value = "正在生成提示词...";
  try {
    const data = await api("/api/webgal/analyze/prompt", { method: "POST", body: JSON.stringify({ lang: wgCurrentLang(), mode: mode || "analyze" }) });
    if (out) out.value = data.prompt;
    await copyTextToClipboard(data.prompt);
    setWebGalStatus("提示词已生成并复制，请粘贴到你的 AI 客户端", "success");
  } catch (e) {
    setWebGalStatus(e.message, "error");
  }
}

async function applyWgClientResult() {
  const resultInput = document.getElementById("wg-client-result-input");
  const result = resultInput ? resultInput.value.trim() : "";
  if (!result) { setWebGalStatus("请先粘贴 AI 返回的 JSON 结果", "error"); return; }
  try {
    const data = await api("/api/webgal/analyze/import", { method: "POST", body: JSON.stringify({ lang: wgCurrentLang(), result }) });
    state.webgal.emotions = data.emotions || {};
    state.webgal.translations = data.translations || {};
    renderWebGalLines();
    pushWebGalHistory();
    setWebGalStatus("AI 结果已应用", "success");
    updateWebGalTranslateButton();
    refreshHistoryButtons();
  } catch (e) {
    setWebGalStatus(e.message, "error");
  }
}

async function runAnalyze() {
  const text = document.getElementById("script-input").value.trim();
  state.script = text;
  const apiKey = document.getElementById("api-key").value.trim();
  const lang = document.getElementById("script-lang").value;
  const status = document.getElementById("analyze-status");
  if (state.aiMode === "manual") { openClientAi(); return; }
  if (!text) { status.textContent = "请粘贴剧本内容"; status.className = "status-text error"; return; }
  status.textContent = "正在分析情绪...";
  status.className = "status-text";
  document.getElementById("btn-analyze").disabled = true;
  if (analysisController) analysisController.abort();
  const controller = new AbortController();
  analysisController = controller;
  refreshHistoryButtons();
  const stopBtn = document.getElementById("btn-stop-analyze");
  stopBtn.classList.remove("hidden");
  syncScriptDraft();
  try {
    const data = await api("/api/analyze", { method: "POST", body: JSON.stringify({ text, api_key: apiKey, lang, base_url: document.getElementById("ai-base-url").value.trim(), model: document.getElementById("ai-model").value.trim() }), signal: controller.signal });
    handleAnalyzeSuccess(data);
  } catch (e) {
    if (e.name === "AbortError") {
      status.textContent = "已停止分析";
      status.className = "status-text";
      return;
    }
    status.textContent = e.message;
    status.className = "status-text error";
  } finally {
    if (analysisController === controller) analysisController = null;
    stopBtn.classList.add("hidden");
    document.getElementById("btn-analyze").disabled = false;
    refreshHistoryButtons();
  }
}
document.getElementById("btn-stop-analyze").addEventListener("click", () => {
  if (analysisController) analysisController.abort();
  api("/api/analyze/cancel", { method: "POST" }).catch(() => {});
});
document.getElementById("btn-stop-line-analyze").addEventListener("click", () => {
  if (analysisController) analysisController.abort();
  api("/api/analyze/cancel", { method: "POST" }).catch(() => {});
});
const btnClientPrompt = document.getElementById("btn-client-prompt");
if (btnClientPrompt) btnClientPrompt.addEventListener("click", openClientAi);
const btnClientApply = document.getElementById("btn-client-apply");
if (btnClientApply) btnClientApply.addEventListener("click", applyClientResult);
const btnWgClientPrompt = document.getElementById("btn-wg-client-prompt");
if (btnWgClientPrompt) btnWgClientPrompt.addEventListener("click", () => openWgClientAi("analyze"));
const btnWgClientTranslate = document.getElementById("btn-wg-client-translate");
if (btnWgClientTranslate) btnWgClientTranslate.addEventListener("click", () => openWgClientAi("translate"));
const btnWgClientApply = document.getElementById("btn-wg-client-apply");
if (btnWgClientApply) btnWgClientApply.addEventListener("click", applyWgClientResult);

const btnUndoEl = document.getElementById("btn-undo");
const btnRedoEl = document.getElementById("btn-redo");
if (btnUndoEl) btnUndoEl.addEventListener("click", () => runHistory("undo"));
if (btnRedoEl) btnRedoEl.addEventListener("click", () => runHistory("redo"));

document.addEventListener("keydown", (e) => {
  const mod = e.ctrlKey || e.metaKey;
  const isZ = mod && (e.key === "z" || e.key === "Z");
  const isY = mod && (e.key === "y" || e.key === "Y");
  if (!isZ && !isY) return;
  if (state.generating || state.webgal.generating || analysisController || state.webgal.analyzing || webgalParseController || webgalTranslateController) {
    e.preventDefault();
    toast("AI 处理中，请取消后再撤销/重做", "error");
    return;
  }
  const el = document.activeElement;
  const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
  if (typing) {
    if (el.id === "script-input") {
      if (isZ && !e.shiftKey) {
        e.preventDefault();
        if (!localScriptUndo()) runHistory("undo");
        return;
      }
      if ((isZ && e.shiftKey) || isY) {
        e.preventDefault();
        if (!localScriptRedo()) runHistory("redo");
        return;
      }
    }
    return;
  }
  e.preventDefault();
  if (isZ) {
    if (e.shiftKey) runHistory("redo");
    else runHistory("undo");
  } else {
    runHistory("redo");
  }
});

function showProofreadModal(issues) {
  state.lastProofread = issues;
  const modal = document.getElementById("proofread-modal");
  const list = document.getElementById("proofread-list");
  if (!modal || !list) return;
  list.innerHTML = issues.map(it => {
    const arrow = '<span class="proofread-arrow">→</span>';
    const value = esc(it.suggestion || it.name);
    return '<div class="proofread-item"><span class="proofread-line">第 ' + it.line_no + ' 行</span><span class="proofread-bad">' + esc(it.name) + '</span>' + arrow
      + '<input type="text" class="proofread-input" data-line="' + it.line_no + '" data-name="' + esc(it.name) + '" value="' + value + '" placeholder="请输入正确角色名">'
      + '</div>';
  }).join("");
  document.getElementById("proofread-summary").textContent = "发现 " + issues.length + " 个不存在的角色，请确认或修改角色名：";
  modal.classList.remove("hidden");
}

const proofreadModalEl = document.getElementById("proofread-modal");
if (proofreadModalEl) {
  document.getElementById("btn-proofread-ignore").addEventListener("click", () => {
    proofreadModalEl.classList.add("hidden");
  });

  document.getElementById("btn-proofread-fix").addEventListener("click", async () => {
    const rawLines = document.getElementById("script-input").value.split("\n");
    const fixedChars = {};
    document.querySelectorAll("#proofread-list .proofread-input").forEach(input => {
      const lineNo = parseInt(input.dataset.line, 10);
      const name = input.dataset.name || "";
      const suggestion = input.value.trim();
      if (!suggestion || lineNo < 1 || lineNo > rawLines.length) return;
      const idx = lineNo - 1;
      const old = rawLines[idx];
      if (!old.includes(name)) return;
      rawLines[idx] = old.replace(name, suggestion, 1);
      fixedChars[name] = suggestion;
    });
    applyScriptText(rawLines.join("\n"), { resetHistory: true });
    if (state.lines && state.lines.length) {
      state.lines.forEach(line => {
        if (fixedChars[line.character]) line.character = fixedChars[line.character];
      });
      renderLines();
    }
    if (Object.keys(fixedChars).length) {
      try {
        await api("/api/lines/characters", { method: "POST", body: JSON.stringify({ fixes: fixedChars }) });
        refreshHistory();
      } catch (e) {}
    }
    const status = document.getElementById("analyze-status");
    if (status) { status.textContent = "已修正角色名，保留原分析结果"; status.className = "status-text success"; }
    const fixMsg = Object.keys(fixedChars).map(name => name + " → " + fixedChars[name]).join("；");
    if (fixMsg) {
      try {
        await api("/api/logs", { method: "POST", body: JSON.stringify({ type: "character_fix", message: "角色名修正：" + fixMsg, payload: { fixes: fixedChars } }) });
      } catch (e) {}
    }
    proofreadModalEl.classList.add("hidden");
  });
}

function renderLines() {
  if (audioPlayer) { audioPlayer.pause(); }
  const container = document.getElementById("lines-container");
  document.getElementById("line-count").textContent = "共 " + state.lines.length + " 条台词";
  container.innerHTML = state.lines.map((line, i) => {
    const opts = state.emotions.map(e => '<option value="' + e + '"' + (e === line.emotion ? " selected" : "") + '>' + e + '</option>').join("");
    const checked = state.selected.has(i) ? " checked" : "";
    const idxCell = state.selectMode
      ? '<label class="idx-check"><input type="checkbox" class="line-check" data-index="' + i + '"' + checked + '>#' + (i + 1) + '</label>'
      : '<span class="idx">#' + (i + 1) + '</span>';
    const isNarration = line.character === "旁白";
    return '<div class="line-item">'
      + idxCell
      + '<input type="text" class="char char-input" value="' + esc(line.character) + '" data-index="' + i + '" title="点击修改角色名，回车保存" autocomplete="off">'
      + '<span class="line-texts">'
      + '<div class="text line-text-edit" contenteditable="true" spellcheck="false" title="点击修改台词，回车保存" data-index="' + i + '">' + esc(line.text) + '</div>'
      + (line.translated_text ? '<span class="translated" title="' + esc(line.translated_text) + '">日语：' + esc(line.translated_text) + '</span>' : '')
      + (state.failures[i] && !isNarration ? '<span class="line-fail" title="' + esc(state.failures[i]) + '">' + esc(state.failures[i]) + '</span>' : '')
      + '</span>'
      + '<input type="number" class="interval-input" data-index="' + i + '" min="0" max="10" step="0.1" value="' + (typeof line.interval === "number" ? line.interval : 0.5) + '" title="每句前间隔（秒）">'
      + (isNarration ? '<span class="narration-note">字幕</span>'
        : '<select data-index="' + i + '" class="emotion-select">' + opts + '</select>')
      + (isNarration ? '<span class="narration-note">无需配音</span>'
        : '<span class="line-actions">'
        + '<button type="button" class="btn-line-action btn-play" data-index="' + i + '" disabled>试听</button>'
        + '<button type="button" class="btn-line-action btn-regenerate" data-index="' + i + '" disabled>重新生成</button>'
        + '</span>')
      + '</div>';
  }).join("");
  container.querySelectorAll(".emotion-select").forEach(sel => {
    sel.addEventListener("change", async (e) => {
      const idx = parseInt(e.target.dataset.index);
      const oldEmotion = state.lines[idx].emotion;
      const newEmotion = e.target.value;
      if (newEmotion === oldEmotion) return;
      const status = document.getElementById("progress-text");
      status.classList.remove("hidden");
      status.textContent = "正在更新情绪...";
      status.className = "status-text";
      try {
        const res = await api("/api/line/" + idx, { method: "PUT", body: JSON.stringify({ emotion: newEmotion }) });
        state.lines[idx] = res.line;
        delete state.failures[idx];
        renderLines();
        refreshHistory();
        if (res.had_generated) {
          status.textContent = "情绪已更新，正在重新生成该句...";
          status.className = "status-text";
          startGeneration([idx]);
        } else if (state.hasGenerated && !state.generating) {
          await api("/api/merge", { method: "POST" });
          status.textContent = "情绪已更新，字幕已同步";
          status.className = "status-text success";
        } else {
          status.textContent = "情绪已更新";
          status.className = "status-text success";
        }
      } catch (err) {
        state.lines[idx].emotion = oldEmotion;
        renderLines();
        status.textContent = "情绪保存失败: " + err.message;
        status.className = "status-text error";
      }
    });
  });
  container.querySelectorAll(".line-check").forEach(cb => {
    cb.addEventListener("change", () => {
      const idx = parseInt(cb.dataset.index);
      if (cb.checked) {
        state.selected.add(idx);
      } else {
        state.selected.delete(idx);
      }
      updateSelectionUI();
    });
  });
  container.querySelectorAll(".interval-input").forEach(inp => {
    inp.addEventListener("change", async (e) => {
      const idx = parseInt(e.target.dataset.index);
      const raw = parseFloat(e.target.value);
      if (isNaN(raw) || raw < 0 || raw > 10) {
        e.target.value = state.lines[idx].interval;
        return;
      }
      const interval = Math.round(raw * 1000) / 1000;
      state.lines[idx].interval = interval;
      e.target.value = interval;
      const progressText = document.getElementById("progress-text");
      progressText.classList.remove("hidden");
      progressText.textContent = "正在保存间隔...";
      progressText.className = "status-text";
      try {
        await api("/api/line/" + idx, { method: "PUT", body: JSON.stringify({ interval }) });
        refreshHistory();
        if (state.hasGenerated && !state.generating) {
          await api("/api/merge", { method: "POST" });
          progressText.textContent = "间隔已更新，字幕已同步";
        } else {
          progressText.textContent = "间隔已保存";
        }
        progressText.className = "status-text success";
      } catch (err) {
        progressText.textContent = "间隔保存失败: " + err.message;
        progressText.className = "status-text error";
      }
    });
  });
  container.querySelectorAll(".line-text-edit").forEach(edit => {
    const idx = parseInt(edit.dataset.index);
    edit.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        edit.blur();
      }
    });
    edit.addEventListener("blur", async () => {
      if (state.generating) { edit.textContent = esc(state.lines[idx].text); return; }
      const prevText = state.lines[idx].text;
      const prevEmotion = state.lines[idx].emotion;
      let newText = (edit.textContent || "").replace(/\s*\n+\s*/g, " ").replace(/[ \t]{2,}/g, " ").trim();
      if (!newText || newText === prevText) {
        edit.textContent = esc(prevText);
        return;
      }
      edit.contentEditable = "false";
      const status = document.getElementById("progress-text");
      status.classList.remove("hidden");
      status.textContent = "正在重新分析第 " + (idx + 1) + " 行情绪...";
      status.className = "status-text";
      if (analysisController) analysisController.abort();
      const controller = new AbortController();
      analysisController = controller;
      refreshHistoryButtons();
      const stopBtn = document.getElementById("btn-stop-line-analyze");
      stopBtn.classList.remove("hidden");
      try {
        const res = await api("/api/line/" + idx, { method: "PUT", body: JSON.stringify({
          text: newText,
          api_key: document.getElementById("api-key").value.trim(),
          base_url: document.getElementById("ai-base-url").value.trim(),
          model: document.getElementById("ai-model").value.trim()
        }), signal: controller.signal });
        if (res.reanalyze_cancelled) {
          state.lines[idx] = res.line;
          renderLines();
          refreshHistory();
          status.textContent = "已停止分析，文本已保留，情绪未重新分析";
          status.className = "status-text";
          return;
        }
        state.lines[idx] = res.line;
        delete state.failures[idx];
        refreshHistory();
        if (res.reanalyze_error) {
          state.failures[idx] = "文本已修改，情绪重新分析失败：" + res.reanalyze_error;
        }
        renderLines();
        if (res.reanalyze_error) {
          status.textContent = "文本已保存，情绪重新分析失败，请手动选择情绪后重新生成";
          status.className = "status-text error";
          if (state.hasGenerated && !state.generating) {
            try { await api("/api/merge", { method: "POST" }); } catch (e) {}
          }
        } else if (res.had_generated) {
          status.textContent = "文本已更新，正在重新生成该句...";
          status.className = "status-text";
          startGeneration([idx]);
        } else if (state.hasGenerated && !state.generating) {
          try {
            await api("/api/merge", { method: "POST" });
            status.textContent = "文本与情绪已更新，字幕已同步";
            status.className = "status-text success";
          } catch (err) {
            status.textContent = "文本与情绪已更新，字幕同步失败：" + err.message;
            status.className = "status-text error";
          }
        } else {
          status.textContent = "文本与情绪已更新";
          status.className = "status-text success";
        }
      } catch (err) {
        if (err.name === "AbortError") {
          try {
            await api("/api/line/" + idx, { method: "PUT", body: JSON.stringify({ text: newText, emotion: prevEmotion, reanalyze: false }) });
          } catch (e2) {}
          state.lines[idx].text = newText;
          state.lines[idx].emotion = prevEmotion;
          renderLines();
          refreshHistory();
          status.textContent = "已停止分析，文本已保留，情绪未重新分析";
          status.className = "status-text";
        } else {
          edit.textContent = esc(prevText);
          status.textContent = "文本保存失败: " + err.message;
          status.className = "status-text error";
        }
      } finally {
        if (analysisController === controller) analysisController = null;
        stopBtn.classList.add("hidden");
        refreshHistoryButtons();
      }
    });
  });
  container.querySelectorAll(".char-input").forEach(inp => {
    const idx = parseInt(inp.dataset.index);
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); inp.blur(); }
    });
    inp.addEventListener("change", async () => {
      if (state.generating) { inp.value = state.lines[idx].character; return; }
      const prevChar = state.lines[idx].character;
      const newChar = inp.value.trim();
      if (!newChar || newChar === prevChar) { inp.value = prevChar; return; }
      inp.disabled = true;
      const status = document.getElementById("progress-text");
      status.classList.remove("hidden");
      status.textContent = "正在更新第 " + (idx + 1) + " 行角色...";
      status.className = "status-text";
      try {
        const res = await api("/api/line/" + idx, { method: "PUT", body: JSON.stringify({ character: newChar }) });
        state.lines[idx] = res.line;
        delete state.failures[idx];
        renderLines();
        refreshHistory();
        const needsVoice = res.line.character !== "旁白";
        const wasSilent = prevChar === "旁白" || !state.chars.includes(prevChar);
        if (res.had_generated || (needsVoice && wasSilent)) {
          status.textContent = "角色已更新，正在重新生成该句...";
          status.className = "status-text";
          startGeneration([idx]);
        } else if (state.hasGenerated && !state.generating) {
          try {
            await api("/api/merge", { method: "POST" });
            status.textContent = "角色已更新，字幕已同步";
            status.className = "status-text success";
          } catch (err) {
            status.textContent = "角色已更新，字幕同步失败：" + err.message;
            status.className = "status-text error";
          }
        } else {
          status.textContent = "角色已更新";
          status.className = "status-text success";
        }
      } catch (err) {
        inp.value = prevChar;
        status.textContent = "角色保存失败: " + err.message;
        status.className = "status-text error";
      }
    });
  });
  container.querySelectorAll(".btn-play").forEach(btn => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.index);
      if (!audioPlayer) audioPlayer = new Audio();
      if (!audioPlayer.paused && audioPlayer.dataset && audioPlayer.dataset.idx === String(idx)) {
        audioPlayer.pause();
        btn.textContent = "试听";
        return;
      }
      audioPlayer.src = "/api/segment/" + idx + "?t=" + Date.now();
      audioPlayer.dataset = audioPlayer.dataset || {};
      audioPlayer.dataset.idx = String(idx);
      audioPlayer.onended = () => { btn.textContent = "试听"; };
      audioPlayer.play().then(() => { btn.textContent = "停止"; }).catch(() => { btn.textContent = "试听"; });
    });
  });
  container.querySelectorAll(".btn-regenerate").forEach(btn => {
    btn.addEventListener("click", () => {
      startGeneration([parseInt(btn.dataset.index)]);
    });
  });
}

function updateSelectionUI() {
  const total = state.lines.length;
  const count = state.selected.size;
  const allBox = document.getElementById("select-all");
  if (allBox) {
    allBox.checked = total > 0 && count === total;
    allBox.indeterminate = count > 0 && count < total;
  }
  const countEl = document.getElementById("selected-count");
  if (countEl) countEl.textContent = "已选 " + count + " 条";
  const applyBtn = document.getElementById("btn-apply-interval");
  if (applyBtn) applyBtn.disabled = count === 0;
}

function setLineButtonsDisabled(disabled) {
  document.querySelectorAll(".btn-line-action").forEach(b => { b.disabled = disabled; });
  document.querySelectorAll(".interval-input").forEach(inp => { inp.disabled = disabled; });
  document.querySelectorAll(".line-text-edit").forEach(el => { el.contentEditable = disabled ? "false" : "true"; });
  document.querySelectorAll(".char-input").forEach(el => { el.disabled = disabled; });
  document.querySelectorAll(".line-check, #select-all, #btn-apply-interval, #batch-interval, #btn-select-mode").forEach(el => { el.disabled = disabled; });
  const saveRecordBtn = document.getElementById("btn-save-record");
  if (saveRecordBtn) saveRecordBtn.disabled = disabled;
}

function refreshGenerated(p) {
  const generated = p.generated_indices || [];
  state.hasGenerated = generated.length > 0;
  state.failures = p.failures || {};
  state.lastGenHash = JSON.stringify({ indices: generated, failures: p.failures || {} });
  const set = new Set(generated.map(String));
  document.querySelectorAll(".btn-line-action").forEach(btn => {
    const idx = btn.dataset.index;
    const ready = set.has(idx);
    if (btn.classList.contains("btn-play")) {
      btn.disabled = !ready;
      if (!ready) btn.textContent = "试听";
    } else if (btn.classList.contains("btn-regenerate")) {
      btn.disabled = !(ready || state.failures[idx]);
    }
  });
  refreshHistory();
}

function esc(s) { s = (s === null || s === undefined) ? "" : String(s); return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

let recentSettings = {};
let lastAutoVersionAt = 0;
let lastAutoHash = "";
let scriptSyncTimer = null;
let lastHistoryPayload = null;
let scriptDraftSeq = 0;
let scriptTextUndo = [];
let scriptTextRedo = [];
const SCRIPT_TEXT_HISTORY_LIMIT = 300;
let lastScriptValue = "";
let scriptComposing = false;
let scriptComposeStart = "";
let toastTimer = null;
function toast(message, kind) {
  let root = document.getElementById("toast-root");
  if (!root) {
    root = document.createElement("div");
    root.id = "toast-root";
    document.body.appendChild(root);
  }
  const el = document.createElement("div");
  el.className = "toast" + (kind === "error" ? " toast-error" : kind === "success" ? " toast-success" : "");
  el.textContent = message;
  root.appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));
  setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => el.remove(), 300);
  }, 3200);
}

function showProjectPicker() {
  const projects = document.getElementById("view-projects");
  const workbench = document.getElementById("view-workbench");
  const webgal = document.getElementById("view-webgal");
  const settings = document.getElementById("view-settings");
  const dropdown = document.getElementById("recent-dropdown");
  if (dropdown) dropdown.classList.add("hidden");
  if (projects) projects.classList.remove("hidden");
  if (workbench) workbench.classList.add("hidden");
  if (webgal) webgal.classList.add("hidden");
  if (settings) settings.classList.add("hidden");
  ["btn-back-workbench", "btn-undo", "btn-redo", "btn-refresh", "btn-recent", "btn-exit-home"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.add("hidden");
  });
  const settingsBtn = document.getElementById("btn-settings");
  if (settingsBtn) settingsBtn.classList.remove("hidden");
  const banner = document.getElementById("deploy-banner");
  if (banner) banner.classList.add("hidden");
}

function hasDraftContent() {
  if ((state.lines && state.lines.length) || (state.script || "").trim()) return true;
  if (state.projectType === "webgal" && state.webgal && (state.webgal.source || "").trim()) return true;
  return false;
}

async function exitToHome() {
  const projectsView = document.getElementById("view-projects");
  if (projectsView && !projectsView.classList.contains("hidden")) { showProjectPicker(); return; }
  const settingsView = document.getElementById("view-settings");
  const inSettings = settingsView && !settingsView.classList.contains("hidden");
  if (inSettings && settingsReturnTo === "projects") { showProjectPicker(); return; }
  if (state.generating || state.webgal.generating) { toast("生成中，请取消后再退出到主页", "error"); return; }
  if (analysisController || state.webgal.analyzing || webgalParseController || webgalTranslateController) { toast("分析/翻译中，请取消后再退出到主页", "error"); return; }
  const choice = await showConfirmModal("是否保存当前草稿？", {
    title: "退出到主页",
    okText: "保存并退出",
    cancelText: "取消",
    altText: "不保存退出"
  });
  if (choice === false) return;
  if (choice === true && hasDraftContent()) {
    const saved = await saveRecentRecord();
    if (!saved) { toast("保存失败，已取消退出", "error"); return; }
  }
  showProjectPicker();
  toast("已退出到主页", "success");
}

function selectProjectType(type) {
  document.querySelectorAll('input[name="new-project-type"]').forEach(r => { r.checked = (r.value === type); });
}

function selectProjectAiMode(mode) {
  document.querySelectorAll('input[name="new-project-ai-mode"]').forEach(r => { r.checked = (r.value === mode); });
}

function createNewProject() {
  if (state.generating) { toast("生成中，请稍后再新建项目", "error"); return; }
  const modal = document.getElementById("new-project-modal");
  const input = document.getElementById("new-project-name");
  const status = document.getElementById("new-project-status");
  if (!modal || !input) return;
  input.value = "";
  document.querySelectorAll('input[name="new-project-type"]').forEach(r => { r.checked = (r.value === "srt"); });
  document.querySelectorAll('input[name="new-project-ai-mode"]').forEach(r => { r.checked = (r.value === "api"); });
  const freshSrtBox = document.getElementById("client-ai-box");
  if (freshSrtBox) freshSrtBox.classList.add("hidden");
  const freshWgBox = document.getElementById("wg-client-ai-box");
  if (freshWgBox) freshWgBox.classList.add("hidden");
  if (status) { status.textContent = ""; status.className = "status-text"; }
  modal.classList.remove("hidden");
  setTimeout(() => input.focus(), 30);
}

function closeNewProjectModal() {
  const modal = document.getElementById("new-project-modal");
  if (modal) modal.classList.add("hidden");
}

async function confirmNewProject() {
  const modal = document.getElementById("new-project-modal");
  const input = document.getElementById("new-project-name");
  const status = document.getElementById("new-project-status");
  const btn = document.getElementById("btn-new-project-ok");
  if (!modal || !input) return;
  const name = input.value.trim();
  if (!name) {
    if (status) { status.textContent = "请输入项目名称"; status.className = "status-text error"; }
    input.focus();
    return;
  }
  if (state.generating) { toast("生成中，请稍后再新建项目", "error"); return; }
  if (btn) btn.disabled = true;
  try {
    const typeEl = document.querySelector('input[name="new-project-type"]:checked');
    const projectType = typeEl ? typeEl.value : "srt";
    const aiModeEl = document.querySelector('input[name="new-project-ai-mode"]:checked');
    const aiMode = aiModeEl ? aiModeEl.value : "api";
    await api("/api/recent/create", { method: "POST", body: JSON.stringify({ name, project_type: projectType, ai_mode: aiMode }) });
    state.projectType = projectType;
    state.aiMode = aiMode;
    const typeName = projectType === "webgal" ? "WebGaL 板块" : "SRT 工作台";
    if (projectType === "webgal") {
      resetWebGalProject();
      const wgLangEl = document.getElementById("webgal-lang");
      if (wgLangEl) wgLangEl.value = "zh";
      state.webgal.lang = "zh";
    }
    state.lines = [];
    state.script = "";
    state.failures = {};
    state.hasGenerated = false;
    state.selected = new Set();
    state.selectMode = false;
    lastAutoHash = "";
    lastAutoVersionAt = 0;
    applyScriptText("", { resetHistory: true });
    const langEl = document.getElementById("script-lang");
    if (langEl) langEl.value = "zh";
    const selBtn = document.getElementById("btn-select-mode");
    if (selBtn) { selBtn.textContent = "选择模式"; selBtn.classList.remove("active"); }
    const selBar = document.getElementById("selection-toolbar");
    if (selBar) selBar.classList.add("hidden");
    const review = document.getElementById("step-review");
    if (review) review.classList.add("hidden");
    const download = document.getElementById("step-download");
    if (download) download.classList.add("hidden");
    const genBtn = document.getElementById("btn-generate");
    if (genBtn) genBtn.textContent = "生成全部语音";
    renderLines();
    refreshGenerated({ generated_indices: [], failures: {} });
    refreshHistory();
    closeNewProjectModal();
    showWorkbench();
    refreshRecentList();
    toast("已创建项目“" + name + "”（" + typeName + "），撤销/重做已重置");
  } catch (e) {
    if (status) { status.textContent = "创建失败: " + e.message; status.className = "status-text error"; }
  } finally {
    if (btn) btn.disabled = false;
  }
}

function closeProjectsModal() {
  const modal = document.getElementById("projects-modal");
  if (modal) modal.classList.add("hidden");
}

async function openProjectsModal() {
  const modal = document.getElementById("projects-modal");
  const list = document.getElementById("projects-list");
  const status = document.getElementById("projects-status");
  if (!modal || !list) return;
  modal.classList.remove("hidden");
  list.innerHTML = '<div class="recent-empty">加载中...</div>';
  if (status) status.textContent = "";
  projectSelectedIds = new Set();
  updateProjectsToolbar();
  try {
    const data = await api("/api/recent");
    const records = data.records || [];
    if (!records.length) {
      list.innerHTML = '<div class="recent-empty">暂无往期项目</div>';
      return;
    }
    list.innerHTML = records.map(r => {
      const projectName = r.name || r.first_line || "未命名项目";
      const typeBadge = r.project_type === "webgal" ? '<span class="recent-badge">WebGaL</span>' : '<span class="recent-badge">SRT</span>';
      const first = r.first_line ? '<div class="recent-preview" title="' + esc(r.first_line) + '">' + esc(r.first_line) + '</div>' : "";
      const meta = (r.line_count ? r.line_count + " 条" : "仅剧本") + " · " + r.voice_count + " 条语音" + (r.fail_count > 0 ? " · 仅字幕 " + r.fail_count + " 条" : "") + " · " + (r.version_count || 0) + " 个小版本";
      return '<div class="project-item">'
        + '<label class="project-select" title="选择该项目"><input type="checkbox" class="project-select-box" data-id="' + esc(r.id) + '"></label>'
        + '<div class="project-item-main">'
        + '<div class="project-item-head"><span class="project-item-name">' + esc(projectName) + '</span>' + typeBadge + '<span class="recent-badge">' + (r.source === "manual" ? "手动" : "自动") + " · " + (r.lang === "ja" ? "日语" : "中文") + "</span></div>"
        + '<div class="project-item-time">' + esc(r.saved_at || "") + '</div>'
        + first
        + '<div class="recent-meta">' + esc(meta) + "</div>"
        + '</div>'
        + '<div class="project-item-actions">'
        + '<button type="button" class="btn-line-action btn-project-load" data-id="' + esc(r.id) + '">载入</button>'
        + (r.last_folder ? '<button type="button" class="btn-line-action btn-project-open" data-id="' + esc(r.id) + '">打开文件夹</button>' : "")
        + '<button type="button" class="btn-line-action btn-project-del" data-id="' + esc(r.id) + '">删除</button>'
        + '</div>'
        + '</div>';
    }).join("");
    projectSelectedIds = new Set();
    const selectAllBox = document.getElementById("projects-select-all");
    if (selectAllBox) { selectAllBox.checked = false; selectAllBox.indeterminate = false; }
    updateProjectsToolbar();
    list.querySelectorAll(".project-select-box").forEach(box => box.addEventListener("change", () => {
      const id = box.dataset.id;
      if (box.checked) projectSelectedIds.add(id); else projectSelectedIds.delete(id);
      updateProjectsToolbar();
    }));
    list.querySelectorAll(".btn-project-load").forEach(btn => btn.addEventListener("click", () => loadRecentRecord(btn.dataset.id)));
    list.querySelectorAll(".btn-project-open").forEach(btn => btn.addEventListener("click", () => openRecentFolder(btn.dataset.id)));
    list.querySelectorAll(".btn-project-del").forEach(btn => btn.addEventListener("click", () => deleteRecentRecord(btn.dataset.id)));
  } catch (e) {
    list.innerHTML = '<div class="recent-empty">加载失败: ' + esc(e.message) + '</div>';
  }
}


async function refreshRecentList() {
  const listEl = document.getElementById("recent-list");
  const titleEl = document.getElementById("recent-project-title");
  if (!listEl) return;
  try {
    const data = await api("/api/recent");
    const settings = data.settings || {};
    recentSettings = settings;
    const autoEl = document.getElementById("recent-auto-save");
    const limitEl = document.getElementById("recent-limit");
    const vAutoEl = document.getElementById("recent-version-auto");
    const intervalEl = document.getElementById("recent-interval");
    const versionLimitEl = document.getElementById("recent-version-limit");
    if (autoEl) autoEl.checked = !!settings.auto_save;
    if (limitEl) limitEl.value = settings.limit || 50;
    if (vAutoEl) vAutoEl.checked = !!settings.version_auto_save;
    if (intervalEl) intervalEl.value = settings.auto_save_interval || 5;
    if (versionLimitEl) versionLimitEl.value = settings.version_limit || 50;
    const cur = await api("/api/recent/current");
    const record = cur.record;
    if (titleEl) {
      const projectName = record ? (record.name || record.first_line || "未命名项目") : "未命名项目";
      const typeName = record && record.project_type === "webgal" ? "WebGaL 板块" : "SRT 工作台";
      titleEl.textContent = "当前项目：" + projectName + " · " + typeName;
    }
    const versions = (record && record.versions) || [];
    if (!versions.length) {
      listEl.innerHTML = '<div class="recent-empty">暂无小版本，保存后会生成第一个版本</div>';
      return;
    }
    listEl.innerHTML = versions.map(v => {
      return '<div class="recent-item recent-version-item">'
        + '<div class="recent-version-row">'
        + '<span class="recent-version-time">' + esc(v.saved_at || "") + '</span>'
        + '<span class="recent-badge">' + (v.source === "manual" ? "手动" : "自动") + '</span>'
        + '<button type="button" class="btn-line-action btn-version-load" data-id="' + esc(record.id) + '" data-vid="' + esc(v.id) + '">载入</button>'
        + '<button type="button" class="btn-line-action btn-version-del" data-id="' + esc(record.id) + '" data-vid="' + esc(v.id) + '">删除</button>'
        + '</div>'
        + '</div>';
    }).join("");
    listEl.querySelectorAll(".btn-version-load").forEach(btn => btn.addEventListener("click", () => loadRecentVersion(btn.dataset.id, btn.dataset.vid)));
    listEl.querySelectorAll(".btn-version-del").forEach(btn => btn.addEventListener("click", () => deleteRecentVersion(btn.dataset.id, btn.dataset.vid)));
  } catch (e) {
    listEl.innerHTML = '<div class="recent-empty">加载失败: ' + esc(e.message) + '</div>';
  }
}

function setRecentStatus(msg, kind) {
  const el = document.getElementById("recent-status");
  if (!el) return;
  el.textContent = msg;
  el.className = "status-text" + (kind === "error" ? " error" : kind === "success" ? " success" : "");
}

async function syncScriptDraft() {
  const scriptEl = document.getElementById("script-input");
  const webgalEl = document.getElementById("webgal-input");
  const text = state.projectType === "webgal"
    ? (webgalEl ? webgalEl.value : state.webgal.source)
    : (scriptEl ? scriptEl.value : state.script || "");
  state.script = text;
  const seq = ++scriptDraftSeq;
  try {
    await api("/api/script", { method: "POST", body: JSON.stringify({ text, seq }) });
  } catch (e) {}
}

function scheduleScriptDraft(text) {
  clearTimeout(scriptSyncTimer);
  const seq = ++scriptDraftSeq;
  scriptSyncTimer = setTimeout(() => {
    api("/api/script", { method: "POST", body: JSON.stringify({ text, seq }) }).catch(() => {});
  }, 400);
}

function applyScriptText(value, opts) {
  const resetHistory = !!(opts && opts.resetHistory);
  const el = document.getElementById("script-input");
  if (el) el.value = value;
  state.script = value;
  lastScriptValue = value;
  scriptComposeStart = value;
  scheduleScriptDraft(value);
  if (resetHistory) {
    scriptTextUndo = [];
    scriptTextRedo = [];
  }
  refreshHistoryButtons();
}

function recordScriptTextChange(value) {
  if (value === lastScriptValue) return;
  scriptTextUndo.push(lastScriptValue);
  if (scriptTextUndo.length > SCRIPT_TEXT_HISTORY_LIMIT) scriptTextUndo.shift();
  scriptTextRedo = [];
  lastScriptValue = value;
}

function localScriptUndo() {
  const el = document.getElementById("script-input");
  if (!el) return false;
  while (scriptTextUndo.length) {
    const prev = scriptTextUndo.pop();
    if (prev === el.value) continue;
    scriptTextRedo.push(el.value);
    applyScriptText(prev);
    return true;
  }
  return false;
}
function localScriptRedo() {
  const el = document.getElementById("script-input");
  if (!el) return false;
  while (scriptTextRedo.length) {
    const next = scriptTextRedo.pop();
    if (next === el.value) continue;
    scriptTextUndo.push(el.value);
    applyScriptText(next);
    return true;
  }
  return false;
}
async function saveRecentRecord() {
  if (state.generating) { setRecentStatus("生成中，请稍后再保存版本", "error"); return false; }
  await syncScriptDraft();
  if (!state.lines.length && !(state.script || "").trim() && !(state.webgal && (state.webgal.source || "").trim())) { setRecentStatus("还没有可保存的剧本", "error"); return false; }
  setRecentStatus("正在保存...", "");
  try {
    await syncWebGalState();
    await api("/api/recent/save", { method: "POST" });
    setRecentStatus("已保存小版本（手动）", "success");
    refreshRecentList();
    return true;
  } catch (e) {
    setRecentStatus("保存失败: " + e.message, "error");
    return false;
  }
}

async function loadRecentRecord(id) {
  if (state.generating) { setRecentStatus("生成中，请稍后再载入项目", "error"); return; }
  if ((state.lines.length || (state.script || "").trim()) && !(await showConfirmModal("载入项目会覆盖当前工作台，且撤销/重做将重置，确定继续吗？"))) return;
  setRecentStatus("正在载入项目...", "");
  try {
    const res = await api("/api/recent/" + id + "/load", { method: "POST" });
    const s = res.state || {};
    if (s.config && s.config.narration) setNarrationInputs(s.config.narration);
    state.projectType = (res.record && res.record.project_type) || s.project_type || "srt";
    state.aiMode = s.ai_mode || (res.record && res.record.ai_mode) || "api";
    if (state.projectType === "webgal") {
      resetWebGalProject();
      const wgSnap = (s.webgal && Array.isArray(s.webgal.dialogues) && s.webgal.dialogues.length) ? s.webgal : null;
      if (wgSnap) {
        restoreWebGalSnapshot(wgSnap);
        state.script = state.webgal.source || "";
        showWorkbench();
      } else {
        await restoreWebGalProject(s.script || "", (s.webgal && s.webgal.lang) || s.lang || "zh");
        state.script = state.webgal.source || "";
      }
      closeProjectsModal();
      setRecentStatus("已载入项目" + (res.record && res.record.saved_at ? "：" + res.record.saved_at : ""), "success");
      refreshRecentList();
      toast("已载入 WebGaL 项目，撤销/重做已重置");
      return;
    }
    state.lines = s.lines || [];
    state.script = s.script || "";
    applyScriptText(state.script || "", { resetHistory: true });
    const langEl = document.getElementById("script-lang");
    if (langEl) langEl.value = s.lang || "zh";
    state.failures = s.failures || {};
    state.hasGenerated = Object.keys(s.generated || {}).length > 0;
    state.selected = new Set();
    state.selectMode = false;
    const selBtn = document.getElementById("btn-select-mode");
    if (selBtn) {
      selBtn.textContent = "选择模式";
      selBtn.classList.remove("active");
    }
    const selBar = document.getElementById("selection-toolbar");
    if (selBar) selBar.classList.add("hidden");
    renderLines();
    refreshGenerated({ generated_indices: Object.keys(s.generated || {}).map(Number), failures: state.failures });
    const downloadPanel = document.getElementById("step-download");
    if (downloadPanel) downloadPanel.classList.toggle("hidden", !state.hasGenerated && !s.merged_path);
    const genBtn = document.getElementById("btn-generate");
    if (genBtn) genBtn.textContent = state.hasGenerated ? "重新生成" : "生成全部语音";
    updateHistoryButtons(res.history);
    closeProjectsModal();
    showWorkbench();
    setRecentStatus("已载入项目" + (res.record && res.record.saved_at ? "：" + res.record.saved_at : ""), "success");
    refreshRecentList();
    toast("已载入项目，撤销/重做已重置");
  } catch (e) {
    setRecentStatus("载入失败: " + e.message, "error");
  }
}

async function openRecentFolder(id) {
  try {
    const res = await api("/api/recent/" + id);
    const exportsList = (res.record && res.record.exports) || [];
    const folder = exportsList.length ? exportsList[exportsList.length - 1].folder : "";
    if (!folder) return;
    await api("/api/open_folder", { method: "POST", body: JSON.stringify({ path: folder }) });
  } catch (e) {
    await showAlertModal("打开失败: " + e.message);
  }
}

async function deleteRecentRecord(id) {
  if (!(await showConfirmModal("确定删除这个项目？此操作不可恢复。"))) return;
  try {
    await api("/api/recent/" + id, { method: "DELETE" });
    setRecentStatus("已删除项目", "success");
    refreshRecentList();
    const projectsModal = document.getElementById("projects-modal");
    if (projectsModal && !projectsModal.classList.contains("hidden")) openProjectsModal();
  } catch (e) {
    setRecentStatus("删除失败: " + e.message, "error");
  }
}

function updateProjectsToolbar() {
  const allBox = document.getElementById("projects-select-all");
  const countEl = document.getElementById("projects-selected-count");
  const delBtn = document.getElementById("btn-projects-delete-selected");
  const count = projectSelectedIds.size;
  if (countEl) countEl.textContent = "已选 " + count + " 项";
  if (delBtn) delBtn.disabled = count === 0;
  if (allBox) {
    const total = document.querySelectorAll("#projects-list .project-select-box").length;
    allBox.checked = total > 0 && count === total;
    allBox.indeterminate = count > 0 && count < total;
  }
}

async function deleteSelectedProjects() {
  const ids = [...projectSelectedIds];
  if (!ids.length) return;
  if (!(await showConfirmModal("确定删除选中的 " + ids.length + " 个项目？此操作不可恢复。"))) return;
  const status = document.getElementById("projects-status");
  const btn = document.getElementById("btn-projects-delete-selected");
  if (status) { status.textContent = "正在删除 " + ids.length + " 个项目..."; status.className = "status-text"; }
  if (btn) btn.disabled = true;
  let ok = 0, fail = 0;
  for (const id of ids) {
    try {
      await api("/api/recent/" + id, { method: "DELETE" });
      ok++;
    } catch (e) {
      fail++;
    }
  }
  if (fail === 0) toast("已删除 " + ok + " 个项目", "success");
  else if (ok === 0) toast("删除失败：" + fail + " 个项目", "error");
  else toast("已删除 " + ok + " 个，失败 " + fail + " 个", "error");
  openProjectsModal();
}

const projectsSelectAllEl = document.getElementById("projects-select-all");
if (projectsSelectAllEl) {
  projectsSelectAllEl.addEventListener("change", (e) => {
    document.querySelectorAll("#projects-list .project-select-box").forEach(box => {
      box.checked = e.target.checked;
      if (e.target.checked) projectSelectedIds.add(box.dataset.id); else projectSelectedIds.delete(box.dataset.id);
    });
    updateProjectsToolbar();
  });
}
const btnProjectsDeleteSelected = document.getElementById("btn-projects-delete-selected");
if (btnProjectsDeleteSelected) btnProjectsDeleteSelected.addEventListener("click", deleteSelectedProjects);

async function clearRecentRecords() {
  if (!(await showConfirmModal("确定清空全部项目？此操作不可恢复。"))) return;
  const status = document.getElementById("recent-settings-status");
  try {
    await api("/api/recent/clear", { method: "POST" });
    if (status) { status.textContent = "已清空全部项目"; status.className = "status-text success"; }
    refreshRecentList();
  } catch (e) {
    if (status) { status.textContent = "清空失败: " + e.message; status.className = "status-text error"; }
  }
}

async function saveRecentSettings() {
  const status = document.getElementById("recent-settings-status");
  const limitEl = document.getElementById("recent-limit");
  if (!status || !limitEl) return;
  const limitRaw = parseInt(limitEl.value, 10);
  if (isNaN(limitRaw) || limitRaw < 10 || limitRaw > 500) {
    status.textContent = "项目上限请输入 10-500 的数字";
    status.className = "status-text error";
    return;
  }
  const autoEl = document.getElementById("recent-auto-save");
  const auto = autoEl ? autoEl.checked : true;
  const vAutoEl = document.getElementById("recent-version-auto");
  const intervalEl = document.getElementById("recent-interval");
  const versionLimitEl = document.getElementById("recent-version-limit");
  const vAuto = vAutoEl ? vAutoEl.checked : true;
  const intervalRaw = parseInt(intervalEl ? intervalEl.value : "5", 10);
  if (isNaN(intervalRaw) || intervalRaw < 1 || intervalRaw > 120) {
    status.textContent = "自动保存间隔请输入 1-120 的数字";
    status.className = "status-text error";
    return;
  }
  const versionLimitRaw = parseInt(versionLimitEl ? versionLimitEl.value : "50", 10);
  if (isNaN(versionLimitRaw) || versionLimitRaw < 5 || versionLimitRaw > 200) {
    status.textContent = "版本上限请输入 5-200 的数字";
    status.className = "status-text error";
    return;
  }
  status.textContent = "正在保存版本设置...";
  status.className = "status-text";
  try {
    const res = await api("/api/recent/settings", { method: "POST", body: JSON.stringify({ limit: limitRaw, auto_save: auto, version_auto_save: vAuto, auto_save_interval: intervalRaw, version_limit: versionLimitRaw }) });
    if (limitEl) limitEl.value = res.settings.limit;
    if (autoEl) autoEl.checked = !!res.settings.auto_save;
    if (intervalEl) intervalEl.value = res.settings.auto_save_interval;
    if (vAutoEl) vAutoEl.checked = !!res.settings.version_auto_save;
    if (versionLimitEl) versionLimitEl.value = res.settings.version_limit;
    recentSettings = res.settings || recentSettings;
    lastAutoVersionAt = 0;
    status.textContent = "版本设置已保存";
    status.className = "status-text success";
    refreshRecentList();
  } catch (e) {
    status.textContent = "保存失败: " + e.message;
    status.className = "status-text error";
  }
}

function toggleRecentVersions(id) {
  const panel = document.getElementById("versions-panel-" + id);
  if (panel) panel.classList.toggle("hidden");
}

async function loadRecentVersion(recordId, versionId) {
  if (state.generating) { setRecentStatus("生成中，请稍后再载入版本", "error"); return; }
  if ((state.lines.length || (state.script || "").trim()) && !(await showConfirmModal("载入版本会覆盖当前工作台，且撤销/重做将重置，确定继续吗？"))) return;
  setRecentStatus("正在载入版本...", "");
  try {
    const res = await api("/api/recent/" + recordId + "/versions/" + versionId + "/load", { method: "POST" });
    const s = res.state || {};
    if (s.config && s.config.narration) setNarrationInputs(s.config.narration);
    state.projectType = s.project_type || "srt";
    state.aiMode = s.ai_mode || "api";
    if (state.projectType === "webgal") {
      resetWebGalProject();
      const wgSnap = (s.webgal && Array.isArray(s.webgal.dialogues) && s.webgal.dialogues.length) ? s.webgal : null;
      if (wgSnap) {
        restoreWebGalSnapshot(wgSnap);
        state.script = state.webgal.source || "";
        showWorkbench();
      } else {
        await restoreWebGalProject(s.script || "", (s.webgal && s.webgal.lang) || s.lang || "zh");
        state.script = state.webgal.source || "";
      }
      setRecentStatus("已载入版本" + (res.version && res.version.saved_at ? "：" + res.version.saved_at : ""), "success");
      refreshRecentList();
      toast("已载入版本，撤销/重做已重置");
      return;
    }
    state.lines = s.lines || [];
    state.script = s.script || "";
    applyScriptText(state.script || "", { resetHistory: true });
    const langEl = document.getElementById("script-lang");
    if (langEl) langEl.value = s.lang || "zh";
    state.failures = s.failures || {};
    state.hasGenerated = Object.keys(s.generated || {}).length > 0;
    state.selected = new Set();
    state.selectMode = false;
    const selBtn = document.getElementById("btn-select-mode");
    if (selBtn) {
      selBtn.textContent = "选择模式";
      selBtn.classList.remove("active");
    }
    const selBar = document.getElementById("selection-toolbar");
    if (selBar) selBar.classList.add("hidden");
    renderLines();
    refreshGenerated({ generated_indices: Object.keys(s.generated || {}).map(Number), failures: state.failures });
    const downloadPanel = document.getElementById("step-download");
    if (downloadPanel) downloadPanel.classList.toggle("hidden", !state.hasGenerated && !s.merged_path);
    const genBtn = document.getElementById("btn-generate");
    if (genBtn) genBtn.textContent = state.hasGenerated ? "重新生成" : "生成全部语音";
    updateHistoryButtons(res.history);
    setRecentStatus("已载入版本" + (res.version && res.version.saved_at ? "：" + res.version.saved_at : ""), "success");
    refreshRecentList();
    toast("已载入版本，撤销/重做已重置");
  } catch (e) {
    setRecentStatus("载入失败: " + e.message, "error");
  }
}

async function deleteRecentVersion(recordId, versionId) {
  if (!(await showConfirmModal("确定删除这条版本？"))) return;
  try {
    await api("/api/recent/" + recordId + "/versions/" + versionId, { method: "DELETE" });
    setRecentStatus("已删除版本", "success");
    refreshRecentList();
  } catch (e) {
    setRecentStatus("删除失败: " + e.message, "error");
  }
}

function currentContentHash() {
  try {
    const readVal = (id) => { const el = document.getElementById(id); return el ? el.value : ""; };
    const fixedBtn = document.getElementById("narration-mode-fixed");
    return JSON.stringify({
      projectType: state.projectType || "srt",
      script: state.script || "",
      lines: state.lines || [],
      srtGenerated: state.lastGenHash || "",
      webgalSource: state.webgal.source || "",
      webgalLang: state.webgal.lang || "zh",
      webgalEmotions: state.webgal.emotions || {},
      webgalTranslations: state.webgal.translations || {},
      webgalGenerated: state.webgal.generated || {},
      webgalFailures: state.webgal.failures || {},
      webgalPsy: { voice: !!state.webgal.psyVoice, character: state.webgal.psyCharacter || "" },
      webgalLastExport: state.webgal.lastExport || "",
      narration: {
        base: readVal("narration-base"),
        per: readVal("narration-per"),
        min: readVal("narration-min"),
        max: readVal("narration-max"),
        fixed: readVal("narration-fixed"),
        fixedMode: !!(fixedBtn && fixedBtn.classList.contains("active"))
      }
    });
  } catch (e) { return ""; }
}

async function maybeAutoSaveVersion() {
  if (!recentSettings.version_auto_save) return;
  if (state.generating || analysisController || state.webgal.generating || state.webgal.analyzing || webgalParseController || webgalTranslateController) return;
  const now = Date.now();
  const intervalMs = (Math.max(1, parseInt(recentSettings.auto_save_interval, 10) || 5)) * 60000;
  if (now - lastAutoVersionAt < intervalMs) return;
  await syncScriptDraft();
  await syncWebGalState();
  const hash = currentContentHash();
  if (!hash) { lastAutoVersionAt = now; return; }
  if (hash === lastAutoHash) { lastAutoVersionAt = now; return; }
  try {
    const res = await api("/api/recent/versions", { method: "POST", body: JSON.stringify({}) });
    lastAutoVersionAt = now;
    lastAutoHash = hash;
    if (res.created) refreshRecentList();
  } catch (e) {
    lastAutoVersionAt = now;
  }
}

function initRecentRecords() {
  const saveBtn = document.getElementById("btn-save-record");
  if (saveBtn) saveBtn.addEventListener("click", saveRecentRecord);
  const saveSettingsBtn = document.getElementById("btn-save-recent-settings");
  if (saveSettingsBtn) saveSettingsBtn.addEventListener("click", saveRecentSettings);
  const clearBtn = document.getElementById("btn-clear-recent");
  if (clearBtn) clearBtn.addEventListener("click", clearRecentRecords);
  const newBtn = document.getElementById("btn-new-project");
  if (newBtn) newBtn.addEventListener("click", createNewProject);
  const newOk = document.getElementById("btn-new-project-ok");
  if (newOk) newOk.addEventListener("click", confirmNewProject);
  const newCancel = document.getElementById("btn-new-project-cancel");
  if (newCancel) newCancel.addEventListener("click", closeNewProjectModal);
  const newName = document.getElementById("new-project-name");
  if (newName) {
    newName.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); confirmNewProject(); }
    });
  }
  const newProjectModal = document.getElementById("new-project-modal");
  if (newProjectModal) {
    newProjectModal.addEventListener("click", (e) => {
      if (e.target === newProjectModal) closeNewProjectModal();
    });
  }
  const openBtn = document.getElementById("btn-open-projects");
  if (openBtn) openBtn.addEventListener("click", openProjectsModal);
  const closeBtn = document.getElementById("btn-projects-close");
  if (closeBtn) closeBtn.addEventListener("click", closeProjectsModal);
  const projectsModal = document.getElementById("projects-modal");
  if (projectsModal) {
    projectsModal.addEventListener("click", (e) => {
      if (e.target === projectsModal) closeProjectsModal();
    });
  }
  const scriptEl = document.getElementById("script-input");
  if (scriptEl) {
    scriptEl.addEventListener("compositionstart", () => {
      scriptComposing = true;
      scriptComposeStart = scriptEl.value;
    });
    scriptEl.addEventListener("compositionend", () => {
      scriptComposing = false;
      if (scriptEl.value !== scriptComposeStart) {
        scriptTextUndo.push(scriptComposeStart);
        if (scriptTextUndo.length > SCRIPT_TEXT_HISTORY_LIMIT) scriptTextUndo.shift();
        scriptTextRedo = [];
        lastScriptValue = scriptEl.value;
      }
      scheduleScriptDraft(scriptEl.value);
      refreshHistoryButtons();
    });
    scriptEl.addEventListener("input", (e) => {
      if (e.isComposing || scriptComposing) return;
      recordScriptTextChange(scriptEl.value);
      state.script = scriptEl.value;
      scheduleScriptDraft(scriptEl.value);
      refreshHistoryButtons();
    });
  }
  const scriptLangEl = document.getElementById("script-lang");
  if (scriptLangEl) {
    scriptLangEl.addEventListener("change", () => { refreshHistory(); });
  }
  setInterval(maybeAutoSaveVersion, 30000);
  maybeAutoSaveVersion();
  const recentBtn = document.getElementById("btn-recent");
  const dropdown = document.getElementById("recent-dropdown");
  if (recentBtn && dropdown) {
    recentBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      dropdown.classList.toggle("hidden");
      if (!dropdown.classList.contains("hidden")) refreshRecentList();
    });
    document.addEventListener("click", (e) => {
      if (dropdown.classList.contains("hidden")) return;
      if (dropdown.contains(e.target) || recentBtn.contains(e.target)) return;
      dropdown.classList.add("hidden");
    });
  }
}

function updateHistoryButtons(h) {
  const undoBtn = document.getElementById("btn-undo");
  const redoBtn = document.getElementById("btn-redo");
  if (!undoBtn || !redoBtn) return;
  if (state.generating || state.webgal.generating || analysisController || state.webgal.analyzing || webgalParseController || webgalTranslateController) {
    undoBtn.disabled = true;
    redoBtn.disabled = true;
    undoBtn.title = "AI 处理中，暂不可用";
    redoBtn.title = "AI 处理中，暂不可用";
    return;
  }
  if (state.projectType === "webgal") {
    const uc = Math.max(0, webgalHistoryIndex);
    const rc = Math.max(0, webgalHistory.length - 1 - webgalHistoryIndex);
    undoBtn.disabled = uc === 0;
    redoBtn.disabled = rc === 0;
    undoBtn.title = uc ? "撤销 WebGaL (Ctrl+Z)" : "撤销 (Ctrl+Z)";
    redoBtn.title = rc ? "重做 WebGaL (Ctrl+Shift+Z)" : "重做 (Ctrl+Shift+Z)";
    return;
  }
  const uc = (h ? h.undo_count : 0) + scriptTextUndo.length;
  const rc = (h ? h.redo_count : 0) + scriptTextRedo.length;
  undoBtn.disabled = uc === 0;
  redoBtn.disabled = rc === 0;
  undoBtn.title = uc ? "撤销 " + (h.undo_label || "") + " (Ctrl+Z)" : "撤销 (Ctrl+Z)";
  redoBtn.title = rc ? "重做 " + (h.redo_label || "") + " (Ctrl+Shift+Z)" : "重做 (Ctrl+Shift+Z)";
}

function refreshHistoryButtons() {
  updateHistoryButtons(lastHistoryPayload);
}
async function refreshHistory() {
  try {
    const h = await api("/api/history");
    lastHistoryPayload = h;
    updateHistoryButtons(h);
  } catch (e) {}
}

async function runHistory(dir) {
  if (state.generating) { toast("生成中，请取消后再撤销/重做", "error"); return; }
  if (analysisController) { toast("分析中，请取消后再撤销/重做", "error"); return; }
  if (state.projectType === "webgal") {
    if (state.webgal.analyzing || webgalParseController || webgalTranslateController) {
      toast("分析/翻译中，请稍后再撤销/重做", "error");
      return;
    }
    const ok = dir === "undo" ? webgalUndo() : webgalRedo();
    if (ok) {
      toast(dir === "undo" ? "已撤销 WebGaL 操作" : "已重做 WebGaL 操作", "success");
    } else {
      toast(dir === "undo" ? "没有可撤销的操作" : "没有可重做的操作", "error");
    }
    return;
  }
  try {
    const res = await api("/api/" + dir, { method: "POST" });
    const s = res.state || {};
    if (s.config && s.config.narration) setNarrationInputs(s.config.narration);
    state.lines = s.lines || [];
    state.script = s.script || "";
    applyScriptText(state.script || "");
    state.failures = s.failures || {};
    state.hasGenerated = Object.keys(s.generated || {}).length > 0;
    state.selected = new Set();
    state.selectMode = false;
    const selBtn = document.getElementById("btn-select-mode");
    if (selBtn) {
      selBtn.textContent = "选择模式";
      selBtn.classList.remove("active");
    }
    const selBar = document.getElementById("selection-toolbar");
    if (selBar) selBar.classList.add("hidden");
    renderLines();
    refreshGenerated({ generated_indices: Object.keys(s.generated || {}).map(Number), failures: state.failures });
    const downloadPanel = document.getElementById("step-download");
    if (downloadPanel) downloadPanel.classList.toggle("hidden", !state.hasGenerated && !s.merged_path);
    const genBtn = document.getElementById("btn-generate");
    if (genBtn) genBtn.textContent = state.hasGenerated ? "重新生成" : "生成全部语音";
    updateHistoryButtons(res.history);
    toast((dir === "undo" ? "已撤销：" : "已重做：") + (res.label || ""), "success");
  } catch (e) {
    const msg = e.message || "";
    const noGlobal = dir === "undo" ? /没有可撤销/.test(msg) : /没有可重做/.test(msg);
    if (noGlobal) {
      const didLocal = dir === "undo" ? localScriptUndo() : localScriptRedo();
      if (didLocal) {
        refreshHistory();
        toast(dir === "undo" ? "已撤销：剧本输入" : "已重做：剧本输入", "success");
        return;
      }
    }
    toast((dir === "undo" ? "撤销失败：" : "重做失败：") + msg, "error");
  }
}


document.getElementById("btn-merge").addEventListener("click", async () => {
  const btn = document.getElementById("btn-merge");
  const progressText = document.getElementById("progress-text");
  if (!(await ensureServerLines())) return;
  btn.disabled = true;
  btn.textContent = "合并中...";
  progressText.textContent = "正在合并音频并生成字幕...";
  progressText.className = "status-text";
  try {
    const result = await api("/api/merge", { method: "POST" });
    if (result.status === "ok") {
      progressText.textContent = "合并完成!";
      progressText.className = "status-text success";
      btn.classList.add("hidden");
      document.getElementById("btn-merge").classList.remove("hidden");
    }
  } catch (e) {
    progressText.textContent = "合并失败: " + e.message;
    progressText.className = "status-text error";
    btn.disabled = false;
    btn.textContent = "合并音频并生成字幕";
  }
});

document.getElementById("btn-select-mode").addEventListener("click", () => {
  state.selectMode = !state.selectMode;
  const btn = document.getElementById("btn-select-mode");
  btn.textContent = state.selectMode ? "退出选择" : "选择模式";
  btn.classList.toggle("active", state.selectMode);
  document.getElementById("selection-toolbar").classList.toggle("hidden", !state.selectMode);
  if (!state.selectMode) {
    state.selected.clear();
    const allBox = document.getElementById("select-all");
    allBox.checked = false;
    allBox.indeterminate = false;
    document.getElementById("selection-status").textContent = "";
  }
  renderLines();
  updateSelectionUI();
});

document.getElementById("select-all").addEventListener("change", (e) => {
  state.selected = e.target.checked ? new Set(state.lines.map((_, i) => i)) : new Set();
  renderLines();
  updateSelectionUI();
});

document.getElementById("btn-apply-interval").addEventListener("click", async () => {
  const indices = [...state.selected];
  const statusEl = document.getElementById("selection-status");
  if (!indices.length) {
    statusEl.textContent = "请先勾选台词";
    statusEl.className = "status-text error";
    return;
  }
  const raw = parseFloat(document.getElementById("batch-interval").value);
  if (isNaN(raw) || raw < 0 || raw > 10) {
    statusEl.textContent = "请输入 0-10 秒的间隔";
    statusEl.className = "status-text error";
    return;
  }
  const interval = Math.round(raw * 1000) / 1000;
  statusEl.textContent = "正在应用间隔...";
  statusEl.className = "status-text";
  try {
    const res = await api("/api/lines/interval", { method: "POST", body: JSON.stringify({ indices, interval }) });
    for (const idx of res.indices) state.lines[idx].interval = interval;
    refreshHistory();
    if (state.hasGenerated && !state.generating) {
      await api("/api/merge", { method: "POST" });
      statusEl.textContent = "已更新 " + res.updated + " 条，字幕已同步";
    } else {
      statusEl.textContent = "已更新 " + res.updated + " 条";
    }
    statusEl.className = "status-text success";
    renderLines();
    updateSelectionUI();
  } catch (err) {
    statusEl.textContent = "应用失败: " + err.message;
    statusEl.className = "status-text error";
  }
});

document.getElementById("btn-generate").addEventListener("click", () => startGeneration());

document.getElementById("btn-cancel-generate").addEventListener("click", async () => {
  const btn = document.getElementById("btn-cancel-generate");
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = "正在取消...";
  try {
    await api("/api/generate/cancel", { method: "POST" });
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "取消生成";
  }
});

async function ensureServerLines() {
  if (!state.lines.length) return true;
  try {
    const s = await api("/api/state");
    if (s && Array.isArray(s.lines) && s.lines.length) return true;
  } catch (e) {}
  state.lines = [];
  state.script = "";
  state.hasGenerated = false;
  state.failures = {};
  state.selected = new Set();
  state.selectMode = false;
  renderLines();
  const review = document.getElementById("step-review");
  if (review) review.classList.add("hidden");
  const download = document.getElementById("step-download");
  if (download) download.classList.add("hidden");
  const genBtn = document.getElementById("btn-generate");
  if (genBtn) { genBtn.disabled = true; genBtn.textContent = "生成全部语音"; }
  const progressText = document.getElementById("progress-text");
  if (progressText) {
    progressText.textContent = "检测到服务已重启，分析结果已失效，请重新分析";
    progressText.className = "status-text error";
    progressText.classList.remove("hidden");
  }
  return false;
}
async function startGeneration(indices, srtOnly) {
  if (state.generating) return;
  if (!(await ensureServerLines())) return;
  const btn = document.getElementById("btn-generate");
  const progressText = document.getElementById("progress-text");
  const targets = indices ? indices.map(i => state.lines[i]) : state.lines;
  const bad = [];
  targets.forEach((line, k) => {
    if (line.character !== "旁白" && !state.chars.includes(line.character)) {
      const lineNo = (indices ? indices[k] : k) + 1;
      bad.push("#" + lineNo + " " + line.character);
    }
  });
  if (!srtOnly && bad.length) {
    progressText.textContent = "以下角色不存在，无法生成：" + bad.slice(0, 5).join("、") + (bad.length > 5 ? " 等" + bad.length + " 条" : "");
    progressText.className = "status-text error";
    progressText.classList.remove("hidden");
    const srtBtn = document.getElementById("btn-srt-only");
    if (srtBtn) {
      srtBtn.classList.remove("hidden");
      srtBtn.onclick = () => startGeneration(indices, true);
    }
    return;
  }
  btn.disabled = true;
  btn.textContent = "生成中...";
  state.generating = true;
  setLineButtonsDisabled(true);
  const cancelBtn = document.getElementById("btn-cancel-generate");
  if (cancelBtn) {
    cancelBtn.classList.remove("hidden");
    cancelBtn.disabled = false;
    cancelBtn.textContent = "取消生成";
  }
  const srtBtn = document.getElementById("btn-srt-only");
  if (srtBtn) srtBtn.classList.add("hidden");
  progressText.classList.remove("hidden");
  let barWrap = document.querySelector(".progress-bar-wrap");
  if (!barWrap) {
    barWrap = document.createElement("div");
    barWrap.className = "progress-bar-wrap";
    barWrap.innerHTML = '<div class="progress-bar-fill" style="width:0%"></div>';
    btn.parentElement.appendChild(barWrap);
  }
  const barFill = barWrap.querySelector(".progress-bar-fill");
  try {
    const payload = {};
    if (indices) payload.indices = indices;
    if (srtOnly) payload.srt_only = true;
    const body = JSON.stringify(payload);
    await api("/api/generate", { method: "POST", body });
    refreshHistory();
    pollProgress(btn, progressText, barFill);
  } catch (e) {
    progressText.textContent = e.message;
    progressText.className = "status-text error";
    btn.disabled = false;
    btn.textContent = "生成全部语音";
    state.generating = false;
    setLineButtonsDisabled(false);
    const cancelBtnCatch = document.getElementById("btn-cancel-generate");
    if (cancelBtnCatch) cancelBtnCatch.classList.add("hidden");
  }
}

function summarizeFailures(failures) {
  const counts = {};
  Object.values(failures || {}).forEach(msg => {
    let key = "其他原因";
    if (msg.indexOf("缺少参考音频") === 0) key = "缺少参考音频";
    else if (msg.indexOf("没有配音模型") >= 0 || msg.indexOf("角色模型加载失败") >= 0) key = "角色模型不可用";
    else if (msg.indexOf("情绪重新分析失败") >= 0) key = "情绪重新分析失败";
    else if (msg.indexOf("生成失败") >= 0) key = "语音合成失败";
    counts[key] = (counts[key] || 0) + 1;
  });
  return Object.keys(counts).map(k => k + "（" + counts[k] + " 条）").join("、");
}

async function pollProgress(btn, progressText, barFill) {
  try {
    const p = await api("/api/progress");
    if (p.progress.total > 0) {
      const pct = Math.round((p.progress.current / p.progress.total) * 100);
      barFill.style.width = pct + "%";
      progressText.textContent = "进度: " + p.progress.current + "/" + p.progress.total + " (" + pct + "%)";
      progressText.className = "status-text";
    }
    if (p.error) {
      progressText.textContent = "错误: " + p.error;
      progressText.className = "status-text error";
      btn.disabled = false; btn.textContent = "生成全部语音"; state.generating = false;
      const cancelBtnErr = document.getElementById("btn-cancel-generate");
      if (cancelBtnErr) cancelBtnErr.classList.add("hidden");
      setLineButtonsDisabled(false); renderLines(); refreshGenerated(p); return;
    }
    if (p.cancelled) {
      progressText.textContent = "已取消：本次生成已清理，可重新生成";
      progressText.className = "status-text";
      btn.disabled = false; btn.textContent = "重新生成"; state.generating = false;
      const cancelBtnDone = document.getElementById("btn-cancel-generate");
      if (cancelBtnDone) { cancelBtnDone.classList.add("hidden"); cancelBtnDone.disabled = false; }
      setLineButtonsDisabled(false); renderLines(); refreshGenerated(p); return;
    }
    if (p.cancel_requested) {
      progressText.textContent = "正在取消...";
      progressText.className = "status-text";
      setTimeout(() => pollProgress(btn, progressText, barFill), 800);
      return;
    }
    if (!p.generating && p.merged_path) {
      const failCount = Object.keys(p.failures || {}).length;
      if (failCount > 0) {
        progressText.textContent = "生成完成：语音 " + p.generated_count + " 条，另有 " + failCount + " 条仅保留字幕。原因：" + summarizeFailures(p.failures);
        progressText.className = "status-text success";
      } else if (p.generated_count > 0) {
        progressText.textContent = "生成完成：语音 " + p.generated_count + " 条";
        progressText.className = "status-text success";
      } else {
        progressText.textContent = "生成完成：全部为旁白字幕";
        progressText.className = "status-text success";
      }
      btn.textContent = "重新生成"; btn.disabled = false; state.generating = false;
      const cancelBtnFinish = document.getElementById("btn-cancel-generate");
      if (cancelBtnFinish) cancelBtnFinish.classList.add("hidden");
      setLineButtonsDisabled(false); renderLines(); refreshGenerated(p);
      document.getElementById("step-download").classList.remove("hidden");
      refreshRecentList();
      return;
    }
    setTimeout(() => pollProgress(btn, progressText, barFill), 1000);
  } catch (e) {
    setTimeout(() => pollProgress(btn, progressText, barFill), 2000);
  }
}

let lastExportFolder = "";

async function runExport(folderName) {
  const status = document.getElementById("export-status");
  const btn = document.getElementById("btn-export-tracks");
  btn.disabled = true;
  status.textContent = "正在导出...";
  status.className = "status-text";
  try {
    const result = await api("/api/export_tracks", { method: "POST", body: JSON.stringify({ folder_name: folderName }) });
    lastExportFolder = result.folder;
    status.textContent = "导出完成：" + result.folder;
    status.className = "status-text success";
    document.getElementById("btn-open-export").classList.remove("hidden");
    document.getElementById("export-folder-input").value = "";
    refreshRecentList();
  } catch (e) {
    if (e.code === "folder_exists") {
      status.textContent = e.message;
      status.className = "status-text error";
    } else {
      status.textContent = "导出失败：" + e.message;
      status.className = "status-text error";
    }
  } finally {
    btn.disabled = false;
  }
}

document.getElementById("btn-export-tracks").addEventListener("click", async () => {
  const input = document.getElementById("export-folder-input");
  const status = document.getElementById("export-status");
  const folderName = input.value.trim();
  if (!folderName) {
    status.textContent = "请输入导出文件夹名称";
    status.className = "status-text error";
    return;
  }
  await runExport(folderName);
});

document.getElementById("export-folder-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") document.getElementById("btn-export-tracks").click();
});

document.getElementById("btn-open-export").addEventListener("click", async () => {
  if (!lastExportFolder) return;
  try {
    await api("/api/open_folder", { method: "POST", body: JSON.stringify({ path: lastExportFolder }) });
  } catch (e) {
    await showAlertModal("打开失败: " + e.message);
  }
});


function loadDeployPath(cfg) {
  const input = document.getElementById("deploy-gs-path");
  if (!input) return;
  const saved = localStorage.getItem("mygo_deploy_gs_path");
  if (saved) {
    input.value = saved;
  } else if (cfg.gptsovits_path) {
    input.value = cfg.gptsovits_path;
  }
  input.addEventListener("change", () => {
    localStorage.setItem("mygo_deploy_gs_path", input.value.trim());
  });
}

async function scanDeploy() {
  const btn = document.getElementById("btn-deploy-scan");
  const status = document.getElementById("deploy-scan-status");
  const result = document.getElementById("deploy-scan-result");
  const input = document.getElementById("deploy-gs-path");
  const path = input.value.trim();
  btn.disabled = true;
  status.textContent = "正在扫描环境...";
  status.className = "status-text";
  result.innerHTML = "";
  try {
    const data = await api("/api/deploy/scan", { method: "POST", body: JSON.stringify({ gptsovits_path: path }) });
    localStorage.setItem("mygo_deploy_gs_path", path);
    renderDeployScan(data);
    updateDeployBanner();
    status.textContent = "扫描完成";
    status.className = "status-text success";
  } catch (e) {
    status.textContent = e.message;
    status.className = "status-text error";
  } finally {
    btn.disabled = false;
  }
}

document.getElementById("btn-deploy-scan").addEventListener("click", scanDeploy);
function fmtSize(bytes) {
  if (!bytes && bytes !== 0) return "-";
  if (bytes >= 1024 * 1024 * 1024) return (bytes / (1024 * 1024 * 1024)).toFixed(2) + " GB";
  if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  if (bytes >= 1024) return (bytes / 1024).toFixed(0) + " KB";
  return bytes + " B";
}

function loadCleanPath(cfg) {
  const input = document.getElementById("clean-gs-path");
  if (!input) return;
  const saved = localStorage.getItem("mygo_deploy_gs_path");
  input.value = saved || (cfg.gptsovits_path || "");
  input.addEventListener("change", () => {
    localStorage.setItem("mygo_deploy_gs_path", input.value.trim());
  });
}

let cleanScanData = null;

async function scanClean() {
  const btn = document.getElementById("btn-clean-scan");
  const status = document.getElementById("clean-scan-status");
  const result = document.getElementById("clean-scan-result");
  const input = document.getElementById("clean-gs-path");
  if (!btn || !status || !result) return;
  const path = input.value.trim();
  btn.disabled = true;
  status.textContent = "正在扫描...";
  status.className = "status-text";
  result.innerHTML = "";
  try {
    const data = await api("/api/deploy/clean_scan", { method: "POST", body: JSON.stringify({ gptsovits_path: path }) });
    localStorage.setItem("mygo_deploy_gs_path", path);
    cleanScanData = data;
    renderCleanScan(data);
    status.textContent = "扫描完成";
    status.className = "status-text success";
  } catch (e) {
    status.textContent = e.message;
    status.className = "status-text error";
  } finally {
    btn.disabled = false;
  }
}

function renderCleanScan(d) {
  const host = document.getElementById("clean-scan-result");
  if (!host) return;
  const parts = [];
  const missing = d.missing_models || [];
  if (missing.length) {
    parts.push('<div class="clean-warning"><strong>SoVITS 中缺少 ' + missing.length + ' 个模型文件：</strong><ul class="issue-list">' +
      missing.slice(0, 20).map(m => "<li>" + esc(m.kind + " " + m.name) + "</li>").join("") +
      (missing.length > 20 ? "<li>…等 " + missing.length + " 项</li>" : "") +
      '</ul><p>请先到「部署」板块点击“复制缺失模型”，模型补齐后才能清理。</p></div>');
  } else {
    parts.push('<div class="clean-ok">模型已确认完整，可以安全清理程序包内的模型副本。</div>');
  }
  const groups = d.groups || [];
  if (!groups.length) {
    parts.push('<p class="status-text">暂无可清理的内容。</p>');
    host.innerHTML = parts.join("");
    return;
  }
  const items = groups.map(g =>
    '<label class="clean-item">' +
      '<input type="checkbox" class="clean-check" data-key="' + esc(g.key) + '"' + ((g.key === "model_weights" && g.safe) ? " checked" : "") + '>' +
      '<span class="clean-item-main"><span class="clean-item-title">' + esc(g.label) + ' <em>' + fmtSize(g.size) + '</em></span>' +
      '<span class="clean-item-detail">' + esc(g.detail) + '</span></span>' +
      '<span class="clean-item-warning">' + esc(g.warning) + '</span>' +
    '</label>'
  ).join("");
  parts.push('<div class="clean-groups">' + items + '</div>');
  parts.push('<div class="clean-total">已选 <span id="clean-total-size">0 MB</span></div>');
  parts.push('<div class="clean-actions"><button id="btn-clean-run" class="btn-secondary btn-small">一键清理选中项</button><span id="clean-run-status" class="status-text"></span></div>');
  host.innerHTML = parts.join("");
  const checks = host.querySelectorAll(".clean-check");
  checks.forEach(c => c.addEventListener("change", updateCleanTotal));
  updateCleanTotal();
  document.getElementById("btn-clean-run").addEventListener("click", runClean);
}

function selectedCleanKeys() {
  const checks = document.querySelectorAll("#clean-scan-result .clean-check:checked");
  return Array.from(checks).map(c => c.dataset.key);
}

function updateCleanTotal() {
  const el = document.getElementById("clean-total-size");
  if (!el) return;
  const keys = selectedCleanKeys();
  const groups = (cleanScanData && cleanScanData.groups) || [];
  const total = groups.filter(g => keys.indexOf(g.key) >= 0).reduce((sum, g) => sum + (g.size || 0), 0);
  el.textContent = fmtSize(total);
}

async function runClean() {
  const btn = document.getElementById("btn-clean-run");
  const statusEl = document.getElementById("clean-run-status");
  const input = document.getElementById("clean-gs-path");
  if (!btn || !statusEl) return;
  const keys = selectedCleanKeys();
  if (!keys.length) { await showAlertModal("请先勾选要清理的项目"); return; }
  const groups = (cleanScanData && cleanScanData.groups) || [];
  const total = groups.filter(g => keys.indexOf(g.key) >= 0).reduce((sum, g) => sum + (g.size || 0), 0);
  const missing = (cleanScanData && cleanScanData.missing_models) || [];
  const names = keys.map(k => { const g = groups.find(x => x.key === k); return g ? g.label + "（" + fmtSize(g.size) + "）" : k; });
  const msg = "即将删除以下内容：\n" + names.join("\n") + "\n\n共 " + fmtSize(total) + "，删除后不可恢复。确定继续吗？";
  if (keys.indexOf("model_weights") >= 0 && missing.length && !(await showConfirmModal("SoVITS 中缺少部分模型，继续清理可能导致这些角色无法生成语音。确定仍要清理吗？"))) return;
  if (!(await showConfirmModal(msg))) return;
  btn.disabled = true;
  statusEl.textContent = "正在清理...";
  statusEl.className = "status-text";
  try {
    const data = await api("/api/deploy/clean", { method: "POST", body: JSON.stringify({ gptsovits_path: input.value.trim(), items: keys, confirm_missing: keys.indexOf("model_weights") >= 0 && missing.length > 0 }) });
    const errors = data.errors || [];
    if (errors.length) {
      statusEl.textContent = "清理完成，但有 " + errors.length + " 项失败：" + errors.join("；");
      statusEl.className = "status-text error";
    } else {
      statusEl.textContent = "已清理 " + fmtSize(data.freed_bytes || 0);
      statusEl.className = "status-text success";
    }
    await scanClean();
    await loadConfig();
  } catch (e) {
    statusEl.textContent = e.message;
    statusEl.className = "status-text error";
  } finally {
    btn.disabled = false;
  }
}

function initCleanSpace() {
  const scanBtn = document.getElementById("btn-clean-scan");
  if (scanBtn) scanBtn.addEventListener("click", scanClean);
}



function scanGroup(title, items, okMode) {
  return '<div class="scan-group"><h4>' + esc(title) + "</h4>" +
    items.map(function (item) {
      const label = item[0];
      const value = String(item[1] == null ? "未知" : item[1]);
      let cls = "";
      if (okMode) {
        const ok = !/未|缺失|否|失败|not found|not installed|unknown/i.test(value);
        cls = ok ? " ok" : " bad";
      }
      const wrap = value.length > 48 ? " wrap" : "";
      return '<div class="scan-row' + wrap + '"><span class="scan-label">' + esc(label) + '</span><span class="scan-value' + cls + '" title="' + esc(value) + '">' + esc(value) + "</span></div>";
    }).join("") + "</div>";
}

function renderDeployScan(d) {
  const host = document.getElementById("deploy-scan-result");
  const rows = [];
  rows.push('<div class="scan-overview ' + (d.ready ? "ok" : "bad") + '">' + (d.ready ? "环境就绪" : "存在问题，请查看下方详情") + "</div>");

  const osLabel = (d.os.system || "") + " " + (d.os.release || "") + " (" + (d.os.arch || "") + ")";
  const cpuLabel = d.cpu + " · " + (d.cores || "?") + " 线程";
  rows.push(scanGroup("电脑配置", [
    ["系统", osLabel],
    ["CPU", cpuLabel],
    ["内存", d.memory_total_gb ? d.memory_total_gb + " GB" : "未知"],
    ["磁盘剩余", d.disk ? d.disk.free_gb + " GB / " + d.disk.total_gb + " GB" : "未知"],
    ["Python", d.python.version],
    ["pip", d.python.pip]
  ]));

  const gpuRows = d.gpu && d.gpu.length
    ? d.gpu.map(g => ["显卡", g.name + " · " + g.vram_gb + " GB · 驱动 " + g.driver])
    : [["NVIDIA 显卡", "未检测到"]];
  if (d.cuda_version) gpuRows.push(["CUDA", d.cuda_version]);
  rows.push(scanGroup("显卡 / CUDA", gpuRows, true));

  rows.push(scanGroup("依赖检查", d.packages.map(p => [p.name, p.installed ? p.version : "未安装"]), true));

  const gs = d.gptsovits || {};
  rows.push(scanGroup("GPT-SoVITS 目录", [
    ["目录", gs.path || "未配置"],
    ["目录存在", gs.exists ? "是" : "否"],
    ["runtime/python", gs.runtime_python ? "存在" : "缺失"],
    ["GPT_SoVITS", gs.checks && gs.checks.GPT_SoVITS ? "存在" : "缺失"],
    ["tools", gs.checks && gs.checks.tools ? "存在" : "缺失"],
    ["pretrained_models", gs.checks && gs.checks.pretrained_models ? "存在" : "缺失"],
    ["磁盘剩余", d.gptsovits_disk ? d.gptsovits_disk.free_gb + " GB" : "未知"]
  ], true));

  const modelRows = (d.models || []).map(m => [m.character + " / " + m.kind, m.installed ? "已安装" : (m.bundled ? "未安装" : "未随程序提供")]);
  let modelHtml = scanGroup("角色模型", modelRows, true);
  if (modelRows.some(r => r[1] === "未安装")) {
    modelHtml = modelHtml.replace(/<\/div>$/, '<div class="deploy-model-actions"><button id="btn-deploy-copy-models" class="btn-secondary btn-small">复制缺失模型</button><span id="deploy-model-status" class="status-text"></span></div></div>');
  }
  rows.push(modelHtml);
  let ffmpegRows = [
    ["状态", d.ffmpeg.installed ? "已安装" : "未安装"],
    ["版本", d.ffmpeg.version || "-"]
  ];
  let ffmpegHtml = scanGroup("FFmpeg", ffmpegRows, true);
  if (!d.ffmpeg.installed && d.gptsovits && d.gptsovits.exists) {
    ffmpegHtml = ffmpegHtml.replace(/<\/div>$/, '<div class="deploy-model-actions"><button id="btn-deploy-ffmpeg" class="btn-secondary btn-small">一键下载 ffmpeg</button><span id="deploy-ffmpeg-status" class="status-text"></span></div></div>');
  }
  rows.push(ffmpegHtml);

  const plan = d.install_plan || { packages: [] };
  if (plan.packages && plan.packages.length) {
    rows.push('<div class="scan-group"><h4>需要安装</h4><p class="install-note">' + esc(plan.note || "") + '</p><ul class="issue-list install-list">' + plan.packages.map(p => "<li>" + esc(p) + "</li>").join("") + '</ul><button id="btn-deploy-install" class="btn-secondary btn-small">一键 pip 安装</button><span id="deploy-install-status" class="status-text"></span></div>');
  } else {
    rows.push('<div class="scan-group"><h4>需要安装</h4><p class="install-note ok">' + esc(plan.note || "依赖已就绪，无需安装") + "</p></div>");
  }

  if (d.issues && d.issues.length) {
    rows.push('<div class="scan-group"><h4>需要处理</h4><ul class="issue-list">' + d.issues.map(i => "<li>" + esc(i) + "</li>").join("") + "</ul></div>");
  }

  host.innerHTML = rows.join("");
  const installBtn = host.querySelector("#btn-deploy-install");
  if (installBtn) installBtn.addEventListener("click", startDeployInstall);
  const copyBtn = host.querySelector("#btn-deploy-copy-models");
  if (copyBtn) copyBtn.addEventListener("click", startDeployCopyModels);
  const ffmpegBtn = host.querySelector("#btn-deploy-ffmpeg");
  if (ffmpegBtn) ffmpegBtn.addEventListener("click", startDeployFfmpeg);
}


let deployInstalling = false;

async function startDeployInstall() {
  if (deployInstalling) return;
  const input = document.getElementById("deploy-gs-path");
  const btn = document.getElementById("btn-deploy-install");
  const statusEl = document.getElementById("deploy-install-status");
  const logBox = document.getElementById("deploy-install-box");
  const logEl = document.getElementById("deploy-install-log");
  if (!btn || !statusEl) return;
  deployInstalling = true;
  btn.disabled = true;
  statusEl.textContent = "正在准备安装...";
  statusEl.className = "status-text";
  if (logBox) logBox.classList.remove("hidden");
  const progressFill = document.getElementById("deploy-progress-fill");
  if (progressFill) progressFill.style.width = "0%";
  if (logEl) logEl.textContent = "";
  try {
    const data = await api("/api/deploy/install", { method: "POST", body: JSON.stringify({ gptsovits_path: input.value.trim() }) });
    if (data.status === "nothing_to_install") {
      statusEl.textContent = "无需安装";
      statusEl.className = "status-text success";
      deployInstalling = false;
      btn.disabled = false;
      return;
    }
    pollDeployInstall(btn, statusEl, logEl);
  } catch (e) {
    statusEl.textContent = e.message;
    statusEl.className = "status-text error";
    deployInstalling = false;
    btn.disabled = false;
  }
}

async function pollDeployInstall(btn, statusEl, logEl) {
  try {
    const st = await api("/api/deploy/install_status");
    if (logEl) {
      logEl.textContent = (st.log || []).join("\n");
      logEl.scrollTop = logEl.scrollHeight;
    }
    const progressFill = document.getElementById("deploy-progress-fill");
    const pct = Math.min(100, Math.max(0, st.progress || 0));
    if (progressFill) progressFill.style.width = pct + "%";
    if (st.running) {
      const pkgText = (st.current_packages && st.current_packages.length) ? "（" + st.current_packages.join(", ") + "）" : "";
      statusEl.textContent = "安装中 " + pct + "%" + pkgText;
      statusEl.className = "status-text";
      setTimeout(() => pollDeployInstall(btn, statusEl, logEl), 1200);
      return;
    }
    deployInstalling = false;
    btn.disabled = false;
    if (st.success) {
      if (progressFill) progressFill.style.width = "100%";
      statusEl.textContent = "安装完成 100%，正在重新扫描...";
      statusEl.className = "status-text success";
      await scanDeploy();
    } else {
      statusEl.textContent = "安装失败，请查看日志";
      statusEl.className = "status-text error";
    }
  } catch (e) {
    statusEl.textContent = e.message;
    statusEl.className = "status-text error";
    deployInstalling = false;
    btn.disabled = false;
  }
}

let deployCopying = false;

async function startDeployCopyModels() {
  if (deployCopying) return;
  const input = document.getElementById("deploy-gs-path");
  const btn = document.getElementById("btn-deploy-copy-models");
  const statusEl = document.getElementById("deploy-model-status");
  if (!btn || !statusEl) return;
  deployCopying = true;
  btn.disabled = true;
  statusEl.textContent = "正在准备复制...";
  statusEl.className = "status-text";
  const logBox = document.getElementById("deploy-install-box");
  const logEl = document.getElementById("deploy-install-log");
  if (logBox) logBox.classList.remove("hidden");
  const progressFill = document.getElementById("deploy-progress-fill");
  if (progressFill) progressFill.style.width = "0%";
  if (logEl) logEl.textContent = "";
  try {
    const data = await api("/api/deploy/copy_models", { method: "POST", body: JSON.stringify({ gptsovits_path: input.value.trim() }) });
    if (data.status === "nothing_to_copy") {
      statusEl.textContent = "无需复制";
      statusEl.className = "status-text success";
      deployCopying = false;
      btn.disabled = false;
      return;
    }
    pollDeployCopyModels(btn, statusEl, logEl);
  } catch (e) {
    statusEl.textContent = e.message;
    statusEl.className = "status-text error";
    deployCopying = false;
    btn.disabled = false;
  }
}

async function pollDeployCopyModels(btn, statusEl, logEl) {
  try {
    const st = await api("/api/deploy/copy_models_status");
    if (logEl) {
      logEl.textContent = (st.log || []).join("\n");
      logEl.scrollTop = logEl.scrollHeight;
    }
    const progressFill = document.getElementById("deploy-progress-fill");
    const pct = Math.min(100, Math.max(0, st.progress || 0));
    if (progressFill) progressFill.style.width = pct + "%";
    if (st.running) {
      statusEl.textContent = "复制中 " + pct + "% " + (st.current || "");
      statusEl.className = "status-text";
      setTimeout(() => pollDeployCopyModels(btn, statusEl, logEl), 1200);
      return;
    }
    deployCopying = false;
    btn.disabled = false;
    if (st.success) {
      if (progressFill) progressFill.style.width = "100%";
      statusEl.textContent = "模型复制完成 100%，正在重新扫描...";
      statusEl.className = "status-text success";
      await scanDeploy();
    } else {
      statusEl.textContent = "模型复制失败，请查看日志";
      statusEl.className = "status-text error";
    }
  } catch (e) {
    statusEl.textContent = e.message;
    statusEl.className = "status-text error";
    deployCopying = false;
    btn.disabled = false;
  }
}

let deployFfmpeg = false;

async function startDeployFfmpeg() {
  if (deployFfmpeg) return;
  const input = document.getElementById("deploy-gs-path");
  const btn = document.getElementById("btn-deploy-ffmpeg");
  const statusEl = document.getElementById("deploy-ffmpeg-status");
  if (!btn || !statusEl) return;
  deployFfmpeg = true;
  btn.disabled = true;
  statusEl.textContent = "正在准备下载...";
  statusEl.className = "status-text";
  const logBox = document.getElementById("deploy-install-box");
  const logEl = document.getElementById("deploy-install-log");
  if (logBox) logBox.classList.remove("hidden");
  const progressFill = document.getElementById("deploy-progress-fill");
  if (progressFill) progressFill.style.width = "0%";
  if (logEl) logEl.textContent = "";
  try {
    const data = await api("/api/deploy/install_ffmpeg", { method: "POST", body: JSON.stringify({ gptsovits_path: input.value.trim() }) });
    if (data.status === "nothing_to_do") {
      statusEl.textContent = "已存在，无需下载";
      statusEl.className = "status-text success";
      deployFfmpeg = false;
      btn.disabled = false;
      return;
    }
    pollDeployFfmpeg(btn, statusEl, logEl);
  } catch (e) {
    statusEl.textContent = e.message;
    statusEl.className = "status-text error";
    deployFfmpeg = false;
    btn.disabled = false;
  }
}

async function pollDeployFfmpeg(btn, statusEl, logEl) {
  try {
    const st = await api("/api/deploy/install_ffmpeg_status");
    if (logEl) {
      logEl.textContent = (st.log || []).join("\n");
      logEl.scrollTop = logEl.scrollHeight;
    }
    const progressFill = document.getElementById("deploy-progress-fill");
    const pct = Math.min(100, Math.max(0, st.progress || 0));
    if (progressFill) progressFill.style.width = pct + "%";
    if (st.running) {
      statusEl.textContent = "下载中 " + pct + "%";
      statusEl.className = "status-text";
      setTimeout(() => pollDeployFfmpeg(btn, statusEl, logEl), 1200);
      return;
    }
    deployFfmpeg = false;
    btn.disabled = false;
    if (st.success) {
      if (progressFill) progressFill.style.width = "100%";
      statusEl.textContent = "ffmpeg 安装完成，正在重新扫描...";
      statusEl.className = "status-text success";
      await scanDeploy();
    } else {
      statusEl.textContent = "ffmpeg 下载失败，请查看日志";
      statusEl.className = "status-text error";
    }
  } catch (e) {
    statusEl.textContent = e.message;
    statusEl.className = "status-text error";
    deployFfmpeg = false;
    btn.disabled = false;
  }
}

function showDeployFlow(kind) {
  const question = document.getElementById("deploy-question");
  const hasFlow = document.getElementById("deploy-has-flow");
  const noFlow = document.getElementById("deploy-no-flow");
  if (!question || !hasFlow || !noFlow) return;
  if (kind === "has") {
    question.classList.add("hidden");
    hasFlow.classList.remove("hidden");
    noFlow.classList.add("hidden");
  } else if (kind === "no") {
    question.classList.add("hidden");
    hasFlow.classList.add("hidden");
    noFlow.classList.remove("hidden");
  } else {
    question.classList.remove("hidden");
    hasFlow.classList.add("hidden");
    noFlow.classList.add("hidden");
  }
}

function initDeployFlow() {
  const saved = localStorage.getItem("mygo_deploy_installed");
  if (saved === "yes" || saved === "no") showDeployFlow(saved);
  document.getElementById("btn-deploy-has").addEventListener("click", () => {
    localStorage.setItem("mygo_deploy_installed", "yes");
    showDeployFlow("has");
    updateDeployBanner();
  });
  document.getElementById("btn-deploy-no").addEventListener("click", () => {
    localStorage.setItem("mygo_deploy_installed", "no");
    showDeployFlow("no");
    updateDeployBanner();
  });
  document.getElementById("btn-deploy-back-has").addEventListener("click", () => {
    localStorage.removeItem("mygo_deploy_installed");
    showDeployFlow(null);
    updateDeployBanner();
  });
  document.getElementById("btn-deploy-back-no").addEventListener("click", () => {
    localStorage.removeItem("mygo_deploy_installed");
    showDeployFlow(null);
    updateDeployBanner();
  });
  initDeployDownload();
  updateDeployBanner();
}

let deployDownloadOptions = [];
let deployDownloading = false;

async function loadDeployDownloadOptions() {
  const wrap = document.getElementById("deploy-download-options");
  if (!wrap) return;
  try {
    const data = await api("/api/deploy/download_options");
    deployDownloadOptions = data.options || [];
    const recommendedId = (data.recommended || {}).id;
    if (!deployDownloadOptions.length) {
      wrap.innerHTML = '<span class="status-text error">未获取到下载选项</span>';
      return;
    }
    wrap.innerHTML = deployDownloadOptions.map(opt => {
      const isRec = opt.id === recommendedId;
      return '<label class="deploy-download-option' + (isRec ? " recommended" : "") + '">'
        + '<input type="radio" name="deploy-download-option" value="' + esc(opt.id) + '"' + (isRec ? " checked" : "") + '>'
        + '<span class="dd-label">' + esc(opt.label) + '</span>'
        + (isRec ? '<span class="dd-badge">已按你的显卡推荐</span>' : "")
        + "</label>";
    }).join("");
  } catch (e) {
    wrap.innerHTML = '<span class="status-text error">加载下载选项失败: ' + esc(e.message) + "</span>";
  }
}

function getDeployDownloadOption() {
  const checked = document.querySelector('input[name="deploy-download-option"]:checked');
  if (!checked) return null;
  return deployDownloadOptions.find(opt => opt.id === checked.value) || null;
}

function initDeployDownload() {
  loadDeployDownloadOptions();
  const dirInput = document.getElementById("deploy-download-dir");
  const btn = document.getElementById("btn-deploy-download");
  const cancelBtn = document.getElementById("btn-deploy-download-cancel");
  const updateBtn = () => { btn.disabled = !dirInput.value.trim() || deployDownloading; };
  dirInput.addEventListener("input", updateBtn);
  updateBtn();
  btn.addEventListener("click", startDeployDownload);
  syncDeployDownloadState();
  cancelBtn.addEventListener("click", async () => {
    try {
      await api("/api/deploy/download_cancel", { method: "POST" });
      document.getElementById("deploy-download-status").textContent = "正在取消...";
    } catch (e) {
      document.getElementById("deploy-download-status").textContent = e.message;
    }
  });
}

async function syncDeployDownloadState() {
  const btn = document.getElementById("btn-deploy-download");
  const cancelBtn = document.getElementById("btn-deploy-download-cancel");
  const statusEl = document.getElementById("deploy-download-status");
  const box = document.getElementById("deploy-download-box");
  const logEl = document.getElementById("deploy-download-log");
  const fill = document.getElementById("deploy-download-fill");
  try {
    const st = await api("/api/deploy/download_status");
    if (st.running) {
      deployDownloading = true;
      btn.disabled = true;
      cancelBtn.classList.remove("hidden");
      box.classList.remove("hidden");
      logEl.textContent = (st.log || []).join("\n");
      logEl.scrollTop = logEl.scrollHeight;
      const pct = Math.min(100, Math.max(0, st.progress || 0));
      fill.style.width = pct + "%";
      statusEl.textContent = st.cancel_requested ? "正在取消..." : "下载并解压中 " + pct + "%";
      statusEl.className = "status-text";
      pollDeployDownload();
      return;
    }
    deployDownloading = false;
    cancelBtn.classList.add("hidden");
    btn.disabled = false;
    if (!st.done) return;
    box.classList.remove("hidden");
    logEl.textContent = (st.log || []).join("\n");
    logEl.scrollTop = logEl.scrollHeight;
    if (st.success) {
      fill.style.width = "100%";
      statusEl.textContent = "下载并解压完成";
      statusEl.className = "status-text success";
      const gsPath = st.extracted_path || st.target_dir || "";
      document.getElementById("deploy-gs-path").value = gsPath;
      localStorage.setItem("mygo_deploy_gs_path", gsPath);
      localStorage.setItem("mygo_deploy_installed", "yes");
      showDeployFlow("has");
      updateDeployBanner();
    } else if (st.cancelled) {
      statusEl.textContent = "已取消下载";
      statusEl.className = "status-text";
    } else {
      statusEl.textContent = "下载失败，请查看日志";
      statusEl.className = "status-text error";
    }
  } catch (e) {
    statusEl.textContent = e.message;
    statusEl.className = "status-text error";
  }
}

async function startDeployDownload() {
  if (deployDownloading) return;
  const opt = getDeployDownloadOption();
  const target = document.getElementById("deploy-download-dir").value.trim();
  if (!opt || !target) return;
  const btn = document.getElementById("btn-deploy-download");
  const cancelBtn = document.getElementById("btn-deploy-download-cancel");
  const statusEl = document.getElementById("deploy-download-status");
  const box = document.getElementById("deploy-download-box");
  const logEl = document.getElementById("deploy-download-log");
  const fill = document.getElementById("deploy-download-fill");
  deployDownloading = true;
  btn.disabled = true;
  cancelBtn.classList.remove("hidden");
  statusEl.textContent = "正在准备下载...";
  statusEl.className = "status-text";
  box.classList.remove("hidden");
  logEl.textContent = "";
  fill.style.width = "0%";
  try {
    await api("/api/deploy/download", { method: "POST", body: JSON.stringify({ url: opt.url, target_dir: target }) });
    pollDeployDownload();
  } catch (e) {
    statusEl.textContent = e.message;
    statusEl.className = "status-text error";
    deployDownloading = false;
    cancelBtn.classList.add("hidden");
    btn.disabled = false;
    syncDeployDownloadState();
  }
}

async function pollDeployDownload() {
  const btn = document.getElementById("btn-deploy-download");
  const cancelBtn = document.getElementById("btn-deploy-download-cancel");
  const statusEl = document.getElementById("deploy-download-status");
  const logEl = document.getElementById("deploy-download-log");
  const fill = document.getElementById("deploy-download-fill");
  try {
    const st = await api("/api/deploy/download_status");
    if (logEl) {
      logEl.textContent = (st.log || []).join("\n");
      logEl.scrollTop = logEl.scrollHeight;
    }
    const pct = Math.min(100, Math.max(0, st.progress || 0));
    if (fill) fill.style.width = pct + "%";
    if (st.running) {
      statusEl.textContent = st.cancel_requested ? "正在取消..." : "下载并解压中 " + pct + "%";
      statusEl.className = "status-text";
      setTimeout(pollDeployDownload, 1000);
      return;
    }
    deployDownloading = false;
    cancelBtn.classList.add("hidden");
    btn.disabled = false;
    if (st.success) {
      statusEl.textContent = "下载并解压完成";
      statusEl.className = "status-text success";
      if (fill) fill.style.width = "100%";
      const gsPath = st.extracted_path || st.target_dir || "";
      document.getElementById("deploy-gs-path").value = gsPath;
      localStorage.setItem("mygo_deploy_gs_path", gsPath);
      localStorage.setItem("mygo_deploy_installed", "yes");
      showDeployFlow("has");
      updateDeployBanner();
    } else if (st.cancelled) {
      statusEl.textContent = "已取消下载";
      statusEl.className = "status-text";
    } else {
      statusEl.textContent = "下载失败，请查看日志";
      statusEl.className = "status-text error";
    }
  } catch (e) {
    deployDownloading = false;
    cancelBtn.classList.add("hidden");
    btn.disabled = false;
    statusEl.textContent = e.message;
    statusEl.className = "status-text error";
  }
}
// ===== WebGaL 板块 =====
let webgalPollTimer = null;
let webgalAnalyzeController = null;
let webgalDraftTimer = null;
let webgalDraftSeq = 0;

function setWebGalStatus(message, kind) {
  const el = document.getElementById("webgal-status");
  if (!el) return;
  el.textContent = message || "";
  el.className = "status-text" + (kind ? " " + kind : "");
}

function populateWebGalPsyCharacters() {
  const sel = document.getElementById("webgal-psy-character");
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = '<option value="">沿用该行角色</option>' + state.chars.map(c => '<option value="' + esc(c) + '">' + esc(c) + '</option>').join("");
  if (current && state.chars.indexOf(current) !== -1) sel.value = current;
}

function resetWebGalProject() {
  state.webgal = {
    source: "", dialogues: [], emotions: {}, translations: {}, generated: {}, failures: {},
    generating: false, lastExport: "", psyVoice: false, psyCharacter: "", lang: "zh", analyzing: false,
    progress: { current: 0, total: 0 }
  };
  if (webgalPollTimer) { clearInterval(webgalPollTimer); webgalPollTimer = null; }
  if (webgalParseController) { webgalParseController.abort(); webgalParseController = null; }
  if (webgalTranslateController) { webgalTranslateController.abort(); webgalTranslateController = null; }
  if (webgalAnalyzeController) { webgalAnalyzeController.abort(); webgalAnalyzeController = null; }
  const input = document.getElementById("webgal-input");
  if (input) input.value = "";
  const psyVoice = document.getElementById("webgal-psy-voice");
  if (psyVoice) psyVoice.checked = false;
  const psyChar = document.getElementById("webgal-psy-character");
  if (psyChar) psyChar.value = "";
  const langSel = document.getElementById("webgal-lang");
  if (langSel) langSel.value = "zh";
  updateWebGalTranslateButton();
  ["webgal-settings", "webgal-review", "webgal-export"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.add("hidden");
  });
  const container = document.getElementById("webgal-lines-container");
  if (container) container.innerHTML = "";
  const count = document.getElementById("webgal-line-count");
  if (count) count.textContent = "";
  const failures = document.getElementById("webgal-failures");
  if (failures) failures.textContent = "";
  const progress = document.getElementById("webgal-progress-text");
  if (progress) { progress.textContent = ""; progress.classList.add("hidden"); }
  const openBtn = document.getElementById("btn-webgal-open-export");
  if (openBtn) openBtn.classList.add("hidden");
  const exportStatus = document.getElementById("webgal-export-status");
  if (exportStatus) { exportStatus.textContent = ""; exportStatus.className = "status-text"; }
  const exportInput = document.getElementById("webgal-export-folder");
  if (exportInput) exportInput.value = "";
  setWebGalStatus("", "");
  resetWebGalHistory();
}

const WEBGAL_HISTORY_LIMIT = 60;
let webgalHistory = [];
let webgalHistoryIndex = -1;

function webgalSnapshot() {
  const wg = state.webgal;
  return {
    source: wg.source || "",
    lang: wg.lang || "zh",
    dialogues: JSON.parse(JSON.stringify(wg.dialogues || [])),
    emotions: { ...(wg.emotions || {}) },
    translations: { ...(wg.translations || {}) },
    generated: { ...(wg.generated || {}) },
    failures: { ...(wg.failures || {}) },
    psyVoice: !!wg.psyVoice,
    psyCharacter: wg.psyCharacter || "",
    lastExport: wg.lastExport || "",
  };
}

function resetWebGalHistory() {
  webgalHistory = [];
  webgalHistoryIndex = -1;
  refreshHistoryButtons();
}

function pushWebGalHistory() {
  webgalHistory = webgalHistory.slice(0, webgalHistoryIndex + 1);
  webgalHistory.push(webgalSnapshot());
  if (webgalHistory.length > WEBGAL_HISTORY_LIMIT) webgalHistory.shift();
  webgalHistoryIndex = webgalHistory.length - 1;
  refreshHistoryButtons();
}

function restoreWebGalSnapshot(snap) {
  const s = snap || {};
  state.webgal.source = s.source || "";
  state.webgal.lang = s.lang || "zh";
  state.webgal.dialogues = JSON.parse(JSON.stringify(s.dialogues || []));
  state.webgal.emotions = { ...(s.emotions || {}) };
  state.webgal.translations = { ...(s.translations || {}) };
  state.webgal.generated = { ...(s.generated || {}) };
  state.webgal.failures = { ...(s.failures || {}) };
  state.webgal.psyVoice = !!s.psyVoice;
  state.webgal.psyCharacter = s.psyCharacter || "";
  state.webgal.lastExport = s.lastExport || "";
  state.webgal.progress = { current: 0, total: 0 };
  state.webgal.generating = false;
  const input = document.getElementById("webgal-input");
  if (input) input.value = state.webgal.source;
  const langSel = document.getElementById("webgal-lang");
  if (langSel) langSel.value = state.webgal.lang;
  updateWebGalTranslateButton();
  const psyVoiceEl = document.getElementById("webgal-psy-voice");
  if (psyVoiceEl) psyVoiceEl.checked = state.webgal.psyVoice;
  const psyCharEl = document.getElementById("webgal-psy-character");
  if (psyCharEl) psyCharEl.value = state.webgal.psyCharacter;
  populateWebGalPsyCharacters();
  ["webgal-settings", "webgal-review", "webgal-export"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove("hidden");
  });
  renderWebGalLines();
  updateWebGalProgress();
  const openBtn = document.getElementById("btn-webgal-open-export");
  if (openBtn) openBtn.classList.toggle("hidden", !state.webgal.lastExport);
}

function webgalUndo() {
  if (webgalHistoryIndex <= 0) return false;
  webgalHistoryIndex -= 1;
  restoreWebGalSnapshot(webgalHistory[webgalHistoryIndex]);
  refreshHistoryButtons();
  return true;
}

function webgalRedo() {
  if (webgalHistoryIndex >= webgalHistory.length - 1) return false;
  webgalHistoryIndex += 1;
  restoreWebGalSnapshot(webgalHistory[webgalHistoryIndex]);
  refreshHistoryButtons();
  return true;
}

async function translateWebGal(silent) {
  if (wgCurrentLang() !== "ja") {
    state.webgal.translations = {};
    renderWebGalLines();
    return { ok: true };
  }
  if (state.aiMode === "manual") {
    setWebGalStatus("客户端生成模式下请点击“客户端生成”生成提示词并应用结果", "error");
    return { ok: false, error: new Error("manual mode") };
  }
  const statusEl = document.getElementById("webgal-status");
  const analyzeBtn = document.getElementById("btn-webgal-analyze");
  if (!silent && statusEl) { statusEl.textContent = "正在日语翻译..."; statusEl.className = "status-text"; }
  if (analyzeBtn) analyzeBtn.disabled = true;
  if (webgalTranslateController) webgalTranslateController.abort();
  const controller = new AbortController();
  webgalTranslateController = controller;
  refreshHistoryButtons();
  updateWebGalTranslateButton();
  try {
    const data = await api("/api/webgal/translate", {
      method: "POST",
      body: JSON.stringify({ lang: "ja" }),
      signal: controller.signal
    });
    state.webgal.translations = data.translations || {};
    renderWebGalLines();
    pushWebGalHistory();
    return { ok: true };
  } catch (e) {
    if (e.name === "AbortError") {
      if (!silent) setWebGalStatus("已取消日语翻译", "");
    } else {
      setWebGalStatus(e.message || "日语翻译失败", "error");
    }
    return { ok: false, error: e };
  } finally {
    if (webgalTranslateController === controller) webgalTranslateController = null;
    if (analyzeBtn) analyzeBtn.disabled = false;
    updateWebGalTranslateButton();
    refreshHistoryButtons();
  }
}

function wgCurrentLang() {
  const el = document.getElementById("webgal-lang");
  return el ? (el.value || "zh") : state.webgal.lang;
}

function updateWebGalTranslateButton() {
  const btn = document.getElementById("btn-webgal-translate");
  if (!btn) return;
  btn.disabled = wgCurrentLang() !== "ja" || state.webgal.analyzing || !!webgalTranslateController || state.webgal.generating;
}

function syncWebGalDraft() {
  const input = document.getElementById("webgal-input");
  const text = input ? input.value : "";
  state.webgal.source = text;
  state.script = text;
  clearTimeout(webgalDraftTimer);
  const seq = ++webgalDraftSeq;
  webgalDraftTimer = setTimeout(() => {
    api("/api/script", { method: "POST", body: JSON.stringify({ text, seq }) }).catch(() => {});
  }, 400);
}

async function syncWebGalState() {
  if (state.projectType !== "webgal") return;
  const wg = state.webgal;
  await api("/api/webgal/sync", {
    method: "POST",
    body: JSON.stringify({
      source: wg.source || "",
      lang: wg.lang || "zh",
      dialogues: wg.dialogues || [],
      emotions: wg.emotions || {},
      translations: wg.translations || {},
      generated: wg.generated || {},
      failures: wg.failures || {},
      psyVoice: !!wg.psyVoice,
      psyCharacter: wg.psyCharacter || "",
      lastExport: wg.lastExport || ""
    })
  });
}

async function parseWebGal() {
  if (state.webgal.generating) { setWebGalStatus("生成中，请稍后再解析", "error"); return; }
  const text = (document.getElementById("webgal-input").value || "").trim();
  if (!text) { setWebGalStatus("请先粘贴 anogo 脚本", "error"); return; }
  setWebGalStatus("正在解析脚本...", "");
  const stopBtn = document.getElementById("btn-webgal-stop-analyze");
  if (stopBtn) { stopBtn.textContent = "停止解析"; stopBtn.classList.remove("hidden"); }
  if (webgalParseController) webgalParseController.abort();
  const parseController = new AbortController();
  webgalParseController = parseController;
  refreshHistoryButtons();
  try {
    const lang = wgCurrentLang();
    const data = await api("/api/webgal/parse", { method: "POST", body: JSON.stringify({ text, lang }), signal: parseController.signal });
    state.webgal.source = text;
    state.webgal.lang = lang;
    updateWebGalTranslateButton();
    state.webgal.dialogues = data.dialogues || [];
    state.webgal.emotions = {};
    state.webgal.translations = {};
    state.webgal.generated = {};
    state.webgal.failures = {};
    state.webgal.progress = { current: 0, total: 0 };
    state.webgal.lastExport = "";
    state.webgal.psyVoice = document.getElementById("webgal-psy-voice").checked;
    state.webgal.psyCharacter = document.getElementById("webgal-psy-character").value;
    populateWebGalPsyCharacters();
    ["webgal-settings", "webgal-review", "webgal-export"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.classList.remove("hidden");
    });
    renderWebGalLines();
    pushWebGalHistory();
    if (lang === "ja") {
      if (state.aiMode === "manual") {
        setWebGalStatus("已解析 " + data.count + " 条对话；客户端生成模式下请生成翻译提示词并应用结果", "success");
      } else {
        const tr = await translateWebGal(false);
        if (tr.ok) {
          setWebGalStatus("已解析 " + data.count + " 条对话，日语翻译完成", "success");
        }
      }
    } else {
      setWebGalStatus("已解析 " + data.count + " 条对话，可以校对或让 AI 分析情绪", "success");
    }
    syncWebGalDraft();
  } catch (e) {
    if (e.name === "AbortError") {
      setWebGalStatus("已取消解析", "");
    } else {
      setWebGalStatus("解析失败: " + e.message, "error");
    }
  } finally {
    if (webgalParseController === parseController) webgalParseController = null;
    if (stopBtn) { stopBtn.textContent = "停止分析"; stopBtn.classList.add("hidden"); }
    refreshHistoryButtons();
  }
}

function renderWebGalLines() {
  const container = document.getElementById("webgal-lines-container");
  if (!container) return;
  const wg = state.webgal;
  const countEl = document.getElementById("webgal-line-count");
  if (countEl) countEl.textContent = "共 " + wg.dialogues.length + " 条对话";
  container.innerHTML = wg.dialogues.map(d => {
    const idx = d.index;
    const emotion = wg.emotions[idx] || d.emotion || "思考";
    const opts = state.emotions.map(e => '<option value="' + esc(e) + '"' + (e === emotion ? " selected" : "") + '>' + esc(e) + '</option>').join("");
    const gen = wg.generated[idx];
    const failMsg = wg.failures[idx];
    const psyNote = d.is_psy
      ? (wg.psyVoice ? (wg.psyCharacter ? "心理活动 → 配给 " + esc(wg.psyCharacter) : "心理活动 · 沿用角色配音") : "心理活动 · 不配音")
      : "";
    const extraNote = (!d.is_psy && state.chars.indexOf(d.character) === -1) ? "路人 · 不配音" : "";
    const note = psyNote || extraNote;
    const canVoice = (d.is_psy && wg.psyVoice) || (!d.is_psy && state.chars.indexOf(d.character) !== -1);
    const audioState = gen ? '<span class="wg-audio-ok">已生成</span>' : (failMsg ? '<span class="wg-audio-fail">' + esc(failMsg) + '</span>' : '');
    const actions = canVoice
      ? '<button type="button" class="btn-line-action wg-play" data-index="' + idx + '"' + (gen ? "" : " disabled") + '>试听</button>'
        + '<button type="button" class="btn-line-action wg-regen" data-index="' + idx + '"' + (gen || failMsg ? "" : " disabled") + '>重新生成</button>'
        + audioState
      : '<span class="wg-psy-note">不生成</span>' + audioState;
    return '<div class="line-item">'
      + '<span class="idx">#' + (idx + 1) + '</span>'
      + '<span class="char">' + esc(d.character) + '</span>'
      + '<span class="line-texts">'
      + '<span class="text">' + esc(d.text) + '</span>'
      + (wg.translations[idx] ? '<span class="translated">日语：' + esc(wg.translations[idx]) + '</span>' : '')
      + (note ? '<span class="wg-psy-note">' + note + '</span>' : '')
      + '</span>'
      + '<span class="wg-fig">' + (d.figure_id ? esc(d.figure_id) : "—") + '</span>'
      + '<select class="emotion-select" data-index="' + idx + '"' + (canVoice ? "" : " disabled") + '>' + opts + '</select>'
      + '<span class="line-actions">' + actions + '</span>'
      + '</div>';
  }).join("");
  container.querySelectorAll(".emotion-select").forEach(sel => {
    sel.addEventListener("change", () => {
      state.webgal.emotions[parseInt(sel.dataset.index, 10)] = sel.value;
      pushWebGalHistory();
    });
  });
  container.querySelectorAll(".wg-play").forEach(btn => {
    btn.addEventListener("click", () => playWebGalAudio(parseInt(btn.dataset.index, 10), btn));
  });
  container.querySelectorAll(".wg-regen").forEach(btn => {
    btn.addEventListener("click", () => generateWebGal([parseInt(btn.dataset.index, 10)]));
  });
}

function playWebGalAudio(idx, btn) {
  if (!audioPlayer) audioPlayer = new Audio();
  if (!audioPlayer.paused && audioPlayer.dataset && audioPlayer.dataset.wgIdx === String(idx)) {
    audioPlayer.pause();
    if (btn) btn.textContent = "试听";
    return;
  }
  audioPlayer.src = "/api/webgal/audio/" + idx + "?t=" + Date.now();
  audioPlayer.dataset = audioPlayer.dataset || {};
  audioPlayer.dataset.wgIdx = String(idx);
  audioPlayer.onended = () => { if (btn) btn.textContent = "试听"; };
  audioPlayer.play().then(() => { if (btn) btn.textContent = "停止"; }).catch(() => { if (btn) btn.textContent = "试听"; });
}

async function analyzeWebGal() {
  if (!state.webgal.dialogues.length) { setWebGalStatus("请先解析脚本", "error"); return; }
  if (state.webgal.generating) { setWebGalStatus("生成中，请稍后再分析", "error"); return; }
  if (state.webgal.analyzing) return;
  if (state.aiMode === "manual") { openWgClientAi("analyze"); return; }
  setWebGalStatus("正在分析情绪...", "");
  const btn = document.getElementById("btn-webgal-analyze");
  if (btn) btn.disabled = true;
  const stopBtn = document.getElementById("btn-webgal-stop-analyze");
  if (stopBtn) { stopBtn.textContent = "停止分析"; stopBtn.classList.remove("hidden"); }
  if (webgalAnalyzeController) webgalAnalyzeController.abort();
  const controller = new AbortController();
  webgalAnalyzeController = controller;
  state.webgal.analyzing = true;
  refreshHistoryButtons();
  updateWebGalTranslateButton();
  try {
    const data = await api("/api/webgal/analyze", {
      method: "POST",
      body: JSON.stringify({ lang: wgCurrentLang() }),
      signal: controller.signal
    });
    if (data.status === "cancelled") {
      setWebGalStatus("已停止分析", "");
      return;
    }
    state.webgal.emotions = data.emotions || {};
    state.webgal.translations = data.translations || {};
    renderWebGalLines();
    pushWebGalHistory();
    setWebGalStatus("情绪分析完成，已填充 " + Object.keys(state.webgal.emotions).length + " 条", "success");
  } catch (e) {
    if (e.name === "AbortError") {
      setWebGalStatus("已停止分析", "");
    } else {
      setWebGalStatus("情绪分析失败: " + e.message, "error");
    }
  } finally {
    state.webgal.analyzing = false;
    webgalAnalyzeController = null;
    if (btn) btn.disabled = false;
    if (stopBtn) stopBtn.classList.add("hidden");
    refreshHistoryButtons();
  }
}

async function stopWebGalAnalyze() {
  if (webgalParseController) webgalParseController.abort();
  if (webgalTranslateController) webgalTranslateController.abort();
  if (webgalAnalyzeController) webgalAnalyzeController.abort();
  try { await api("/api/analyze/cancel", { method: "POST" }); } catch (e) {}
  setWebGalStatus("正在停止...", "");
}

async function generateWebGal(indices) {
  if (!state.webgal.dialogues.length) { setWebGalStatus("请先解析脚本", "error"); return; }
  if (state.webgal.generating) return;
  if (wgCurrentLang() === "ja") {
    const missing = state.webgal.dialogues.some(d => !state.webgal.translations[d.index]);
    if (missing) {
      const tr = await translateWebGal(true);
      if (!tr.ok) { setWebGalStatus("缺少日语翻译，已取消生成", "error"); return; }
    }
  }
  state.webgal.psyVoice = document.getElementById("webgal-psy-voice").checked;
  state.webgal.psyCharacter = document.getElementById("webgal-psy-character").value;
  const emotions = {};
  state.webgal.dialogues.forEach(d => {
    emotions[d.index] = state.webgal.emotions[d.index] || d.emotion || "思考";
  });
  const statusEl = document.getElementById("webgal-progress-text");
  if (statusEl) {
    statusEl.classList.remove("hidden");
    statusEl.textContent = indices && indices.length ? "正在生成 " + indices.length + " 条..." : "正在生成 " + state.webgal.dialogues.length + " 条...";
    statusEl.className = "status-text";
  }
  const genBtn = document.getElementById("btn-webgal-generate");
  const cancelBtn = document.getElementById("btn-webgal-cancel");
  if (genBtn) genBtn.disabled = true;
  if (cancelBtn) cancelBtn.classList.remove("hidden");
  try {
    const data = await api("/api/webgal/generate", {
      method: "POST",
      body: JSON.stringify({
        emotions,
        psy_voice: state.webgal.psyVoice,
        psy_character: state.webgal.psyCharacter,
        lang: wgCurrentLang(),
        indices: indices || undefined
      })
    });
    state.webgal.generating = true;
    state.webgal.progress = { current: 0, total: data.total || 0 };
    startWebGalPolling();
  } catch (e) {
    if (statusEl) {
      statusEl.textContent = "生成失败: " + e.message;
      statusEl.className = "status-text error";
    }
    if (genBtn) genBtn.disabled = false;
    if (cancelBtn) cancelBtn.classList.add("hidden");
  }
}

function startWebGalPolling() {
  if (webgalPollTimer) clearInterval(webgalPollTimer);
  const poll = async () => {
    try {
      const p = await api("/api/webgal/progress");
      state.webgal.generating = !!p.generating;
      state.webgal.progress = p.progress || { current: 0, total: 0 };
      state.webgal.failures = p.failures || {};
      const gen = {};
      Object.keys(p.generated || {}).forEach(k => { gen[parseInt(k, 10)] = p.generated[k]; });
      state.webgal.generated = gen;
      updateWebGalProgress();
      renderWebGalLines();
      if (!state.webgal.generating) {
        if (webgalPollTimer) { clearInterval(webgalPollTimer); webgalPollTimer = null; }
        finishWebGalGeneration();
      }
    } catch (e) {
      if (webgalPollTimer) { clearInterval(webgalPollTimer); webgalPollTimer = null; }
      state.webgal.generating = false;
      finishWebGalGeneration();
    }
  };
  poll();
  webgalPollTimer = setInterval(poll, 1500);
}

function updateWebGalProgress() {
  const el = document.getElementById("webgal-progress-text");
  const p = state.webgal.progress || { current: 0, total: 0 };
  if (el) {
    if (state.webgal.generating) {
      el.classList.remove("hidden");
      el.textContent = "生成中: " + p.current + "/" + p.total;
      el.className = "status-text";
    } else {
      el.classList.add("hidden");
    }
  }
  const failEl = document.getElementById("webgal-failures");
  if (failEl) {
    const fails = Object.values(state.webgal.failures || {});
    failEl.textContent = fails.length ? "未生成：" + fails.join("；") : "";
    failEl.className = "status-text" + (fails.length ? " error" : "");
  }
}

function finishWebGalGeneration() {
  pushWebGalHistory();
  const genBtn = document.getElementById("btn-webgal-generate");
  const cancelBtn = document.getElementById("btn-webgal-cancel");
  if (genBtn) genBtn.disabled = false;
  if (cancelBtn) cancelBtn.classList.add("hidden");
  const p = state.webgal.progress || { current: 0, total: 0 };
  const voiced = Object.keys(state.webgal.generated).length;
  const failed = Object.keys(state.webgal.failures).length;
  updateWebGalProgress();
  renderWebGalLines();
  if (p.total > 0) {
    toast("生成完成：语音 " + voiced + " 条" + (failed ? "，未生成 " + failed + " 条" : ""), failed ? "error" : "success");
  }
  refreshRecentList();
}

async function pickWebGalExportDir() {
  const statusEl = document.getElementById("webgal-export-status");
  try {
    const res = await api("/api/webgal/pick-export-dir", { method: "POST" });
    if (!res.path) return;
    state.webgal.exportDir = res.path;
    const label = document.getElementById("webgal-export-dir-label");
    if (label) label.textContent = "已选游戏音频目录：" + res.path + "（完整导出仍存程序 exports）";
    if (statusEl) { statusEl.textContent = ""; statusEl.className = "status-text"; }
  } catch (e) {
    if (statusEl) { statusEl.textContent = "选择导出位置失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

async function exportWebGal() {
  const folderName = document.getElementById("webgal-export-folder").value.trim();
  const statusEl = document.getElementById("webgal-export-status");
  if (!statusEl) return;
  if (!folderName) { statusEl.textContent = "请输入导出文件夹名称"; statusEl.className = "status-text error"; return; }
  statusEl.textContent = "正在导出...";
  statusEl.className = "status-text";
  const btn = document.getElementById("btn-webgal-export");
  if (btn) btn.disabled = true;
  try {
    const data = await api("/api/webgal/export", { method: "POST", body: JSON.stringify({ folder_name: folderName, output_dir: state.webgal.exportDir || "" }) });
    state.webgal.lastExport = data.folder;
    const openBtn = document.getElementById("btn-webgal-open-export");
    if (openBtn) openBtn.classList.remove("hidden");
    let msg = "导出完成：完整包 → " + data.folder + "，音频 " + data.voiced + " 条" + (data.unvoiced ? "，未生成 " + data.unvoiced + " 条" : "");
    if (data.audio_copy_dir) msg += "；游戏音频已另存 → " + data.audio_copy_dir;
    statusEl.textContent = msg;
    statusEl.className = "status-text success";
    toast("WebGaL 导出完成", "success");
    refreshRecentList();
  } catch (e) {
    statusEl.textContent = "导出失败: " + e.message;
    statusEl.className = "status-text error";
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function restoreWebGalProject(scriptText, lang) {
  resetWebGalProject();
  const input = document.getElementById("webgal-input");
  const langSel = document.getElementById("webgal-lang");
  if (input) input.value = scriptText || "";
  if (langSel) langSel.value = lang === "ja" ? "ja" : "zh";
  updateWebGalTranslateButton();
  state.webgal.source = scriptText || "";
  state.webgal.lang = lang === "ja" ? "ja" : "zh";
  if (!(scriptText || "").trim()) {
    showWorkbench();
    return;
  }
  try {
    const data = await api("/api/webgal/parse", { method: "POST", body: JSON.stringify({ text: scriptText, lang: state.webgal.lang }) });
    state.webgal.dialogues = data.dialogues || [];
    populateWebGalPsyCharacters();
    ["webgal-settings", "webgal-review", "webgal-export"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.classList.remove("hidden");
    });
    renderWebGalLines();
    setWebGalStatus("已恢复项目：" + data.count + " 条对话", "success");
  } catch (e) {
    setWebGalStatus("恢复项目失败: " + e.message, "error");
  }
  showWorkbench();
}

function initWebGal() {
  const parseBtn = document.getElementById("btn-webgal-parse");
  if (parseBtn) parseBtn.addEventListener("click", parseWebGal);
  const analyzeBtn = document.getElementById("btn-webgal-analyze");
  if (analyzeBtn) analyzeBtn.addEventListener("click", analyzeWebGal);
  const stopAnalyzeBtn = document.getElementById("btn-webgal-stop-analyze");
  if (stopAnalyzeBtn) stopAnalyzeBtn.addEventListener("click", stopWebGalAnalyze);
  const genBtn = document.getElementById("btn-webgal-generate");
  if (genBtn) genBtn.addEventListener("click", () => generateWebGal(null));
  const cancelBtn = document.getElementById("btn-webgal-cancel");
  if (cancelBtn) cancelBtn.addEventListener("click", async () => {
    try { await api("/api/webgal/cancel", { method: "POST" }); } catch (e) {}
    const el = document.getElementById("webgal-progress-text");
    if (el) { el.textContent = "正在取消..."; el.className = "status-text"; }
  });
  const exportBtn = document.getElementById("btn-webgal-export");
  if (exportBtn) exportBtn.addEventListener("click", exportWebGal);
  const pickDirBtn = document.getElementById("btn-webgal-pick-export-dir");
  if (pickDirBtn) pickDirBtn.addEventListener("click", pickWebGalExportDir);
  const openBtn = document.getElementById("btn-webgal-open-export");
  if (openBtn) openBtn.addEventListener("click", async () => {
    if (!state.webgal.lastExport) return;
    try {
      await api("/api/open_folder", { method: "POST", body: JSON.stringify({ path: state.webgal.lastExport }) });
    } catch (e) {
      await showAlertModal("打开失败: " + e.message);
    }
  });
  const input = document.getElementById("webgal-input");
  if (input) input.addEventListener("input", syncWebGalDraft);
  const langSel = document.getElementById("webgal-lang");
  if (langSel) langSel.addEventListener("change", () => {
    state.webgal.lang = langSel.value;
    updateWebGalTranslateButton();
    pushWebGalHistory();
  });
  const psyVoice = document.getElementById("webgal-psy-voice");
  if (psyVoice) psyVoice.addEventListener("change", () => {
    state.webgal.psyVoice = psyVoice.checked;
    renderWebGalLines();
    pushWebGalHistory();
  });
  const psyChar = document.getElementById("webgal-psy-character");
  if (psyChar) psyChar.addEventListener("change", () => {
    state.webgal.psyCharacter = psyChar.value;
    renderWebGalLines();
    pushWebGalHistory();
  });
  populateWebGalPsyCharacters();
  resetWebGalProject();
}
function initSplash() {

  const overlay = document.getElementById("splash-overlay");
  const video = document.getElementById("splash-video");
  const skip = document.getElementById("splash-skip");
  if (!overlay || !video) return;
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    overlay.classList.add("fade-out");
    setTimeout(() => overlay.classList.add("hidden"), 500);
    showProjectPicker();
  };
  if (skip) skip.addEventListener("click", close);
  video.addEventListener("ended", close);
  video.addEventListener("error", close);
  const promise = video.play();
  if (promise) promise.catch(close);
}
initSplash();
initDeployFlow();
initCleanSpace();
loadConfig();
refreshHistory();
initBackgroundSettings();
initRecentRecords();
refreshRecentList();
function updateDeployBanner(cfgPath) {
  const banner = document.getElementById("deploy-banner");
  if (!banner) return;
  const installed = localStorage.getItem("mygo_deploy_installed") === "yes";
  const path = localStorage.getItem("mygo_deploy_gs_path") || cfgPath || "";
  banner.classList.toggle("hidden", !!(installed || path));
}

const SETTINGS_TAB_KEY = "mygo_settings_tab";
let settingsReturnTo = "workbench";

function openSettingsTab(tab) {
  document.querySelectorAll(".settings-tab").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.settingsTab === tab);
  });
  document.querySelectorAll(".settings-page").forEach(page => {
    page.classList.toggle("active", page.dataset.settingsPage === tab);
  });
  if (tab) localStorage.setItem(SETTINGS_TAB_KEY, tab);
}

function showSettings(tab) {
  const workbench = document.getElementById("view-workbench");
  const webgal = document.getElementById("view-webgal");
  const settings = document.getElementById("view-settings");
  const projects = document.getElementById("view-projects");
  if (!workbench || !settings) return;
  if (settings.classList.contains("hidden")) {
    settingsReturnTo = (projects && !projects.classList.contains("hidden")) ? "projects" : "workbench";
  }
  if (projects) projects.classList.add("hidden");
  const recentDropdown = document.getElementById("recent-dropdown");
  if (recentDropdown) recentDropdown.classList.add("hidden");
  const recentBtn = document.getElementById("btn-recent");
  if (recentBtn) recentBtn.classList.add("hidden");
  workbench.classList.add("hidden");
  if (webgal) webgal.classList.add("hidden");
  settings.classList.remove("hidden");
  const backWorkbenchBtn = document.getElementById("btn-back-workbench");
  if (backWorkbenchBtn) backWorkbenchBtn.classList.toggle("hidden", settingsReturnTo !== "workbench");
  const exitHomeBtn = document.getElementById("btn-exit-home");
  if (exitHomeBtn) exitHomeBtn.classList.remove("hidden");
  if (!tab) {
    const installed = localStorage.getItem("mygo_deploy_installed") === "yes";
    const saved = localStorage.getItem(SETTINGS_TAB_KEY);
    const savedValid = saved && document.querySelector('.settings-tab[data-settings-tab="' + saved + '"]');
    tab = savedValid ? saved : (installed ? "general" : "deploy");
  }
  openSettingsTab(tab);
}

function showWorkbench() {
  const workbench = document.getElementById("view-workbench");
  const webgal = document.getElementById("view-webgal");
  const settings = document.getElementById("view-settings");
  const projects = document.getElementById("view-projects");
  if (!workbench || !settings) return;
  if (projects) projects.classList.add("hidden");
  const isWebGal = state.projectType === "webgal";
  workbench.classList.toggle("hidden", isWebGal);
  if (webgal) webgal.classList.toggle("hidden", !isWebGal);
  settings.classList.add("hidden");
  document.getElementById("btn-back-workbench").classList.add("hidden");
  ["btn-undo", "btn-redo", "btn-refresh", "btn-settings", "btn-recent", "btn-exit-home"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove("hidden");
  });
  updateAiModeUI();
  updateDeployBanner();
}

function initSettingsNav() {
  document.getElementById("btn-settings").addEventListener("click", () => {
    const settingsView = document.getElementById("view-settings");
    if (settingsView && !settingsView.classList.contains("hidden")) {
      if (settingsReturnTo === "projects") showProjectPicker(); else showWorkbench();
    } else {
      showSettings(null);
    }
  });
  document.getElementById("btn-back-workbench").addEventListener("click", () => {
    if (settingsReturnTo === "projects") showProjectPicker(); else showWorkbench();
  });
  const exitHomeBtn = document.getElementById("btn-exit-home");
  if (exitHomeBtn) exitHomeBtn.addEventListener("click", exitToHome);
  document.querySelectorAll(".settings-tab").forEach(btn => {
    btn.addEventListener("click", () => showSettings(btn.dataset.settingsTab));
  });
  const goDeploy = document.getElementById("btn-go-deploy");
  if (goDeploy) goDeploy.addEventListener("click", () => showSettings("deploy"));
  const installed = localStorage.getItem("mygo_deploy_installed") === "yes";
  const saved = localStorage.getItem(SETTINGS_TAB_KEY);
  const savedValid = saved && document.querySelector('.settings-tab[data-settings-tab="' + saved + '"]');
  openSettingsTab(savedValid ? saved : (installed ? "general" : "deploy"));
  updateDeployBanner();
}
initSettingsNav();
function fmtLogEvent(ev) {
  if (!ev || !ev.ts) return "";
  const d = new Date(ev.ts);
  const t = isNaN(d.getTime()) ? "" : d.toLocaleTimeString("zh-CN", { hour12: false });
  return "[" + t + "] " + (ev.message || ev.type || "日志");
}

async function refreshLogs() {
  const el = document.getElementById("log-view");
  if (!el) return;
  try {
    const data = await api("/api/logs");
    const lines = (data.events || []).map(fmtLogEvent);
    el.textContent = lines.length ? lines.join("\n") : "暂无日志";
    el.scrollTop = el.scrollHeight;
  } catch (e) {}
}

const logDownloadBtn = document.getElementById("btn-log-download");
if (logDownloadBtn) {
  logDownloadBtn.addEventListener("click", () => {
    window.location = "/api/logs/export";
  });
}

const logResetBtn = document.getElementById("btn-log-reset");
if (logResetBtn) {
  logResetBtn.addEventListener("click", async () => {
    if (!(await showConfirmModal("确定清空全部日志吗？"))) return;
    try {
      await api("/api/logs/reset", { method: "POST" });
      refreshLogs();
    } catch (e) {}
  });
}
refreshLogs();
setInterval(refreshLogs, 4000);

const DEEPSEEK_PRESET = { name: "DeepSeek", base_url: "https://api.deepseek.com", model: "deepseek-v4-flash" };
const AI_CONFIG_KEY = "mygo_ai_config";

function loadAIConfigFromStorage() {
  try {
    const raw = localStorage.getItem(AI_CONFIG_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function applyAIConfig(cfg) {
  document.getElementById("ai-name").value = cfg.name || "";
  document.getElementById("ai-base-url").value = cfg.base_url || "";
  document.getElementById("ai-model").value = cfg.model || "";
}

function saveAIConfigToStorage() {
  const cfg = {
    name: document.getElementById("ai-name").value.trim(),
    base_url: document.getElementById("ai-base-url").value.trim(),
    model: document.getElementById("ai-model").value.trim()
  };
  localStorage.setItem(AI_CONFIG_KEY, JSON.stringify(cfg));
  return cfg;
}

async function loadPronunciation() {
  try {
    const data = await api("/api/pronunciation");
    state.pronunciation = (data.entries || []).map(e => ({ zh: e.zh || "", ja: e.ja || "" }));
    state.pronunciationDefaults = (data.defaults || []).map(e => ({ zh: e.zh || "", ja: e.ja || "" }));
    renderPronunciation();
  } catch (e) {}
}

function renderPronunciation() {
  const list = document.getElementById("pronunciation-list");
  if (!list) return;
  list.innerHTML = state.pronunciation.map((entry, i) =>
    '<div class="pron-row">'
    + '<input type="text" class="pron-zh-input" data-index="' + i + '" value="' + esc(entry.zh) + '" placeholder="中文 / 昵称" autocomplete="off">'
    + '<input type="text" class="pron-ja-input" data-index="' + i + '" value="' + esc(entry.ja) + '" placeholder="日文发音" autocomplete="off">'
    + '<button type="button" class="btn-secondary btn-small btn-pron-del" data-index="' + i + '">删除</button>'
    + '</div>'
  ).join("");
  list.querySelectorAll(".pron-zh-input").forEach(inp => {
    inp.addEventListener("input", () => {
      const idx = parseInt(inp.dataset.index);
      state.pronunciation[idx].zh = inp.value;
    });
  });
  list.querySelectorAll(".pron-ja-input").forEach(inp => {
    inp.addEventListener("input", () => {
      const idx = parseInt(inp.dataset.index);
      state.pronunciation[idx].ja = inp.value;
    });
  });
  list.querySelectorAll(".btn-pron-del").forEach(btn => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.dataset.index);
      state.pronunciation.splice(idx, 1);
      renderPronunciation();
    });
  });
}

async function savePronunciation() {
  const status = document.getElementById("pronunciation-config-status");
  const entries = state.pronunciation.filter(e => (e.zh || "").trim() && (e.ja || "").trim())
    .map(e => ({ zh: e.zh.trim(), ja: e.ja.trim() }));
  status.textContent = "正在保存纠音词典...";
  status.className = "status-text";
  try {
    const res = await api("/api/pronunciation", { method: "PUT", body: JSON.stringify({ entries }) });
    state.pronunciation = res.entries;
    renderPronunciation();
    status.textContent = "纠音词典已保存，重新生成语音时生效";
    status.className = "status-text success";
  } catch (err) {
    status.textContent = "保存失败: " + err.message;
    status.className = "status-text error";
  }
}

function initPronunciationConfig() {
  const addBtn = document.getElementById("btn-add-pronunciation");
  const saveBtn = document.getElementById("btn-save-pronunciation");
  const resetBtn = document.getElementById("btn-reset-pronunciation");
  if (!addBtn || !saveBtn || !resetBtn) return;
  addBtn.addEventListener("click", () => {
    const zh = document.getElementById("pron-zh").value.trim();
    const ja = document.getElementById("pron-ja").value.trim();
    const status = document.getElementById("pronunciation-config-status");
    if (!zh || !ja) {
      status.textContent = "中文和日文发音都要填写";
      status.className = "status-text error";
      return;
    }
    state.pronunciation.push({ zh, ja });
    document.getElementById("pron-zh").value = "";
    document.getElementById("pron-ja").value = "";
    renderPronunciation();
    status.textContent = "已添加到列表，记得保存";
    status.className = "status-text";
  });
  saveBtn.addEventListener("click", savePronunciation);
  resetBtn.addEventListener("click", async () => {
    if (!(await showConfirmModal("恢复默认纠音词典？当前修改会被覆盖。"))) return;
    state.pronunciation = (state.pronunciationDefaults || []).map(e => ({ zh: e.zh, ja: e.ja }));
    renderPronunciation();
    await savePronunciation();
  });
}

function setNarrationMode(mode) {
  const autoBtn = document.getElementById("narration-mode-auto");
  const fixedBtn = document.getElementById("narration-mode-fixed");
  const autoFields = document.getElementById("narration-auto-fields");
  const fixedField = document.getElementById("narration-fixed-field");
  if (!autoBtn || !fixedBtn || !autoFields || !fixedField) return;
  autoBtn.classList.toggle("active", mode === "auto");
  fixedBtn.classList.toggle("active", mode === "fixed");
  autoFields.classList.toggle("hidden", mode !== "auto");
  fixedField.classList.toggle("hidden", mode !== "fixed");
}

function setNarrationInputs(nr) {
  const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
  setVal("narration-base", nr.base_duration != null ? nr.base_duration : 2.0);
  setVal("narration-per", nr.per_char != null ? nr.per_char : 0.32);
  setVal("narration-min", nr.min_duration != null ? nr.min_duration : 1.5);
  setVal("narration-max", nr.max_duration != null ? nr.max_duration : 8.0);
  setVal("narration-fixed", nr.fixed_duration != null ? nr.fixed_duration : 0.0);
  setNarrationMode((nr.fixed_duration != null && nr.fixed_duration > 0) ? "fixed" : "auto");
}

function initNarrationConfig() {
  const btn = document.getElementById("btn-save-narration");
  if (!btn) return;
  const autoModeBtn = document.getElementById("narration-mode-auto");
  const fixedModeBtn = document.getElementById("narration-mode-fixed");
  if (autoModeBtn) autoModeBtn.addEventListener("click", () => setNarrationMode("auto"));
  if (fixedModeBtn) fixedModeBtn.addEventListener("click", () => setNarrationMode("fixed"));
  btn.addEventListener("click", async () => {
    const status = document.getElementById("narration-config-status");
    const data = {
      base_duration: parseFloat(document.getElementById("narration-base").value),
      per_char: parseFloat(document.getElementById("narration-per").value),
      min_duration: parseFloat(document.getElementById("narration-min").value),
      max_duration: parseFloat(document.getElementById("narration-max").value),
      fixed_duration: document.getElementById("narration-mode-fixed").classList.contains("active")
        ? parseFloat(document.getElementById("narration-fixed").value)
        : 0
    };
    for (const k of Object.keys(data)) {
      if (isNaN(data[k]) || data[k] < 0) {
        status.textContent = "请输入不小于 0 的秒数";
        status.className = "status-text error";
        return;
      }
    }
    try {
      const res = await api("/api/settings/narration", { method: "PUT", body: JSON.stringify(data) });
      setNarrationInputs(res.narration || data);
      status.textContent = "旁白时长已保存";
      status.className = "status-text success";
    } catch (e) {
      status.textContent = "保存失败: " + e.message;
      status.className = "status-text error";
    }
  });
}

function initAIConfig() {
  const saved = loadAIConfigFromStorage();
  if (saved) applyAIConfig(saved);
  document.getElementById("btn-deepseek-preset").addEventListener("click", () => {
    applyAIConfig(DEEPSEEK_PRESET);
    const status = document.getElementById("ai-config-status");
    status.textContent = "已填入 DeepSeek 预设";
    status.className = "status-text success";
  });
  document.getElementById("btn-save-ai-config").addEventListener("click", async () => {
    saveAIConfigToStorage();
    const status = document.getElementById("ai-config-status");
    try {
      const payload = {
        deepseek_base_url: document.getElementById("ai-base-url").value.trim(),
        deepseek_model: document.getElementById("ai-model").value.trim(),
        deepseek_name: document.getElementById("ai-name").value.trim()
      };
      const apiKey = document.getElementById("api-key").value.trim();
      if (apiKey) payload.deepseek_api_key = apiKey;
      await api("/api/config", { method: "POST", body: JSON.stringify(payload) });
      status.textContent = "配置已保存";
      status.className = "status-text success";
    } catch (e) {
      status.textContent = "保存失败: " + e.message;
      status.className = "status-text error";
    }
  });
  document.getElementById("btn-clear-ai-key").addEventListener("click", async () => {
    document.getElementById("api-key").value = "";
    document.getElementById("api-key").placeholder = "sk-...";
    const saved = loadAIConfigFromStorage();
    if (saved) {
      delete saved.api_key;
      localStorage.setItem(AI_CONFIG_KEY, JSON.stringify(saved));
    }
    const status = document.getElementById("ai-config-status");
    status.textContent = "API Key 已清除";
    status.className = "status-text success";
    try {
      await api("/api/config", { method: "POST", body: JSON.stringify({ deepseek_api_key: "" }) });
    } catch (e) {}
  });
}
const BG_PREF_KEY = "mygo_bg_pref";

function applyBgImage(filename) {
  if (!filename) return;
  document.body.style.backgroundImage = "url(\"/picture/" + encodeURIComponent(filename) + "\")";
}

async function initBackgroundSettings() {
  const select = document.getElementById("bg-select");
  if (!select) return;
  let list = [];
  try {
    const data = await api("/api/backgrounds");
    list = data.backgrounds || [];
  } catch (e) {}
  list.forEach(name => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  });
  const saved = localStorage.getItem(BG_PREF_KEY) || "";
  const savedValid = saved && list.includes(saved);
  if (savedValid) {
    select.value = saved;
    applyBgImage(saved);
  } else if (list.length) {
    select.value = "";
    applyBgImage(list[Math.floor(Math.random() * list.length)]);
  }
  const status = document.getElementById("bg-status");
  select.addEventListener("change", () => {
    const val = select.value;
    localStorage.setItem(BG_PREF_KEY, val);
    if (val) {
      applyBgImage(val);
      if (status) { status.textContent = "已切换背景"; status.className = "status-text success"; }
    } else if (list.length) {
      applyBgImage(list[Math.floor(Math.random() * list.length)]);
      if (status) { status.textContent = "已切换为随机背景"; status.className = "status-text success"; }
    }
  });
  const randomBtn = document.getElementById("btn-bg-random");
  if (randomBtn) {
    randomBtn.addEventListener("click", () => {
      select.value = "";
      localStorage.setItem(BG_PREF_KEY, "");
      if (list.length) applyBgImage(list[Math.floor(Math.random() * list.length)]);
      if (status) { status.textContent = "已切换为随机背景"; status.className = "status-text success"; }
    });
  }
}

async function loadModels() {
  const listEl = document.getElementById("model-list");
  if (!listEl) return;
  try {
    const data = await api("/api/models");
    const models = data.models || [];
    listEl.innerHTML = "";
    if (!models.length) {
      listEl.innerHTML = '<div class="model-empty">暂无模型</div>';
      return;
    }
    models.forEach(m => {
      const item = document.createElement("div");
      item.className = "model-item";
      const head = document.createElement("div");
      head.className = "model-item-head";
      const nameEl = document.createElement("span");
      nameEl.className = "model-item-name";
      nameEl.textContent = m.name;
      head.appendChild(nameEl);
      item.appendChild(head);
      const files = document.createElement("div");
      files.className = "model-item-files";
      files.textContent = "GPT: " + m.gpt_file + "\nSoVITS: " + m.sovits_file + "\n参考音频: " + (m.ref_audio_dir || "-");
      item.appendChild(files);
      const aliasWrap = document.createElement("div");
      aliasWrap.className = "model-alias-wrap";
      (m.aliases || []).forEach(alias => {
        const chip = document.createElement("span");
        chip.className = "model-alias-chip";
        chip.textContent = alias;
        const del = document.createElement("button");
        del.type = "button";
        del.className = "model-alias-del";
        del.textContent = "×";
        del.title = "删除激活词";
        del.addEventListener("click", () => deleteModelAlias(m.key, alias));
        chip.appendChild(del);
        aliasWrap.appendChild(chip);
      });
      item.appendChild(aliasWrap);
      const addRow = document.createElement("div");
      addRow.className = "model-alias-add";
      const input = document.createElement("input");
      input.type = "text";
      input.placeholder = "新增激活词，如：爱音";
      input.autocomplete = "off";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn-secondary btn-small";
      btn.textContent = "添加";
      btn.addEventListener("click", () => addModelAlias(m.key, input, btn));
      input.addEventListener("keydown", e => { if (e.key === "Enter") addModelAlias(m.key, input, btn); });
      addRow.appendChild(input);
      addRow.appendChild(btn);
      item.appendChild(addRow);
      listEl.appendChild(item);
    });
  } catch (e) {
    listEl.innerHTML = '<div class="model-empty">加载失败: ' + e.message + '</div>';
  }
}

async function addModelAlias(key, input, btn) {
  const alias = input.value.trim();
  if (!alias) { input.focus(); return; }
  btn.disabled = true;
  try {
    await api("/api/models/" + encodeURIComponent(key) + "/aliases", { method: "POST", body: JSON.stringify({ alias }) });
    input.value = "";
    await loadModels();
    await loadConfig();
  } catch (e) {
    await showAlertModal("添加激活词失败: " + e.message);
  } finally {
    btn.disabled = false;
  }
}

async function deleteModelAlias(key, alias) {
  if (!(await showConfirmModal("确定删除激活词“" + alias + "”吗？"))) return;
  try {
    await api("/api/models/" + encodeURIComponent(key) + "/aliases/" + encodeURIComponent(alias), { method: "DELETE" });
    await loadModels();
    await loadConfig();
  } catch (e) {
    await showAlertModal("删除激活词失败: " + e.message);
  }
}

let refRootPath = "";
let refAudioPlayer = null;
let refPromptState = null;

function refAudioUrl(key, emotion, name) {
  return "/api/reference/audio/file?key=" + encodeURIComponent(key) + "&emotion=" + encodeURIComponent(emotion) + "&name=" + encodeURIComponent(name);
}

async function loadReferenceLibrary() {
  const listEl = document.getElementById("ref-library-list");
  const statusEl = document.getElementById("ref-library-status");
  if (!listEl) return;
  listEl.innerHTML = '<div class="model-empty">加载中...</div>';
  try {
    const data = await api("/api/reference/audio");
    refRootPath = data.root || "";
    const rootEl = document.getElementById("ref-root-path");
    if (rootEl) rootEl.textContent = refRootPath || "reference_audio/";
    renderReferenceLibrary(data.items || []);
    if (statusEl) { statusEl.textContent = ""; statusEl.className = "status-text"; }
  } catch (e) {
    listEl.innerHTML = '<div class="model-empty">加载失败: ' + esc(e.message) + '</div>';
    if (statusEl) { statusEl.textContent = "加载失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

function renderReferenceLibrary(items) {
  const listEl = document.getElementById("ref-library-list");
  if (!listEl) return;
  if (!items.length) {
    listEl.innerHTML = '<div class="model-empty">暂无角色模型，请先配置模型</div>';
    return;
  }
  listEl.innerHTML = items.map(item => {
    const emotionsHtml = (item.emotions || []).map(em => {
      const files = em.files || [];
      const filesHtml = files.length ? files.map(f => (
        '<div class="ref-file">'
        + '<span class="ref-file-name" title="' + esc(f.name) + '">' + esc(f.name) + ' <em>' + fmtSize(f.size || 0) + '</em></span>'
        + '<div class="ref-file-actions">'
        + '<button type="button" class="btn-line-action btn-ref-prompt" data-key="' + esc(item.key) + '" data-emotion="' + esc(em.emotion) + '" data-name="' + esc(f.name) + '" data-prompt="' + esc(f.prompt_text || "") + '">字幕</button>'
        + '<button type="button" class="btn-line-action btn-ref-play" data-key="' + esc(item.key) + '" data-emotion="' + esc(em.emotion) + '" data-name="' + esc(f.name) + '">试听</button>'
        + '<button type="button" class="btn-line-action btn-ref-del" data-key="' + esc(item.key) + '" data-emotion="' + esc(em.emotion) + '" data-name="' + esc(f.name) + '">删除</button>'
        + '</div>'
        + '<span class="ref-file-prompt' + (f.prompt_text ? "" : " empty") + '" title="' + esc(f.prompt_text || "") + '">' + esc(f.prompt_text || "未填字幕") + '</span>'
        + '</div>'
      )).join("") : '<span class="ref-empty">暂无音频</span>';
      return '<div class="ref-emotion">'
        + '<div class="ref-emotion-head">'
        + '<span class="ref-emotion-name">' + esc(em.emotion) + '</span>'
        + '<span class="ref-emotion-status ' + (files.length ? "ok" : "empty") + '">' + (files.length ? files.length + " 个音频" : "缺失") + '</span>'
        + '</div>'
        + '<div class="ref-files">' + filesHtml + '</div>'
        + '<div class="ref-upload-row">'
        + '<button type="button" class="btn-secondary btn-small btn-ref-upload" data-key="' + esc(item.key) + '" data-emotion="' + esc(em.emotion) + '">上传音频</button>'
        + '</div>'
        + '</div>';
    }).join("");
    return '<div class="ref-char">'
      + '<div class="ref-char-head">'
      + '<span class="ref-char-name">' + esc(item.name) + '</span>'
      + '<span class="ref-char-dir">reference_audio/' + esc(item.ref_dir || "") + '</span>'
      + '</div>'
      + '<div class="ref-emotions">' + emotionsHtml + '</div>'
      + '</div>';
  }).join("");
  listEl.querySelectorAll(".btn-ref-play").forEach(btn => {
    btn.addEventListener("click", () => playRefAudio(btn.dataset.key, btn.dataset.emotion, btn.dataset.name));
  });
  listEl.querySelectorAll(".btn-ref-del").forEach(btn => {
    btn.addEventListener("click", () => deleteRefAudio(btn.dataset.key, btn.dataset.emotion, btn.dataset.name));
  });
  listEl.querySelectorAll(".btn-ref-upload").forEach(btn => {
    btn.addEventListener("click", () => openRefUploadModal(btn.dataset.key, btn.dataset.emotion));
  });
  listEl.querySelectorAll(".btn-ref-prompt").forEach(btn => {
    btn.addEventListener("click", () => openRefPromptEditor(btn.dataset.key, btn.dataset.emotion, btn.dataset.name, btn.dataset.prompt || ""));
  });
}

function playRefAudio(key, emotion, name) {
  if (refAudioPlayer) { refAudioPlayer.pause(); refAudioPlayer = null; }
  const a = new Audio(refAudioUrl(key, emotion, name));
  refAudioPlayer = a;
  a.play().catch(() => {});
}

function openRefUploadModal(key, emotion) {
  const modal = document.getElementById("ref-prompt-modal");
  if (!modal) return;
  refPromptState = { mode: "upload", key, emotion };
  document.getElementById("ref-prompt-title").textContent = "上传参考音频";
  document.getElementById("ref-prompt-desc").textContent = key + "「" + emotion + "」：选择音频文件，并填写该音频对应的字幕文本。";
  const fileRow = document.getElementById("ref-prompt-file-row");
  if (fileRow) fileRow.classList.remove("hidden");
  const fileInput = document.getElementById("ref-prompt-file");
  if (fileInput) fileInput.value = "";
  const fileNameEl = document.getElementById("ref-prompt-file-name");
  if (fileNameEl) fileNameEl.textContent = "未选择文件";
  const textEl = document.getElementById("ref-prompt-text");
  if (textEl) textEl.value = "";
  modal.classList.remove("hidden");
}

function openRefPromptEditor(key, emotion, name, promptText) {
  const modal = document.getElementById("ref-prompt-modal");
  if (!modal) return;
  refPromptState = { mode: "edit", key, emotion, name };
  document.getElementById("ref-prompt-title").textContent = "编辑音频字幕";
  document.getElementById("ref-prompt-desc").textContent = name + "：填写该音频对应的字幕文本。";
  const fileRow = document.getElementById("ref-prompt-file-row");
  if (fileRow) fileRow.classList.add("hidden");
  const textEl = document.getElementById("ref-prompt-text");
  if (textEl) textEl.value = promptText || "";
  modal.classList.remove("hidden");
}

function closeRefPromptModal() {
  const modal = document.getElementById("ref-prompt-modal");
  if (modal) modal.classList.add("hidden");
  refPromptState = null;
}

async function confirmRefPrompt() {
  const st = refPromptState;
  if (!st) return;
  const statusEl = document.getElementById("ref-library-status");
  const textEl = document.getElementById("ref-prompt-text");
  const promptText = textEl ? textEl.value.trim() : "";
  if (st.mode === "upload") {
    const fileInput = document.getElementById("ref-prompt-file");
    const file = fileInput && fileInput.files && fileInput.files[0];
    if (!file) {
      if (statusEl) { statusEl.textContent = "请先选择音频文件"; statusEl.className = "status-text error"; }
      return;
    }
    const form = new FormData();
    form.append("key", st.key);
    form.append("emotion", st.emotion);
    form.append("prompt_text", promptText);
    form.append("file", file);
    if (statusEl) { statusEl.textContent = "正在上传 " + file.name + "..."; statusEl.className = "status-text"; }
    try {
      const res = await fetch("/api/reference/audio/upload", { method: "POST", body: form });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || ("HTTP " + res.status));
      }
      if (statusEl) { statusEl.textContent = "已上传到 " + st.key + "「" + st.emotion + "」"; statusEl.className = "status-text success"; }
      closeRefPromptModal();
      await loadReferenceLibrary();
    } catch (e) {
      if (statusEl) { statusEl.textContent = "上传失败: " + e.message; statusEl.className = "status-text error"; }
    }
    return;
  }
  if (statusEl) { statusEl.textContent = "正在保存字幕..."; statusEl.className = "status-text"; }
  try {
    await api("/api/reference/audio/prompt", {
      method: "POST",
      body: JSON.stringify({ key: st.key, emotion: st.emotion, name: st.name, prompt_text: promptText }),
    });
    if (statusEl) { statusEl.textContent = "字幕已保存"; statusEl.className = "status-text success"; }
    closeRefPromptModal();
    await loadReferenceLibrary();
  } catch (e) {
    if (statusEl) { statusEl.textContent = "保存字幕失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

async function deleteRefAudio(key, emotion, name) {
  if (!(await showConfirmModal("确定删除参考音频“" + name + "”吗？"))) return;
  const statusEl = document.getElementById("ref-library-status");
  try {
    await api("/api/reference/audio", { method: "DELETE", body: JSON.stringify({ key, emotion, name }) });
    if (statusEl) { statusEl.textContent = "已删除 " + name; statusEl.className = "status-text success"; }
    await loadReferenceLibrary();
  } catch (e) {
    if (statusEl) { statusEl.textContent = "删除失败: " + e.message; statusEl.className = "status-text error"; }
  }
}


function initEmotions() {
  const addBtn = document.getElementById("btn-add-emotion");
  if (addBtn) addBtn.addEventListener("click", addEmotion);
  const resetBtn = document.getElementById("btn-reset-emotions");
  if (resetBtn) resetBtn.addEventListener("click", resetEmotions);
  const input = document.getElementById("emotion-name");
  if (input) input.addEventListener("keydown", (e) => { if (e.key === "Enter") addEmotion(); });
  loadEmotions();
}

async function loadEmotions() {
  const listEl = document.getElementById("emotions-list");
  const statusEl = document.getElementById("emotions-status");
  if (!listEl) return;
  listEl.innerHTML = '<div class="model-empty">加载中...</div>';
  try {
    const data = await api("/api/emotions");
    state.emotions = data.emotions || [];
    renderEmotions(state.emotions);
    if (statusEl) { statusEl.textContent = ""; statusEl.className = "status-text"; }
  } catch (e) {
    listEl.innerHTML = '<div class="model-empty">加载失败: ' + esc(e.message) + '</div>';
    if (statusEl) { statusEl.textContent = "加载失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

function renderEmotions(list) {
  const listEl = document.getElementById("emotions-list");
  if (!listEl) return;
  if (!list.length) {
    listEl.innerHTML = '<div class="model-empty">暂无情绪</div>';
    return;
  }
  listEl.innerHTML = list.map(e => (
    '<span class="emotion-chip">' + esc(e)
    + '<button type="button" class="emotion-chip-del" data-name="' + esc(e) + '" title="删除">×</button>'
    + '</span>'
  )).join("");
  listEl.querySelectorAll(".emotion-chip-del").forEach(btn => {
    btn.addEventListener("click", () => deleteEmotion(btn.dataset.name));
  });
}

async function addEmotion() {
  const input = document.getElementById("emotion-name");
  const statusEl = document.getElementById("emotions-status");
  const name = input ? input.value.trim() : "";
  if (!name) {
    if (statusEl) { statusEl.textContent = "请输入情绪名"; statusEl.className = "status-text error"; }
    return;
  }
  if (statusEl) { statusEl.textContent = "正在添加..."; statusEl.className = "status-text"; }
  try {
    const res = await api("/api/emotions", { method: "POST", body: JSON.stringify({ action: "add", name }) });
    if (input) input.value = "";
    state.emotions = res.emotions || [];
    renderEmotions(state.emotions);
    if (statusEl) { statusEl.textContent = "已添加情绪"; statusEl.className = "status-text success"; }
    loadReferenceLibrary();
    renderLines();
    loadEmotionParams();
  } catch (e) {
    if (statusEl) { statusEl.textContent = "添加失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

async function deleteEmotion(name) {
  if (!(await showConfirmModal("确定删除情绪“" + name + "”吗？已有参考音频文件夹会保留。"))) return;
  const statusEl = document.getElementById("emotions-status");
  if (statusEl) { statusEl.textContent = "正在删除..."; statusEl.className = "status-text"; }
  try {
    const res = await api("/api/emotions", { method: "POST", body: JSON.stringify({ action: "delete", name }) });
    state.emotions = res.emotions || [];
    renderEmotions(state.emotions);
    if (statusEl) { statusEl.textContent = "已删除情绪"; statusEl.className = "status-text success"; }
    loadReferenceLibrary();
    renderLines();
    loadEmotionParams();
  } catch (e) {
    if (statusEl) { statusEl.textContent = "删除失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

async function resetEmotions() {
  if (!(await showConfirmModal("确定恢复系统默认情绪吗？自定义情绪会被移除，对应文件夹保留。"))) return;
  const statusEl = document.getElementById("emotions-status");
  if (statusEl) { statusEl.textContent = "正在重置..."; statusEl.className = "status-text"; }
  try {
    const res = await api("/api/emotions/reset", { method: "POST" });
    state.emotions = res.emotions || [];
    renderEmotions(state.emotions);
    if (statusEl) { statusEl.textContent = "已恢复系统默认"; statusEl.className = "status-text success"; }
    loadReferenceLibrary();
    renderLines();
    loadEmotionParams();
  } catch (e) {
    if (statusEl) { statusEl.textContent = "重置失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

let webgalMap = {};
let webgalMapDefaults = {};
let webgalRetranslateOnAnalyze = true;

function initWebgalMap() {
  const addBtn = document.getElementById("btn-add-webgal-map");
  if (addBtn) addBtn.addEventListener("click", addWebgalMapRow);
  const saveBtn = document.getElementById("btn-save-webgal-map");
  if (saveBtn) saveBtn.addEventListener("click", saveWebgalMap);
  const resetBtn = document.getElementById("btn-reset-webgal-map");
  if (resetBtn) resetBtn.addEventListener("click", resetWebgalMap);
  const retranslateBtn = document.getElementById("btn-save-webgal-retranslate");
  if (retranslateBtn) retranslateBtn.addEventListener("click", saveWebgalRetranslate);
}

function webgalMapTargetOptions(selected) {
  return (state.emotions || []).map(e => '<option value="' + esc(e) + '"' + (e === selected ? " selected" : "") + '>' + esc(e) + '</option>').join("");
}

function renderWebgalMap() {
  const listEl = document.getElementById("webgal-map-list");
  if (!listEl) return;
  const keys = Object.keys(webgalMap || {});
  if (!keys.length) {
    listEl.innerHTML = '<div class="model-empty">暂无映射，添加脚本表情词后保存</div>';
    return;
  }
  listEl.innerHTML = keys.map(k => (
    '<div class="webgal-map-row">'
    + '<span class="webgal-map-key">' + esc(k) + '</span>'
    + '<select class="webgal-map-target" data-key="' + esc(k) + '">' + webgalMapTargetOptions(webgalMap[k]) + '</select>'
    + '<button type="button" class="webgal-map-del" data-key="' + esc(k) + '" title="删除">×</button>'
    + '</div>'
  )).join("");
  listEl.querySelectorAll(".webgal-map-del").forEach(btn => {
    btn.addEventListener("click", () => { delete webgalMap[btn.dataset.key]; renderWebgalMap(); });
  });
}

function addWebgalMapRow() {
  const keyInput = document.getElementById("webgal-map-key");
  const targetSel = document.getElementById("webgal-map-target");
  const statusEl = document.getElementById("webgal-map-status");
  const key = keyInput ? keyInput.value.trim() : "";
  const target = targetSel ? targetSel.value : "";
  if (!key || !target) {
    if (statusEl) { statusEl.textContent = "请填写脚本表情词并选择目标情绪"; statusEl.className = "status-text error"; }
    return;
  }
  webgalMap[key.toLowerCase()] = target;
  if (keyInput) keyInput.value = "";
  renderWebgalMap();
  if (statusEl) { statusEl.textContent = "已添加，记得保存"; statusEl.className = "status-text"; }
}

function loadWebgalMap(cfg) {
  webgalMap = cfg.webgal_emotion_map || {};
  webgalMapDefaults = cfg.webgal_emotion_defaults || {};
  const targetSel = document.getElementById("webgal-map-target");
  if (targetSel) targetSel.innerHTML = webgalMapTargetOptions((state.emotions && state.emotions[0]) || "");
  renderWebgalMap();
}

function loadWebgalRetranslate(cfg) {
  webgalRetranslateOnAnalyze = cfg.webgal_retranslate_on_analyze !== false;
  const el = document.getElementById("webgal-retranslate");
  if (el) el.checked = !!webgalRetranslateOnAnalyze;
}

async function saveWebgalRetranslate() {
  const el = document.getElementById("webgal-retranslate");
  const statusEl = document.getElementById("webgal-retranslate-status");
  const val = el ? el.checked : true;
  if (statusEl) { statusEl.textContent = "正在保存..."; statusEl.className = "status-text"; }
  try {
    const res = await api("/api/webgal/settings", { method: "POST", body: JSON.stringify({ retranslate_on_analyze: val }) });
    webgalRetranslateOnAnalyze = res.retranslate_on_analyze !== false;
    if (el) el.checked = !!webgalRetranslateOnAnalyze;
    if (statusEl) { statusEl.textContent = "已保存 WebGaL 翻译设置"; statusEl.className = "status-text success"; }
  } catch (e) {
    if (statusEl) { statusEl.textContent = "保存失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

async function saveWebgalMap() {
  const statusEl = document.getElementById("webgal-map-status");
  if (statusEl) { statusEl.textContent = "正在保存..."; statusEl.className = "status-text"; }
  try {
    const res = await api("/api/webgal/emotion_map", { method: "POST", body: JSON.stringify({ map: webgalMap }) });
    webgalMap = res.map || webgalMap;
    renderWebgalMap();
    if (statusEl) { statusEl.textContent = "已保存 WebGaL 情绪映射"; statusEl.className = "status-text success"; }
  } catch (e) {
    if (statusEl) { statusEl.textContent = "保存失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

async function resetWebgalMap() {
  if (!(await showConfirmModal("确定恢复默认的 WebGaL 情绪映射吗？自定义映射会被清除。"))) return;
  const statusEl = document.getElementById("webgal-map-status");
  if (statusEl) { statusEl.textContent = "正在恢复..."; statusEl.className = "status-text"; }
  try {
    const res = await api("/api/webgal/emotion_map/reset", { method: "POST" });
    webgalMap = res.map || {};
    renderWebgalMap();
    if (statusEl) { statusEl.textContent = "已恢复默认映射"; statusEl.className = "status-text success"; }
  } catch (e) {
    if (statusEl) { statusEl.textContent = "重置失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

let emotionParams = {};
let emotionParamsEnabled = true;
let emotionPresets = [];

function initEmotionParams() {
  const suggestBtn = document.getElementById("btn-suggest-params");
  if (suggestBtn) suggestBtn.addEventListener("click", suggestEmotionParams);
  const saveBtn = document.getElementById("btn-save-params");
  if (saveBtn) saveBtn.addEventListener("click", saveEmotionParams);
  const resetBtn = document.getElementById("btn-reset-params");
  if (resetBtn) resetBtn.addEventListener("click", resetEmotionParams);
  const toggleEl = document.getElementById("cb-enable-emotion-params");
  if (toggleEl) toggleEl.addEventListener("change", toggleEmotionParamsEnabled);
  const savePresetBtn = document.getElementById("btn-save-emotion-preset");
  if (savePresetBtn) savePresetBtn.addEventListener("click", saveEmotionPreset);
  const loadPresetBtn = document.getElementById("btn-load-emotion-preset");
  if (loadPresetBtn) loadPresetBtn.addEventListener("click", loadEmotionPreset);
  const deletePresetBtn = document.getElementById("btn-delete-emotion-preset");
  if (deletePresetBtn) deletePresetBtn.addEventListener("click", deleteEmotionPreset);
  loadEmotionParams();
}

async function loadEmotionParams() {
  const body = document.getElementById("emotion-params-body");
  if (!body) return;
  try {
    const [pRes, eRes] = await Promise.all([
      api("/api/emotion_params"),
      api("/api/emotions"),
    ]);
    emotionParams = pRes.params || {};
    emotionParamsEnabled = pRes.enabled !== false;
    const toggleEl = document.getElementById("cb-enable-emotion-params");
    if (toggleEl) toggleEl.checked = emotionParamsEnabled;
    state.emotions = eRes.emotions || [];
    renderEmotionParams();
    loadEmotionPresets();
  } catch (e) {
    body.innerHTML = '<tr><td colspan="6" class="model-empty">加载失败: ' + esc(e.message) + '</td></tr>';
  }
}

async function toggleEmotionParamsEnabled() {
  const toggleEl = document.getElementById("cb-enable-emotion-params");
  const enabled = toggleEl ? toggleEl.checked : true;
  emotionParamsEnabled = enabled;
  const statusEl = document.getElementById("emotion-params-status");
  try {
    await api("/api/emotion_params", { method: "POST", body: JSON.stringify({ enabled }) });
    if (statusEl) { statusEl.textContent = enabled ? "已启用 AI 情绪参数" : "已停用 AI 情绪参数（生成时使用 SoVITS 默认参数）"; statusEl.className = "status-text success"; }
  } catch (e) {
    if (toggleEl) toggleEl.checked = !enabled;
    emotionParamsEnabled = !enabled;
    if (statusEl) { statusEl.textContent = "设置失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

function renderEmotionParams() {
  const body = document.getElementById("emotion-params-body");
  if (!body) return;
  const list = state.emotions || [];
  if (!list.length) {
    body.innerHTML = '<tr><td colspan="6" class="model-empty">暂无情绪</td></tr>';
    return;
  }
  body.innerHTML = list.map(e => {
    const p = emotionParams[e] || {};
    const f = (key) => (p[key] !== undefined && p[key] !== null && p[key] !== "" ? p[key] : "");
    return '<tr>'
      + '<td class="param-name">' + esc(e) + '</td>'
      + '<td><input type="number" step="0.1" min="0.1" max="1.5" data-emotion="' + esc(e) + '" data-key="temperature" value="' + esc(f("temperature")) + '"></td>'
      + '<td><input type="number" step="1" min="1" max="50" data-emotion="' + esc(e) + '" data-key="top_k" value="' + esc(f("top_k")) + '"></td>'
      + '<td><input type="number" step="0.05" min="0.1" max="1" data-emotion="' + esc(e) + '" data-key="top_p" value="' + esc(f("top_p")) + '"></td>'
      + '<td><input type="number" step="0.05" min="0.5" max="1.5" data-emotion="' + esc(e) + '" data-key="speed_factor" value="' + esc(f("speed_factor")) + '"></td>'
      + '<td><input type="number" step="1" data-emotion="' + esc(e) + '" data-key="seed" value="' + esc(f("seed")) + '"></td>'
      + '</tr>';
  }).join("");
}

function collectEmotionParams() {
  const params = {};
  document.querySelectorAll("#emotion-params-body tr").forEach(row => {
    const inputs = row.querySelectorAll("input[data-emotion]");
    if (!inputs.length) return;
    const name = inputs[0].dataset.emotion;
    const p = {};
    inputs.forEach(inp => {
      const v = inp.value.trim();
      p[inp.dataset.key] = v === "" ? "" : Number(v);
    });
    params[name] = p;
  });
  return params;
}

async function suggestEmotionParams() {
  const statusEl = document.getElementById("emotion-params-status");
  if (statusEl) { statusEl.textContent = "AI 正在生成参数建议..."; statusEl.className = "status-text"; }
  try {
    const scriptLines = (state.lines || []).map(l => ({ index: l.index, character: l.character, emotion: l.emotion, text: l.text }));
    const res = await api("/api/emotion_params/suggest", { method: "POST", body: JSON.stringify({ lines: scriptLines }) });
    const params = res.params || {};
    for (const name of Object.keys(params)) {
      emotionParams[name] = { ...(emotionParams[name] || {}), ...params[name] };
    }
    renderEmotionParams();
    const saveRes = await api("/api/emotion_params", { method: "POST", body: JSON.stringify({ params: collectEmotionParams(), enabled: emotionParamsEnabled }) });
    emotionParams = saveRes.params || {};
    emotionParamsEnabled = saveRes.enabled !== false;
    const toggleEl = document.getElementById("cb-enable-emotion-params");
    if (toggleEl) toggleEl.checked = emotionParamsEnabled;
    renderEmotionParams();
    if (statusEl) { statusEl.textContent = "已填入并应用 AI 情绪参数建议"; statusEl.className = "status-text success"; }
  } catch (e) {
    if (statusEl) { statusEl.textContent = "生成建议失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

async function saveEmotionParams() {
  const statusEl = document.getElementById("emotion-params-status");
  if (statusEl) { statusEl.textContent = "正在保存..."; statusEl.className = "status-text"; }
  try {
    const res = await api("/api/emotion_params", { method: "POST", body: JSON.stringify({ params: collectEmotionParams(), enabled: emotionParamsEnabled }) });
    emotionParams = res.params || {};
    emotionParamsEnabled = res.enabled !== false;
    const toggleEl = document.getElementById("cb-enable-emotion-params");
    if (toggleEl) toggleEl.checked = emotionParamsEnabled;
    renderEmotionParams();
    if (statusEl) { statusEl.textContent = "已保存情绪参数模板"; statusEl.className = "status-text success"; }
  } catch (e) {
    if (statusEl) { statusEl.textContent = "保存失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

async function resetEmotionParams() {
  if (!(await showConfirmModal("确定清空情绪参数模板吗？生成时将使用 SoVITS 默认参数。"))) return;
  const statusEl = document.getElementById("emotion-params-status");
  if (statusEl) { statusEl.textContent = "正在清空..."; statusEl.className = "status-text"; }
  try {
    const res = await api("/api/emotion_params", { method: "POST", body: JSON.stringify({ params: {}, enabled: emotionParamsEnabled }) });
    emotionParams = res.params || {};
    emotionParamsEnabled = res.enabled !== false;
    const toggleEl = document.getElementById("cb-enable-emotion-params");
    if (toggleEl) toggleEl.checked = emotionParamsEnabled;
    renderEmotionParams();
    if (statusEl) { statusEl.textContent = "已清空模板"; statusEl.className = "status-text success"; }
  } catch (e) {
    if (statusEl) { statusEl.textContent = "清空失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

function renderEmotionPresets() {
  const sel = document.getElementById("emotion-preset-select");
  if (!sel) return;
  sel.innerHTML = emotionPresets.map(p => '<option value="' + esc(p.name) + '">' + esc(p.name) + '</option>').join("");
}

async function loadEmotionPresets() {
  try {
    const res = await api("/api/emotion_params/presets");
    emotionPresets = res.presets || [];
    renderEmotionPresets();
  } catch (e) {}
}

async function saveEmotionPreset() {
  const nameEl = document.getElementById("emotion-preset-name");
  const statusEl = document.getElementById("emotion-presets-status");
  const name = nameEl ? nameEl.value.trim() : "";
  if (!name) { if (statusEl) { statusEl.textContent = "请输入预设名称"; statusEl.className = "status-text error"; } return; }
  try {
    await api("/api/emotion_params/presets", { method: "POST", body: JSON.stringify({ action: "save", name, params: collectEmotionParams(), enabled: emotionParamsEnabled }) });
    if (nameEl) nameEl.value = "";
    await loadEmotionPresets();
    if (statusEl) { statusEl.textContent = "已保存预设"; statusEl.className = "status-text success"; }
  } catch (e) {
    if (statusEl) { statusEl.textContent = "保存失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

async function loadEmotionPreset() {
  const sel = document.getElementById("emotion-preset-select");
  const statusEl = document.getElementById("emotion-presets-status");
  const name = sel ? sel.value : "";
  if (!name) { if (statusEl) { statusEl.textContent = "请先选择一个预设"; statusEl.className = "status-text error"; } return; }
  try {
    const res = await api("/api/emotion_params/presets", { method: "POST", body: JSON.stringify({ action: "load", name }) });
    emotionParams = res.params || {};
    emotionParamsEnabled = res.enabled !== false;
    const toggleEl = document.getElementById("cb-enable-emotion-params");
    if (toggleEl) toggleEl.checked = emotionParamsEnabled;
    renderEmotionParams();
    if (statusEl) { statusEl.textContent = "已载入预设"; statusEl.className = "status-text success"; }
  } catch (e) {
    if (statusEl) { statusEl.textContent = "载入失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

async function deleteEmotionPreset() {
  const sel = document.getElementById("emotion-preset-select");
  const statusEl = document.getElementById("emotion-presets-status");
  const name = sel ? sel.value : "";
  if (!name) { if (statusEl) { statusEl.textContent = "请先选择一个预设"; statusEl.className = "status-text error"; } return; }
  if (!(await showConfirmModal("确定删除预设「" + name + "」吗？"))) return;
  try {
    await api("/api/emotion_params/presets", { method: "POST", body: JSON.stringify({ action: "delete", name }) });
    await loadEmotionPresets();
    if (statusEl) { statusEl.textContent = "已删除预设"; statusEl.className = "status-text success"; }
  } catch (e) {
    if (statusEl) { statusEl.textContent = "删除失败: " + e.message; statusEl.className = "status-text error"; }
  }
}

function initReferenceLibrary() {
  const openBtn = document.getElementById("btn-ref-open-root");
  if (openBtn) {
    openBtn.addEventListener("click", async () => {
      if (!refRootPath) await loadReferenceLibrary();
      if (!refRootPath) return;
      try {
        await api("/api/open_folder", { method: "POST", body: JSON.stringify({ path: refRootPath }) });
      } catch (e) {
        await showAlertModal("打开失败: " + e.message);
      }
    });
  }
  const promptFileInput = document.getElementById("ref-prompt-file");
  const promptFileName = document.getElementById("ref-prompt-file-name");
  if (promptFileInput && promptFileName) {
    promptFileInput.addEventListener("change", () => {
      promptFileName.textContent = promptFileInput.files && promptFileInput.files[0] ? promptFileInput.files[0].name : "未选择文件";
    });
  }
  const promptOk = document.getElementById("btn-ref-prompt-ok");
  if (promptOk) promptOk.addEventListener("click", confirmRefPrompt);
  const promptCancel = document.getElementById("btn-ref-prompt-cancel");
  if (promptCancel) promptCancel.addEventListener("click", closeRefPromptModal);
  const promptModal = document.getElementById("ref-prompt-modal");
  if (promptModal) promptModal.addEventListener("click", (e) => { if (e.target === promptModal) closeRefPromptModal(); });
  loadReferenceLibrary();
}

function initModelConfig() {
  loadModels();
}

function initShareImportExport() {
  const fileInput = document.getElementById("share-import-file");
  if (!fileInput) return;
  let pendingKind = null;
  const statusFor = () => document.getElementById("share-status");
  const triggerImport = kind => {
    pendingKind = kind;
    fileInput.value = "";
    fileInput.click();
  };
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files && fileInput.files[0];
    const kind = pendingKind;
    pendingKind = null;
    if (!file || !kind) return;
    const statusEl = statusFor(kind);
    if (kind === "audio" && !(await showConfirmModal("导入参考音频包会合并到本地语音库，同名文件将被覆盖。确定继续吗？"))) {
      fileInput.value = "";
      return;
    }
    const form = new FormData();
    form.append("file", file);
    if (statusEl) { statusEl.textContent = "正在导入 " + file.name + "..."; statusEl.className = "status-text"; }
    try {
      const res = await fetch("/api/share/import", { method: "POST", body: form });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || ("HTTP " + res.status));
      }
      if (statusEl) { statusEl.textContent = res.message || "导入成功"; statusEl.className = "status-text success"; }
      if (kind === "pronunciation") {
        await loadPronunciation();
      } else if (kind === "emotion_params") {
        await loadEmotionParams();
      } else if (kind === "webgal_map") {
        await loadEmotions();
        const cfg = await api("/api/config");
        loadWebgalMap(cfg);
      } else if (kind === "audio") {
        await Promise.all([loadReferenceLibrary(), loadEmotions(), loadEmotionParams()]);
        const cfg = await api("/api/config");
        loadWebgalMap(cfg);
      }
    } catch (e) {
      if (statusEl) { statusEl.textContent = "导入失败: " + e.message; statusEl.className = "status-text error"; }
    }
    fileInput.value = "";
  });
  let lastShareExportPath = "";
  const openExportBtn = document.getElementById("btn-share-open-export");
  if (openExportBtn) {
    openExportBtn.addEventListener("click", async () => {
      if (!lastShareExportPath) return;
      try {
        await api("/api/open_folder", { method: "POST", body: JSON.stringify({ path: lastShareExportPath }) });
      } catch (e) {
        await showAlertModal("打开失败: " + e.message);
      }
    });
  }
  const exports = [
    ["btn-export-pronunciation", "pronunciation", "share-status"],
    ["btn-export-emotion-params", "emotion_params", "share-status"],
    ["btn-export-webgal-map", "webgal_map", "share-status"],
    ["btn-export-ref-audio", "audio", "share-status"]
  ];
  exports.forEach(item => {
    const btn = document.getElementById(item[0]);
    if (!btn) return;
    btn.addEventListener("click", async () => {
      const statusEl = document.getElementById(item[2]);
      if (item[1] === "audio" && !(await showConfirmModal("导出全部参考音频、字幕与情绪列表为 ZIP 压缩包？文件较大时可能需要等待。"))) return;
      if (statusEl) { statusEl.textContent = "正在准备导出，请在弹出窗口中选择保存位置..."; statusEl.className = "status-text"; }
      try {
        const timeout = item[1] === "audio" ? 600000 : 180000;
        const res = await api("/api/share/export", { method: "POST", timeout, body: JSON.stringify({ type: item[1] }) });
        if (res.status === "cancelled") {
          if (statusEl) { statusEl.textContent = "已取消导出"; statusEl.className = "status-text"; }
          return;
        }
        lastShareExportPath = res.dir || "";
        if (statusEl) { statusEl.textContent = "导出完成：" + (res.path || ""); statusEl.className = "status-text success"; }
        if (openExportBtn) openExportBtn.classList.remove("hidden");
      } catch (e) {
        if (statusEl) { statusEl.textContent = "导出失败: " + e.message; statusEl.className = "status-text error"; }
      }
    });
  });
  const imports = [
    ["btn-import-pronunciation", "pronunciation"],
    ["btn-import-emotion-params", "emotion_params"],
    ["btn-import-webgal-map", "webgal_map"],
    ["btn-import-ref-audio", "audio"]
  ];
  imports.forEach(item => {
    const btn = document.getElementById(item[0]);
    if (btn) btn.addEventListener("click", () => triggerImport(item[1]));
  });
}

initAIConfig();
initNarrationConfig();
initPronunciationConfig();
initWebgalMap();
initModelConfig();
initEmotions();
initEmotionParams();
initReferenceLibrary();
initShareImportExport();
const refreshBtn = document.getElementById("btn-refresh");
const refreshModal = document.getElementById("refresh-modal");
if (refreshBtn && refreshModal) {
  refreshBtn.addEventListener("click", () => {
    refreshModal.classList.remove("hidden");
  });
  document.getElementById("btn-refresh-confirm").addEventListener("click", async () => {
    refreshModal.classList.add("hidden");
    try {
      await api("/api/recent/new", { method: "POST" });
    } catch (e) {}
    window.location.reload();
  });
  document.getElementById("btn-refresh-cancel").addEventListener("click", () => {
    refreshModal.classList.add("hidden");
  });
  refreshModal.addEventListener("click", (e) => {
    if (e.target === refreshModal) refreshModal.classList.add("hidden");
  });
}
initWebGal();
showProjectPicker();
