# Data stores. All of these hold state the app cannot rebuild cheaply, so they are imported, matched
# to their real settings, and guarded against accidental destroy.

resource "google_sql_database_instance" "waypoint" {
  name             = "waypoint-db"
  region           = var.region
  database_version = "POSTGRES_16"

  # Terraform-side guard: refuses destroy/replace even if someone removes prevent_destroy below.
  deletion_protection = true

  settings {
    tier              = "db-f1-micro"
    edition           = "ENTERPRISE"
    availability_type = "ZONAL"
    pricing_plan      = "PER_USE"
    activation_policy = "ALWAYS"

    disk_type             = "PD_SSD"
    disk_size             = 10
    disk_autoresize       = true
    disk_autoresize_limit = 0

    # Mirrors the instance as created. The API-side flag (blocks deletes from console/gcloud too)
    # is off today; flipping it to true is a safe in-place update but is left as a separate change
    # so this first plan stays import-only.
    deletion_protection_enabled = false
    connector_enforcement       = "NOT_REQUIRED"
    enable_dataplex_integration = true

    location_preference {
      zone = "us-central1-a"
    }

    # Backups are off to stay near free tier; the corpus can be re-ingested from data/corpus.
    backup_configuration {
      enabled                        = false
      start_time                     = "08:00"
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 7
        retention_unit   = "COUNT"
      }
    }

    # Public IP with no authorized networks: only the Cloud SQL connector / auth proxy can reach it.
    ip_configuration {
      ipv4_enabled   = true
      ssl_mode       = "ALLOW_UNENCRYPTED_AND_ENCRYPTED"
      server_ca_mode = "GOOGLE_MANAGED_INTERNAL_CA"
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_sql_database" "waypoint" {
  name      = "waypoint"
  instance  = google_sql_database_instance.waypoint.name
  charset   = "UTF8"
  collation = "en_US.UTF8"
}

# Tables (courses, programs, prerequisites) are created and loaded by `waypoint.ingest bigquery`,
# so only the dataset is declared here.
resource "google_bigquery_dataset" "waypoint" {
  dataset_id            = "waypoint"
  location              = "US"
  max_time_travel_hours = "168"

  lifecycle {
    prevent_destroy = true
  }
}

resource "google_firestore_database" "default" {
  name                              = "(default)"
  location_id                       = var.region
  type                              = "FIRESTORE_NATIVE"
  database_edition                  = "STANDARD"
  concurrency_mode                  = "PESSIMISTIC"
  app_engine_integration_mode       = "DISABLED"
  point_in_time_recovery_enablement = "POINT_IN_TIME_RECOVERY_DISABLED"
  delete_protection_state           = "DELETE_PROTECTION_DISABLED"

  # ABANDON: removing this from Terraform must never delete the ADK session store.
  deletion_policy = "ABANDON"

  lifecycle {
    prevent_destroy = true
  }
}

# Secret containers only. Values are added out of band with `gcloud secrets versions add`, so no
# secret material ever lands in Terraform state.
locals {
  secret_ids = toset(["db-password", "langfuse-public-key", "langfuse-secret-key"])
}

resource "google_secret_manager_secret" "app" {
  for_each  = local.secret_ids
  secret_id = each.key

  replication {
    auto {}
  }
}
