# Cloud Run services. Settings mirror infra/deploy.sh; images are built and rolled out by
# `gcloud run deploy --source`, so Terraform owns the service shape but not what it runs.

locals {
  sql_connection_name = google_sql_database_instance.waypoint.connection_name

  # Plain env vars from deploy.sh.
  base_env = {
    WAYPOINT_LLM              = "vertex"
    WAYPOINT_EMBEDDINGS       = "vertex"
    WAYPOINT_STORE            = "pgvector"
    WAYPOINT_CATALOG          = "bigquery"
    WAYPOINT_SESSIONS         = "firestore"
    WAYPOINT_CLOUD_TRACE      = "true"
    WAYPOINT_DB_HOST          = "/cloudsql/${local.sql_connection_name}"
    WAYPOINT_GOOGLE_CLIENT_ID = var.google_client_id
    WAYPOINT_GCP_PROJECT      = var.project_id
  }

  # Env var name -> Secret Manager secret id, always resolved at the latest version.
  secret_env = {
    WAYPOINT_DB_PASSWORD         = "db-password"
    WAYPOINT_LANGFUSE_PUBLIC_KEY = "langfuse-public-key"
    WAYPOINT_LANGFUSE_SECRET_KEY = "langfuse-secret-key"
  }

  services = {
    waypoint = {
      image = var.waypoint_image
      env   = local.base_env
    }
    fdekit-demo = {
      image = var.fdekit_demo_image
      env   = merge(local.base_env, var.fdekit_demo_env)
    }
  }
}

resource "google_cloud_run_v2_service" "app" {
  for_each = local.services
  name     = each.key
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account                  = google_service_account.run.email
    timeout                          = "300s"
    max_instance_request_concurrency = 10

    # Scale to zero keeps idle cost at nothing; max 2 caps spend if traffic spikes.
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image = each.value.image

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        # CPU only while serving (request-based billing), plus a boost to hide cold starts.
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = each.value.env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.app[env.value].secret_id
              version = "latest"
            }
          }
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
    }

    # Unix socket to Cloud SQL, matching WAYPOINT_DB_HOST above.
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [local.sql_connection_name]
      }
    }
  }

  lifecycle {
    # Deploys come from `gcloud run deploy --source`; without this every plan would try to roll
    # the service back to the placeholder image. gcloud also stamps its own client fields.
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  # A revision that cannot read its secrets fails to start, so grant access first.
  depends_on = [
    google_secret_manager_secret_iam_member.run_accessor,
    google_project_iam_member.run,
  ]
}

# Public on purpose: the app verifies Google Sign-In ID tokens itself (waypoint/api/auth.py) and
# fails closed without a client id, so Cloud Run IAM in front would only block the browser.
resource "google_cloud_run_v2_service_iam_member" "public" {
  for_each = google_cloud_run_v2_service.app
  name     = each.value.name
  location = each.value.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}
