const MS_RATE_DEFAULT = 102.46;
const HSD_RATE_DEFAULT = 93.72;

const formatMoney = (value) => {
    return "₹ " + (Number(value) || 0).toFixed(2);
};

function getNumber(selector) {
    const el = document.querySelector(selector);
    return el ? parseFloat(el.value) || 0 : 0;
}

function sumInputs(selector) {
    let total = 0;
    document.querySelectorAll(selector).forEach(input => {
        total += parseFloat(input.value) || 0;
    });
    return total;
}

function setupNozzleCalculation() {
    document.querySelectorAll(".nozzle-table tbody tr").forEach(row => {
        const opening = row.querySelector(".opening");
        const closing = row.querySelector(".closing");
        const testing = row.querySelector(".testing");
        const sale = row.querySelector(".sale");

        function calculateSale() {
            const open = parseFloat(opening.value) || 0;
            const close = parseFloat(closing.value) || 0;
            const test = parseFloat(testing.value) || 0;

            const total = close - open - test;
            sale.value = total > 0 ? total.toFixed(2) : "0.00";

            updateAllTotals();
        }

        opening.addEventListener("input", calculateSale);
        closing.addEventListener("input", calculateSale);
        testing.addEventListener("input", calculateSale);
    });
}

function setupCreditRow(row) {
    const fuel = row.querySelector(".fuel");
    const rate = row.querySelector(".rate");
    const amount = row.querySelector(".amount");
    const litres = row.querySelector(".litres");

    function updateRate() {
        rate.value = fuel.value === "MS"
            ? MS_RATE_DEFAULT.toFixed(2)
            : HSD_RATE_DEFAULT.toFixed(2);

        calculateLitres();
    }

    function calculateLitres() {
        const amt = parseFloat(amount.value) || 0;
        const rt = parseFloat(rate.value) || 0;

        litres.value = rt > 0 ? (amt / rt).toFixed(2) : "0.00";
        updateAllTotals();
    }

    fuel.addEventListener("change", updateRate);
    amount.addEventListener("input", calculateLitres);

    updateRate();
}

function setupLubeRow(row) {
    const product = row.querySelector(".lube-product");
    const qty = row.querySelector(".qty");
    const rate = row.querySelector(".lube-rate");
    const amount = row.querySelector(".lube-amount");

    function calculate() {
        const selected = product.options[product.selectedIndex];
        const productRate = parseFloat(selected.dataset.rate) || 0;

        rate.value = productRate.toFixed(2);

        const q = parseFloat(qty.value) || 0;
        amount.value = (q * productRate).toFixed(2);

        updateAllTotals();
    }

    product.addEventListener("change", calculate);
    qty.addEventListener("input", calculate);

    calculate();
}

function setupExpenseRow(row) {
    const amount = row.querySelector(".expense-amount");
    amount.addEventListener("input", updateAllTotals);
}

function setupAddButtons() {
    document.querySelector("#add-credit").addEventListener("click", () => {
        const tbody = document.querySelector("#credit-table tbody");

        const tr = document.createElement("tr");
        tr.className = "credit-row";

        tr.innerHTML = `
            <td><input type="text" placeholder="Party Name"></td>
            <td>
                <select class="fuel">
                    <option>MS</option>
                    <option selected>HSD</option>
                </select>
            </td>
            <td><input type="number" class="rate" readonly></td>
            <td><input type="number" class="amount" placeholder="Amount"></td>
            <td><input type="number" class="litres" readonly></td>
            <td>
                <select>
                    <option>Pending</option>
                    <option>Paid</option>
                </select>
            </td>
        `;

        tbody.appendChild(tr);
        setupCreditRow(tr);
    });

    document.querySelector("#add-lube").addEventListener("click", () => {
        const tbody = document.querySelector("#lube-table tbody");

        const tr = document.createElement("tr");
        tr.className = "lube-row";

        tr.innerHTML = `
            <td>
                <select class="lube-product">
                    <option value="" data-rate="0">Select Product</option>
                    <option value="HP Racer 2T" data-rate="210">HP Racer 2T</option>
                    <option value="HP Petrol Plus" data-rate="300">HP Petrol Plus</option>
                    <option value="HP Long Life Grease" data-rate="450">HP Long Life Grease</option>
                    <option value="HP Milcy" data-rate="190">HP Milcy</option>
                </select>
            </td>
            <td><input type="number" class="qty"></td>
            <td><input type="number" class="lube-rate" readonly></td>
            <td><input type="number" class="lube-amount" readonly></td>
        `;

        tbody.appendChild(tr);
        setupLubeRow(tr);
    });

    document.querySelector("#add-expense").addEventListener("click", () => {
        const tbody = document.querySelector("#expense-table tbody");

        const tr = document.createElement("tr");
        tr.className = "expense-row";

        tr.innerHTML = `
            <td><input type="text" placeholder="Expense Name"></td>
            <td><input type="number" class="expense-amount"></td>
        `;

        tbody.appendChild(tr);
        setupExpenseRow(tr);
    });
}

