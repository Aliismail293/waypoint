# The metrics dashboard: tokens/sec, cost per request, P95 latency, cache hit rate, tool errors.
#
# Each request writes one structured log line ({"message": "waypoint.request", "latency_ms": ..., ...}, see
# waypoint/observability/tracing.py). Log-based distribution metrics turn those fields into time series,
# so the dashboard needs no extra exporter. Traces with the same numbers per agent are in Cloud Trace / Langfuse.

locals {
  request_log_filter = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="waypoint"
    jsonPayload.message="waypoint.request"
  EOT

  request_metrics = {
    latency_ms        = { unit = "ms", description = "End-to-end latency per question (all agents)." }
    cost_usd          = { unit = "USD", description = "Estimated model cost per question." }
    tokens_per_second = { unit = "1", description = "Output tokens per second of request latency." }
    cache_hit_rate    = { unit = "1", description = "Share of input tokens served from Gemini's prompt cache." }
    tool_errors       = { unit = "1", description = "Tool errors per question." }
    model_calls       = { unit = "1", description = "Model calls per question, across all agents." }
  }
}

resource "google_logging_metric" "request" {
  for_each = local.request_metrics

  name            = "waypoint/${each.key}"
  description     = each.value.description
  filter          = local.request_log_filter
  value_extractor = "EXTRACT(jsonPayload.${each.key})"

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = each.value.unit
  }

  # Exponential buckets cover 1e-5 (a cheap request's dollars) to ~1e6 (milliseconds) in one scheme.
  bucket_options {
    exponential_buckets {
      num_finite_buckets = 64
      growth_factor      = 1.5
      scale              = 0.00001
    }
  }
}

resource "google_monitoring_dashboard" "waypoint" {
  dashboard_json = jsonencode({
    displayName = "Waypoint: requests"
    mosaicLayout = {
      columns = 12
      tiles = [
        for i, tile in [
          { title = "P50 / P95 latency (ms)", metric = "latency_ms", aligners = ["ALIGN_PERCENTILE_50", "ALIGN_PERCENTILE_95"] },
          { title = "Cost per request (USD, mean)", metric = "cost_usd", aligners = ["ALIGN_DELTA"] },
          { title = "Tokens per second (mean)", metric = "tokens_per_second", aligners = ["ALIGN_DELTA"] },
          { title = "Prompt cache hit rate (mean)", metric = "cache_hit_rate", aligners = ["ALIGN_DELTA"] },
          { title = "Tool errors per request (mean)", metric = "tool_errors", aligners = ["ALIGN_DELTA"] },
          { title = "Model calls per request (P95)", metric = "model_calls", aligners = ["ALIGN_PERCENTILE_95"] },
          ] : {
          xPos   = (i % 2) * 6
          yPos   = floor(i / 2) * 4
          width  = 6
          height = 4
          widget = {
            title = tile.title
            xyChart = {
              dataSets = [
                for aligner in tile.aligners : {
                  plotType       = "LINE"
                  legendTemplate = replace(aligner, "ALIGN_", "")
                  timeSeriesQuery = {
                    timeSeriesFilter = {
                      filter = "metric.type=\"logging.googleapis.com/user/waypoint/${tile.metric}\" resource.type=\"cloud_run_revision\""
                      aggregation = {
                        alignmentPeriod    = "3600s"
                        perSeriesAligner   = aligner
                        crossSeriesReducer = aligner == "ALIGN_DELTA" ? "REDUCE_MEAN" : "REDUCE_MAX"
                      }
                    }
                  }
                }
              ]
            }
          }
        }
      ]
    }
  })

  depends_on = [google_logging_metric.request]
}
