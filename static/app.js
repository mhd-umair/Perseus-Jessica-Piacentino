const rankBySelect = document.querySelector("#rankBy");
const metricNote = document.querySelector("#metricNote");
const refreshButton = document.querySelector("#refreshButton");
const partsTableSelect = document.querySelector("#partsTableSelect");
const partsTableSearch = document.querySelector("#partsTableSearch");
const partsTableNote = document.querySelector("#partsTableNote");
const partsTableBody = document.querySelector("#partsTableBody");
const partsTableToggle = document.querySelector("#partsTableToggle");
const partTrendPanel = document.querySelector("#partTrendPanel");
const partTrendTitle = document.querySelector("#partTrendTitle");
const partTrendNote = document.querySelector("#partTrendNote");
const startMonthRange = document.querySelector("#startMonthRange");
const endMonthRange = document.querySelector("#endMonthRange");
const inventoryStartMonthRange = document.querySelector("#inventoryStartMonthRange");
const inventoryEndMonthRange = document.querySelector("#inventoryEndMonthRange");
const rangeLabel = document.querySelector("#rangeLabel");
const inventoryRangeLabel = document.querySelector("#inventoryRangeLabel");
const kpiGrid = document.querySelector("#kpiGrid");
const partsKpiGrid = document.querySelector("#partsKpiGrid");
const leaderboardTable = document.querySelector("#leaderboardTable");
const retentionTable = document.querySelector("#retentionTable");
const concentrationSummary = document.querySelector("#concentrationSummary");
const detailTitle = document.querySelector("#detailTitle");
const detailContent = document.querySelector("#detailContent");
const chatLauncher = document.querySelector("#chatLauncher");
const chatPanel = document.querySelector("#chatPanel");
const chatClose = document.querySelector("#chatClose");
const chatMessages = document.querySelector("#chatMessages");
const chatForm = document.querySelector("#chatForm");
const chatInput = document.querySelector("#chatInput");
const chatPopover = document.querySelector("#chatPopover");
const runnerTabs = document.querySelectorAll(".runner-tab");
const customerView = document.querySelector("#customerView");
const inventoryView = document.querySelector("#inventoryView");

let dashboardData = null;
let partsKpiData = null;
let partsTableData = null;
let selectedCustomerId = null;
let leaderboardChart = null;
let trendChart = null;
let concentrationChart = null;
let partTrendChart = null;
let reloadTimer = null;
let partsSort = { key: "PartsRevenue", direction: "desc" };
let partsTableExpanded = false;
let selectedPartId = null;

const minMonth = "2017-04";
const maxMonth = "2026-03";
const cagrMinStartMonth = "2018-03";
const minMonthIndex = monthToIndex(minMonth);
const maxMonthIndex = monthToIndex(maxMonth);

const rankLabels = {
  total_revenue: "Ranking by total posted invoice revenue in the selected month range.",
  invoice_count: "Ranking by posted invoice count in the selected month range.",
  average_invoice_value: "Ranking by average posted invoice value in the selected month range.",
  latest_year_revenue: "Ranking by posted invoice revenue in the selected ending month.",
  cagr:
    "Ranking by TTM CAGR from the selected starting month to ending month. Customers need at least $10,000 TTM revenue and 3 invoices in both TTM windows.",
  revenue_growth_dollars: "Ranking by ending-month revenue minus starting-month revenue.",
};

const rankValueKeys = {
  total_revenue: "TotalRevenue",
  invoice_count: "InvoiceCount",
  average_invoice_value: "AverageInvoiceValue",
  latest_year_revenue: "EndRevenue",
  cagr: "Cagr",
  revenue_growth_dollars: "RevenueGrowthDollars",
};

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const moneyDetailed = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});

const number = new Intl.NumberFormat("en-US");

function formatMoney(value) {
  return money.format(Number(value || 0));
}

function formatMoneyDetailed(value) {
  return moneyDetailed.format(Number(value || 0));
}

function formatNumber(value) {
  return number.format(Number(value || 0));
}

function formatPercent(value) {
  return value === null || value === undefined ? "N/A" : `${Number(value).toFixed(2)}%`;
}

function shortDate(value) {
  if (!value) return "N/A";
  return String(value).slice(0, 10);
}

function monthToIndex(month) {
  const [year, monthNumber] = month.split("-").map(Number);
  return year * 12 + monthNumber - 1;
}

