// Landing page: show the task field for Human Test, and submit the order as JSON.
const form = document.getElementById("order-form");
if (form) {
  const taskField = document.getElementById("task-field");
  const errorBox = document.getElementById("form-error");
  const syncTier = () => { taskField.hidden = form.tier.value !== "human"; };
  form.addEventListener("change", syncTier);
  syncTier();

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorBox.hidden = true;
    const body = {
      url: form.url.value,
      tier: form.tier.value,
      task: form.tier.value === "human" ? form.task.value : null,
      email: form.email.value || null,
    };
    const res = await fetch("/api/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      errorBox.textContent = typeof data.detail === "string" ? data.detail : "Please check the form and try again.";
      errorBox.hidden = false;
      return;
    }
    window.location.href = data.checkout_url;
  });
}
