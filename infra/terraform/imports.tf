# Everything below was created by hand before Terraform. Import blocks adopt it into state on the
# first apply instead of trying to create duplicates. After that apply these blocks are no-ops and
# can be deleted.

import {
  to = google_sql_database_instance.waypoint
  id = "projects/${var.project_id}/instances/waypoint-db"
}

import {
  to = google_sql_database.waypoint
  id = "projects/${var.project_id}/instances/waypoint-db/databases/waypoint"
}

import {
  to = google_bigquery_dataset.waypoint
  id = "projects/${var.project_id}/datasets/waypoint"
}

import {
  to = google_firestore_database.default
  id = "projects/${var.project_id}/databases/(default)"
}

import {
  for_each = local.secret_ids
  to       = google_secret_manager_secret.app[each.key]
  id       = "projects/${var.project_id}/secrets/${each.key}"
}
