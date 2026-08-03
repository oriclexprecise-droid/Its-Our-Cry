const state = { lines: [], chars: [], emotions: [], generating: false, hasGenerated: false, selectMode: false, selected: new Set(), failures: {}, pronunciation: [] };
let audioPlayer = null;
let analysisController = null;

async function api(url, opts = {}) {
  const res = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const error = new Error(err.error || "HTTP " + res.status);
    if (err.code) error.code = err.code;
    throw error;
  }
  return res.json();
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
    titleEl.textContent = options.title || "确认操作";
    msgEl.textContent = message;
    okBtn.textContent = options.okText || "确定";
    cancelBtn.textContent = options.cancelText || "取消";
    cancelBtn.classList.remove("hidden");
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      modal.classList.add("hidden");
      okBtn.onclick = null;
      cancelBtn.onclick = null;
      modal.onclick = null;
      resolve(value);
    };
    okBtn.onclick = () => finish(true);
    cancelBtn.onclick = () => finish(false);
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
    titleEl.textContent = options.title || "提示";
    msgEl.textContent = message;
    okBtn.textContent = options.okText || "知道了";
    cancelBtn.classList.add("hidden");
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      modal.classList.add("hidden");
      okBtn.onclick = null;
      cancelBtn.onclick = null;
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

async function runAnalyze() {
  const text = document.getElementById("script-input").value.trim();
  const apiKey = document.getElementById("api-key").value.trim();
  const lang = document.getElementById("script-lang").value;
  const status = document.getElementById("analyze-status");
  if (!text) { status.textContent = "请粘贴剧本内容"; status.className = "status-text error"; return; }
  status.textContent = "正在分析情绪...";
  status.className = "status-text";
  document.getElementById("btn-analyze").disabled = true;
  if (analysisController) analysisController.abort();
  const controller = new AbortController();
  analysisController = controller;
  const stopBtn = document.getElementById("btn-stop-analyze");
  stopBtn.classList.remove("hidden");
  try {
    const data = await api("/api/analyze", { method: "POST", body: JSON.stringify({ text, api_key: apiKey, lang, base_url: document.getElementById("ai-base-url").value.trim(), model: document.getElementById("ai-model").value.trim() }), signal: controller.signal });
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
    const issues = data.proofread || [];
    if (issues.length) showProofreadModal(issues);
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
  }
}

document.getElementById("btn-analyze").addEventListener("click", runAnalyze);
document.getElementById("btn-stop-analyze").addEventListener("click", () => {
  if (analysisController) analysisController.abort();
  api("/api/analyze/cancel", { method: "POST" }).catch(() => {});
});
document.getElementById("btn-stop-line-analyze").addEventListener("click", () => {
  if (analysisController) analysisController.abort();
  api("/api/analyze/cancel", { method: "POST" }).catch(() => {});
});

