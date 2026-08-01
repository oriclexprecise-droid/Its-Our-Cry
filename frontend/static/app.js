const state = { lines: [], chars: [], emotions: [], generating: false, hasGenerated: false, selectMode: false, selected: new Set() };
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
  loadDeployPath(cfg);
  if (cfg.has_api_key) {
    try {
      const keyData = await api("/api/config/api_key");
      if (keyData.api_key) {
        document.getElementById("api-key").value = keyData.api_key;
        document.getElementById("api-key").placeholder = "已自动填入";
      }
    } catch (e) {}
  }
}

document.getElementById("btn-analyze").addEventListener("click", async () => {
  const text = document.getElementById("script-input").value.trim();
  const apiKey = document.getElementById("api-key").value.trim();
  const lang = document.getElementById("script-lang").value;
  const status = document.getElementById("analyze-status");
  if (!text) { status.textContent = "请粘贴剧本内容"; status.className = "status-text error"; return; }
  status.textContent = "正在分析情绪...";
  status.className = "status-text";
  document.getElementById("btn-analyze").disabled = true;
  try {
    const data = await api("/api/analyze", { method: "POST", body: JSON.stringify({ text, api_key: apiKey, lang }) });
    state.lines = data.lines;
    state.hasGenerated = false;
    state.selectMode = false;
    state.selected = new Set();
    document.getElementById("btn-select-mode").textContent = "选择模式";
    document.getElementById("btn-select-mode").classList.remove("active");
    document.getElementById("selection-toolbar").classList.add("hidden");
    renderLines();
    status.textContent = "已分析 " + data.lines.length + " 条台词";
    status.className = "status-text success";
    document.getElementById("step-review").classList.remove("hidden");
    document.getElementById("step-download").classList.add("hidden");
    document.getElementById("btn-merge").classList.add("hidden");
    document.getElementById("btn-generate").disabled = false;
    document.getElementById("btn-generate").textContent = "生成全部语音";
    document.getElementById("progress-text").classList.add("hidden");
  } catch (e) {
    status.textContent = e.message;
    status.className = "status-text error";
  } finally {
    document.getElementById("btn-analyze").disabled = false;
  }
});

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
    return '<div class="line-item">'
      + idxCell
      + '<span class="char">' + esc(line.character) + '</span>'
      + '<span class="line-texts">'
      + '<span class="text" title="' + esc(line.text) + '">' + esc(line.text) + '</span>'
      + (line.translated_text ? '<span class="translated" title="' + esc(line.translated_text) + '">日语：' + esc(line.translated_text) + '</span>' : '')
      + '</span>'
      + '<input type="number" class="interval-input" data-index="' + i + '" min="0" max="10" step="0.1" value="' + (typeof line.interval === "number" ? line.interval : 0.5) + '" title="每句前间隔（秒）">'
      + '<select data-index="' + i + '" class="emotion-select">' + opts + '</select>'
      + '<span class="line-actions">'
      + '<button type="button" class="btn-line-action btn-play" data-index="' + i + '" disabled>试听</button>'
      + '<button type="button" class="btn-line-action btn-regenerate" data-index="' + i + '" disabled>重新生成</button>'
      + '</span>'
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
  const set = new Set(generated.map(String));
  document.querySelectorAll(".btn-line-action").forEach(btn => {
    const idx = btn.dataset.index;
    const ready = set.has(idx);
    if (btn.classList.contains("btn-play")) {
      btn.disabled = !ready;
      if (!ready) btn.textContent = "试听";
    } else if (btn.classList.contains("btn-regenerate")) {
      btn.disabled = !ready;
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

async function startGeneration(indices) {
  if (state.generating) return;
  const btn = document.getElementById("btn-generate");
  const progressText = document.getElementById("progress-text");
  btn.disabled = true;
  btn.textContent = "生成中...";
  state.generating = true;
  setLineButtonsDisabled(true);
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
    const body = indices ? JSON.stringify({ indices }) : JSON.stringify({});
    await api("/api/generate", { method: "POST", body });
    pollProgress(btn, progressText, barFill);
  } catch (e) {
    progressText.textContent = e.message;
    progressText.className = "status-text error";
    btn.disabled = false;
    btn.textContent = "生成全部语音";
    state.generating = false;
    setLineButtonsDisabled(false);
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
      setLineButtonsDisabled(false); refreshGenerated(p); return;
    }
    if (!p.generating && p.generated_count > 0 && p.merged_path) {
      progressText.textContent = "生成完成!";
      progressText.className = "status-text success";
      btn.textContent = "重新生成"; btn.disabled = false; state.generating = false;
      setLineButtonsDisabled(false); refreshGenerated(p);
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

  rows.push(scanGroup("FFmpeg", [
    ["状态", d.ffmpeg.installed ? "已安装" : "未安装"],
    ["版本", d.ffmpeg.version || "-"]
  ], true));

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
  const cloneDir = document.getElementById("deploy-clone-dir");
  const cloneBtn = document.getElementById("btn-deploy-clone");
  const updateCloneBtn = () => { cloneBtn.disabled = !cloneDir.value.trim(); };
  cloneDir.addEventListener("input", updateCloneBtn);
  updateCloneBtn();
  cloneBtn.addEventListener("click", startDeployClone);
}

async function startDeployClone() {
  const btn = document.getElementById("btn-deploy-clone");
  const statusEl = document.getElementById("deploy-clone-status");
  const box = document.getElementById("deploy-clone-box");
  const logEl = document.getElementById("deploy-clone-log");
  const fill = document.getElementById("deploy-clone-fill");
  const repo = document.getElementById("deploy-git-url").value.trim();
  const target = document.getElementById("deploy-clone-dir").value.trim();
  btn.disabled = true;
  statusEl.textContent = "正在克隆 GPT-SoVITS...";
  statusEl.className = "status-text";
  if (box) box.classList.remove("hidden");
  if (logEl) logEl.textContent = "";
  if (fill) fill.style.width = "0%";
  try {
    await api("/api/deploy/clone", { method: "POST", body: JSON.stringify({ repo, target_dir: target }) });
    pollDeployClone(btn, statusEl, logEl, fill);
  } catch (e) {
    statusEl.textContent = e.message;
    statusEl.className = "status-text error";
    btn.disabled = false;
  }
}

async function pollDeployClone(btn, statusEl, logEl, fill) {
  try {
    const st = await api("/api/deploy/clone_status");
    if (logEl) {
      logEl.textContent = (st.log || []).join("\n");
      logEl.scrollTop = logEl.scrollHeight;
    }
    const pct = Math.min(100, Math.max(0, st.progress || 0));
    if (fill) fill.style.width = pct + "%";
    if (st.running) {
      statusEl.textContent = "克隆中 " + pct + "%";
      statusEl.className = "status-text";
      setTimeout(() => pollDeployClone(btn, statusEl, logEl, fill), 1000);
      return;
    }
    btn.disabled = false;
    if (st.success) {
      statusEl.textContent = "克隆完成，请扫描环境";
      statusEl.className = "status-text success";
      document.getElementById("deploy-gs-path").value = st.target_dir || "";
      localStorage.setItem("mygo_deploy_gs_path", st.target_dir || "");
      localStorage.setItem("mygo_deploy_installed", "yes");
      showDeployFlow("has");
    } else {
      statusEl.textContent = "克隆失败，请查看日志";
      statusEl.className = "status-text error";
    }
  } catch (e) {
    statusEl.textContent = e.message;
    statusEl.className = "status-text error";
    btn.disabled = false;
  }
}
initDeployFlow();
loadConfig();