function updateStock(msSale, hsdSale) {
    document.querySelector("#ms-stock-sale").value = msSale.toFixed(2);
    document.querySelector("#hsd-stock-sale").value = hsdSale.toFixed(2);

    const msOpening = getNumber("#ms-stock-opening");
    const msReceived = getNumber("#ms-stock-received");
    const hsdOpening = getNumber("#hsd-stock-opening");
    const hsdReceived = getNumber("#hsd-stock-received");

    document.querySelector("#ms-stock-closing").value =
        (msOpening + msReceived - msSale).toFixed(2);

    document.querySelector("#hsd-stock-closing").value =
        (hsdOpening + hsdReceived - hsdSale).toFixed(2);
}

function updateAllTotals() {
    const msLitres = sumInputs(".ms-sale");
    const hsdLitres = sumInputs(".hsd-sale");

    const msAmount = msLitres * MS_RATE_DEFAULT;
    const hsdAmount = hsdLitres * HSD_RATE_DEFAULT;
    const totalFuelSale = msAmount + hsdAmount;

    const lubeSale = sumInputs(".lube-amount");
    const digitalCollection = sumInputs(".digital-input");
    const transportReceived = getNumber("#transport-received");
    const creditGiven = sumInputs(".credit-row .amount");
    const netCreditDue = Math.max(creditGiven - transportReceived, 0);
    const totalExpense = sumInputs(".expense-amount");

    const cashInHand =
        totalFuelSale +
        lubeSale -
        digitalCollection -
        netCreditDue -
        totalExpense;

    document.querySelector("#ms-sale-card").innerText = formatMoney(msAmount);
    document.querySelector("#hsd-sale-card").innerText = formatMoney(hsdAmount);
    document.querySelector("#total-sale-card").innerText = formatMoney(totalFuelSale + lubeSale);

    document.querySelector("#ms-litres-card").innerText = msLitres.toFixed(2) + " Ltrs";
    document.querySelector("#hsd-litres-card").innerText = hsdLitres.toFixed(2) + " Ltrs";

    document.querySelector("#summary-fuel-sale").innerText = formatMoney(totalFuelSale);
    document.querySelector("#summary-lube-sale").innerText = formatMoney(lubeSale);
    document.querySelector("#summary-digital").innerText = formatMoney(digitalCollection);
    document.querySelector("#summary-credit").innerText = formatMoney(creditGiven);
    document.querySelector("#summary-transport-received").innerText = formatMoney(transportReceived);
    document.querySelector("#summary-net-credit").innerText = formatMoney(netCreditDue);
    document.querySelector("#summary-expense").innerText = formatMoney(totalExpense);
    document.querySelector("#summary-cash").innerText = formatMoney(cashInHand);

    updateStock(msLitres, hsdLitres);
}

document.addEventListener("DOMContentLoaded", () => {
    setupNozzleCalculation();

    document.querySelectorAll(".credit-row").forEach(setupCreditRow);
    document.querySelectorAll(".lube-row").forEach(setupLubeRow);
    document.querySelectorAll(".expense-row").forEach(setupExpenseRow);

    document.querySelectorAll(".digital-input").forEach(input => {
        input.addEventListener("input", updateAllTotals);
    });

    document.querySelector("#ms-stock-opening").addEventListener("input", updateAllTotals);
    document.querySelector("#ms-stock-received").addEventListener("input", updateAllTotals);
    document.querySelector("#hsd-stock-opening").addEventListener("input", updateAllTotals);
    document.querySelector("#hsd-stock-received").addEventListener("input", updateAllTotals);

    setupAddButtons();
    updateAllTotals();
});