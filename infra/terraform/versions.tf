terraform {
  # import {} blocks with for_each need Terraform 1.7+.
  required_version = ">= 1.7"

  required_providers {
    google = {
      source = "hashicorp/google"
      # Exact pin: plans against hand-made resources must be reproducible.
      version = "8.9.0"
    }
  }

  # Local state on purpose for now: nothing has been applied yet. Move to a GCS backend
  # before the first apply so state is shared and locked.
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# The Budgets API rejects user (ADC) credentials unless a quota project is sent with each call.
# Only the budget uses this alias, so every other resource keeps default request routing.
provider "google" {
  alias                 = "billing"
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}
