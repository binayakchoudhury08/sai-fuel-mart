var MS_RATE = parseFloat(document.getElementById("ms-rate")?.value) || 0;
var HSD_RATE = parseFloat(document.getElementById("hsd-rate")?.value) || 0;
var CNG_RATE = parseFloat(document.getElementById("cng-rate")?.value) || 0;

function money(v){
    return "₹ " + (Number(v) || 0).toFixed(2);
}

function num(v){
    return parseFloat(v) || 0;
}

function sum(selector){
    let total = 0;
    document.querySelectorAll(selector).forEach(function(el){
        total += num(el.value);
    });
    return total;
}

function setText(id, value){
    const el = document.getElementById(id);
    if(el){
        el.innerText = value;
    }
}

function getLubeTotals(){
    let cashLubeSale = 0;
    let creditLubeSale = 0;

    document.querySelectorAll(".lube-row").forEach(function(row){
        const mode = row.querySelector(".lube-mode");
        const amount = num(row.querySelector(".lube-amount")?.value);

        if(mode && mode.value === "Credit"){
            creditLubeSale += amount;
        }else{
            cashLubeSale += amount;
        }
    });

    return {
        cashLubeSale: cashLubeSale,
        creditLubeSale: creditLubeSale,
        totalLubeSale: cashLubeSale + creditLubeSale
    };
}

/* NOZZLE */

function setupNozzleCalculation(){
    document.querySelectorAll(".nozzle-table tbody tr").forEach(function(row){

        const opening = row.querySelector(".opening");
        const closing = row.querySelector(".closing");
        const testing = row.querySelector(".testing");
        const sale = row.querySelector(".sale");

        if(!opening || !closing || !testing || !sale) return;

        function calculate(){
            let total = num(closing.value) - num(opening.value) - num(testing.value);

            if(total < 0){
                total = 0;
            }

            sale.value = total.toFixed(2);
            updateAllTotals();
        }

        opening.addEventListener("input", calculate);
        closing.addEventListener("input", calculate);
        testing.addEventListener("input", calculate);

        calculate();
    });
}

/* CREDIT FUEL */

function setupCreditRow(row){
    const fuel = row.querySelector(".fuel");
    const rate = row.querySelector(".rate");
    const amount = row.querySelector(".amount");
    const litres = row.querySelector(".litres");

    if(!fuel || !rate || !amount || !litres) return;

    function updateRate(){
        if(fuel.value === "MS"){
            rate.value = MS_RATE.toFixed(2);
        }else if(fuel.value === "HSD"){
            rate.value = HSD_RATE.toFixed(2);
        }else{
            rate.value = CNG_RATE.toFixed(2);
        }

        calculateLitres();
    }

    function calculateLitres(){
        const amt = num(amount.value);
        const rt = num(rate.value);

        litres.value = rt > 0 ? (amt / rt).toFixed(2) : "0.00";
        updateAllTotals();
    }

    fuel.addEventListener("change", updateRate);
    amount.addEventListener("input", calculateLitres);

    updateRate();
}

/* LUBE WITH CASH / CREDIT MODE */

function setupLubeRow(row){
    const product = row.querySelector(".lube-product");
    const qty = row.querySelector(".qty");
    const rate = row.querySelector(".lube-rate");
    const amount = row.querySelector(".lube-amount");
    const mode = row.querySelector(".lube-mode");
    const transporter = row.querySelector(".lube-transporter");

    if(!product || !qty || !rate || !amount || !mode || !transporter) return;

    function calculate(){
        const selected = product.options[product.selectedIndex];
        const productRate = num(selected.dataset.rate);

        rate.value = productRate.toFixed(2);
        amount.value = (num(qty.value) * productRate).toFixed(2);

        if(mode.value === "Credit"){
            transporter.style.display = "block";
        }else{
            transporter.style.display = "none";
            transporter.value = "";
        }

        updateAllTotals();
    }

    product.addEventListener("change", calculate);
    qty.addEventListener("input", calculate);
    mode.addEventListener("change", calculate);
    transporter.addEventListener("change", updateAllTotals);

    calculate();
}

