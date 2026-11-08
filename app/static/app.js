// The page: who is signed in, the assistant turns a plain-words description into a structured brief, and the chosen option is built as a 3D
// massing model on the worker (FreeCAD) and rendered as a picture (Blender). No framework; everything the app does is in app.py.
const $ = (id) => document.getElementById(id);
let lastAnswer = null;
let options = [];

async function api(path, options = {}) {
  const res = await fetch(path, { redirect: "manual", headers: { "Content-Type": "application/json" }, ...options });
  if (res.type === "opaqueredirect" || res.status === 401) throw new Error("Your sign-in has expired: reload the page.");
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `Something went wrong (${res.status})`);
  return body;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  for (const c of children) node.append(c);
  return node;
}

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

// A tiny site plan from the option's volumes: rectangles on the site grid, north up (so the y axis flips).
function svgPlan(volumes) {
  const W = 150, H = 120, pad = 9;
  const xs = volumes.map((v) => [v.x, v.x + v.w]).flat();
  const ys = volumes.map((v) => [v.y, v.y + v.d]).flat();
  const spanX = Math.max(...xs) - Math.min(...xs) || 1;
  const spanY = Math.max(...ys) - Math.min(...ys) || 1;
  const s = Math.min((W - 2 * pad) / spanX, (H - 2 * pad) / spanY);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "site plan of the option" });
  for (const v of volumes) {
    svg.append(svgEl("rect", {
      x: (pad + (v.x - Math.min(...xs)) * s).toFixed(1),
      y: (H - pad - (v.y + v.d - Math.min(...ys)) * s).toFixed(1),
      width: (v.w * s).toFixed(1), height: (v.d * s).toFixed(1),
    }));
  }
  return svg;
}

function showBrief(a) {
  const box = $("answer");
  box.replaceChildren();
  if (a.title) box.append(el("h3", {}, a.title));
  if (a.summary) box.append(el("p", {}, a.summary));
  if (Array.isArray(a.spaces) && a.spaces.length) {
    const table = el("table", { class: "brief-table" });
    table.append(el("tr", {}, el("th", {}, "Space"), el("th", {}, "Floors"), el("th", { class: "num" }, "m²")));
    let total = 0;
    const outdoors = [];
    for (const sp of a.spaces) {
      const name = String(sp.name || "");
      if (String(sp.floors || "").toLowerCase() === "outdoor") { outdoors.push([name, sp.area_m2]); continue; }
      total += Number(sp.area_m2) || 0;
      table.append(el("tr", {}, el("td", {}, name), el("td", {}, String(sp.floors || "")), el("td", { class: "num" }, String(sp.area_m2 ?? ""))));
    }
    table.append(el("tr", {}, el("td", { class: "total" }, "Total (indoor)"), el("td", {}, ""), el("td", { class: "num total" }, String(Math.round(total)))));
    for (const [name, area] of outdoors) {
      table.append(el("tr", {}, el("td", {}, name), el("td", {}, "outdoor"), el("td", { class: "num" }, String(area ?? "") + " (not in total)")));
    }
    box.append(table);
  }
  if (Array.isArray(a.site_notes) && a.site_notes.length) box.append(el("ul", {}, ...a.site_notes.map((n) => el("li", {}, String(n)))));
  if (a.next) box.append(el("p", { class: "next" }, "Next: ", String(a.next)));
  box.hidden = false;
}

function renderOptions() {
  const list = $("option-list");
  list.replaceChildren();
  $("run-job").disabled = true;
  options.forEach((opt, i) => {
    const input = el("input");
    input.type = "radio"; input.name = "option"; input.id = `option-${i}`; input.dataset.i = String(i);
    input.addEventListener("change", () => { $("run-job").disabled = false; });
    const label = el("label", { for: input.id }, input);
    if (Array.isArray(opt.volumes) && opt.volumes.length) label.append(svgPlan(opt.volumes));
    label.append(el("span", { class: "option-text" },
      el("span", { class: "option-name" }, `${opt.id || "?"} · ${opt.name || "option"}`),
      el("span", { class: "option-idea" }, String(opt.idea || ""))));
    list.append(el("li", {}, label));
  });
}

async function me() {
  try {
    const who = await api("/api/me");
    $("who").textContent = who.signed_in ? `${who.name}${who.allowed ? "" : " (not on this tool's list)"}` : "Not signed in";
    $("signout").hidden = !who.signed_in;
  } catch (e) {
    $("who").textContent = e.message;
  }
}

$("ask-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const prompt = $("prompt").value.trim();
  if (!prompt) return;
  $("ask").disabled = true;
  $("ask-state").textContent = "Thinking…";
  try {
    const reply = await api("/api/ask", { method: "POST", body: JSON.stringify({ prompt }) });
    lastAnswer = { prompt, answer: reply.answer };
    options = Array.isArray(reply.answer.options) ? reply.answer.options : [];
    showBrief(reply.answer);
    renderOptions();
    $("ask-state").textContent = `Answered in ${reply.seconds} s`;
  } catch (e) {
    $("ask-state").textContent = e.message;
  } finally {
    $("ask").disabled = false;
  }
});

$("run-job").addEventListener("click", async () => {
  const chosen = document.querySelector("input[name=option]:checked");
  if (!lastAnswer || !chosen) return;
  $("run-job").disabled = true;
  try {
    const opt = options[Number(chosen.dataset.i)];
    const job = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({ kind: "massing", input: { project: lastAnswer.answer.title || "", option: opt } }),
    });
    const item = el("li", { id: `job-${job.id}` }, el("span", { class: "job-id" }, job.id), el("span", { class: "job-state" }, "queued"));
    $("job-list").prepend(item);
    watch(job.id, item);
  } catch (e) {
    // the error belongs in the jobs panel (ask-state is the assistant's), so the job panel shows it as its own entry
    $("job-list").prepend(el("li", { class: "job-error" }, el("span", { class: "job-state bad" }, "job not queued"), el("span", {}, e.message)));
  } finally {
    $("run-job").disabled = false;
  }
});

async function watch(id, item) {
  for (let i = 0; i < 120; i++) {
    await new Promise((r) => setTimeout(r, 2500));
    let status;
    try { status = await api(`/api/jobs/${id}`); } catch { continue; }
    item.querySelector(".job-state").textContent = status.state + (status.message ? `: ${status.message}` : "");
    if (status.state === "done" || status.state === "failed") {
      for (const [name, url] of Object.entries(status.links || {})) {
        const link = /\.(png|jpe?g|gif|webp)$/i.test(name) ? el("img", { src: url, alt: name }) : el("a", { href: url, target: "_blank", rel: "noopener" }, name);
        item.append(link);
      }
      return;
    }
  }
}

me();
