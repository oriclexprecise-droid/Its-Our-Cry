const state = { lines: [], chars: [], emotions: [], generating: false, hasGenerated: false, selectMode: false, selected: new Set(), failures: {} };
let audioPlayer = null;

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

async function loadConfig() {
  const cfg = await api("/api/config");
  state.chars = cfg.characters;
  state.emotions = cfg.emotions;
  setNarrationInputs(cfg.narration || {});
  loadDeployPath(cfg);
  loadCleanPath(cfg);
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
  try {
    const data = await api("/api/analyze", { method: "POST", body: JSON.stringify({ text, api_key: apiKey, lang, base_url: document.getElementById("ai-base-url").value.trim(), model: document.getElementById("ai-model").value.trim() }) });
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
    const issues = data.proofread || [];
    if (issues.length) showProofreadModal(issues);
  } catch (e) {
    status.textContent = e.message;
    status.className = "status-text error";
  } finally {
    document.getElementById("btn-analyze").disabled = false;
  }
}

document.getElementById("btn-analyze").addEventListener("click", runAnalyze);

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
      + '<span class="char">' + esc(line.character) + '</span>'
      + '<span class="line-texts">'
      + '<span class="text" title="' + esc(line.text) + '">' + esc(line.text) + '</span>'
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
      state.lines[idx].emotion = e.target.value;
      try { await api("/api/line/" + idx, { method: "PUT", body: JSON.stringify({ emotion: e.target.value }) }); } catch (err) {}
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
  document.querySelectorAll(".line-check, #select-all, #btn-apply-interval, #batch-interval, #btn-select-mode").forEach(el => { el.disabled = disabled; });
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
}

function esc(s) { return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }


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
        progressText.textContent = "生成完成：语音 " + p.generated_count + " 条，另有 " + failCount + " 条仅保留字幕";
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
    alert("打开失败: " + e.message);
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
  if (!keys.length) { alert("请先勾选要清理的项目"); return; }
  const groups = (cleanScanData && cleanScanData.groups) || [];
  const total = groups.filter(g => keys.indexOf(g.key) >= 0).reduce((sum, g) => sum + (g.size || 0), 0);
  const missing = (cleanScanData && cleanScanData.missing_models) || [];
  const names = keys.map(k => { const g = groups.find(x => x.key === k); return g ? g.label + "（" + fmtSize(g.size) + "）" : k; });
  const msg = "即将删除以下内容：\n" + names.join("\n") + "\n\n共 " + fmtSize(total) + "，删除后不可恢复。确定继续吗？";
  if (keys.indexOf("model_weights") >= 0 && missing.length && !confirm("SoVITS 中缺少部分模型，继续清理可能导致这些角色无法生成语音。确定仍要清理吗？")) return;
  if (!confirm(msg)) return;
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
  });
  document.getElementById("btn-deploy-no").addEventListener("click", () => {
    localStorage.setItem("mygo_deploy_installed", "no");
    showDeployFlow("no");
  });
  document.getElementById("btn-deploy-back-has").addEventListener("click", () => {
    localStorage.removeItem("mygo_deploy_installed");
    showDeployFlow(null);
  });
  document.getElementById("btn-deploy-back-no").addEventListener("click", () => {
    localStorage.removeItem("mygo_deploy_installed");
    showDeployFlow(null);
  });
  initDeployDownload();
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
initBackgroundSettings();
function initPanelCollapse() {
  const specs = [
    { btn: "btn-collapse-settings", key: "mygo_panel_settings_collapsed" },
    { btn: "btn-collapse-models", key: "mygo_panel_models_collapsed" },
    { btn: "btn-collapse-deploy", key: "mygo_panel_deploy_collapsed" },
    { btn: "btn-collapse-clean", key: "mygo_panel_clean_collapsed" },
    { btn: "btn-collapse-log", key: "mygo_panel_log_collapsed" },
    { btn: "btn-collapse-legal", key: "mygo_panel_legal_collapsed" }
  ];
  const registry = {};
  specs.forEach(spec => {
    const btn = document.getElementById(spec.btn);
    const panel = btn ? btn.closest(".panel") : null;
    if (!btn || !panel) return;
    registry[spec.btn] = { btn, panel };
    const apply = (collapsed) => {
      panel.classList.toggle("collapsed", collapsed);
      btn.textContent = collapsed ? "▴" : "▾";
      btn.title = collapsed ? "展开面板" : "收起面板";
    };
    apply(true);
    localStorage.setItem(spec.key, "1");
    btn.addEventListener("click", () => {
      const next = !panel.classList.contains("collapsed");
      apply(next);
      localStorage.setItem(spec.key, next ? "1" : "0");
      if (!next) {
        Object.keys(registry).forEach(otherBtn => {
          if (otherBtn === spec.btn) return;
          const other = registry[otherBtn];
          if (other && !other.panel.classList.contains("collapsed")) {
            other.panel.classList.add("collapsed");
            other.btn.textContent = "▴";
            other.btn.title = "展开面板";
            const otherSpec = specs.find(s => s.btn === otherBtn);
            if (otherSpec) localStorage.setItem(otherSpec.key, "1");
          }
        });
      }
    });
  });
}
initPanelCollapse();
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
    if (!confirm("确定清空全部日志吗？")) return;
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
    alert("添加激活词失败: " + e.message);
  } finally {
    btn.disabled = false;
  }
}

async function deleteModelAlias(key, alias) {
  if (!confirm("确定删除激活词“" + alias + "”吗？")) return;
  try {
    await api("/api/models/" + encodeURIComponent(key) + "/aliases/" + encodeURIComponent(alias), { method: "DELETE" });
    await loadModels();
    await loadConfig();
  } catch (e) {
    alert("删除激活词失败: " + e.message);
  }
}

function initModelConfig() {
  loadModels();
}

initAIConfig();
initNarrationConfig();
initModelConfig();
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
