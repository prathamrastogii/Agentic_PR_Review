let container = null;

function ensureContainer() {
  if (!container) {
    container = document.getElementById("toast-container");
  }
  return container;
}

export function showToast(message, { type = "info", duration = 4000 } = {}) {
  const root = ensureContainer();
  if (!root) return;

  const toast = document.createElement("div");
  toast.className = `toast toast--${type}`;
  toast.setAttribute("role", "status");
  toast.textContent = message;
  root.appendChild(toast);

  requestAnimationFrame(() => toast.classList.add("toast--visible"));

  const remove = () => {
    toast.classList.remove("toast--visible");
    setTimeout(() => toast.remove(), 200);
  };

  const timer = setTimeout(remove, duration);
  toast.addEventListener("click", () => {
    clearTimeout(timer);
    remove();
  });
}
