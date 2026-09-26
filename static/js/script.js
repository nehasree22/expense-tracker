// =========================================
// THE LEDGER — Personal Expense Tracker
// =========================================

const expenseModal = document.getElementById("expenseModal");
const expenseForm = document.getElementById("expenseForm");
const modalTitle = document.getElementById("modalTitle");
const modalSubmitBtn = document.getElementById("modalSubmitBtn");

const ADD_ACTION = expenseForm ? expenseForm.getAttribute("action") : "";

function openModal() {
    expenseModal.style.display = "flex";
    document.body.style.overflow = "hidden";
}

function closeExpenseModal() {
    expenseModal.style.display = "none";
    document.body.style.overflow = "auto";
}

// ---- ADD mode ----
function openAddModal() {
    expenseForm.reset();
    expenseForm.setAttribute("action", ADD_ACTION);
    modalTitle.textContent = "Record an Expense";
    modalSubmitBtn.textContent = "Save to Ledger";
    openModal();
}

// ---- EDIT mode ----
function openEditModal(id, title, amount, category, paymentMethod, expenseDate) {
    expenseForm.setAttribute("action", "/edit/" + id);

    document.getElementById("title").value = title;
    document.getElementById("amount").value = amount;
    document.getElementById("category").value = category;
    document.getElementById("payment_method").value = paymentMethod;
    document.getElementById("expense_date").value = expenseDate;

    modalTitle.textContent = "Edit Expense";
    modalSubmitBtn.textContent = "Update Entry";
    openModal();
}

// Close when clicking outside the modal content
window.addEventListener("click", function (event) {
    if (event.target === expenseModal) {
        closeExpenseModal();
    }
});

// Close with the Escape key
document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
        closeExpenseModal();
    }
});
