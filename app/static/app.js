// Sim Drop Lab: the page talks only to its own /api (same origin); result files come from Blob Storage through 30-minute read links.
const $ = (id) => document.getElementById(id);
let known = ["bottle_laboratory_a02"];
let watching = null;

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(`/api/${path}`, { headers: { "Content-Type": "application/json" }, redirect: "manual", ...opts });
  } catch { throw new Error("Can't reach the API. Reload the page to sign in again."); }
  if (res.type === "opaqueredirect") throw new Error("Your sign-in has run out. Reload the page to sign in again.");
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
  return body;
}

function text(el, value) { el.textContent = value; return el; }
function make(tag, props = {}, kids = []) {
  const el = Object.assign(document.createElement(tag), props);
  kids.forEach((k) => el.append(k));
  return el;
}

// ---------------------------------------------------------------- who is signed in
async function me() {
  try {
    const who = await api("me");
    const box = $("me");
    if (!who.signed_in) { box.append(make("a", { href: "/.auth/login/aad?post_login_redirect_uri=/", textContent: "Sign in" })); return; }
    box.append(`${who.name}${who.operator ? "" : " (not an operator)"} · `,
      make("a", { href: "/.auth/logout?post_logout_redirect_uri=/", textContent: "Sign out" }));
  } catch { /* the page still works without it */ }
}

// ---------------------------------------------------------------- the worker at home
async function worker() {
  const el = $("worker-state");
  try {
    const w = await api("worker");
    known = w.assets && w.assets.length ? w.assets : known;
    el.className = w.online ? "state-done" : "state-failed";
    text(el, w.online
      ? `Online: ${w.host || "worker"}, last seen ${w.seen_s_ago} s ago${w.busy ? ", running a job" : ""}${w.gpu ? `. GPU: ${w.gpu}` : ""}. Assets ready: ${known.length}.`
      : `Offline${w.seen_s_ago ? ` (last seen ${Math.round(w.seen_s_ago / 60)} min ago)` : ""}. Jobs wait on the queue for up to 2 hours.`);
    document.querySelectorAll("datalist#assets").forEach((d) => d.remove());
    document.body.append(make("datalist", { id: "assets" }, known.map((a) => make("option", { value: a }))));
  } catch (e) { el.className = "state-failed"; text(el, e.message); }
}

// ---------------------------------------------------------------- the form
function addObject(asset = known[0], at = [0, 0, 0.4]) {
  const rows = $("objects");
  if (rows.children.length >= 4) return;
  const num = (v, label) => make("input", { type: "number", step: "0.05", value: v, title: label, ariaLabel: label });
  const row = make("div", { className: "obj" }, [
    make("input", { className: "asset", value: asset, title: "SimReady asset name", ariaLabel: "SimReady asset name", required: true, autocomplete: "off" }),
    num(at[0], "x (m)"), num(at[1], "y (m)"), num(at[2], "height (m)"),
    make("button", { type: "button", className: "x", textContent: "✕", title: "Remove", onclick: () => row.remove() }),
  ]);
  row.firstChild.setAttribute("list", "assets");
  rows.append(row);
}

async function submit(ev) {
  ev.preventDefault();
  const objects = [...$("objects").children].map((row) => {
    const [asset, x, y, z] = row.querySelectorAll("input");
    return { asset: asset.value.trim(), at: [x, y, z].map((i) => Number(i.value)) };
  });
  $("send").disabled = true;
  text($("form-msg"), "Queuing…");
  try {
    const job = await api("jobs", { method: "POST", body: JSON.stringify({ objects, seconds: Number($("seconds").value) }) });
    text($("form-msg"), `Queued as ${job.id}.`);
    show(job.id);
    recent();
  } catch (e) { text($("form-msg"), e.message); }
  finally { $("send").disabled = false; }
}

// ---------------------------------------------------------------- one job
async function show(id) {
  clearTimeout(watching);
  $("view").hidden = false;
  text($("view-id"), id);
  let j;
  try { j = await api(`jobs/${id}`); } catch (e) { text($("view-state"), e.message); return; }
  const state = $("view-state");
  state.className = `state-${j.state}`;
  text(state, j.state === "done" ? `Done in ${j.result?.real_s ?? "?"} s on the worker.` : j.state === "failed" ? `Failed: ${j.error}` : `${j.state[0].toUpperCase()}${j.state.slice(1)}…`);
  const gif = $("view-gif");
  gif.hidden = !j.links?.["sim.gif"];
  if (j.links?.["sim.gif"]) gif.src = j.links["sim.gif"];
  $("view-bodies").replaceChildren(...(j.result?.free_bodies || []).map((b) =>
    make("li", { textContent: `${b.body}: ${b.resting ? "came to rest" : `still moving at ${b.speed_m_s} m/s`} at height ${b.pos_m[2]} m${b.below_floor ? " (fell through the floor!)" : ""}` })));
  $("view-links").replaceChildren(...["sim.mp4", "last.png"].filter((n) => j.links?.[n]).flatMap((n) => [make("a", { href: j.links[n], textContent: n, target: "_blank", rel: "noopener" }), " "]));
  if (j.state === "queued" || j.state === "running") watching = setTimeout(() => show(id), 3000);
  else recent();
}

// ---------------------------------------------------------------- recent jobs
async function recent() {
  try {
    const { jobs } = await api("jobs");
    $("recent").replaceChildren(...jobs.map((j) => make("tr", {}, [
      make("td", {}, [make("a", { textContent: j.id, onclick: () => show(j.id) })]),
      make("td", { className: `state-${j.state}`, textContent: j.state }),
      make("td", { textContent: (j.objects || []).map((o) => o.asset).join(", ") }),
      make("td", { textContent: (j.submitted || "").replace("T", " ").replace("+00:00", " UTC") }),
    ])));
  } catch (e) { $("recent").replaceChildren(make("tr", {}, [make("td", { colSpan: 4, textContent: e.message })])); }
}

$("add").onclick = () => addObject(known[0], [Math.round((Math.random() - 0.5) * 20) / 100, 0, 0.6]);
$("job").onsubmit = submit;
me();
worker().then(() => addObject());
recent();
setInterval(worker, 30000);
