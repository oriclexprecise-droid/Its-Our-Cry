const state = { lines: [], chars: [], emotions: [], generating: false };

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
  const container = document.getElementById("lines-container");
  document.getElementById("line-count").textContent = "共 " + state.lines.length + " 条台词";
  container.innerHTML = state.lines.map((line, i) => {
    const opts = state.emotions.map(e => '<option value="' + e + '"' + (e === line.emotion ? " selected" : "") + '>' + e + '</option>').join("");
    return '<div class="line-item">'
      + '<span class="idx">#' + (i + 1) + '</span>'
      + '<span class="char">' + esc(line.character) + '</span>'
      + '<span class="text" title="' + esc(line.text) + '">' + esc(line.text) + '</span>'
      + '<select data-index="' + i + '" class="emotion-select">' + opts + '</select>'
      + '</div>';
  }).join("");
  container.querySelectorAll(".emotion-select").forEach(sel => {
    sel.addEventListener("change", async (e) => {
      const idx = parseInt(e.target.dataset.index);
      state.lines[idx].emotion = e.target.value;
      try { await api("/api/line/" + idx, { method: "PUT", body: JSON.stringify({ emotion: e.target.value }) }); } catch (err) {}
    });
  });
}

function esc(s) { return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

async function downloadFile(type, label) {
  try {
    const resp = await fetch("/api/download/" + type + "?t=" + Date.now());
    if (!resp.ok) throw new Error("file not ready");
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = type === "merged" ? "merged_output.wav" : "subtitles.srt";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (e) {
    alert("下载失败: " + e.message);
  }
}

document.getElementById("dl-audio").addEventListener("click", () => downloadFile("merged"));
document.getElementById("dl-srt").addEventListener("click", () => downloadFile("srt"));

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

document.getElementById("btn-generate").addEventListener("click", async () => {
  if (state.generating) return;
  const btn = document.getElementById("btn-generate");
  const progressText = document.getElementById("progress-text");
  btn.disabled = true;
  btn.textContent = "生成中...";
  state.generating = true;
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
    await api("/api/generate", { method: "POST", body: JSON.stringify({}) });
    pollProgress(btn, progressText, barFill);
  } catch (e) {
    progressText.textContent = e.message;
    progressText.className = "status-text error";
    btn.disabled = false;
    btn.textContent = "生成全部语音";
    state.generating = false;
  }
});

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
      btn.disabled = false; btn.textContent = "生成全部语音"; state.generating = false; return;
    }
    if (!p.generating && p.generated_count > 0 && p.merged_path) {
      progressText.textContent = "生成完成!";
      progressText.className = "status-text success";
      btn.textContent = "重新生成"; btn.disabled = false; state.generating = false;
      document.getElementById("step-download").classList.remove("hidden");
      return;
    }
    setTimeout(() => pollProgress(btn, progressText, barFill), 1000);
  } catch (e) {
    setTimeout(() => pollProgress(btn, progressText, barFill), 2000);
  }
}

loadConfig();
