// Stands in for web_app.py in the browser-only demo. Every answer comes from a saved run of the real code.
(() => {
  const DATA = JSON.parse(document.getElementById("demo-data").textContent);
  const PACE_MS = 80;
  let processed = false, approved = {}, rules = {min_average: 6, allow_failed_subjects: false};

  const key = r => `${(Math.round(Math.min(9, Math.max(5, r.min_average)) * 2) / 2).toFixed(1)}|${r.allow_failed_subjects ? 1 : 0}`;
  const current = () => DATA.settings[key(rules)];
  const reply = (body, status = 200) => new Response(JSON.stringify(body), {status, headers: {"Content-Type": "application/json"}});
  const fail = (detail, status = 400) => reply({detail}, status);

  function counts(rows) {
    const n = d => rows.filter(r => r.decision === d).length;
    return {eligible: n("eligible"), not_eligible: n("not_eligible"), needs_info: n("needs_info"),
            person: rows.filter(r => r.decision === "review" || r.decision === "other").length, total: rows.length,
            unread: rows.filter(r => r.failed).length};
  }
  function state() {
    if (!processed) return {...DATA.empty, rules: {...DATA.empty.rules, ...rules}};
    const setting = current();
    const records = setting.records.map(r => ({...r, approved: !!approved[r.id]}));
    return {...DATA.empty, counts: counts(records), rules: {...DATA.empty.rules, ...rules}, records, places: setting.places};
  }

  function stream() {
    const encoder = new TextEncoder();
    return new ReadableStream({
      async start(controller) {
        for (const line of DATA.stream) {
          controller.enqueue(encoder.encode(JSON.stringify(line) + "\n"));
          await new Promise(r => setTimeout(r, PACE_MS));
        }
        processed = true;
        controller.enqueue(encoder.encode(JSON.stringify({finished: true}) + "\n"));
        controller.close();
      },
    });
  }

  function approve(body) {
    const rows = state().records;
    if (body.id) {
      const index = current().details[body.id];
      const record = rows.find(r => r.id === body.id);
      if (index === undefined || !DATA.details[index].body) return fail("There's no reply to approve for this email.", 404);
      if (record.decision === "not_eligible" && !body.checked) return fail("Please confirm you've checked the not eligible replies.");
      approved[body.id] = true;
      return reply({...state(), approved_now: 1});
    }
    if (body.decision === "not_eligible" && !body.checked) return fail("Please confirm you've checked the not eligible replies.");
    if (!["eligible", "needs_info", "not_eligible"].includes(body.decision)) return fail("This group can't be approved in bulk.");
    let n = 0;
    for (const r of rows) {
      if (r.decision === body.decision && !r.approved && DATA.details[current().details[r.id]].body) { approved[r.id] = true; n++; }
    }
    return reply({...state(), approved_now: n});
  }

  function setRules(body) {
    const before = Object.fromEntries(state().records.map(r => [r.id, current().details[r.id]]));
    const decisions = Object.fromEntries(state().records.map(r => [r.id, r.decision]));
    rules = {min_average: Number(body.min_average), allow_failed_subjects: !!body.allow_failed_subjects};
    let changed = 0;
    for (const r of current().records) {
      if (decisions[r.id] !== r.decision) changed++;
      if (before[r.id] !== current().details[r.id]) delete approved[r.id];
    }
    return reply({...state(), changed});
  }

  function sameForm(body) {
    const a = DATA.sample_check;
    const list = v => [...(v || [])].sort().join("|");
    return body.grade === a.grade && body.failed_status === a.failed_status && (body.failed_subjects || "") === a.failed_subjects
      && body.field === a.field && body.level === a.level && list(body.countries) === list(a.countries);
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, options = {}) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (!url.pathname.includes("/api/")) return realFetch(input, options);
    const path = url.pathname.slice(url.pathname.indexOf("/api/"));
    const body = options.body ? JSON.parse(options.body) : {};
    if (path === "/api/state") return reply(state());
    if (path === "/api/process") {
      if (url.searchParams.get("mode") === "live") return fail("Live Gemini is off in this online demo.");
      processed = false; approved = {};
      return new Response(stream(), {headers: {"Content-Type": "application/x-ndjson"}});
    }
    if (path === "/api/reset") { processed = false; approved = {}; rules = {min_average: 6, allow_failed_subjects: false}; return reply(state()); }
    if (path === "/api/rules") return setRules(body);
    if (path === "/api/approve") return approve(body);
    if (path.startsWith("/api/record/")) {
      const id = decodeURIComponent(path.slice("/api/record/".length));
      const index = processed ? current().details[id] : undefined;
      return index === undefined ? fail("Email not found.", 404) : reply({...DATA.details[index], approved: !!approved[id]});
    }
    if (path === "/api/chat") {
      const saved = DATA.chat[(body.question || "").trim()];
      return reply(saved || {can_answer: false, faq_id: null, language: "English",
        reply: "This online demo answers only the sample questions above. In the full app, Gemini answers any question from the office FAQ.",
        english: "This online demo answers only the sample questions above."});
    }
    if (path === "/api/advice/read") {
      return (body.text || "").trim() === DATA.sample_text.trim() ? reply(DATA.sample_form)
        : reply({error: "This online demo can read only the sample email. Press \"Use a sample email\" to try it."});
    }
    if (path === "/api/advice/check") {
      return sameForm(body) ? reply({...DATA.sample_advice})
        : fail("This online demo can check only the sample student's details. The full app checks any student.");
    }
    if (path === "/api/documents") return reply(DATA.documents);
    if (path === "/api/cheat") return reply(processed ? DATA.cheat : {found: false});
    return fail("Not available in this online demo.", 404);
  };
})();
