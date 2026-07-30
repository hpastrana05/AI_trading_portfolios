document.addEventListener("DOMContentLoaded", () => {
  const startForm = document.querySelector("form[action='/start']");
  if (startForm) {
    startForm.addEventListener("submit", () => {
      const button = startForm.querySelector("button[type='submit']");
      if (button) {
        button.disabled = true;
        button.textContent = "Starting…";
      }
    });
  }

  const runOnceForm = document.querySelector("form[action='/run-once']");
  if (runOnceForm) {
    runOnceForm.addEventListener("submit", () => {
      const button = runOnceForm.querySelector("button[type='submit']");
      if (button) {
        button.disabled = true;
        button.textContent = "Running…";
      }
    });
  }

  const canvas = document.getElementById("performance-chart");
  if (!canvas) {
    return;
  }

  const rangeButtons = Array.from(document.querySelectorAll(".range-btn"));
  const emptyHint = document.getElementById("performance-empty");
  const metricPnl = document.getElementById("metric-pnl");
  const metricPnlPct = document.getElementById("metric-pnl-pct");
  const metricDd = document.getElementById("metric-dd");
  const ctx = canvas.getContext("2d");
  const hasChartLib = typeof Chart !== "undefined" && !!ctx;

  let chart;

  function formatSigned(value, digits = 2) {
    const sign = value >= 0 ? "+" : "";
    return `${sign}${Number(value).toFixed(digits)}`;
  }

  function pickClass(value) {
    return value >= 0 ? "pnl-pos" : "pnl-neg";
  }

  function setMetric(el, value, suffix = "", digits = 2) {
    if (!el) return;
    el.textContent = `${formatSigned(value, digits)}${suffix}`;
    el.classList.remove("pnl-pos", "pnl-neg");
    el.classList.add(pickClass(value));
  }

  function draw(data) {
    const points = data.points || [];
    if (points.length < 2) {
      if (chart) chart.destroy();
      canvas.style.display = "none";
      if (emptyHint) emptyHint.style.display = "block";
      setMetric(metricPnl, data.metrics?.total_pnl || 0);
      setMetric(metricPnlPct, data.metrics?.total_pnl_pct || 0, "%", 2);
      setMetric(metricDd, -(data.metrics?.max_dd_pct || 0), "%", 2);
      return;
    }

    if (!hasChartLib) {
      canvas.style.display = "none";
      if (emptyHint) {
        emptyHint.style.display = "block";
        emptyHint.textContent = "Chart library could not be loaded, but metrics are still available.";
      }
      setMetric(metricPnl, data.metrics.total_pnl || 0);
      setMetric(metricPnlPct, data.metrics.total_pnl_pct || 0, "%", 2);
      setMetric(metricDd, -(data.metrics.max_dd_pct || 0), "%", 2);
      return;
    }

    canvas.style.display = "block";
    if (emptyHint) emptyHint.style.display = "none";

    setMetric(metricPnl, data.metrics.total_pnl || 0);
    setMetric(metricPnlPct, data.metrics.total_pnl_pct || 0, "%", 2);
    setMetric(metricDd, -(data.metrics.max_dd_pct || 0), "%", 2);

    const labels = points.map((p) => new Date(p.timestamp).toLocaleString());
    const pnl = points.map((p) => p.pnl);
    const pnlPct = points.map((p) => p.pnl_pct);

    if (chart) chart.destroy();
    chart = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Total P&L",
            data: pnl,
            borderColor: "#34d399",
            backgroundColor: "rgba(52,211,153,0.15)",
            yAxisID: "yMoney",
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.25,
          },
          {
            label: "Total P&L (%)",
            data: pnlPct,
            borderColor: "#60a5fa",
            backgroundColor: "rgba(96,165,250,0.12)",
            yAxisID: "yPct",
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.25,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        plugins: {
          legend: {
            labels: { color: "#e8edf4" },
          },
        },
        scales: {
          x: {
            ticks: { color: "#8b9bb4", maxTicksLimit: 8 },
            grid: { color: "rgba(45,59,82,0.35)" },
          },
          yMoney: {
            position: "left",
            ticks: { color: "#34d399" },
            grid: { color: "rgba(45,59,82,0.35)" },
          },
          yPct: {
            position: "right",
            ticks: {
              color: "#60a5fa",
              callback: (value) => `${value}%`,
            },
            grid: { drawOnChartArea: false },
          },
        },
      },
    });
  }

  async function loadPerformance(rangeKey) {
    rangeButtons.forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.range === rangeKey);
    });
    try {
      const res = await fetch(`/api/performance?range=${encodeURIComponent(rangeKey)}`);
      if (!res.ok) {
        throw new Error(`API error ${res.status}`);
      }
      const data = await res.json();
      draw(data);
    } catch (err) {
      if (chart) chart.destroy();
      canvas.style.display = "none";
      if (emptyHint) {
        emptyHint.style.display = "block";
        emptyHint.textContent = "Could not load performance data yet.";
      }
      setMetric(metricPnl, 0);
      setMetric(metricPnlPct, 0, "%", 2);
      setMetric(metricDd, 0, "%", 2);
    }
  }

  rangeButtons.forEach((btn) => {
    btn.addEventListener("click", () => loadPerformance(btn.dataset.range || "max"));
  });

  loadPerformance("1d");
});