function indexToMonth(index) {
  const year = Math.floor(index / 12);
  const monthNumber = (index % 12) + 1;
  return `${year}-${String(monthNumber).padStart(2, "0")}`;
}

function monthOffsetToValue(offset) {
  return indexToMonth(minMonthIndex + Number(offset));
}

function monthValueToOffset(month) {
  return monthToIndex(month) - minMonthIndex;
}

function formatMonth(month) {
  const [year, monthNumber] = month.split("-").map(Number);
  return new Date(year, monthNumber - 1, 1).toLocaleDateString("en-US", {
    month: "short",
    year: "numeric",
  });
}

function selectedMonths() {
  let startOffset = Number(startMonthRange.value);
  let endOffset = Number(endMonthRange.value);
  if (startOffset > endOffset) {
    [startOffset, endOffset] = [endOffset, startOffset];
  }
  return {
    startMonth: monthOffsetToValue(startOffset),
    endMonth: monthOffsetToValue(endOffset),
  };
}

function updateRangeLabel() {
  const { startMonth, endMonth } = selectedMonths();
  const label = `${formatMonth(startMonth)}-${formatMonth(endMonth)}`;
  rangeLabel.textContent = label;
  inventoryRangeLabel.textContent = label;
  updateCagrAvailability(startMonth);
}

function syncRangeControls(sourceStart, sourceEnd) {
  const startValue = sourceStart.value;
  const endValue = sourceEnd.value;
  [startMonthRange, inventoryStartMonthRange].forEach((input) => {
    input.value = startValue;
  });
  [endMonthRange, inventoryEndMonthRange].forEach((input) => {
    input.value = endValue;
  });
}

function updateCagrAvailability(startMonth) {
  const cagrOption = rankBySelect.querySelector('option[value="cagr"]');
  const cagrDisabled = monthToIndex(startMonth) < monthToIndex(cagrMinStartMonth);
  cagrOption.disabled = cagrDisabled;
  if (cagrDisabled && rankBySelect.value === "cagr") {
    rankBySelect.value = "total_revenue";
  }
  cagrOption.textContent = cagrDisabled ? "TTM CAGR % (available Mar 2018+)" : "TTM CAGR %";
}

function scheduleDashboardReload(sourceStart = startMonthRange, sourceEnd = endMonthRange) {
  syncRangeControls(sourceStart, sourceEnd);
  updateRangeLabel();
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(() => {
    loadDashboard().catch(showError);
  }, 250);
}

async function fetchJson(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Request failed");
  }
  return payload;
}

async function loadDashboard() {
  setLoading();
  const rankBy = rankBySelect.value;
  const { startMonth, endMonth } = selectedMonths();
  metricNote.textContent = rankLabels[rankBy];
  const params = new URLSearchParams({
    rankBy,
    limit: "20",
    startMonth,
    endMonth,
  });
  const partsParams = new URLSearchParams({ startMonth, endMonth });
  const partsTableParams = new URLSearchParams({ startMonth, endMonth, limit: "75" });
  [dashboardData, partsKpiData, partsTableData] = await Promise.all([
    fetchJson(`/api/customer-dashboard?${params.toString()}`),
    fetchJson(`/api/parts-sales-kpis?${partsParams.toString()}`),
    fetchJson(`/api/parts-sales-tables?${partsTableParams.toString()}`),
  ]);
  selectedCustomerId = dashboardData.leaderboard[0]?.CustomerId ?? null;
  renderDashboard();
  if (selectedCustomerId) {
    await loadCustomerDetail(selectedCustomerId);
  }
}

function setLoading() {
  kpiGrid.innerHTML = `<div class="kpi-card"><span>Loading</span><strong>Customer metrics</strong><small>Reading SQLite data...</small></div>`;
  partsKpiGrid.innerHTML = `<div class="kpi-card"><span>Loading</span><strong>Parts metrics</strong><small>Reading parts sales data...</small></div>`;
  partsTableBody.innerHTML = `<tr><td colspan="10">Loading parts table...</td></tr>`;
  leaderboardTable.innerHTML = "";
  retentionTable.innerHTML = "";
}

function renderDashboard() {
  renderKpis();
  renderLeaderboard();
  renderTrend();
  renderConcentration();
  renderRetention();
  renderPartsKpis();
  renderPartsTable();
  if (selectedPartId) {
    loadPartTrend(selectedPartId).catch(showError);
  }
}

