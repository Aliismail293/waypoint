# Uptime check on /healthz plus alerts (uptime failure, high 5xx rate) emailed to var.alert_email.
# The address is never committed: pass it with  export TF_VAR_alert_email="$WAYPOINT_ALERT_EMAIL".

variable "alert_email" {
  description = "Where alerts go. Set TF_VAR_alert_email from WAYPOINT_ALERT_EMAIL. Empty disables the alerts."
  type        = string
  default     = ""
}

locals {
  alerts_on = var.alert_email != ""
  app_host  = trimprefix(google_cloud_run_v2_service.app["waypoint"].uri, "https://")
}

resource "google_monitoring_notification_channel" "email" {
  count        = local.alerts_on ? 1 : 0
  display_name = "Waypoint alerts"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

resource "google_monitoring_uptime_check_config" "healthz" {
  count        = local.alerts_on ? 1 : 0
  display_name = "waypoint /healthz"
  timeout      = "10s"
  period       = "300s"

  http_check {
    path         = "/healthz"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type   = "uptime_url"
    labels = { project_id = var.project_id, host = local.app_host }
  }
}

resource "google_monitoring_alert_policy" "down" {
  count        = local.alerts_on ? 1 : 0
  display_name = "Waypoint is down"
  combiner     = "OR"

  conditions {
    display_name = "/healthz failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.labels.check_id=\"${google_monitoring_uptime_check_config.healthz[0].uptime_check_id}\" AND resource.type=\"uptime_url\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "300s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email[0].id]
}

resource "google_monitoring_alert_policy" "errors" {
  count        = local.alerts_on ? 1 : 0
  display_name = "Waypoint 5xx errors"
  combiner     = "OR"

  conditions {
    display_name = "5xx responses"
    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND resource.label.service_name=\"waypoint\" AND metric.label.response_code_class=\"5xx\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "300s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email[0].id]
}
