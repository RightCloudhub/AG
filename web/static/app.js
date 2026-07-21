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
    $("planTree").innerHTML = "";
    $("pathsBox").innerHTML = "";
    $("claimsList").innerHTML = "";
    $("claimsPanel").hidden = true;
    document.querySelectorAll(".fb-btn").forEach((b) => b.classList.remove("active"));
  }

  function renderResult(data) {
    lastQueryId = data.query_id;
    progressCard.hidden = true;
    answerTurn.hidden = false;
    const claims = data.claims || [];
    renderAnswerWithCitations(data.answer || "(empty)", claims);
    renderClaimsPanel(claims);
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
    renderPlanTree(steps);
    renderPaths(data.explored_paths || []);
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

  function renderAnswerWithCitations(answer, claims) {
    const box = $("answerBox");
    box.textContent = "";
    if (!claims.length) {
      box.textContent = answer;
      return;
    }
    // Prefer claim-text markers in the answer; else append citation badges.
    let remaining = answer;
    const frag = document.createDocumentFragment();
    let anyInline = false;
    claims.forEach((c, i) => {
      const text = (c && c.text) || "";
      if (!text) return;
      const idx = remaining.indexOf(text);
      if (idx < 0) return;
      anyInline = true;
      if (idx > 0) frag.appendChild(document.createTextNode(remaining.slice(0, idx)));
      frag.appendChild(document.createTextNode(text));
      frag.appendChild(citeBadge(i + 1, c.evidence_ids || []));
      remaining = remaining.slice(idx + text.length);
    });
    if (anyInline) {
      if (remaining) frag.appendChild(document.createTextNode(remaining));
      box.appendChild(frag);
      return;
    }
    box.appendChild(document.createTextNode(answer + " "));
    claims.forEach((c, i) => {
      box.appendChild(citeBadge(i + 1, (c && c.evidence_ids) || []));
      box.appendChild(document.createTextNode(" "));
    });
  }

  function citeBadge(n, evidenceIds) {
    const sup = document.createElement("sup");
    sup.className = "cite-badge";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cite-btn";
    btn.textContent = String(n);
    btn.title = evidenceIds.length
      ? "证据: " + evidenceIds.join(", ")
      : "论断 " + n;
    btn.addEventListener("click", () => {
      const el = document.getElementById("claim-" + n);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    sup.appendChild(btn);
    return sup;
  }

  function renderClaimsPanel(claims) {
    const panel = $("claimsPanel");
    const list = $("claimsList");
    list.innerHTML = "";
    if (!claims.length) {
      panel.hidden = true;
      return;
    }
    panel.hidden = false;
    claims.forEach((c, i) => {
      const li = document.createElement("li");
      li.id = "claim-" + (i + 1);
      const title = document.createElement("div");
      title.className = "claim-text";
      title.textContent = (c && c.text) || "(empty claim)";
      const ev = document.createElement("div");
      ev.className = "claim-ev";
      ev.textContent =
        "证据: " + (((c && c.evidence_ids) || []).join(", ") || "—");
      li.appendChild(title);
      li.appendChild(ev);
      list.appendChild(li);
    });
  }

  function renderPlanTree(steps) {
    const root = $("planTree");
    root.innerHTML = "";
    if (!steps.length) {
      const li = document.createElement("li");
      li.className = "muted";
      li.textContent = "无子问题步骤";
      root.appendChild(li);
      return;
    }
    // Index by hop; edges via depends_on when present.
    steps.forEach((s, i) => {
      const li = document.createElement("li");
      li.className = "plan-node status-" + statusClass(s.critic_action);
      const head = document.createElement("div");
      head.className = "plan-head";
      head.textContent =
        "Hop " +
        (s.hop != null ? s.hop : i + 1) +
        " · " +
        (s.critic_action || "open");
      const q = document.createElement("div");
      q.className = "plan-q";
      q.textContent = s.sub_question || "";
      const conc = document.createElement("div");
      conc.className = "plan-c";
      conc.textContent = s.conclusion ? "→ " + s.conclusion : "";
      const deps = (s.depends_on || []).join(", ");
      if (deps) {
        const d = document.createElement("div");
        d.className = "plan-deps";
        d.textContent = "depends: " + deps;
        li.appendChild(d);
      }
      li.appendChild(head);
      li.appendChild(q);
      if (s.conclusion) li.appendChild(conc);
      root.appendChild(li);
    });
  }

  function statusClass(action) {
    const a = String(action || "").toLowerCase();
    if (a === "sufficient") return "ok";
    if (a === "give_up") return "fail";
    if (a === "rewrite" || a === "next_hop") return "partial";
    return "open";
  }

  function renderPaths(paths) {
    const box = $("pathsBox");
    box.innerHTML = "";
    if (!paths.length) {
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = "无探索路径";
      box.appendChild(empty);
      return;
    }
    paths.slice(0, 40).forEach((p) => {
      const row = document.createElement("div");
      row.className = "path-row";
      parsePath(p).forEach((seg, i) => {
        if (i > 0) {
          const arrow = document.createElement("span");
          arrow.className = "path-arrow";
          arrow.textContent = "→";
          row.appendChild(arrow);
        }
        const chip = document.createElement("span");
        chip.className = seg.kind === "edge" ? "path-edge" : "path-node";
        chip.textContent = seg.text;
        row.appendChild(chip);
      });
      box.appendChild(row);
    });
  }

  function parsePath(raw) {
    const s = String(raw || "").trim();
    if (!s) return [{ kind: "node", text: "(empty)" }];
    // Common shapes: "A -[REL]-> B" or "A -> B -> C" or free text.
    const edgeRe = /\s*-\[([^\]]+)\]->\s*|\s*->\s*|\s*→\s*/g;
    const parts = [];
    let last = 0;
    let m;
    while ((m = edgeRe.exec(s)) !== null) {
      const node = s.slice(last, m.index).trim();
      if (node) parts.push({ kind: "node", text: node });
      const rel = m[1] ? m[1].trim() : "→";
      parts.push({ kind: "edge", text: rel === "→" ? "rel" : rel });
      last = m.index + m[0].length;
    }
    const tail = s.slice(last).trim();
    if (tail) parts.push({ kind: "node", text: tail });
    return parts.length ? parts : [{ kind: "node", text: s }];
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
      if (env.success) {
        addProgress("反馈已提交，感谢");
      } else {
        const msg =
          (env.error && env.error.message) ||
          (env.error && env.error.code) ||
          "反馈失败";
        addProgress("反馈失败: " + msg);
      }
    });
  });
})();