function renderKpis() {
  const concentration = dashboardData.concentration;
  const topCustomer = dashboardData.leaderboard[0];
  const totalInvoices = dashboardData.leaderboard.reduce(
    (sum, customer) => sum + Number(customer.InvoiceCount || 0),
    0,
  );
  const top20Revenue = dashboardData.leaderboard.reduce(
    (sum, customer) => sum + Number(customer.TotalRevenue || 0),
    0,
  );
  const top20Share = concentration.totalRevenue
    ? (top20Revenue / concentration.totalRevenue) * 100
    : 0;

  const cards = [
    {
      label: "Posted Revenue",
      value: formatMoney(concentration.totalRevenue),
      note: `All customers, ${formatMonth(dashboardData.settings.startMonth)}-${formatMonth(dashboardData.settings.endMonth)}`,
    },
    {
      label: "Top Customer",
      value: topCustomer ? formatMoney(topCustomer.TotalRevenue) : "N/A",
      note: topCustomer ? topCustomer.CustomerName : "No posted revenue",
    },
    {
      label: "Top 20 Revenue Share",
      value: `${top20Share.toFixed(1)}%`,
      note: `${formatMoney(top20Revenue)} of posted revenue`,
    },
    {
      label: "Top 20 Invoice Count",
      value: formatNumber(totalInvoices),
      note: "Posted invoices for displayed customers",
    },
  ];

  kpiGrid.innerHTML = cards
    .map(
      (card) => `
        <article class="kpi-card">
          <span>${card.label}</span>
          <strong>${card.value}</strong>
          <small>${card.note}</small>
        </article>
      `,
    )
    .join("");
}

function renderPartsKpis() {
  const cards = [
    {
      label: "Parts Revenue",
      value: formatMoneyDetailed(partsKpiData.partsRevenue),
      note: "Posted parts sales revenue",
    },
    {
      label: "Quantity Sold",
      value: formatNumber(partsKpiData.quantitySold),
      note: "Total part quantity sold",
    },
    {
      label: "Estimated Gross Margin",
      value: formatMoneyDetailed(partsKpiData.estimatedGrossMargin),
      note: "Net sales less AvgCost x Qty",
    },
    {
      label: "Estimated Margin %",
      value: formatPercent(partsKpiData.estimatedMarginPercent),
      note: "Estimated margin divided by parts revenue",
    },
    {
      label: "Parts Invoice Count",
      value: formatNumber(partsKpiData.partsInvoiceCount),
      note: "Posted invoices containing parts",
    },
    {
      label: "Avg Parts Revenue / Invoice",
      value: formatMoneyDetailed(partsKpiData.averagePartsRevenuePerInvoice),
      note: "Parts revenue divided by parts invoices",
    },
  ];

  partsKpiGrid.innerHTML = cards
    .map(
      (card) => `
        <article class="kpi-card">
          <span>${card.label}</span>
          <strong>${card.value}</strong>
          <small>${card.note}</small>
        </article>
      `,
    )
    .join("");
}

