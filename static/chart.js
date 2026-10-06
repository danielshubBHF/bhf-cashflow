// Cash by month, laid out like a standard cashflow chart:
//   bars on the left axis   - money in (green, above zero) and money out (red, below zero) each month;
//                             solid = happened, faded = still expected (overdue items sit in the current month)
//   line on the same axis   - the running cash balance; solid up to today, dashed after. One shared axis so
//                             the balance reads straight off the same $ scale as the bars (zero is zero)
//   a thin "Today" marker. The FY toggle filters months; each month keeps its real balance (server-side).
(() => {
  const el = document.querySelector("canvas.cashchart");
  if (!el || !window.CURVE || !window.Chart) return;
  const ALL = window.CURVE, FYS = window.CURVE_FY || window.CURVE, card = el.closest(".card");
  const K = { in: "#128a55", inL: "rgba(18,138,85,.32)", out: "#d05541", outL: "rgba(208,85,65,.32)",
              line: "#0E2A47", grid: "#eef2f6", tick: "#6b7884", head: "'Space Grotesk', 'Segoe UI', sans-serif" };
  const full = n => (n < -0.5 ? "−$" : "$") + Math.abs(Math.round(n)).toLocaleString("en-AU");
  const short = n => { const a = Math.abs(n), s = n < 0 ? "−" : "";
    return a >= 1e6 ? `${s}$${(a / 1e6).toFixed(1)}M` : a >= 1e3 ? `${s}$${Math.round(a / 1e3)}k` : `${s}$${Math.round(a)}`; };
  let rows = ALL;

  const balance = r => (r.is_past ? r.actual_position ?? r.position : r.position);
  function datasets() {
    const lastPast = rows.map(r => r.is_past).lastIndexOf(true);
    return [
      { type: "bar", label: "Money in", yAxisID: "y", order: 2, categoryPercentage: .7, barPercentage: .9,
        data: rows.map(r => r.in_actual + r.in_future),
        backgroundColor: rows.map(r => (r.is_past ? K.in : K.inL)) },
      { type: "bar", label: "Money out", yAxisID: "y", order: 2, categoryPercentage: .7, barPercentage: .9,
        data: rows.map(r => -(r.out_actual + r.out_future)),
        backgroundColor: rows.map(r => (r.is_past ? K.out : K.outL)) },
      { type: "line", label: "Cash balance", yAxisID: "y", stack: "balance", order: 1, data: rows.map(balance),
        borderColor: K.line, backgroundColor: K.line, borderWidth: 2.5, pointRadius: 3, pointHoverRadius: 5, tension: 0,
        segment: { borderDash: c => (c.p0DataIndex >= lastPast ? [6, 5] : undefined) } },
    ];
  }

  function ranges() {
    let hi = 0, lo = 0, bh = -Infinity, bl = Infinity;
    rows.forEach(r => {
      hi = Math.max(hi, r.in_actual + r.in_future); lo = Math.min(lo, -(r.out_actual + r.out_future));
      const b = balance(r); bh = Math.max(bh, b); bl = Math.min(bl, b);
    });
    const top = Math.max(hi, bh, 0), bot = Math.min(lo, bl, 0), pad = (top - bot || 1) * .08;
    return { y: { min: bot < 0 ? bot - pad : 0, max: top + pad } };
  }

  // "Today" marker and the zero line.
  const marks = {
    id: "marks",
    beforeDatasetsDraw(ch) {
      const { ctx, chartArea: a, scales: { x, y } } = ch;
      const cur = rows.findIndex(r => r.is_current);
      ctx.save();
      const y0 = y.getPixelForValue(0);
      ctx.strokeStyle = "#c3ced8"; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(a.left, y0); ctx.lineTo(a.right, y0); ctx.stroke();
      if (cur >= 0) {
        const cx = x.getPixelForValue(cur);
        ctx.strokeStyle = "#00A4C7"; ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(cx, a.top); ctx.lineTo(cx, a.bottom); ctx.stroke();
        ctx.setLineDash([]); ctx.fillStyle = "#0089a8"; ctx.font = `700 11.5px ${K.head}`; ctx.textAlign = "center"; ctx.textBaseline = "bottom";
        ctx.fillText("Today", cx, a.top - 2);
      }
      ctx.restore();
    },
  };

  function lines(r) {
    const L = [`Opening balance   ${full(r.opening)}`];
    if (r.in_actual) L.push(`+ Received   ${full(r.in_actual)}`);
    if (r.in_future) L.push(`+ Still expected in   ${full(r.in_future)}` + (r.in_overdue ? `  (overdue ${full(r.in_overdue)})` : ""));
    if (r.out_actual) L.push(`− Paid   ${full(r.out_actual)}`);
    if (r.out_future) L.push(`− Still expected out   ${full(r.out_future)}` + (r.out_overdue ? `  (overdue ${full(r.out_overdue)})` : ""));
    if (L.length === 1) L.push("No cash movement");
    L.push(`= Closing balance   ${full(r.position)}`);
    return L;
  }
  const when = r => r.month === "Undated" ? "no expected date yet" : r.is_current ? "this month" : r.is_past ? "actual" : "projected";

  const R = ranges();
  const chart = new Chart(el, {
    data: { labels: rows.map(r => r.label), datasets: datasets() },
    plugins: [marks],
    options: {
      maintainAspectRatio: false, animation: { duration: 250 },
      interaction: { mode: "index", intersect: false },
      layout: { padding: { top: 14 } },
      plugins: {
        legend: { display: true, position: "top", align: "start",
                  labels: { boxWidth: 14, boxHeight: 10, color: "#48555f", font: { family: K.head, size: 12.5 } } },
        tooltip: {
          filter: it => it.datasetIndex === 0, displayColors: false, backgroundColor: "rgba(14,42,71,.94)",
          titleFont: { family: K.head, weight: "600", size: 13.5 }, bodyFont: { family: K.head, size: 13 }, padding: 10,
          callbacks: { title: it => `${rows[it[0].dataIndex].label} · ${when(rows[it[0].dataIndex])}`,
                       label: it => lines(rows[it.dataIndex]) },
        },
      },
      scales: {
        x: { grid: { display: false }, stacked: true,
             ticks: { font: c => ({ family: K.head, size: 12, weight: rows[c.index] && rows[c.index].is_current ? "700" : "500" }),
                      color: K.tick, maxRotation: 0, autoSkip: true } },
        y: { stacked: true, ...R.y, grid: { color: K.grid }, border: { display: false },
             ticks: { callback: short, color: K.tick, font: { family: K.head, size: 12 }, maxTicksLimit: 8 } },
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
    const r = ranges();
    chart.data.labels = rows.map(x => x.label);
    chart.data.datasets = datasets();
    Object.assign(chart.options.scales.y, r.y);
    chart.update();
  }
  if (el.dataset.pick && el.dataset.pick !== "All") show(el.dataset.pick);
  window.bhfChart = chart;
})();
