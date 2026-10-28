# Monthly budget with email alerts to billing admins. Alerts only: a budget never stops spending.
# Optional via var.enable_budget because it needs billing.budgets.* on the billing account,
# which a project-only operator may not have.

# The Budgets API is not enabled in the project yet and must be, for the quota project used by the
# billing provider alias. disable_on_destroy = false so removing this never turns the API off.
resource "google_project_service" "billingbudgets" {
  count              = var.enable_budget ? 1 : 0
  service            = "billingbudgets.googleapis.com"
  disable_on_destroy = false
}

resource "google_billing_budget" "monthly" {
  count           = var.enable_budget && var.billing_account != "" ? 1 : 0
  provider        = google.billing
  billing_account = var.billing_account
  display_name    = "waypoint-monthly"

  budget_filter {
    projects        = ["projects/${data.google_project.this.number}"]
    calendar_period = "MONTH"
  }

  amount {
    specified_amount {
      currency_code = var.budget_currency
      units         = tostring(var.budget_amount)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }

  depends_on = [google_project_service.billingbudgets]
}

data "google_project" "this" {
  project_id = var.project_id
}