function renderPartsTable() {
  const tableKey = partsTableSelect.value;
  const rows = [...(partsTableData?.[tableKey] || [])];
  const search = partsTableSearch.value.trim().toLowerCase();
  const tableLabels = {
    topByRevenue: "Parts ranked by posted parts revenue in the selected period.",
    topByQuantity: "Parts ranked by quantity sold in the selected period.",
    missingStockingPolicy:
      "Sold parts with no configured min/max stocking policy in PartLocation.",
  };
  partsTableNote.textContent = tableLabels[tableKey];

  const filteredRows = rows.filter((row) => {
    if (!search) return true;
    return `${row.PartNo || ""} ${row.Description || ""}`.toLowerCase().includes(search);
  });

  filteredRows.sort((a, b) => {
    const left = a[partsSort.key] ?? "";
    const right = b[partsSort.key] ?? "";
    const multiplier = partsSort.direction === "asc" ? 1 : -1;
    if (typeof left === "number" && typeof right === "number") {
      return (left - right) * multiplier;
    }
    return String(left).localeCompare(String(right)) * multiplier;
  });

  if (!filteredRows.length) {
    partsTableBody.innerHTML = `<tr><td colspan="10">No parts found for this view.</td></tr>`;
    partsTableToggle.hidden = true;
    return;
  }

  const visibleRows = partsTableExpanded ? filteredRows : filteredRows.slice(0, 10);
  partsTableToggle.hidden = filteredRows.length <= 10;
  partsTableToggle.textContent = partsTableExpanded ? "Show less" : "Show more";

  partsTableBody.innerHTML = visibleRows
    .map(
      (row) => `
        <tr>
          <td class="customer-cell">
            <button class="link-button" type="button" data-part-id="${row.PartId}">
              ${row.PartNo || "Unknown"}
            </button>
            <small>ID ${row.PartId}</small>
          </td>
          <td>${row.Description || "No description"}</td>
          <td class="number">${formatNumber(row.QuantitySold)}</td>
          <td class="number">${formatMoneyDetailed(row.PartsRevenue)}</td>
          <td class="number">${formatMoneyDetailed(row.EstimatedGrossMargin)}</td>
          <td class="number">${formatPercent(row.EstimatedMarginPercent)}</td>
          <td class="number">${formatNumber(row.InvoiceCount)}</td>
          <td class="number">${formatNumber(row.ActiveMonths)}</td>
          <td>${shortDate(row.LastSoldDate)}</td>
          <td><span class="tag">${row.StockingPolicyStatus}</span></td>
        </tr>
      `,
    )
    .join("");

  partsTableBody.querySelectorAll("[data-part-id]").forEach((button) => {
    button.addEventListener("click", () => {
      loadPartTrend(Number(button.dataset.partId)).catch(showError);
    });
  });
}

async function loadPartTrend(partId) {
  selectedPartId = partId;
  const { startMonth, endMonth } = selectedMonths();
  const params = new URLSearchParams({
    partId: String(partId),
    startMonth,
    endMonth,
  });
  const trend = await fetchJson(`/api/part-trend?${params.toString()}`);
  renderPartTrend(trend);
}

function renderPartTrend(trend) {
  if (!trend.part) {
    partTrendPanel.hidden = false;
    partTrendTitle.textContent = "Part Sales Trend";
    partTrendNote.textContent = "No sales found for this part in the selected period.";
    if (partTrendChart) partTrendChart.destroy();
    return;
  }

  const byPeriod = new Map(trend.series.map((row) => [row.Period, row]));
  const revenueData = trend.periods.map((period) => Number(byPeriod.get(period)?.PartsRevenue || 0));
  const marginData = trend.periods.map((period) =>
    Number(byPeriod.get(period)?.EstimatedGrossMargin || 0),
  );

  partTrendPanel.hidden = false;
  partTrendTitle.textContent = `${trend.part.partNo} Sales Trend`;
  partTrendNote.textContent = trend.part.description;

  if (partTrendChart) partTrendChart.destroy();
  partTrendChart = new Chart(document.querySelector("#partTrendChart"), {
    data: {
      labels: trend.periods.map(formatMonth),
      datasets: [
        {
          type: "bar",
          label: "Parts Revenue",
          data: revenueData,
          backgroundColor: "#1e5eff",
          borderRadius: 6,
          yAxisID: "y",
        },
        {
          type: "line",
          label: "Estimated Margin",
          data: marginData,
          borderColor: "#18a058",
          backgroundColor: "#18a058",
          tension: 0.25,
          yAxisID: "y",
        },
      ],
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        tooltip: {
          callbacks: {
            label: (context) => `${context.dataset.label}: ${formatMoneyDetailed(context.raw)}`,
          },
        },
      },
      scales: {
        y: { ticks: { callback: (value) => formatMoney(value) } },
      },
    },
  });

  partTrendPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderLeaderboard() {
  const rankBy = rankBySelect.value;
  const valueKey = rankValueKeys[rankBy];
  const labels = dashboardData.leaderboard.map((row) => row.CustomerName);
  const data = dashboardData.leaderboard.map((row) => Number(row[valueKey] || 0));

  if (leaderboardChart) leaderboardChart.destroy();
  leaderboardChart = new Chart(document.querySelector("#leaderboardChart"), {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: rankBySelect.options[rankBySelect.selectedIndex].text,
          data,
          borderRadius: 8,
          backgroundColor: "#1e5eff",
        },
      ],
    },
    options: {
      indexAxis: "y",
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (context) => formatRankValue(context.raw, rankBy),
          },
        },
      },
      scales: {
        x: { ticks: { callback: (value) => axisValue(value, rankBy) } },
      },
      onClick: (_event, elements) => {
        if (!elements.length) return;
        const customer = dashboardData.leaderboard[elements[0].index];
        selectCustomer(customer.CustomerId);
      },
    },
  });

  leaderboardTable.innerHTML = dashboardData.leaderboard
    .map(
      (row, index) => `
        <tr data-customer-id="${row.CustomerId}">
          <td>${index + 1}</td>
          <td class="customer-cell">
            <strong>${row.CustomerName}</strong>
            <small>${row.CustomerNo || "No customer number"}</small>
          </td>
          <td class="number">${formatMoneyDetailed(row.TotalRevenue)}</td>
          <td class="number">${formatNumber(row.InvoiceCount)}</td>
          <td class="number">${formatMoneyDetailed(row.AverageInvoiceValue)}</td>
          <td class="number">${formatMoneyDetailed(row.EndRevenue)}</td>
          <td class="number">${formatPercent(row.Cagr)}</td>
          <td class="number">${formatMoneyDetailed(row.RevenueGrowthDollars)}</td>
        </tr>
      `,
    )
    .join("");

  leaderboardTable.querySelectorAll("tr").forEach((row) => {
    row.addEventListener("click", () => selectCustomer(Number(row.dataset.customerId)));
  });
}

