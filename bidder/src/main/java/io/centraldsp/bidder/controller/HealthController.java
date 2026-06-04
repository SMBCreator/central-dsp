package io.centraldsp.bidder.controller;

import io.centraldsp.bidder.service.CampaignLoaderService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Health check endpoint.
 *
 * <p>GET /health — used by:
 * <ul>
 *   <li>Docker HEALTHCHECK</li>
 *   <li>Python backend's RTBService.get_bidder_status()</li>
 *   <li>Load balancer probes</li>
 * </ul>
 * </p>
 *
 * <p>Returns 200 when the bidder is ready to process requests.
 * Returns 503 when campaigns haven't loaded yet (startup grace period).</p>
 */
@RestController
@RequiredArgsConstructor
public class HealthController {

    private final CampaignLoaderService campaignLoader;

    @GetMapping("/health")
    public ResponseEntity<Map<String, Object>> health() {
        int campaignCount = campaignLoader.getCampaignCount();
        boolean ready = campaignLoader.isInitialized();

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", ready ? "ok" : "starting");
        body.put("campaigns_loaded", campaignCount);
        body.put("timestamp", Instant.now().toString());

        return ready
                ? ResponseEntity.ok(body)
                : ResponseEntity.status(503).body(body);
    }

    /**
     * Kubernetes / ECS readiness probe.
     * Returns 200 only when fully initialized.
     */
    @GetMapping("/ready")
    public ResponseEntity<String> ready() {
        return campaignLoader.isInitialized()
                ? ResponseEntity.ok("ready")
                : ResponseEntity.status(503).body("not ready");
    }

    /**
     * Liveness probe — always returns 200 as long as the process is alive.
     */
    @GetMapping("/live")
    public ResponseEntity<String> live() {
        return ResponseEntity.ok("live");
    }
}
