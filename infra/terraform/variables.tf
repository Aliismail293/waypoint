variable "project_id" {
  description = "GCP project that hosts Waypoint."
  type        = string
  default     = "waypoint-513003"
}

variable "region" {
  description = "Region for Cloud Run, Cloud SQL and Firestore."
  type        = string
  default     = "us-central1"
}

variable "waypoint_image" {
  description = "Initial image for the waypoint service. Real images come from `gcloud run deploy --source`, so Terraform ignores later changes."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "fdekit_demo_image" {
  description = "Initial image for the fdekit-demo service (ignored after creation, like waypoint_image)."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "google_client_id" {
  description = "OAuth client id for Google Sign-In. Public by design (it ships to the browser), so not a secret."
  type        = string
  default     = "48349810745-snc3au6tu4hfgqv8srrqlq0b766lh46h.apps.googleusercontent.com"
}

variable "fdekit_demo_env" {
  description = "WAYPOINT_* env vars for fdekit-demo, merged over the shared waypoint env (keys here win)."
  type        = map(string)
  default = {
    WAYPOINT_SERVICE_NAME = "fdekit-demo"
  }
}

variable "enable_budget" {
  description = "Create the monthly billing budget. Turn off if the caller lacks billing.budgets.* on the billing account."
  type        = bool
  default     = true
}

variable "billing_account" {
  description = "Billing account linked to the project (from `gcloud billing projects describe`)."
  type        = string
  default     = "016B73-5EA57C-C8C383"
}

variable "budget_amount" {
  description = "Monthly budget in whole units of budget_currency."
  type        = number
  default     = 25
}

variable "budget_currency" {
  description = "Must equal the billing account's currency or the Budgets API rejects the budget. This account bills in CAD."
  type        = string
  default     = "CAD"
}