function renderTrend(customerId = selectedCustomerId) {
  const periods = dashboardData.periods;
  const customers = customerId
    ? dashboardData.leaderboard.filter((customer) => customer.CustomerId === customerId)
    : dashboardData.leaderboard.slice(0, 5);

  const datasets = customers.map((customer, index) => {
    const periodRows = dashboardData.periodMetrics.filter(
      (row) => row.CustomerId === customer.CustomerId,
    );
    const byPeriod = new Map(periodRows.map((row) => [row.Period, Number(row.Revenue || 0)]));
    return {
      label: customer.CustomerName,
      data: periods.map((period) => byPeriod.get(period) || 0),
      borderColor: chartColors[index % chartColors.length],
      backgroundColor: chartColors[index % chartColors.length],
      tension: 0.28,
    };
  });

  if (trendChart) trendChart.destroy();
  trendChart = new Chart(document.querySelector("#trendChart"), {
    type: "line",
    data: { labels: periods.map(formatMonth), datasets },
    options: {
      maintainAspectRatio: false,
      plugins: {
        tooltip: {
          callbacks: {
            label: (context) => `${context.dataset.label}: ${formatMoneyDetailed(context.raw)}`,
          },
        },
      },
      scales: {
        y: { ticks: { callback: (value) => formatMoney(value) } },
      },
    },
  });
}

function renderConcentration() {
  const buckets = dashboardData.concentration.buckets;
  if (concentrationChart) concentrationChart.destroy();
  concentrationChart = new Chart(document.querySelector("#concentrationChart"), {
    type: "doughnut",
    data: {
      labels: buckets.map((bucket) => bucket.Label),
      datasets: [
        {
          data: buckets.map((bucket) => bucket.Revenue),
          backgroundColor: ["#1e5eff", "#18a058", "#f2a900", "#8091aa"],
        },
      ],
    },
    options: {
      maintainAspectRatio: false,
      plugins: {
        tooltip: {
          callbacks: {
            label: (context) => {
              const bucket = buckets[context.dataIndex];
              return `${bucket.Label}: ${formatMoneyDetailed(bucket.Revenue)} (${bucket.Share}%)`;
            },
          },
        },
      },
    },
  });

  concentrationSummary.innerHTML = buckets
    .map(
      (bucket) => `
        <div class="summary-row">
          <span>${bucket.Label}</span>
          <strong>${formatMoney(bucket.Revenue)} <span class="muted">(${bucket.Share}%)</span></strong>
        </div>
      `,
    )
    .join("");
}

function renderRetention() {
  retentionTable.innerHTML = dashboardData.retention
    .map(
      (row) => `
        <tr data-customer-id="${row.CustomerId}">
          <td class="customer-cell">
            <strong>${row.CustomerName}</strong>
            <small>${row.CustomerNo || "No customer number"}</small>
          </td>
          <td class="number">${formatMoneyDetailed(row.TotalRevenue)}</td>
          <td class="number">${formatMoneyDetailed(row.StartPeriodRevenue)}</td>
          <td class="number">${formatMoneyDetailed(row.EndPeriodRevenue)}</td>
          <td class="number">${formatMoneyDetailed(row.RevenueGrowthDollars)}</td>
          <td>${shortDate(row.LastPurchaseDate)}</td>
        </tr>
      `,
    )
    .join("");

  retentionTable.querySelectorAll("tr").forEach((row) => {
    row.addEventListener("click", () => selectCustomer(Number(row.dataset.customerId)));
  });
}