/* EXPENSE */

function setupExpenseRow(row){
    const amount = row.querySelector(".expense-amount");

    if(amount){
        amount.addEventListener("input", updateAllTotals);
    }
}

/* TOTALS */

function updateAllTotals(){
    const msLitres = sum(".ms-sale");
    const hsdLitres = sum(".hsd-sale");
    const cngKg = sum(".cng-sale");

    const msAmount = msLitres * MS_RATE;
    const hsdAmount = hsdLitres * HSD_RATE;
    const cngAmount = cngKg * CNG_RATE;

    const totalFuelSale = msAmount + hsdAmount + cngAmount;

    const lubeTotals = getLubeTotals();
    const cashLubeSale = lubeTotals.cashLubeSale;
    const creditLubeSale = lubeTotals.creditLubeSale;
    const lubeSale = lubeTotals.totalLubeSale;

    const phonepe = num(document.getElementById("phonepe")?.value);
    const cardSwipe = num(document.getElementById("card-swipe")?.value);
    const hpPay = num(document.getElementById("hp-pay")?.value);
    const hpclOtp = num(document.getElementById("hpcl-otp")?.value);
    const upiOther = num(document.getElementById("upi-other")?.value);

    const digitalCollection = phonepe + cardSwipe + hpPay + hpclOtp + upiOther;
    const transportReceived = num(document.getElementById("transport-received")?.value);

    const fuelCredit = sum(".credit-row .amount");
    const creditGiven = fuelCredit + creditLubeSale;

    const netCreditDue = Math.max(creditGiven - transportReceived, 0);
    const totalExpense = sum(".expense-amount");

    const cashInHand =
        totalFuelSale +
        cashLubeSale -
        digitalCollection -
        netCreditDue -
        totalExpense;

    setText("ms-sale-card", money(msAmount));
    setText("hsd-sale-card", money(hsdAmount));
    setText("cng-sale-card", money(cngAmount));
    setText("total-sale-card", money(totalFuelSale + lubeSale));

    setText("ms-litres-card", msLitres.toFixed(2) + " Ltrs");
    setText("hsd-litres-card", hsdLitres.toFixed(2) + " Ltrs");
    setText("cng-kg-card", cngKg.toFixed(2) + " KG");

    setText("summary-fuel-sale", money(totalFuelSale));
    setText("summary-lube-sale", money(lubeSale));
    setText("summary-digital", money(digitalCollection));
    setText("summary-credit", money(creditGiven));
    setText("summary-transport-received", money(transportReceived));
    setText("summary-net-credit", money(netCreditDue));
    setText("summary-expense", money(totalExpense));
    setText("summary-cash", money(cashInHand));
}

/* ADD ROWS */

