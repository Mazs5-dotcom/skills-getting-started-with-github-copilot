document.addEventListener("DOMContentLoaded", () => {
  const activitiesList = document.getElementById("activities-list");
  const activitySelect = document.getElementById("activity");
  const signupForm = document.getElementById("signup-form");
  const loginForm = document.getElementById("login-form");
  const sessionInfo = document.getElementById("session-info");
  const studentEmailGroup = document.getElementById("student-email-group");
  const studentEmailInput = document.getElementById("email");
  const messageDiv = document.getElementById("message");

  let session = null;

  function showMessage(text, className) {
    messageDiv.textContent = text;
    messageDiv.className = className;
    messageDiv.classList.remove("hidden");

    // Hide message after 5 seconds
    setTimeout(() => {
      messageDiv.classList.add("hidden");
    }, 5000);
  }

  function setSession(newSession) {
    session = newSession;
    const loggedIn = session !== null;
    const isStaff = loggedIn && session.role === "staff";

    loginForm.classList.toggle("hidden", loggedIn);
    signupForm.classList.toggle("hidden", !loggedIn);
    sessionInfo.classList.toggle("hidden", !loggedIn);
    studentEmailGroup.classList.toggle("hidden", !isStaff);
    studentEmailInput.required = isStaff;
    sessionInfo.textContent = loggedIn
      ? `Logged in as ${session.email} (${session.role})`
      : "";
  }

  loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    try {
      const response = await fetch("/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: document.getElementById("login-email").value,
          password: document.getElementById("login-password").value,
        }),
      });

      const result = await response.json();

      if (response.ok) {
        loginForm.reset();
        setSession(result);
      } else {
        showMessage(result.detail || "Login failed", "error");
      }
    } catch (error) {
      showMessage("Failed to log in. Please try again.", "error");
      console.error("Error logging in:", error);
    }
  });

  // Function to fetch activities from API
  async function fetchActivities() {
    try {
      const response = await fetch("/activities");
      const activities = await response.json();

      // Clear loading message
      activitiesList.innerHTML = "";

      // Populate activities list
      Object.entries(activities).forEach(([name, details]) => {
        const activityCard = document.createElement("div");
        activityCard.className = "activity-card";

        const spotsLeft = details.max_participants - details.participants.length;

        activityCard.innerHTML = `
          <h4>${name}</h4>
          <p>${details.description}</p>
          <p><strong>Schedule:</strong> ${details.schedule}</p>
          <p><strong>Availability:</strong> ${spotsLeft} spots left</p>
        `;

        activitiesList.appendChild(activityCard);

        // Add option to select dropdown
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        activitySelect.appendChild(option);
      });
    } catch (error) {
      activitiesList.innerHTML = "<p>Failed to load activities. Please try again later.</p>";
      console.error("Error fetching activities:", error);
    }
  }

  // Handle form submission
  signupForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (session === null) {
      showMessage("Please log in first.", "error");
      return;
    }

    const activity = document.getElementById("activity").value;
    let url = `/activities/${encodeURIComponent(activity)}/signup`;
    if (session.role === "staff") {
      url += `?email=${encodeURIComponent(studentEmailInput.value)}`;
    }

    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { Authorization: `Bearer ${session.token}` },
      });

      const result = await response.json();

      if (response.ok) {
        showMessage(result.message, "success");
        signupForm.reset();
      } else {
        if (response.status === 401) {
          setSession(null);
        }
        showMessage(result.detail || "An error occurred", "error");
      }
    } catch (error) {
      showMessage("Failed to sign up. Please try again.", "error");
      console.error("Error signing up:", error);
    }
  });

  // Initialize app
  fetchActivities();
});
