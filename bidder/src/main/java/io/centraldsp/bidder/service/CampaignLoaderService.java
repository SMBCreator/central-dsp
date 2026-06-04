package io.centraldsp.bidder.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.centraldsp.bidder.model.campaign.CampaignDTO;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import javax.annotation.PostConstruct;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * Periodically loads active campaigns from the Python backend.
 *
 * <p>Endpoint: GET {backend.url}/api/rtb/campaigns?status=active</p>
 *
 * <p>Campaigns are stored in a thread-safe in-memory map:
 * {@code Map<campaignId, CampaignDTO>} — O(1) lookup in BidEngine.</p>
 *
 * <p>Refresh interval is configurable: {@code campaigns.update.interval} (seconds).
 * Default: 30 seconds. On first load failure, bidder stays in "starting" state
 * and HealthController returns 503 until campaigns are loaded.</p>
 */
@Slf4j
@Service
@EnableScheduling
@RequiredArgsConstructor
public class CampaignLoaderService {

    @Value("${backend.url:http://backend:8000}")
    private String backendUrl;

    @Value("${backend.api.token:}")
    private String apiToken;

    @Value("${campaigns.update.interval:30}")
    private int updateIntervalSeconds;

    private final ObjectMapper objectMapper;
    private final BudgetService budgetService;

    /** Campaigns cache: campaignId → CampaignDTO */
    private volatile Map<Long, CampaignDTO> campaigns = new ConcurrentHashMap<>();

    /** True once at least one successful load has completed. */
    @Getter
    private final AtomicBoolean initialized = new AtomicBoolean(false);

    private HttpClient httpClient;

    @PostConstruct
    public void startup() {
        httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .build();
        log.info("[CampaignLoader] Starting. Backend: {} Interval: {}s", backendUrl, updateIntervalSeconds);
        // Eager first load on startup (blocking).
        loadCampaigns();
    }

    /**
     * Scheduled refresh. Spring @Scheduled fixedDelayString reads from property.
     * Falls back to 30s if property missing.
     */
    @Scheduled(fixedDelayString = "${campaigns.update.interval:30}000")
    public void scheduledRefresh() {
        loadCampaigns();
    }

    /**
     * Return the live campaign map (read-only view).
     * BidEngine calls this on every bid request.
     */
    public Map<Long, CampaignDTO> getCampaigns() {
        return Collections.unmodifiableMap(campaigns);
    }

    /**
     * Return a specific campaign by ID, or null if not found / not active.
     */
    public CampaignDTO getCampaign(Long campaignId) {
        return campaigns.get(campaignId);
    }

    /**
     * Number of currently loaded campaigns.
     * Used by HealthController.
     */
    public int getCampaignCount() {
        return campaigns.size();
    }

    /**
     * Whether at least one successful load has completed.
     * HealthController uses this to determine readiness.
     */
    public boolean isInitialized() {
        return initialized.get();
    }

    // ─────────────────────────────────────────────────────────────

    private void loadCampaigns() {
        String url = backendUrl + "/api/rtb/campaigns?status=active";
        try {
            HttpRequest.Builder reqBuilder = HttpRequest.newBuilder()
                    .uri(URI.create(url))
                    .timeout(Duration.ofSeconds(10))
                    .GET();

            if (apiToken != null && !apiToken.isBlank()) {
                reqBuilder.header("Authorization", "Bearer " + apiToken);
            }

            HttpResponse<String> response = httpClient.send(
                    reqBuilder.build(),
                    HttpResponse.BodyHandlers.ofString()
            );

            if (response.statusCode() != 200) {
                log.warn("[CampaignLoader] Backend returned HTTP {}: {}", response.statusCode(), url);
                return;
            }

            List<CampaignDTO> list = objectMapper.readValue(
                    response.body(),
                    new TypeReference<List<CampaignDTO>>() {}
            );

            // Filter: only campaigns ready to serve.
            Map<Long, CampaignDTO> newMap = list.stream()
                    .filter(CampaignDTO::isReadyToServe)
                    .collect(Collectors.toMap(CampaignDTO::getId, Function.identity()));

            // Initialize budget counters for any new campaigns.
            newMap.values().forEach(budgetService::initBudget);

            // Atomic swap.
            this.campaigns = new ConcurrentHashMap<>(newMap);
            initialized.set(true);

            log.info("[CampaignLoader] Loaded {} active campaigns (total in response: {})",
                    newMap.size(), list.size());

        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            log.warn("[CampaignLoader] Load interrupted");
        } catch (Exception e) {
            log.error("[CampaignLoader] Failed to load campaigns from {}: {}", url, e.getMessage());
            // Do NOT clear campaigns on error — keep serving with stale data.
        }
    }
}