async function selectCustomer(customerId) {
  selectedCustomerId = customerId;
  renderTrend(customerId);
  await loadCustomerDetail(customerId);
  document.querySelector("#customerDetailPanel").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadCustomerDetail(customerId) {
  detailContent.innerHTML = `<div class="empty-state">Loading customer detail...</div>`;
  const { startMonth, endMonth } = selectedMonths();
  const params = new URLSearchParams({
    customerId: String(customerId),
    startMonth,
    endMonth,
  });
  const detail = await fetchJson(`/api/customer-detail?${params.toString()}`);
  renderCustomerDetail(detail);
}

function renderCustomerDetail(detail) {
  const summary = detail.summary;
  if (!summary) {
    detailTitle.textContent = "Customer Not Found";
    detailContent.innerHTML = `<div class="empty-state">No posted invoices found for this customer.</div>`;
    return;
  }

  detailTitle.textContent = summary.CustomerName;
  const renderInvoiceRows = (invoices) =>
    invoices
      .map(
        (row) => `
          <tr>
            <td>${row.InvoiceNumber}</td>
            <td>${shortDate(row.ActivityDate)}</td>
            <td><span class="tag">${row.InvoiceType || "unknown"}</span></td>
            <td><span class="tag">${row.PaymentStatus || "Unknown"}</span></td>
            <td class="number">${formatMoneyDetailed(row.TotalInvoice)}</td>
          </tr>
        `,
      )
      .join("");
  const firstFiveInvoices = detail.recentInvoices.slice(0, 5);
  const hasMoreInvoices = detail.recentInvoices.length > firstFiveInvoices.length;

  detailContent.innerHTML = `
    <div class="detail-grid">
      <div class="detail-stat">
        <span>Posted Revenue</span>
        <strong>${formatMoneyDetailed(summary.LifetimePostedRevenue)}</strong>
      </div>
      <div class="detail-stat">
        <span>Posted Invoices</span>
        <strong>${formatNumber(summary.LifetimePostedInvoiceCount)}</strong>
      </div>
      <div class="detail-stat">
        <span>Last Purchase</span>
        <strong>${shortDate(summary.LastPurchaseDate)}</strong>
      </div>
    </div>

    <div class="detail-section-heading">
      <h3>Recent Invoices</h3>
      ${
        hasMoreInvoices
          ? `<button id="toggleInvoices" type="button" data-expanded="false">Show more</button>`
          : ""
      }
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Invoice</th>
            <th>Date</th>
            <th>Type</th>
            <th>Payment Status</th>
            <th class="number">Total</th>
          </tr>
        </thead>
        <tbody id="invoiceRows">${renderInvoiceRows(firstFiveInvoices)}</tbody>
      </table>
    </div>
  `;

  const toggleInvoicesButton = document.querySelector("#toggleInvoices");
  if (toggleInvoicesButton) {
    toggleInvoicesButton.addEventListener("click", () => {
      const isExpanded = toggleInvoicesButton.dataset.expanded === "true";
      document.querySelector("#invoiceRows").innerHTML = renderInvoiceRows(
        isExpanded ? firstFiveInvoices : detail.recentInvoices,
      );
      toggleInvoicesButton.dataset.expanded = String(!isExpanded);
      toggleInvoicesButton.textContent = isExpanded ? "Show more" : "Show less";
    });
  }
}

function formatRankValue(value, rankBy) {
  if (rankBy === "invoice_count") return formatNumber(value);
  if (rankBy === "cagr") return formatPercent(value);
  return formatMoneyDetailed(value);
}

function axisValue(value, rankBy) {
  if (rankBy === "invoice_count") return formatNumber(value);
  if (rankBy === "cagr") return `${value}%`;
  return formatMoney(value);
}

function showError(error) {
  kpiGrid.innerHTML = "";
  document.querySelector(".page").insertAdjacentHTML(
    "afterbegin",
    `<div class="error"><strong>Dashboard error:</strong> ${error.message}</div>`,
  );
}

function appendChatMessage(role, message) {
  const bubble = document.createElement("div");
  bubble.className = `chat-message ${role}`;
  bubble.textContent = message;
  chatMessages.appendChild(bubble);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return bubble;
}

async function sendChatQuestion(question) {
  const { startMonth, endMonth } = selectedMonths();
  const params = new URLSearchParams({
    q: question,
    startMonth,
    endMonth,
    customerId: selectedCustomerId ? String(selectedCustomerId) : "0",
  });
  const response = await fetchJson(`/api/customer-chat?${params.toString()}`);
  return response.answer;
}

function openChat() {
  chatPanel.hidden = false;
  chatLauncher.hidden = true;
  chatPopover.hidden = true;
  chatInput.focus();
}

function closeChat() {
  chatPanel.hidden = true;
  chatLauncher.hidden = false;
}

function showChatPopover() {
  if (!chatPanel.hidden) return;
  chatPopover.hidden = false;
}

function setDashboardView(view) {
  const isInventory = view === "inventory";
  customerView.hidden = isInventory;
  inventoryView.hidden = !isInventory;
  runnerTabs.forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.view === view);
  });
}

