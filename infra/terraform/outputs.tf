output "run_service_account" {
  description = "Runtime service account used by both Cloud Run services."
  value       = google_service_account.run.email
}

output "service_urls" {
  description = "Cloud Run URLs (known after the services exist)."
  value       = { for k, s in google_cloud_run_v2_service.app : k => s.uri }
}

output "sql_connection_name" {
  description = "Cloud SQL connection name, for cloud-sql-proxy and the Cloud Run volume."
  value       = google_sql_database_instance.waypoint.connection_name
}