const btnUndoEl = document.getElementById("btn-undo");
const btnRedoEl = document.getElementById("btn-redo");
if (btnUndoEl) btnUndoEl.addEventListener("click", () => runHistory("undo"));
if (btnRedoEl) btnRedoEl.addEventListener("click", () => runHistory("redo"));

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && (e.key === "z" || e.key === "Z")) {
    const el = document.activeElement;
    const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
    if (typing) return;
    e.preventDefault();
    if (e.shiftKey) runHistory("redo");
    else runHistory("undo");
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
    document.getElementById("script-input").value = rawLines.join("\n");
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

function esc(s) { return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

async function refreshRecentList() {
  const listEl = document.getElementById("recent-list");
  if (!listEl) return;
  try {
    const data = await api("/api/recent");
    const records = data.records || [];
    const settings = data.settings || {};
    const autoEl = document.getElementById("recent-auto-save");
    const limitEl = document.getElementById("recent-limit");
    if (autoEl) autoEl.checked = !!settings.auto_save;
    if (limitEl) limitEl.value = settings.limit || 50;
    if (!records.length) {
      listEl.innerHTML = '<div class="recent-empty">暂无近期记录</div>';
      return;
    }
    listEl.innerHTML = records.map(r => {
      const first = r.first_line ? '<div class="recent-preview" title="' + esc(r.first_line) + '">' + esc(r.first_line) + '</div>' : "";
      const meta = r.line_count + " 条 · " + r.voice_count + " 条语音" + (r.fail_count > 0 ? " · 仅字幕 " + r.fail_count + " 条" : "") + (r.export_count > 0 ? " · 已导出 " + r.export_count + " 次" : "");
      return '<div class="recent-item">'
        + '<div class="recent-item-main">'
        + '<div class="recent-item-head"><span class="recent-time">' + esc(r.saved_at || "") + '</span><span class="recent-badge">' + (r.source === "manual" ? "手动" : "自动") + " · " + (r.lang === "ja" ? "日语" : "中文") + "</span></div>"
        + first
        + '<div class="recent-meta">' + esc(meta) + "</div>"
        + '</div>'
        + '<div class="recent-actions">'
        + '<button type="button" class="btn-line-action btn-recent-load" data-id="' + esc(r.id) + '">载入</button>'
        + (r.last_folder ? '<button type="button" class="btn-line-action btn-recent-open" data-id="' + esc(r.id) + '">打开文件夹</button>' : "")
        + '<button type="button" class="btn-line-action btn-recent-del" data-id="' + esc(r.id) + '">删除</button>'
        + '</div>'
        + '</div>';
    }).join("");
    listEl.querySelectorAll(".btn-recent-load").forEach(btn => btn.addEventListener("click", () => loadRecentRecord(btn.dataset.id)));
    listEl.querySelectorAll(".btn-recent-open").forEach(btn => btn.addEventListener("click", () => openRecentFolder(btn.dataset.id)));
    listEl.querySelectorAll(".btn-recent-del").forEach(btn => btn.addEventListener("click", () => deleteRecentRecord(btn.dataset.id)));
  } catch (e) {}
}

function setRecentStatus(msg, kind) {
  const el = document.getElementById("recent-status");
  if (!el) return;
  el.textContent = msg;
  el.className = "status-text" + (kind === "error" ? " error" : kind === "success" ? " success" : "");
}

async function saveRecentRecord() {
  if (state.generating) { setRecentStatus("生成中，请稍后再保存记录", "error"); return; }
  if (!state.lines.length) { setRecentStatus("还没有可保存的剧本", "error"); return; }
  setRecentStatus("正在保存...", "");
  try {
    await api("/api/recent/save", { method: "POST" });
    setRecentStatus("已保存为近期记录", "success");
    refreshRecentList();
  } catch (e) {
    setRecentStatus("保存失败: " + e.message, "error");
  }
}

async function loadRecentRecord(id) {
  if (state.generating) { setRecentStatus("生成中，请稍后再载入记录", "error"); return; }
  if (state.lines.length && !(await showConfirmModal("载入记录会覆盖当前工作台（可以用撤销恢复），确定继续吗？"))) return;
  setRecentStatus("正在载入...", "");
  try {
    const res = await api("/api/recent/" + id + "/load", { method: "POST" });
    const s = res.state || {};
    state.lines = s.lines || [];
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
    setRecentStatus("已载入记录" + (res.record && res.record.saved_at ? "：" + res.record.saved_at : ""), "success");
    refreshRecentList();
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
  if (!(await showConfirmModal("确定删除这条近期记录？"))) return;
  try {
    await api("/api/recent/" + id, { method: "DELETE" });
    setRecentStatus("已删除记录", "success");
    refreshRecentList();
  } catch (e) {
    setRecentStatus("删除失败: " + e.message, "error");
  }
}

async function clearRecentRecords() {
  if (!(await showConfirmModal("确定清空全部近期记录？此操作不可恢复。"))) return;
  const status = document.getElementById("recent-settings-status");
  try {
    await api("/api/recent/clear", { method: "POST" });
    if (status) { status.textContent = "已清空全部记录"; status.className = "status-text success"; }
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
    status.textContent = "记录上限请输入 10-500 的数字";
    status.className = "status-text error";
    return;
  }
  const autoEl = document.getElementById("recent-auto-save");
  const auto = autoEl ? autoEl.checked : true;
  status.textContent = "正在保存记录设置...";
  status.className = "status-text";
  try {
    const res = await api("/api/recent/settings", { method: "POST", body: JSON.stringify({ limit: limitRaw, auto_save: auto }) });
    if (limitEl) limitEl.value = res.settings.limit;
    if (autoEl) autoEl.checked = !!res.settings.auto_save;
    status.textContent = "记录设置已保存";
    status.className = "status-text success";
    refreshRecentList();
  } catch (e) {
    status.textContent = "保存失败: " + e.message;
    status.className = "status-text error";
  }
}

function initRecentRecords() {
  const saveBtn = document.getElementById("btn-save-record");
  if (saveBtn) saveBtn.addEventListener("click", saveRecentRecord);
  const saveSettingsBtn = document.getElementById("btn-save-recent-settings");
  if (saveSettingsBtn) saveSettingsBtn.addEventListener("click", saveRecentSettings);
  const clearBtn = document.getElementById("btn-clear-recent");
  if (clearBtn) clearBtn.addEventListener("click", clearRecentRecords);
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
  const uc = h ? h.undo_count : 0;
  const rc = h ? h.redo_count : 0;
  undoBtn.disabled = uc === 0;
  redoBtn.disabled = rc === 0;
  undoBtn.title = uc ? "撤销 " + (h.undo_label || "") + " (Ctrl+Z)" : "撤销 (Ctrl+Z)";
  redoBtn.title = rc ? "重做 " + (h.redo_label || "") + " (Ctrl+Shift+Z)" : "重做 (Ctrl+Shift+Z)";
}

async function refreshHistory() {
  try {
    const h = await api("/api/history");
    updateHistoryButtons(h);
  } catch (e) {}
}

async function runHistory(dir) {
  if (state.generating) return;
  const status = document.getElementById("progress-text");
  if (status) {
    status.classList.remove("hidden");
    status.textContent = dir === "undo" ? "正在撤销..." : "正在重做...";
    status.className = "status-text";
  }
  try {
    const res = await api("/api/" + dir, { method: "POST" });
    const s = res.state || {};
    state.lines = s.lines || [];
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
    if (status) {
      status.textContent = (dir === "undo" ? "已撤销：" : "已重做：") + (res.label || "");
      status.className = "status-text success";
    }
    updateHistoryButtons(res.history);
  } catch (e) {
    if (status) {
      status.textContent = (dir === "undo" ? "撤销失败: " : "重做失败: ") + e.message;
      status.className = "status-text error";
    }
  }
}


document.getElementById("btn-merge").addEventListener("click", async () => {
  const btn = document.getElementById("btn-merge");
  const progressText = document.getElementById("progress-text");
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

async function startGeneration(indices, srtOnly) {
  if (state.generating) return;
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
  const settings = document.getElementById("view-settings");
  if (!workbench || !settings) return;
  const recentDropdown = document.getElementById("recent-dropdown");
  if (recentDropdown) recentDropdown.classList.add("hidden");
  const recentBtn = document.getElementById("btn-recent");
  if (recentBtn) recentBtn.classList.add("hidden");
  workbench.classList.add("hidden");
  settings.classList.remove("hidden");
  document.getElementById("btn-back-workbench").classList.remove("hidden");
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
  const settings = document.getElementById("view-settings");
  if (!workbench || !settings) return;
  workbench.classList.remove("hidden");
  settings.classList.add("hidden");
  document.getElementById("btn-back-workbench").classList.add("hidden");
  document.getElementById("btn-settings").classList.remove("hidden");
  const recentBtn = document.getElementById("btn-recent");
  if (recentBtn) recentBtn.classList.remove("hidden");
}

function initSettingsNav() {
  document.getElementById("btn-settings").addEventListener("click", () => showSettings(null));
  document.getElementById("btn-back-workbench").addEventListener("click", showWorkbench);
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
        + '<button type="button" class="btn-line-action btn-ref-play" data-key="' + esc(item.key) + '" data-emotion="' + esc(em.emotion) + '" data-name="' + esc(f.name) + '">试听</button>'
        + '<button type="button" class="btn-line-action btn-ref-del" data-key="' + esc(item.key) + '" data-emotion="' + esc(em.emotion) + '" data-name="' + esc(f.name) + '">删除</button>'
        + '</div>'
      )).join("") : '<span class="ref-empty">暂无音频</span>';
      return '<div class="ref-emotion">'
        + '<div class="ref-emotion-head">'
        + '<span class="ref-emotion-name">' + esc(em.emotion) + '</span>'
        + '<span class="ref-emotion-status ' + (files.length ? "ok" : "empty") + '">' + (files.length ? files.length + " 个音频" : "缺失") + '</span>'
        + '</div>'
        + '<div class="ref-files">' + filesHtml + '</div>'
        + '<div class="ref-upload-row">'
        + '<input type="file" class="ref-file-input hidden" accept=".wav,.mp3,.flac,.ogg,.m4a,.aac" data-key="' + esc(item.key) + '" data-emotion="' + esc(em.emotion) + '">'
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
    btn.addEventListener("click", () => {
      const input = btn.parentElement.querySelector(".ref-file-input");
      if (input) input.click();
    });
  });
  listEl.querySelectorAll(".ref-file-input").forEach(input => {
    input.addEventListener("change", () => uploadRefAudio(input));
  });
}

function playRefAudio(key, emotion, name) {
  if (refAudioPlayer) { refAudioPlayer.pause(); refAudioPlayer = null; }
  const a = new Audio(refAudioUrl(key, emotion, name));
  refAudioPlayer = a;
  a.play().catch(() => {});
}

async function uploadRefAudio(input) {
  const file = input.files && input.files[0];
  const statusEl = document.getElementById("ref-library-status");
  if (!file) return;
  const key = input.dataset.key;
  const emotion = input.dataset.emotion;
  const form = new FormData();
  form.append("key", key);
  form.append("emotion", emotion);
  form.append("file", file);
  if (statusEl) { statusEl.textContent = "正在上传 " + file.name + "..."; statusEl.className = "status-text"; }
  try {
    const res = await fetch("/api/reference/audio/upload", { method: "POST", body: form });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || ("HTTP " + res.status));
    }
    if (statusEl) { statusEl.textContent = "已上传到 " + key + "「" + emotion + "」"; statusEl.className = "status-text success"; }
    input.value = "";
    await loadReferenceLibrary();
  } catch (e) {
    if (statusEl) { statusEl.textContent = "上传失败: " + e.message; statusEl.className = "status-text error"; }
    input.value = "";
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
  loadReferenceLibrary();
}

function initModelConfig() {
  loadModels();
}

initAIConfig();
initNarrationConfig();
initPronunciationConfig();
initModelConfig();
initReferenceLibrary();
const refreshBtn = document.getElementById("btn-refresh");
const refreshModal = document.getElementById("refresh-modal");
if (refreshBtn && refreshModal) {
  refreshBtn.addEventListener("click", () => {
    refreshModal.classList.remove("hidden");
  });
  document.getElementById("btn-refresh-confirm").addEventListener("click", () => {
    refreshModal.classList.add("hidden");
    window.location.reload();
  });
  document.getElementById("btn-refresh-cancel").addEventListener("click", () => {
    refreshModal.classList.add("hidden");
  });
  refreshModal.addEventListener("click", (e) => {
    if (e.target === refreshModal) refreshModal.classList.add("hidden");
  });
}
