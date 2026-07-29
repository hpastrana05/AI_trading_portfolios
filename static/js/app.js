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
});
