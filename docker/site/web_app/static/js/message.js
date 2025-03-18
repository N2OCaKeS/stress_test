document.addEventListener("DOMContentLoaded", function () {
    const form = document.querySelector("form");
    const textarea = form.querySelector("textarea");
    const messageBox = document.createElement("div");
    messageBox.classList.add("alert", "mt-3");
    messageBox.style.display = "none";
    form.appendChild(messageBox);

    form.addEventListener("submit", async function (event) {
        event.preventDefault(); // Отмена стандартного поведения формы

        let content = textarea.value.trim();
        if (!content) {
            showMessage("Ошибка: сообщение не может быть пустым!", "danger");
            return;
        }

        let response = await fetch("/message/create", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: content })
        });

        let result = await response.json();

        if (response.ok) {
            showMessage(result.message, result.status === "success" ? "success" : "warning");
            if (result.status === "success") {
                textarea.value = ""; // Очищаем поле ввода
            }
        } else {
            showMessage(result.error || "Ошибка отправки сообщения", "danger");
        }
    });

    function showMessage(text, type) {
        messageBox.textContent = text;
        messageBox.className = `alert alert-${type} mt-3`;
        messageBox.style.display = "block";
        setTimeout(() => messageBox.style.display = "none", 5000); // Авто-скрытие через 5 сек
    }
});

