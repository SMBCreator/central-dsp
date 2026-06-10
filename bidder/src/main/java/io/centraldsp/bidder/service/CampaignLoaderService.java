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

import jakarta.annotation.PostConstruct;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.*;
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
 * {@code Map<Long, CampaignDTO>} — O(1) lookup in BidEngine.</p>
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

    /** campaignId -> CampaignDTO */
    private volatile Map<Long, CampaignDTO> campaigns = new ConcurrentHashMap<>();

    @Getter
    private final AtomicBoolean initialized = new AtomicBoolean(false);

    private HttpClient httpClient;

    @PostConstruct
    public void startup() {
        httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .build();
        log.info("[CampaignLoader] Starting. Backend: {} Interval: {}s", backendUrl, updateIntervalSeconds);
        loadCampaigns();
    }

    @Scheduled(fixedDelayString = "${campaigns.update.interval:30}000")
    public void scheduledRefresh() {
        loadCampaigns();
    }

    /** Full map (unmodifiable). */
    public Map<Long, CampaignDTO> getCampaigns() {
        return Collections.unmodifiableMap(campaigns);
    }

    /**
     * Returns active campaigns as a List.
     * Used by BidEngineService to iterate over all candidates.
     */
    public List<CampaignDTO> getActiveCampaigns() {
        return new ArrayList<>(campaigns.values());
    }

    /** Lookup by ID. */
    public CampaignDTO getCampaign(Long campaignId) {
        return campaigns.get(campaignId);
    }

    /** Campaign count for HealthController. */
    public int getCampaignCount() {
        return campaigns.size();
    }

    /** True after at least one successful load. */
    public boolean isInitialized() {
        return initialized.get();
    }

    // -------------------------------------------------------------------------

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
                    reqBuilder.build(), HttpResponse.BodyHandlers.ofString());

            if (response.statusCode() != 200) {
                log.warn("[CampaignLoader] Backend returned HTTP {}", response.statusCode());
                return;
            }

            List<CampaignDTO> list = objectMapper.readValue(
                    response.body(), new TypeReference<List<CampaignDTO>>() {});

            Map<Long, CampaignDTO> newMap = list.stream()
                    .filter(CampaignDTO::isReadyToServe)
                    .collect(Collectors.toMap(CampaignDTO::getId, Function.identity()));

            newMap.values().forEach(budgetService::initBudget);

            this.campaigns = new ConcurrentHashMap<>(newMap);
            initialized.set(true);

            log.info("[CampaignLoader] Loaded {} active campaigns (total in response: {})",
                    newMap.size(), list.size());

        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            log.warn("[CampaignLoader] Load interrupted");
        } catch (Exception e) {
            log.error("[CampaignLoader] Failed to load campaigns from {}: {}", url, e.getMessage());
        }
    }
}