const chartColors = ["#1e5eff", "#18a058", "#f2a900", "#b85c00", "#7c3aed"];

rankBySelect.addEventListener("change", () => {
  loadDashboard().catch(showError);
});

refreshButton.addEventListener("click", () => {
  loadDashboard().catch(showError);
});

partsTableSelect.addEventListener("change", () => {
  const defaultSorts = {
    topByRevenue: "PartsRevenue",
    topByQuantity: "QuantitySold",
    missingStockingPolicy: "QuantitySold",
  };
  partsSort = { key: defaultSorts[partsTableSelect.value], direction: "desc" };
  partsTableExpanded = false;
  renderPartsTable();
});
partsTableSearch.addEventListener("input", () => {
  partsTableExpanded = false;
  renderPartsTable();
});
partsTableToggle.addEventListener("click", () => {
  partsTableExpanded = !partsTableExpanded;
  renderPartsTable();
});
document.querySelectorAll("[data-parts-sort]").forEach((header) => {
  header.addEventListener("click", () => {
    const key = header.dataset.partsSort;
    const isSameKey = partsSort.key === key;
    partsSort = {
      key,
      direction: isSameKey && partsSort.direction === "desc" ? "asc" : "desc",
    };
    partsTableExpanded = false;
    renderPartsTable();
  });
});

chatLauncher.addEventListener("click", openChat);
chatClose.addEventListener("click", closeChat);
chatPopover.addEventListener("click", openChat);
runnerTabs.forEach((tab) => {
  tab.addEventListener("click", () => setDashboardView(tab.dataset.view));
});
chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = chatInput.value.trim();
  if (!question) return;
  chatInput.value = "";
  appendChatMessage("user", question);
  const loadingMessage = appendChatMessage("assistant", "Checking the customer data...");
  try {
    loadingMessage.textContent = await sendChatQuestion(question);
  } catch (error) {
    loadingMessage.textContent = `I could not answer that yet: ${error.message}`;
  }
});

setTimeout(showChatPopover, 5000);

startMonthRange.min = "0";
startMonthRange.max = String(maxMonthIndex - minMonthIndex);
startMonthRange.value = String(monthValueToOffset(minMonth));
endMonthRange.min = "0";
endMonthRange.max = String(maxMonthIndex - minMonthIndex);
endMonthRange.value = String(monthValueToOffset(maxMonth));
inventoryStartMonthRange.min = "0";
inventoryStartMonthRange.max = String(maxMonthIndex - minMonthIndex);
inventoryStartMonthRange.value = String(monthValueToOffset(minMonth));
inventoryEndMonthRange.min = "0";
inventoryEndMonthRange.max = String(maxMonthIndex - minMonthIndex);
inventoryEndMonthRange.value = String(monthValueToOffset(maxMonth));
startMonthRange.addEventListener("input", () =>
  scheduleDashboardReload(startMonthRange, endMonthRange),
);
endMonthRange.addEventListener("input", () => scheduleDashboardReload(startMonthRange, endMonthRange));
inventoryStartMonthRange.addEventListener("input", () =>
  scheduleDashboardReload(inventoryStartMonthRange, inventoryEndMonthRange),
);
inventoryEndMonthRange.addEventListener("input", () =>
  scheduleDashboardReload(inventoryStartMonthRange, inventoryEndMonthRange),
);
updateRangeLabel();

loadDashboard().catch(showError);