function setupAddButtons(){

    document.getElementById("add-credit")?.addEventListener("click", function(){
        const tbody = document.querySelector("#credit-table tbody");
        const first = document.querySelector(".transporter");
        const options = first ? first.innerHTML : "";

        const tr = document.createElement("tr");
        tr.className = "credit-row";

        tr.innerHTML = `
            <td><select class="transporter">${options}</select></td>
            <td>
                <select class="fuel">
                    <option>MS</option>
                    <option selected>HSD</option>
                    <option>CNG</option>
                </select>
            </td>
            <td><input type="number" class="rate" readonly></td>
            <td><input type="number" class="amount"></td>
            <td><input type="number" class="litres" readonly></td>
            <td><button type="button" class="delete-row">Delete</button></td>
        `;

        tbody.appendChild(tr);
        setupCreditRow(tr);

        tr.querySelector(".delete-row").addEventListener("click", function(){
            tr.remove();
            updateAllTotals();
        });
    });

    document.getElementById("add-lube")?.addEventListener("click", function(){
        const tbody = document.querySelector("#lube-table tbody");

        const firstProduct = document.querySelector(".lube-product");
        const firstTransporter = document.querySelector(".lube-transporter");

        const productOptions = firstProduct ? firstProduct.innerHTML : "";
        const transporterOptions = firstTransporter ? firstTransporter.innerHTML : "";

        const tr = document.createElement("tr");
        tr.className = "lube-row";

        tr.innerHTML = `
            <td><select class="lube-product">${productOptions}</select></td>
            <td><input type="number" class="qty"></td>
            <td><input type="number" class="lube-rate" readonly></td>
            <td><input type="number" class="lube-amount" readonly></td>

            <td>
                <select class="lube-mode">
                    <option value="Cash">Cash</option>
                    <option value="Credit">Credit</option>
                </select>
            </td>

            <td>
                <select class="lube-transporter" style="display:none;">
                    ${transporterOptions}
                </select>
            </td>

            <td><button type="button" class="delete-row">Delete</button></td>
        `;

        tbody.appendChild(tr);
        setupLubeRow(tr);

        tr.querySelector(".delete-row").addEventListener("click", function(){
            tr.remove();
            updateAllTotals();
        });
    });

    document.getElementById("add-expense")?.addEventListener("click", function(){
        const tbody = document.querySelector("#expense-table tbody");

        const tr = document.createElement("tr");
        tr.className = "expense-row";

        tr.innerHTML = `
            <td><input type="text" placeholder="Expense Name"></td>
            <td><input type="number" class="expense-amount"></td>
            <td><button type="button" class="delete-row">Delete</button></td>
        `;

        tbody.appendChild(tr);
        setupExpenseRow(tr);

        tr.querySelector(".delete-row").addEventListener("click", function(){
            tr.remove();
            updateAllTotals();
        });
    });
}

/* DATE */

function setupDateChange(){
    const dateInput = document.getElementById("closing-date");

    if(!dateInput) return;

    if(!dateInput.value){
        dateInput.value = new Date().toISOString().split("T")[0];
    }

    dateInput.addEventListener("change", function(){
        window.location.href = "/daily-closing?date=" + dateInput.value;
    });
}

/* COLLECT DATA */

function collectNozzleEntries(){
    let rows = [];

    document.querySelectorAll(".nozzle-table tbody tr").forEach(function(row){
        const nozzle = row.querySelector(".nozzle-id");
        const opening = row.querySelector(".opening");
        const closing = row.querySelector(".closing");
        const testing = row.querySelector(".testing");

        if(!nozzle) return;

        rows.push({
            nozzle_id: nozzle.value,
            opening: opening ? opening.value : 0,
            closing: closing ? closing.value : 0,
            testing: testing ? testing.value : 0
        });
    });

    return rows;
}

function collectLubeSales(){
    let rows = [];

    document.querySelectorAll(".lube-row").forEach(function(row){
        const product = row.querySelector(".lube-product");
        const qty = row.querySelector(".qty");
        const rate = row.querySelector(".lube-rate");
        const amount = row.querySelector(".lube-amount");
        const mode = row.querySelector(".lube-mode");
        const transporter = row.querySelector(".lube-transporter");

        if(!product || !qty || !rate || !amount || !mode) return;

        if(product.value && num(qty.value) > 0){
            rows.push({
                product_id: product.value,
                product_name: product.options[product.selectedIndex].dataset.name || "",
                qty: num(qty.value),
                rate: num(rate.value),
                amount: num(amount.value),
                mode: mode.value,
                transporter_id: transporter ? transporter.value : "",
                transporter_name: transporter && transporter.value
                    ? transporter.options[transporter.selectedIndex].dataset.name || ""
                    : ""
            });
        }
    });

    return rows;
}

