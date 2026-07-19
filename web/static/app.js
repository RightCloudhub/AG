/* AgenticGraphRAG trial UI — Claude-style chat shell (P4-UI-01) */
(() => {
  const $ = (id) => document.getElementById(id);
  let lastQueryId = null;

  const emptyState = $("emptyState");
  const messages = $("messages");
  const progressCard = $("progressCard");
  const answerTurn = $("answerTurn");
  const qEl = $("q");
  const askBtn = $("askBtn");

  function showConversationChrome() {
    if (emptyState) emptyState.hidden = true;
    if (messages) messages.hidden = false;
  }

  function addUserMessage(text) {
    showConversationChrome();
    const row = document.createElement("article");
    row.className = "msg user";
    row.innerHTML = `
      <div class="avatar" aria-hidden="true">你</div>
      <div class="bubble"><div class="user-text"></div></div>`;
    row.querySelector(".user-text").textContent = text;
    messages.appendChild(row);
    row.scrollIntoView({ behavior: "smooth", block: "end" });
  }

  function addProgress(msg) {
    progressCard.hidden = false;
    const li = document.createElement("li");
    li.className = "live";
    li.textContent = msg;
    $("progressList").appendChild(li);
    progressCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function clearProgress() {
    $("progressList").innerHTML = "";
    progressCard.hidden = true;
  }

  function clearAnswerTurn() {
    answerTurn.hidden = true;
    $("answerBox").textContent = "";
    $("confidence").textContent = "";
    $("chainBox").textContent = "—";
    $("stepsBox").innerHTML = "";
    document.querySelectorAll(".fb-btn").forEach((b) => b.classList.remove("active"));
  }

  function renderResult(data) {
    lastQueryId = data.query_id;
    progressCard.hidden = true;
    answerTurn.hidden = false;
    $("answerBox").textContent = data.answer || "(empty)";
    const conf = (data.metadata && data.metadata.confidence) || {};
    $("confidence").textContent = conf.level
      ? `置信度 ${conf.level}${conf.score != null ? ` · ${conf.score}` : ""} · 路由 ${data.route} · ${data.status}`
      : `路由 ${data.route || "—"} · ${data.status || "—"}`;
    $("chainBox").textContent = JSON.stringify(
      {
        query_id: data.query_id,
        route: data.route,
        status: data.status,
        claims: data.claims,
        cost: data.cost,
        explored_paths: data.explored_paths,
      },
      null,
      2
    );
    const steps = data.steps || [];
    $("stepsBox").innerHTML = steps
      .map(
        (s) => `
      <div class="step">
        <div class="hop">Hop ${s.hop} · ${escapeHtml(s.critic_action || "")}</div>
        <div><strong>子问题</strong> ${escapeHtml(s.sub_question || "")}</div>
        <div><strong>结论</strong> ${escapeHtml(s.conclusion || "—")}</div>
        <div><strong>证据</strong> ${escapeHtml((s.evidence_ids || []).join(", ") || "—")}</div>
        <div><strong>工具</strong> ${escapeHtml(
          (s.tool_calls || []).map((t) => t.tool).join(", ") || "—"
        )}</div>
      </div>`
      )
      .join("");
    answerTurn.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function requestBody() {
    return {
      question: qEl.value.trim(),
      force_agentic: $("forceAgentic").checked,
      max_hops: Number($("maxHops").value) || 5,
    };
  }

  async function askJson() {
    const res = await fetch("/v1/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody()),
    });
    const env = await res.json();
    if (!env.success) throw new Error(env.error?.message || "query failed");
    renderResult(env.data);
  }

  async function askStream() {
    const res = await fetch("/v1/query/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody()),
    });
    if (!res.ok) throw new Error("stream failed: " + res.status);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let eventType = "message";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop() || "";
      for (const block of parts) {
        let dataLine = "";
        eventType = "message";
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) eventType = line.slice(6).trim();
          if (line.startsWith("data:")) dataLine += line.slice(5).trim();
        }
        if (!dataLine) continue;
        let payload;
        try {
          payload = JSON.parse(dataLine);
        } catch {
          continue;
        }
        if (eventType === "triage") {
          addProgress(`分诊 → ${payload.route} (${payload.rationale || ""})`);
        } else if (eventType === "sub_question") {
          addProgress(`子问题 hop=${payload.hop}: ${payload.sub_question}`);
        } else if (eventType === "hop_done") {
          addProgress(
            `hop ${payload.hop} 完成: ${payload.conclusion || payload.critic_action || ""}`
          );
        } else if (eventType === "cache_hit") {
          addProgress("缓存命中");
        } else if (eventType === "answer") {
          renderResult(payload);
          addProgress("完成");
        } else if (eventType === "error") {
          addProgress("错误: " + (payload.message || payload.code));
        }
      }
    }
  }

  async function submitAsk(e) {
    if (e) e.preventDefault();
    const text = qEl.value.trim();
    if (!text) return;
    addUserMessage(text);
    clearProgress();
    clearAnswerTurn();
    askBtn.disabled = true;
    try {
      if ($("useStream").checked) {
        addProgress("连接流式接口…");
        await askStream();
      } else {
        addProgress("同步查询 /v1/query …");
        await askJson();
        addProgress("完成");
      }
    } catch (err) {
      addProgress(String(err.message || err));
      answerTurn.hidden = false;
      $("answerBox").textContent = "请求失败，请稍后重试。";
    } finally {
      askBtn.disabled = false;
      qEl.value = "";
      autoResize();
    }
  }

  function autoResize() {
    qEl.style.height = "auto";
    qEl.style.height = Math.min(qEl.scrollHeight, 128) + "px";
  }

  $("askForm").addEventListener("submit", submitAsk);

  qEl.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      submitAsk();
    }
  });
  qEl.addEventListener("input", autoResize);

  document.querySelectorAll(".chip[data-q]").forEach((chip) => {
    chip.addEventListener("click", () => {
      qEl.value = chip.getAttribute("data-q") || "";
      autoResize();
      qEl.focus();
      submitAsk();
    });
  });

  document.querySelectorAll(".fb-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!lastQueryId) {
        addProgress("请先提问一次再反馈");
        return;
      }
      const accurate = btn.dataset.acc === "1";
      const reason = $("fbReason").value || "";
      document.querySelectorAll(".fb-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const res = await fetch("/v1/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query_id: lastQueryId, accurate, reason }),
      });
      const env = await res.json();
      addProgress(env.success ? "反馈已提交，感谢" : "反馈失败");
    });
  });
})();
