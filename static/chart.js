// Cash by month. Bars: money in above the axis, money out below; solid = happened, light = still expected,
// hatched amber = overdue (due before today, so counted in this month). Lines: actual position up to
// today (solid), projected position from today on (dashed). Past months are shaded; the FY toggle filters
// the months but every month keeps its real opening position (computed server-side).
(() => {
  const el = document.querySelector("canvas.cashchart");
  if (!el || !window.CURVE || !window.Chart) return;
  const ALL = window.CURVE, FYS = window.CURVE_FY || window.CURVE, card = el.closest(".card");   // FY views may add completed projects
  const K = { good: "#128a55", goodL: "rgba(18,138,85,.3)", navy: "#0E2A47", navyL: "#9fb2c0", cyan: "#00A4C7",
              amber: "#c98a1f", muted: "#7c8894", grid: "#eef2f6", head: "'Space Grotesk', 'Segoe UI', sans-serif" };
  const full = n => (n < -0.5 ? "−$" : "$") + Math.abs(Math.round(n)).toLocaleString("en-AU");
  const short = n => { const a = Math.abs(n), s = n < 0 ? "−" : "";
    return a >= 1e6 ? `${s}$${(a / 1e6).toFixed(1)}M` : a >= 1e3 ? `${s}$${Math.round(a / 1e3)}k` : `${s}$${Math.round(a)}`; };

  function hatch() {
    const c = document.createElement("canvas"); c.width = c.height = 6;
    const x = c.getContext("2d");
    x.fillStyle = "rgba(201,138,31,.25)"; x.fillRect(0, 0, 6, 6);
    x.strokeStyle = K.amber; x.lineWidth = 1.5; x.beginPath();
    x.moveTo(0, 6); x.lineTo(6, 0); x.moveTo(-1, 1); x.lineTo(1, -1); x.moveTo(5, 7); x.lineTo(7, 5); x.stroke();
    return x.createPattern(c, "repeat");
  }
  const HATCH = hatch();
  let rows = ALL;

  function datasets() {
    const bar = (label, data, color, stack, extra) => ({ type: "bar", label, data, backgroundColor: color, stack,
      yAxisID: "y", order: 2, categoryPercentage: .78, barPercentage: .92, ...extra });
    const ov = { borderColor: K.amber, borderWidth: 1 };
    return [
      bar("Received", rows.map(r => r.in_actual), K.good, "in"),
      bar("Expected in", rows.map(r => r.in_future - r.in_overdue), K.goodL, "in"),
      bar("Overdue in", rows.map(r => r.in_overdue), HATCH, "in", ov),
      bar("Paid out", rows.map(r => -r.out_actual), K.navy, "out"),
      bar("Expected out", rows.map(r => -(r.out_future - r.out_overdue)), K.navyL, "out"),
      bar("Overdue out", rows.map(r => -r.out_overdue), HATCH, "out", ov),
      { type: "line", label: "Cash position (actual)", data: rows.map(r => r.actual_position), borderColor: K.cyan,
        backgroundColor: K.cyan, borderWidth: 2.6, pointRadius: 2.5, pointHoverRadius: 4, tension: 0, yAxisID: "y2", order: 1, spanGaps: false },
      // Starts at the last past month so the two lines meet; Undated keeps the projection going.
      { type: "line", label: "Projected position",
        data: rows.map((r, i) => !r.is_past || (rows[i + 1] && !rows[i + 1].is_past) ? r.position : null),
        borderColor: K.cyan, backgroundColor: "#fff", borderDash: [5, 4], borderWidth: 2, pointRadius: 2.5,
        pointBorderWidth: 1.5, pointHoverRadius: 4, tension: 0, yAxisID: "y2", order: 1, spanGaps: false },
    ];
  }

  function range() {
    let lo = 0, hi = 0;
    rows.forEach(r => {
      hi = Math.max(hi, r.in_actual + r.in_future, r.position, r.actual_position ?? 0);
      lo = Math.min(lo, -(r.out_actual + r.out_future), r.position, r.actual_position ?? 0);
    });
    const pad = (hi - lo || 1) * .08;
    return { min: lo < 0 ? lo - pad : 0, max: hi + pad };
  }

  // Shade the past, mark the current month.
  const marks = {
    id: "marks",
    beforeDatasetsDraw(ch) {
      const { ctx, chartArea: a, scales: { x } } = ch;
      const step = rows.length > 1 ? x.getPixelForValue(1) - x.getPixelForValue(0) : a.right - a.left;
      const lastPast = rows.map(r => r.is_past).lastIndexOf(true), cur = rows.findIndex(r => r.is_current);
      ctx.save();
      ctx.font = `700 11.5px ${K.head}`; ctx.textBaseline = "top";
      if (lastPast >= 0) {
        const edge = Math.min(a.right, x.getPixelForValue(lastPast) + step / 2);
        ctx.fillStyle = "rgba(14,42,71,.045)"; ctx.fillRect(a.left, a.top, edge - a.left, a.bottom - a.top);
        ctx.fillStyle = "#9aa7b3"; ctx.textAlign = "left"; ctx.fillText("ACTUAL", a.left + 5, a.top + 4);
      }
      if (cur >= 0) {
        const cx = x.getPixelForValue(cur);
        ctx.fillStyle = "rgba(0,164,199,.09)"; ctx.fillRect(cx - step / 2, a.top, step, a.bottom - a.top);
        ctx.fillStyle = "#0089a8"; ctx.textAlign = "center"; ctx.fillText("TODAY", cx, a.top + 4);
        if (cur < rows.length - 1) { ctx.fillStyle = "#9aa7b3"; ctx.textAlign = "right"; ctx.fillText("PROJECTED", a.right - 5, a.top + 4); }
      } else if (lastPast < 0 && rows.length) {
        ctx.fillStyle = "#9aa7b3"; ctx.textAlign = "right"; ctx.fillText("PROJECTED", a.right - 5, a.top + 4);
      }
      // zero line
      const y0 = ch.scales.y.getPixelForValue(0);
      ctx.strokeStyle = "#c3ced8"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(a.left, y0); ctx.lineTo(a.right, y0); ctx.stroke();
      ctx.restore();
    },
  };

  // Tooltip reconciles the month: opening, in, out, closing.
  function lines(r) {
    const L = [`Opening   ${full(r.opening)}`];
    if (r.in_actual) L.push(`+ Received   ${full(r.in_actual)}`);
    if (r.in_future) L.push(`+ Expected in   ${full(r.in_future)}` + (r.in_overdue ? `  (overdue ${full(r.in_overdue)})` : ""));
    if (r.out_actual) L.push(`− Paid   ${full(r.out_actual)}`);
    if (r.out_future) L.push(`− Expected out   ${full(r.out_future)}` + (r.out_overdue ? `  (overdue ${full(r.out_overdue)})` : ""));
    if (L.length === 1) L.push("No cash movement");
    L.push(`= Closing   ${full(r.position)}`);
    if (r.is_current) L.push("", `Actual so far this month: ${full(r.actual_position)}`);
    return L;
  }
  const when = r => r.month === "Undated" ? "no expected date yet" : r.is_current ? "this month: actual + still expected"
    : r.is_past ? "actual" : "projected";

  const R = range();
  const chart = new Chart(el, {
    data: { labels: rows.map(r => r.label), datasets: datasets() },
    plugins: [marks],
    options: {
      maintainAspectRatio: false, animation: { duration: 250 },
      interaction: { mode: "index", intersect: false },
      layout: { padding: { top: 18 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          filter: it => it.datasetIndex === 0, displayColors: false, backgroundColor: "rgba(14,42,71,.94)",
          titleFont: { family: K.head, weight: "600", size: 13.5 }, bodyFont: { family: K.head, size: 13 }, padding: 10,
          callbacks: { title: it => `${rows[it[0].dataIndex].label} · ${when(rows[it[0].dataIndex])}`,
                       label: it => lines(rows[it.dataIndex]) },
        },
      },
      scales: {
        x: { stacked: true, grid: { display: false },
             ticks: { font: c => ({ family: K.head, size: 12, weight: rows[c.index] && rows[c.index].is_current ? "700" : "500" }),
                      color: c => rows[c.index] && rows[c.index].is_current ? K.navy : "#6b7884", maxRotation: 0, autoSkip: true } },
        y: { stacked: true, min: R.min, max: R.max, grid: { color: K.grid }, border: { display: false },
             ticks: { callback: short, color: "#6b7884", font: { family: K.head, size: 12 }, maxTicksLimit: 7 } },
        y2: { display: false, min: R.min, max: R.max },
      },
    },
  });

  // FY toggle.
  card.querySelectorAll(".fyseg button").forEach(b => b.addEventListener("click", () => {
    const fy = b.dataset.fy;
    card.querySelectorAll(".fyseg button").forEach(x => x.classList.toggle("on", x === b));
    card.querySelectorAll("[data-fyline] [data-fy]").forEach(s => { s.hidden = s.dataset.fy !== fy; });
    show(fy);
  }));
  function show(fy) {
    rows = fy === "All" ? ALL : FYS.filter(r => r.fy === fy);
    const r = range();
    chart.data.labels = rows.map(x => x.label);
    chart.data.datasets = datasets();
    Object.assign(chart.options.scales.y, r); Object.assign(chart.options.scales.y2, r);
    chart.update();
  }
  if (el.dataset.pick && el.dataset.pick !== "All") show(el.dataset.pick);
  window.bhfChart = chart;
})();