function collectCreditSales(){
    let rows = [];

    document.querySelectorAll(".credit-row").forEach(function(row){
        const transporter = row.querySelector(".transporter");
        const amount = row.querySelector(".amount");
        const fuel = row.querySelector(".fuel");

        if(transporter && transporter.value && num(amount.value) > 0){
            rows.push({
                transporter_id: transporter.value,
                fuel: fuel ? fuel.value : "",
                amount: num(amount.value)
            });
        }
    });

    return rows;
}

/* SAVE */

function setupSaveClosing(){
    const btn = document.getElementById("save-closing");

    if(!btn) return;

    btn.addEventListener("click", async function(){
        updateAllTotals();

        const selectedDate = document.getElementById("closing-date").value;

        if(!selectedDate){
            alert("Please select date");
            return;
        }

        const lubeTotals = getLubeTotals();

        const msLitres = sum(".ms-sale");
        const hsdLitres = sum(".hsd-sale");
        const cngSale = sum(".cng-sale");

        const totalFuelSale =
            (msLitres * MS_RATE) +
            (hsdLitres * HSD_RATE) +
            (cngSale * CNG_RATE);

        const lubeSale = lubeTotals.totalLubeSale;

        const phonepe = num(document.getElementById("phonepe")?.value);
        const cardSwipe = num(document.getElementById("card-swipe")?.value);
        const hpPay = num(document.getElementById("hp-pay")?.value);
        const hpclOtp = num(document.getElementById("hpcl-otp")?.value);
        const upiOther = num(document.getElementById("upi-other")?.value);

        const digitalCollection = phonepe + cardSwipe + hpPay + hpclOtp + upiOther;
        const transportReceived = num(document.getElementById("transport-received")?.value);

        const fuelCredit = sum(".credit-row .amount");
        const creditGiven = fuelCredit + lubeTotals.creditLubeSale;

        const netCreditDue = Math.max(creditGiven - transportReceived, 0);
        const totalExpense = sum(".expense-amount");

        const cashInHand =
            totalFuelSale +
            lubeTotals.cashLubeSale -
            digitalCollection -
            netCreditDue -
            totalExpense;

        const data = {
            date: selectedDate,
            nozzle_entries: collectNozzleEntries(),

            ms_litres: msLitres,
            hsd_litres: hsdLitres,
            cng_sale: cngSale,

            total_fuel_sale: totalFuelSale,
            lube_sale: lubeSale,
            digital_collection: digitalCollection,

            phonepe: phonepe,
            card_swipe: cardSwipe,
            hp_pay: hpPay,
            hpcl_otp: hpclOtp,
            upi_other: upiOther,

            credit_given: creditGiven,
            transport_received: transportReceived,
            net_credit_due: netCreditDue,
            total_expense: totalExpense,
            cash_in_hand: cashInHand,

            lube_sales: collectLubeSales(),
            credit_transport_sales: collectCreditSales()
        };

        btn.disabled = true;
        btn.innerHTML = "Saving...";

        try{
            const response = await fetch("/save-daily-closing", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(data)
            });

            const result = await response.json();
            alert(result.message || "Saved Successfully");

        }catch(error){
            console.log(error);
            alert("Error saving daily closing");
        }finally{
            btn.disabled = false;
            btn.innerHTML = '<i class="fa-solid fa-floppy-disk"></i> Save Closing';
        }
    });
}

/* INIT */

document.addEventListener("DOMContentLoaded", function(){
    setupDateChange();
    setupNozzleCalculation();

    document.querySelectorAll(".credit-row").forEach(setupCreditRow);
    document.querySelectorAll(".lube-row").forEach(setupLubeRow);
    document.querySelectorAll(".expense-row").forEach(setupExpenseRow);

    document.querySelectorAll(".digital-input").forEach(function(input){
        input.addEventListener("input", updateAllTotals);
    });

    document.getElementById("transport-received")?.addEventListener("input", updateAllTotals);

    setupAddButtons();
    setupSaveClosing();
    updateAllTotals();
});