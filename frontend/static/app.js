const state = { lines: [], chars: [], emotions: [], generating: false, hasGenerated: false };
let audioPlayer = null;

async function api(url, opts = {}) {
  const res = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || "HTTP " + res.status);
  }
  return res.json();
}

async function loadConfig() {
  const cfg = await api("/api/config");
  state.chars = cfg.characters;
  state.emotions = cfg.emotions;
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
    return '<div class="line-item">'
      + '<span class="idx">#' + (i + 1) + '</span>'
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

function setLineButtonsDisabled(disabled) {
  document.querySelectorAll(".btn-line-action").forEach(b => { b.disabled = disabled; });
  document.querySelectorAll(".interval-input").forEach(inp => { inp.disabled = disabled; });
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
      refreshGenerated(p);
      document.getElementById("step-download").classList.remove("hidden");
      return;
    }
    setTimeout(() => pollProgress(btn, progressText, barFill), 1000);
  } catch (e) {
    setTimeout(() => pollProgress(btn, progressText, barFill), 2000);
  }
}

let lastExportFolder = "";

document.getElementById("btn-export-tracks").addEventListener("click", async () => {
  const status = document.getElementById("export-status");
  const btn = document.getElementById("btn-export-tracks");
  btn.disabled = true;
  try {
    while (true) {
      const name = prompt("请输入导出文件夹名称：", "");
      if (name === null) { status.textContent = ""; break; }
      const folderName = name.trim();
      if (!folderName) { alert("名称不能为空"); continue; }
      try {
        const result = await api("/api/export_tracks", { method: "POST", body: JSON.stringify({ folder_name: folderName }) });
        lastExportFolder = result.folder;
        status.textContent = "导出完成：" + result.folder;
        status.className = "status-text success";
        document.getElementById("btn-open-export").classList.remove("hidden");
        break;
      } catch (e) {
        if (!confirm("导出失败：" + e.message + "\n是否重新输入名称？")) break;
      }
    }
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("btn-open-export").addEventListener("click", async () => {
  if (!lastExportFolder) return;
  try {
    await api("/api/open_folder", { method: "POST", body: JSON.stringify({ path: lastExportFolder }) });
  } catch (e) {
    alert("打开失败: " + e.message);
  }
});

loadConfig();
